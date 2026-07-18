"""Polite HTTP fetcher for the CEEC archive mirror.

Rules (learned the hard way in the exploratory session, see HANDOFF.md §0):
- Never rebuild a URL: callers must pass scraped hrefs byte-for-byte.
- HTTP 200 is not success: an HTML error page comes back with status 200.
  Every download is classified by magic bytes, not status or extension.
- Single connection, ~1s between requests, resumable from the manifest.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE = "https://www.ceec.edu.tw"
USER_AGENT = "ceec-archive-mirror/0.1 (research archival)"
DELAY_S = 1.0

MAGIC = [
    (b"PK\x03\x04", "zip"),          # docx / xlsx / audio zip
    (b"\xd0\xcf\x11\xe0", "ole"),    # legacy .doc / .xls
    (b"%PDF", "pdf"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),   # 22 of these in the special archives
]
HTML_PREFIXES = (b"<!DOCTYPE", b"<!doctype", b"<html", b"<HTML", b"\r\n<!DOC", b"\n<!DOC")


def classify_magic(head: bytes) -> str:
    for sig, name in MAGIC:
        if head.startswith(sig):
            return name
    stripped = head.lstrip(b"\r\n\t \xef\xbb\xbf")
    for pfx in HTML_PREFIXES:
        if stripped.startswith(pfx.strip()):
            return "html_error"
    return "unknown"


@dataclass
class FetchRecord:
    url: str
    status: int
    sha256: str
    size: int
    magic8: str          # hex of first 8 bytes
    magic_class: str     # zip | ole | pdf | html_error | unknown
    content_type: str
    captured_at: str
    label: str = ""      # link label from the index (試題內容 etc.)
    local_path: str = ""
    ok: bool = False


class Fetcher:
    def __init__(self, manifest_path: Path, delay_s: float = DELAY_S):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.manifest_path = Path(manifest_path)
        self.delay_s = delay_s
        self._last_request = 0.0
        self.seen: dict[str, dict] = {}
        if self.manifest_path.exists():
            with open(self.manifest_path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rec = json.loads(line)
                        self.seen[rec["url"]] = rec

    def _throttle(self):
        wait = self.delay_s - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _log(self, rec: FetchRecord) -> dict:
        d = asdict(rec)
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        self.seen[rec.url] = d
        return d

    def get_html(self, url: str) -> str:
        """Fetch an index/detail page (not manifest-logged; pages are re-scraped freely)."""
        self._throttle()
        resp = self.session.get(url, timeout=60)
        resp.raise_for_status()
        return resp.text

    def download(self, href: str, dest: Path, label: str = "",
                 force: bool = False) -> dict:
        """Download one file. `href` is used verbatim (joined to BASE only if
        root-relative). Returns the manifest record dict."""
        url = urljoin(BASE + "/", href) if not href.startswith("http") else href
        if not force and url in self.seen and self.seen[url].get("ok"):
            prior = self.seen[url]
            if prior.get("local_path") and Path(prior["local_path"]).exists():
                return prior
        self._throttle()
        resp = self.session.get(url, timeout=120)
        body = resp.content
        head = body[:8]
        magic_class = classify_magic(body[:64])
        ok = resp.status_code == 200 and magic_class not in ("html_error",) and len(body) > 0
        dest = Path(dest)
        if ok:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
        rec = FetchRecord(
            url=url,
            status=resp.status_code,
            sha256=hashlib.sha256(body).hexdigest(),
            size=len(body),
            magic8=head.hex(),
            magic_class=magic_class,
            content_type=resp.headers.get("Content-Type", ""),
            captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            label=label,
            local_path=str(dest) if ok else "",
            ok=ok,
        )
        return self._log(rec)
