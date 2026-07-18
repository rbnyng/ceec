"""Parse CEEC 選擇題選項分析 workbooks (option-level response distributions).

Verified layout (115 學測, HANDOFF §2b) — old binary .xls, read with xlrd:
- one sheet per subject
- a header-area row carries 報考人數/缺考人數/到考人數
- table header row starts with 題號; columns: 題號, 組別, 未答, then option labels
- three rows per item: 組別 T (total), H (high group), L (low group)
- '*' prefix on a percentage cell marks a keyed option
- '*' prefix on the question number marks a multi-select item
- 數學 sheets store option labels as floats (1.0..5.0)

Derived: p = sum(T[keys]); D = (sum(H[keys]) - sum(L[keys])) / 100.
Percentages are integers, so multi-select rows may not sum to 100.

Whether flat-era (92–98) workbooks share this layout is an open question
(HANDOFF Q2) — this parser reports structural failures loudly instead of
guessing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import xlrd


def _norm(v) -> str:
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def _pct(v):
    """A distribution cell: int, '*'-prefixed int, '-', or ''. Returns (value, keyed)."""
    s = _norm(v)
    keyed = s.startswith("*") or s.startswith("＊")
    s = s.lstrip("*＊").strip()
    if s in ("", "-", "—", "–"):
        return None, keyed
    try:
        return float(s), keyed
    except ValueError:
        return None, keyed


def parse_sheet(sheet) -> dict:
    out = {"subject": sheet.name, "cohort": {}, "items": [], "flags": []}
    header_row = None
    opt_cols: list[tuple[int, str]] = []
    unanswered_col = None
    group_col = None

    for r in range(sheet.nrows):
        vals = [_norm(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
        joined = " ".join(vals)
        for key, pat in (("n_registered", r"報考人數[^\d]*([\d,]+)"),
                         ("n_absent", r"缺考人數[^\d]*([\d,]+)"),
                         ("n_sat", r"到考人數[^\d]*([\d,]+)")):
            m = re.search(pat, joined)
            if m and key not in out["cohort"]:
                out["cohort"][key] = int(m.group(1).replace(",", ""))
        if header_row is None and any(v.replace(" ", "") == "題號" for v in vals):
            header_row = r
            for c, v in enumerate(vals):
                vv = v.replace(" ", "")
                if vv == "組別":
                    group_col = c
                elif vv in ("未答", "未答%"):
                    unanswered_col = c
                elif re.fullmatch(r"[A-J]|\d{1,2}", vv) and vv != "題號":
                    opt_cols.append((c, vv))

    if header_row is None:
        out["flags"].append("no_header_row")
        return out

    cur = None
    for r in range(header_row + 1, sheet.nrows):
        vals = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
        qraw = _norm(vals[0])
        group = _norm(vals[group_col]).upper() if group_col is not None else ""
        if qraw:
            multi = qraw.startswith("*") or qraw.startswith("＊")
            qs = qraw.lstrip("*＊").strip()
            if not re.fullmatch(r"\d{1,3}", qs):
                continue  # footer/notes
            cur = {"number": int(qs), "multi_select": multi,
                   "keys": [], "dist": {}, "omit": {}}
            out["items"].append(cur)
        if cur is None or group not in ("T", "H", "L"):
            continue
        dist = {}
        for c, lab in opt_cols:
            val, keyed = _pct(vals[c])
            if val is not None:
                dist[lab] = val
            if keyed and lab not in cur["keys"]:
                cur["keys"].append(lab)
        cur["dist"][group] = dist
        if unanswered_col is not None:
            oval, _ = _pct(vals[unanswered_col])
            if oval is not None:
                cur["omit"][group] = oval

    for it in out["items"]:
        T, H, L = it["dist"].get("T", {}), it["dist"].get("H", {}), it["dist"].get("L", {})
        ks = it["keys"]
        if not ks:
            it.setdefault("flags", []).append("no_key_marked")
            continue
        if T:
            it["p_value"] = round(sum(T.get(k, 0) for k in ks) / 100, 4)
        if H and L:
            it["p_high"] = round(sum(H.get(k, 0) for k in ks) / 100, 4)
            it["p_low"] = round(sum(L.get(k, 0) for k in ks) / 100, 4)
            it["discrimination"] = round(it["p_high"] - it["p_low"], 4)
        missing = [g for g in ("T", "H", "L") if g not in it["dist"]]
        if missing:
            it.setdefault("flags", []).append(f"missing_groups:{','.join(missing)}")
    return out


def parse_pd_workbook(path: str | Path) -> dict:
    """Parse 42-46_各科答對率及鑑別度表 workbooks (official per-item P and D).

    Verified layout (113 學測): header 題號,P,Ph,Pl,Pa..Pe,T,D,D1..D4.
    The sheet's own footnotes define Ph=高分組(前33%), Pl=低分組(後33%),
    Pa–Pe = 20% ability quintiles (verified: mean(Pa..Pe)≈P, D=Ph−Pl).
    Multi-select P is a partial-credit 得分率, not an all-correct rate
    (footer formula: Σ得分/(考生人數×題分), blanks scored 0).
    """
    book = xlrd.open_workbook(str(path))
    out = {"path": str(path), "sheets": []}
    for sh in book.sheets():
        if sh.nrows == 0:
            continue
        header_row = None
        cols = {}
        for r in range(min(sh.nrows, 12)):
            vals = [_norm(sh.cell_value(r, c)) for c in range(sh.ncols)]
            if "題號" in [v.replace(" ", "") for v in vals]:
                header_row = r
                for c, v in enumerate(vals):
                    cols[v.replace(" ", "")] = c
                break
        if header_row is None:
            out["sheets"].append({"subject": sh.name, "items": [],
                                  "flags": ["no_header_row"]})
            continue
        items = []
        for r in range(header_row + 1, sh.nrows):
            q = _norm(sh.cell_value(r, cols["題號"]))
            multi = q.startswith(("*", "＊"))
            q = q.lstrip("*＊").strip()
            if not re.fullmatch(r"\d{1,3}", q):
                continue
            def cell(name):
                if name not in cols:
                    return None
                v, _ = _pct(sh.cell_value(r, cols[name]))
                return v
            items.append({
                "number": int(q), "multi_select": multi,
                "P": cell("P"), "Ph": cell("Ph"), "Pl": cell("Pl"),
                "D": cell("D"),
                "quintiles": [cell(k) for k in ("Pa", "Pb", "Pc", "Pd", "Pe")],
            })
        out["sheets"].append({"subject": sh.name, "items": items, "flags": []})
    return out


def parse_workbook(path: str | Path) -> dict:
    book = xlrd.open_workbook(str(path), formatting_info=False)
    sheets = []
    for sh in book.sheets():
        if sh.nrows == 0:
            continue
        sheets.append(parse_sheet(sh))
    return {"path": str(path), "sheets": sheets}


def summarize(parsed: dict) -> str:
    lines = []
    for s in parsed["sheets"]:
        n = len(s["items"])
        multi = sum(1 for i in s["items"] if i["multi_select"])
        nokey = sum(1 for i in s["items"] if not i["keys"])
        flags = f" FLAGS={s['flags']}" if s["flags"] else ""
        lines.append(f"  {s['subject']}: {n} items ({multi} multi-select, {nokey} unkeyed), "
                     f"cohort={s['cohort'].get('n_sat', '?')}{flags}")
    return "\n".join(lines)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        parsed = parse_workbook(p)
        print(p)
        print(summarize(parsed))
        out = Path("data/parsed/stats") / (Path(p).stem + ".json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8")
