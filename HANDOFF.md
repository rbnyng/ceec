# CEEC Archive — Handoff Spec

**Goal:** a complete, machine-readable, checksummed mirror of Taiwan's 大考中心
(CEEC) public exam archive, with a manifest, extracted items where reliable, and
honest flags where not.

**Non-goal:** a clean benchmark. TMMLU+, TMLU, and VisTW already exist and did
that. The contribution here is the *metadata layer* those projects discarded:
per-item psychometrics, 題組 structure, multi-select items, original option
order, provenance, and segmented audio.

---

## 0. Read this first: how to not repeat my mistakes

I made five errors in the exploratory session. Four were the same error: inferring
server or file behaviour from partial evidence when checking cost one command.

| mistake | consequence | rule |
|---|---|---|
| Reconstructed a URL from memory, "correcting" the typo `分科科驗`→`分科測驗` | Got an HTML error page, concluded the file was broken server-side | **Never rebuild a URL. Use scraped hrefs byte-for-byte.** CEEC filenames contain typos and inconsistencies. |
| Read HTTP 200 as success | Wrote 4KB of HTML into a `.docx` | **Check magic bytes on every download.** See §3. |
| Concluded `&page=N` didn't work because one `web_fetch` ignored it | Nearly gave up on 30 years of back-catalogue | **Test the parameter directly before concluding anything about it.** |
| Regex `\.docx?` matched `.doc` and `.docx` alike | Claimed true DOCX exists back to 1994; it does not | **Record the actual extension and magic bytes, never a normalised "has word file" boolean.** |
| Guessed 文字版 meant a plain-text rendition | It's audio; the label describes the *source* used to narrate | **Open one file of any new type before theorising about the type.** |

The general rule: this archive rewards checking and punishes inference. A
validation harness that fails loudly is worth more than any parser improvement.

---

## 1. Source map

All under `https://www.ceec.edu.tw`. Two CMS patterns.

### 1a. Flat file tables (`/xmfile?xsmsid=...`)

Server-rendered HTML tables. Paginate with `&page=N` (1-indexed). Page size
selector goes up to 50 (`&pagesize=50` appeared to work; verify). Each row =
one exam-subject-year with 2–5 download links.

| collection | xsmsid | pages | records | years |
|---|---|---|---|---|
| 學測 一般試題 | `0J052424829869345634` | 19 | 189 | 83–115 |
| 分科/指考 一般試題 | `0J052427633128416650` | 25 | 241 | 91–114 |
| 學測 特殊試題 | `0J052392083839398563` | 10 | 98 | 99–115 |
| 分科 特殊試題 | `0J052424613319003165` | 6 | 59 | 103–107, 111–114 |
| 學測 特殊答題卷 | `0M111357021798465239` | ? | ? | **UNMAPPED** |
| 分科 特殊答題卷 | `0M111360260774151742` | ? | ? | **UNMAPPED** |

In 一般試題 rows the link **labels** are stable even when filenames are not:
`試題內容` (paper; may appear twice — PDF and Word), `答題卷` (answer sheet),
`選擇題答案` / `選擇(填)題答案` (MC key), `非選擇題評分原則` (free-response rubric).
**Classify document type from the label, not the filename.**

### 1b. Two-level doc pages (`/xmdoc?xsmsid=...`)

Index lists year entries; each links to `/xmdoc/cont?xsmsid=...&sid=...` where
the actual files live. Paginate the index with `&page=N`.

| collection | xsmsid | entries | years |
|---|---|---|---|
| 學測 統計資料 | `0J018604485538810196` | 27 | 83–115 |
| 分科 統計資料 | `0J018611000723433352` | 24 | 91–114 |

**Trap:** on detail pages the file links are **absolute** (`https://www.ceec.edu.tw/files/...`),
not root-relative. A `href="/files/` pattern silently returns zero. Detail-page
markup is:

```html
<h4 class="ext_title icon_file">考生基本資料</h4>
<ul class="ext_list">
  <li><a href="https://www.ceec.edu.tw/files/file_pool/1/{tok}/{name}.xls"
         title="報名人數統計總表(xls) (開新視窗)" fpctsid="{tok}">報名人數統計總表</a>...
```

