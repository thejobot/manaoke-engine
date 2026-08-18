# The Manaoke Song Page Builder

The pipeline, owned in code. One orchestrator drives a fixed sequence of steps;
a local HTML dashboard (the "denmoku") shows where every song stands, who owns
each step, and the exact command to run it. Nothing here needs a server or the
cloud except the two steps that legitimately do (licensed lyrics, and the deploy).

## The pieces

| File | Does |
|---|---|
| `manaoke_build.py` | The orchestrator. `init` a song, `run` a step (or `--auto` the whole auto-able sequence), `set` ownership/status, `dash` to re-render the dashboard. State per song in `builds/<key>.build_state.json`. |
| `builder/index.html` | The dashboard. Self-contained (state embedded, works on `file://`). Punch in a song name, expand each step's chevron for what it does + the command; ownership is colour-coded (green = the box runs it, purple = hand to a CLI, gold = a signed-in tab / a service). |
| `content_to_data.py` | Assembler. `builds/<key>.content.json` (authored study content) + `builds/<key>.lyrics.json` (licensed Apple TTML) -> exact-contract `data.json` + `tts_manifest.json` + line-maps + audio-job list. Derives per-mora `kana_timings` from the licensed word timing (pyopenjtalk + jaconv). |
| `assemble_page.py` | Clones the production page onto the song: splices `LINE_TR`/`LINE_EXPLAIN`, retargets `SONG`/`YT_ID`/og/canonical/title/chip, swaps the living-gradient palette to the album cover's colours (and prints the matching landing `cardAccent`), adds the per-song `_redirects`. |
| `gen_audio.py` | One local engine for every clip: Kokoro `am_michael` (EN) + `jf_alpha` (JP) -> loudnorm -16 -> mono 80k mp3 (JP) / 128k (EN). Reads the audio-job list. English jobs with any CJK are hard-refused. |
| `landing_card.py` | Adds the song's Norelco card. Preview mode writes a self-contained `songs/<slug>/landing.html` with the card (live root untouched); `--promote` inserts it into the real root. |

## The sequence (what the dashboard shows)

grab_song -> lyrics -> whisper_sync -> author_data -> assemble ->
en_audio -> jp_audio -> pronunciation -> pitch -> drill_concat ->
[podcast -> podcast_align] -> validate -> landing_card -> deploy -> promote

Sixteen steps, fourteen of them on the road to shipping. `podcast` and
`podcast_align` are `optional=True`: the `--auto` walk steps OVER them and they
are left out of every done/total count, so a finished song reads finished. Run
one deliberately with `run <key> podcast`. (2026-07-28: `furigana` and
`compress` were deleted outright — both were honest no-op runners for work that
happens inside `content_to_data` / `gen_audio`, so they were two rows of
ceremony in a list you have to scroll.)

(As of Round 8 `assemble` precedes the audio steps: `content_to_data` inside
assemble emits the `audio_jobs.json` that `gen_audio.py` consumes.)

Ownership is honest: `lyrics` and `podcast` are marked as owned outside the box
(licensed Apple lyrics need your signed-in storefront; the podcast is research +
TTS you may want a specific model for). `author_data` is `cli` — the teaching is
AI-drafted, you review. Everything else the orchestrator runs.

## Server mode — Denmoku.app

The dashboard also runs as a localhost app: `builder/server.py` (stdlib-only,
`http://127.0.0.1:8773/`, dynamic port fallback with the bound URL written to
`builder/.app-url`) wraps the SAME CLI — every button just runs `manaoke_build.py`
as a subprocess and reads the same `builds/*.build_state.json` files. Launch it by
double-clicking `~/Denmoku.app` (app-launcher; auto-opens the browser) or by
hand inside tmux: `tmux new-session -d -s denmoku 'cd ~/manaoke-site/tools/songcraft/builder && python3 server.py'`.
The static `file://` dashboard (`manaoke_build.py dash` → `builder/index.html`)
keeps working unchanged — server mode is additive. One pipeline job runs at a
time: extra Run requests queue FIFO behind the active job (logs stream to the
gitignored `builder/joblogs/`).

## Run a song

```bash
cd ~/manaoke-site/tools/songcraft
# 1. identity
python3 manaoke_build.py init <key> --title-jp .. --title-en .. --artist .. \
    --artist-en .. --yt <id> --art <400x400 itunes url> --apple <music.apple url> \
    --slug <key>-<rand> --template inochi-mijikashi-e03jz0
# 2. lyrics — AUTOMATIC as of 2026-07-06 (the `lyrics` step is auto=True now).
#    fetch_timed_lyrics.py tries Apple word-level TTML (only when a token is
#    configured — tools/songcraft/.apple-lyrics.json or ~/.lyricool-config.json,
#    refresh procedure in its docstring), then NetEase word-level YRC (keyless),
#    then LRCLIB line-level (keyless; whisper_sync --words upgrades it).
#    All sources are vendored in lyric_sources/ — NO sibling repos, no worktree
#    ritual. `run <key> --auto` does this for you; manual invocation:
python3 fetch_timed_lyrics.py <key>          # --source apple|netease|lrclib, --force
#    (legacy paths ../fetch_lyrics.py and lrclib_to_lyrics.py still exist but
#    are superseded)
#        writes builds/<key>.lyrics.json directly.
# 3. teaching: author builds/<key>.content.json (flat top-level words[] with a section ref)
# 4. the rest, automatically:
python3 manaoke_build.py run <key> assemble
/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python gen_audio.py <key> <asset_folder>
python3 ../validate_song.py ../../songs/<slug>          # gate: 0 errors
python3 ../validate_tts_safety.py ../../songs/<slug>/tts_manifest.json
python3 manaoke_build.py run <key> landing_card         # preview landing with the Norelco
python3 ../bump_asset_versions.py
# deploy: commit + push (Cloudflare serves /songs/<slug>/ in ~60s). Hand over the bare URL.
python3 manaoke_build.py dash                            # refresh the denmoku
```

## Audio env (conda `parler`)

Kokoro 0.9.4 + `pyopenjtalk`, `mojimoji`, `jaconv`, `pykakasi`. The JP voice here
is the **preview** voice; the production Irodori clone swaps in by re-rendering to
the same filenames (no code change).

## Lyric sources (the `lyrics` step is source-toggleable)

Two timed-lyric backends, same `builds/<key>.lyrics.json` output shape, so the
rest of the builder is source-agnostic (the toggle the memo asked for):
- **Apple Music TTML** via LyriCool (`tools/fetch_lyrics.py`) — syllable timing,
  but storefront-bound: a US-account token can't fetch a JP-store-only catalog.
- **LRCLIB** via `lrclib_to_lyrics.py` — open synced-lyrics DB, no auth, no
  storefront lock. Line-level timing (content_to_data distributes kana per mora).
  This is how Odoriko (Vaundy, JP-store-only on Apple) got built.

Never scrape HTML lyric sites or reproduce lyrics from memory — use one of these
structured timed-lyric sources (both are what LyriCool is designed to consume).

## Honest edges (audited against the owner's voice-memo vision, 2026-07)

**Met:** chevrons per step (expand for blurb + command), status dots
(done/blocked/pending/running, color-coded), ownership color-coding
(green=local / purple=cli / gold=external, with a legend), copy-command
buttons on every runnable step, album-artwork lookup (`grab_song` — when `--art`
is omitted, `init` queries the public iTunes Search API and derives the
400x400bb cover URL from the best song match, JP storefront first then US), a
toggleable timed-lyric source (Apple TTML vs LRCLIB, same output shape),
forced-alignment sync to the real YouTube vocal (`whisper_sync`, now line- AND
word-level — see Round 5), sentence study cards (`author_data` +
`content_to_data` + `assemble`), an explicit TTS-vs-human-clip decision per word
(particles never go through TTS — Round 5 hardens this further), the podcast,
audio compression, Cloudflare deploy, and a promote gate that only fires on
the owner's word. Two of the memo's three modes exist as real CLI subcommands: **fully
automated** (`run <key> --auto` walks the not-done steps and stops at the first
gate — every auto step has a real runner, the walk REFUSES to silently skip an
auto step with no runner, it steps over `optional` ones, and `deploy` is a
manual gate so the commit+push happens once, on your word) and **manual step-by-step**
(`run <key> <step>`, `set ... --owner --done`).

**Remaining gaps (by design, not yet closed):**
- **Type-a-name-and-hit-Enter kickoff FROM the HTML itself.** The dashboard's
  input box filters already-`init`'d songs; it can't shell out to `init` a new
  one — a `file://` page has no process-spawn capability, and giving it one
  means a local server, which the standing Manaoke rule forbids. Starting a
  song still means typing the `init` command in a terminal.
