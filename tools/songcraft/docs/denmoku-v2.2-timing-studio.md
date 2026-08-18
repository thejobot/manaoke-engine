# Denmoku v2.2 addendum — Timing Studio (2026-07-08)

## v2.3 — the word editor (2026-07-09, the owner's user-notes round)

The line drawer slimmed down to: karaoke reveal → zoom bar → waveform → focus
panel. Gone: the "▶ hear it" button (the reveal grew a round **▶/⏸ toggle on
its left** — tap to play the line, tap to PAUSE, tap to resume), the
word-level chip bar under the wave, the ✎ fine-tune ±ms steppers (drag is the
only nudge surface), the word text labels ON the waveform (markers only — tap
a reveal word to focus; its marker turns amber), and the ⚓ emoji (now a small
amber dot / a plain `hand-set` chip).

New in the reveal:
- **Per-part romaji.** When one study word spans several tokens (かけ+てく =
  kaketeku), each token's romaji shows the full word with ITS slice bolded —
  server-computed (`rom_hl`, kana→romaji consumption in `_attach_rom_ranges`).
- **Held sung vowels (ほど…おおお).** Focus a word → "♪ mark the held part" →
  tap the wave where the words end and the singing holds (or drag the amber ♪
  marker later; "un-mark" clears). Stored as `words[wi].hold_ms` + a
  scope='hold' sidecar entry; the reveal wipes the kanji over [begin, hold]
  then a 〜 dances (.holding) while the vowel is sung out; `content_to_data`
  packs the morae into [begin, hold] and stretches the final mora to the word
  end, so the SONG PAGE's karaoke fill holds the vowel too — no template change.
- **Word-list edits.** Focus panel: **× delete** (a stray 、 token — comes off
  the line text and the page), **+ add a word** (an ad-lib like "hey";
  Japanese additions are auto-declared coverage exceptions until they get a
  study word), **✎ edit** (token text, its kana reading, or the full line
  reading). All via `timing_edit.py worddel|wordadd|wordedit` → scope='textop'
  replay entries in the sidecar (refetches/re-aligns reproduce the edit;
  existing overrides are key/index-migrated), mirrored into content.json, and
  E10-walk-coherent by construction.

Ladder: the first chip now reads **"last saved 3:42 PM"** (edits save the
moment they land; `state.saved_at`); two doors — **open edits in preview ↗**
(`/preview/<slug>/`, which now folds pending edits into data.json as it
serves: `_freshen_preview_if_stale`) and **open in preview ↗** (the private
manaoke.app slug URL, shown once pushed; `state.live_url`).

New/changed endpoints: `POST /api/timing/hold {key,line,word,at_ms|clear}`,
`POST /api/timing/worddel {key,line,word}`, `POST /api/timing/wordadd
{key,line,word,text[,where,reading]}`, `POST /api/timing/wordedit
{key,line,word[,text,reading,line_kana]}` — all return the fresh timing model
like set/word/adopt.

Words tab audition: the human tiers were missing whenever word_meta's kana
field repeated the KANJI (44/108 ema words — the "only my TTS voice" bug);
the server now derives the true reading from the rom uid (`_kana_from_rom`)
before hitting the library/NHK/Tofugu/JPod tiers. New last tier: **generated
takes** — 3 Google Cloud TTS ja-JP Neural2 voices (key from `.env`, synth
from the kana, cached per audition dir, pushed with honest `--source google`
provenance), plus an explicit note when no human recording of the exact form
exists anywhere.

Extends denmoku-v2-api.md + denmoku-v2.1-addendum.md (both stay in force). Same rules:
stdlib-only server, CLI is the only mutator, flat `/api/<noun>` naming, `{"error": str}`
on non-2xx, one FIFO job at a time, no pipeline jargon in UI copy, in-place render
(the 2s `/api/state` poll never rebuilds an open tab).

