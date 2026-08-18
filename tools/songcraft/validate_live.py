#!/usr/bin/env python3
"""validate_live — the standing live-set gate (backlog f8ae38e6).

Reads root index.html SONGS[] (the only truth for what's live) and, for
every live song dir, RE-DERIVES the facts that rot underneath a promoted
page instead of trusting any recorded checkmark:

  AUDIO_V  page cache key == fresh sha-walk of songs/_assets/<folder>/audio
  DRILL    every DRILL_MAP ?v= == sha256[:8] of the concat's current bytes
  PODCAST  a relative PODCAST_URL resolves to a non-empty file in _assets
  PARITY   the page is still a byte-structural clone of its build template

This is the check that would have caught the 2026-07-07 stale-audio wave
the day it happened (commit a2021e0 rewrote shared _assets bytes under
all six live pages; validate_song E18 knew, but nothing ran it against
the LIVE set). Wired into `manaoke_build.py doctor` and the promote
preflight; run standalone any time:

    python3 tools/songcraft/validate_live.py [--json]

Exit 0 = every live page serves exactly what it validated as. 1 = findings.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SONGS = ROOT / 'songs'
BUILDS = HERE / 'builds'
LANDING = ROOT / 'index.html'


def live_slugs():
    """The dirs root SONGS[] links — read fresh every call, never cached."""
    html = LANDING.read_text()
    return re.findall(r"url:\s*'/songs/([a-z0-9-]+)/'", html)


def _asset_folder(slug):
    """functions/songs/[dir] derives the _assets folder as dir-minus-last-
    segment; use the exact same rule so we check what production serves."""
    return slug.rsplit('-', 1)[0]


def _template_for(folder):
    """Resolve the build template for a song folder from its build state."""
    for p in BUILDS.glob('*.build_state.json'):
        try:
            st = json.loads(p.read_text())
        except Exception:
            continue
        if ((st.get('meta') or {}).get('slug') or st.get('key')) == folder:
            return st.get('template'), st.get('key')
    return None, None


def check_dir(slug, parity=True):
    """Fast integrity findings for songs/<slug>. Returns a list of strings —
    empty means the dir is servable exactly as built. Fail-closed: anything
    this check cannot verify is itself a finding."""
    finds = []
    d = SONGS / slug
    page = d / 'index.html'
    if not page.exists():
        return [f'songs/{slug}/index.html missing']
    html = page.read_text()
    folder = _asset_folder(slug)
    adir = SONGS / '_assets' / folder / 'audio'
    if not adir.is_dir():
        return [f'songs/_assets/{folder}/audio missing — the functions layer '
                f'has nothing to serve']

    # AUDIO_V: page key vs fresh walk of the shared asset set
    m = re.search(r"const AUDIO_V\s*=\s*'([0-9a-f]*)'", html)
    if not m:
        finds.append('no const AUDIO_V on the page')
    else:
        try:
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import assemble_page
            fresh = assemble_page.audio_version(folder)
            fresh = fresh[0] if isinstance(fresh, tuple) else fresh
            if m.group(1) != fresh:
                finds.append(f'AUDIO_V stale: page {m.group(1) or "(empty)"} != '
                             f'fresh walk {fresh} — bytes changed under the page')
        except Exception as e:
            finds.append(f'AUDIO_V unverifiable ({type(e).__name__}: {e})')

    # DRILL_MAP: every referenced concat's ?v= vs its current bytes
    for dm in re.finditer(r"audio/drill/(line_[0-9a-f]{8}\.mp3)(?:\?v=([0-9a-f]{8}))?",
                          html):
        fname, ver = dm.group(1), dm.group(2)
        fp = adir / 'drill' / fname
        if not fp.exists():
            finds.append(f'drill {fname} referenced but missing from _assets')
        elif ver is None:
            finds.append(f'drill {fname} has no ?v= — can never cache-bust')
        else:
            got = hashlib.sha256(fp.read_bytes()).hexdigest()[:8]
            if ver != got:
                finds.append(f'drill {fname} ?v={ver} != current bytes {got}')

    # PODCAST: a relative URL must resolve (the dead-inochi-podcast class)
    mp = re.search(r"const PODCAST_URL\s*=\s*'([^']*)'", html)
    if mp and mp.group(1) and not re.match(r'https?://', mp.group(1)):
        pp = SONGS / '_assets' / folder / mp.group(1).lstrip('/')
        if not pp.exists() or pp.stat().st_size == 0:
            finds.append(f'PODCAST_URL {mp.group(1)} does not resolve to a real '
                         f'file under _assets/{folder}/ — player gets HTML')

    # PARITY: still a structural clone of the template it was built from
    if parity:
        template, key = _template_for(folder)
        if not template:
            finds.append('no build state resolves this folder — parity unchecked')
        elif slug == template:
            pass  # the template itself needs no self-parity
        else:
            r = subprocess.run(
                [sys.executable, str(HERE / 'parity_audit.py'), slug, template,
                 '--key', key or ''],
                capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                tail = (r.stdout or r.stderr).strip().splitlines()
                finds.append('parity vs ' + template + ' FAILED: '
                             + (tail[-1] if tail else 'see parity_audit'))
    return finds


def sweep(parity=True):
    """{slug: [findings]} for every dir root SONGS[] links."""
    return {slug: check_dir(slug, parity=parity) for slug in live_slugs()}


def main(argv):
    as_json = '--json' in argv
    res = sweep()
    bad = {s: f for s, f in res.items() if f}
    if as_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(f'validate_live — {len(res)} live dir(s) from root SONGS[]')
        for slug, finds in res.items():
            if finds:
                print(f'\n  ✗ {slug}')
                for f in finds:
                    print(f'      - {f}')
            else:
                print(f'  ✓ {slug}')
        print('\n' + ('LIVE SET DIRTY — a promoted page no longer serves what it '
                      'validated as.' if bad else
                      'live set clean — every promoted page serves exactly what '
                      'it validated as.'))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