- **"Sends prompts to the Claude CLI" isn't literal automation.** For
  `cli`-owned steps (`author_data`, `podcast`) the fully-automated mode prints
  the exact prompt text and stops — it doesn't itself invoke a `claude`
  process. You still paste it in yourself.
- **Linking steps as you take ownership doesn't rewire the DAG.** The
  dashboard's owner buttons (local/cli/external) only relabel a step in the
  browser's `localStorage` scratch state and print the `set` command to make
  it stick — flipping a step to "I own this now" in the browser doesn't
  change what `--auto` walks next time; that still comes from the STEPS list
  baked into `manaoke_build.py`.
- **No live progress without re-rendering.** *(Closed in Round 8; reworked in
  Round 12.)* `do_run` marks each step `running` (and rewrites the dashboard)
  before its runner starts, and again after it finishes; the dashboard's JS
  arms a 4s `location.reload()` ticker ONLY while some step is `running`
  (`ANY_RUNNING`, baked in at render time). Idle = completely static (no reload
  loop); the ↻ button in the masthead is the manual refresh. Open chevrons +
  scroll position survive each reload via sessionStorage. No server (the
  no-local-servers rule holds).
  `builder/RunSong.command` is a double-clickable wrapper that prompts for the
  song key, opens the dashboard, and runs the `--auto` walk in a terminal.
- **Timing** comes from the licensed/aligned word timing. `whisper_sync`
  tightens line starts against the real YouTube vocal, and (Round 5) can now
  write real per-word onsets for LRCLIB sources too, closing the "mora-only
  reveal on line-level sources" gap Round 4 flagged.

