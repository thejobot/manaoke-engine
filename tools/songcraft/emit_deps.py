#!/usr/bin/env python3
"""emit_deps.py — one-time ADOPTION of an already-built song into the deps
manifest (builds/<folder>.deps.json) WITHOUT re-rendering anything.

Re-encoding would change concat bytes and rotate AUDIO_V, so this tool only
READS: it hashes the current inputs/clips/template, reads the shipped windows
from songs/_assets/<folder>/audio/drill/drill_map.json (falling back to the
page's inline DRILL_MAP), reads AUDIO_V off the deployed page, reconstructs
each line's seg-input list the exact way build_drill_concat.py would (its own
derive_line_segs, against current data/tts_manifest + the puppeteer drill
extraction — read-only), and writes a deps.json asserting the current state as
fresh. Every drill line it records carries adopted:true so a later real build
can replace it; build_drill_concat's incremental skip treats adopted entries
with non-empty inputs as skippable (out bytes + inputs verified), and entries
with EMPTY inputs (extraction fallback) as unverifiable — never skipped.

Usage:
  python3 tools/songcraft/emit_deps.py <folder> <deploy_slug> [--key K] [--template T]
  e.g.  emit_deps.py inochi-mijikashi inochi-mijikashi-v098
        emit_deps.py odoriko odoriko-z5z2gv
"""
import argparse, json, re, sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import assemble_page as ap          # sha8 / tree_sha8 / audio_version / ROOT
import build_drill_concat as bdc    # derive_line_segs / seg_inputs / recipe

