"""Post-hoc refinement of the asset layer. Nothing is deleted.

1. Annotate every vector_region with advisory="textish" when its stored
   features say text block (density>4 chars/kpt², <3 curves) — these are
   underline/table-rule clusters around prose, not figures.
2. Old PDFs assemble one figure from many raster tiles. Group image
   crops per page by bbox adjacency (gap <= 8pt) and render one merged
   crop per group (kind="image_merged", members listed); member tiles
   get advisory="tile_of_merged".

Rewrites data/assets/index.jsonl in place (plus the merged rows).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pdfplumber

from .validate import load_manifest, local_for
from .run_extraction import parse_title

GAP = 8
DPI = 200


def textish(r):
    f = r.get("features") or {}
    return (r["kind"] == "vector_region"
            and f.get("text_density", 0) > 4 and f.get("n_curves", 99) < 3)


def groups(boxes):
    """Union-find over bboxes: same group if gaps <= GAP on both axes."""
    parent = list(range(len(boxes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if (a[0] - GAP <= b[2] and b[0] - GAP <= a[2]
                    and a[1] - GAP <= b[3] and b[1] - GAP <= a[3]):
                parent[find(i)] = find(j)
    out = defaultdict(list)
    for i in range(len(boxes)):
        out[find(i)].append(i)
    return [v for v in out.values() if len(v) >= 2]


def pdf_for_dir(records, manifest):
    """Map each asset out_dir back to its source PDF (same walk as
    extract_all_assets)."""
    m = {}
    for r in records:
        if r["collection"] not in ("gsat_regular", "ast_regular"):
            continue
        meta = parse_title(r["title"])
        if not meta:
            continue
        for l in r["links"]:
            p = local_for(l["href"], manifest)
            if p and l["label"].startswith("試題內容") and p.lower().endswith(".pdf"):
                sys_dir = meta["exam_system"] + ("補" if meta["sitting"] == "makeup" else "")
                sub = meta["subject_key"] or meta["subject_zh"]
                m[f"data/assets/{sys_dir}{meta['year_roc']}/{sub}"] = p
                break
    return m


def main():
    idx = Path("data/assets/index.jsonl")
    rows = [json.loads(l) for l in open(idx, encoding="utf-8")]
    n_text = 0
    for r in rows:
        if textish(r):
            r["advisory"] = "textish"
            n_text += 1

    records = [json.loads(l) for l in open("data/index/records.jsonl", encoding="utf-8")]
    pdfmap = pdf_for_dir(records, load_manifest())

    by_page = defaultdict(list)
    for r in rows:
        if r["kind"] == "image":
            by_page[(r["path"].rsplit("/", 1)[0], r["page"])].append(r)

    merged_rows, n_merged, missing = [], 0, 0
    by_pdf = defaultdict(list)
    for (d, page), tiles in by_page.items():
        if len(tiles) >= 2:
            by_pdf[d].append((page, tiles))

    for d, pages in sorted(by_pdf.items()):
        pdf = pdfmap.get(d)
        if not pdf or not Path(pdf).exists():
            missing += 1
            continue
        with pdfplumber.open(pdf) as doc:
            for page_no, tiles in pages:
                boxes = [t["bbox"] for t in tiles]
                for k, grp in enumerate(groups(boxes)):
                    x0 = min(boxes[i][0] for i in grp)
                    top = min(boxes[i][1] for i in grp)
                    x1 = max(boxes[i][2] for i in grp)
                    bot = max(boxes[i][3] for i in grp)
                    page = doc.pages[page_no - 1]
                    bbox = (max(0, x0), max(0, top),
                            min(page.width, x1), min(page.height, bot))
                    token = tiles[0]["path"].rsplit("/", 1)[1].split("_")[0]
                    out = Path(d) / f"{token}_p{page_no}_merge{k}.png"
                    page.crop(bbox).to_image(resolution=DPI).save(out)
                    base = tiles[0]
                    merged_rows.append({**{f: base[f] for f in
                                           ("exam_system", "year_roc", "sitting",
                                            "subject_key", "subject_zh", "item", "page")},
                                        "kind": "image_merged",
                                        "bbox": list(bbox),
                                        "path": str(out),
                                        "members": [boxes[i] for i in grp],
                                        "features": None})
                    for i in grp:
                        tiles[i]["advisory"] = "tile_of_merged"
                    n_merged += 1

    with open(idx, "w", encoding="utf-8") as f:
        for r in rows + merged_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"textish: {n_text}, merged crops: {n_merged}, unmapped dirs: {missing}")


if __name__ == "__main__":
    main()
