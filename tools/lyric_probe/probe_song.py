#!/usr/bin/env python3
"""Probe Apple Music for a song's TTML lyrics (timing granularity + text).

Usage: python3 tools/lyric_probe/probe_song.py <apple-song-id> [out-prefix]

WHY THIS EXISTS: LyriCool's public release (commit 0167a47) deliberately
DROPPED its Apple Music client, so tools/fetch_lyrics.py's `lyricool.py json`
path is gone. These api.py/parser.py/config.py are the pre-release client
recovered from ~/lyricool git history; tokens still come from
~/.lyricool-config.json. Saves <prefix>_{syllable,line}.ttml next to cwd.
Timing "None"/0ms everywhere = Apple has TEXT ONLY (use whisper_sync).
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api import fetch_song_info, fetch_lyrics, fetch_syllable_lyrics
from config import load_config, validate_config
from parser import parse_ttml

import sys as _sys
SONG_ID = _sys.argv[1] if len(_sys.argv) > 1 else "1593160065"
PREFIX = _sys.argv[2] if len(_sys.argv) > 2 else "probe"

cfg = load_config()
try:
    validate_config(cfg)
except Exception as e:
    print(f"CONFIG INVALID: {e}"); sys.exit(2)
print(f"storefront={cfg.get('storefront')!r}")

try:
    info = fetch_song_info(SONG_ID, cfg)
    attrs = info.get("attributes", info) if isinstance(info, dict) else {}
    print(f"TRACK OK: {attrs.get('artistName')} — {attrs.get('name')} "
          f"({attrs.get('albumName')}, {attrs.get('durationInMillis')}ms)")
    print(f"hasLyrics={attrs.get('hasLyrics')} hasTimeSyncedLyrics={attrs.get('hasTimeSyncedLyrics')}")
except Exception as e:
    print(f"SONG INFO FAILED: {e}"); sys.exit(1)

for label, fn in (("syllable", fetch_syllable_lyrics), ("line", fetch_lyrics)):
    try:
        ttml = fn(SONG_ID, cfg)
        if not ttml:
            print(f"{label}: EMPTY"); continue
        lines = parse_ttml(ttml)
        n_words = sum(len(l.get("words") or []) for l in lines)
        first = lines[0] if lines else {}
        last = lines[-1] if lines else {}
        print(f"{label}: {len(lines)} lines, {n_words} timed words; "
              f"first={first.get('begin_ms')}ms {first.get('text','')[:25]!r} "
              f"last={last.get('begin_ms')}ms {last.get('text','')[:30]!r}")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{PREFIX}_{label}.ttml")
        with open(out, "w") as f: f.write(ttml)
        print(f"  saved {out} ({len(ttml)} bytes)")
    except Exception as e:
        print(f"{label}: FAILED — {e}")
