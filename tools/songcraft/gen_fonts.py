#!/usr/bin/env python3
"""
gen_fonts.py — per-song subset fonts (SONG-CONTRACT §4.9), owned in code.

The song page self-hosts 5 woff2 subsets at /fonts/<folder>/
(MPLUSRounded1c-{400,500,700,800}.subset.woff2 + DotGothic16.song.subset.woff2),
GLYPH-SPECIFIC to that song's text. Cloning the template inherits inochi's
subset — a new song's kanji tofu silently unless the subsets are regenerated.
This tool recreates the (previously ad-hoc) §4.9 recipe:

  charset = every char in the built page (index.html + data.json)
  pyftsubset <src>.ttf --text-file=charset --unicodes=<full-kana insurance>
             --layout-features='*' --flavor=woff2

Source TTFs are VENDORED at tools/songcraft/data/fonts_src/ (OFL, see
NOTICE.txt) so generation is fully offline. Needs fontTools + brotli, which do
not live in the parler env (assemble shells out) and are not in every python3 on
the PATH either — so this script re-execs itself into an interpreter that has
them rather than trusting whichever python3 its caller happened to resolve. See
_reexec_where_fonttools_lives().

Usage:
  gen_fonts.py <folder> [--check] [--force] [--charset-file F | --song-dir D]
      default: ensure fonts/<folder>/ exists and covers the song's glyphs;
               generate only what's missing/stale. --check reports only
               (exit 0 complete / 2 regeneration needed); --force regenerates.
      charset default: songs/<slug>/index.html + data.json (slug from
               builds/*.build_state.json whose asset folder == <folder>).
  gen_fonts.py --check-landing "<title text>" [--fix-landing]
      the landing DotGothic gotcha (BUILDER Round 8: "+死踊子"): the root
      landing's fonts/DotGothic16.subset.woff2 is also glyph-subset — a new
      song TITLE with a new kanji renders as fallback on the landing.
      --check-landing reports missing title glyphs (exit 2 when any);
      --fix-landing regenerates the landing subset (union of its current
      coverage + the new chars) and bumps the ?v= hashes in root index.html.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path


def _reexec_where_fonttools_lives():
    """Re-exec into an interpreter that can actually import fontTools+brotli.

    "Runs on SYSTEM python3 — fontTools + brotli live there" was an assumption
    about the PATH, and the PATH depends on who launched the caller. A terminal
    resolves python3 to miniforge (has fontTools); Denmoku.app, launched from
    Finder, resolves it to homebrew python (does NOT). So the same assemble step
    subset fonts correctly by hand and silently skipped it for every song built
    from the box — and skipping is not cosmetic: a cloned page keeps inochi's
    subsets, so any kanji the new song introduces renders as tofu. Found on
    mariigoorudo, 2026-07-29, as a ModuleNotFoundError buried in a step that
    treats font failure as non-fatal.

    Probing beats hardcoding: the interpreter that has the module today is not
    guaranteed to be the one that has it next year."""
    try:
        import brotli  # noqa: F401
        from fontTools import subset  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get('MANAOKE_FONTS_REEXEC'):
        sys.exit('[fonts] no interpreter on this machine can import both '
                 'fontTools and brotli.\n'
                 '  Install them where they will be found:\n'
                 '    /opt/homebrew/Caskroom/miniforge/base/bin/python3 -m pip '
                 'install fonttools brotli')
    seen, cands = set(), [
        shutil.which('python3'),
        '/opt/homebrew/Caskroom/miniforge/base/bin/python3',
        '/opt/homebrew/bin/python3',
        '/usr/bin/python3',
    ]
    for cand in cands:
        if not cand or cand in seen or not Path(cand).exists():
            continue
        seen.add(cand)
        if Path(cand).resolve() == Path(sys.executable).resolve():
            continue
        probe = subprocess.run([cand, '-c', 'import brotli, fontTools.subset'],
                               capture_output=True)
        if probe.returncode == 0:
            os.environ['MANAOKE_FONTS_REEXEC'] = '1'
            print(f'[fonts] this python has no fontTools — re-running under {cand}',
                  file=sys.stderr)
            os.execv(cand, [cand, str(Path(__file__).resolve())] + sys.argv[1:])
    os.environ['MANAOKE_FONTS_REEXEC'] = '1'   # fall through to the message above
    _reexec_where_fonttools_lives()


_reexec_where_fonttools_lives()

HERE = Path(__file__).resolve().parent            # tools/songcraft
ROOT = HERE.parents[1]                            # ~/manaoke-site
BUILDS = HERE / 'builds'
SRC = HERE / 'data' / 'fonts_src'
FONTS = ROOT / 'fonts'

# the five per-song subsets and the source TTF each is cut from (§4.9)
SONG_SUBSETS = {
    'MPLUSRounded1c-400.subset.woff2': 'MPLUSRounded1c-Regular.ttf',
    'MPLUSRounded1c-500.subset.woff2': 'MPLUSRounded1c-Medium.ttf',
    'MPLUSRounded1c-700.subset.woff2': 'MPLUSRounded1c-Bold.ttf',
    'MPLUSRounded1c-800.subset.woff2': 'MPLUSRounded1c-ExtraBold.ttf',
    'DotGothic16.song.subset.woff2':   'DotGothic16-Regular.ttf',
}
# full-kana + punctuation insurance ranges, verbatim from the §4.9 recipe
SONG_UNICODES = ('U+0020-00FF,U+3000-30FF,U+2026,U+FF01-FF60,'
                 'U+2018-201F,U+2022,U+2190-2193')
# the LANDING DotGothic subset's insurance ranges (fonts/README.md recipe)
LANDING_SUBSET = 'DotGothic16.subset.woff2'
LANDING_UNICODES = 'U+0020-007E,U+3000-303F,U+3040-30FF,U+2026,U+30FC,U+FF01-FF60'


def sha8(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:8]


def cmap_chars(font_path):
    """Set of characters a font file covers (woff2 needs brotli — system py3)."""
    from fontTools.ttLib import TTFont
    return {chr(cp) for cp in TTFont(str(font_path)).getBestCmap()}


def charset_from_texts(texts):
    """The §4.9 charset: every char >= U+0020 across the given texts."""
    c = set()
    for t in texts:
        c |= set(t)
    return {x for x in c if ord(x) >= 0x20}


def song_texts(folder, song_dir=None):
    """Default charset inputs: the built page's index.html + data.json."""
    d = Path(song_dir) if song_dir else None
    if d is None:
        for p in sorted(BUILDS.glob('*.build_state.json')):
            st = json.loads(p.read_text())
            if (st.get('meta') or {}).get('slug', st.get('key')) == folder:
                d = ROOT / 'songs' / st['slug']
                break
    if d is None:
        sys.exit(f'[fonts] no build state maps asset folder {folder!r} to a song dir — '
                 f'pass --song-dir songs/<slug> or --charset-file.')
    texts = []
    for n in ('index.html', 'data.json'):
        f = d / n
        if not f.exists():
            sys.exit(f'[fonts] {f.relative_to(ROOT)} missing — assemble the page first '
                     f'(charset comes from the built page).')
        texts.append(f.read_text(encoding='utf-8'))
    return texts


