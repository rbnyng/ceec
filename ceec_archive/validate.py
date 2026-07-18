"""Validation harness: join parsed papers, answer keys, and stats; cross-check.

Papers declare their own structure (HANDOFF §6): section declarations give
expected item ranges; the separately-published answer key gives keys and
free-response markers; the stats workbook independently marks multi-select.
Disagreement between sources is reported, never silently reconciled.
Thresholds are never relaxed to make a run pass.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

TITLE = re.compile(r"(\d{2,3})學年度(.+?)－(.+)$")

SUBJECT_KEYS = {
    "國綜": "chinese_reading", "國寫": "chinese_writing", "國文": "chinese",
    "英文": "english", "數學A": "math_a", "數學B": "math_b", "數學甲": "math_jia",
    "數學乙": "math_yi", "數學": "math", "社會": "social", "自然": "science",
    "物理": "physics", "化學": "chemistry", "生物": "biology",
    "歷史": "history", "地理": "geography", "公民與社會": "civics",
    "國語文綜合能力測驗": "chinese_reading", "國語文寫作能力測驗": "chinese_writing",
    "國文（選擇題）": "chinese_reading",   # 107–110 transition naming
}
# stats sheet name -> paper subject aliases
SHEET_ALIAS = {"國文": ["國綜", "國文"], "數學A": ["數學A"], "數學B": ["數學B"]}


def load_manifest(path="data/manifest/downloads.jsonl") -> dict[str, dict]:
    out = {}
    for line in open(path, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            out[r["url"]] = r
    return out


def local_for(href: str, manifest: dict) -> str | None:
    url = href if href.startswith("http") else "https://www.ceec.edu.tw" + href
    rec = manifest.get(url)
    if rec and rec.get("ok") and Path(rec.get("local_path", "")).exists():
        return rec["local_path"]
    return None


def validate_paper(parsed: dict, keys: dict[int, str] | None,
                   stats_items: list[dict] | None) -> dict:
    """Run the §6 checks on one parsed paper. Returns a findings dict."""
    findings = {"checks": {}, "flags": []}
    # numbering restarts in the 非選擇題 part (中譯英 is numbered 1,2 again):
    # exclude free-response parts from number-keyed joins, and on remaining
    # collisions prefer the item that carries options
    items: dict[int, dict] = {}
    for i in parsed["items"]:
        n = i.get("number")
        if n is None:
            continue
        part = i.get("part") or ""
        if "非選" in part and "混合" not in part:
            continue
        if n in items and items[n].get("options") and not i.get("options"):
            continue
        items[n] = i

    declared = set()
    for s in parsed.get("sections", []):
        if s.get("first_item") and s.get("last_item"):
            declared.update(range(s["first_item"], s["last_item"] + 1))
    if declared:
        missing = sorted(declared - set(items))
        extra = sorted(n for n in items if declared and n not in declared
                       and n <= max(declared))
        findings["checks"]["declared_range_covered"] = not missing
        if missing:
            findings["flags"].append(f"missing_declared_items:{missing}")
        if extra:
            findings["flags"].append(f"items_outside_declaration:{extra}")

    empty = [n for n, i in sorted(items.items())
             if not (i.get("stem") or "").strip() and not i.get("flags")
             and not i.get("group_passage")   # cloze items live in the passage
             and not i.get("options")]
    if empty:
        findings["flags"].append(f"empty_stems:{empty}")

    if keys:
        mc_keys = {n: k for n, k in keys.items() if re.fullmatch(r"[A-J]+|\d+", k)}
        parsed_mc = {n for n, i in items.items() if i.get("options")}
        keyed_not_parsed = sorted(set(mc_keys) - parsed_mc)
        if keyed_not_parsed:
            real = [n for n in keyed_not_parsed
                    if not any("not_extracted" in f for f in items.get(n, {}).get("flags", []))]
            findings["checks"]["all_keyed_items_have_options"] = not real
            if real:
                findings["flags"].append(f"keyed_but_no_options:{real}")
        else:
            findings["checks"]["all_keyed_items_have_options"] = True
        # option letters must cover the key
        badkey = [n for n, k in mc_keys.items()
                  if n in items and items[n].get("options")
                  and k.isalpha() and not set(k) <= set(items[n]["options"])]
        findings["checks"]["keys_within_parsed_options"] = not badkey
        if badkey:
            findings["flags"].append(f"key_letter_not_in_options:{badkey}")

    if stats_items is not None and keys:
        stats_multi = {i["number"] for i in stats_items if i.get("multi_select")}
        key_multi = {n for n, k in keys.items() if k.isalpha() and len(k) > 1}
        # letter keys only: math numeric keys ('13' = options 1+3, but also
        # '48' = a fill-in answer) are ambiguous about multi-select
        joint = ({i["number"] for i in stats_items} & set(keys)
                 & {n for n, k in keys.items() if k.isalpha()})
        disagree = sorted((stats_multi ^ key_multi) & joint)
        findings["checks"]["multiselect_agreement"] = not disagree
        if disagree:
            findings["flags"].append(f"multiselect_disagreement:{disagree}")
        # keyed option in stats vs answer key
        mismatch = []
        for si in stats_items:
            n = si["number"]
            if n in keys and si.get("keys") and keys[n].isalpha():
                if set(si["keys"]) != set(keys[n]):
                    mismatch.append(n)
        findings["checks"]["stats_key_matches_answer_key"] = not mismatch
        if mismatch:
            findings["flags"].append(f"stats_vs_key_mismatch:{mismatch}")

    return findings


def cross_check(pdf_parsed: dict, docx_parsed: dict) -> dict:
    p_items = {i["number"]: i for i in pdf_parsed["items"] if i.get("number")}
    d_items = {i["number"]: i for i in docx_parsed["items"] if i.get("number")}
    both = set(p_items) & set(d_items)
    opt_disagree = [n for n in sorted(both)
                    if set(p_items[n]["options"]) != set(d_items[n]["options"])
                    and p_items[n]["options"] and d_items[n]["options"]]
    return {
        "pdf_only": sorted(set(p_items) - set(d_items)),
        "docx_only": sorted(set(d_items) - set(p_items)),
        "option_set_disagree": opt_disagree,
        "n_agree": len(both) - len(opt_disagree),
        "n_both": len(both),
    }