### 1c. File URL shape

```
/files/file_pool/1/{20-char-token}/{urlencoded-filename}.{ext}
```
Token is opaque and non-enumerable — you **must** scrape the index. Filename
carries subject/year/type but inconsistently (`試卷` vs `試題`,
`01-114學測國綜試題` vs `01-114學年度分科數甲`, and at least one typo).

---

## 2. What's in each collection

### 2a. Exam papers (一般試題)

Up to 5 docs per record. Word source is present for most years but the **format
differs by era** and this is load-bearing:

| era | Word format | magic | parse route |
|---|---|---|---|
| ~113–115 | true `.docx` | `PK\x03\x04` | semantic styles — cheap, reliable |
| older (verified back to 83) | legacy `.doc` (OLE) | `\xd0\xcf\x11\xe0` | LibreOffice → docx → tab/regex heuristics |
| all years | PDF | `%PDF` | coordinate-based; also the only source for assets |

Verified: 84年 papers are OLE `.doc`. After `soffice --headless --convert-to docx`
the semantic styles are **gone** (304× `Normal`), but the tab convention survives:
items are `N.\t{stem}`, options are `(A) …\t(B) …`. That's parseable.

### 2b. Statistics (統計資料) — the distinctive layer

Sections (names drift, see §5): 考生基本資料, 成績統計, 非選擇題閱卷/人工閱卷,
答對率及鑑別度/鑑別指數, 選擇題選項分析.

The two that matter most:
- `42-46_各科答對率及鑑別度表{yr}.xls` — per-item p-value and discrimination
- `51-55_各科選擇題選項分析{yr}.xls` — **option-level response distributions**

Coverage: 學測 has option analysis 91–115, discrimination reliably 98+ (92–96
looked thinner — verify). 分科 has both for **all 24 years, 91–114**, unbroken.

