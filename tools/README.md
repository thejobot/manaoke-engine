# Manaoke Tools

**Read this when:** running `generate_tts.py` / `generate_podcast.py` / `build_song.py` / `upload_r2.py`, tweaking voices, uploading to R2, or organizing TTS test output.

For the song-page spec and the add-a-song workflow see `tools/songcraft/BUILDER.md` + `tools/songcraft/SONG-CONTRACT.md`. This file owns the tool-level details: voice IDs, code snippets, R2 structure, API key location, testing conventions.

## Scripts

| Script | Purpose |
|---|---|
| `fetch_lyrics.py` | Pull word-level lyric timing for a song via LyriCool → `experiments/tap-lines/data/lyrics/{slug}.json`. |
| `build_song.py` | **LEGACY / SUPERSEDED — do not run for new songs.** Emits HTML from an old inline f-string template on the dead R2-streaming `audioCache` model; it has NONE of the v095 runtime fixes (lean `_assets`, word-by-word stall watchdog, unmuted iOS unlock, prewarm throttle). New/updated songs are built by the **songcraft orchestrator** (`tools/songcraft/manaoke_build.py init/run`, template `songs/inochi-mijikashi-e03jz0` — see `tools/songcraft/BUILDER.md`), which inherits every fix. Kept for reference only. |
| `generate_tts.py` | Word audio MP3s via Google Cloud TTS (Neural2). Runs `validate_tts_safety.py` first and aborts on failure (`--skip-safety` to override). |
| `validate_tts_safety.py` | **Language-safety gate.** Fails the build if an `en-US`/`es-US` clip speaks Japanese, a `ja-JP` clip has no Japanese, or any string the page will speak has no matching clip (which would fall back to the browser voice). Prevents the "English voice says pinsaro" class of bug. Run on a manifest: `python3 tools/validate_tts_safety.py songs/{slug}/tts_manifest.json`. |
| `generate_podcast.py` | Podcast MP3 from the `podcast_script` list in `data.json` (Chirp 3 HD). |
| `upload_r2.py` | Uploads `Audio Lyrics/` and `Podcast/` to R2 via `wrangler`. |
| `build_timestamp_recorder.py` | Copies the recorder template into each song's directory. See `experiments/timestamp-recorder/README.md`. |
| `bump_asset_versions.py` | Append `?v=<sha8>` to `data.json` / `tts_manifest.json` references in each song's HTML so browsers refetch only when content changes. Idempotent. **Run before every commit that changes those files.** See "Cache busting" in `CLAUDE.md`. |
| `validate_song.py` | **Build-integrity linter.** Replicates the page's own romaji-matching walk and fails the build on coverage gaps + asset/manifest/schema problems. See "Validation" below. Run on a song dir: `python3 tools/validate_song.py songs/{slug}`. |
| `data_schema_example.json` | Reference for `data.json` shape. |

New songs are built by the songcraft orchestrator: `manaoke_build.py init/run` (template `songs/inochi-mijikashi-e03jz0`) — see `tools/songcraft/BUILDER.md`. (`build_song.py`/`upload_r2.py` are legacy R2-era steps, not used by pipeline builds.)

> Note: the runtime side of the same guard lives in each song's `index.html` (`HAS_CJK`, `speakEN`, `fallbackTTS`, `speakTranslationPill`): English browser voices are structurally blocked from reading Japanese, and all English speech goes through curated `en_speak`, never the display `en` gloss (which can contain romaji like "pinsaro (slang)").

## Validation

`validate_song.py` is the song-build linter. It exists because two bug classes shipped to production with nothing checking song builds: (a) **romaji coverage gaps** — the page builds each lyric line's romaji ONLY from study-word matches over `data.json` `sections[].words[]`; any span of `apple_lyrics.lines[].text` no word covers renders with NO romaji (the real victims were "OL さん" in コピーにお茶汲み OL さん and the trailing か in いつかは言えるか / あんたに言えるか); and (b) a CSS specificity bug that killed the translation toggle (CSS is out of scope here — too hard to lint statically). This tool covers everything else.

```bash
python3 tools/validate_song.py songs/<slug>
```

Exit `0` = clean **or** warnings-only; `1` = at least one ERROR; `2` = bad usage. Run it after editing a song's `data.json` / `index.html` / `tts_manifest.json`, before promoting a build.

