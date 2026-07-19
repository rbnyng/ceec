"""Partition the monolithic items.jsonl into per-paper files.

data/items/{system}[補]/{year}/{subject}.jsonl — mirrors the archive/
layout: parser improvements diff one paper at a time, and a paper's rows
sit at a predictable path. Also writes a small manifest of row counts.

Rebuild single-file artifacts on demand:
  python3 -m ceec_archive.partition_items concat  > all_items.jsonl
  python3 -m ceec_archive.partition_items sqlite  -> data/items.db (not committed)
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path("data/parsed/items.jsonl")
DST = Path("data/items")


def partition():
    groups = defaultdict(list)
    for line in open(SRC, encoding="utf-8"):
        r = json.loads(line)
        sys_dir = r["exam_system"] + ("補" if r.get("sitting") == "makeup" else "")
        sub = r.get("subject_key") or r.get("subject_zh")
        groups[(sys_dir, r["year_roc"], sub)].append(line)
    counts = {}
    for (sys_dir, year, sub), lines in sorted(groups.items()):
        p = DST / sys_dir / str(year) / f"{sub}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.writelines(lines)
        counts[str(p)] = len(lines)
    (DST / "partition_manifest.json").write_text(
        json.dumps({"total_rows": sum(counts.values()), "files": counts},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{sum(counts.values())} rows -> {len(counts)} files under {DST}/")


def concat():
    for p in sorted(DST.rglob("*.jsonl")):
        sys.stdout.write(open(p, encoding="utf-8").read())


def sqlite():
    import sqlite3
    db = Path("data/items.db")
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE items (
        item_uid TEXT PRIMARY KEY, exam_system TEXT, year_roc INT, year_ce INT,
        sitting TEXT, subject_key TEXT, subject_zh TEXT, curriculum TEXT,
        number INT, part TEXT, section TEXT, stem TEXT, options TEXT,
        option_source TEXT, group_id TEXT, group_passage TEXT, keys TEXT,
        join_excluded INT, multi_select INT, p_value REAL, p_high REAL,
        p_low REAL, discrimination REAL, p_quintiles TEXT, hl_definition TEXT,
        option_dist_T TEXT, option_dist_H TEXT, option_dist_L TEXT,
        omit_rate REAL, item_flags TEXT, extracted_by TEXT)""")
    n = 0
    for p in DST.rglob("*.jsonl"):
        for line in open(p, encoding="utf-8"):
            r = json.loads(line)
            con.execute("INSERT OR REPLACE INTO items VALUES (" + ",".join("?" * 31) + ")", (
                r.get("item_uid"), r.get("exam_system"), r.get("year_roc"),
                r.get("year_ce"), r.get("sitting"), r.get("subject_key"),
                r.get("subject_zh"), r.get("curriculum"), r.get("number"),
                r.get("part"), r.get("section"), r.get("stem"),
                json.dumps(r.get("options"), ensure_ascii=False),
                r.get("option_source"), r.get("group_id"), r.get("group_passage"),
                r.get("keys"), int(bool(r.get("join_excluded"))),
                None if r.get("multi_select") is None else int(r["multi_select"]),
                r.get("p_value"), r.get("p_high"), r.get("p_low"),
                r.get("discrimination"),
                json.dumps(r.get("p_quintiles")), r.get("hl_definition"),
                json.dumps(r.get("option_dist_T")), json.dumps(r.get("option_dist_H")),
                json.dumps(r.get("option_dist_L")), r.get("omit_rate"),
                json.dumps(r.get("item_flags"), ensure_ascii=False),
                r.get("extracted_by")))
            n += 1
    con.execute("CREATE INDEX idx_items_paper ON items(exam_system, year_roc, subject_key)")
    con.commit()
    con.close()
    print(f"{n} rows -> {db}")


if __name__ == "__main__":
    {"concat": concat, "sqlite": sqlite}.get(
        sys.argv[1] if len(sys.argv) > 1 else "partition", partition)()