The Timing tab became a studio: zoom + a playhead, per-word focus (romaji, meaning, our
recordings), a full-song section-alignment overview, an **inline karaoke reveal** (below),
and a truthful save→online→main-page ladder that makes "it changed on this page but never
reached manaoke.app" (the いい trap) impossible to miss.

## Inline karaoke reveal (2026-07-09 — replaced the iframe preview)

The karaoke preview is no longer a full song-page iframe. It is the **open line, inline,
right above the waveform** (`tmRevealHTML` → `.tm-reveal`): each word is kanji over its
romaji, with the full hiragana reading (`line.kana`) underneath as a kanji-reading aid.

- **Real-time sync, one clock.** The reveal is painted by `tmRevealPaint(ms)` called from the
  SAME rAF playhead loop that moves the wave cursor (`_tmClipBegin + audio.currentTime*1000`).
  So ▶ hear it (whole line) and ▶ this word light the words up live — each word is
  `future → now → past`, and the current word wipes left→right (`--p` 0→100%, a CSS
  `background-clip:text` gradient). No iframe, no seek, no `content_to_data` rebuild.
- **Always matches the edits.** The reveal DOM rebuilds from `TM.data` on every `tmRender`,
  so a nudge/drag is reflected immediately; the paint reads word times live each frame. There
  is no "stale — refresh" state anymore.
- **Idle = readable.** With no audio playing, `.tm-reveal` drops `.playing` and every word is
  full-ink readable; the focused word (tap a reveal word to focus, same as a time-scale chip)
  is outlined amber.
- This removed: the `#tmPrevPanel` iframe + `tmPreviewMount/Drive/Refresh/Unmount`, the
  `previewOn/previewStale` state, the "▸ karaoke preview" toggle, the redundant "👂 what's
  sung" button (it was byte-identical to ▶ this word), and the temporary "🔊 test audio"
  diagnostic. `/api/preview_data` still exists but the UI no longer calls it.

## New / changed endpoints

- `GET /api/songwav/<key>[?hifi=1]` — served with **HTTP Range (206)** (`protocol_version
  HTTP/1.1`). Default is the ~7 MB mono wsync wav; `?hifi=1` prefers the full-quality hq wav.
  404 when no corpus audio is on this machine (e.g. kaijuu).
- `GET /api/songclip/<key>?b=<ms>&e=<ms>` — a small **from-0 PCM slice** of the song wav
  (pure-stdlib `wave`, cached to `builder/cache/clips/`). This is what ▶ hear it / ▶ this word
  play: **iOS Safari cannot seek a network WAV** (it only plays one from the start), so instead
  of seeking `songwav` we serve just the `[b,e)` window and play it from 0; the playhead maps
  the clip's 0-based time back to absolute song-ms via `_tmClipBegin`. This is also what the
  inline reveal clocks off.
- `GET /api/timing/<key>` — payload enriched (read-model only, additive):
  - per line: `section {id,name,short_name}` (content.json line↔section join by index, with
    a normalized-text fallback), `translation`, and `kana` — the full hiragana reading from
    content.json (the reveal's reading aid). Coverage is 100% for 5 of 6 songs; ema authored
    no per-line kana (backlog b2300530) so its reveal shows per-word romaji only.
  - per timing word: `study[]` — every study word CONTAINED in that token (a token like
    ホラでも holds ホラ + でも), each `{jp, rom, en, gloss, particle, uid, clip_url,
    clip_exists, provenance:{source}, pinned, pos}`. Best-effort; coverage is 44–100%
    song-by-song, so `[]` is normal (the UI shows "no study word on file").
  - song-level `state {slug, folder, root_slug, preview_url, edited, built, pushed,
    promoted, has_corpus}` — the truth model (below).
- `POST /api/preview_data {key}` — the LIGHT reveal refresh. Shells the **parler python**
  `content_to_data.py <key> <slug>` synchronously (~1–3s) so `songs/<slug>/data.json` (and
  its kana_timings) pick up the latest edits, WITHOUT a full assemble. 409 if a job for the
  key is active or the per-key lock is held. MUST use parler (system python3 silently writes
  empty kana_timings — a lying preview).
- `POST /api/ship {key}` — queue `manaoke_build.py ship <key>` (scoped commit + push of this
  song only; not `git add -A`).
- `POST /api/promote {key}` — refuses (409) unless `state.pushed`, then queues
  `manaoke_build.py promote <key> --push` (repoint root SONGS[] + commit + push index.html).
- `POST /api/peaks/build {key}` — queue `peaks.py <key> --bin-ms 2` (sharper zoom). 409 when
  no corpus audio.
- `POST /api/timing/set|word|adopt` — now return the fresh `timing` read model in the
  response, so the client patches the edited row + drawer in place (preserving zoom / focus /
  playhead / scroll) instead of a full re-render. Also 409 while a job for the key runs.

Job `step` labels: `ship:<key>`, `promote:<key>`, `peaks`, plus the existing
`word-push:<uid>`, `refetch-lyrics`, and `assemble` (via `/api/run`).

## The state (truth) ladder

`build_state`'s deploy/promote flags LIE (deploy is print-only; promote marks "done" on the
local repoint before any push; a fresh-slug rebuild never reset them). So the ladder is
derived from files + git, never from step status:

