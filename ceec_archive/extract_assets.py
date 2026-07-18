"""Extract figures/assets from exam PDFs.

Vector art is the common case (HANDOFF §4): chemistry/geometry/map figures
are drawn with rects+curves, not embedded rasters. So we extract:
  1. embedded raster images
  2. rasterised regions of clustered vector graphics
Classification thresholds are advisory only — raw features are stored and
nothing is deleted. Item linking is geometric: an asset belongs to the item
whose stem starts nearest above it on the same page.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pdfplumber

PAD = 4
MIN_REGION_AREA = 1200.0   # pt^2; tiny rules/underlines are not figures
CLUSTER_GAP = 12.0         # pt; merge shapes closer than this


def _sha10(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:10]


def _merge_boxes(boxes: list[tuple]) -> list[tuple]:
    """Union-find style merge of overlapping/near boxes."""
    boxes = list(boxes)
    merged = True
    while merged:
        merged = False
        out = []
        while boxes:
            b = boxes.pop()
            for i, o in enumerate(out):
                if (b[0] - CLUSTER_GAP <= o[2] and b[2] + CLUSTER_GAP >= o[0]
                        and b[1] - CLUSTER_GAP <= o[3] and b[3] + CLUSTER_GAP >= o[1]):
                    out[i] = (min(b[0], o[0]), min(b[1], o[1]),
                              max(b[2], o[2]), max(b[3], o[3]))
                    merged = True
                    break
            else:
                out.append(b)
        boxes = []
        if merged:
            boxes = out
        else:
            return out
    return boxes


def extract_assets(pdf_path: str | Path, out_dir: str | Path,
                   item_positions: list[dict] | None = None,
                   dpi: int = 200) -> list[dict]:
    """item_positions: [{'number': n, 'page': p, 'top': y}, ...] from parse_pdf."""
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    uid_base = _sha10(pdf_path)
    assets = []

    def link_item(page_no: int, top: float):
        if not item_positions:
            return None
        cands = [i for i in item_positions
                 if i["page"] == page_no and i["top"] <= top + 2]
        if not cands:
            return None
        return max(cands, key=lambda i: i["top"])["number"]

    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            # 1. embedded rasters
            for k, img in enumerate(page.images):
                bbox = (max(img["x0"], 0), max(img["top"], 0),
                        min(img["x1"], page.width), min(img["bottom"], page.height))
                if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    continue
                uid = f"{uid_base}_p{pno}_img{k}"
                # tiny inline images are glyph substitutions (rare CJK chars
                # outside the font), not figures: record, don't rasterize
                if (bbox[2] - bbox[0]) < 24 or (bbox[3] - bbox[1]) < 24:
                    assets.append({"uid": uid, "page": pno, "kind": "inline_glyph",
                                   "bbox": [round(v, 1) for v in bbox],
                                   "item": link_item(pno, bbox[1])})
                    continue
                try:
                    page.crop(bbox).to_image(resolution=dpi).save(out_dir / f"{uid}.png")
                except Exception as e:
                    assets.append({"uid": uid, "page": pno, "kind": "image",
                                   "bbox": bbox, "error": str(e)})
                    continue
                assets.append({
                    "uid": uid, "page": pno, "kind": "image",
                    "bbox": [round(v, 1) for v in bbox],
                    "item": link_item(pno, bbox[1]),
                    "file": f"{uid}.png",
                })
            # 2. vector regions: cluster rects+curves+lines
            shapes = []
            for s in page.rects + page.curves + page.lines:
                w, h = s["x1"] - s["x0"], s["bottom"] - s["top"]
                if w < 2 and h < 2:
                    continue
                shapes.append((s["x0"], s["top"], s["x1"], s["bottom"]))
            for k, box in enumerate(_merge_boxes(shapes)):
                area = (box[2] - box[0]) * (box[3] - box[1])
                if area < MIN_REGION_AREA:
                    continue
                # full-width boxes spanning most of the page are layout chrome
                if (box[2] - box[0]) > page.width * 0.9 and (box[3] - box[1]) > page.height * 0.85:
                    continue
                bbox = (max(box[0] - PAD, 0), max(box[1] - PAD, 0),
                        min(box[2] + PAD, page.width), min(box[3] + PAD, page.height))
                region = page.crop(bbox)
                n_chars = len(region.chars)
                n_curves = len(region.curves)
                uid = f"{uid_base}_p{pno}_vec{k}"
                try:
                    region.to_image(resolution=dpi).save(out_dir / f"{uid}.png")
                except Exception as e:
                    assets.append({"uid": uid, "page": pno, "kind": "vector_region",
                                   "bbox": bbox, "error": str(e)})
                    continue
                assets.append({
                    "uid": uid, "page": pno, "kind": "vector_region",
                    "bbox": [round(v, 1) for v in bbox],
                    "item": link_item(pno, bbox[1]),
                    "file": f"{uid}.png",
                    "features": {          # raw, advisory-only (HANDOFF §4)
                        "n_chars": n_chars,
                        "n_curves": n_curves,
                        "area": round(area, 1),
                        "text_density": round(n_chars / area * 1000, 3) if area else None,
                    },
                })
    (out_dir / f"{uid_base}_assets.json").write_text(
        json.dumps({"source": str(pdf_path), "assets": assets},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return assets


if __name__ == "__main__":
    from .parse_pdf import parse_pdf
    for p in sys.argv[1:]:
        parsed = parse_pdf(p)
        pos = [{"number": i["number"], "page": i["page"], "top": i["top"]}
               for i in parsed["items"]]
        assets = extract_assets(p, "data/assets/" + Path(p).stem, pos)
        linked = sum(1 for a in assets if a.get("item"))
        print(f"{p}: {len(assets)} assets ({linked} item-linked)")
