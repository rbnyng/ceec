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


def parse_title(title: str):
    m = TITLE.match(title.strip())
    if not m:
        return None
    year = int(m.group(1))
    system_zh = m.group(2).strip()
    subject_zh = m.group(3).strip()
    system = next((v for k, v in EXAM_SYSTEM.items() if k in system_zh), system_zh)
    return {
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
            if "選項分析" in text:
                idx.setdefault((system, year), {})["options"] = p
            elif "答對率及鑑別" in text or "鑑別指數" in text:
                if "分布圖" not in text:
                    idx.setdefault((system, year), {})["pd"] = p
    return idx


def find_stats_sheet(parsed_wb: dict, subject_zh: str):
    aliases = {subject_zh}
    if subject_zh == "國綜":
        aliases.add("國文")
    for s in parsed_wb["sheets"]:
        name = s["subject"].replace(" ", "")
        if name in aliases or any(a in name for a in aliases):
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
            docx_parsed = (parse_docx(files["docx"])
                           if files["docx"] and files["docx"].endswith(".docx") else None)
            keys = parse_answers(files["key"]) if files["key"] else None

            stats_sheet = None
            pd_sheet = None
            skey = ("學測" if r["collection"] == "gsat_regular" else "分科/指考",
                    meta["year_roc"])
            paths = sidx.get(skey, {})
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

            primary = docx_parsed or pdf_parsed
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
            for it in primary["items"]:
                n = it.get("number")
                part = it.get("part") or ""
                in_free_part = "非選" in part and "混合" not in part
                if in_free_part:
                    n_join = None   # numbering restarts; don't join keys/stats
                else:
                    n_join = n
                row = {
                    **{k: meta[k] for k in ("exam_system", "year_roc", "year_ce",
                                            "subject_key", "subject_zh", "curriculum")},
                    "number": n,
                    "part": it.get("part"),
                    "stem": it.get("stem"),
                    "options": it.get("options"),
                    "option_source": it.get("option_source"),
                    "group_id": it.get("group_id"),
                    "group_passage": it.get("group_passage"),
                    "section": it.get("section"),
                    "keys": keys.get(n_join) if keys else None,
                    "item_flags": it.get("flags", []),
                    "extracted_by": "docx" if primary is docx_parsed else "pdf",
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
