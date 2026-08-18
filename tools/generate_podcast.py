#!/usr/bin/env python3
"""Generate a podcast MP3 from the podcast_script in data.json.

Usage: python3 tools/generate_podcast.py songs/silhouette/data.json
Output: ./tts_output/Podcast/{slug}_podcast.mp3

CLIP-SPLICE ENTRIES (language safety — the owner's rule: the English voice NEVER speaks
Japanese, not even in the podcast). A JP particle referenced in isolation (a lone
わ, は, etc.) must be voiced by the curated HUMAN recording, not TTS. Any podcast
entry may carry a clip marker — a dict element `{"clip": "<repo-relative path>"}`
anywhere after e[1] — e.g.
    ["JP", "わ", {"clip": "songs/_assets/inochi-mijikashi/audio/jp/word_v1_wa.mp3"}]
or, once force-aligned, the marker rides as a 5th element:
    ["JP", "わ", 91.7, [["わ", 91.7]], {"clip": ".../word_v1_wa.mp3"}]
When a clip marker is present the human recording is spliced in place of TTS,
transcoded + loudnorm'd to sit at the podcast's speaking level (see CLIP_LOUDNORM).
`align_podcast.py` preserves the marker across re-alignment.
"""

import json, sys, os, base64, time, requests, subprocess, tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Podcast segment target format (matches Google Chirp3-HD MP3 output) + the
# loudness the spliced human particle clip is normalized to so it sits naturally
# among the TTS segments (measured JP TTS voice ~ -16..-19 LUFS).
CLIP_SR = "24000"
CLIP_BR = "32k"
CLIP_LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


def find_clip(entry):
    """Return the clip path from a podcast entry's dict marker, or None."""
    for x in entry[2:]:
        if isinstance(x, dict) and x.get("clip"):
            return x["clip"]
    return None


def render_clip_mp3(clip_rel):
    """Transcode a curated human clip to the podcast segment format + loudness.
    clip_rel is repo-root-relative. Returns MP3 bytes ready to concatenate."""
    src = clip_rel if os.path.isabs(clip_rel) else os.path.join(REPO_ROOT, clip_rel)
    if not os.path.exists(src):
        print(f"  ERROR: clip not found: {src}")
        sys.exit(1)
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
    # -write_xing 0 + no id3 tag: the podcast is a raw concatenation of MP3
    # frames, so an embedded Xing/LAME info frame or ID3 tag mid-stream shows up
    # as "Header missing" garbage at the splice. Google's TTS segments carry no
    # such header; match that so the human clip concatenates cleanly.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", src, "-af", CLIP_LOUDNORM,
         "-ar", CLIP_SR, "-ac", "1", "-c:a", "libmp3lame", "-b:a", CLIP_BR,
         "-write_xing", "0", "-id3v2_version", "0", "-map_metadata", "-1",
         "-f", "mp3", tmp],
        check=True)
    with open(tmp, "rb") as f:
        data = f.read()
    os.unlink(tmp)
    return data

VOICES = {
    # New format: single narrator + native foreign speaker
    "HOST":   {"name": "en-US-Chirp3-HD-Charon", "languageCode": "en-US"},
    "GUEST":  {"name": "en-US-Chirp3-HD-Charon", "languageCode": "en-US"},
    "JP":     {"name": "ja-JP-Chirp3-HD-Kore",    "languageCode": "ja-JP"},
    "ES":     {"name": "es-US-Chirp3-HD-Kore",    "languageCode": "es-US"},
    # Legacy keys for older podcast scripts (HOST_A/HOST_B two-host format)
    "HOST_A": {"name": "en-US-Chirp3-HD-Charon", "languageCode": "en-US"},
    "HOST_B": {"name": "en-US-Chirp3-HD-Kore",   "languageCode": "en-US"},
    "EN":     {"name": "en-US-Chirp3-HD-Kore",    "languageCode": "en-US"},
}

def load_key():
    for path in [".env", os.path.join(os.path.dirname(__file__), "..", ".env")]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.startswith("GOOGLE_TTS_KEY="):
                        return line.split("=", 1)[1].strip()
    print("ERROR: No .env file with GOOGLE_TTS_KEY found")
    sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 tools/generate_podcast.py songs/{slug}/data.json")
        sys.exit(1)

    data_path = sys.argv[1]
    en_mode = "--en" in sys.argv
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    script_key = "podcast_script_en" if en_mode else "podcast_script"
    script = data.get(script_key)
    if not script:
        print(f"ERROR: No {script_key} found in data.json")
        sys.exit(1)

    key = load_key()
    slug = data["slug"]
    out_dir = os.path.join("tts_output", "Podcast")
    os.makedirs(out_dir, exist_ok=True)
    suffix = "_en" if en_mode else ""
    out_path = os.path.join(out_dir, f"{slug}_podcast{suffix}.mp3")

    total = len(script)
    parts = []

    # Cost estimate
    total_chars = sum(len(e[1]) for e in script)
    print(f"Podcast: {total} segments, ~{total_chars:,} chars")
    voice_keys_used = set(e[0] for e in script)
    print(f"Voices: {', '.join(sorted(voice_keys_used))}")
    print(f"Output: {out_path}\n")

    for i, e in enumerate(script, 1):
        voice_key, text = e[0], e[1]
        voice = VOICES[voice_key]
        label = {"HOST": "Host", "HOST_A": "Host-A", "HOST_B": "Host-B", "JP": "JP", "ES": "ES", "EN": "EN"}.get(voice_key, voice_key)
        preview = text[:60] + ("..." if len(text) > 60 else "")

        # Clip-splice: a human recording replaces TTS for this entry (particle in
        # isolation must be the curated voice, never the English/JP synthetic voice).
        clip = find_clip(e)
        if clip:
            print(f"  [{i}/{total}] {label}: {preview}  [HUMAN CLIP {os.path.basename(clip)}]")
            parts.append(render_clip_mp3(clip))
            time.sleep(0.05)
            continue

        print(f"  [{i}/{total}] {label}: {preview}")

        result = None
        for attempt in range(3):
            resp = requests.post(
                f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}",
                json={
                    "input": {"text": text},
                    "voice": {
                        "languageCode": voice["languageCode"],
                        "name": voice["name"]
                    },
                    "audioConfig": {
                        "audioEncoding": "MP3",
                        "effectsProfileId": ["headphone-class-device"]
                    }
                }
            )
            result = resp.json()
            if "audioContent" in result:
                break
            if attempt < 2:
                wait = 2 * (attempt + 1)
                print(f"    Retrying in {wait}s (attempt {attempt+2}/3)...")
                time.sleep(wait)

        if "audioContent" in result:
            parts.append(base64.b64decode(result["audioContent"]))
        else:
            err = result.get("error", {}).get("message", str(result))
            print(f"  ERROR on segment {i}: {err}")
            print("  Stopping.")
            sys.exit(1)

        # Rate limit: pause between each segment for Chirp 3 HD
        time.sleep(0.5)

    if not parts:
        print("No audio generated.")
        sys.exit(1)

    # Stitch MP3 segments
    with open(out_path, "wb") as f:
        for part in parts:
            f.write(part)

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"\n✓ Podcast: {size_mb:.1f} MB, {total} segments → {out_path}")