**Verified workbook layout (115 學測, `51-55_...xls`, xlrd — it's old binary `.xls`):**
- One sheet per subject (國文/英文/數學A/數學B/社會/自然)
- Row 2 carries 報考人數 / 缺考人數 / 到考人數
- Header row starts with `題號`; columns: 題號, 組別, 未答, then A–J
- **Three rows per item**: `T` (total), `H` (high group), `L` (low group)
- `*` prefix on a cell marks a keyed option
- `*` prefix on the **question number** marks a multi-select item
- 數學 sheets use numeric option labels (`1.0`–`5.0` — Excel stored them as floats)

Derived: `p = Σ T[keys]`, `D = (Σ H[keys] − Σ L[keys]) / 100`.

Sanity figures from 115: 208 items, 37 multi-select, cohorts 78k–118k.
19 of 171 single-select items had a distractor outdraw the key.
Lowest discrimination: 英文 Q19 (p=31%, D=−0.07), 國文 Q24 (p=14%, D=−0.04).

**Unverified:** whether 92–98 (flat-era) workbooks share this layout. Section
*names* changed across eras, so the schema plausibly did too. **Check before
assuming a single parser works.**

### 2c. Accessibility (特殊試題) — audio

~226 audio ZIPs across both archives. Three parallel renditions per subject-year:
`文字版`, `圖文版`, `點字版` — these name the **source used to narrate**, not the
output format. All are audio.

Verified `114sat_語音_國寫_文字版.zip` (6.9MB): 8 MP3s, segmented and named by
structural unit — `01_封面`, `02_非選擇題說明`, `03_第一大題題幹`,
`04_第一大題問題一`, … `08_試題結束`. **Per-item alignment is in the filenames.**

Covers MC subjects, not just 國寫: 115 學測 has 國綜/國寫/英文/數B; 114 分科 has
公民與社會/歷史/地理. Each record also ships a `音軌內容對照表` PDF and an NVDA `.doc`.

**Encoding trap:** ZIP entry names need `cp437` → `big5` round-trip. Store raw
bytes *and* decoded form.

**UNKNOWN — check early, it changes the value a lot:** is the audio human-read or
TTS? One listen answers it.

---

## 3. Crawl requirements

**Two phases, strictly separated.** Crawl and cache everything to disk first;
parse offline. You will rewrite extraction a dozen times and must not re-hit the
server for it.

Per download, record: source URL (verbatim), SHA-256, byte size, HTTP status,
**first 8 magic bytes**, Content-Type, capture timestamp, and the link label from
the index.

Magic-byte dispatch — this catches all four real cases:
```
PK\x03\x04       → zip container (docx / xlsx / audio zip)
\xd0\xcf\x11\xe0 → OLE (legacy .doc / .xls)
%PDF             → pdf
<!DOCTYPE / <html → SERVER ERROR PAGE, treat as failure regardless of status 200
```

Politeness: ~1s between requests, single connection, resume from manifest. Total
volume is small (roughly 640 records → a few thousand files, low single-digit GB);
there is no reason to be aggressive. Check `robots.txt` first.

Expect ~44 index pages for the flat archives plus 51 detail pages for the stats.

---

## 4. Parsers (starting code provided)

Three exist and work, with known limits.

### `parse_exam4.py` — PDF, coordinate-based

**The key insight, which took ten iterations to find:** item numbers sit at a
consistent hanging-indent x-position (x=64 in the sample; stems at x=82). Detect
them by coordinate, not by `^\d+\.` — PDF text ordering does *not* respect logical
line starts, and the number token frequently serialises *after* the stem it
introduces. The margin is auto-detected as the modal x0 of bare-number tokens, so
it self-calibrates per document.

**Second non-obvious bug:** row clustering by `round(top/4)` split a number
(top=306.39) from its stem (top=305.85) into adjacent buckets over 0.54pt. Use
tolerance-based clustering (±5pt), not fixed-width bucketing. Fixed five items at once.

Also handles: `find_tables()` for table-formatted options (recovers 婚前/婚後
two-column layouts intact); x-gap sidebar splitting to pull marginal glosses into
a separate `asides` field; section declarations (`第1題至第24題`) to drive expected
option counts.

Result on 113 學測國綜: 36/36 items, 139 options, validation clean.

**Known residual:** 3 of 139 options contaminated where a sidebar box shares rows
with option markers (Q21). Two heuristics conflict — option rows must be protected
from sidebar splitting (needed for Q13, where `(D)丁` sits at x=425), which
re-admits the sidebar. Proper fix: use the box border `rects` (74 on that page) to
define an exclusion zone.

### `parse_docx.py` — style-driven

Clean first-run on 114 學測國綜 (36 items). **But style vocabularies differ per
subject and per era** — this is the main thing to solve:

| document | stem style | passage style |
|---|---|---|
| 114 國綜 | `TIT1` | `tit2` |
| 114/113 歷史 | `樣式 樣式 TIT1 + 加寬 1.2 pt…` | `題組總題幹` |
| 114 數學B | `List Paragraph` (no semantic style) | — |
| converted legacy .doc | `Normal` (all semantics lost) | — |

Names are decorative but derivative: **substring-match** on `TIT1`, `AB`, `ABCD`,
`題組`, then fall back to content heuristics (`^\d+\.`, presence of `(A)`) when
names are uninformative. The fallback is what the PDF parser already does, so the
two converge.

**Known bug:** paragraph-only walk skips `<w:tbl>`, losing table-formatted options
(113 國綜 Q6 → 0/4). Fix: traverse `document.body` in document order.

### `parse_stats.py` — XLS psychometrics

Handles the T/H/L three-row layout, `*` keyed options, `*` multi-select question
numbers, and numeric option labels. Uses `xlrd` (files are old binary `.xls`).

**Note:** integer percentages, so multi-select rows may not sum to 100.
**Unverified:** how CEEC defines the H/L groups. Commonly top/bottom 27%, but I
did not confirm it, and every discrimination figure depends on it. Find the
methodology note or flag D as convention-dependent.

### `extract_assets.py` — figures

Embedded rasters + rasterised vector regions + full-page renders, with stable UIDs
(`{sha1_10}_p{page}_{kind}{n}`) and bbox in the manifest.

**Vector art is the common case:** the sample paper had 3 embedded images but 265
rects and 6 curves. Chemistry/geometry/map figures in 自然/地理 will be vector.
Region rasterisation is the only way to preserve them.

Classification (`diagram` / `graphic` / `framed_text` / `mixed_stimulus`) via text
density and curve count is **advisory only** — my threshold of 12 was arbitrary
and untuned. Store the raw features (`n_chars`, `n_curves`, `area`,
`text_density`) and let consumers filter. Never delete.

**Item linking:** my text-overlap matching is brittle. Use geometry — an asset
belongs to the item whose stem has the nearest y-coordinate above it on the same
page. You have item positions from the PDF parser; it's a join on `(page, top)`.

---

## 5. Schema

One row per item. Suggested fields:

```
source:      archive, xsmsid, index_page, record_label,
             source_url, sha256, capture_date, file_magic, file_ext
identity:    exam_system (學測|指考|分科), year_roc, year_ce,
             subject_key (stable ascii), subject_zh, curriculum (99課綱|108課綱)
item:        number, section_kind (單選題|多選題|混合題|非選擇題),
             multi_select, expected_options, n_options,
             stem, options{A..J}, option_source (inline|table),
             group_id, group_passage, stimulus, has_subquestions, free_response
answer:      keys[], (from 選擇題答案 pdf)
psychometrics: p_value, p_high, p_low, discrimination,
             option_dist_T{}, option_dist_H{}, option_dist_L{}, omit_rate,
             n_candidates, n_absent, n_sat
assets:      asset_uids[], page, bbox
audio:       zip_sha256, mp3_entry, segment_label
provenance:  extracted_by (pdf|docx|doc_converted), validation_flags[],
             cross_check (agree|disagree|single_source)
```

Normalisation notes:
- **Always store both ROC and CE year.** 民國114 = 2025. Everyone outside Taiwan
  gets this wrong once.
- **Stable ascii subject keys** (`chinese`, `math_a`, `math_jia`, `civics`,
  `history`) alongside the Chinese label. Subject sets change across the
  指考→分科 transition (指考 had 國文/英文; 分科 dropped them), so the schema
  must tolerate that rather than assume a fixed list.
- **Record the exam-system generation and 課綱.** Without it, longitudinal
  comparison silently mixes incomparable instruments. The 111 boundary
  (國文→國綜/國寫, 數學→數學A/數學B) is a real discontinuity.
- **Terminology drift in the stats archive** — key on the numeric filename
  prefixes (`11_`, `21_`, `41_`, `42-46_`, `51-55_`), not the Chinese names:
  - 鑑別度 (108+) vs 鑑別指數 (98–107)
  - 選擇題選項分析 (111+) vs 選擇(填)題選項分析 (98–110)
  - 非選擇題閱卷 (102+) vs 人工閱卷 (98–101)

Join key across sources: `(exam_system, year_roc, subject_key, item_number)`.
Verify on one paper before trusting it — the multi-select flag appears in both the
stats file and the parsed paper, so disagreement there is a cheap integrity check.

---

## 6. Validation — build this first

The reason this task suits autonomous iteration is that correctness is
**objectively checkable without human review**: papers declare their own structure.

- `說明：第1題至第24題` gives expected item numbering
- `一、單選題` / `二、多選題` gives expected option count (4 vs 5)
- `（占 76 分）` gives point totals
- The separately-published 選擇題答案 PDF gives the key
- The stats workbook independently states which items are multi-select

Required checks: missing/extra item numbers; option count vs section
expectation; empty stems or options; group members without a passage; parsed
multi-select set vs stats-file multi-select set; PDF-vs-DOCX item count agreement.

**Do not relax a validation threshold to make a run pass.** That is the obvious
reward hack here and the one thing that would quietly ruin the output. If a
document fails, flag it and move on. Partial coverage with honest flags beats
silent completion.

Emit per-run: documents processed, items extracted, validation failures grouped by
type, and a cross-check agreement rate. Those numbers are how you tell "needs a
new style rule" from "genuinely hard document" from "bad URL" — three very
different problems that look identical in a silent pipeline.

---

## 7. Scope and priorities

**Mirror everything.** Bytes are cheap; the archival case doesn't care what's
interesting, and some of this (audio, braille, legacy `.doc`) exists nowhere else.

**Extract selectively.** Rough value order:

1. **公民與社會, 歷史, 地理** — the most distinctive content. Taiwan-specific
   constitutional/electoral/historical material, absent from MMLU and framed
   differently from PRC-oriented C-Eval/CMMLU. Parses reasonably.
2. **國文, 英文** — linguistic interest, best psychometrics, cleanest DOCX.
3. **自然, 物理, 化學, 生物** — prose-heavy with occasional formulas; middling.
4. **數學** — **explicitly out of scope.** Option content lives in OMML that
   python-docx doesn't surface (`(1) \t(2) \t(3)` with nothing between), so the
   DOCX is *worse* than the PDF. Route to a "known-hard, not attempted" bucket.
   The 非選擇題評分原則 rubrics for maths are still worth mirroring — step-level
   partial-credit criteria are scarce and don't require solving OMML.

---

## 8. Legal / publication

Not settled, and the spec should not pretend otherwise. Record the facts, let
downstream decide.

- ROC Copyright Act **Art. 9(1)(5)** excludes 依法令舉行之各類考試試題及其備用試題
  from copyright entirely. The site's blanket `版權所有` footer is a CMS default
  and cannot create a right the statute withholds — but the exclusion attaches to
  **試題 specifically**, not to everything on the domain.
- **Less settled**, flag rather than assert: 選擇題答案 (arguably part of the
  item), 非選擇題評分原則 (analytical prose by the marking committee), 答題卷
  (a form layout). I would be least confident about the rubrics.
- **Separate rights layer:** passages and figures the exam adapted from third
  parties. The 113 國綜 paper alone draws on Ackerman, Ekman, Saramago criticism,
  a typography book, and a 2023 film synopsis. Art. 9 removes protection from the
  *question*, not from a quoted work inside it. Densest in 國文/英文; images in
  自然/地理 are a live version of the same issue.
- Taiwan uses enumerated 合理使用 conditions (Arts. 44–65), not US four-factor
  fair use. Art. 46 covers reproduction for teaching **by schools**; whether that
  extends to third-party bulk redistribution is untested as far as I know.

**Recommended shape:** private complete mirror + public index/manifest
immediately (checksums, URLs, capture dates — uncontroversially yours and the
thing nobody else has). Publish extracted structured items ahead of source PDFs
where third-party content is heaviest. Keep a `doc_type` and `rights_note` field
per file so takedowns are surgical. Feed URLs to Wayback — free, independent, and
under an institution with actual legal footing.

Realistic risk is a DMCA notice from a textbook publisher, not litigation — an
availability problem, which is exactly why the offline mirror matters.

---

## 9. Open questions — verify, don't assume

1. **Audio: human-read or TTS?** Changes the value substantially. One listen.
2. **Flat-era (92–98) stats workbook layout** — same T/H/L schema as 115, or different?
3. **H/L group definition** — top/bottom 27%? Every D value depends on it.
4. **特殊答題卷 archives** (`0M111357021798465239`, `0M111360260774151742`) — unmapped.
5. **分科 特殊試題 gap at 108–110** — real, or an artefact of my sampling?
6. **83 and 90 stats entries** — 23 files and 3 files respectively, neither fitting
   the usual pattern. Look directly.
7. **`&pagesize=50`** — appeared to work, cuts index requests ~5×. Confirm.
8. **Word coverage per year** — I sampled, didn't enumerate. The manifest should
   *produce* this answer rather than assume it.

---

## 10. Suggested order

1. `robots.txt`; build the crawler with magic-byte checking and manifest logging
2. Crawl the two 一般試題 indexes → cache → manifest (answers Q8 as a byproduct)
3. Crawl the two 統計資料 archives (two-level; watch the absolute URLs)
4. Crawl the two 特殊試題 archives; inventory ZIP contents without extracting
5. Map the two 特殊答題卷 archives (Q4)
6. Wire up validation harness against the cached corpus
7. Run all three parsers; measure PDF-vs-Word agreement
8. Add per-subject style rules for the DOCX path, guided by failures
9. Parse stats workbooks; join to items; cross-check multi-select flags
10. Asset extraction with geometric item linking
11. Emit flat item table + manifest; write the README with the caveats from §5, §8

Steps 1–5 are mechanical and the urgent part — the archive is the deliverable
even if extraction never improves. Steps 6–11 are where judgement is needed and
where check-ins matter.