- **edited** = `max mtime(lyrics.json, timing_overrides.json, content.json) > deps.json mtime`
  (the light preview refresh touches data.json only, so it does NOT clear this).
- **built** = `songs/<slug>/` exists + `deps.json` present.
- **pushed** = git working tree clean AND `origin/main..HEAD` empty for
  `tools/songcraft/builds/<key>.*`, `songs/<slug>`, `songs/_assets/<folder>` (memoized on
  `HEAD sha + .git/index mtime`; never runs in the 2s poll).
- **promoted** = root `index.html` SONGS[] slug == `build_state.slug` (the ONLY honest "on
  manaoke.app" signal).

UI chips: `saved on this Mac · page rebuilt · up at the preview link · on manaoke.app`. The
first unfinished one carries its action (apply my changes → `/api/run` assemble; put it
online → `/api/ship`; put it on the main page → `/api/promote`, behind a custom confirm —
never a native prompt). "preview link" and "manaoke.app" are a hard wall — a preview is never
called live/deployed.

## CLI additions (manaoke_build.py)

- `ship <key> [--dry-run]` — scoped `git add` + pathspec-scoped `git commit` + `git push`;
  commit and push are separate so a failed push is re-runnable. Sets the deploy step done on
  success; journals a lessons entry.
- `promote <key> --push` — after the root repoint, one scoped commit + push of `index.html`.
  Refuses if `songs/<slug>` isn't pushed yet.
- `dispatch_rebuild` on a fresh-slug rebuild now resets deploy + promote to `pending` (the
  real cure for the lying flags).

## peaks.py

- `--bin-ms N` (default 10; 2 = sharper zoom, ~5× file size). Threaded through
  `wav_peaks`/`build_key`; the client already reads `bin_ms` from the JSON, so no format bump.

## Dev / testing

`python3 server.py --port 8873 --dev` — a read-only mirror: refuses every mutating POST, does
NOT start the job worker (two workers on shared `builds/` = a race), and does NOT clobber
`.app-url` (the real Denmoku's Dock pointer survives). Run it in tmux. Drive the read/render
UI headlessly there; never ship/promote/assemble from it.

## Carve-outs

- inochi-mijikashi's corpus audio was downloaded 2026-07-09 (hq + wsync wavs; mix-lane
  peaks), so the studio plays it like every other song. Its DEMUCS VOCAL STEM is still
  absent — the "voice only" lane falls back to the full mix until a demucs run writes
  `corpus/demucs/htdemucs/hq_7cCL0owFBqk/vocals.wav`. kaijuu remains audio-less.
- Study coverage is partial and song-dependent. ema + inochi nest their study words under
  `sections[].words` (not top-level `words[]`); `_study_index` reads BOTH layouts, so their
  focus panel + per-word romaji populate correctly (an earlier "no study data" bug).
