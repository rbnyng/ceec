"""Download every file referenced by data/index/records.jsonl into mirror/.

Priority order: stats workbooks and small documents first, audio zips/7z last,
so an interrupted run still has the high-value layer. Resumable: already-ok
URLs in the manifest are skipped.

Local layout mirrors the URL: mirror/file_pool/{token}/{filename}
(token is the 20-char opaque segment; guarantees uniqueness).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from .fetch import Fetcher

MIRROR = Path("mirror")


def dest_for(href: str) -> Path:
    path = urlparse(href).path  # strips scheme/host for absolute links
    parts = [p for p in path.split("/") if p]
    # .../files/file_pool/1/{token}/{filename}
    try:
        i = parts.index("file_pool")
        token, filename = parts[i + 2], unquote(parts[i + 3])
    except (ValueError, IndexError):
        token, filename = "misc", unquote(parts[-1]) if parts else "unnamed"
    return MIRROR / "file_pool" / token / filename


def bulk_ext(href: str) -> bool:
    p = urlparse(href).path.lower()
    return p.endswith(".zip") or p.endswith(".7z")


def main(limit: int | None = None, phase: str = "all"):
    recs = [json.loads(l) for l in open("data/index/records.jsonl", encoding="utf-8")]
    jobs = []
    for r in recs:
        for l in r["links"]:
            if not l["href"]:
                continue
            jobs.append((r["collection"], r["title"], l))
    # de-dup by href, preserve first occurrence's label
    seen = set()
    uniq = []
    for coll, title, l in jobs:
        if l["href"] in seen:
            continue
        seen.add(l["href"])
        uniq.append((coll, title, l))

    COLL_PRIORITY = {"gsat_stats": 0, "ast_stats": 1, "gsat_regular": 2, "ast_regular": 3,
                     "gsat_special": 4, "ast_special": 5, "gsat_special_sheet": 6, "ast_special_sheet": 7}
    uniq.sort(key=lambda j: COLL_PRIORITY.get(j[0], 9))
    small = [j for j in uniq if not bulk_ext(j[2]["href"])]
    bulky = [j for j in uniq if bulk_ext(j[2]["href"])]
    ordered = {"all": small + bulky, "small": small, "bulky": bulky}[phase]
    if limit:
        ordered = ordered[:limit]

    f = Fetcher(Path("data/manifest/downloads.jsonl"))
    stats = {"ok": 0, "skip": 0, "fail": 0}
    for n, (coll, title, l) in enumerate(ordered, 1):
        href = l["href"]
        already = f.seen.get(href if href.startswith("http") else "https://www.ceec.edu.tw" + href)
        if already and already.get("ok") and Path(already.get("local_path", "")).exists():
            stats["skip"] += 1
            continue
        try:
            rec = f.download(href, dest_for(href), label=f"{coll}|{l['label']}")
        except Exception as e:
            # one broken connection must not kill a multi-hour run
            print(f"EXC {type(e).__name__} {href}", flush=True)
            stats["fail"] += 1
            import time
            time.sleep(10)
            continue
        stats["ok" if rec["ok"] else "fail"] += 1
        if not rec["ok"]:
            print(f"FAIL [{rec['status']}/{rec['magic_class']}] {href}", flush=True)
        if n % 100 == 0:
            print(f"progress {n}/{len(ordered)} ok={stats['ok']} skip={stats['skip']} fail={stats['fail']}", flush=True)
    print(f"done: {stats}", flush=True)


if __name__ == "__main__":
    kw = {}
    if len(sys.argv) > 1:
        kw["phase"] = sys.argv[1]
    if len(sys.argv) > 2:
        kw["limit"] = int(sys.argv[2])
    main(**kw)
