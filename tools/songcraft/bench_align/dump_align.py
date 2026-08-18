#!/usr/bin/env python3
"""dump_align.py — run the CTC forced aligner (whisper_sync.align_lines) in a
pure DRY capacity and dump the RAW per-token alignment to a JSON file, without
ever reading from or writing to builds/.

The lyrics file is staged into a private temp dir and whisper_sync.BUILDS is
pointed there, so the real builds/<key>.lyrics.json is never touched — the
benchmark needs the aligner's opinion, not the pipeline's write path.

Two structures are dumped:
  raw_tokens : the aligner's untouched output — one entry per fugashi token
               with the raw CTC begin/end (ms, on the video-audio clock) and
               confidence score. This is the "pure aligner" hypothesis.
  per_line   : whisper_sync's per-line (begin, end) after its tail-clamp —
               kept for reference, not scored.

Run with the parler python (demucs + ctc_forced_aligner live there):
  .../envs/parler/bin/python dump_align.py <key> --yt <id> \
      --lyrics <path/to/lyrics.json> --out <hyps/key.json>
"""
import argparse, json, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # tools/songcraft — where whisper_sync lives

import whisper_sync  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('key')
    ap.add_argument('--yt', required=True)
    ap.add_argument('--lyrics', required=True,
                    help='lyrics.json to align (staged copy; builds/ is never read)')
    ap.add_argument('--out', required=True, help='where to write the raw dump')
    a = ap.parse_args()

    lyrics = json.loads(Path(a.lyrics).read_text())
    if 'lines' not in lyrics:
        raise SystemExit(f'{a.lyrics} has no "lines" key')

    stage = Path(tempfile.mkdtemp(prefix='bench_align_'))
    try:
        (stage / f'{a.key}.lyrics.json').write_text(
            json.dumps(lyrics, ensure_ascii=False))
        whisper_sync.BUILDS = stage  # align_lines reads BUILDS/<key>.lyrics.json
        per_line, lyr, toks, line_idx = whisper_sync.align_lines(a.key, a.yt)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if not per_line:
        raise SystemExit('no alignable lyric words')

    out = {
        'key': a.key,
        'yt': a.yt,
        'raw_tokens': [
            {'line': t['line'], 'text': t['text'], 'rom': t['rom'],
             'begin_ms': t.get('begin'), 'end_ms': t.get('end'),
             'cscore': t.get('cscore')}
            for t in toks
        ],
        'per_line': {str(i): list(be) for i, be in sorted(per_line.items())},
        'line_idx': line_idx,
        'line_text': {str(i): lyr['lines'][i].get('text', '') for i in line_idx},
    }
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    n_al = sum(1 for t in toks if t.get('begin') is not None)
    print(f'[dump] {a.key}: {len(toks)} tokens, {n_al} CTC-aligned, '
          f'{len(per_line)} lines -> {outp}')


if __name__ == '__main__':
    main()
