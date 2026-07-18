"""Parse CEEC exam papers from DOCX (style-driven with content fallback).

Style names differ per subject and era (HANDOFF §4/parse_docx): substring-match
on TIT1 / AB / 題組 etc., then fall back to content heuristics (^N. stems,
(A) option markers) when styles are uninformative (List Paragraph, Normal).

Traverses document.body in document order including w:tbl, because
table-formatted options are real (113 國綜 Q6).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

OPT_MARK = re.compile(r"\(([A-J])\)")
NUM_STEM = re.compile(r"^\s*(\d{1,3})\s*[.．]\s*")
SECTION = re.compile(r"[一二三四五六七八九十]+、|第[壹貳參肆伍]+部分")
DECL = re.compile(r"第\s*(\d+)\s*題\s*至\s*第\s*(\d+)\s*題")
POINTS = re.compile(r"[（(]\s*[占佔]\s*(\d+)\s*分\s*[)）]")
# two observed phrasings: 「6-8為題組。閱讀下文…」 and 「第11至15題為題組」
GROUP_DECL = re.compile(r"^(?:第\s*)?(\d{1,3})\s*[-–~至]\s*(?:第\s*)?(\d{1,3})\s*題?為題組")
BLANK_STEM = re.compile(r"_{3,}|＿{2,}")


def iter_block_items(doc):
    """Yield Paragraph and Table objects in true document order."""
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, doc)
        elif child.tag.endswith("}tbl"):
            yield Table(child, doc)


def classify_style(name: str) -> str:
    n = (name or "").upper()
    if "TIT1" in n and "內縮" in name:
        return "passage"
    if "TIT1" in n:
        return "stem"
    if "TIT2" in n or "題組" in name:
        return "passage"
    if "ABCD" in n or re.search(r"\bAB\b", n) or "樣式 AB" in name:
        return "options"
    if name in ("壹", "說明") or "說明" in name:
        return "section"
    return "unknown"


def split_options(text: str) -> dict[str, str]:
    """Split '(A) foo\t(B) bar' style runs into {letter: text}.

    Tolerates a missing leading (A) marker (seen in 115 英文 Q6): if the first
    marker found is (B) and text before it is non-empty, that prefix is (A).
    """
    marks = list(OPT_MARK.finditer(text))
    out = {}
    if marks and marks[0].group(1) == "B":
        prefix = text[:marks[0].start()].strip().strip("\t").strip()
        if prefix and len(prefix) < 120:
            out["A"] = prefix
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = text[m.end():end].strip().strip("\t").strip()
    return out


def parse_docx(path: str | Path) -> dict:
    doc = docx.Document(str(path))
    items: list[dict] = []
    sections: list[dict] = []
    cur = None
    cur_section = None
    cur_group = None          # {"id": "6-8", "first":6, "last":8, "passage":[...]}
    pending_passage: list[str] = []

    def close_item():
        nonlocal cur
        if cur:
            items.append(cur)
            cur = None

    def open_item(num, stem, style, flags=None):
        nonlocal cur
        close_item()
        inline_opts = {}
        marks = list(OPT_MARK.finditer(stem or ""))
        if len(marks) >= 2:
            inline_opts = split_options(stem)
            stem = stem[:marks[0].start()].strip() or None
        grp = cur_group if (cur_group and num is not None
                            and cur_group["first"] <= num <= cur_group["last"]) else None
        cur = {
            "number": num,
            "stem": stem,
            "options": inline_opts,
            "option_source": "inline",
            "group_id": grp["id"] if grp else None,
            "group_passage": "\n".join(grp["passage"]) if grp and grp["passage"] else None,
            "passage": "\n".join(pending_passage) if pending_passage else None,
            "stimulus": [],
            "section": cur_section["heading"] if cur_section else None,
            "style": style,
            "flags": flags or [],
        }

    def next_expected() -> int | None:
        """Infer the number of an auto-numbered item from context."""
        prev = max((i["number"] for i in items if i["number"] is not None), default=None)
        if cur and cur["number"] is not None:
            prev = cur["number"]
        if prev is not None:
            return prev + 1
        if cur_section and cur_section["first_item"]:
            return cur_section["first_item"]
        return None

    for block in iter_block_items(doc):
        if isinstance(block, Table):
            texts = []
            for row in block.rows:
                for cell in row.cells:
                    t = cell.text.strip()
                    if t and t not in texts:
                        texts.append(t)
            joined = "\t".join(texts)
            if cur is not None and OPT_MARK.search(joined):
                cur["options"].update(split_options(joined))
                cur["option_source"] = "table"
            elif cur is not None:
                cur["stimulus"].append(joined)
            elif cur_group is not None:
                cur_group["passage"].append(joined)
            else:
                pending_passage.append(joined)
            continue

        text = block.text.strip()
        if not text:
            continue
        style_name = block.style.name if block.style else ""
        kind = classify_style(style_name)

        g = GROUP_DECL.match(text)
        if g:
            close_item()
            cur_group = {"id": f"{g.group(1)}-{g.group(2)}",
                         "first": int(g.group(1)), "last": int(g.group(2)),
                         "passage": []}
            pending_passage = []
            continue

        if SECTION.match(text) or text.startswith("說明"):
            close_item()
            decl = DECL.search(text)
            pts = POINTS.search(text)
            sec = {
                "heading": text[:80],
                "first_item": int(decl.group(1)) if decl else None,
                "last_item": int(decl.group(2)) if decl else None,
                "points": int(pts.group(1)) if pts else None,
            }
            sections.append(sec)
            cur_section = sec
            if not text.startswith("說明"):
                cur_group = None
            pending_passage = []
            continue

        m = NUM_STEM.match(text)
        if m and kind in ("unknown", "stem", "options"):
            kind = "stem"
        elif kind == "unknown" and OPT_MARK.match(text):
            kind = "options"

        if kind == "stem":
            if m:
                open_item(int(m.group(1)), NUM_STEM.sub("", text, count=1).strip(), style_name)
                pending_passage = []
            else:
                # un-numbered stem-like line (Word auto-numbering, e.g. 英文 詞彙題)
                if BLANK_STEM.search(text) or text.endswith(("：", ":", "？", "?")):
                    open_item(next_expected(), text, style_name, flags=["number_inferred"])
                    pending_passage = []
                elif cur is not None:
                    cur["stimulus"].append(text)
                else:
                    pending_passage.append(text)
        elif kind == "passage":
            if cur is not None and not cur["options"]:
                cur["stimulus"].append(text)
            elif cur_group is not None and cur is None:
                cur_group["passage"].append(text)
            else:
                close_item()
                pending_passage.append(text)
        elif kind == "options":
            if OPT_MARK.match(text) and cur is not None:
                cur["options"].update(split_options(text))
            elif BLANK_STEM.search(text):
                # style says options but content is a cloze stem (英文 詞彙題 pattern:
                # stem and options alternate in the same ABCD style)
                open_item(next_expected(), text, style_name, flags=["number_inferred"])
                pending_passage = []
            elif cur is not None:
                cur["options"].update(split_options(text))
        else:
            if cur is not None:
                if OPT_MARK.search(text) and not cur["options"]:
                    cur["options"].update(split_options(text))
                else:
                    cur["stimulus"].append(text)
            elif cur_group is not None:
                cur_group["passage"].append(text)
            else:
                pending_passage.append(text)

    close_item()

    # synthesize placeholders for declared-but-unparsed numbers (bank/cloze
    # sections like 文意選填 have no per-item text at all)
    have = {i["number"] for i in items if i["number"] is not None}
    for sec in sections:
        if sec["first_item"] and sec["last_item"]:
            for n in range(sec["first_item"], sec["last_item"] + 1):
                if n not in have:
                    items.append({
                        "number": n, "stem": None, "options": {},
                        "option_source": None, "group_id": None,
                        "group_passage": None, "passage": None, "stimulus": [],
                        "section": sec["heading"], "style": None,
                        "flags": ["not_extracted_bank_or_blank"],
                    })
                    have.add(n)
    items.sort(key=lambda i: (i["number"] is None, i["number"] or 0))
    return {"path": str(path), "sections": sections, "items": items}


def summarize(parsed: dict) -> str:
    items = parsed["items"]
    n_opts = sum(len(i["options"]) for i in items)
    nums = [i["number"] for i in items if i["number"] is not None]
    gaps = []
    if nums:
        gaps = [n for n in range(min(nums), max(nums) + 1) if n not in nums]
    return (f"  {len(items)} items ({min(nums) if nums else '?'}–{max(nums) if nums else '?'}), "
            f"{n_opts} options, missing numbers: {gaps}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        parsed = parse_docx(p)
        print(p)
        print(summarize(parsed))
        out = Path("data/parsed/docx") / (Path(p).stem + ".json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8")
