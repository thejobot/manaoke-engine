#!/usr/bin/env python3
"""
parity_audit.py — prove a built song page is a FAITHFUL clone of the template.

The whole builder rests on one invariant: a song page must be structurally
identical to the reference page ("creep hype" / inochi), differing ONLY in
  - the per-line teaching data (LINE_TR / LINE_EXPLAIN),
  - the cover palette (--field-c* + the dark base tones),
  - identity + asset paths (title / artist / slug / YT id / og / canonical /
    version chip / podcast + kit paths).
Everything else — every line of CSS, every line of JS, the whole study/sing/
immerse machinery — must match byte-for-byte, so every behavior the reference
nails (translation-toggle flow, word-by-word scroll/anchor, ...) is inherited,
not reimplemented or patched. Round 2 broke this by injecting per-song patches;
this audit makes that class of drift impossible to ship silently.

It normalizes away the legitimately-varying regions on BOTH files and diffs the
remainder. Clean == faithful. Any surviving diff is real structural drift and is
printed. Exit 0 = parity, 1 = drift.

Usage: python3 parity_audit.py <song_slug> <template_dir> [--key <build key>]
"""
import argparse, difflib, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from template_dir import require_template

ROOT = Path(__file__).resolve().parents[2]
SONGS = ROOT / 'songs'
BUILDS = Path(__file__).resolve().parent / 'builds'


def excise(html, decl):
    """Replace `decl{...}` or `decl[...]` (balanced, string-aware) with a stub."""
    try:
        start = html.index(decl)
    except ValueError:
        return html
    i = start + len(decl)
    if i >= len(html) or html[i] not in '{[':
        return html
    open_c, close_c = html[i], ('}' if html[i] == '{' else ']')
    depth, j, in_str, esc = 0, i, None, False
    while j < len(html):
        c = html[j]
        if in_str:
            if esc: esc = False
            elif c == '\\': esc = True
            elif c == in_str: in_str = None
        else:
            if c in '"\'`': in_str = c
            elif c == open_c: depth += 1
            elif c == close_c:
                depth -= 1
                if depth == 0:
                    break
        j += 1
    return html[:start] + decl + open_c + '§DATA§' + close_c + html[j+1:]


def normalize(html, identity):
    # 1. per-song data literals (teaching maps + the drill-audio map that
    #    build_drill_concat injects post-assemble)
    html = excise(html, 'const LINE_TR = ')
    html = excise(html, 'const LINE_EXPLAIN = ')
    html = excise(html, 'const DRILL_MAP = ')
    # 2. palette: field vars (c1/2/3/hi + fb1/2/3 bloom accents) + any hex color
    #    value (base-radial + body-gradient stops etc.) — all cover-derived.
    html = re.sub(r'--field-(c1|c2|c3|hi|fb1|fb2|fb3):\d+,\s*\d+,\s*\d+', r'--field-\1:§RGB§', html)
    html = re.sub(r'#[0-9a-fA-F]{6}\b', '§HEX§', html)
    # 2b. gradient-lab dials (Round 11): per-song speed/amp/motion splices are
    #     data, not structure — durations, amp multiplier, and the html-level
    #     data-field-motion attribute all normalize away.
    html = re.sub(r'--fdur-(drift-sheet|drift|breath|a|b|c):[\d.]+s', r'--fdur-\1:§DUR§', html)
    html = re.sub(r'--field-amp:[\d.]+', '--field-amp:§AMP§', html)
    # excise (not value-stub) the html-level motion attr: the template <html>
    # carries no attribute for the default drift, so a motion override would
    # otherwise diff by the tag line itself.
    html = re.sub(r'(<html\b[^>]*?)\s*data-field-motion="[a-z]+"', r'\1', html)
    # 3. identity + asset paths (both files -> same placeholders)
    html = re.sub(r"const SONG = '[^']*';", "const SONG = '§SLUG§';", html)
    html = re.sub(r"const YT_ID = '[^']*';", "const YT_ID = '§YT§';", html)
    html = re.sub(r"const PODCAST_URL = '[^']*';", "const PODCAST_URL = '§POD§';", html)
    html = re.sub(r'(songs/)[a-z0-9-]+(/">)', r'\1§SLUG§\2', html)      # og/canonical
    html = re.sub(r'_assets/[a-z0-9-]+/(kit|audio|pitch_data|images)/', r'_assets/§F§/\1/', html)
    html = re.sub(r'/fonts/[a-z0-9-]+/', '/fonts/§F§/', html)          # per-song subset fonts (§4.9)
    html = re.sub(r'/songs/[a-z0-9-]+/(audio|pitch_data|images)/', r'/songs/§SLUG§/\1/', html)
    html = re.sub(r'Manaoke_[a-z0-9-]+_', 'Manaoke_§F§_', html)     # kit file basenames
    html = re.sub(r'\?v=[0-9a-f]{6,}', '?v=§V§', html)              # asset content hashes
    html = re.sub(r"const AUDIO_V = '[0-9a-f]*';", "const AUDIO_V = '§AV§';", html)  # per-song audio version splice
    html = re.sub(r'(<div class="u-version"[^>]*>)[^<]*(</div>)', r'\1§CHIP§\2', html)
    html = re.sub(r'<title>[^<]*</title>', '<title>§TITLE§</title>', html)
    html = re.sub(r'(content=")[^"]*(")', lambda m: m.group(1) + '§META§' + m.group(2)
                  if any(k in m.group(0).lower() for k in ('title', 'description', 'url', 'image'))
                  else m.group(0), html)
    # identity strings anywhere (title_jp/en, artist, artist_en on both sides)
    for s in sorted([x for x in identity if x], key=len, reverse=True):
        html = html.replace(s, '§ID§')
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('slug'); ap.add_argument('template'); ap.add_argument('--key', default='')
    a = ap.parse_args()
    song = (SONGS / a.slug / 'index.html').read_text()
    # the recorded template dir can be gone (pruned 2026-07-28) — one shared
    # resolver falls back to the live page of the same song, loudly
    tdir = require_template(a.template, '[parity]')
    tmpl = (tdir / 'index.html').read_text()

    # identity strings to blank on each side (from each side's own data.json)
    tmpl_ident, song_ident = [], []
    try:
        td = json.loads((tdir / 'data.json').read_text())
        tmpl_ident = [td.get(k, '') for k in ('title_jp', 'title_en', 'artist', 'artist_en')]
    except Exception:
        pass
    try:
        sd = json.loads((SONGS / a.slug / 'data.json').read_text())
        song_ident = [sd.get(k, '') for k in ('title_jp', 'title_en', 'artist', 'artist_en')]
    except Exception:
        pass

    ns = normalize(song, song_ident)
    nt = normalize(tmpl, tmpl_ident)
    if ns == nt:
        print(f'✓ PARITY: songs/{a.slug} is a faithful clone of {a.template} '
              f'(only data/palette/paths differ).')
        return 0

    diff = list(difflib.unified_diff(nt.splitlines(), ns.splitlines(),
                                     fromfile=f'{a.template} (normalized)',
                                     tofile=f'{a.slug} (normalized)', lineterm='', n=1))
    drift = [d for d in diff if d.startswith(('+', '-')) and not d.startswith(('+++', '---'))]
    print(f'✗ DRIFT: songs/{a.slug} diverges from {a.template} in {len(drift)} lines '
          f'beyond data/palette/paths:\n')
    print('\n'.join(diff[:120]))
    if len(diff) > 120:
        print(f'... (+{len(diff)-120} more diff lines)')
    return 1


if __name__ == '__main__':
    sys.exit(main())