def missing_glyphs(subset_path, needed, src_cmap):
    """Chars the song needs that the subset lacks BUT the source font has —
    a char the source itself can't draw is not this subset's failure."""
    have = cmap_chars(subset_path)
    return sorted(c for c in needed if c not in have and c in src_cmap)


def subset_font(src_ttf, out_woff2, text, unicodes):
    """One §4.9 pyftsubset invocation, in-process (fontTools.subset.main)."""
    from fontTools import subset
    with tempfile.NamedTemporaryFile('w', suffix='.charset.txt', delete=False,
                                     encoding='utf-8') as tf:
        tf.write(''.join(sorted(text)))
        charset_file = tf.name
    out_woff2.parent.mkdir(parents=True, exist_ok=True)
    subset.main([str(src_ttf),
                 f'--text-file={charset_file}',
                 f'--unicodes={unicodes}',
                 '--layout-features=*',
                 '--flavor=woff2',
                 f'--output-file={out_woff2}'])
    Path(charset_file).unlink(missing_ok=True)


def check_sources():
    miss = [t for t in set(SONG_SUBSETS.values()) if not (SRC / t).exists()]
    if miss:
        sys.exit(f'[fonts] source TTFs missing from {SRC.relative_to(ROOT)}: '
                 f'{", ".join(sorted(miss))} — re-vendor from github.com/google/fonts '
                 f'(ofl/mplusrounded1c + ofl/dotgothic16).')


def ensure_song(folder, texts, check_only=False, force=False):
    """Ensure fonts/<folder>/ holds all five subsets covering the song's text.
    Returns (changed, report_lines); exits 2 in --check mode when work is needed."""
    check_sources()
    needed = charset_from_texts(texts)
    fdir = FONTS / folder
    work, report = [], []
    for out_name, src_name in SONG_SUBSETS.items():
        out, src = fdir / out_name, SRC / src_name
        if force or not out.exists():
            work.append((out, src, 'missing' if not out.exists() else 'forced'))
            continue
        gaps = missing_glyphs(out, needed, cmap_chars(src))
        if gaps:
            work.append((out, src, f'missing glyphs: {"".join(gaps[:20])}'
                                   + (f' (+{len(gaps)-20} more)' if len(gaps) > 20 else '')))
        else:
            report.append(f'[fonts] {out_name}: complete ({len(needed)} chars covered)')
    if not work:
        report.append(f'[fonts] fonts/{folder}/ complete — all 5 subsets cover the '
                      f'song text (no-op).')
        return False, report
    if check_only:
        for out, _src, why in work:
            report.append(f'[fonts] STALE {out.relative_to(ROOT)}: {why}')
        report.append(f'[fonts] regenerate: python3 tools/songcraft/gen_fonts.py {folder}')
        return True, report
    for out, src, why in work:
        subset_font(src, out, needed, SONG_UNICODES)
        report.append(f'[fonts] wrote {out.relative_to(ROOT)} '
                      f'({out.stat().st_size//1024} KB, was {why}) sha8={sha8(out)}')
    return True, report


