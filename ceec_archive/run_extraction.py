"""Orchestrate extraction over the mirrored corpus.

For each 一般試題 record: identify year/subject from the title, parse the
paper (DOCX preferred, PDF always for cross-check), the answer key, and the
matching stats sheet; validate; emit a run report and a flat item table.

Failures are flagged and skipped, never silently reconciled (HANDOFF §6).
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from collections import Counter
from pathlib import Path

from .validate import (SUBJECT_KEYS, load_manifest, local_for,
                       validate_paper, cross_check)
from .parse_answers import parse_answers
from .parse_docx import parse_docx
from .parse_pdf import parse_pdf
from .parse_stats import parse_workbook, parse_pd_workbook

TITLE = re.compile(r"^(\d{2,3})學年度(.+?)[－\-—](.+?)\s*$")
EXAM_SYSTEM = {"學科能力測驗": "學測", "指定科目考試": "指考", "分科測驗": "分科"}
CONVERT_DIR = Path("mirror/_converted")


def ensure_docx(path: str) -> str | None:
    """Convert a legacy OLE .doc to .docx via LibreOffice (cached).
    Semantic styles are lost in conversion (HANDOFF §2a) but the tab
    conventions survive, which the content-fallback heuristics handle."""
    if path.endswith(".docx"):
        return path
    import subprocess
    CONVERT_DIR.mkdir(parents=True, exist_ok=True)
    out = CONVERT_DIR / (Path(path).stem + ".docx")
    if out.exists():
        return str(out)
    try:
        subprocess.run(["soffice", "--headless", "--convert-to", "docx",
                        "--outdir", str(CONVERT_DIR), path],
                       capture_output=True, timeout=120, check=True)
    except Exception:
        return None
    return str(out) if out.exists() else None


def parse_title(title: str):
    m = TITLE.match(title.strip())
    if not m:
        return None
    year = int(m.group(1))
    system_zh = m.group(2).strip()
    subject_zh = m.group(3).strip()
    system = next((v for k, v in EXAM_SYSTEM.items() if k in system_zh), system_zh)
    makeup = any(t in title for t in ("補考", "補救考試"))
    return {
        "sitting": "makeup" if makeup else "regular",
        "year_roc": year,
        "year_ce": year + 1911,
        "exam_system": system,
        "subject_zh": subject_zh,
        "subject_key": SUBJECT_KEYS.get(subject_zh),
        "curriculum": "108課綱" if year >= 111 else "99課綱及以前",
    }


def stats_index(manifest: dict, records: list[dict]) -> dict:
    """(system, year) -> {'options': path, 'pd': path} stats workbooks."""
    idx = {}
    for r in records:
        if r["collection"] not in ("gsat_stats", "ast_stats"):
            continue
        m = re.match(r"(\d{2,3})", r["title"])
        if not m:
            continue
        year = int(m.group(1))
        system = "學測" if r["collection"] == "gsat_stats" else "分科/指考"
        for l in r["links"]:
            text = l["label"] + l["title_attr"]
            p = local_for(l["href"], manifest)
            if not p:
                continue
            if not p.lower().endswith((".xls", ".xlsx")):
                continue   # 91-era stats are per-subject PDFs; xlrd can't read them
            if "選項分析" in text:
                idx.setdefault((system, year), {})["options"] = p
            elif "答對率及鑑別" in text or "鑑別指數" in text:
                if "分布圖" not in text:
                    idx.setdefault((system, year), {})["pd"] = p
    return idx


SHEET_ALIASES = {
    # 指考-era workbooks abbreviate sheet names
    "國文": ["國文", "國"], "國綜": ["國文", "國"], "國文（選擇題）": ["國文", "國"],
    "英文": ["英文", "英"], "數學甲": ["數學甲", "數甲"], "數學乙": ["數學乙", "數乙"],
    "公民與社會": ["公民與社會", "公民"], "數學": ["數學", "數"],
}


def find_stats_sheet(parsed_wb: dict, subject_zh: str):
    aliases = SHEET_ALIASES.get(subject_zh, [subject_zh])
    # exact match first, then substring — longest alias first so 數學甲
    # never falls through to a bare 數 sheet meant for another paper
    def names(s):
        out = [s["subject"].replace(" ", "")]
        if s.get("subject_detected"):
            out.append(s["subject_detected"].replace(" ", ""))
        return out
    for a in aliases:
        for s in parsed_wb["sheets"]:
            if a in names(s):
                return s
    for a in aliases:
        for s in parsed_wb["sheets"]:
            if any(a in n for n in names(s)):
                return s
    return None


def run(records_path="data/index/records.jsonl", collections=("gsat_regular", "ast_regular"),
        year_min=None, year_max=None, out_dir="data/parsed"):
    records = [json.loads(l) for l in open(records_path, encoding="utf-8")]
    manifest = load_manifest()
    sidx = stats_index(manifest, records)
    wb_cache: dict[str, dict] = {}

    report = {"processed": 0, "skipped_no_files": 0, "parse_errors": 0,
              "papers": [], "flag_counts": Counter(), "agree_rate": []}
    items_out = open(Path(out_dir) / "items.jsonl", "w", encoding="utf-8")

    for r in records:
        if r["collection"] not in collections:
            continue
        meta = parse_title(r["title"])
        if not meta:
            report["flag_counts"]["unparseable_title"] += 1
            continue
        if year_min and meta["year_roc"] < year_min:
            continue
        if year_max and meta["year_roc"] > year_max:
            continue

        files = {"pdf": None, "docx": None, "key": None}
        for l in r["links"]:
            p = local_for(l["href"], manifest)
            if not p:
                continue
            if l["label"].startswith("試題內容"):
                if p.lower().endswith(".pdf"):
                    files["pdf"] = p
                elif p.lower().endswith((".docx", ".doc")):
                    files["docx"] = p
            elif "選擇" in l["label"] and "答案" in l["label"]:
                files["key"] = p

        if not files["pdf"] and not files["docx"]:
            report["skipped_no_files"] += 1
            continue

        entry = {"title": r["title"], **meta, "files": {k: v for k, v in files.items() if v},
                 "flags": [], "checks": {}}
        try:
            pdf_parsed = parse_pdf(files["pdf"]) if files["pdf"] else None
            docx_parsed = None
            word_src = "docx"
            if files["docx"]:
                usable = ensure_docx(files["docx"])
                if usable:
                    docx_parsed = parse_docx(usable)
                    if not files["docx"].endswith(".docx"):
                        word_src = "doc_converted"
            keys = parse_answers(files["key"]) if files["key"] else None

            stats_sheet = None
            pd_sheet = None
            skey = ("學測" if r["collection"] == "gsat_regular" else "分科/指考",
                    meta["year_roc"])
            paths = sidx.get(skey, {}) if meta["sitting"] == "regular" else {}
            if paths.get("options"):
                wb_path = paths["options"]
                if wb_path not in wb_cache:
                    wb_cache[wb_path] = parse_workbook(wb_path)
                stats_sheet = find_stats_sheet(wb_cache[wb_path], meta["subject_zh"])
            if paths.get("pd"):
                pd_path = paths["pd"]
                if pd_path not in wb_cache:
                    wb_cache[pd_path] = parse_pd_workbook(pd_path)
                pd_sheet = find_stats_sheet(wb_cache[pd_path], meta["subject_zh"])

            # true docx is richest; converted .doc lost its styles, so the
            # PDF is the better primary there (conversion still cross-checks)
            if docx_parsed and word_src == "docx":
                primary = docx_parsed
            else:
                primary = pdf_parsed or docx_parsed
            v = validate_paper(primary, keys, stats_sheet["items"] if stats_sheet else None)
            entry["checks"] = v["checks"]
            entry["flags"] = v["flags"]
            if pdf_parsed and docx_parsed:
                xc = cross_check(pdf_parsed, docx_parsed)
                entry["cross_check"] = xc
                if xc["n_both"]:
                    report["agree_rate"].append(xc["n_agree"] / xc["n_both"])

            stats_by_num = ({i["number"]: i for i in stats_sheet["items"]}
                            if stats_sheet else {})
            pd_by_num = ({i["number"]: i for i in pd_sheet["items"]}
                         if pd_sheet else {})

            # a part that restarts numbering (its numbers collide with an
            # earlier part's) is a separate namespace — join-excluded. This
            # catches old papers whose free-response part heading is a bare
            # 第貳部分 with no 非選 in the text.
            part_max: dict = {}
            restart_parts = set()
            seen_max = 0
            for it in primary["items"]:
                n, part = it.get("number"), it.get("part") or ""
                if n is None:
                    continue
                if part not in part_max:
                    if n <= seen_max and part:
                        restart_parts.add(part)
                    part_max[part] = n
                part_max[part] = max(part_max[part], n)
                seen_max = max(seen_max, n)

            for seq, it in enumerate(primary["items"], 1):
                n = it.get("number")
                part = it.get("part") or ""
                in_free_part = (("非選" in part and "混合" not in part)
                                or part in restart_parts)
                if in_free_part:
                    n_join = None   # separate numbering namespace
                else:
                    n_join = n
                uid_sys = meta['exam_system'] + ("補" if meta["sitting"] == "makeup" else "")
                row = {
                    "item_uid": f"{uid_sys}{meta['year_roc']}-"
                                f"{meta['subject_key'] or meta['subject_zh']}-{seq:03d}",
                    "sitting": meta["sitting"],
                    **{k: meta[k] for k in ("exam_system", "year_roc", "year_ce",
                                            "subject_key", "subject_zh", "curriculum")},
                    "number": n,
                    "join_excluded": in_free_part,
                    "part": it.get("part"),
                    "stem": it.get("stem"),
                    "options": it.get("options"),
                    "option_source": it.get("option_source"),
                    "group_id": it.get("group_id"),
                    "group_passage": it.get("group_passage"),
                    "section": it.get("section"),
                    "keys": keys.get(n_join) if keys else None,
                    "item_flags": it.get("flags", []),
                    "extracted_by": word_src if primary is docx_parsed else "pdf",
                }
                si = stats_by_num.get(n_join)
                if si:
                    row.update({
                        "multi_select": si["multi_select"],
                        "option_dist_T": si["dist"].get("T"),
                        "option_dist_H": si["dist"].get("H"),
                        "option_dist_L": si["dist"].get("L"),
                        "omit_rate": si["omit"].get("T"),
                    })
                pi = pd_by_num.get(n_join)
                if pi:
                    # official values; groups are top/bottom 33%, quintiles 20%
                    # (definition printed in the workbook's own footnotes)
                    row.update({
                        "p_value": pi["P"],
                        "p_high": pi["Ph"],
                        "p_low": pi["Pl"],
                        "discrimination": pi["D"],
                        "p_quintiles": pi["quintiles"],
                        "hl_definition": "top_bottom_33pct",
                    })
                elif si and not si["multi_select"] and si.get("p_value") is not None:
                    # fallback: derived from option distribution (single-select
                    # only; multi-select marginals double-count)
                    row.update({
                        "p_value": round(si["p_value"] * 100, 1),
                        "p_high": round(si["p_high"] * 100, 1) if si.get("p_high") is not None else None,
                        "p_low": round(si["p_low"] * 100, 1) if si.get("p_low") is not None else None,
                        "discrimination": round(si["discrimination"] * 100, 1) if si.get("discrimination") is not None else None,
                        "hl_definition": "derived_from_option_dist",
                    })
                items_out.write(json.dumps(row, ensure_ascii=False) + "\n")

            report["processed"] += 1
            for f in v["flags"]:
                report["flag_counts"][f.split(":")[0]] += 1
        except Exception:
            report["parse_errors"] += 1
            entry["flags"].append("exception:" + traceback.format_exc(limit=1).strip().splitlines()[-1])
            report["flag_counts"]["exception"] += 1
        report["papers"].append(entry)

    items_out.close()
    report["flag_counts"] = dict(report["flag_counts"])
    if report["agree_rate"]:
        report["mean_pdf_docx_agreement"] = round(
            sum(report["agree_rate"]) / len(report["agree_rate"]), 4)
    del report["agree_rate"]
    Path(out_dir, "run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "papers"},
                     ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    kw = {}
    if len(sys.argv) > 1:
        kw["year_min"] = int(sys.argv[1])
    if len(sys.argv) > 2:
        kw["year_max"] = int(sys.argv[2])
    run(**kw)
