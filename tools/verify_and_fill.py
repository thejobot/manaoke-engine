#!/usr/bin/env python3
"""verify_and_fill.py — converge a song's R2 audio with its manifest, idempotently.

Pipeline: HEAD-check every manifest entry against audio.manaoke.app (with the
required Referer header), regenerate + upload the missing ones via wrangler,
re-HEAD-check. Exits non-zero if the bucket still doesn't match the manifest.

Auth:
    - GOOGLE_TTS_KEY in .env  (Cloud TTS API)
    - CLOUDFLARE_API_TOKEN in env  (auto-used by wrangler)

Usage:
    python3 tools/verify_and_fill.py songs/inochi-mijikashi-v26/tts_manifest.json \\
        --song-folder "Song 1 イノチミジカシコイセヨオトメ" [--verify-only]
"""
import argparse, base64, json, os, subprocess, sys, time
import urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BUCKET = "manaoke"
PUBLIC_BASE = "https://audio.manaoke.app"
FETCH_HEADERS = {
    "Referer": "https://manaoke.app/",
    "Origin": "https://manaoke.app",
    "User-Agent": "manaoke-pipeline/1.0",
}
VOICES = {
    "ja-JP": ("ja-JP-Neural2-B", 0.85),
    "en-US": ("en-US-Neural2-J", 1.0),
    "es-US": ("es-US-Neural2-A", 0.9),
}

def load_tts_key():
    for path in [".env", os.path.join(os.path.dirname(__file__), "..", ".env")]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.startswith("GOOGLE_TTS_KEY="):
                        return line.split("=", 1)[1].strip()
    env = os.environ.get("GOOGLE_TTS_API_KEY") or os.environ.get("GOOGLE_TTS_KEY")
    if env:
        return env
    sys.exit("ERROR: GOOGLE_TTS_KEY not in .env or env")

def head_one(song_folder, fname):
    url = f"{PUBLIC_BASE}/{urllib.parse.quote(song_folder)}/Audio%20Lyrics/{urllib.parse.quote(fname)}"
    try:
        req = urllib.request.Request(url, method="HEAD", headers=FETCH_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise
    except Exception:
        return False

def check_all(manifest, song_folder):
    out = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(head_one, song_folder, e[3]): e[3] for e in manifest}
        for f in as_completed(futs):
            out[futs[f]] = f.result()
    return out

def generate_tts(text, lang, voice, rate, api_key, retries=4):
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": lang, "name": voice},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": rate},
    }
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
    last = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=payload, timeout=30)
            data = resp.json()
            if "audioContent" in data:
                mp3 = base64.b64decode(data["audioContent"])
                if len(mp3) < 200:
                    raise RuntimeError(f"suspiciously small audio: {len(mp3)} bytes")
                return mp3
            last = data.get("error", data)
        except Exception as e:
            last = str(e)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"TTS failed after {retries} retries: {last}")

def wrangler_put(local_path, remote_key):
    """Upload via wrangler r2 object put. Raises on non-zero exit."""
    full_key = f"{BUCKET}/{remote_key}"
    proc = subprocess.run(
        ["wrangler", "r2", "object", "put", full_key,
         "--file", local_path,
         "--content-type", "audio/mpeg",
         "--remote"],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"wrangler put failed: {proc.stderr.strip() or proc.stdout.strip()}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", help="path to tts_manifest.json")
    ap.add_argument("--song-folder", required=True,
                    help="R2 folder name, e.g. 'Song 1 イノチミジカシコイセヨオトメ'")
    ap.add_argument("--verify-only", action="store_true",
                    help="report present/missing; don't generate or upload")
    ap.add_argument("--local-cache", default="tts_output/Audio Lyrics",
                    help="local mp3 dir (kept as side effect for resume/debug)")
    args = ap.parse_args()

    manifest = json.load(open(args.manifest))

    print(f"Manifest:  {args.manifest}  ({len(manifest)} entries)")
    print(f"R2 prefix: {BUCKET}/{args.song_folder}/Audio Lyrics/")
    print(f"Edge:      {PUBLIC_BASE} (HEAD with Referer)")
    print()

    print("--- Pass 1: verify ---")
    presence = check_all(manifest, args.song_folder)
    missing = [(i, e) for i, e in enumerate(manifest) if not presence[e[3]]]
    print(f"  present: {len(manifest) - len(missing)}   missing: {len(missing)}")
    for idx, (lang, _, _, fname) in missing[:30]:
        print(f"    [{idx+1:03d}] {lang}  {fname}")
    if len(missing) > 30:
        print(f"    ... and {len(missing)-30} more")

    if not missing:
        print("\n✓ all entries present on R2. nothing to do.")
        return 0
    if args.verify_only:
        print(f"\n--verify-only: would generate+upload {len(missing)} entries.")
        return 0

    print(f"\n--- Pass 2: generate + upload {len(missing)} ---")
    api_key = load_tts_key()
    os.makedirs(args.local_cache, exist_ok=True)
    ok, err = 0, 0
    for idx, (lang, _, speak_text, fname) in missing:
        voice, rate = VOICES[lang]
        local_path = os.path.join(args.local_cache, fname)
        remote_key = f"{args.song_folder}/Audio Lyrics/{fname}"
        try:
            mp3 = generate_tts(speak_text, lang, voice, rate, api_key)
            with open(local_path, "wb") as f:
                f.write(mp3)
            wrangler_put(local_path, remote_key)
            ok += 1
            print(f"  ✓ [{idx+1:03d}] {lang}  {fname}  ({len(mp3):,}B)")
        except Exception as e:
            err += 1
            print(f"  ✗ [{idx+1:03d}] {fname}: {e}")
        if (ok + err) % 10 == 0:
            time.sleep(1)

    # CDN may cache 404 briefly; give it a moment before re-checking.
    print("\n  (waiting 5s for edge to settle)")
    time.sleep(5)

    print("--- Pass 3: re-verify ---")
    presence2 = check_all(manifest, args.song_folder)
    still = [e for e in manifest if not presence2[e[3]]]
    print(f"  present: {len(manifest) - len(still)}   missing: {len(still)}")
    if still:
        for lang, _, _, fname in still[:30]:
            print(f"    {lang}  {fname}")
        return 1
    print("\n✓ converged. manifest and R2 match.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
