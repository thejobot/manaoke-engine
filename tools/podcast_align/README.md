# Podcast word-timing (forced alignment)

`align_podcast.py` generates the per-word timing that drives the immerse
"Liner Notes" transcript karaoke. **Every new song's podcast must be run through
this** so each word — English narration AND Japanese reader lines — lights when
it is actually spoken.

## Why forced alignment (not whisper transcription)
Whisper *transcription* hallucinates on bilingual audio: English whisper "hears"
English over the Japanese reader and stamps it on the wrong line (it lit "Life
is" on top of the Japanese title). Forced alignment is given the **known script
text** and only finds *when* each word is spoken — constrained to the real words,
it cannot hallucinate. This is the standard going forward.

## Run it
```bash
# conda parler env has the deps (ctc-forced-aligner, fugashi, pykakasi, unidecode, onnxruntime, ffmpeg)
PY=/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python
$PY tools/podcast_align/align_podcast.py \
    --audio /path/to/<song>_podcast.mp3 \
    --data  songs/<song-dir>/data.json
# then bump the cache hash (data.json changed):
python3 tools/bump_asset_versions.py songs/<song-dir>/index.html
```
Input `data.json` must already have a `podcast_script` of `[speaker, text, ...]`
entries (speaker = "HOST" English / "JP" Japanese). The tool rewrites each to
`[speaker, text, lineStartSec, [[tokenText, startSec], ...]]`. It refuses to write
if the stamp count drifts or token-concat != text (integrity gate).

## Per-song setup
- Archaic/forced spellings the tokenizer can't split (e.g. an all-katakana title)
  go in `KNOWN_SPLITS` at the top of `align_podcast.py` (concat of values == key).
- A second song needs its own `songs/_assets/<song>/` and `_redirects` rules
  (see CLAUDE.md "Shared audio / lean builds").

## The renderer side (already in the song page template)
New songs are cloned from the current promoted version, so they inherit:
- `renderTranscript` consuming the `[token, time]` pair format (EN spaced, JP
  adjacent) + a JP-pair-missing `console.warn` guard.
- `updateWordHighlight` + the requestAnimationFrame driver (samples ~60Hz, not
  the ~4Hz `timeupdate`) so the fill never skips.
- The high-contrast **reveal** CSS: unspoken `.24` opacity → spoken bright
  (EN white, JP `rgb(255,221,232)` + weight 700) → current word white + glow.
Keep these when starting a new song; only the per-song timing data changes.
