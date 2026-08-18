#!/usr/bin/env python3
"""
landing_card.py — add a song's Norelco card to the library landing.

Preview mode (default): writes a self-contained landing COPY that includes the
new card, at songs/<slug>/landing.html, so the owner can see the Norelco + click
through WITHOUT touching the live root landing (root only changes on promote).

Promote mode (--promote): inserts the card into the real root index.html SONGS
array and does not write the preview copy.

Usage:
  python3 landing_card.py <slug> --title-jp .. --artist .. --len 03:52 \
      --art <url> --accent '#rrggbb' [--promote]
"""
import argparse, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def make_entry(title, artist, url, accent, art_url, length, coming_soon=False):
    # a small, on-theme SVG fallback (used only if the real jacket 404s)
    svg = (f'<svg viewBox="0 0 60 60" shape-rendering="crispEdges" xmlns="http://www.w3.org/2000/svg">'
           f'<rect width="60" height="60" fill="{accent}"/>'
           f'<rect x="0" y="52" width="60" height="8" fill="#1a1333"/>'
           f'<circle cx="30" cy="26" r="12" fill="#1a1333" opacity="0.35"/></svg>')
    soon = 'comingSoon: true, ' if coming_soon else ''
    return f"""    {{
      title: '{title}', artist: '{artist}', lang: 'JA', len: '{length}', {soon}
      url: '{url}',
      color: 't-gold', reelColor: '{accent}',
      // Card band accent = this song's living-gradient dominant (--field-c1),
      // sampled from the album cover by assemble_page.py.
      cardAccent: '{accent}', cardInk: '#f3e8cf',
      artUrl: '{art_url}',
      art: `{svg}`
    }},
"""


def insert_entry(html, entry):
    # insert just before the closing `];` of `const SONGS = [ ... ];`
    m = re.search(r'const SONGS = \[', html)
    if not m:
        raise SystemExit('SONGS array not found in landing')
    i = m.end()
    depth = 1
    while depth:
        i += 1
        if html[i] == '[':
            depth += 1
        elif html[i] == ']':
            depth -= 1
    # html[i] is the closing ']'; back up to just after the last entry's comma/newline
    return html[:i] + entry + html[i:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('slug')
    ap.add_argument('--title-jp', required=True); ap.add_argument('--artist', required=True)
    ap.add_argument('--art', required=True); ap.add_argument('--accent', required=True)
    ap.add_argument('--len', dest='length', default='03:52'); ap.add_argument('--promote', action='store_true')
    ap.add_argument('--coming-soon', action='store_true')
    ap.add_argument('--onto', default='', help='build another song\'s preview landing (its slug) instead of root')
    a = ap.parse_args()

    base = (ROOT / 'songs' / a.onto / 'landing.html') if a.onto else (ROOT / 'index.html')
    root_html = base.read_text()
    url = '#' if a.coming_soon else f'/songs/{a.slug}/'
    entry = make_entry(a.title_jp, a.artist, url, a.accent, a.art, a.length, a.coming_soon)

    if a.promote:
        (ROOT / 'index.html').write_text(insert_entry(root_html, entry))
        print(f'root landing: added Norelco card for {a.slug}')
    else:
        preview = insert_entry(root_html, entry)
        # write onto an existing preview landing (--onto) or start one at this slug's dir
        out = base if a.onto else (ROOT / 'songs' / a.slug / 'landing.html')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(preview)
        rel = out.relative_to(ROOT)
        print(f'preview landing with card -> {rel} (live root untouched). '
              f'URL: https://manaoke.app/{rel}')


if __name__ == '__main__':
    main()
