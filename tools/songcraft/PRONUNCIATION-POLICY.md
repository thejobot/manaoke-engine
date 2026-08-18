# Pronunciation Policy — detector, memory, and the line between them

**Read-back is the DETECTOR. The lexicon is the MEMORY.** The whisper large-v3
read-back (`verify_jp_pronunciation.py`, and gen_audio's inline gate on short
dict-miss renders) finds mispronounced clips. `pronunciation_lexicon.json` is
where a find becomes permanent: a word caught once can never regress to TTS —
not on this song, not on the next one, not after a re-render. gen_audio refuses
to Kokoro-voice a listed word (study word, citation clip, or legacy job), and
`validate_song.py` E15 fails any build whose served clip for a listed word
lacks provenance from that entry's allowed sources.

This file is the reasoning record. It grows **a paragraph only when a NEW
failure class appears**. A routine find — read-back flags a word, the word gets
a lexicon entry — is one JSON entry, not a paragraph here.

## Failure-class catalog (real incidents)

**Carrier-cut window swallow** (こと, 2026-07-03). The Qwen carrier-phrase cut
isolates a word by whisper word-timestamps inside a carrier phrase. The cut
window ate the preceding words: the shipped こと clip actually said 大事なこと.
The small-model spot check passed it (superstring — see below); the large-v3
read-back caught it. Lesson: a cut clip must be verified for *exact* content,
and the word joined the lexicon with `allow: ["curated"]`.

**Short-word TTS hallucination** (≤2-mora dict-miss). Kokoro renders of 1–2
mora words are where TTS quietly ships garbage: いい→"いえ", どっか→"どうか",
こと→"こど" all passed a small-model spot check. Code-level rule (not lexicon):
every `kokoro_dictmiss` render must survive the large-v3 read-back or the build
fails. Words that fail it repeatedly get listed.

**Superstring read-back loophole** (phrase_cut verify). The cut verifier
accepted `target in heard` — 'こと' inside 'だいじなこと' passed, which is
exactly how the carrier-cut swallow shipped. `phrase_cut.py --strict` now
requires exact folded equality; use it for any lexicon-remediation cut.

**Transcription-invisible cut damage** (いい/の class, 2026-07-07, backlog
0bd85bd1). A truncated or contaminated take can transcribe PERFECTLY: the
0.29s いい carrier-cut read back as これでいい and containment passed it; the
きっと cut carried the next word's onset as a +17dB burst whisper simply
ignored; a hard-cut long vowel "sounds obviously wrong but transcribes fine."
Read-back alone can never close this class — the cure is acoustic physics:
`tools/human_audio/clip_physics.py` (voiced span vs weighted morae + energy at
the effective end, thresholds calibrated on the recovered bad takes, regression
fixtures in `physics_fixtures/`). Every render/copy path (gen_audio), install
(install_word), and cut (phrase_cut, which also energy-trims next-word bleed)
now runs it; `sweep_clip_physics.py` covers the whole library and
`validate_song.py` E19 blocks a 'fail' verdict without a provenance
`physics_waiver`. Read-back equality for ≤2-mora words is now EXACT, both in
gen_audio and install_word — containment was the hole. And per the どっか/床
finds during remediation: a dictionary candidate can be a false homophone
(読過 for どっか) or read back wrong (床 heard ここ) — the install gates,
not the source tier, are what make a swap trustworthy.

**Whisper-limitation words + the evidence ladder** (2026-07-07). Some isolated
short words are untranscribable to whisper no matter how good the take
(けって→決定, とこ→ここ/投稿, どっか→読歌). The discriminator is PROMPT
RESISTANCE: transcribe plain, then with `initial_prompt=<kana>。` — a good
take flips to an exact match (OLさん, NHK 読過), a bad take stays wrong
(Aivis とこ stayed 投稿 = really long-voweled). install_word encodes the
narrow automatic case (5+ morae, plain within one mora, prompted exact →
`readback: prompted`) and takes `--waive-readback WHY` for operator-ear
installs beyond it — the waiver must record whose ear and what evidence.
Engine ladder when the dictionary misses: Qwen phrase_cut (now energy-trims
next-word bleed + physics per take) → AivisSpeech Aida → **Google Cloud TTS**
(`GOOGLE_TTS_KEY` in .env, ja-JP-Neural2-C solved とこ when all three others
failed; provenance 'google') — every rung behind the same read-back + physics
gates.

## The escalation ladder

Escalate only as far as the failure demands:

1. **Respell** — the word is fine, the *orthography* misleads the voice: author
   `jp_speak` with the spoken form (the は→わ pattern). No lexicon entry.
