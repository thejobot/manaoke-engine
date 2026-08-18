#!/usr/bin/env python3
"""verify_palette.py — QA for assemble_page.cover_palette().

Reusable builder QA step. Two jobs:
  1. REGRESSION: run the derivation on INOCHI's own cover and diff every
     palette-derived value against the template's baked literals. Inochi's look
     is the approved reference and must NOT visibly change — each derived RGB
     channel must land within TOL of the baked value (fb / base / body).
  2. PREVIEW: print the derived palette for other songs (odoriko = COLORFUL
     multi-hue -> yellow/red/orange blooms; shinunoga = GREY -> neutral silver
     blooms) so a human can eyeball that the field will read like the art.

Usage:
    python3 tools/songcraft/verify_palette.py            # inochi regression + previews
    python3 tools/songcraft/verify_palette.py <song-dir> ...   # extra previews

Song art URLs are read from songs/<dir>/data.json (apple_lyrics.song.artwork_url).
Exits non-zero if the inochi regression exceeds tolerance.
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SONGS = ROOT / 'songs'
BUILDS = Path(__file__).resolve().parent / 'builds'
sys.path.insert(0, str(Path(__file__).resolve().parent))
from assemble_page import (cover_palette, c1_chain, hex_rgb, load_gradient_design,
                           pale_error, fmt_num, FDUR_BASES)

TOL = 12  # max allowed per-channel drift on the inochi reproduction

# The template's CURRENT baked inochi literals (songs/inochi-mijikashi-v098):
#   #livingField .fb1/2/3 blooms, #livingField base radial stops, body gradient.
BAKED = {
    'fb1':  (194, 134, 150), 'fb2': (176, 122, 140), 'fb3': (150, 96, 118),
    'base1': (0x2a, 0x0f, 0x20), 'base2': (0x18, 0x06, 0x11), 'base3': (0x0c, 0x03, 0x09),
    'g1': (0x3b, 0x27, 0x30), 'g2': (0x4a, 0x31, 0x3c), 'g3': (0x2a, 0x18, 0x22), 'g4': (0x16, 0x0a, 0x10),
}


def art_url(song_dir):
    d = json.loads((SONGS / song_dir / 'data.json').read_text())
    return d.get('apple_lyrics', {}).get('song', {}).get('artwork_url')


def hex2rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def flatten(pal):
    """cover_palette() tuple -> {name: (r,g,b)} for every palette-derived value."""
    c1, c2, c3, hi, card, fb, base, body = pal
    out = {'c1': c1, 'c2': c2, 'c3': c3, 'hi': hi}
    out.update({'fb1': fb[0], 'fb2': fb[1], 'fb3': fb[2]})
    out.update({'base1': hex2rgb(base[0]), 'base2': hex2rgb(base[1]), 'base3': hex2rgb(base[2])})
    out.update({'g1': hex2rgb(body[0]), 'g2': hex2rgb(body[1]), 'g3': hex2rgb(body[2]), 'g4': hex2rgb(body[3])})
    out['card'] = hex2rgb(card)
    return out


def check_overrides():
    """GRADIENT LAB QA (Round 11): for every song whose merged override block
    (gradient.defaults.json + design.gradient) is non-empty, the BUILT page must
    carry exactly the spliced values — colors, --fdur durations, --field-amp,
    the data-field-motion attr — plus the c1-chain (base/body/cardaccent) when
    c1 is overridden, and the pale gate unless force_pale was recorded by
    `gradient set --force-pale`. Returns a list of failure strings."""
    fails, checked = [], 0

    def need(html, tok, what, key):
        # declaration tokens must not be a prefix of a longer number
        # (--field-amp:1.4 must not pass on --field-amp:1.45)
        if re.search(re.escape(tok) + r'(?![\d.])', html):
            print(f'  ok  {what}: {tok}')
        else:
            print(f'  ✗   {what}: {tok} NOT in built page')
            fails.append(f'{key}: {what}')

    for bs in sorted(BUILDS.glob('*.build_state.json')):
        key = bs.name[:-len('.build_state.json')]
        try:
            slug = json.loads(bs.read_text()).get('slug', '')
        except Exception:
            continue
        g = load_gradient_design(key)
        if not g:
            continue
        checked += 1
        print(f'\n=== OVERRIDE {key} (songs/{slug}) — {json.dumps(g, ensure_ascii=False)} ===')
        page = SONGS / slug / 'index.html'
        if not page.exists():
            print('  page not assembled yet — splice check skipped (rebuild first)')
            continue
        html = page.read_text()
        for f in ('c1', 'c2', 'c3', 'hi'):
            if f in g:
                r, gg, b = hex_rgb(g[f])
                need(html, f'--field-{f}:{r},{gg},{b}', f'{f} splice', key)
        for i, hx in enumerate(g.get('fb', []), 1):
            r, gg, b = hex_rgb(hx)
            need(html, f'--field-fb{i}:{r},{gg},{b}', f'fb{i} splice', key)
        if 'c1' in g:
            card, base, body = c1_chain(hex_rgb(g['c1']))
            for i, hx in enumerate(base, 1):
                need(html, f'--field-base{i}:{hx}', f'base{i} c1-chain', key)
            for i, hx in enumerate(body, 1):
                need(html, f'--body-g{i}:{hx}', f'g{i} c1-chain', key)
            accf = BUILDS / f'{key}.cardaccent.txt'
            got = accf.read_text().strip() if accf.exists() else '(missing)'
            if got == card:
                print(f'  ok  cardaccent.txt == {card}')
            else:
                print(f'  ✗   cardaccent.txt {got} != c1-chain {card}')
                fails.append(f'{key}: cardaccent')
        speed = float(g.get('speed', 1.0))
        amp = float(g.get('amp', 1.0))
        motion = g.get('motion', 'drift')
        dials = speed != 1.0 or amp != 1.0 or motion != 'drift'
        if dials and '--fdur-drift:' not in html:
            print('  ✗   dials overridden but the built page has no --fdur vars '
                  '(pre-Round-11 template — rebuild from a Round-11 template)')
            fails.append(f'{key}: dials on old template')
        else:
            if speed != 1.0:
                for name, base_s in FDUR_BASES.items():
                    need(html, f'--fdur-{name}:{fmt_num(base_s / speed)}s', f'fdur {name}', key)
            if amp != 1.0:
                need(html, f'--field-amp:{fmt_num(amp)}', 'amp splice', key)
            tag = re.search(r'<html\b[^>]*>', html).group(0)
            attr = re.search(r'data-field-motion="([a-z]+)"', tag)
            got_m = attr.group(1) if attr else 'drift'
            if got_m == motion:
                print(f'  ok  motion attr = {motion}' + (' (absent)' if motion == 'drift' else ''))
            else:
                print(f'  ✗   motion attr = {got_m}, want {motion}')
                fails.append(f'{key}: motion attr')
        if g.get('force_pale'):
            print('  (force_pale recorded — pale gate skipped)')
        else:
            for f in ('c1', 'c2', 'c3', 'hi'):
                err = pale_error(f, g[f]) if f in g else None
                if err:
                    print('  ✗   ' + err)
                    fails.append(f'{key}: pale {f}')
            for hx in g.get('fb', []):
                err = pale_error('fb', hx)
                if err:
                    print('  ✗   ' + err)
                    fails.append(f'{key}: pale fb')
    print(f'\noverride QA: {checked} overridden song(s), {len(fails)} failure(s)'
          if checked else '\noverride QA: no design.gradient overrides recorded — nothing to check')
    return fails


def main():
    print('=== INOCHI regression (derived vs baked template literals) ===')
    pal = cover_palette(art_url('inochi-mijikashi-v098'))
    if not pal:
        print('FAIL: could not fetch inochi cover'); sys.exit(2)
    got = flatten(pal)
    worst = 0
    print(f'{"var":6} {"baked":>15} {"derived":>15} {"Δ per chan":>14}')
    for name, baked in BAKED.items():
        d = got[name]
        deltas = [abs(d[i] - baked[i]) for i in range(3)]
        worst = max(worst, *deltas)
        flag = '' if max(deltas) <= TOL else '  <-- OVER TOL'
        print(f'{name:6} {str(baked):>15} {str(d):>15} {str(tuple(deltas)):>14}{flag}')
    print(f'worst channel drift = {worst} (tolerance {TOL})')
    ok = worst <= TOL

    previews = sys.argv[1:] or ['odoriko-z5z2gv', 'shinunoga-gl7lrr']
    for song in previews:
        print(f'\n=== PREVIEW {song} ===')
        u = art_url(song)
        if not u:
            print('  no artwork_url'); continue
        p = cover_palette(u)
        if not p:
            print('  fetch failed'); continue
        g = flatten(p)
        for k in ('c1', 'hi', 'fb1', 'fb2', 'fb3', 'base1', 'g1', 'g4', 'card'):
            print(f'  {k:6} {g[k]}')

    ofails = check_overrides()

    verdicts = []
    if not ok:
        verdicts.append('inochi drift over tolerance')
    if ofails:
        verdicts.append(f'{len(ofails)} override splice failure(s): {"; ".join(ofails[:6])}')
    print('\nRESULT:', 'PASS' if not verdicts else 'FAIL (' + ' + '.join(verdicts) + ')')
    sys.exit(0 if not verdicts else 1)


if __name__ == '__main__':
    main()
