#!/usr/bin/env python3
"""
assemble_page.py — clone the production page onto an authored song.

Runs AFTER content_to_data.py (which already wrote songs/<slug>/data.json +
tts_manifest.json). This copies the template's index.html + timestamp-recorder
into songs/<slug> WITHOUT clobbering that data, then:
  - splices the per-line LINE_TR / LINE_EXPLAIN literals (from builds/<key>.line_maps.json),
  - retargets SONG / YT_ID / og:url / canonical / <title> / version chip,
  - swaps the living-gradient palette (--field-c1/2/3/hi, --field-fb1/2/3 bloom
    accents, --field-base1/2/3 base radial, --body-g1..4 body gradient) to the cover's,
  - adds the per-song _redirects audio rewrite above the generic fallthrough,
  - prints the cardAccent hex for the landing Norelco card.

Usage: python3 assemble_page.py <key> <slug> <asset_folder> <template_dir> [--yt ID] [--title-jp T] [--artist A]
"""
import argparse, colorsys, hashlib, io, json, re, shutil, sys, urllib.request
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from template_dir import require_template

ROOT = Path(__file__).resolve().parents[2]
SONGS = ROOT / 'songs'
BUILDS = Path(__file__).resolve().parent / 'builds'

# ---- GRADIENT LAB (Round 11): per-song design.gradient overrides ------------
# Schema (builds/<key>.content.json "design"->"gradient", all keys optional;
# builds/gradient.defaults.json carries the same shape as site-wide defaults):
#   c1/c2/c3/hi: "#rrggbb"   fb: ["#..","#..","#.."]   speed: 1.0 (divisor on
#   the template's --fdur-* bases)   motion: drift|orbit|sway|pulse (drift =
#   NO data-field-motion attribute)   amp: 1.0 (--field-amp)   force_pale: true
#   (recorded by `gradient set --force-pale`; lets verify_palette skip the
#   pale-tone gate for this song).
# Precedence: cover_palette(art) -> gradient.defaults.json ->
#             builds/<key>.design.json -> design.gradient.
# The design.json layer is what Denmoku's New song screen writes when you
# eyedrop a color off the cover before the song exists — content.json is the
# authored file and doesn't get created until author_data, so an early pick
# needs its own home. Anything later written into content.json still wins.
FDUR_BASES = {'drift': 20.0, 'breath': 11.0, 'a': 15.0, 'b': 21.0, 'c': 13.0,
              'drift-sheet': 22.0}
MOTIONS = ('drift', 'orbit', 'sway', 'pulse')
GRAD_COLOR_KEYS = ('c1', 'c2', 'c3', 'hi')
# pale guard (RULE: no whitish tones in the field — white lyric letters sit on
# top; pale blooms kill contrast). HSV V ceilings, mirrored by the CLI verb,
# the Gradient Lab panel JS, and verify_palette.
PALE_V_MAIN = 0.68   # c1 / c2 / c3 / fb
PALE_V_HI = 0.82     # hi (it is a highlight; matches cover_palette's own cap)


def gradient_defaults_path():
    """builds/gradient.defaults.json — read via this fn (not a baked constant)
    so tests can repoint BUILDS."""
    return BUILDS / 'gradient.defaults.json'


def hex_rgb(h):
    """'#rrggbb' (leading # optional) -> (r,g,b). Raises ValueError on junk."""
    h = str(h).strip().lstrip('#')
    if not re.fullmatch(r'[0-9a-fA-F]{6}', h):
        raise ValueError(f'not a #rrggbb hex color: {h!r}')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def rgb_hex(t):
    return '#%02x%02x%02x' % tuple(t)


def pale_error(field, hexstr):
    """None if the color passes the pale guard, else the human error string."""
    v = max(hex_rgb(hexstr)) / 255
    limit = PALE_V_HI if field == 'hi' else PALE_V_MAIN
    if v > limit:
        return (f'{field}={rgb_hex(hex_rgb(hexstr))} is too pale (HSV V {v:.2f} > '
                f'{limit:.2f}) — RULE: no whitish/pale tones in the living field; '
                f'white lyric letters sit on top and pale blooms kill contrast. '
                f'Pick a deeper tone, or pass --force-pale to override.')
    return None


def validate_gradient_block(g, where):
    """Validate one design.gradient dict (schema only, NOT the pale guard —
    force_pale is the CLI's business). Exits loudly on junk: a typo'd override
    must never silently ship a half-themed page."""
    if not isinstance(g, dict):
        sys.exit(f'[gradient] {where}: design.gradient must be an object, got {type(g).__name__}')
    for k, v in g.items():
        if k in GRAD_COLOR_KEYS:
            try:
                hex_rgb(v)
            except ValueError as e:
                sys.exit(f'[gradient] {where}: {k}: {e}')
        elif k == 'fb':
            if not (isinstance(v, list) and len(v) == 3):
                sys.exit(f'[gradient] {where}: fb must be a list of exactly 3 hex colors')
            for hx in v:
                try:
                    hex_rgb(hx)
                except ValueError as e:
                    sys.exit(f'[gradient] {where}: fb: {e}')
        elif k in ('speed', 'amp'):
            try:
                f = float(v)
            except (TypeError, ValueError):
                sys.exit(f'[gradient] {where}: {k} must be a number, got {v!r}')
            if k == 'speed' and f <= 0:
                sys.exit(f'[gradient] {where}: speed must be > 0 (it divides the base durations)')
            if k == 'amp' and f < 0:
                sys.exit(f'[gradient] {where}: amp must be >= 0')
        elif k == 'motion':
            if v not in MOTIONS:
                sys.exit(f'[gradient] {where}: motion must be one of {"|".join(MOTIONS)}, got {v!r}')
        elif k == 'force_pale':
            if not isinstance(v, bool):
                sys.exit(f'[gradient] {where}: force_pale must be true/false')
        else:
            sys.exit(f'[gradient] {where}: unknown key {k!r} '
                     f'(allowed: c1 c2 c3 hi fb speed motion amp force_pale)')