def check_landing(title_text, fix=False):
    """The landing DotGothic gotcha: is every char of this title in the
    landing's own DotGothic16 subset? Returns (missing_chars, report_lines)."""
    check_sources()
    sub = FONTS / LANDING_SUBSET
    src = SRC / 'DotGothic16-Regular.ttf'
    src_cmap = cmap_chars(src)
    needed = {c for c in title_text if ord(c) >= 0x20}
    if not sub.exists():
        return sorted(needed), [f'[fonts] landing subset {sub.relative_to(ROOT)} MISSING']
    gaps = missing_glyphs(sub, needed, src_cmap)
    if not gaps:
        return [], [f'[fonts] landing DotGothic16 subset covers {title_text!r} — ok.']
    report = [f'[fonts] LANDING GOTCHA: fonts/{LANDING_SUBSET} lacks: {"".join(gaps)} — '
              f'the landing would render this title in the system fallback, not the '
              f'dotted face.']
    if not fix:
        report.append(f'[fonts] fix: python3 tools/songcraft/gen_fonts.py '
                      f'--check-landing "{title_text}" --fix-landing')
        return gaps, report
    # regenerate = union of the current subset's coverage + the new chars, so a
    # fix for one title never drops another title's kanji.
    keep = cmap_chars(sub)
    old_sha = sha8(sub)
    subset_font(src, sub, keep | needed, LANDING_UNICODES)
    new_sha = sha8(sub)
    report.append(f'[fonts] rewrote {sub.relative_to(ROOT)} '
                  f'(+{len(gaps)} glyph(s): {"".join(gaps)}) sha8 {old_sha}->{new_sha}')
    # bump the immutable-cache ?v= hashes in the root landing (fonts/README.md:
    # the files are served immutable for a year, so the URL must change).
    landing = ROOT / 'index.html'
    if landing.exists():
        html = landing.read_text()
        new_html, n = re.subn(rf'(/fonts/{re.escape(LANDING_SUBSET)}\?v=)[0-9a-f]+',
                              rf'\g<1>{new_sha}', html)
        if n:
            landing.write_text(new_html)
            report.append(f'[fonts] bumped {n} ?v= ref(s) in root index.html -> {new_sha}')
        else:
            report.append(f'[fonts] ⚠ no ?v= ref for {LANDING_SUBSET} found in root '
                          f'index.html — bump it by hand (immutable cache).')
    return gaps, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('folder', nargs='?', help='asset folder, e.g. ema (fonts/<folder>/)')
    ap.add_argument('--check', action='store_true', help='report only; exit 2 if stale')
    ap.add_argument('--force', action='store_true', help='regenerate even if complete')
    ap.add_argument('--song-dir', help='built page dir (default: from build state)')
    ap.add_argument('--charset-file', help='read the charset from this file instead '
                                           'of the built page (assemble passes the '
                                           'in-flight html this way)')
    ap.add_argument('--check-landing', metavar='TITLE',
                    help='check the LANDING DotGothic subset covers this title text')
    ap.add_argument('--fix-landing', action='store_true',
                    help='with --check-landing: regenerate the landing subset + bump ?v=')
    ap.add_argument('--which', action='store_true',
                    help='print the interpreter that will do the subsetting and '
                         'exit (0 = subsetting works from here, 2 = nothing on '
                         'this machine can)')
    a = ap.parse_args()

    if a.which:
        # By the time argv is parsed the re-exec has already happened, so
        # sys.executable IS the answer. Lets `doctor` check the real path
        # instead of asking whether ITS OWN python3 has fontTools — the old
        # check passed from a terminal while the app silently skipped fonts.
        import fontTools
        print(f'[fonts] subsetting runs under {sys.executable} '
              f'(fontTools {fontTools.version})')
        sys.exit(0)

    if a.check_landing is not None:
        gaps, report = check_landing(a.check_landing, fix=a.fix_landing)
        print('\n'.join(report))
        sys.exit(0 if (not gaps or a.fix_landing) else 2)

    if not a.folder:
        ap.error('need <folder> (or --check-landing "<title>")')
    if a.charset_file:
        texts = [Path(a.charset_file).read_text(encoding='utf-8')]
    else:
        texts = song_texts(a.folder, a.song_dir)
    changed, report = ensure_song(a.folder, texts, check_only=a.check, force=a.force)
    print('\n'.join(report))
    sys.exit(2 if (a.check and changed) else 0)


if __name__ == '__main__':
    main()
