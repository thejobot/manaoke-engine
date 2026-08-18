#!/usr/bin/env python3
"""Compress JP per-word study clips from WAV master -> mono 80kbps mp3 (served).

Why this exists (2026-06-29, "car glitch" audit):
The per-word JP clips used to ship as 44.1kHz STEREO PCM WAV @1411kbps (~150KB
each, ~20MB/song). On flaky cellular the word-by-word study drill's per-word cold
fetch lost its retry race and dropped to the English gloss ("in the car it
glitched, didn't go word-by-word"). The fix: serve them as mono 80kbps mp3
(~11KB/clip, 20MB -> ~1.7MB). The song runtime + tts_manifest reference `.mp3`.

This is the turnkey compress step. It runs AFTER the WAV master is rendered +
two-pass loudnorm'd (see ../songcraft/README.md and SONG-CONTRACT §3.1) and
BEFORE deploy. It keeps the `.wav` masters in place (legacy build dirs + the Anki
study-kit builder still read them); it only ADDS the `.mp3` the page serves.

Idempotent: re-encoding to mp3 is harmless; `--force` re-encodes existing mp3s.
Never delete-then-recreate (writes temp then os.replace) so a partial run can't
leave a gap. Lowercase `.mp3` only — `.MP3` 404s on case-sensitive Cloudflare.

Usage:
    python3 tools/human_audio/jp_to_mp3.py                 # default: inochi-mijikashi
    python3 tools/human_audio/jp_to_mp3.py --song <slug>   # songs/_assets/<slug>/audio/jp
    python3 tools/human_audio/jp_to_mp3.py --dir <path>    # any dir of word_*.wav
    python3 tools/human_audio/jp_to_mp3.py --force         # re-encode even if .mp3 exists
"""
import argparse
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must match the EN clip profile + the runtime expectation exactly.
FFMPEG_ARGS = ["-ac", "1", "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "80k"]


def compress_dir(jp_dir, force=False):
    if not os.path.isdir(jp_dir):
        sys.exit(f"not a dir: {jp_dir}")
    # ALL JP wavs, not just word_*: the full-line (line_uNN) and grammar example
    # clips are served as mp3 too (drill tail / grammar section, via the manifest).
    wavs = sorted(f for f in os.listdir(jp_dir) if f.endswith(".wav"))
    if not wavs:
        sys.exit(f"no word_*.wav in {jp_dir}")
    made = skipped = failed = 0
    for wav in wavs:
        src = os.path.join(jp_dir, wav)
        dst = os.path.join(jp_dir, wav[:-4] + ".mp3")
        if os.path.exists(dst) and not force:
            skipped += 1
            continue
        tmp = dst + ".tmp"
        try:
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src,
                 *FFMPEG_ARGS, "-f", "mp3", tmp],  # .tmp suffix defeats format inference
                check=True,
            )
            os.replace(tmp, dst)  # atomic; never delete-then-recreate
            made += 1
        except subprocess.CalledProcessError:
            failed += 1
            print(f"  FAIL: {wav}", file=sys.stderr)
            if os.path.exists(tmp):
                os.remove(tmp)
    print(f"{jp_dir}: encoded={made} skipped(existing)={skipped} failed={failed}")
    return failed == 0


def main():
    ap = argparse.ArgumentParser(description="Compress JP word WAVs -> mono 80k mp3")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--song", default="inochi-mijikashi",
                   help="slug under songs/_assets/<slug>/audio/jp (default inochi-mijikashi)")
    g.add_argument("--dir", help="explicit dir of word_*.wav (overrides --song)")
    ap.add_argument("--force", action="store_true", help="re-encode even if .mp3 exists")
    a = ap.parse_args()
    jp_dir = a.dir or os.path.join(REPO, "songs", "_assets", a.song, "audio", "jp")
    ok = compress_dir(jp_dir, force=a.force)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