def design_json_path(key):
    """builds/<key>.design.json — the builder's pre-content.json override home
    (written by Denmoku's New song screen). Same gradient block shape."""
    return BUILDS / f'{key}.design.json'


def load_gradient_design(key):
    """The merged override block for one song: gradient.defaults.json overlaid
    by builds/<key>.design.json, overlaid by builds/<key>.content.json
    design.gradient (later wins per key; fb replaces as a whole). {} when
    nothing is overridden — the everyday case, which must stay a byte-identical
    no-op in assemble."""
    merged = {}
    dp = gradient_defaults_path()
    if dp.exists():
        try:
            g = json.loads(dp.read_text())
        except Exception as e:
            sys.exit(f'[gradient] unreadable {dp.name}: {e}')
        g = (g.get('gradient') if isinstance(g, dict) and 'gradient' in g else g) or {}
        validate_gradient_block(g, dp.name)
        merged.update(g)
    bp = design_json_path(key)
    if bp.exists():
        try:
            g = (json.loads(bp.read_text()).get('gradient')) or {}
        except Exception as e:
            sys.exit(f'[gradient] unreadable {bp.name}: {e}')
        validate_gradient_block(g, bp.name)
        merged.update(g)
    cp = BUILDS / f'{key}.content.json'
    if cp.exists():
        try:
            g = (json.loads(cp.read_text()).get('design') or {}).get('gradient') or {}
        except Exception:
            g = {}
        validate_gradient_block(g, f'{key}.content.json')
        merged.update(g)
    return merged


def fmt_num(x):
    """Duration/amp literal: round to 2 decimals, strip trailing zeros
    (20/1.25 -> '16', 21/1.3 -> '16.15')."""
    return ('%.2f' % round(float(x), 2)).rstrip('0').rstrip('.')


