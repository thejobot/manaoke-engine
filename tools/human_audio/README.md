# Human-recorded word audio

The fix for TTS mangling short words / single morae (頃 → "goroo", 気 → "kii").
Caches clean **human** recordings in `library/` for reuse across songs, so we
never re-fight TTS on the small parts. **Three interchangeable sources**, same
`library/<kanji>__<kana>.mp3` writer naming and same particle guard:

1. **`corpus.py`** — local NHK/yomichan corpus (~250k native clips, offline;
   tiers nhk16 > shinmeikai8 > forvo > jpod, homophone-safe kanji+kana exact
   matching). The BEST source — try it first.
   `python3 corpus.py resolve 頃 ころ` / `python3 corpus.py fetch 頃 ころ`.
2. **`fetch.py`** — JapanesePod101 dictionary endpoint (online; what
   Jisho/Yomitan use). Broadest online coverage incl. particles.
3. **`tofugu.py`** — local WaniKani/Tofugu corpus (offline; CC-BY-SA, one mostly
   consistent pro speaker). The clean-licensed fallback. Run it for the misses,
   or with `--overwrite` to prefer Tofugu's voice.

**Reading the library:** TWO name forms coexist (`<kanji>__<kana>.mp3` and the
bare `<kanji>.mp3` slug() degenerate for kanji == kana, e.g. に.mp3) — readers
must use `fetch.library_lookup(surface, kana)`, never re-derive `slug()` paths
(that's how the curated `の__の.mp3` went unreachable). Writers keep `slug()`.

## Use
```bash
# online source — explicit kanji:kana pairs
python3 fetch.py --words 気:き 頃:ころ 日:ひ
# harvest a song's vocab (feed CORRECT kana — jp_speak that repeats the kanji misses)
python3 fetch.py --from-song ../../songs/<dir>/data.json

# offline fallback for the misses (same flags + --overwrite)
python3 tofugu.py --from-song ../../songs/<dir>/data.json
python3 tofugu.py --words 頃:ころ --overwrite
```
Hits land in `library/<kanji>__<kana>.mp3`. Misses are listed at the end.

### tofugu.py corpus + caveat
Corpus lives at `~/Desktop/JP TTS Research/tofugu-wanikani-audio/lib/mp3/`
(6,355 native word recordings, `SURFACE【READING】.mp3`; override with `$TOFUGU_DIR`).
Re-clone: `git clone --depth 1 --filter=blob:none --sparse <repo> && git sparse-checkout set lib/mp3`.
**The corpus mixes two speakers (male Tokyo pro + female Kansai amateur) and the
filename doesn't say which** — so spot-listen each clip before baking it in (you're
loudnorm'ing it in by hand anyway, see below). Coverage skews to common content
words — strongest exactly on the short single-kanji ones TTS garbles.

## Install a library clip into a song
Use `tools/human_audio/install_word.py --song <folder> --sec <secId> --rom <romUid> --src <clip> [--pin] [--chain]` — loudnorm → wav master → served mp3 → provenance update → optional lexicon pin → large-v3 read-back with auto-rollback; the Denmoku Words tab queues exactly this. The manual two-step below is what it automates (kept for reference). A swapped clip is NOT live until re-assemble rotates AUDIO_V (validate_song E18); finish with `manaoke_build.py rebuild <key> --why <clip>`.

Two steps — write the **wav master**, then compress to the **mp3 the page serves**:
1. Loudnorm to the production target and write the wav master at
   `songs/_assets/<song>/audio/jp/word_<sec>_<rom>.wav`:
   two-pass `loudnorm=I=-16:TP=-1.5:LRA=11:linear=true`, `-ar 44100`, `pcm_s16le`.
   (See the swap block in the session that created this dir, or `.../REFINE-*/loudnorm_all.py`.)
2. **Compress to the served mono mp3:** `python3 tools/human_audio/jp_to_mp3.py --song <song>`
   (`ffmpeg -ac 1 -ar 48000 -c:a libmp3lame -b:a 80k`). The runtime + `tts_manifest.json`
   reference `audio/jp/word_<sec>_<rom>.mp3` (lowercase) — the v095+ word-by-word drill
   fetches the `.mp3`, not the `.wav`. Keep the `.wav` master alongside it (the Anki kit
   reads it; legacy build dirs still play it). Skipping step 2 = the new clip is silently
   absent for the drill (it 404s, the page falls back to the gloss). See the car-glitch note
   in the repo CLAUDE.md "Shared audio / lean builds".

## Rules / gotchas
- **Miss detection:** the "not found" reply is a fixed file (md5 `7e2c2f95…`,
  52288 B). `fetch.py` fingerprints it — never trust the HTTP 200.
- **は / へ / を:** the endpoint gives the headword reading "ha / he / wo", but as
  grammatical particles they're spoken "wa / e / o". `fetch.py` refuses these
  (`BAD-PARTICLE`). Keep the TTS わ/え/お clip for particle は/へ/を.
- **Verb te-forms** (数えて, 消えて) aren't dictionary headwords — query the dict
  form (数える, 消える) only if you want the dict-form audio; otherwise TTS handles
  te-forms fine (it doesn't garble multi-mora forms).
- **Coverage:** content words + most particles (に, も, と, から, で, が, か, ね) hit;
  pure grammar の and some slang/katakana names miss — TTS already handles those.
- **Licensing:** `fetch.py` = JapanesePod101 / Innovative Language asset —
  standard in the Anki/Yomitan study community; fine for a private learning site.
  `tofugu.py` = WaniKani/Tofugu corpus, **CC-BY-SA-4.0** (attribute Tofugu +
  WaniKani) — the clean-licensed source, now wired up.

`library/` is committed so the recordings travel with the repo.
