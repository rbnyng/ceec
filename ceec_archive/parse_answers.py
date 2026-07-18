"""Parse 選擇題答案 PDFs into {item_number: key_string}.

Layout: one or more tables with repeated (題號, 答案) column pairs.
Multi-select keys are letter runs ('ABD'); 數學 keys may be digits or
fraction-like strings — stored verbatim.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pdfplumber

NUM = re.compile(r"^\d{1,3}$")


def parse_answers(path: str | Path) -> dict[int, str]:
    keys: dict[int, str] = {}
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            for tbl in page.find_tables():
                rows = tbl.extract()
                if not rows:
                    continue
                header = [(c or "").replace(" ", "") for c in rows[0]]
                pairs = [(i, i + 1) for i, h in enumerate(header[:-1])
                         if "題號" in h and "答案" in header[i + 1]]
                if not pairs:
                    # headerless continuation table: assume alternating pairs
                    pairs = [(i, i + 1) for i in range(0, len(rows[0]) - 1, 2)]
                    start = 0
                else:
                    start = 1
                for row in rows[start:]:
                    for qi, ai in pairs:
                        if qi >= len(row) or ai >= len(row):
                            continue
                        q = (row[qi] or "").strip()
                        a = (row[ai] or "").strip().replace(" ", "").replace("\n", "")
                        if NUM.match(q) and a:
                            keys[int(q)] = a
    return keys


if __name__ == "__main__":
    for p in sys.argv[1:]:
        ks = parse_answers(p)
        print(p)
        print(f"  {len(ks)} keys: " + " ".join(f"{n}:{k}" for n, k in sorted(ks.items())[:40]))