It is **separate from** `validate_tts_safety.py` — that one is the language-safety gate (no EN clip speaks Japanese, nothing the page speaks falls through to the browser voice). `validate_song.py` does NOT duplicate those CJK-in-EN checks; run both.

How it works: it reads the matching helpers straight out of the song's `index.html` (`tokenize` / `collectStudyWords` / `buildRomajiParts` / `lineTrKey` / `_romUid`, and the `LINE_TR` / `LINE_EXPLAIN` object literals) and replays the page's exact greedy first-match word walk against each lyric line, so a gap fails the lint instead of silently shipping.

| Check | Severity | What it catches |
|---|---|---|
| **E1** line-coverage | ERROR | Any non-whitespace span of a lyric line that the word-matching walk leaves uncovered (→ no romaji on the page). Reports the span + the line's `kana_timings` reading. |
| **E2** LINE_TR | ERROR | Any non-instrumental line whose `lineTrKey` (trailing `(×N)` + all whitespace stripped) has no `LINE_TR` entry with non-empty `en`. |
| **E3** LINE_EXPLAIN | ERROR | Same coverage check against `LINE_EXPLAIN`. |
| **E4** audio existence | ERROR | Every section word needs `audio/jp/word_<secId>_<romUid>.mp3` (the served mono-80k clip; a legacy `.wav` is also accepted) + `audio/en/word_<secId>_<romUid>_en.mp3` (+ `_ctx.mp3` when the word has `context`) under `songs/_assets/<song>/`. `<song>` resolves from the repo-root `_redirects` rewrite (falls back to `data.json` `slug`). |
| **E5** manifest | ERROR | Every manifest entry's file exists; every section `speak_en` and every `LINE_EXPLAIN` value is present byte-identical as a manifest key (`entry[1]`). |
| **E6** schema | ERROR | Required word fields (`jp`,`rom`,`en`,`en_speak`) non-empty; required top-level `data.json` keys present. |
| **W1** rom-vs-kana | WARNING | A line's concatenated matched-word romaji disagrees with its `kana_timings` reading (lenient: macrons/long-vowels/small-tsu/は-particle folded). Surfaces genuinely different readings, e.g. 独り `hitori` vs a kana reading of `dokuri`. Line-level, because `kana_timings` can't be soundly aligned to individual text chars (one kanji = multiple kana). |
| **W2** missing hint/context | WARNING | Words lacking `hint` or `context`. |
| **W3** duplicate jp | WARNING | The same `jp` appears in multiple sections with a different `en`. |

## Lyric data via LyriCool

> **LEGACY — superseded.** The current lyric path is `tools/songcraft/fetch_timed_lyrics.py` with vendored `tools/songcraft/lyric_sources/` (apple/netease/lrclib), writing `tools/songcraft/builds/<key>.lyrics.json`. This section is the pre-songcraft flow, kept for history.

`fetch_lyrics.py` is a thin shell over LyriCool, a separate multi-source lyric workbench. LyriCool cross-checks Apple Music TTML + LRCLIB + NetEase YRC, normalises each to the same `line` shape with word-level timing, and exports JSON identical to what Manaoke reads at `experiments/tap-lines/data/lyrics/{slug}.json`.

```bash
python3 tools/fetch_lyrics.py <apple-music-url> <slug>
```

First-time setup: `python3 ~/lyricool/lyricool.py setup` (pastes Apple Music tokens). Tokens cache at `~/.lyricool-config.json`. LRCLIB and NetEase need no auth.

Why this flow: before LyriCool, Apple word-timing JSONs were hand-stitched per song. This removes that step — any song with Apple Music word-level lyrics now has a one-shot import, and non-Apple songs can fall back to NetEase YRC (CJK-strong) or LRCLIB line-level.

Planned next step (per `songs/README.md`): when the unified-song page graduates, the canonical file location moves from `experiments/tap-lines/data/lyrics/{slug}.json` → `songs/{slug}/lyrics.json` and `build_song.py` consumes it directly.

## API key

`.env` at repo root (gitignored):

```
GOOGLE_TTS_KEY=AIza...
```

Restricted in GCP Console to Cloud Text-to-Speech API only. Load with `dotenv` or `open(".env").read().split("=",1)[1].strip()`.

`pip install` on this Mac requires `--break-system-packages`.

## Voices

**Word audio (Neural2):**

> **LEGACY** — superseded: pipeline word audio is `tools/songcraft/gen_audio.py` with the human dictionary chain; per-song audio ships in-repo under `songs/_assets/`. Kept for history.

