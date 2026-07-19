"""Build a human-navigable semantic tree over the bit-faithful mirror.

mirror/file_pool/{token}/{name} stays untouched (verbatim URL mapping).
archive/{system}/{year}/{subject}/{label}.{ext} is a derived view built
from index labels (never filenames), hardlinked so it costs no disk, and
cheap in git because identical blobs dedupe by content hash.

Every entry is recorded in data/manifest/tree_map.jsonl:
  semantic_path ↔ pool_path ↔ source url ↔ sha256
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

ARCHIVE = Path("archive")
OVERSIZE = 99 * 1024 * 1024   # GitHub blob limit; oversize stays pool-only

SYSTEM_DIR = {
    "gsat_regular": "學測", "gsat_special": "學測", "gsat_special_sheet": "學測",
    "gsat_stats": "學測",
    "ast_regular": None, "ast_special": "分科", "ast_special_sheet": "分科",
    "ast_stats": None,   # 指考 vs 分科 decided per title
}

TITLE = re.compile(r"^(\d{2,3})學年度(.+?)(?:[－\-—](.+?))?\s*$")


def system_for(coll: str, title: str) -> str:
    fixed = SYSTEM_DIR.get(coll)
    if fixed:
        return fixed
    if "分科" in title:
        return "分科"
    if "指定科目" in title or "指考" in title:
        return "指考"
    return "學測"


def sanitize(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "_", s).strip()
    return s[:80] or "unnamed"


def subject_dir(coll: str, title: str) -> tuple[int | None, str]:
    m = TITLE.match(title.strip())
    if not m:
        return None, sanitize(title)
    year = int(m.group(1))
    mid, subj = m.group(2) or "", m.group(3) or ""
    makeup = any(t in title for t in ("補考", "補救考試"))
    if coll.endswith("_stats"):
        return year, "統計資料"
    if coll.endswith("_special_sheet"):
        return year, "特殊答題卷"
    name = sanitize(subj) if subj else sanitize(mid)
    if makeup and "補" not in name:
        name += "(補考)"
    if coll.endswith("_special"):
        return year, f"特殊試題/{name}"
    return year, name


def main():
    manifest = {}
    for line in open("data/manifest/downloads.jsonl", encoding="utf-8"):
        r = json.loads(line)
        if r["ok"]:
            manifest[r["url"]] = r

    used: dict[str, int] = defaultdict(int)
    mapping = []
    stats = {"linked": 0, "oversize_skipped": 0, "missing": 0}

    for line in open("data/index/records.jsonl", encoding="utf-8"):
        rec = json.loads(line)
        year, subj = subject_dir(rec["collection"], rec["title"])
        system = system_for(rec["collection"], rec["title"])
        base = ARCHIVE / system / (str(year) if year else "other") / subj

        # two-level stats records: preserve their section grouping
        link_groups = []
        if rec.get("sections"):
            for sec in rec["sections"]:
                for lk in sec["links"]:
                    link_groups.append((sanitize(sec["section"]), lk))
        else:
            link_groups = [(None, lk) for lk in rec["links"]]

        for secname, lk in link_groups:
            url = lk["href"] if lk["href"].startswith("http") \
                else "https://www.ceec.edu.tw" + lk["href"]
            r = manifest.get(url)
            if not r or not Path(r["local_path"]).exists():
                stats["missing"] += 1
                continue
            src = Path(r["local_path"])
            ext = src.suffix.lower()
            label = sanitize(lk["label"]) or src.stem
            d = base / secname if secname else base
            dest = d / f"{label}{ext}"
            key = str(dest)
            used[key] += 1
            if used[key] > 1:
                dest = d / f"{label}-{used[key]}{ext}"
            entry = {
                "semantic_path": str(dest),
                "pool_path": str(src),
                "url": url,
                "sha256": r["sha256"],
                "size": r["size"],
                "in_git": r["size"] <= OVERSIZE,
            }
            mapping.append(entry)
            if r["size"] > OVERSIZE:
                stats["oversize_skipped"] += 1
                continue     # pool-only; keeps archive/ pushable
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest.unlink()
            try:
                os.link(src, dest)
            except OSError:
                import shutil
                shutil.copy2(src, dest)
            stats["linked"] += 1

    out = Path("data/manifest/tree_map.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for e in mapping:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(json.dumps(stats), "->", out)


if __name__ == "__main__":
    main()