## Round 2 additions (2026-07, the owner's feedback pass)
- **Faithful palette** — `cover_palette` now median-cut-samples several DISTINCT dominant colors from the art (Apple-Music-style mesh), saturation-faithful (B&W covers stay dark), and retargets the html/body/base-radial tones too (were inochi's magenta). Drives page gradient + mid-dark Norelco accent.
- **whisper_sync.py** — *(historical: this Round 2 voiced-onset / grid-solve-scale+offset method was superseded by CTC forced alignment in Round 3 — see the Round 3 bullet for the current approach.)*
- **Study-sheet scroll/anchor** — injected `STUDY_SHEET_PATCH`: word list scrolls, Word-by-Word pill + transport toolbar anchored over a scrim, active drill word auto-scrolls into view.
- **Podcast** — `generate_podcast.py` (Google Chirp3, HOST=Charon + JP=Kore, single-narrator + JP-reader inochi pattern) → hosted in-repo at `_assets/<song>/audio/<key>_podcast.mp3`, wired via relative `PODCAST_URL`, force-aligned (`align_podcast.py`) for word highlighting. Persist the aligned script back into content.json.
- **Kits** — `anki_kit/build_song_kit.py` has per-song configs; outputs hosted in `_assets/<song>/kit/`.
- **verify_jp_pronunciation.py** — whisper read-back of every JP study clip vs its expected reading (phonetic compare), `--fix` swaps mispronounced words for EXACT (kanji,kana) human recordings from the vocal dictionary (`human_audio/`). Single-mora whisper-noise left as TTS.
- **JP voice** — `gen_audio.py` takes a `jp_voice` (default jf_alpha); the read-back gate makes non-default voices safe (the male jm_kumo mispronounces 踊り子/指切り, caught by read-back).
- **assemble now auto-chains drill_concat + bump** so they can't be forgotten after a re-assemble.

## Round 3 additions (2026-07, the owner's parity pass) — CROSS-REFERENCE THE REFERENCE

The rule now enforced in code: **a song page is inochi ("creep hype") with swapped
data + palette + paths, nothing else.** Round 2 patched the clones (a position:fixed
translation pill fought inochi's native inline one — that was the "toggling
translations throws off the flow" bug); Round 3 rips patches out and guarantees
parity.

- **assemble_page.py injects NO structural CSS/JS.** Every behavior the reference
  nails (translation-toggle flow, word-by-word scroll/anchor) is inherited. If a
  study behavior needs improving, fix it in the TEMPLATE (bump inochi) so all songs
  including inochi stay identical.
- **parity_audit.py** runs inside `assemble` and FAILS the build on any structural
  drift from the template (only data / palette / kit+audio paths / content hashes
  may differ). This is what keeps Silhouette (and every future song) from silently
  diverging. Run standalone: `python3 parity_audit.py <slug> <template_dir>`.
- **whisper_sync.py is now FORCED ALIGNMENT, not onset/transcription guessing.**
  Demucs isolates the vocal stem; a CTC aligner (the podcast aligner's MMS model)
  places each KNOWN lyric word on its sung onset. Robust to: a long instrumental
  intro (踊り子 was 12s early — Round 2's onset fit had latched onto the
  hallucinated composer-credit "vocals"), and breathy ad-libs (死ぬのがいいわ opens
  with "はぁ" moans — not the first line). A tail-clamp pulls any line the aligner
  parks over the instrumental outro back into real vocal energy. Deps (parler env):
  `demucs`, `ctc-forced-aligner`, `fugashi`, `pykakasi`, `soundfile`, `torch`.
  Result: odoriko line0 6040->18620ms, shinunoga 24365->24860ms, every line on its
  real sung onset.
- **cover_palette** gates hue candidates by saturation relative to the signature,
  so a desaturated photo backdrop (odoriko's ~40% pale blue-grey) can't become the
  gradient highlight — the mesh stays in the cover's real warm family (Apple-style);
  a true B&W cover still collapses to charcoal grey.

## Round 4 additions (2026-07, the owner's "make it general, not per-song" pass)

The rule the owner held us to: a quirk gets fixed ONCE in the template (so every song
+ Silhouette inherit it), never patched per song, and it's proven by re-cloning
and letting `parity_audit` confirm the clone still matches.

- **Long-line study card (template fix).** inochi's own lines top out at 6 study
  words, so its native study back face was never stress-tested; Odoriko/Shinunoga
  have 10-word lines whose word grid overflowed and collided with the fixed
  transport row + the Word-by-Word pill (measured 29px overlap). The template now
  makes a real study card (`#cardSheet .card-face.back:has(.study-wrap)`) a flex
  column that reserves the fixed-transport band and scrolls ONLY `.study-wrap`;
  the Word-by-Word + Explainer pills anchor as a footer just above the transport.
  Scoped with `:has(.study-wrap)` so the intro/how-to card keeps native centering
  and the sing-mode inline translation pill is untouched (that was the Round 2
  regression). Verify method (headless, no deploy needed): load the LIVE page,
  `page.addStyleTag` a candidate CSS, make the target line's card `.is-active`,
  call `openCards()`, screenshot + dump rects of `.card-translation` vs the fixed
  `.card-actions` (overlap must be 0). Once good, bake into the template and
  RE-CLONE the songs (don't hand-edit their CSS — that's the per-song patching we
  removed). Driver + candidate CSS were in scratchpad/overflow/probe.js.
- **`pronunciation` step wired into the DAG** (after `compress`, before
  `assemble`): `verify_jp_pronunciation.py <slug> <folder> --fix` runs whisper
  read-back on the FINAL served JP clips and swaps a mispronounced word for an
  EXACT vocal-dict recording. It's a real orchestrator runner now (auto), so TTS
  QA "happens as we go" instead of being a forgotten side tool. NOTE its base
  Whisper model over-flags single-mora words + close compounds (子/あぁ/指切り/
  げんまん read as artifacts); `--fix` only acts on exact `{kanji}__{kana}` dict
  matches so those noise flags can't cause a bad swap. Sanity-check flags against
  a stronger model or by ear before trusting them.
- **Timing** stays FORCED ALIGNMENT at the line level (odoriko 18620ms, shinunoga
  24860ms), preserved across the re-clone because it lives in
  `builds/<key>.lyrics.json`. Honest edge still open: for a line-level source
  (LRCLIB) the WITHIN-line word reveals are mora-distributed, not per-word
  forced onsets — the aligner computes per-word onsets but the tool currently
  uses only line begin/end. Fine-tune by ear with `?sync=<ms>` + the
  timestamp-recorder; a future upgrade is to write the aligner's per-word onsets
  back into the words[]. **Resolved in Round 5** — `whisper_sync.py --words`.

## Round 4b additions (2026-07, study-card polish pass — all in the TEMPLATE)

Follow-on to the long-line fix; same faithful-clone discipline (fix template →
reclone → parity). All verified headless at 390x844.
- **Word-by-Word pill sits directly above the transport.** `#sheetBody` already
  reserves the fixed-transport band, so the long-line fix's face `padding-bottom`
  double-reserved it (pill floated ~112px up). Now `padding-bottom:6px` → pill
  lands ~38px above the transport row.
- **Drill auto-scrolls the lit word into view.** `scrollDrillRowIntoView()` (called
  from BOTH drill paths' row-highlight) does a minimal "nearest" scroll of
  `.study-wrap` — moves only when the active row hits an edge, just enough to bring
  it fully inside with top clearance. This reveals even the LAST word of a long
  line (odoriko 止まり) above the pill; centering-with-clamp did NOT (once the pill
  padding shrank the scroll range). GOTCHA when verifying via headless real-drill:
  the scroll is `behavior:smooth` (~300ms), so measure AFTER a settle-wait or you
  catch it mid-scroll and think it's hidden.
- **Pill wears the Play-key look:** graphite den-key gradient (slightly translucent)
  + white glyph; solid `#EDECE6` fill + dark text when `.playing`. No wine band.
- **Top-only fade** on `.study-wrap` (`mask-image` top 30px) matching the lyric
  list — bottom fade was dropped because it dimmed the active word near the pill.
- **English words in lyrics — proper spacing (the real "not clean on songs other
  than creep hype").** 死ぬのがいいわ has English lyric lines; `normalizeApple` built
  the JP display tokens straight from Apple's per-word array (already space-split;
  English further split into syllables Sunday→Sun|day) and never used the line
  text's real spaces, so a pure-English line rendered run-together
  ("Itdoesn'tmatterifit'sSunday"). Creep hype + odoriko have no English lyrics so
  it never showed there. Fix = `tokensFromWords(text, words)`: rebuild display
  tokens from the timed words but re-insert a whitespace token wherever `text` has
  a space AND English is on either side (`/[A-Za-z]/`) — Japanese phrasing spaces
  stay dropped so JP lines look identical. Whitespace tokens are tagged `.tok-ws`
  and given an explicit `width:.28em` (a lone space collapses inside the inline-
  block `.tok-glow`). Pure-English lines already suppress the redundant self-
  translation via `.is-pure-en`. Verified live on 死ぬのがいいわ: "It doesn't matter
  if it's Sunday" spaced, "いただき Monday" spaced, "鏡よ鏡よこの世で1番" untouched.
- **Romaji/EN 2-line wrapping on long lines: left as-is.** Creep hype ALSO wraps
  its longer romaji/EN — so wrapping is not the "not clean" driver (the English
  run-together was). Forcing one-line romaji shrinks it to ~9px or defeats
  `fitCardText`; uniform shrink contradicts the owner's "romaji ~15px". Not worth it.

## Round 5 additions (2026-07, word-level timing + universal palette + particle hardening)

- **`whisper_sync.py --words`** — additive, universal word-level timing. The
  same CTC forced-alignment pass that already pins each line's onset now also
  writes per-word onsets into that line's `words[]`, Apple-TTML-shaped. This
  gives an LRCLIB (line-level) song a true word-level kanji reveal identical
  to an Apple TTML song, instead of mora-distributing across the line. Apple
  songs already carry real `words[]` and don't need this flag; it's purely
  additive and never overwrites word timing that's already forced-aligned.
  Run: `python whisper_sync.py <key> --yt <id> --words --apply`, then
  re-run `content_to_data` + `assemble`.
- **Palette is now fully universal — zero baked literals in the template.**
  `cover_palette` in `assemble_page.py` derives every themed color from the
  artwork: `--field-c1/2/3/hi` (gradient), `--field-fb1/2/3` (the drifting-bloom
  hero accents), `--field-base1/2/3` (the base radial), and `--body-g1..4`
  (the body gradient) — Round 2/3 only had the gradient + Norelco accent
  deriving from the cover; the bloom/base/body tones were still inochi's
  baked magenta. `tools/songcraft/verify_palette.py` is the reusable QA: it
  reproduces inochi's own cover through the derivation and diffs every value
  against the template's approved baked reference (regression), and prints
  the derived palette for any other song so a human can eyeball the field
  will read like the art (preview).
- **Pronunciation QA now covers particles, with corroboration.**
  `verify_jp_pronunciation.py` used to skip particles as too noisy for
  Whisper on sub-second clips; it now runs two independent read-back attempts
  and only flags/swaps a particle when BOTH agree the sound is wrong (a
  single disagreeing attempt is treated as Whisper noise, not a real
  mispronunciation). `gen_audio.py` also routes every lone particle
  (にとものがでやかねよなへをはぞさお) around Kokoro entirely at generation
  time — Kokoro mangles isolated particles — pulling a curated human clip
  from `tools/human_audio/library/` or failing loudly with the exact Qwen3
  phrase-cut command instead of shipping a bad TTS clip silently.
  `validate_song.py` gained **E8**: a particle word's `jp_speak` must be its
  spoken form (は→わ, へ→え, を→お), not the written character — catches the
  raw-particle-to-TTS class of bug at data-authoring time, before audio ever
  renders.

## Pitch-accent data (now a turnkey step)
`gen_pitch.py <slug> <folder>` (auto step, qwentts env) bakes the pitch-accent
contour per word into `_assets/<folder>/pitch_data/pitch.json`, keyed by
`jp_speak||jp` (both surface + kana). The engine (pyopenjtalk-plus + kanjium +
marine cross-check) is **vendored** at `tools/songcraft/pitch_pipeline/` — no
external project dependency. It's INCREMENTAL (only new words compute; the heavy
import is skipped when the word set is unchanged) and runs in the rebuild chain
`assemble → pitch → validate`, so a re-segmentation regenerates contours in
place. Latin surfaces (English loanwords) are looked up by their KANA or
pyopenjtalk spells them out letter by letter. A word still missing a contour
falls back to a plain mora row (graceful, never a break). `pitch.json` is served
`no-cache` (no `?v=` hash) so a regen propagates.

## Remaining for promotion (both songs)
- **Per-song font subset** (fonts/README.md) — currently share inochi's `/fonts/inochi-mijikashi/` via per-glyph fallback.
- Optional: scene images (dual-coding).

## Round 6 additions (2026-07-03 — the rules stop leaking)

- **Continuous word spans.** `whisper_sync.py --words` now emits Apple-shaped CONTINUOUS spans (word end = next word's begin; last = line end) instead of raw CTC spikes. The karaoke wipe was tuned to that contract; spike gaps froze the fill mid-line then snapped it (the "all light up at once / jumps back" bug). Onsets stay exactly as aligned.
- **Countdown starts with the music.** `whisper_sync.py` measures `music_start_ms` (full-mix RMS onset) into lyrics.json → data.json → the intro card's `data-music-t`. The count-in stays blank through a silent MV pre-roll and begins at music onset. Songs without the field count from t=0 as before.
- **AUDIO_V cache-busting.** Per-word clips + drill concats + the podcast are served immutable under stable names — replacing content in place NEVER reaches devices (or even the CF edge). The template appends `?v=AUDIO_V` at every playback choke point; `assemble_page.audio_version()` splices a sha8 of the song's audio bytes. `build_drill_concat.py` adds per-file `?v=` in drill_map. Changed audio now mints fresh URLs automatically.
- **Particle provenance (E9).** Lone-particle clips must come from a REAL voice (curated human library, else Qwen3 carrier-cut) — never Kokoro. Whisper read-back is NOT the gate (a Kokoro が transcribes fine and still sounds terrible); `builds/<key>.clip_provenance.json` is. `validate_song` E9 fails any lone-particle clip without non-kokoro provenance. All three shipped songs' particles regenerated from real voices.
- **Podcast language safety.** The EN host never speaks Japanese — enforced in `validate_tts_safety.py` (`check_podcast_language_safety`): no CJK and no standalone romanized particles/ritual words in HOST lines. Particles cited in a podcast use the human clip via the `{"clip": path}` entry marker (`generate_podcast.py` splices + loudnorms it; `align_podcast.py` preserves it).
- **_redirects cap policy.** CF honors ~100 rules; we were AT the cap (the tail catch-all was one rule away from dying = "no sound"). Policy now in the file: promoted version + 2 rollbacks + active previews only.

## Round 7 additions (2026-07-03 — audit: TTS keeps sneaking back in)

- **TOTAL AUDIO_V coverage + lint (the word-card に bug).** Round 6 versioned
  playAudio/_drillClip/prewarm/podcast — but the WORD/PITCH CARD players
  (`_audioUrlFor`, `_armPitchAudio`, the card en/ctx/definition-chain
  `new Audio(...)` sites and `playUrl`'s audioCache path) still built bare
  URLs, so devices kept replaying the year-cached Kokoro clips even after the
  repo bytes went curated-human. Every audio playback site now routes through
  `_withAudioV` (which also skips any scheme URI: data:/blob:/http). Enforced
  forever by `lint_template.py` (R1 sentinel, R2 every `new Audio`, R3 `.src=`
  audio assignments, R4 `_audioUrlFor` returns), run on template + built page
  inside `manaoke_build run <key> assemble`. Lesson: "replaced the clip" means
  NOTHING until the URL the PLAYER builds changes — audit players, not files.
- **Row-relative romaji wipe (two-line reveal glitch).** The romaji fill
  cursor was `romLeft + romWidth * progress` — one global strip valid only
  when the romaji sits on ONE visual row. A wrapped (two-row) pronunciation
  line popped row 2 in half-revealed (and snapped row 1's tail) the moment
  the row band moved. Fix: anchor the cursor to the CURRENT part's own box
  (`rp.left + rp.width * warp(f)`), exactly like the JP-text wipe anchors to
  `tk.left/tk.width`. Any line long enough to wrap now wipes each row from
  its own left edge. (inochi wraps on its longest lines too — the fix is
  universal, not per-song.)
- **Dictionary-priority short words (E12).** Every JP study-word render now
  tries the human voice dictionaries FIRST (library/ cache → JPod101 fetch.py
  → offline 6,355-word Tofugu corpus, keyed surface+kana via
  `builds/<key>.word_meta.json` from content_to_data). Hit → `curated`
  provenance. Miss on a ≤2-mora word → Kokoro is allowed ONLY with a
  large-v3 read-back that must hear the word's kana (the small model passed
  いい→"いえ", どっか→"どうか", こと→"こど"); a failed read-back deletes the
  clip and fails the build with the Qwen phrase-cut remediation. Provenance
  `kokoro_dictmiss` proves the lookup ran; `validate_song` **E12** rejects
  plain `kokoro` for any ≤2-mora word. `phrase_cut.py` gained consecutive-
  token window matching (start-tight, raw + folded-hira) so conjugated forms
  (乗って/散って/蹴って) can be carrier-cut; AivisSpeech Aida is the backstop
  when Qwen can't say a word (蹴って), provenance `aivis`.
- **EN voice NEVER speaks Japanese (E11).** `jp_token_detect.py` finds
  romanized-JP tokens (song-vocab-anchored, COMMON_EN-guarded, longest-match
  incl. bigrams like "ano ko") in every EN clip text; `gen_audio` splits the
  text and SPLICES the song's own JP word clips between Kokoro EN segments
  (recorded in `builds/<key>.en_splice.json`). Podcast HOST lines run the
  same detector (the old gate only knew a fixed particle set — that's how
  "dokka" slipped through); JP citations in podcasts are `{"clip": path}`
  entries. `validate_tts_safety` **E11** fails any EN text whose detected JP
  token is not in the splice manifest.
- **Citation forms count too.** The podcast/host cites DICTIONARY forms the
  vocab doesn't contain ("hajikeru" while the song sings はじけて; "tara" as
  a grammar suffix). `jp_token_detect` therefore matches four ways — exact
  vocab rom, stem (≥4-char shared prefix), suffix (of a ≥6-char rom), and a
  podcast-only romaji-phonotactic shape net — with COMMON_EN plus a
  NAME_ALLOW list (Fujii, Kaze, Kurosawa, Manaoke…) so proper nouns stay
  host-spoken. Tokens with no song clip get `render_citation_clip`: the
  dictionary chain first, Kokoro only >2 morae or passing the large-v3
  read-back (the gate correctly REJECTED Kokoro's ちる, which read back 汁).

## Round 8 additions (2026-07-03 — the pipeline stops lying about itself)

An audit found the orchestrator's advertised commands and its actual behavior had
drifted apart. This round closes the gaps; nothing here re-triggers the two built
songs (their step statuses are byte-identical before/after — only cmd/blurb/auto
text and step ORDER changed).

- **Phantom commands removed.** The dashboard chevrons showed pastes that would
  fail: `jp_audio` cited `gen_jp_audio.py` and `en_audio` cited `gen_en_audio.py`
  (silhouette-only — SONG-CONTRACT §3.1 warns against it), when the real runner
  `run_audio` calls **`gen_audio.py <key> <folder>`** for BOTH in one pass; and
  `furigana` pointed at `add_furigana.py` when kana timing is derived inside
  `content_to_data.py`. All three `cmd`/blurb fields now mirror what actually runs.
- **Both lyric sources surfaced.** The `lyrics` step (and the quickstart above)
  now show BOTH invocations — Apple TTML (`fetch_lyrics.py`) and LRCLIB
  (`lrclib_to_lyrics.py`), the non-Apple source Odoriko was built from.
- **Real runners for every auto step (`--auto` no longer silently skips).**
  `furigana`/`compress` got honest no-op runners (they're subsumed by
  content_to_data / gen_audio) — SUPERSEDED 2026-07-28: both steps were deleted,
  see "Adding a song ends with work to do" above; `whisper_sync` (heavy: demucs + CTC, prints a
  warning then really runs `--words --apply`), `podcast_align`
  (`align_podcast.py`), and `kits` (`build_song_kit.py --song <key>`) got real
  runners. `do_run` now HALTS loudly if it meets an `auto=True` step with no
  runner, instead of printing the command and marching on.
- **DAG order fixed.** `assemble` was listed AFTER the audio steps, but
  `gen_audio.py` reads the `audio_jobs.json` that `content_to_data` (inside
  assemble) emits — a fresh `--auto` walk would hit audio with no jobs file.
  Order is now assemble → en_audio/jp_audio/compress/pronunciation → drill_concat.
  `run_assemble`'s drill-concat auto-chain is gated on the song's audio dir
  already holding clips: a first-pass assemble on a fresh song skips it (prints a
  note; it runs on the re-assemble after audio), a re-assemble on a built song
  chains exactly as before.
- **`deploy` is a manual gate now** (`auto=False`) — deploying is a commit+push,
  and the standing rule is to push once at the end, never incrementally.
- **Album-artwork lookup (`grab_song`).** When `init` is called without `--art`,
  it queries the public iTunes Search API (term = artist + title, JP storefront
  then US, entity=song) and derives the `400x400bb.jpg` cover URL from the best
  match's `artworkUrl100`. `--art` still overrides. (Vendored from
  `~/lyricool/itunes.py`.)
- **Live dashboard.** `do_run` flips each step to `running` before its runner
  starts and rewrites the dashboard before + after every step; the dashboard JS
  reloads every 4s ONLY while a step is `running` (embedded `ANY_RUNNING`), so
  an open `file://` dashboard shows the dots flip during an `--auto` walk and
  sits completely still when idle (↻ = manual refresh; no server).
  `builder/RunSong.command` is a double-clickable wrapper: prompt for the key
  (init args if new), open the dashboard, run the `--auto` walk.
- **Two validator gates.** `validate_song.py` E13 (timing sanity: `music_start_ms`
  present for pipeline songs — tolerant/warn on the template that predates it;
  first sung line ≥ `music_start_ms`; word onsets monotonic within a line) and
  E14 (kit integrity: `_assets/<folder>/kit/` holds a `.apkg` + `.pdf` each >50KB,
  and the page wires `uResAnki`/`uResPdf` at this song's own kit path).

## Round 10 additions (2026-07-04 — every song on the same page)

- **`rebuild <key> [--dry-run|--fresh-slug]` / `rebuild --all` / `rebuild --why
  <clip-or-sha8>`.** Pages are pure build artifacts of template⊕data, so a
  template fix propagates by re-assembly, not patching. The planner diffs the
  current world (template tree sha8, page-input sha8s, every clip, tool recipe
  sha8s) against `builds/<folder>.deps.json` and reports exactly what's stale;
  dispatch reuses the run_assemble chain + gates unchanged. `--why` answers
  "this clip changed — which songs/lines/concats contain it" via the deps
  manifests + the clip_provenance sha8 join. Deploy stays a manual gate.
- **Deps manifests (`builds/<folder>.deps.json`).** assemble_page and
  build_drill_concat emit them (DVC-lockfile pattern: content hashes only, no
  mtimes); `emit_deps.py` adopts an already-built song without re-rendering.
  Drill concat is now INCREMENTAL: unchanged lines (same input clip sha8s +
  recipe) skip ffmpeg entirely and replay recorded timing windows — a full
  re-assemble of an unchanged song re-encodes zero audio. run_assemble
  re-stamps `page.out_sha8` after the drill splice + bump so plans don't report
  phantom drift.
- **Pronunciation lexicon (`pronunciation_lexicon.json` +
  `PRONUNCIATION-POLICY.md`).** Words that may never ship from TTS, keyed by
  folded spoken kana with per-word allow-sets, carrier phrases, and incident
  history. Enforced in gen_audio at every synthesis decision point (incl. the
  skip-existing trap: a newly-listed word's kokoro clip is deleted and
  re-routed) and by validate_song E15. `lexicon add/list/check` on
  manaoke_build; the dashboard shows the list + composes the add command.
  read-back (whisper) stays the DETECTOR, the lexicon is the MEMORY —
  `verify_jp_pronunciation --add-to-lexicon` closes the loop, and `--fix` now
  updates provenance when it swaps bytes. phrase_cut gained `--strict`
  (exact-equality read-back; the superstring loophole shipped 大事なこと once).
- **Silhouette onboarded (4th song, first folder≠slug build):** key
  `silhouette`, assets `_assets/silhouette2/` (the live v023 page serves
  `_assets/silhouette/` — never write there), deploy `silhouette-49erne`.
  Forced alignment replaced the legacy `yt_offset_ms:750` hack — the true
  offset vs the YouTube vocal is ~+1354ms (demucs stem is silent until 23.8s;
  the legacy value put line 1 in pure silence). Folder-vs-slug fixes landed in
  build_drill_concat (folder resolution) and build_song_kit (uid-aware slugs).

## Round 11 additions (2026-07-04 — dials, EN lines, and the 5th song)

**Round-11 template = `songs/inochi-mijikashi-e03jz0`** (unpromoted preview;
on promote it becomes the next v0N and build_states repoint). All four songs'
build_states already point at it. Three template changes, all
behavior-preserving at defaults:

1. **Gradient dials.** Animation durations are CSS vars (`--fdur-drift/breath/
   a/b/c/drift-sheet`), translate/rotate spans scale by `--field-amp`, and
   `:root[data-field-motion="orbit|sway|pulse"]` swaps the mesh path (drift =
   attribute absent; fb blooms keep their own drift — never re-add scale to
   them). `parity_audit.normalize()` stubs the dials and EXCISES the
   `<html data-field-motion>` attr (the template tag carries none).
2. **Word-less lines can teach.** A line with a `LINE_EXPLAIN` entry gets
   `has-translation` on its Word-by-Word pill even with zero study words, and
   `speakTranslationPill` dispatches `playWordDrill([], el)` off its DRILL_MAP
   entry. Built for pure-EN lyric lines (shinunoga's six).
3. **speakJP language guard.** `speakJP` routes non-CJK text to `speakEN`
   (closes the "JA browser voice reads English" fallback; the reverse guard
   already existed).

**Gradient Lab.** `manaoke_build.py gradient set <key>|--all / show / clear`
writes `design.gradient` into content.json (or `builds/gradient.defaults.json`
globally): c1/c2/c3/hi/fb hexes, `speed` (divides the base durations),
`motion`, `amp`. Precedence: cover_palette → defaults → per-song. A c1
override re-drives the darken() chain AND cardAccent (printed + cardaccent.txt
— root landing stays a manual edit on promote). Pale guard rejects HSV V>0.68
(main) / >0.82 (hi) unless `--force-pale`; verify_palette audits overridden
songs. The eyedropper panel is `tools/songcraft/builder/gradient.html` (file://
page linked from the denmoku; composes the CLI command, never writes disk,
never emits --force-pale). Dials need a Round-11 page; colors work on any.
Speed/amp/motion changes on a pre-Round-11 page FAIL the assemble loudly.

**EN lyric lines (the shinunoga/headlong standard).** LINE_TR = the line
itself; author LINE_EXPLAIN for every EN line; give culturally-interesting EN
words 1-2 anchor study cards with katakana `jp_speak` (ヘッドロング precedent).
Tooling: `content_to_data` emits an `en-US`-keyed spoken-line clip for
explainer-bearing non-CJK lines (JP voice NEVER reads Latin text);
`build_drill_concat` builds word-less `[EN line + explainer]` concats
(`words:[]`, `tail:{jp:0.0, ex:…}`); `extract_drill` keeps zero-word cards
that have explainers; `line_explainers.py` has a pure-EN clause (explainer +
en-US line clip + en-US explain clip + DRILL_MAP audio).

**Lyric sources, source (c).** LRCLIB empty + Apple TTML unsynced happens
(headlong: `itunes:timing="None"`). Probe Apple FIRST with
`python3 tools/lyric_probe/probe_song.py <apple-song-id>` (the pre-public
LyriCool Apple client recovered from its git history — `lyricool.py json` no
longer exists; tokens still read ~/.lyricool-config.json). If text-only:
author lyrics.json from the TTML text (stanza structure preserved), then
whisper_sync needs BOTH passes — line-level first, then `--words --apply`
(the dashboard's documented single `--words` invocation anchors words into
placeholder line timings and ships them). Known whisper_sync gaps to check by
hand: no head-clamp (line 0 can park in a silent intro — headlong's 月 sat at
5.2s inside a 26.7s-silent intro; verify vocal-stem RMS at the claimed onset)
and repeated outro vocalises smear the last line's words. pp-youtube-pull is
research-only (transcript/comments/metadata — NO audio); alignment audio is
whisper_sync's own `/tmp/wsync_<id>.wav` cache.

**Kit builder gap.** `tools/anki_kit/build_song_kit.py` hardcodes a SONGS
table (song_dir/live_slug per song) — every fresh slug means editing it.
Future: read `builds/index.json` instead. Until then it's part of the
promote checklist.

**Segmentation canon — every card is a REAL DICTIONARY WORD.** A beginner can't
be assumed to know that two glued morphemes combine into a meaning, so a card must
be a thing they could look up. **jisho.org is the arbiter, not judgment about
"clusters"** — that judgment is what shipped 付けてほしい (under-split) AND のかな
(a non-word left whole because someone, me, decided "sentence clusters are fine";
jisho has no のかな, though かな alone IS a word). A card is fine iff:
- it's a single (possibly inflected) word — 汚れた, 気付いた, 生まれ変わったら
  (た/たら/ます ride the stem), OR
- a V-renyōkei+V **compound verb** — 弾け出す, 守り続ける (one lexeme, and NOT a
  jisho headword, so the morphology — not the dictionary — keeps it whole), OR
- an exact **jisho headword** — 思考回路, にも, ように, だろう, から, 三度, 木の葉,
  誰も彼も all ARE, so they stay whole.
Split anything else (jisho can't find it): 付けて│ほしい, タッチ│した, の│か│な,
なろう│か│な, シガレット│アメリカン. Every genuine particle is still its own card.

**The gate:** `validate_segmentation.py` (fugashi units + a **local JMdict headword
set**, parler env) is wired into `validate` (LOUD, non-fatal — a deferred-stray
song isn't blocked). The dictionary is `data/jmdict_headwords.txt.gz` (462k
headwords, ~1.9MB, bundled — JMdict is jisho.org's own source, so the verdict
equals jisho's, validated 60/60; see `data/NOTICE-jmdict.txt`). It's a hash-set
membership test — **fully offline, no API call**, deterministic. Honorific 接頭辞
(お/ご) attach forward and number+
counter coalesce, so お買い物 / 1番 don't false-flag. A real word a lookup can't
confirm offline goes on `KEEP_WHOLE_LEXICAL`. `backlog.py import-segmentation`
tracks every flagged card. When a romUid is taken by a retired chunk clip
(bridge も → `mo-naku` was the old chunk), name by neighbor word (`mo-shousou`).

## Doctor, promote-as-a-script, the stale-running reaper, auto font subsets (2026-07-07)

- **`manaoke_build.py doctor [--fast]`** — preflight before a walk: datasets
  (jmdict gz, openjtalk dict + extracted sentinel, kanjium, human library,
  tofugu ≥6000 mp3, yomichan corpora), env imports (parler's 11 heavy modules,
  qwentts pyopenjtalk + a 桜 pitch smoke, system PIL+genanki — all via
  subprocess, never in system python), model caches (ctc onnx ~1.2G, htdemucs,
  HF Kokoro-82M + whisper base/large-v3), binaries (yt-dlp/ffmpeg/git/wrangler/
  Chrome/fonttools), tokens (Apple lyrics, GOOGLE_TTS_KEY, keychain CF token —
  WARN-only), services (Denmoku `/api/state`, tailscale serve :8773 —
  WARN-only), corpus wav warmth + disk headroom. Aligned PASS/WARN/FAIL table;
  exit 1 on any FAIL; `--fast` skips the env-import sweeps. Every check is
  individually guarded — doctor never crashes.
- **`manaoke_build.py promote <key> [--dry-run]`** — promotion is a script now,
  not honor-system freehand. Current-era semantics: repoint the root landing's
  `SONGS[]` `url:` for this song to the build_state's current slug dir and
  refresh that entry's `cardAccent` from `builds/<key>.cardaccent.txt`.
  `--dry-run` prints the unified index.html diff and writes NOTHING; the real
  run prints the diff it applied, marks the promote step done, and journals a
  lessons entry. It REFUSES when the dir is missing/insane (no index.html or
  data.json), validate isn't done, or the song isn't on the landing yet
  (adding a card is `landing_card.py --promote`'s job). Deploy stays manual.
- **Stale-running reaper.** A SIGTERM/crash used to strand a step on 'running'
  forever. `do_run` now records the runner's pid + start time in the step
  state; any later invocation (state load, dash) flips a 'running' step whose
  pid is dead (or unrecorded) and older than 10 min to `failed` with note
  "reaped: runner died". A live pid is never touched, so parallel Denmoku runs
  are safe.
- **Auto font subsets (`gen_fonts.py`).** The SONG-CONTRACT §4.9 recipe owned
  in code: assemble now generates `fonts/<folder>/`'s five woff2 subsets when
  missing or glyph-stale for the song's text (charset = the in-flight page
  html + data.json, full-kana insurance unicodes, `--layout-features='*'`).
  Source TTFs are vendored at `data/fonts_src/` (OFL) so it's fully offline;
  it runs under PATH python3 (fontTools+brotli — a doctor binary check).
  Assemble also checks the LANDING DotGothic gotcha (a new title kanji missing
  from `fonts/DotGothic16.subset.woff2`) and prints the fix command
  (`gen_fonts.py --check-landing "<title>" --fix-landing` regenerates the
  landing subset as a coverage union + bumps the root `?v=` hashes).
- **Album-art cache.** `assemble_page.cover_palette` reads `builds/<key>.art.jpg`
  BEFORE the network and writes it on the first successful download — rebuilds
  no longer re-fetch the Apple CDN, and a palette fallback (no art at all) now
  prints an unmissable PALETTE FALLBACK warning block instead of silently
  keeping the template's colors.

## Rebuild wave (2026-07-07 — all six songs through the upgraded pipeline)

The finale of the builder-improvement round: every song rebuilt at a fresh
random slug through the full upgraded chain (assemble → gen_audio →
pronunciation → pitch → re-assemble → drill → kits → validate), all gates
E1–E18 = 0 + tts_safety 0 + segmentation clean on all six. What the wave
exercised and shipped:

- **Slugs:** ema-6rs1ij, silhouette2-o8mugf, odoriko-q4f3rn, shinunoga-b3qfut,
  headlong-u0o2p4, inochi-mijikashi-ry4rk0. Root landing untouched — promotion
  stays the owner's word (`manaoke_build.py promote <key> --dry-run` shows each diff;
  ema needs `landing_card.py --promote` first, it has no root card yet).
- **Segmentation splits shipped** (odoriko/silhouette2/inochi authored splits +
  headlong's pre-done ones): every new split card got real clips — JP via the
  curated/NHK corpus chain (`tools/human_audio/corpus.py`: nhk16 > shinmeikai8 >
  forvo > jpod), lone particles curated (E17), EN via Kokoro. 14 segmentation
  backlog items auto-closed; the remaining open ones are findings against the
  OLD still-deployed dirs and close when those prune after promotion.
- **セヨ precedent (dict-miss + Qwen-cut failure):** the コイセヨ title split's
  セヨ dict-missed, Kokoro read back 西洋, six strict Qwen carrier cuts failed
  isolation — rendered with AivisSpeech Aida (engine run inside tmux, Normal
  style), large-v3 read-back heard せよ twice (plain + prompted), installed with
  `aivis` provenance + lexicon-pinned. That's the full E12/E15 remediation
  ladder working as designed.
- **Read-back QA on rebuilt libraries** swapped 15 clips to human recordings
  across the six songs (e.g. inochi's two 明日 clips read back みょうにち; ema
  ここ hit shinmeikai8 via the new corpus resolver). The corpus chain found
  words the old library/JPod path missed.
- **Legacy debt cleared en route:** inochi's 2 pre-splice EN clips re-rendered
  WITH splicing (bf77fd10), the no-provenance いつか clip re-rendered through
  the lexicon-aware chain (E15), odoriko's 3 EN explainers that the new split
  vocab newly flagged re-rendered with splicing, silhouette2's podcast copied
  to its canonical post-rename name (`silhouette2_podcast.mp3`; old name kept
  for the live 1rjcj8 page), ema got its missing `pitch_data/timing.json`
  (phase-2 MMS alignment, 106/108 words — producer still unowned in-repo,
  backlog 44b77c75).
- **Kit-builder key alias:** `build_song_kit.py --song inochi-mijikashi` now
  aliases to the `inochi` table entry (the `run <key> kits` runner passes the
  builds key — it used to die on argparse choices). SONGS-table slug hardcode
  still stands (d50d7733): every fresh slug means editing the table.
- **Scorecard headlines** (informational): ema MedAE 58ms/PCO 90% (acoustic
  55%), silhouette2 acoustic 82%, odoriko PCO 94–97%, shinunoga word-PCO 74.6%
  (acoustic 87%), headlong word-PCO 93%/acoustic 100%, inochi word-PCO 93.75%
  (acoustic 90%). History in `builds/alignment_history.jsonl`.

## Acoustic clip physics — the いい/の cure (2026-07-07, E19)

The defect class read-back could never catch: a short take that is truncated
or carries the NEXT word's onset still transcribes as the target (the bad
いい read back as これでいい and passed containment; きっと carried a fresh
burst of the next word that whisper simply ignored). Duration + envelope
physics catch it. Backlog 0bd85bd1.

- **Detector:** `tools/human_audio/clip_physics.py` — voiced span vs weighted
  morae (long vowels / geminates / bare-vowel repeats must be SUSTAINED) +
  effective-end energy (a natural take decays; a cut ends hot). Verdicts
  pass / suspect / fail. Thresholds calibrated on all 583 library clips + the
  four recovered known-bad takes; `--selftest` re-proves 4/4 bad fail, 4/4
  good pass against the committed fixtures in
  `tools/human_audio/physics_fixtures/{bad,good}/`. Recalibrate before
  loosening anything — the fixtures are the regression truth.
- **Render gates:** gen_audio physics-checks EVERY ja render and dict copy
  (synthetic takes must be a clean 'pass'; curated fails refuse, curated
  marginals warn). Read-back for <=2-mora words is now EXACT equality —
  containment was the いい⊂これでいい hole. phrase_cut gained an energy tail
  trim (retreats the cut to the last real valley when the window caught the
  next word's onset), per-take physics, always-strict short-word compare, and
  verifies in TMP so a failed re-cut can't clobber a good clip.
  install_word runs physics between read-back and pin (hard fail rolls back;
  marginal installs loudly) and takes `--source curated|qwen|aivis` so
  provenance stays honest for carrier cuts.
- **Sweep:** `sweep_clip_physics.py <key>|--all` (parler env) measures every
  served JP clip (words, lines, podcast citations) and writes
  `builds/<folder>.clip_suspects.json` (incremental by sha8). The validate
  runner re-runs it before validate_song on every build.
- **E19 (validate_song):** a study-word clip with no FRESH sidecar entry
  (missing or sha8-stale) is an error — new clips can't ship unchecked.
  Verdict 'fail' without a `physics_waiver` on the clip's provenance entry
  is an error; with one it's a visible warning. 'suspect' clips warn and sit
  in the Denmoku words-tab ear strip (merged regardless of provenance —
  curated clips can be marginal too). Line/podcast fails warn only.
- **First sweep of the live library (2026-07-07):** 19 fails — every one a
  qwen carrier-cut except legacy OLさん (which large-v3 confirmed truncated:
  heard 'OLさ'). Read-back even proved contamination the physics flagged:
  ema なって heard こうなって, とこ heard いいとこ. Remediation ladder as
  played: 12 dictionary swaps (nhk16/shinmeikai8/forvo — the gates themselves
  rejected two bad candidates: 床【とこ】 read back ここ, and 読過 for どっか),
  1 physics-gated Qwen re-cut (なって), 4 AivisSpeech Aida (ちって/のって/
  消えぬ/OLさん), 2 NHK homophone installs with a documented read-back waiver
  (どっか — the owner's picker pick; prompted-exact + physics corroborated), and 1
  Google Cloud TTS render (とこ, ja-JP-Neural2-C — plain read-back exact,
  the only engine of four that could say it; 'google' is now an honest
  provenance source, E12-allowed, ear-strip-flagged like qwen/aivis).
  check_jp_gates.py (the broken 2026-06 acoustic prototype) is retired —
  clip_physics.py supersedes it.
- **Whisper-limitation waivers (evidence, not vibes):** whisper cannot
  transcribe some isolated short words (けって→決定 precedent; とこ→ここ/投稿,
  どっか→読歌 — prompt-RESISTANT wrong hearings mark genuinely-bad takes,
  prompt-CONFORMANT exact hearings + clean physics corroborate good ones).
  install_word grew `--waive-readback WHY` / `--waive-physics WHY` (recorded
  as readback_waiver/physics_waiver on the provenance entry; E19 warns
  instead of blocking on a physics waiver), plus an automatic narrow rule:
  a 5+-mora word whose plain hearing is ONE mora off passes iff a prompted
  retry matches exactly (recorded as readback: prompted). Read-back folds
  digits AND short Latin letter runs to kana readings on both sides
  ('OLさん' ≡ オーエルさん), mirrored in gen_audio + clip_physics.
- **A word fix means THE WORD (2026-07-07, the owner):** install_word now copies a
  verified take to every card with the same (surface, kana) in the song by
  default (`--only-this-card` opts out) — fixing どっか on the v1 card must
  not leave the ch card on the old clip. Same-word divergence that predates
  this is backlog e0bd4ea3 (consistency detector).

## Adding a song ends with work to do, not a checklist (2026-07-28)

the owner walked the tool and named the gap: "as soon as I have the song, where it
starts, the best copy of lyric timings, and artwork, the steps to refining
should be cleared so I can get to work. That is not the case." He was right. By
the end of the New song screen the box had already settled every question the
next steps ask — and then handed him eighteen rows and made him press run on a
two-second job it could have started itself.

What changed:

- **`/api/init` queues the work immediately.** Right after `init` succeeds the
  server enqueues `run <key> --auto`, which fetches the lyric sheet, draws the
  waveform and force-aligns the timing, then halts at `author_data` — the first
  thing that genuinely needs a person. It is an ordinary job: the bar shows it,
  Stop stops it.
- **Add song lands on Timing, not Build.** Timing is the room the work happens
  in. Before the sheet arrives it shows a live "getting this song ready" note
  that fills itself in (it re-checks every 3s); if nothing is running — a song
  `init`'d from the CLI, say — it offers a `get it ready` button instead.
- **`ensure_peaks()` builds the waveform as part of the pipeline.** Nothing used
  to write `builds/<key>.peaks.json`, so a fresh song opened the Timing tab to a
  blank strip and a 404 and the drag-a-word editor could not draw. It now runs
  twice: after `lyrics` (the mix lane, off the wav the New song screen's start
  probe already downloaded — so it is there in seconds) and again after
  `whisper_sync` (adding the Demucs vocals lane, the one you align against).
- **Measured end to end:** lines AND waveform 3.4s after Add song, zero clicks;
  the full alignment finished at ~160s with the walk halted at `author_data`.

Honesty fixes that came out of the same walk:

- A hand-off step the walk stops at is now `waiting` (hollow ring in the
  hand-off colour), not `blocked` (alarm red). Nothing went wrong; it's your
  turn. `_sync_steps` converts the ones already on disk.
- `_cheap_stale` returns `warn: False` + `not built yet` for a song that was
  never assembled, instead of flying a red `no manifest` chip at a song that is
  two minutes old. The dashboard paints a chip red only when `warn` is set.
- The Timing state ladder no longer offers "apply my changes to the page" when
  there is no page — it says what's actually missing — and "last saved" no
  longer keys off `lyrics.json` (the automatic fetch writes that, so a song
  nobody had touched claimed a save). Only the overrides sidecar and
  `content.json` count as human-work receipts.

## Start point, cover colors, and removing a song (2026-07-28)

Three things that used to be decided for you, or not decidable at all.

**Where the song starts is now a choice, not a measurement.** `whisper_sync`
still measures `music_start_ms` (first full-mix energy above 10% of peak), and
that stays the default — but "first sound" is not the same question as "where
should this song start", and only a person listening can tell the difference.
So:

- `start_probe.py <yt_id>` answers both questions BEFORE a build key exists —
  same `whisper_sync.music_start_ms` call (never a second opinion that drifts),
  plus 100ms waveform peaks, into `builds/_probe/<yt>.start.json`. The wav it
  pulls is `corpus/wsync_<yt>.wav`, the same file the sync step wants later, so
  probing at step 2 warms the cache instead of duplicating a download.
- Denmoku's **New song step 3** draws that waveform with the suggestion marked
  and the marker draggable, plus a *hear it* button (`/api/startclip/<yt>`).
- A hand-set point rides through `/api/init --music-start-ms` into build_state
  `meta.music_start_ms` + `meta.music_start_src: manual`, and
  `whisper_sync.manual_start_ms()` makes the sync step USE it instead of
  measuring (it prints what auto would have said, so the disagreement is on the
  record).
- For a song that already shipped: `manaoke_build.py start <key> <ms>` patches
  meta + `builds/<key>.lyrics.json` and tells you to rebuild — seconds, instead
  of re-running Demucs and a forced alignment to move one number.
  `--auto` drops back to measuring.

**Cover colors are pickable at step 2.** The auto palette was always
`cover_palette(art)`; now you can see it and overrule it before the song is
built. `GET /api/palette?art=` returns exactly what assemble would derive
(c1/c2/c3/hi, the fb blooms, base+body chains, and the landing card accent).
The cover itself is served through `GET /api/art?url=` — same-origin, because a
canvas painted from another origin is tainted and `getImageData` throws, which
would make the eyedropper silently do nothing. The cover is ALWAYS armed: one
click sets the background, no selecting a field first. Picks land in a new
precedence layer, `builds/<key>.design.json`, merged by
`assemble_page.load_gradient_design` between `gradient.defaults.json` and the
authored `content.json` — content.json doesn't exist until author_data, so an
early pick needed its own home. Nothing picked = nothing written = the build is
byte-identical to before this screen existed.

**Removing a song moves it, never deletes it.** `manaoke_build.py remove <key>`
moves `builds/<key>.*`, `songs/<slug>/` and `songs/_assets/<key>/` into
`builds/_trash/<key>-<timestamp>/`, mirroring the repo tree so undoing is a
plain `mv` back, and drops a `REMOVED.json` recording every path. It REFUSES
while the song is live in the root `SONGS[]` — pulling files out from under a
live card ships a 404 to real visitors — unless `--force`, which also strips the
card and saves it to `landing-entry.js`. In Denmoku it's a collapsed section at
the bottom of the Build tab, and you have to type the song's key.

## Fixing a song's name and links after it exists (2026-07-30)

The seven strings that say WHICH song this is — `title_jp`, `title_en`,
`artist`, `artist_en`, `yt`, `apple`, `art` — were settable only at `init`. A
typo, or the blank English title the New song box used to accept, could be
corrected only by hand-editing two JSON files; two consecutive mariigoorudo
walks did exactly that.

- `manaoke_build.py identity <key> [--title-en .. --artist-en .. ...]` writes
  **both** homes in one go: build_state `meta` (what `scaffold` reads) and
  `builds/<key>.content.json` (what `content_to_data` copies into data.json and
  what assemble's identity retarget reads). Writing one without the other is the
  failure this verb exists to prevent. An unpassed flag leaves that field alone.
- It REFUSES an empty `title_en`. Measured, not assumed: a blank English title
  ships data.json with `title_en: ""` (the song has no English name) and makes
  the `author_data` hand-off prompt call the song by its key, because the
  `<TITLE>` fill-in falls back to `st['key']`. A blank `artist_en` is the worse
  one — that string IS in the page markup, so the retarget leaves the TEMPLATE's
  band in the clone and only `parity_audit.py` notices.
- On an already-assembled song it reopens `reassemble`, `landing_card` and
  `validate`, so Denmoku's own "run everything it can" is the fix. The staleness
  chip would also light up (content.json is a recorded page input in
  `builds/<folder>.deps.json`), but that surface only offers a command to paste
  in a terminal.
- A changed cover URL moves `builds/<key>.art.jpg` aside to
  `<key>.art.stale.jpg`: assemble reads that cache BEFORE the network, so
  leaving it would keep the palette on the old cover.
- In Denmoku: **Name, artist and links**, a collapsed section on the Build tab
  above Remove. It opens itself when the English title is missing, and the song
  header patches itself on save (the poll only touches the counter and the bar,
  so a corrected title used to leave the old one on screen, which reads as "it
  didn't save"). API: `POST /api/identity`.

## Building the same song twice, as a user (2026-07-30)

CreepHyp rebuilt from scratch under its own key (`koiseyootome`) so the result
could be held against the page we already know works. Everything below is a
thing the walk hit, in the order it hit it. All of it is fixed in the code; this
is the record of WHY those lines read the way they do.

- **A video that cannot be embedded ends the song.** The page is a YouTube
  player with lyrics wrapped around it, so a video whose owner turned embedding
  off (or that is age-gated) produces a page that never plays a note. Both of
  YOASOBI 夜に駆ける's official uploads are exactly that, and the picker offered
  the blocked one as its best match. Nothing off-page can tell you: `yt-dlp`
  reports `playable_in_embed: true` for it and oEmbed answers 200 for every
  video alive. The only thing that knows is the player, so the picker now mounts
  its preview through the IFrame API, watches `onError`, marks the candidate
  "won't play in a page", moves to the next one that does, and holds **Add song**
  shut while the chosen video is a known refusal.
- **An artist already written in Latin letters got a blank English name.**
  `_artist_en_suggestion` romanized and then returned '' when the result equalled
  the input — which is every act named YOASOBI, Vaundy, KANA-BOON. Blank there
  leaves the TEMPLATE's English artist sitting in the clone. Latin in, same name
  out.
- **Placeholders that read as answers.** `artist (en)` sat there saying "Aimyon"
  on a YOASOBI song; the library's filter box said "find a song" (the same words
  as the catalog search) with "odoriko" in it. Placeholders describe the field.
- **`おわり` shipped as a lyric.** NetEase transcribers close a sheet with a bare
  "the end". `strip_end_marker` drops an exact final marker only — the mirror of
  the leading-credits strip that was already there.
- **A refetch could quietly hand back a worse sheet.** NetEase rate-limits
  bursts, so pressing "refetch lyrics" a minute after adding a song swapped a
  25-line word-level YRC for a 17-line line-level LRC with "la la la" filler,
  same button, no warning. Busy is not an answer: the fetch now refuses to
  replace a better sheet while the better source is throttled, keeps the
  replaced sheet as `<key>.lyrics.prev.json`, and reopens `whisper_sync` +
  `scaffold`, which had kept their green dots against a sheet that no longer
  existed.
- **The box could see staleness and not fix it.** The red STALE: TEMPLATE chip
  and the library's "N songs are behind the template" line had no button;
  run all (auto) walks a list where every step is already done and parks on
  deploy with the old page on disk. `POST /api/rebuild` + a "rebuild this page"
  button on the notice itself.
- **Automatic sectioning is nothing like the shipped page.** The scaffold cuts
  sections on timing gaps: 2 unnamed blobs where the reference has 7 named ones
  (Title, Verse 1, Verse 2, Chorus, Verse 3, Chorus 2, Outro) with subtitles and
  spoken intros. Cards were ported from `builds/inochi-mijikashi.content.json` by
  matching the Japanese, and the sections re-cut to the reference's shape by
  hand. **Anyone building a song this pipeline has no reference for still has to
  write those seven section headers themselves** — the scaffold will not do it.
- **Step names were written for whoever wrote the code.** "Sync to the real
  vocal (forced alignment)", "Build the study skeleton", "Gate: validators",
  and pills reading AUTO / LOCAL / HAND-OFF. Now: "Line the words up with the
  singing", "Cut the lines into word cards", "Check everything before it ships",
  "runs itself / the box", "needs you / claude".

## Writing the study text in the box (2026-07-30)

Sixteen steps on the road to shipping; fifteen of them run on this Mac with no
account and no network past fetching the song and its lyrics. One did not:
`author_data` printed a prompt to paste into a cloud model and waited. That was
the whole no-cloud story — not "a worse page without an AI subscription", **no
page at all**, because nothing in Denmoku could write a translation, a card
meaning, or a section intro. `gloss` appeared exactly once in the entire
dashboard, read-only, in the Timing panel.

So the box now has the other half:

- **`content_edit.py`** — the one sanctioned writer for
  `builds/<key>.content.json`, the same way `timing_edit.py` owns the lyric
  sheet. `todo` lists every empty box; `show` prints one row; `set` writes one
  field. Everything the write has to do besides changing a string lives here:
  - **Refuse now what a gate refuses later.** Japanese in a box the English
    voice reads (`en_speak`, `context`, `gloss`, a section's `speak_en`, a line
    explainer) is rejected at the keystroke with the offending characters
    quoted back, instead of failing `validate_tts_safety` an hour downstream. A
    card with no romaji is refused because its clip filename IS its romaji
    (`content_to_data` exits on it). A `jp_speak` with no Japanese is refused.
  - **Throw away the recording of the old words.** `gen_audio` skips any clip
    already on disk, and a card's clip is named `word_<section>_<romaji>_en.mp3`
    — not after its text. So editing `en_speak` and re-running the audio step
    silently kept the old recording forever. The write deletes the affected
    clip and its provenance row. A clip cut from a **real human voice is never
    deleted** — it is reported so you can re-cut it, because losing a curated
    take to a typo fix is the worse bug. (Line explainers are named after a
    hash of their own text, so a rewrite lands on a new filename by itself.)
  - **Reopen what baked the old words in.** assemble → deploy go back to
    pending with a note. Display-only edits skip the audio steps; anything
    spoken reopens them too.
- **The Writing tab** (server mode) — three groups: parts of the song, lines,
  word cards. Every box is labelled in plain English, boxes a voice reads are
  marked `spoken`, boxes the page can live without are marked `optional`, and
  what's still empty is marked and counted in the header. Type, click away, it
  saves. "show what's left" filters to just the gaps. When the count hits zero
  a button marks `author_data` done. The `author_data` step on the Build tab now
  links straight here, so the hand-off is a choice rather than the only door.

What this does NOT change: the teaching is still the hardest thing in the
build, and a person typing it is slower than a model drafting it. The BYOM seam
(`ai_provider.py`) still routes a draft through a cloud key or a local
OpenAI-compatible server. The point is that the box no longer *requires* either
one to finish a song.

## Backlog manager (`backlog.py`)

A stdlib-only issue backlog so known problems across songs don't get lost between
builds. GENERAL by design — it holds any issue type (validator findings,
hand-noted TODOs, "this clip sounds off") keyed by a stable id so imports never
duplicate. Store: `builds/backlog.json` (flat list of
`{id, created, song, section, type, severity, title, detail, suggest, status,
source, notes}`); `id` = short sha1 of `song|type|section|key` so re-importing
the same finding upserts the SAME row.

```bash
python3 tools/songcraft/backlog.py import-segmentation   # seed/refresh from validate_segmentation
python3 tools/songcraft/backlog.py list [--song S] [--status open] [--type T]
python3 tools/songcraft/backlog.py add --song S --type T --title "..." [--detail ..] [--severity low|med|high] [--section X]
python3 tools/songcraft/backlog.py resolve <id> [--status done|wontfix] [--note "..."]
python3 tools/songcraft/backlog.py view                  # regenerate builder/backlog.html
```

- **`import-segmentation`** shells out to `validate_segmentation.py --all --json`
  in the parler env and upserts each over-merged-card finding as a
  `type:segmentation` item (title `over-merged card: <jp> → <p1 │ p2>`, severity
  `med`). Idempotent: re-running adds zero duplicates. A finding that STOPS
  appearing (the song was fixed) auto-closes its open item to `done`
  ("resolved: no longer flagged"); a regressed finding reopens. Manual items and
  non-segmentation items are never auto-touched, and a human `wontfix` (a waved-
  through false positive) is respected on re-import.
- **`view`** writes `builder/backlog.html` — a self-contained read-only viewer
  (data embedded, no fetch; open/done/wontfix filter, grouped by song, house
  style matching the denmoku). Status changes are CLI-only, then re-`view`.
  Linked from the denmoku masthead.

## Lessons journal (`lessons.py`)

The feedback loop between the owner's manual interventions and the next round of
pipeline improvements. Every manual-edit path journals what it did to the
append-only `builds/lessons.jsonl` (committed — it IS the institutional
memory) and re-renders `LESSONS.md`, grouped by kind, newest first. **"Work on
improvements" / "make it better" starts by reading `LESSONS.md`** (plus
`backlog.py list --status open`): one entry is an anecdote, a recurring manual
fix is the thing the pipeline should learn to do itself.

Auto-journaled (each hook is try/except-wrapped — a journal failure never
breaks the host tool):

- `timing_edit.py set/adopt` → `kind:timing` (every nudge = a place the aligner was wrong)
- `tools/human_audio/install_word.py` → `kind:word` (every curated install = a clip TTS got wrong)
- `manaoke_build.py lexicon add` → `kind:lexicon` (site-wide pronunciation pins)
- `backlog.py resolve` → `kind:backlog` (how known issues actually got closed)

```bash
python3 tools/songcraft/lessons.py add --kind manual|timing|word|lexicon|backlog \
        --song S --summary "..." [--detail ..]   # hand entry; appends + re-renders
python3 tools/songcraft/lessons.py render        # regenerate LESSONS.md (idempotent)
python3 tools/songcraft/lessons.py list [--kind K]
```
