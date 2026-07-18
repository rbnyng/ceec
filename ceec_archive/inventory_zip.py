"""Inventory ZIP/7z archives without extracting.

ZIP entry names from CEEC are Big5 stored without the UTF-8 flag, so the
zipfile module decodes them as cp437: round-trip cp437→big5 for the human-
readable name and keep the raw form too (HANDOFF §2c).

For MP3 entries, pull duration/bitrate + encoder tags from the first bytes
(mutagen on an in-memory read) — enough to characterise the audio without
unpacking the corpus.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path


def fix_name(name: str) -> tuple[str, bool]:
    """cp437-mojibake -> big5. Returns (decoded, was_mojibake)."""
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name, False   # already decoded properly (UTF-8 flag set)
    for enc in ("big5", "cp950"):
        try:
            return raw.decode(enc), True
        except UnicodeDecodeError:
            continue
    return name, False


def inventory(path: str | Path, probe_audio: bool = True) -> dict:
    path = Path(path)
    out = {"path": str(path), "entries": [], "flags": []}
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        out["flags"].append("not_a_zip")
        return out
    for info in zf.infolist():
        decoded, mojibake = fix_name(info.filename)
        entry = {
            "name_raw": info.filename,
            "name": decoded,
            "size": info.file_size,
            "compressed": info.compress_size,
        }
        if probe_audio and decoded.lower().endswith((".mp3", ".wav", ".m4a")):
            try:
                import mutagen
                data = zf.read(info)
                m = mutagen.File(io.BytesIO(data))
                if m is not None:
                    entry["duration_s"] = round(getattr(m.info, "length", 0), 1)
                    entry["bitrate"] = getattr(m.info, "bitrate", None)
                    entry["tags"] = {k: str(v)[:80] for k, v in (m.tags or {}).items()
                                     } if m.tags else {}
                    # encoder fingerprints often reveal TTS pipelines
                    enc = getattr(m.info, "encoder_info", "") or ""
                    if enc:
                        entry["encoder"] = enc
            except Exception as e:
                entry["audio_probe_error"] = str(e)[:80]
        out["entries"].append(entry)
    return out


if __name__ == "__main__":
    for p in sys.argv[1:]:
        inv = inventory(p)
        print(json.dumps(inv, ensure_ascii=False, indent=1))
