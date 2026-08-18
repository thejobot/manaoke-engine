#!/usr/bin/env python3
"""peaks.py — precompute waveform min/max peaks for the timing editor.

The drag-a-word editor needs to SHOW the audio it is editing against. No
waveform code existed anywhere in the repo and /api/songwav serves a whole
multi-MB wav with no Range support — so the editor reads a small precomputed
peaks JSON instead: per song, one [min, max] pair per 10ms bin, two lanes
(the Demucs vocal stem = where the singing actually is; the full mix = what
the listener hears), int8-quantized (-127..127, each lane normalized to its
own absolute peak) to keep files small.

Output: builds/<key>.peaks.json
  {"version": 1, "bin_ms": 10, "duration_ms": <bins*10>,
   "lanes": {"vocals": [[min,max], ...], "mix": [[min,max], ...]}}
Both lanes carry the SAME bin count (shorter lane zero-padded), so
duration_ms / bin_ms == len(lane) for every lane.

Audio sources (first hit wins):
  vocals  /tmp/demucs/htdemucs/hq_<yt>/vocals.wav, then corpus/demucs/...
  mix     corpus/hq_<yt>.wav, /tmp/hq_<yt>.wav, then the 16k wsync copies
<yt> comes from builds/<key>.content.json youtube_id (builds/index.json
meta.yt as fallback).

Needs numpy + soundfile: run under the parler python — invoking with plain
python3 auto re-execs into it.
  python3 tools/songcraft/peaks.py <key> [<key> ...]
  python3 tools/songcraft/peaks.py --all
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILDS = HERE / 'builds'
CORPUS = HERE / 'corpus'
TMP = Path('/tmp')
PARLER = Path('/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python')
BIN_MS = 10
VERSION = 1


def _ensure_deps():
    try:
        import numpy, soundfile  # noqa: F401
    except ImportError:
        me = Path(sys.executable).resolve()
        if PARLER.exists() and me != PARLER.resolve():
            os.execv(str(PARLER), [str(PARLER), str(Path(__file__).resolve())]
                     + sys.argv[1:])
        raise SystemExit(f'[peaks] needs numpy + soundfile — run under the '
                         f'parler python:\n  {PARLER} {__file__} ...')


def yt_id(key):
    p = BUILDS / f'{key}.content.json'
    if p.exists():
        y = json.loads(p.read_text()).get('youtube_id')
        if y:
            return y
    idx = BUILDS / 'index.json'
    if idx.exists():
        for it in json.loads(idx.read_text()):
            if it.get('key') == key:
                y = (it.get('meta') or {}).get('yt')
                if y:
                    return y
    raise SystemExit(f'[peaks] no youtube_id for {key!r} '
                     f'(builds/{key}.content.json / index.json)')


def _first(paths):
    for p in paths:
        if p.exists():
            return p
    return None


def lane_sources(yt):
    vocals = _first([TMP / 'demucs' / 'htdemucs' / f'hq_{yt}' / 'vocals.wav',
                     CORPUS / 'demucs' / 'htdemucs' / f'hq_{yt}' / 'vocals.wav'])
    mix = _first([CORPUS / f'hq_{yt}.wav', TMP / f'hq_{yt}.wav',
                  CORPUS / f'wsync_{yt}.wav', TMP / f'wsync_{yt}.wav'])
    return {'vocals': vocals, 'mix': mix}


def wav_peaks(path, bin_ms=BIN_MS):
    """[[min, max]] int8 pairs, one per bin_ms, lane-normalized to its own
    absolute peak so the full -127..127 range is used."""
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(str(path), always_2d=True)
    y = y.mean(axis=1)
    step = max(int(round(sr * bin_ms / 1000.0)), 1)
    n = math.ceil(len(y) / step) if len(y) else 0
    if not n:
        return []
    pad = n * step - len(y)
    if pad:
        y = np.concatenate([y, np.zeros(pad, dtype=y.dtype)])
    yb = y.reshape(n, step)
    mn, mx = yb.min(axis=1), yb.max(axis=1)
    peak = float(max(abs(float(mn.min())), abs(float(mx.max())), 1e-9))
    scale = 127.0 / peak
    qmn = np.clip(np.round(mn * scale), -127, 127).astype(int)
    qmx = np.clip(np.round(mx * scale), -127, 127).astype(int)
    return [[int(a), int(b)] for a, b in zip(qmn, qmx)]


def build_key(key, bin_ms=BIN_MS):
    yt = yt_id(key)
    srcs = lane_sources(yt)
    lanes = {}
    for name, p in srcs.items():
        if p is None:
            print(f'[peaks] {key}: no {name} wav for yt {yt} — lane skipped '
                  f'(run whisper_sync to regenerate stems)', file=sys.stderr)
            continue
        lanes[name] = wav_peaks(p, bin_ms)
    if not lanes:
        print(f'[peaks] {key}: NO audio found — skipped', file=sys.stderr)
        return False
    nbins = max(len(v) for v in lanes.values())
    for name, v in lanes.items():
        if len(v) < nbins:
            v.extend([[0, 0]] * (nbins - len(v)))
    doc = {'version': VERSION, 'bin_ms': bin_ms, 'duration_ms': nbins * bin_ms,
           'lanes': lanes}
    out = BUILDS / f'{key}.peaks.json'
    out.write_text(json.dumps(doc, separators=(',', ':')) + '\n')
    kb = out.stat().st_size / 1024
    print(f'[peaks] {key}: {nbins} bins ({nbins * bin_ms / 1000:.1f}s), lanes '
          f'{"+".join(sorted(lanes))} -> {out.name} ({kb:.0f}KB, {bin_ms}ms bins)'
          + '  '.join([''] + [f'{n}<-{srcs[n]}' for n in sorted(lanes)]))
    return True


def main():
    ap = argparse.ArgumentParser(description='precompute waveform peaks JSON')
    ap.add_argument('keys', nargs='*')
    ap.add_argument('--all', action='store_true',
                    help='every song with a builds/<key>.content.json')
    ap.add_argument('--bin-ms', type=int, default=BIN_MS,
                    help='ms per peak bin (default 10; 2 = sharper zoom, ~5x '
                         'file size). The client reads bin_ms from the JSON.')
    a = ap.parse_args()
    _ensure_deps()
    bin_ms = max(1, a.bin_ms)
    keys = a.keys
    if a.all:
        keys = sorted(p.name[:-len('.content.json')]
                      for p in BUILDS.glob('*.content.json'))
    if not keys:
        ap.error('give one or more keys, or --all')
    ok = sum(1 for k in keys if build_key(k, bin_ms))
    print(f'[peaks] {ok}/{len(keys)} song(s) written')
    if ok < len(keys):
        sys.exit(1)


if __name__ == '__main__':
    main()
