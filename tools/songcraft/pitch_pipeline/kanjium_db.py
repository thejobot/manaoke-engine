"""Kanjium accents.txt loader. Format per line:
    <surface>\t<reading_kana>\t<accent_csv>

Where <accent_csv> can be:
    "0"        — single accent
    "0,2"      — multiple accepted accents (NHK lists alternates)
    "0,2,3"    — three options

License: CC-BY-SA 4.0 (kanjium maintainer Uros O., NHK-derived).
"""
import os
from collections import defaultdict
from .morae import kata_to_hira

_DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'kanjium_accents.txt')
_db: dict[tuple[str,str], list[int]] | None = None

def _load() -> dict[tuple[str,str], list[int]]:
    global _db
    if _db is not None: return _db
    db = defaultdict(list)
    with open(_DB_PATH, encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 3: continue
            surface, reading, accents = parts[0], parts[1], parts[2]
            for a in accents.split(','):
                a = a.strip()
                if a.isdigit():
                    db[(surface, reading)].append(int(a))
                    # also key by reading-alone for kana-only lookups
                    db[(reading, reading)].append(int(a))
    _db = db
    return _db

def lookup(surface: str, reading_kana: str | None = None) -> list[int] | None:
    """Return list of accent numbers if found, else None.

    Tries (surface, reading) first, falls back to (surface, surface) if
    reading isn't supplied, then tries hira/kata-normalized reading.
    """
    db = _load()
    if reading_kana:
        hits = db.get((surface, reading_kana)) or db.get((surface, kata_to_hira(reading_kana)))
        if hits: return list(dict.fromkeys(hits))  # dedupe preserving order
    # Try surface-only (kana-only words like なんぼ live in this path)
    hits = db.get((surface, surface)) or db.get((surface, kata_to_hira(surface)))
    return list(dict.fromkeys(hits)) if hits else None
