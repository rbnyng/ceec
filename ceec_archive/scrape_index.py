"""Scrape CEEC index pages into a records manifest.

Two CMS patterns (HANDOFF.md §1):
- flat:    /xmfile?xsmsid=...&page=N[&pagesize=50]  -> table.rwdTable rows
- twolevel:/xmdoc?xsmsid=...&page=N -> entry links /xmdoc/cont?xsmsid=...&sid=...
           detail pages carry h4.ext_title sections with ul.ext_list file links
           (ABSOLUTE hrefs — do not grep for href="/files/)

Hrefs are stored verbatim, exactly as scraped. Labels are stored because
document type is classified from the label, not the filename.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

from .fetch import Fetcher, BASE

COLLECTIONS = {
    # key: (kind, xsmsid, human name)
    "gsat_regular":       ("flat", "0J052424829869345634", "學測 一般試題"),
    "ast_regular":        ("flat", "0J052427633128416650", "分科/指考 一般試題"),
    "gsat_special":       ("flat", "0J052392083839398563", "學測 特殊試題"),
    "ast_special":        ("flat", "0J052424613319003165", "分科 特殊試題"),
    "gsat_special_sheet": ("flat", "0M111357021798465239", "學測 特殊答題卷"),
    "ast_special_sheet":  ("flat", "0M111360260774151742", "分科 特殊答題卷"),
    "gsat_stats":         ("twolevel", "0J018604485538810196", "學測 統計資料"),
    "ast_stats":          ("twolevel", "0J018611000723433352", "分科 統計資料"),
}

PAGESIZE = 50  # verified working 2026-07-18 (51 rows incl header on page 1)


def _links_from_cell(cell) -> list[dict]:
    out = []
    for a in cell.find_all("a"):
        out.append({
            "label": a.get_text(strip=True),
            "href": a.get("href", ""),          # verbatim
            "title_attr": a.get("title", ""),
            "css_class": " ".join(a.get("class", [])),
        })
    return out


def scrape_flat(f: Fetcher, key: str, xsmsid: str) -> list[dict]:
    records, page = [], 1
    while True:
        url = f"{BASE}/xmfile?xsmsid={xsmsid}&page={page}&pagesize={PAGESIZE}"
        soup = BeautifulSoup(f.get_html(url), "lxml")
        tbl = soup.find("table", class_="rwdTable")
        rows = tbl.find_all("tr")[1:] if tbl else []
        if not rows:
            break
        for tr in rows:
            date = tr.find("td", class_="date")
            title = tr.find("td", class_="title")
            dl = tr.find("td", class_="download")
            records.append({
                "collection": key,
                "xsmsid": xsmsid,
                "index_page": page,
                "date": date.get_text(strip=True) if date else "",
                "title": title.get_text(strip=True) if title else "",
                "links": _links_from_cell(dl) if dl else [],
            })
        # is there a next page? (widget shows 下一頁 only when one exists)
        pages_div = soup.find("div", class_="pages") or soup.find("ul", class_="pages")
        has_next = pages_div and "下一頁" in pages_div.get_text()
        if not has_next:
            break
        page += 1
    return records


def scrape_twolevel(f: Fetcher, key: str, xsmsid: str) -> list[dict]:
    entries, page = [], 1
    while True:
        url = f"{BASE}/xmdoc?xsmsid={xsmsid}&page={page}"
        soup = BeautifulSoup(f.get_html(url), "lxml")
        found = 0
        for a in soup.find_all("a", href=re.compile(r"^/xmdoc/cont\?xsmsid=" + xsmsid)):
            entries.append({
                "entry_href": a["href"],
                "entry_title": a.get_text(strip=True),
                "index_page": page,
            })
            found += 1
        pages_div = soup.find("div", class_="pages") or soup.find("ul", class_="pages")
        has_next = pages_div and "下一頁" in pages_div.get_text()
        if found == 0 or not has_next:
            break
        page += 1
    # dedupe (nav repetition)
    seen, uniq = set(), []
    for e in entries:
        if e["entry_href"] not in seen:
            seen.add(e["entry_href"])
            uniq.append(e)
    records = []
    for e in uniq:
        soup = BeautifulSoup(f.get_html(BASE + e["entry_href"]), "lxml")
        sections = []
        for h4 in soup.find_all("h4", class_="ext_title"):
            ul = h4.find_next_sibling("ul", class_="ext_list")
            sections.append({
                "section": h4.get_text(strip=True),
                "links": _links_from_cell(ul) if ul else [],
            })
        records.append({
            "collection": key,
            "xsmsid": xsmsid,
            "index_page": e["index_page"],
            "date": "",
            "title": e["entry_title"],
            "entry_href": e["entry_href"],
            "sections": sections,
            "links": [l for s in sections for l in s["links"]],
        })
    return records


def main(out_path="data/index/records.jsonl", only: list[str] | None = None):
    f = Fetcher(Path("data/manifest/downloads.jsonl"))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    all_records = []
    for key, (kind, xsmsid, name) in COLLECTIONS.items():
        if only and key not in only:
            continue
        print(f"[{key}] {name} ({kind}) ...", flush=True)
        recs = (scrape_flat if kind == "flat" else scrape_twolevel)(f, key, xsmsid)
        nlinks = sum(len(r["links"]) for r in recs)
        print(f"  -> {len(recs)} records, {nlinks} links", flush=True)
        all_records.extend(recs)
    with open(out, "w", encoding="utf-8") as fh:
        for r in all_records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(all_records)} records to {out}")


if __name__ == "__main__":
    main(only=sys.argv[1:] or None)
