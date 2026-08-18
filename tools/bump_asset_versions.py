#!/usr/bin/env python3
"""
bump_asset_versions.py — append content-hash query strings to dynamic
asset references in song HTML so browsers refetch only when an asset
actually changes.

Why:
  Song pages set `Cache-Control: no-cache` (see `_headers`), which
  guarantees freshness via revalidation but costs a round-trip on
  every visit. Per-song JSON assets (`data.json`, `tts_manifest.json`)
  don't change often, so a content-hashed query string lets browsers
  cache them indefinitely and skip the round-trip — they only refetch
  when the hash flips.

What it does:
  For each `songs/<slug>/index.html`, rewrite references like
      `/songs/${SONG}/data.json`   →  `/songs/${SONG}/data.json?v=<sha8>`
      `'./tts_manifest.json'`      →  `'./tts_manifest.json?v=<sha8>'`
  The hash is sha256(file)[:8] of the sibling asset. Re-running the
  script with no asset changes is a no-op; if an asset changes the
  hash updates and HTML is rewritten in place.

Quote-anchored: only rewrites references immediately followed by a
quote (`'`, `"`, `` ` ``), so prose like `'data.json missing ...'`
and comments are left alone.

Run before every commit that touches a song's `data.json` or
`tts_manifest.json`. See CLAUDE.md and tools/README.md.
"""

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Asset basenames that get content-hash query strings. Each is resolved
# relative to the HTML file being processed (sibling lookup).
PER_SONG_ASSETS = ["data.json", "tts_manifest.json"]


def short_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def bump_html(html_path: Path) -> list[tuple[str, str]]:
    text = html_path.read_text()
    original = text
    bumps: list[tuple[str, str]] = []

    for asset_name in PER_SONG_ASSETS:
        asset_path = html_path.parent / asset_name
        if not asset_path.is_file():
            continue
        h = short_hash(asset_path)
        escaped = re.escape(asset_name)
        # Match the asset name with an optional existing ?v=hex,
        # only when immediately followed by a JS string delimiter.
        pattern = re.compile(rf"({escaped})(\?v=[a-f0-9]+)?(?=['\"`])")
        new_text, n = pattern.subn(rf"\1?v={h}", text)
        if n and new_text != text:
            bumps.append((asset_name, h))
            text = new_text

    if text != original:
        html_path.write_text(text)
    return bumps


def main() -> int:
    html_files = sorted(ROOT.glob("songs/*/index.html"))
    if not html_files:
        print("No song HTML files found under songs/*/index.html.")
        return 0

    any_changes = False
    for html_path in html_files:
        bumps = bump_html(html_path)
        rel = html_path.relative_to(ROOT)
        if bumps:
            any_changes = True
            for name, h in bumps:
                print(f"  {rel}: {name} -> ?v={h}")

    if not any_changes:
        print("No version bumps needed (all asset hashes already current).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
