"""Extract figure crops for every paper in the corpus.

Output: data/assets/{system}{year}[補]/{subject_key}/{uid}.png plus a
per-paper assets json, and a corpus-level index at
data/assets/index.jsonl keyed by the same identity fields as items.jsonl
(item linking via each paper's PDF parse positions).
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from .validate import load_manifest, local_for
from .run_extraction import parse_title
from .parse_pdf import parse_pdf
from .extract_assets import extract_assets

OUT = Path("data/assets")


def main():
    records = [json.loads(l) for l in open("data/index/records.jsonl", encoding="utf-8")]
    manifest = load_manifest()
    index = open(OUT / "index.jsonl", "w", encoding="utf-8")
    done = failed = 0
    for r in records:
        if r["collection"] not in ("gsat_regular", "ast_regular"):
            continue
        meta = parse_title(r["title"])
        if not meta:
            continue
        pdf = None
        for l in r["links"]:
            p = local_for(l["href"], manifest)
            if p and l["label"].startswith("試題內容") and p.lower().endswith(".pdf"):
                pdf = p
                break
        if not pdf:
            continue
        sys_dir = meta["exam_system"] + ("補" if meta["sitting"] == "makeup" else "")
        sub = meta["subject_key"] or meta["subject_zh"]
        out_dir = OUT / f"{sys_dir}{meta['year_roc']}" / sub
        if (out_dir / "_done").exists():
            done += 1
            continue
        try:
            parsed = parse_pdf(pdf)
            pos = [{"number": i["number"], "page": i["page"], "top": i["top"]}
                   for i in parsed["items"]]
            assets = extract_assets(pdf, out_dir, pos)
            for a in assets:
                if a.get("file"):    # rasterized crops only in the index
                    index.write(json.dumps({
                        "exam_system": meta["exam_system"],
                        "year_roc": meta["year_roc"],
                        "sitting": meta["sitting"],
                        "subject_key": meta["subject_key"],
                        "subject_zh": meta["subject_zh"],
                        "item": a.get("item"),
                        "kind": a["kind"],
                        "page": a["page"],
                        "bbox": a["bbox"],
                        "path": str(out_dir / a["file"]),
                        "features": a.get("features"),
                    }, ensure_ascii=False) + "\n")
            index.flush()
            (out_dir / "_done").touch()
            done += 1
        except Exception:
            failed += 1
            print(f"FAIL {r['title']}: {traceback.format_exc(limit=1).strip().splitlines()[-1]}",
                  flush=True)
        if (done + failed) % 25 == 0:
            print(f"progress: {done} done, {failed} failed", flush=True)
    index.close()
    print(f"finished: {done} papers, {failed} failed")


if __name__ == "__main__":
    main()