def sha8(path):
    """First 8 hex of sha256 of a file's bytes (site-wide convention)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:8]


def tree_sha8(d):
    """sha8 over a sorted (relpath, bytes) walk of dir d — same style as
    audio_version(); fingerprints the whole template dir for the deps manifest."""
    d = Path(d)
    h = hashlib.sha256()
    for p in sorted((x for x in d.rglob('*') if x.is_file()),
                    key=lambda p: str(p.relative_to(d))):
        h.update(str(p.relative_to(d)).encode('utf-8'))
        h.update(p.read_bytes())
    return h.hexdigest()[:8]


def audio_version(folder):
    """Content hash over this song's per-word/drill audio (songs/_assets/<folder>/
    audio/{jp,en,drill}). Per-word clip URLs are built by CONVENTION in the page
    JS (audio/jp/word_<sid>_<uid>.mp3), not from a manifest, so a regenerated clip
    reusing the same filename is otherwise served stale forever (CF marks
    /songs/*.mp3 immutable for a year). The page's template appends `?v=AUDIO_V`
    at every URL-constructor site via _withAudioV(); we compute AUDIO_V here as a
    sha8 over (sorted relpath + raw bytes) of every audio file, so any byte change
    to any clip mints fresh URLs for the whole set. Drill concats carry their own
    per-file ?v= (build_drill_concat.py) and are excluded here to avoid churn.

    Returns (audio_v, clips) where clips maps each covered file's relpath ->
    sha8 of its bytes (the deps manifest's `clips` block). audio_v is folded
    over the SAME walk/bytes the map records, byte-identical to the historical
    single-value computation for unchanged assets."""
    adir = ROOT / 'songs' / '_assets' / folder / 'audio'
    h = hashlib.sha256()
    files = []
    for sub in ('jp', 'en'):
        d = adir / sub
        if d.is_dir():
            files += sorted(p for p in d.rglob('*') if p.is_file())
    # root-level audio (the podcast mp3): PODCAST_URL is a relative const the
    # page also passes through _withAudioV(), so a re-rendered podcast must
    # change AUDIO_V too (same stale-immutable trap as the per-word clips).
    files += sorted(p for p in adir.glob('*') if p.is_file())
    clips = {}
    for p in sorted(files, key=lambda p: str(p.relative_to(adir))):
        rel = str(p.relative_to(adir))
        data = p.read_bytes()
        h.update(rel.encode('utf-8'))
        h.update(data)
        clips[rel] = hashlib.sha256(data).hexdigest()[:8]
    return h.hexdigest()[:8], clips

def splice_object(html, decl, new_literal):
    """Replace `decl{ ... };` (balanced, string-aware) with `decl<new_literal>;`."""
    start = html.index(decl)
    i = start + len(decl)
    assert html[i] == '{', f'{decl} not followed by {{'
    depth, j, in_str, esc = 0, i, None, False
    while j < len(html):
        c = html[j]
        if in_str:
            if esc: esc = False
            elif c == '\\': esc = True
            elif c == in_str: in_str = None
        else:
            if c in '"\'`': in_str = c
            elif c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    break
        j += 1
    end = j + 1
    if end < len(html) and html[end] == ';':
        end += 1
    return html[:start] + decl + new_literal + ';' + html[end:]


def js(s):
    """Single-quoted JS string literal (matches the inochi convention + the
    line_explainers.py / validate_song regex parsers, which key on `'...'`)."""
    s = str(s or '').replace('\\', '\\\\').replace("'", "\\'").replace('\n', ' ').replace('\r', ' ')
    return "'" + s + "'"


def build_line_tr_literal(d):
    lines = ['{']
    for k, v in d.items():
        val = v if isinstance(v, dict) else {'en': v}
        lines.append(f'  {js(k)}: {{en: {js(val.get("en",""))}' +
                     (f', full: {js(val["full"])}' if val.get('full') else '') + '},')
    lines.append('}')
    return '\n'.join(lines)


def build_line_explain_literal(d):
    lines = ['{']
    for k, v in d.items():
        lines.append(f'  {js(k)}: {js(v)},')
    lines.append('}')
    return '\n'.join(lines)


def darken(base_rgb, vt, sat_mul=1.0, sat_cap=0.80):
    """A DARK tone carrying base_rgb's hue at value vt (the livingField base /
    body-gradient recipe). V/S levels were measured from the template's baked
    inochi values; a grey input yields neutral charcoal (sat ~0 stays ~0)."""
    h, s, _v = colorsys.rgb_to_hsv(*[x/255 for x in base_rgb])
    r, g, b = colorsys.hsv_to_rgb(h, min(sat_cap, s * sat_mul), vt)
    return '#%02x%02x%02x' % (round(r*255), round(g*255), round(b*255))


def c1_chain(c1):
    """Everything the dominant c1 drives, in one place: the #livingField base
    radial (base1-3 hex), the body/html gradient (g1-4 hex), and the landing
    Norelco cardAccent — the signature color as a mid-dark band (cream text
    needs contrast: readable brightness, the album's hue/sat kept). Used by the
    cover-derived path AND a gradient-lab c1 override, so both stay one formula.
    Returns (card_hex, (base1,base2,base3), (g1,g2,g3,g4))."""
    base = (darken(c1, 0.165, sat_mul=1.30),
            darken(c1, 0.094, sat_mul=1.30),
            darken(c1, 0.047, sat_mul=1.30))
    body = (darken(c1, 0.231, sat_mul=0.68),
            darken(c1, 0.290, sat_mul=0.68),
            darken(c1, 0.165, sat_mul=0.86),
            darken(c1, 0.086, sat_mul=1.10))
    hs, ss, _vs = colorsys.rgb_to_hsv(*[x/255 for x in c1])
    r, g, b = colorsys.hsv_to_rgb(hs % 1,
                                  max(0, min(1, min(0.92, ss * 1.12 + 0.04) if ss > 0.06 else ss)),
                                  0.45)
    card = '#%02x%02x%02x' % (round(r*255), round(g*255), round(b*255))
    return card, base, body


PALETTE_FALLBACK_WARNING = (
    '[palette] !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
    '[palette] !! PALETTE FALLBACK — NO COVER ART AVAILABLE               !!\n'
    '[palette] !! The page is keeping the TEMPLATE\'s palette (inochi\'s    !!\n'
    '[palette] !! colors). WRONG SONG COLORS WILL SHIP if you deploy this. !!\n'
    '[palette] !! Fix: check the art URL / network, or drop the cover jpg  !!\n'
    '[palette] !! at builds/<key>.art.jpg and re-run assemble.             !!\n'
    '[palette] !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!')


def cover_art_bytes(art_url, cache=None):
    """The album-cover bytes, LOCAL-FIRST: builds/<key>.art.jpg is read before
    any network; a first successful download populates it so every future
    assemble/rebuild is offline for art. Returns None when neither works."""
    cache = Path(cache) if cache else None

    def _disp(p):   # repo-relative when possible, never a crash (test paths)
        try:
            return str(p.relative_to(ROOT))
        except ValueError:
            return str(p)
    if cache and cache.exists():
        try:
            raw = cache.read_bytes()
        except Exception as e:
            raw = None
            print(f'[palette] art cache unreadable ({e}) — falling back to the URL')
        if raw:
            print(f'[palette] art from local cache {_disp(cache)} '
                  f'({len(raw)//1024} KB — no network)')
            return raw
    if not art_url:
        print('[palette] no art url and no cached art bytes')
        return None
    try:
        raw = urllib.request.urlopen(art_url, timeout=20).read()
    except Exception as e:
        print(f'[palette] cover fetch failed ({e})')
        return None
    if cache:
        try:
            cache.write_bytes(raw)
            print(f'[palette] cached art bytes -> {_disp(cache)} '
                  f'({len(raw)//1024} KB; future assembles read it first)')
        except Exception as e:
            print(f'[palette] art cache write failed ({e}) — palette still derived')
    return raw


def cover_palette(art_url, cache=None):
    """Sample the album cover's real dominant colors (median-cut) and map several
    DISTINCT ones to the living-gradient vars, the way Apple Music's lyric
    background does. Faithful to the art: a colorful cover -> a rich multi-hue
    mesh; a dark / black-and-white cover -> a dark, low-saturation gradient (not
    an invented hue). Art bytes come cache-first via cover_art_bytes (gap 1:
    no re-download on every rebuild, and NEVER a silent template-palette ship —
    a miss prints PALETTE_FALLBACK_WARNING). Returns (c1,c2,c3,hi rgb,
    cardAccent hex, fb=(fb1,fb2,fb3) bloom rgb triples, base=(hex,hex,hex)
    base-radial stops, body=(hex*4) body-gradient stops)."""
    from PIL import Image
    import colorsys
    raw = cover_art_bytes(art_url, cache)
    if raw is None:
        print(PALETTE_FALLBACK_WARNING)
        return None
    try:
        im = Image.open(io.BytesIO(raw)).convert('RGB').resize((128, 128))
    except Exception as e:
        print(f'[palette] art bytes undecodable ({e})')
        print(PALETTE_FALLBACK_WARNING)
        return None
    # dominant colors by area via median-cut quantization
    q = im.quantize(colors=16, method=Image.MEDIANCUT)
    pal = q.getpalette()
    counts = q.getcolors() or []
    total = sum(c for c, _ in counts) or 1
    cols = []  # (area_frac, (r,g,b), h, s, v)
    for count, idx in counts:
        r, g, b = pal[idx*3:idx*3+3]
        h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
        cols.append((count/total, (r, g, b), h, s, v))
    if not cols:
        print('[palette] quantizer returned no colors from the art')
        print(PALETTE_FALLBACK_WARNING)
        return None
    cols.sort(key=lambda c: -c[0])
    prom = [c for c in cols if c[0] >= 0.03] or cols[:6]

    def rgb(h, s, v):
        r, g, b = colorsys.hsv_to_rgb(h % 1, max(0, min(1, s)), max(0, min(1, v)))
        return (round(r*255), round(g*255), round(b*255))

    def rev(rgb_t, lo, hi, hue=None, sat=None):
        """clamp a real cover color's VALUE into [lo,hi]; optionally retint its
        hue/sat toward a target (used to pull a discordant grey back in-family)."""
        r, g, b = rgb_t
        h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
        return rgb(hue if hue is not None else h,
                   sat if sat is not None else s,
                   max(lo, min(hi, v)))

    def hue_dist(a, b):
        d = abs(a - b) % 1
        return min(d, 1 - d)

    def hue_delta(a, b):
        """signed shortest hue step a->b (for finding a midpoint hue)."""
        d = (b - a) % 1.0
        return d - 1.0 if d > 0.5 else d

    # signature = the most colorful prominent color (area-weighted saturation).
    # For a B&W cover every s is ~0 so this just returns a gray -> correct.
    sig = max(prom, key=lambda c: c[3] * (0.35 + 0.65 * c[0]) * (0.4 + 0.6 * c[4]))
    sig_h, sig_s = sig[2], sig[3]
    # is this a real-color cover, or a black-and-white / near-grey one? A grey
    # cover (Fujii Kaze's B&W portrait) MUST stay grey — Apple Music renders it
    # charcoal, not an invented hue. Only build a multi-hue mesh when the cover
    # actually has saturated color.
    COLORFUL = sig_s >= 0.18
    # A "hero" color is one whose saturation is in the signature's league — not a
    # desaturated surface. On the Odoriko cover the record label is vivid gold/
    # orange/red (s~0.85) while ~40% of the art is a pale blue-grey photo
    # backdrop (s~0.15); Apple's mesh ignores that backdrop, and so must we (a
    # low floor let the blue-grey become the highlight — the bug the owner flagged).
    sat_floor = max(0.30, sig_s * 0.40) if COLORFUL else 0.12
    hero = sorted([c for c in prom if c[3] >= sat_floor], key=lambda c: -c[3])

    def in_family(c):
        # analogous to the signature (Apple pairs a hero hue with its neighbours
        # for a warm/cool-consistent mesh; no muddy off-axis jumps).
        return hue_dist(c[2], sig_h) <= 0.16

    darkest = min(prom, key=lambda c: c[4])
    lightest = max(prom, key=lambda c: c[4])

    if COLORFUL and hero:
        fam = [c for c in hero if in_family(c)] or hero
        # c2 = the most PROMINENT hero hue distinct from the signature (the second
        # mesh color — amber/orange next to Odoriko's gold).
        c2cands = sorted([c for c in fam if hue_dist(c[2], sig_h) > 0.03],
                         key=lambda c: -c[0])
        c2src = c2cands[0] if c2cands else sig
        # c3 = the darkest hero color (a warm deep base), retinted to the family
        # if the literal darkest prominent color is a near-grey.
        dark_hero = min(fam, key=lambda c: c[4])
        c3src = dark_hero if dark_hero[4] < 0.35 else darkest
        c3_grey = c3src[3] < 0.10
        # hi = the BRIGHTEST hero color, a light warm accent (never the cool
        # backdrop). Lifted above c1 so it actually reads as a highlight.
        hi_src = max(fam, key=lambda c: c[4])
        # R-palette (owner): raised c1/hi V-ceilings — the field read too muddy at
        # 0.60/0.74; 0.68/0.82 lets the hero hue actually glow.
        c1 = rev(sig[1], 0.40, 0.68)
        c2 = rev(c2src[1], 0.26, 0.42)
        c3 = rev(c3src[1], 0.10, 0.20, hue=sig_h, sat=min(0.6, sig_s)) if c3_grey \
            else rev(c3src[1], 0.10, 0.20)
        hi = rev(hi_src[1], 0.56, 0.82)
    else:
        # grey / B&W cover: a faithful charcoal gradient (Apple's treatment).
        c1 = rev(sig[1], 0.40, 0.58)
        c2 = rev(sorted(prom, key=lambda c: abs(c[4] - 0.35))[0][1], 0.26, 0.40)
        c3 = rev(darkest[1], 0.10, 0.20)
        hi = rev(lightest[1], 0.55, 0.70)

    # ---- drifting-bloom HERO accents (--field-fb1/2/3) -----------------------
    # These are the DOMINANT mid-field colour (screen-blended, blur(62px)). They
    # must carry the artwork's vivid hero accents so the field reads like the art
    # (Replica -> yellow/red/orange; inochi -> its dusty-rose family; a grey cover
    # -> neutral silver). Tuned so INOCHI's cover reproduces the template's baked
    # rose blooms (194,134,150 / 176,122,140 / 150,96,118) within ~12/channel.
    def bloom(src_rgb, vt, dsat=0.18):
        h, s, _v = colorsys.rgb_to_hsv(*[x/255 for x in src_rgb])
        return rgb(h, max(0.0, s - dsat), max(0.0, min(0.92, vt)))

    if COLORFUL:
        fb1 = bloom(sig[1], sig[4] * 0.94)
        distinct = sorted([c for c in hero if hue_dist(c[2], sig_h) > 0.06],
                          key=lambda c: -c[3])
        if distinct:
            d = distinct[0]                      # most-saturated distinct-hue hero
            fb2 = bloom(d[1], d[4] * 0.94)
            midh = (sig_h + hue_delta(sig_h, d[2]) * 0.5) % 1.0   # hue between them
            # the BRIGHT accent nearest that midpoint hue (Replica's orange between
            # its yellow + red) — skip dim surface colours that share the hue but
            # would drag the bloom to a muddy brown.
            bright = [c for c in hero if c[4] >= 0.55] or hero
            f3 = min(bright, key=lambda c: hue_dist(c[2], midh))
            fb3 = bloom(f3[1], f3[4] * 0.90)
        else:                                    # single hue family -> deeper sig
            fb2 = bloom(sig[1], sig[4] * 0.85)
            fb3 = bloom(sig[1], sig[4] * 0.73)
    else:
        # grey / B&W cover: neutral silver blooms, descending value (no invented hue)
        def grey(vt):
            n = round(max(0.0, min(1.0, vt)) * 255)
            return (n, n, n)
        fb1, fb2, fb3 = grey(0.62), grey(0.52), grey(0.42)

    # ---- livingField base radial (--field-base1/2/3) + body gradient (--body-g*)
    # + card accent — ALL derived from c1 by the shared c1_chain() so a
    # gradient-lab c1 override re-drives the exact same formulas.
    card, base, body = c1_chain(c1)
    fb = (fb1, fb2, fb3)
    print(f'[palette] {"COLORFUL" if COLORFUL else "GREY"} c1{c1} c2{c2} c3{c3} hi{hi} '
          f'card{card} (sig sat {sig_s:.2f}, {len(prom)} dominant colors)')
    print(f'[palette]   fb{fb} base{base} body{body}')
    return c1, c2, c3, hi, card, fb, base, body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('key'); ap.add_argument('slug'); ap.add_argument('folder'); ap.add_argument('template')
    ap.add_argument('--yt', default=''); ap.add_argument('--title-jp', default='')
    ap.add_argument('--artist', default=''); ap.add_argument('--art', default='')
    a = ap.parse_args()

    src = require_template(a.template, '[assemble]')
    dst = SONGS / a.slug
    dst.mkdir(parents=True, exist_ok=True)
    # copy index.html + timestamp-recorder, preserve data.json + tts_manifest.json
    shutil.copy(src / 'index.html', dst / 'index.html')
    if (src / 'timestamp-recorder').exists() and not (dst / 'timestamp-recorder').exists():
        shutil.copytree(src / 'timestamp-recorder', dst / 'timestamp-recorder')

    html = (dst / 'index.html').read_text()

    # splice line maps
    lm = json.loads((BUILDS / f'{a.key}.line_maps.json').read_text())
    html = splice_object(html, 'const LINE_TR = ', build_line_tr_literal(lm['LINE_TR']))
    html = splice_object(html, 'const LINE_EXPLAIN = ', build_line_explain_literal(lm['LINE_EXPLAIN']))

    # retargets
    html = re.sub(r"const SONG = '[^']+';", f"const SONG = '{a.slug}';", html)
    # per-word audio cache-bust: splice the content hash into the template's
    # `const AUDIO_V = '';` sentinel. The template's _withAudioV() appends
    # `?v=<AUDIO_V>` at each per-word URL-constructor site. No-op (count=1, no
    # match) on templates that don't yet carry the sentinel.
    av, clips = audio_version(a.folder)
    html, n_av = re.subn(r"const AUDIO_V = '[^']*';", f"const AUDIO_V = '{av}';", html, count=1)
    if not n_av:
        print(f"[audio_v] note: template has no `const AUDIO_V = '';` sentinel yet "
              f"(computed {av}); per-word cache-bust will apply once it lands.")
    if a.yt:
        html = re.sub(r"const YT_ID = '[^']+';", f"const YT_ID = '{a.yt}';", html)
    html = re.sub(r'(<meta property="og:url" content="https://manaoke\.app/songs/)[^"]+(/">)',
                  rf'\g<1>{a.slug}\g<2>', html)
    html = re.sub(r'(<link rel="canonical" href="https://manaoke\.app/songs/)[^"]+(/">)',
                  rf'\g<1>{a.slug}\g<2>', html)
    if a.title_jp and a.artist:
        title = f'{a.title_jp} by {a.artist} – Manaoke'
        html = re.sub(r'<title>[^<]*</title>', f'<title>{title}</title>', html)
        html = re.sub(r'(<meta property="og:title" content=")[^"]*(">)', rf'\g<1>{title}\g<2>', html)
        html = re.sub(r'(<meta property="twitter:title" content=")[^"]*(">)', rf'\g<1>{title}\g<2>', html)
    chip = a.slug.split('-')[-1]
    html = re.sub(r'(<div class="u-version"[^>]*>)[^<]*(</div>)', rf'\g<1>{chip}\g<2>', html)

    # palette from cover, then GRADIENT LAB overrides on top
    # (precedence: cover_palette -> gradient.defaults.json -> design.gradient).
    grad = load_gradient_design(a.key)
    accent = None
    # local-first: builds/<key>.art.jpg is read before the network, and a cached
    # cover can drive the palette even when --art was omitted this run.
    art_cache = BUILDS / f'{a.key}.art.jpg'
    pal = cover_palette(a.art, art_cache) if (a.art or art_cache.exists()) else None
    if pal is None and not (a.art or art_cache.exists()):
        print('[palette] no --art url and no builds cache')
        print(PALETTE_FALLBACK_WARNING)
    colors = {}
    if pal:
        c1, c2, c3, hi, accent, fb, base, body = pal
        colors = dict(c1=c1, c2=c2, c3=c3, hi=hi,
                      fb1=fb[0], fb2=fb[1], fb3=fb[2], base=base, body=body)
    if grad:
        print(f'[gradient] override active: '
              f'{", ".join(f"{k}={grad[k]}" for k in sorted(grad))}')
        for k in GRAD_COLOR_KEYS:
            if k in grad:
                colors[k] = hex_rgb(grad[k])
        if 'fb' in grad:
            for i, hx in enumerate(grad['fb'], 1):
                colors[f'fb{i}'] = hex_rgb(hx)
        if 'c1' in grad:
            # an overridden c1 re-drives the WHOLE c1 chain, exactly like the
            # cover-derived path: base radial, body gradient, cardAccent.
            accent, colors['base'], colors['body'] = c1_chain(colors['c1'])
    if colors:
        # Swap the palette-derived var DEFINITIONS in place (the template bakes
        # inochi's values as the defaults; every usage across the page reads the
        # var, so one definition swap retargets the whole living field). Same
        # mechanism as --field-c1..hi — no more drift-prone literal hex lists.
        def _rgb_var(name, t):  # rgb-triple var (--field-c*/fb*)
            return re.sub(rf'{name}:\d+,\d+,\d+', f'{name}:{t[0]},{t[1]},{t[2]}', html)
        for nm, ck in [('--field-c1', 'c1'), ('--field-c2', 'c2'), ('--field-c3', 'c3'),
                       ('--field-hi', 'hi'), ('--field-fb1', 'fb1'),
                       ('--field-fb2', 'fb2'), ('--field-fb3', 'fb3')]:
            if ck in colors:
                html = _rgb_var(nm, colors[ck])
        # hex vars: field base-radial stops + body/html gradient stops
        hex_targets = []
        if 'base' in colors:
            b = colors['base']
            hex_targets += [('--field-base1', b[0]), ('--field-base2', b[1]),
                            ('--field-base3', b[2])]
        if 'body' in colors:
            g = colors['body']
            hex_targets += [('--body-g1', g[0]), ('--body-g2', g[1]),
                            ('--body-g3', g[2]), ('--body-g4', g[3])]
        for nm, hx in hex_targets:
            html = re.sub(rf'{nm}:#[0-9a-fA-F]{{6}}', f'{nm}:{hx}', html)

    # GRADIENT LAB dials: speed (divisor on the --fdur-* bases), amp, motion.
    # Only touched when an override block exists — the no-override path stays a
    # byte-identical clone of today's output.
    if grad:
        speed = float(grad.get('speed', 1.0))
        amp = float(grad.get('amp', 1.0))
        motion = grad.get('motion', 'drift')
        if speed != 1.0 or amp != 1.0 or motion != 'drift':
            missing = [n for n in FDUR_BASES if f'--fdur-{n}:' not in html]
            if missing or '--field-amp:' not in html:
                sys.exit(f'[gradient] template {a.template} is too old for gradient '
                         f'dials (no --fdur-*/--field-amp vars: missing '
                         f'{missing or ["--field-amp"]}) — rebuild from a Round-11 '
                         f'template (songs/inochi-mijikashi-e03jz0 or later). '
                         f'Color-only overrides still work on old templates; drop '
                         f'speed/motion/amp via: manaoke_build.py gradient clear '
                         f'{a.key} --motion')
        if speed != 1.0:
            for name, base_s in FDUR_BASES.items():
                html = re.sub(rf'--fdur-{re.escape(name)}:[\d.]+s',
                              f'--fdur-{name}:{fmt_num(base_s / speed)}s', html)
            print(f'[gradient] speed x{fmt_num(speed)} — fdur '
                  + ' '.join(f'{n}:{fmt_num(b / speed)}s' for n, b in FDUR_BASES.items()))
        if amp != 1.0:
            html = re.sub(r'--field-amp:[\d.]+', f'--field-amp:{fmt_num(amp)}', html)
            print(f'[gradient] --field-amp:{fmt_num(amp)}')
        # motion preset rides an attribute on the <html> element
        # (:root[data-field-motion="..."]); drift = attribute ABSENT.
        m = re.search(r'<html\b[^>]*>', html)
        tag = m.group(0)
        new_tag = re.sub(r'\s*data-field-motion="[a-z]*"', '', tag)
        if motion != 'drift':
            new_tag = new_tag[:-1] + f' data-field-motion="{motion}">'
            print(f'[gradient] data-field-motion="{motion}" on <html>')
        if new_tag != tag:
            html = html[:m.start()] + new_tag + html[m.end():]

    # retarget the TEMPLATE's identity everywhere it is hardcoded (topbar
    # .u-title/.u-artist DOM, meta description/og, code comments) + the kit /
    # podcast asset paths. Generic: read the template's own data.json for the
    # strings to replace, and this song's content.json for the replacements.
    try:
        tmpl_data = json.loads((src / 'data.json').read_text())
        content = json.loads((BUILDS / f'{a.key}.content.json').read_text())
        ident = [(f, tmpl_data.get(f, ''), content.get(f, ''))
                 for f in ('title_jp', 'title_en', 'artist', 'artist_en')]
        for field, old, new in ident:
            if old and new:
                html = html.replace(old, new)
            elif old and old in html:
                # An empty field is not "nothing to substitute" — it means this
                # clone KEEPS the template's identity. That shipped a page
                # crediting the template's band (mariigoorudo, 2026-07-29:
                # artist_en blank in the New Song box → "· CreepHyp" left in the
                # markup and a code comment). Invisible on screen, but the
                # parity gate then fails with a diff that never names the cause,
                # so say the cause out loud here.
                print(f'[identity] ⚠ {field} is EMPTY for {a.key}, so the '
                      f'template\'s {field} ({old!r}) STAYS in the clone '
                      f'{html.count(old)}x — parity will read that as drift. '
                      f'Set {field} in builds/{a.key}.content.json (and the '
                      f'song\'s meta) and re-assemble.')
        tslug = tmpl_data.get('slug', 'inochi-mijikashi')      # conceptual slug
        # point the podcast at a repo-hosted file served by the audio/* rewrite
        # (self-contained, no R2 dependency); the file lives at
        # _assets/<folder>/audio/<key>_podcast.mp3. Podcast is EXPERIMENTAL
        # (mission 2026-07-12): data.json podcast_file '' means this song
        # ships with NO podcast — bake an EMPTY URL (fails soft at the
        # player, E20 warns) instead of a URL that 404s into HTML (the
        # dead-inochi class). Graceful Immerse UI = backlog 2601104f.
        pod_url = (f'audio/{a.key}_podcast.mp3'
                   if content.get('podcast_script') else '')
        html = re.sub(r"const PODCAST_URL = '[^']*';",
                      f"const PODCAST_URL = '{pod_url}';", html)
        # per-song subset fonts (SONG-CONTRACT §4.9): when fonts/<folder>/ holds
        # all five subsets, repoint the @font-face srcs (+ fresh sha8 ?v=) from
        # the template's /fonts/{tslug}/ to this song's own. Without the dir the
        # template fonts stay (per-glyph system fallback) exactly as before.
        font_dir = ROOT / 'fonts' / a.folder
        font_names = ['MPLUSRounded1c-400.subset.woff2', 'MPLUSRounded1c-500.subset.woff2',
                      'MPLUSRounded1c-700.subset.woff2', 'MPLUSRounded1c-800.subset.woff2',
                      'DotGothic16.song.subset.woff2']
        # AUTO-GENERATE the per-song subsets when missing or glyph-stale
        # (backlog c972ecc9): gen_fonts.py recreates the §4.9 recipe from the
        # vendored source TTFs (data/fonts_src/, offline). It runs under the
        # PATH python3 — fontTools+brotli live there, not in this (parler)
        # interpreter. Charset = the in-flight page html + the song's data.json
        # (exactly what the ad-hoc recipe swept). Failure is NON-FATAL: the
        # template fonts stay wired (per-glyph fallback), same as before.
        try:
            import subprocess, tempfile
            data_txt = ''
            dj = dst / 'data.json'
            if dj.exists():
                data_txt = dj.read_text()
            with tempfile.NamedTemporaryFile('w', suffix='.charset.txt', delete=False,
                                             encoding='utf-8') as tf:
                tf.write(html + '\n' + data_txt)
                cs_file = tf.name
            gf = subprocess.run(['python3', str(Path(__file__).resolve().parent / 'gen_fonts.py'),
                                 a.folder, '--charset-file', cs_file],
                                capture_output=True, text=True, timeout=300)
            Path(cs_file).unlink(missing_ok=True)
            tail = (gf.stdout or gf.stderr or '').strip().splitlines()
            if tail:
                print('\n'.join(tail[-6:]))
            if gf.returncode != 0:
                print(f'[fonts] gen_fonts.py failed (rc {gf.returncode}) — see above; '
                      f'keeping whatever fonts/{a.folder}/ holds.')
            # the landing DotGothic gotcha (BUILDER Round 8): a new TITLE kanji
            # missing from the LANDING's own subset renders fallback on the
            # landing. Report only — the public landing is promote territory.
            title_jp = content.get('title_jp', '')
            if title_jp:
                lc = subprocess.run(['python3',
                                     str(Path(__file__).resolve().parent / 'gen_fonts.py'),
                                     '--check-landing', title_jp],
                                    capture_output=True, text=True, timeout=120)
                if lc.returncode == 2:
                    print((lc.stdout or '').strip())
        except Exception as e:
            print(f'[fonts] auto-generation skipped ({type(e).__name__}: {e}) — '
                  f'keeping whatever fonts/{a.folder}/ holds.')
        if all((font_dir / n).exists() for n in font_names):
            import hashlib as _hl
            for n in font_names:
                sha8 = _hl.sha256((font_dir / n).read_bytes()).hexdigest()[:8]
                html = re.sub(rf"/fonts/{re.escape(tslug)}/{re.escape(n)}\?v=[0-9a-f]+",
                              f"/fonts/{a.folder}/{n}?v={sha8}", html)
            print(f'[fonts] @font-face -> /fonts/{a.folder}/ (5 subsets, §4.9)')
        else:
            print(f'[fonts] fonts/{a.folder}/ incomplete or absent — keeping template subset (fallback renders)')
    except Exception as e:
        print(f'[identity] retarget skipped: {e}')

    # NO structural patches. The built page must stay byte-identical to the
    # template except for data + palette + asset paths — the template (inochi /
    # "creep hype") is the reference that nails study-card scroll/anchor and the
    # translation-toggle flow natively. A per-song patch fought that native
    # behavior (a position:fixed translation pill overrode inochi's inline
    # scrolling chip → "toggling translations throws off the flow"). If a study
    # behavior genuinely needs improving, fix it in the TEMPLATE so every song
    # (including inochi) inherits it and parity holds. parity_audit.py enforces
    # that the only diffs vs the template are data/palette/paths.

    (dst / 'index.html').write_text(html)

    # _redirects: add per-song audio rewrite ABOVE the first generic /songs/inochi fallthrough
    red = ROOT / '_redirects'
    lines = red.read_text().splitlines()
    tag = f'/songs/{a.slug}/audio/*'
    if not any(tag in ln for ln in lines):
        new = [f'/songs/{a.slug}/audio/*       /songs/_assets/{a.folder}/audio/:splat       200',
               f'/songs/{a.slug}/pitch_data/*  /songs/_assets/{a.folder}/pitch_data/:splat  200',
               f'/songs/{a.slug}/images/*      /songs/_assets/{a.folder}/images/:splat      200']
        # insert after the first two utility lines (keep them on top)
        insert_at = 2
        lines[insert_at:insert_at] = new
        red.write_text('\n'.join(lines) + '\n')
        print(f'_redirects: added 3 rewrites for {a.slug} -> _assets/{a.folder}')
    else:
        print(f'_redirects: {a.slug} already present')

    # deps manifest: record the content-hashes of everything this page was built
    # from, so a rebuild command can compute exactly what's stale. One manifest
    # per song at builds/<folder>.deps.json (asset folder, NOT the deploy slug).
    # assemble owns template/recipe.assemble_page/page/clips; the drill block
    # (per-line concat inputs) is owned by build_drill_concat.py — preserve it.
    write_deps_manifest(a, html, av, clips, src)

    print(f'assembled songs/{a.slug} from {a.template}')
    if pal is None and 'c1' not in colors:
        # repeat the loud fallback at the very END so the runner's tail-captured
        # note can't scroll it away — this must never ship silently (gap 1).
        print(PALETTE_FALLBACK_WARNING)
    if accent:
        print(f'CARD_ACCENT={accent}  (set landing SONGS[].cardAccent to this)')
        (BUILDS / f'{a.key}.cardaccent.txt').write_text(accent)


