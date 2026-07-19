# CEEC Archive

A machine-readable, checksummed mirror of Taiwan's 大學入學考試中心 (CEEC)
public exam archive — 學測, 指考/分科測驗 — with extracted items where
reliable and honest flags where not.

**The contribution is the metadata layer** that existing benchmarks
(TMMLU+, TMLU, VisTW) discarded: per-item psychometrics from cohorts of
78k–118k real examinees, option-level response distributions, 題組
structure, multi-select items, original option order, provenance, and
segmented accessibility audio.

## Layout

```
ceec_archive/          crawler + parsers (python)
  fetch.py             polite fetcher; magic-byte checks; JSONL manifest
  scrape_index.py      both CMS patterns (xmfile flat / xmdoc two-level)
  download.py          prioritized, resumable mirror download
  parse_docx.py        style-driven paper parser w/ content fallback
  parse_pdf.py         coordinate-based paper parser (cross-check)
  parse_answers.py     選擇題答案 key tables
  parse_stats.py       選項分析 (T/H/L dists) + 答對率及鑑別度 (official P/D)
  extract_assets.py    embedded rasters + vector regions, item-linked
  validate.py          self-declared-structure checks; never relaxed
  run_extraction.py    orchestrator -> items.jsonl + run_report.json
data/index/records.jsonl      scraped index: 697 records, verbatim hrefs + labels
data/manifest/downloads.jsonl per-file: url, sha256, size, magic, status, label
data/items/{sys}/{yr}/{subj}.jsonl  canonical per-paper item files (416 papers)
data/parsed/                  items.jsonl (monolith), run_report.json
data/assets/{sys}{yr}/{subj}/ figure crops (vector regions + images), item-linked
                              index at data/assets/index.jsonl
mirror/                       raw files (not committed; rebuild via download.py)
```

Rebuild the mirror from scratch:

```
python3 -m ceec_archive.scrape_index      # ~120 polite requests
python3 -m ceec_archive.download all      # ~3,300 files, resumable
python3 -m ceec_archive.run_extraction    # parse + validate + join
```

## Source map

All under `https://www.ceec.edu.tw`. No robots.txt (the site 404s it with
an HTTP-200 error page — which is why every download is classified by
magic bytes, never by status code).

| collection | xsmsid | records | years (ROC) |
|---|---|---|---|
| 學測 一般試題 | `0J052424829869345634` | 189 | 83–115 |
| 分科/指考 一般試題 | `0J052427633128416650` | 241 | 91–114 |
| 學測 特殊試題 | `0J052392083839398563` | 98 | 99–115 |
| 分科 特殊試題 | `0J052424613319003165` | 59 | 103–107, 111–114 |
| 學測 特殊答題卷 | `0M111357021798465239` | 30 | 111–115 |
| 分科 特殊答題卷 | `0M111360260774151742` | 29 | 111–114 |
| 學測 統計資料 | `0J018604485538810196` | 27 | 83–115 |
| 分科 統計資料 | `0J018611000723433352` | 24 | 91–114 |

File URLs are `/files/file_pool/1/{20-char-token}/{name}.{ext}`; tokens are
opaque, so the index must be scraped. Hrefs are stored byte-for-byte —
filenames contain typos and are never reconstructed. Document type is
classified from the link label (試題內容, 選擇題答案, …), not the filename.

## Psychometrics (the distinctive layer)

Two workbook families per year, one sheet per subject:

- **選項分析** (`51-55_…選項分析{yr}.xls`): three rows per item (T=total,
  H=high, L=low group), option-level percentage distributions, `*` marks
  the keyed option, `*` on the item number marks multi-select.
- **答對率及鑑別度** (`42-46_…{yr}.xls`): official per-item columns
  `P, Ph, Pl, Pa..Pe, D, D1..D4`.

Definitions **printed in the workbooks' own footnotes** (113 學測 verified):

- `Ph` / `Pl` = 高分組/低分組 answer rate, defined as **top/bottom 33%**
  (not the commonly assumed 27%); `D = Ph − Pl`.
- `Pa..Pe` = five 20% ability bands (verified: mean(Pa..Pe) ≈ P).
- Multi-select `P` is a **partial-credit 得分率**
  (Σ score / (candidates × points), blanks = 0), not an all-correct rate.
  Multi-select option marginals double-count and must not be summed.

Coverage: item-level stats exist 91–115 (91 as per-subject PDFs, XLS from
92). 83–90 have only aggregate score tables (the "83" entry is a combined
83–89 bundle). Era drift: ≤97 workbooks leave the 組別 column header blank
(the parser sniffs it); section names drift (鑑別度 vs 鑑別指數 etc.) —
key on numeric filename prefixes, not names.

## Extraction status

Validation is structural self-consistency (papers declare their own item
ranges, option counts, and point totals; the answer key and stats files
independently mark free-response and multi-select items). Failures are
flagged per item and never silently reconciled; **thresholds are never
relaxed to make a run pass**.