2. **Allow-restricted lexicon entry** — TTS is untrustworthy for this word but
   real-voice sources work: list it with `allow` limited to
   `curated` / `qwen` / `aivis` as evidence warrants. The default (no `allow`
   key) already excludes `kokoro` and `kokoro_dictmiss`.
3. **Pinned clip** — even sourcing is error-prone (wrong dict homophone, bad
   cuts keep passing): set `clip` to a known-good file under
   `tools/human_audio/`; gen_audio installs exactly those bytes.

## Choosing an allow set

- Was the *dictionary* clip itself ever the problem (wrong reading, wrong
  homophone)? Then `curated` alone is not enough evidence — pin a clip.
- Did a Qwen carrier cut fail for this word (こと)? Drop `qwen`:
  `allow: ["curated"]`.
- Did the dict clip fix it and cuts were never tried? Keep the wider
  `["curated", "qwen", "aivis"]` so remediation options stay open.
- Omit `allow` entirely when the only known-bad source is TTS itself.

## What stays in CODE, not the lexicon

Class rules belong with code; the lexicon holds *word* exceptions.

- **Lone particles** — `LONE_PARTICLES` in gen_audio + validate_song E9. Every
  isolated particle routes around Kokoro by rule; listing them here would be
  redundant and E15 deliberately skips them (no double-fire with E9).
- **は/へ/を spoken forms** — E8 + the canonical-clip system
  (`fetch.py`/`tofugu.py` `BAD_PARTICLES` refuse them by design; jp_speak
  carries わ/え/お). NEVER seed は/へ/を or わ/え/お into the lexicon.
- **Bare-gloss style** — E7. A style-class rule about *English* glosses, not a
  pronunciation exception.

## How to add a word

- `python3 tools/songcraft/manaoke_build.py lexicon add <word> --kana <kana>
  --reason "..."` (CLI verb in progress), or
- `verify_jp_pronunciation.py <slug> <folder> --add-to-lexicon` (auto-feeds
  every flagged word; fill in `carrier` afterwards), or
- hand-edit `pronunciation_lexicon.json`. Key = folded spoken kana
  (strip punctuation/長音/っ, widen small vowels: ぁぃぅぇぉ→あいうえお).

Adding a word has teeth immediately: on the next gen_audio run, an existing
clip for that word whose provenance is `kokoro`/`kokoro_dictmiss` is **deleted
and re-routed** (the skip-existing shortcut is bypassed), and E15 fails the
build until the served clip's source is in the allow set.

## Where dictionary clips come from (source tiers, 2026-07-07)

gen_audio's dictionary-priority lookup (`_dict_word_clip` /
`_curated_particle_clip`) and the denmoku audition endpoint share one order:

1. **library/** — the committed cache, read DUAL-FORM (below).
2. **`tools/human_audio/corpus.py`** — the local NHK/yomichan corpus
   (~250k native clips at `~/Desktop/JP TTS Research/yomichan-audio/
   user_files/`, `$YOMICHAN_AUDIO_DIR` to override), tier order
   **nhk16 > shinmeikai8 > forvo > jpod**. Matching is homophone-safe:
   kanji surface must pair exactly with the requested kana in the entry
   (気/き can never return 木/き); a pure-kana surface only matches kana
   headwords (NHK accepts a lone kana entry like の→野【の】, refuses
   multi-kanji homophone kana); forvo (filename = term, reading
   unrecorded) is used for kanji only when jmdict says the spelling has
   exactly one reading. は/へ/を are refused like every dict source —
   their headword clips say ha/he/wo, not the spoken particle.
   Provenance `src` carries the tier + entry (`nhk16:2017….mp3 野【の】`).
3. **fetch.py** — JPod101 online.
4. **tofugu.py** — WaniKani/Tofugu offline corpus.

**Two clip-name forms coexist in library/ — readers must go dual-form.**
`slug()` (the WRITER convention, unchanged) produces `<surface>__<kana>.mp3`,
degenerating to bare `<surface>.mp3` when surface == kana (に.mp3, お.mp3 are
canon). But a 2026-07-06 install also wrote the other form for surface == kana
(`library/の__の.mp3`), a name `slug()` never generates — so that curated の was
unreachable and ema shipped a TTS の. Every reader now goes through
`fetch.library_lookup(surface, kana)`, which tries `<surface>__<kana>` THEN
the `slug()` form (and `.wav` twins where the caller asks). Never re-derive
library paths from `slug()` in read paths.

## Full remediation chain (or the page still sounds like TTS)

Replacing a clip is not done until the page serves it:
`gen_audio` (or manual install) → `build_drill_concat.py` (the drill tails
embed word audio) → assemble/rebuild the page → AUDIO_V rotation so cached
clients refetch. Skipping any step leaves the old TTS bytes audible somewhere.
