#!/usr/bin/env python3
"""verify_sync — the LISTEN gate: prove shipped line timings match what is
actually sung in the video audio.

Born from the ema 20-seconds-early ship (2026-07-07): every structural check
passed (parity, E-checks, reveal geometry on a fake clock) while the whole
sheet sat in album time, not video time. No gate LISTENED. This one does.

For each sampled line it slices the song audio at the line's claimed window
(±0.4s) and whisper-transcribes the slice. PASS = the transcript contains
the line's reading (fuzzy hira 3-gram containment — whisper garbles sung
Japanese constantly, so the bar is "this window contains THIS line", not a
clean transcription). A global offset fails every window at once; a single
smeared line fails its own.

Usage (parler env — needs faster_whisper/pykakasi/jaconv):
  conda run -n parler python tools/songcraft/verify_sync.py <key> [--yt <id>]
      [--lines 0,5,11,...] [--threshold 0.35] [--all]

Audio: corpus/wsync_<yt>.wav, else /tmp/wsync_<yt>.wav (whisper_sync's cache).
Exit 0 = every sampled window matched; 1 = any miss (with per-line detail).
A FAIL means listen to that window yourself before shipping — this gate errs
loud on purpose. Weak PASSes near the threshold deserve ears too.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILDS = HERE / 'builds'
CORPUS = HERE / 'corpus'


def hira(s, _kk=[]):
    from pykakasi import kakasi
    import jaconv
    if not _kk:
        _kk.append(kakasi())
    s = unicodedata.normalize('NFKC', s)
    return re.sub(r'[^ぁ-ゖー]', '', jaconv.kata2hira(
        ''.join(x['hira'] for x in _kk[0].convert(s))))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('key')
    ap.add_argument('--yt', default='')
    ap.add_argument('--lines', default='', help='comma-separated indices (default: ~every 3rd line incl. first+last)')
    ap.add_argument('--threshold', type=float, default=0.35)
    ap.add_argument('--all', action='store_true', help='check every line')
    a = ap.parse_args()

    lyr = json.loads((BUILDS / f'{a.key}.lyrics.json').read_text())
    yt = a.yt
    if not yt:
        try:
            st = json.loads((BUILDS / f'{a.key}.build_state.json').read_text())
            yt = (st.get('meta') or {}).get('yt') or ''
        except Exception:
            pass
    wav = next((p for p in (CORPUS / f'wsync_{yt}.wav', Path(f'/tmp/wsync_{yt}.wav'))
                if p.is_file()), None)
    if wav is None:
        sys.exit(f'[verify_sync] no song audio for {a.key} (yt={yt or "?"}) — '
                 f'the sync step downloads it; re-run whisper_sync first.')

    lines = lyr['lines']
    if a.all:
        idxs = list(range(len(lines)))
    elif a.lines:
        idxs = [int(x) for x in a.lines.split(',')]
    else:
        idxs = sorted(set([0, len(lines) - 1] + list(range(2, len(lines) - 1, 3))))

    from faster_whisper import WhisperModel
    m = WhisperModel('small', device='cpu', compute_type='int8')
    fails = []
    for i in idxs:
        ln = lines[i]
        b, e = ln['begin_ms'] / 1000 - 0.4, ln['end_ms'] / 1000 + 0.4
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(max(0, b)),
                            '-t', str(e - b), '-i', str(wav), tf.name], check=True)
            segs, _ = m.transcribe(tf.name, language='ja', beam_size=3)
            heard = ''.join(s.text for s in segs)
        exp, got = hira(ln['text']), hira(heard)
        grams = [exp[j:j + 3] for j in range(len(exp) - 2)] or [exp]
        hit = sum(1 for g in grams if g in got) / len(grams)
        ok = hit >= a.threshold
        print(f"{i:3} {'PASS' if ok else 'FAIL':4} {hit * 100:3.0f}%  "
              f"{ln['begin_ms'] / 1000:7.2f}s  {ln['text'][:24]}  heard: {heard[:30]}")
        if not ok:
            fails.append(i)
    if fails:
        print(f'\n✗ SYNC LISTEN GATE: {len(fails)}/{len(idxs)} sampled windows do not '
              f'contain their line ({fails}). A cluster of fails = global offset; '
              f'a lone fail = that line. LISTEN before shipping.')
        sys.exit(1)
    print(f'\n✓ SYNC LISTEN GATE: {len(idxs)}/{len(idxs)} sampled windows contain their line.')


if __name__ == '__main__':
    main()
