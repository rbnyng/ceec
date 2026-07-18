"""Parse CEEC exam papers from PDF, coordinate-based.

Key insights inherited from the exploratory session (HANDOFF §4/parse_exam4):
- Item numbers sit at a consistent hanging-indent x-position. Detect by
  coordinate, NOT by ^\\d+\\. — PDF text ordering does not respect logical line
  starts and the number token often serialises after its stem. The margin is
  auto-detected as the modal x0 of bare-number tokens (self-calibrates).
- Row clustering must be tolerance-based (±5pt), not fixed-width bucketing:
  round(top/4) once split a number from its stem over 0.54pt.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pdfplumber

NUM_TOKEN = re.compile(r"^(\d{1,3})\.$")
OPT_MARK = re.compile(r"\(([A-J])\)")
SECTION = re.compile(r"[一二三四五六七八九十]+、|第[壹貳參肆伍]+部分")
DECL = re.compile(r"第\s*(\d+)\s*題\s*至\s*第\s*(\d+)\s*題")
GROUP_DECL = re.compile(r"(?:第\s*)?(\d{1,3})\s*[-–~至]\s*(?:第\s*)?(\d{1,3})\s*題?為題組")
ROW_TOL = 5.0


def cluster_rows(words: list[dict]) -> list[list[dict]]:
    """Group words into visual rows by top coordinate, tolerance-based."""
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        placed = False
        for row in rows:
            if abs(row[0]["top"] - w["top"]) <= ROW_TOL:
                row.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w["x0"])
    rows.sort(key=lambda r: min(w["top"] for w in r))
    return rows


def detect_number_margin(pages_words: list[list[dict]]) -> float | None:
    """Modal x0 of bare 'N.' tokens across the document."""
    xs = []
    for words in pages_words:
        for w in words:
            if NUM_TOKEN.match(w["text"]):
                xs.append(round(w["x0"]))
    if not xs:
        return None
    return float(Counter(xs).most_common(1)[0][0])


def parse_pdf(path: str | Path) -> dict:
    items: list[dict] = []
    sections: list[dict] = []
    cur = None
    cur_group = None
    cur_part = None

    with pdfplumber.open(str(path)) as pdf:
        pages_words = [p.extract_words(use_text_flow=False, keep_blank_chars=False)
                       for p in pdf.pages]
        margin = detect_number_margin(pages_words)

        def close_item():
            nonlocal cur
            if cur:
                text = cur.pop("_buf")
                marks = list(OPT_MARK.finditer(text))
                if marks:
                    cur["stem"] = text[:marks[0].start()].strip()
                    opts = {}
                    for i, m in enumerate(marks):
                        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
                        opts[m.group(1)] = text[m.end():end].strip()
                    cur["options"] = opts
                else:
                    cur["stem"] = text.strip()
                    cur["options"] = {}
                items.append(cur)
                cur = None

        for pageno, words in enumerate(pages_words, 1):
            for row in cluster_rows(words):
                row_text = " ".join(w["text"] for w in row)
                g = GROUP_DECL.search(row_text)
                if g and "題組" in row_text:
                    close_item()
                    cur_group = {"id": f"{g.group(1)}-{g.group(2)}",
                                 "first": int(g.group(1)), "last": int(g.group(2))}
                    continue
                if SECTION.match(row_text.replace(" ", "")) or row_text.replace(" ", "").startswith("說明"):
                    close_item()
                    flat = row_text.replace(" ", "")
                    decl = DECL.search(flat)
                    sections.append({
                        "heading": row_text[:80],
                        "first_item": int(decl.group(1)) if decl else None,
                        "last_item": int(decl.group(2)) if decl else None,
                    })
                    if re.match(r"第[壹貳參肆伍]+部分", flat):
                        cur_part = flat[:30]
                    if not flat.startswith("說明"):
                        cur_group = None
                    continue
                # does this row open a new item? bare number token near margin
                opener = None
                for w in row:
                    if NUM_TOKEN.match(w["text"]) and margin is not None \
                            and abs(w["x0"] - margin) <= 3.0:
                        opener = w
                        break
                if opener:
                    close_item()
                    num = int(NUM_TOKEN.match(opener["text"]).group(1))
                    grp = cur_group if (cur_group and cur_group["first"] <= num <= cur_group["last"]) else None
                    rest = " ".join(w["text"] for w in row if w is not opener)
                    cur = {
                        "number": num,
                        "page": pageno,
                        "top": round(opener["top"], 1),
                        "group_id": grp["id"] if grp else None,
                        "part": cur_part,
                        "_buf": rest,
                    }
                elif cur is not None:
                    cur["_buf"] += "\n" + row_text
        close_item()

    return {"path": str(path), "margin_x": margin, "sections": sections, "items": items}


def summarize(parsed: dict) -> str:
    items = parsed["items"]
    nums = [i["number"] for i in items if i["number"] is not None]
    gaps = [n for n in range(min(nums), max(nums) + 1) if n not in nums] if nums else []
    n_opts = sum(len(i["options"]) for i in items)
    return (f"  margin_x={parsed['margin_x']}, {len(items)} items "
            f"({min(nums) if nums else '?'}–{max(nums) if nums else '?'}), "
            f"{n_opts} options, missing: {gaps}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        parsed = parse_pdf(p)
        print(p)
        print(summarize(parsed))
        out = Path("data/parsed/pdf") / (Path(p).stem + ".json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8")