Full-corpus run (all 一般試題 years): **410 papers → 14,943 items**,
mean PDF↔DOCX option-set agreement 99.88%. Years 111+ are near-clean;
flags concentrate in the 83–98 era where formatting conventions differ.
Per-paper details in `data/parsed/run_report.json`.

- 115 學測 國綜: 36/36 items, PDF and DOCX parses agree exactly.
- 115 學測 英文: 46/46 accounted for; 文意選填/篇章結構 bank items carry
  `not_extracted_bank_or_blank` flags (their content is a shared passage +
  word bank, not per-item text).
- 數學 DOCX option content lives in OMML that python-docx doesn't surface —
  routed to the known-hard bucket, PDF is the better source there.
- Legacy `.doc` papers are converted via LibreOffice for cross-checking;
  the PDF stays primary there because conversion strips semantic styles.

## Browsing: the semantic tree

`archive/` is a human-navigable view over the raw mirror, built by
`ceec_archive/build_tree.py` from index labels (never filenames):

```
archive/學測/115/國綜/試題內容.pdf … 選擇題答案.pdf
archive/學測/115/統計資料/選擇題選項分析/各科選擇題選項分析.xls
archive/學測/103/特殊試題/國文/試題內容(文字).zip
archive/指考/109/國文(補考)/…
```

The files are hardlinks of `mirror/file_pool/` blobs (identical content,
so git stores them once). `data/manifest/tree_map.jsonl` maps every
semantic path back to its pool path, source URL, and sha256. The 37
oversize audio files appear in the map with `in_git: false`.

## In-repo mirror

`mirror/file_pool/` holds the raw archive: the full document layer and all
audio ZIPs ≤100MB — 3,213 files in-repo of 3,250 mirrored (~14GB on disk).
Final crawl completeness: 3,250 of 3,273 indexed links; the 23 failures are
verified server-side dead links (every `.7z` in the index — the newest
115 學測 / 114 分科 audio — plus the 93補考 answer key). The 37 audio files
above GitHub's 100MB blob limit are listed in `.gitignore` and carry
checksums in the manifest; they exist only in offline copies (candidates
for GitHub Release assets, 2GB/file).

## Findings vs. the exploratory handoff

Answers to HANDOFF.md §9's open questions, established from the mirror:

1. **Audio production method:** per-item MP3 segmentation confirmed (one
   file per item; 對照表 track codes distinguish 題組 intros `Q0` from
   sub-questions `S0`). Human-vs-TTS still needs one listen.
2. **Flat-era stats layout:** same T/H/L schema back to 95, but the 組別
   column header is blank (parser sniffs it). 91 stats are per-subject PDFs.
3. **H/L definition:** 高分組/低分組 = top/bottom **33%** (workbook footnotes).
4. **特殊答題卷 archives:** blank answer-sheet templates (A3/A4,
   劃記/自填/電腦作答 variants), years 111+ only. Mirrored.
5. **分科特殊 gap 108–110:** real — absent from the live index.
6. **83/90 stats oddities:** "83" is a combined 83–89 bundle of aggregate
   score tables; item-level psychometrics genuinely start at 91/92.
7. **pagesize=50:** works (5× fewer index requests).
8. **Word coverage** (magic-byte verified, `data/parsed/word_coverage_by_year.json`):
   學測 has Word source for every year 83–115; 指考/分科 from 92 (91 PDF-only).
   True `.docx` begins at 103–105 — earlier than the handoff's ~113 estimate —
   with stragglers (`.doc`) as late as 108.

Known server-side gaps (verified, recorded in the manifest): the 93補考
answer key 404s, and all 14 of 115 學測's audio `.7z` links return the CMS
error page despite being listed in the index.

## Working with the item database

The canonical parsed data is partitioned per paper under
`data/items/{system}/{year}/{subject}.jsonl` (makeup sittings under
`學測補`/`指考補`). Single-file artifacts are derived on demand:

```
python3 -m ceec_archive.partition_items          # rebuild partition from monolith
python3 -m ceec_archive.partition_items concat   # stream one big JSONL to stdout
python3 -m ceec_archive.partition_items sqlite   # -> data/items.db (not committed)
```

Figure crops live under `data/assets/{system}{year}/{subject_key}/` with
`data/assets/index.jsonl` mapping each crop to (exam_system, year_roc,
subject, item, kind, bbox, raw features). Item linking is geometric
(nearest stem above on the page) — treat it as advisory.

## Legal posture (unsettled — recorded, not resolved)

ROC Copyright Act Art. 9(1)(5) excludes 依法令舉行之各類考試試題及其備用試題
from copyright. That exclusion attaches to the exam questions specifically —
not to rubrics or answer sheets (less settled), and not to third-party
passages/figures quoted inside items (a separate rights layer, densest in
國文/英文). This repo therefore keeps the **manifest and index public**
(checksums, URLs, capture dates) while treating redistribution of source
documents as a downstream decision. Per-file `label`/`doc_type` fields make
takedowns surgical.

## Provenance

Scraped and mirrored 2026-07-18 with ~1s request spacing, single
connection. Every file's SHA-256, size, first-8 magic bytes, Content-Type,
capture timestamp, and index label are in `data/manifest/downloads.jsonl`.