| Lang | Voice | Rate |
|---|---|---|
| JP | `ja-JP-Neural2-B` | 0.85 |
| EN | `en-US-Neural2-J` | 1.0 |
| ES | `es-US-Neural2-A` | 0.9 |

**Podcast (Chirp 3 HD):**

| Key | Voice | Notes |
|---|---|---|
| `HOST` | `en-US-Chirp3-HD-Charon` | Male, clear, English narrator |
| `JP` | `ja-JP-Chirp3-HD-Kore` | Female, native Japanese |
| `ES` | `es-US-Chirp3-HD-Kore` | Female, native Spanish |

Chirp 3 HD doesn't support SSML. Expression comes from punctuation, ellipses, sentence rhythm.

## Podcast creative principles

- **One English host, one native foreign-language speaker.** The English host tells the story entirely in English — context, grammar, culture, translation. The native speaker (JP/ES) only ever speaks in their language. They are the voice of the language itself.
- **The English host NEVER pronounces foreign words.** No romanized Japanese, no attempted Spanish, no foreign vocabulary. He describes concepts; the native speaker provides the actual word. The Dakiti podcast is the gold standard — the host never says a single Spanish word.
- **Exception: proper nouns and loanwords already common in English** (Tokyo, Yokohama, karaoke, sushi, reggaeton, Naruto). The host can say these because an English speaker would. The native speaker ALWAYS echoes right after with correct pronunciation.
- **The native speaker delivers:** (1) actual song lyrics as you move through the song, (2) any vocabulary word being discussed, (3) component parts of words being broken down, (4) any foreign word the host needs — place names, grammar terms, anything not English.
- **Flow mirrors the Study tab.** English explains, native pronounces.
- The native speaker has personality — they can react, agree, emphasize ("そうですね..." or "Así es...") — but always in their language. Not a pronunciation robot.
- Go verse by verse through the song.
- "Oh I love the way that sounds" energy — repeat words that deserve it.
- Never soften difficult content — state it plainly like the song does.
- Pacing via `"..."` pauses and natural writing.

## R2 bucket structure

> **LEGACY** — superseded: pipeline word audio is `tools/songcraft/gen_audio.py` with the human dictionary chain; per-song audio ships in-repo under `songs/_assets/`. Kept for history.

Cloudflare R2 bucket `manaoke`, served via `audio.manaoke.app`. The old `pub-*.r2.dev` dev URL is disabled — all audio URLs use `https://audio.manaoke.app`.

```
manaoke/
└── Song {N} {Japanese Title}/
    ├── Audio Lyrics/
    │   └── {NNN}_{JP|EN}_{display text}.mp3
    └── Podcast/
        └── {slug}_podcast.mp3
```

File naming: `safe_filename` replaces `/:*?"<>|\` with `_` and truncates to 80 chars. Song 1 uses a legacy podcast filename (`creephyp_podcast_1774240782320 2.mp3`); new songs use `{slug}_podcast.mp3`.

**Critical:** song number, Japanese title, and slug must be consistent across (1) R2 folder name, (2) `audioCache` URLs in the song HTML, and (3) root `index.html` song card href. Plan R2 paths **before** building HTML.

## TTS script — word audio (Phase 4)

> **LEGACY** — superseded: pipeline word audio is `tools/songcraft/gen_audio.py` with the human dictionary chain; per-song audio ships in-repo under `songs/_assets/`. Kept for history.

```python
import requests, base64

key = open(".env").read().split("=", 1)[1].strip()

def safe_filename(text, max_len=80):
    for ch in r'/:*?"<>|\\':
        text = text.replace(ch, '_')
    return text.strip('. ')[:max_len] or 'untitled'

def generate_tts(text, lang, voice, rate, output_path):
    resp = requests.post(
        f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}",
        json={
            "input": {"text": text},
            "voice": {"languageCode": lang, "name": voice},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": rate}
        }
    )
    data = resp.json()
    if "audioContent" in data:
        with open(output_path, "wb") as f:
            f.write(base64.b64decode(data["audioContent"]))
        return True
    print(f"ERROR: {data}")
    return False

# JP: generate_tts(jp_speak, "ja-JP", "ja-JP-Neural2-B", 0.85, path)
# EN: generate_tts(en_speak, "en-US", "en-US-Neural2-J", 1.0, path)
```

Save MP3s to `./tts_output/Audio Lyrics/`.

## Podcast script format + generation (Phase 5)

Script is a list of `[voice_key, text]` pairs:

```python
SCRIPT = [
    ["HOST", "English narration..."],
    ["JP",   "日本語テキスト"],
    ["ES",   "Texto en español"],
]
```

Generation:

```python
import requests, base64