ROOT = ap.ROOT


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('folder', help='asset folder (data.json slug), e.g. odoriko')
    p.add_argument('deploy_slug', help='deployed dir under songs/, e.g. odoriko-z5z2gv')
    p.add_argument('--key', default='', help='builds/<key>.* key (default: folder)')
    p.add_argument('--template', default='', help='template dir (default: build_state, else deploy_slug)')
    a = p.parse_args()

    folder, dslug = a.folder, a.deploy_slug
    key = a.key or folder
    song_dir = ROOT / 'songs' / dslug
    data = json.loads((song_dir / 'data.json').read_text())
    if data.get('slug') != folder:
        raise SystemExit(f"data.json slug {data.get('slug')!r} != folder {folder!r}")

    template = a.template
    if not template:
        bs = HERE / 'builds' / f'{key}.build_state.json'
        if bs.exists():
            template = json.loads(bs.read_text()).get('template', '')
    template = template or dslug   # inochi IS the template
    tdir = ROOT / 'songs' / template

    html = (song_dir / 'index.html').read_text()
    m = re.search(r"const AUDIO_V = '([^']*)';", html)
    page_av = m.group(1) if m else ''
    av_computed, clips = ap.audio_version(folder)

    # page inputs — the same set assemble_page records, existing files only
    inputs = {}
    for f in [HERE / 'builds' / f'{key}.content.json', HERE / 'builds' / f'{key}.line_maps.json',
              song_dir / 'data.json', song_dir / 'tts_manifest.json',
              *sorted((ROOT / 'fonts' / folder).glob('*.woff2'))]:
        if f.exists():
            inputs[str(f.relative_to(ROOT))] = ap.sha8(f)

    # shipped drill map: prefer the _assets copy; the page's inline DRILL_MAP is
    # the authority for what actually plays — cross-check, prefer page on drift.
    asset_root = ROOT / 'songs' / '_assets' / folder
    drill_dir = asset_root / 'audio' / 'drill'
    dm_file = drill_dir / 'drill_map.json'
    shipped = json.loads(dm_file.read_text()) if dm_file.exists() else None
    mi = re.search(r'const DRILL_MAP = (.*?);\n', html, re.S)
    inline = json.loads(mi.group(1)) if mi else None
    if shipped is None and inline is None:
        raise SystemExit('no drill_map.json and no inline DRILL_MAP — nothing to adopt')
    if shipped is not None and inline is not None and shipped != inline:
        print('[adopt] WARNING: drill_map.json != page DRILL_MAP; adopting the PAGE map')
        shipped = inline
    if shipped is None:
        print('[adopt] no drill_map.json; adopting the page inline DRILL_MAP')
        shipped = inline

    # reconstruct per-line inputs the way build_drill_concat would (read-only:
    # extract_drill.js renders nothing into songs/). Fallback on extractor
    # trouble: keep the line with inputs:[] — recorded windows/audio still let
    # the manifest describe the shipped state, but the skip logic must rebuild.
    cache = bdc.load_manifest_cache(str(song_dir))
    spec, spec_err = [], None
    try:
        spec = bdc.extract_spec(str(song_dir))
    except SystemExit as e:
        spec_err = str(e)
        print(f'[adopt] extract_drill failed ({e}); recording empty input lists')
    by_key = {}
    for L in spec:
        by_key.setdefault(L['lineKey'], L)

    lines, empty = {}, 0
    for lk, entry in shipped.items():
        base, _, vq = entry['audio'].partition('?v=')
        h = bdc.line_key_hash(lk)
        out_rel = f'audio/drill/line_{h}.mp3'
        if base != out_rel:
            print(f'[adopt] WARNING {lk[:24]}: audio {base} != derived {out_rel}')
        mp3 = asset_root / base
        if not mp3.exists():
            print(f'[adopt] WARNING {lk[:24]}: {base} missing on disk — line not adopted')
            continue
        out_sha8 = ap.sha8(mp3)
        rec = {'out': base, 'out_sha8': out_sha8, 'audio': entry['audio'],
               'adopted': True, 'inputs': [],
               'windows': {'dur': entry['dur'], 'words': entry['words'],
                           'tail': entry['tail']}}
        if vq and vq != out_sha8:
            # shipped ?v no longer matches the bytes on disk: desynced asset.
            # Record it, but the out_sha8/?v disagreement means the skip check
            # can never pass — the next real build re-renders it (correct).
            rec['note'] = f'shipped ?v={vq} != current bytes {out_sha8}'
            print(f'[adopt] WARNING {lk[:24]}: {rec["note"]}')
        L = by_key.get(lk)
        if L is not None:
            segs, gaps, wwi, tji, tei, roles, warn = bdc.derive_line_segs(
                L, str(asset_root / 'audio'), str(asset_root), cache)
            if segs:
                rec['inputs'] = bdc.seg_inputs(segs, roles, str(ROOT))
            else:
                rec['note'] = f'inputs unreconstructable: {warn or "no segs"}'
        else:
            rec['note'] = spec_err and f'extractor failed: {spec_err}' \
                or 'line not in current drill extraction'
        if not rec['inputs']:
            empty += 1
        lines[lk] = rec

    man = {
        'schema': 1, 'folder': folder, 'key': key, 'deploy_slug': dslug,
        'built_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'template': {'dir': template, 'tree_sha8': ap.tree_sha8(tdir),
                     'index_sha8': ap.sha8(tdir / 'index.html')},
        'recipe': {'assemble_page': {'tool_sha8': ap.sha8(ap.__file__)},
                   'build_drill_concat': bdc.tool_recipe()},
        'page': {'out': f'songs/{dslug}/index.html',
                 'out_sha8': ap.sha8(song_dir / 'index.html'),
                 'audio_v': page_av, 'inputs': inputs},
        'clips': clips,
        'drill': {'lines': lines},
    }
    if page_av != av_computed:
        man['page']['audio_v_note'] = (f'page ships {page_av!r} but current clips '
                                       f'hash to {av_computed!r}')
        print(f"[adopt] note: {man['page']['audio_v_note']}")

    out = HERE / 'builds' / f'{folder}.deps.json'
    out.write_text(json.dumps(man, ensure_ascii=False, indent=1) + '\n')
    print(f'[adopt] wrote {out.relative_to(ROOT)}: {len(inputs)} page inputs, '
          f'{len(clips)} clips, {len(lines)} drill lines '
          f'({len(lines) - empty} with reconstructed inputs, {empty} unverifiable)')


if __name__ == '__main__':
    main()