def write_deps_manifest(a, html, av, clips, src):
    """Emit/merge builds/<folder>.deps.json (schema 1). Merge, not overwrite:
    build_drill_concat.py owns `drill` + `recipe.build_drill_concat`."""
    from datetime import datetime, timezone
    dep_path = BUILDS / f'{a.folder}.deps.json'
    man = {}
    if dep_path.exists():
        try:
            man = json.loads(dep_path.read_text())
        except Exception as e:
            print(f'[deps] existing {dep_path.name} unreadable ({e}); rewriting')
    inputs = {}
    for p in [BUILDS / f'{a.key}.content.json', BUILDS / f'{a.key}.line_maps.json',
              SONGS / a.slug / 'data.json', SONGS / a.slug / 'tts_manifest.json',
              # site-wide gradient defaults (GRADIENT LAB): recorded when present
              # so a global edit stales every song via `rebuild --why`/`--all`.
              gradient_defaults_path(),
              *sorted((ROOT / 'fonts' / a.folder).glob('*.woff2'))]:
        if p.exists():
            inputs[str(p.relative_to(ROOT))] = sha8(p)
    man.update({
        'schema': 1, 'folder': a.folder, 'key': a.key, 'deploy_slug': a.slug,
        'built_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'template': {'dir': a.template, 'tree_sha8': tree_sha8(src),
                     'index_sha8': sha8(src / 'index.html')},
        'page': {'out': f'songs/{a.slug}/index.html',
                 'out_sha8': hashlib.sha256(html.encode('utf-8')).hexdigest()[:8],
                 'audio_v': av, 'inputs': inputs},
        'clips': clips,
    })
    man.setdefault('recipe', {})['assemble_page'] = {'tool_sha8': sha8(__file__)}
    man.setdefault('drill', {'lines': {}})
    dep_path.write_text(json.dumps(man, ensure_ascii=False, indent=1) + '\n')
    print(f'[deps] wrote {dep_path.relative_to(ROOT)} '
          f'({len(inputs)} page inputs, {len(clips)} clips)')


if __name__ == '__main__':
    main()