key = open(".env").read().split("=", 1)[1].strip()

voices = {
    "HOST": {"name": "en-US-Chirp3-HD-Charon", "languageCode": "en-US"},
    "JP":   {"name": "ja-JP-Chirp3-HD-Kore",   "languageCode": "ja-JP"},
    "ES":   {"name": "es-US-Chirp3-HD-Kore",   "languageCode": "es-US"},
}

parts = []
for voice_key, text in SCRIPT:
    voice = voices[voice_key]
    resp = requests.post(
        f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}",
        json={
            "input": {"text": text},
            "voice": {"languageCode": voice["languageCode"], "name": voice["name"]},
            "audioConfig": {
                "audioEncoding": "MP3",
                "effectsProfileId": ["headphone-class-device"]
            }
        }
    )
    data = resp.json()
    if "audioContent" in data:
        parts.append(base64.b64decode(data["audioContent"]))
    else:
        print(f"ERROR on segment: {data}")
        break

with open(f"./tts_output/Podcast/{slug}_podcast.mp3", "wb") as f:
    for part in parts:
        f.write(part)
```

## Upload to R2 (Phase 6)

> **LEGACY** — superseded: pipeline word audio is `tools/songcraft/gen_audio.py` with the human dictionary chain; per-song audio ships in-repo under `songs/_assets/`. Kept for history.

Verify existing structure first:

```bash
wrangler r2 object list manaoke --prefix "Song"
```

Upload:

```bash
for file in ./tts_output/Audio\ Lyrics/*.mp3; do
  wrangler r2 object put "manaoke/Song {N} {Title}/Audio Lyrics/$(basename "$file")" \
    --file "$file" --content-type "audio/mpeg" --remote
done

wrangler r2 object put "manaoke/Song {N} {Title}/Podcast/{slug}_podcast.mp3" \
  --file "./tts_output/Podcast/{slug}_podcast.mp3" \
  --content-type "audio/mpeg" --remote
```

Verify:

```bash
wrangler r2 object list manaoke --prefix "Song {N}"
```

### Gotchas

- `upload_r2.py` requires `--remote` on all `wrangler r2 object put` commands. Without it, wrangler targets local dev, not the real bucket. The script already includes it; if running wrangler manually, don't forget.
- `upload_r2.py` only uploads audio files listed in the song's `tts_manifest.json`, not everything in `tts_output/Audio Lyrics/`. Prevents cross-contamination when multiple songs' TTS files share the output dir.
- **Re-recording just a podcast:** don't run the full `upload_r2.py` — use `wrangler r2 object put ... --remote` directly. See `songs/README.md`.

## TTS test file conventions

All TTS test audio goes in `tts_output/Audio Testing 123/`, organized by language and test purpose:

```
tts_output/Audio Testing 123/
├── Japanese/
│   ├── voice-comparison/
│   ├── teach-pattern/
│   └── ssml-vs-plain/
├── Spanish/
├── Old English/
└── English/
```

Create new subfolders per experiment (`pitch-accent/`, `speed-tests/`, etc.).

**Every test MP3 must have a companion `.txt` file** with the same name, containing:
1. The text sent to TTS (original script/characters)
2. Hiragana/katakana reading (JP) or phonetic guide
3. Romanization
4. English translation
5. TTS settings used (voice, rate, SSML if any)

### Companion file format

Example — `teach_v3_medium.txt`:

```
=== What you should hear ===

きらめく　　き　ら　め　く　　きらめく
kirameku    ki ra me ku    kirameku

English: to sparkle, to glitter

=== What we sent to TTS ===

Text: 煌めく。... き...ら...め...く。... 煌めく。
Voice: ja-JP-Neural2-B
Rate: 0.85
SSML: no (plain text)

=== What we're testing ===

Can ellipsis characters between syllables make Neural2-B slow down naturally
for a "teach" pattern — say the word, break it down, say it again — without
using SSML? ...
```

**"What you should hear" comes first** — kana with spaced syllables matching the audio phases, romaji underneath, English meaning. Format like a subtitle: big and clear, matching audio pacing. The explanation at the bottom helps future-you (or another listener) understand the goal and how this file relates to other tests.
