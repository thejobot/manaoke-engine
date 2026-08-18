#!/usr/bin/env python3
"""Library-wide acoustic sweep — clip physics over every JP clip of a song.

Runs tools/human_audio/clip_physics.py (duration + envelope vs the reading)
across a song's served JP audio and writes the verdicts to
builds/<folder>.clip_suspects.json. Three consumers:

  - validate_song.py E19: a 'fail' verdict without a physics_waiver in
    clip_provenance.json is a build error; 'suspect' is a warning. A word
    clip with no fresh sidecar entry (missing or sha8-stale) is an error —
    new clips in the いい/の defect class can't ship unchecked.
  - The Denmoku Words tab: suspects join the "needs your ear" strip
    regardless of provenance source — curated clips can be marginal too.
  - Operators: the printed summary is the audition worklist.

Coverage per song: study word clips (word_meta.json), ja-JP manifest clips
including full lines (tts_manifest.json), and podcast citation clips
(clip_provenance entries carrying kana). Incremental: an entry whose sha8
still matches the served bytes (same thresholds build) is not re-measured.

Run under the parler env:
  sweep_clip_physics.py <key> [<key>...] | --all
"""
import importlib.util
import json
import hashlib
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # tools/songcraft
ROOT = HERE.parents[1]                          # repo root
BUILDS = HERE / 'builds'
HUMAN = ROOT / 'tools' / 'human_audio'

_spec = importlib.util.spec_from_file_location(
    'manaoke_clip_physics', str(HUMAN / 'clip_physics.py'))
clip_physics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clip_physics)


def _sha8(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:8]


def _load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def collect_targets(key, st):
    """{out_rel: kana} for every JP clip the song serves."""
    folder = (st.get('meta') or {}).get('slug') or key
    slug = st.get('slug') or ''
    targets = {}
    # study words (the E19-gated class)
    for out_rel, meta in _load(BUILDS / f'{key}.word_meta.json', {}).items():
        targets[out_rel] = meta.get('kana') or meta.get('surface') or ''
    # every ja manifest clip (adds full lines and any stray ja entries)
    manifest = _load(ROOT / 'songs' / slug / 'tts_manifest.json', [])
    for entry in manifest:
        try:
            lang, _display, speak, rel = entry
        except (ValueError, TypeError):
            continue
        if lang == 'ja-JP' and rel.startswith('audio/'):
            targets.setdefault(rel[len('audio/'):], speak)
    # podcast citation clips (provenance carries their kana)
    for out_rel, ent in _load(BUILDS / f'{folder}.clip_provenance.json', {}).items():
        if out_rel.startswith('jp/podcast_') and ent.get('kana'):
            targets.setdefault(out_rel, ent['kana'])
    return folder, targets


def sweep(key):
    st = _load(BUILDS / f'{key}.build_state.json', None)
    if st is None:
        print(f'[sweep] {key}: no build state — nothing to sweep')
        return 0
    folder, targets = collect_targets(key, st)
    if not targets:
        print(f'[sweep] {key}: no JP clips known — nothing to sweep')
        return 0
    prov = _load(BUILDS / f'{folder}.clip_provenance.json', {})
    side_path = BUILDS / f'{folder}.clip_suspects.json'
    side = _load(side_path, {})
    old = side.get('clips', {}) if side.get('thresholds') == clip_physics.THRESHOLDS_ID else {}

    clips, counts = {}, {'pass': 0, 'suspect': 0, 'fail': 0}
    fresh = 0
    for out_rel, kana in sorted(targets.items()):
        f = ROOT / 'songs' / '_assets' / folder / 'audio' / out_rel
        if not f.exists():
            continue        # missing files are other gates' business
        sha = _sha8(f)
        ent = old.get(out_rel)
        # reuse needs BOTH unchanged bytes and an unchanged expected reading —
        # a kana/jp_speak correction must re-judge even if the audio didn't move
        if not (ent and ent.get('sha8') == sha and ent.get('kana') == kana):
            r = clip_physics.check(f, kana)
            m = r['metrics'] or {}
            ent = {
                'sha8': sha, 'kana': kana, 'verdict': r['verdict'],
                'reasons': r['reasons'], 'morae': r['morae'],
                'weighted': r['weighted'],
                'metrics': {k: m[k] for k in
                            ('file_dur', 'speech_dur', 'tail_max', 'tail_mean')
                            if k in m},
                'source': (prov.get(out_rel) or {}).get('source', ''),
            }
            fresh += 1
        clips[out_rel] = ent
        counts[ent['verdict']] = counts.get(ent['verdict'], 0) + 1

    side = {'version': 1, 'thresholds': clip_physics.THRESHOLDS_ID,
            'swept': time.strftime('%Y-%m-%d %H:%M'), 'clips': clips}
    side_path.write_text(json.dumps(side, ensure_ascii=False, indent=1))

    print(f"[sweep] {key}: {len(clips)} clips ({fresh} measured, "
          f"{len(clips) - fresh} unchanged) -> {counts['pass']} pass / "
          f"{counts['suspect']} suspect / {counts['fail']} fail "
          f"-> {side_path.name}")
    for out_rel, ent in sorted(clips.items()):
        if ent['verdict'] != 'pass':
            mark = '✗' if ent['verdict'] == 'fail' else '⚠'
            print(f"  {mark} {out_rel} ({ent['kana']}, {ent['source'] or 'no provenance'}): "
                  f"{'; '.join(ent['reasons'])}")
    return counts['fail']


def _resolve_key(arg):
    """Accept a build key OR an asset-folder name (they differ only for
    legacy keys; E19's advice prints the folder)."""
    if (BUILDS / f'{arg}.build_state.json').exists():
        return arg
    for p in BUILDS.glob('*.build_state.json'):
        st = _load(p, {})
        if (st.get('meta') or {}).get('slug') == arg:
            return p.name[:-len('.build_state.json')]
    return arg


def main(argv):
    keys = [_resolve_key(a) for a in argv if not a.startswith('--')]
    if '--all' in argv:
        keys = sorted(p.name[:-len('.build_state.json')]
                      for p in BUILDS.glob('*.build_state.json'))
    if not keys:
        print(__doc__.strip().splitlines()[-1])
        return 2
    total_fail = 0
    for key in keys:
        total_fail += sweep(key)
    # informational exit — validate_song E19 is the enforcing gate
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
