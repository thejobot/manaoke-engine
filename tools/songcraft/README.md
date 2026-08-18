# Songcraft — the new-song pipeline (proven end-to-end on Silhouette, 2026-06-11)

> **READ `SONG-CONTRACT.md` FIRST.** It is the authoritative "build a song
> **identical** to the current production reference (inochi-mijikashi-v0.91)"
> contract + pipeline: the exact design contract (study sheet / word-by-word /
> pitch card — selectors, labels, CSS values, audio cadence), the `data.json`
> authoring contract + the `en_speak`/`context` spoken-English style guide, the
> two things that live in `index.html` not `data.json` (`LINE_TR`/`LINE_EXPLAIN`)
> and the per-song `const YT_ID`, the audio pipeline + the `tts_manifest`
> text↔file contract, and the build/deploy/verify/promote mechanics — ending in
> explicit **Flow A (add a new song)** and **Flow B (update an existing song)**
> checklists. This README is the historical pipeline notes; SONG-CONTRACT.md is
> the procedure to follow.

Order of operations for adding a song (each script is the Silhouette instance —
per-song constants at the top are what you edit; everything else is convention):

1. LYRICS + TIMING — Apple Music syllable TTML via the pre-public-release
   LyriCool (git worktree: `cd ~/lyricool && git worktree add /tmp/lyricool-apple 0167a47^`).
   Tokens in ~/.lyricool-config.json (developer + media-user-token off a signed-in
   music.apple.com session via window.MusicKit.getInstance()). Then enrich through
   tools/add_furigana.py to get kana_timings.
2. AUTHOR data.json sections/words (validator E1 must be clean: every kanji span
   covered) + LINE_TR / LINE_EXPLAIN maps. Spoken strings: clean English only —
   words whose `context` embeds CJK get a `context_speak` rewrite.
3. EN AUDIO — gen_en_audio.py (Kokoro am_michael 0.95, two-pass loudnorm -16).
   Explainer filenames: line_<sha1(spoken)[:8]>_explain.mp3 (SHA1!).
4. JP AUDIO — hybrid: Qwen3-TTS Ono_Anna primary (instruct 穏やかな大人の朗読),
   AivisSpeech Aida fallback for gate failures; whisper read-back verification;
   clip physics (tools/human_audio/clip_physics.py — duration + envelope vs
   the reading); loudnorm (wav master). Lone は is spoken わ. Memory-queue
   protocol applies. THEN COMPRESS to the served mp3: `python3
   tools/human_audio/jp_to_mp3.py --song <song>` (mono 48k 80kbps). The runtime +
   manifest reference `audio/jp/word_<sec>_<rom>.mp3` (lowercase), NOT `.wav` —
   the word-by-word drill fetches the mp3; large WAVs lost the cellular race and
   dropped to the English gloss (the v095 "car glitch" fix). Keep the wav master.
5. IMAGES — gen_word_images.py (Animagine XL 4.0, concrete words only, scene of
   the word's ACTION in the song's world, consistent protagonist, 416x608 webp).
   Review a contact sheet; re-roll misses with rewritten prompts (hands-only
   close-ups reliably fail — give the model a person or a still life).
6. ASSEMBLE — assemble_song_page.py: clones the newest template build, injects
   data.json + manifest, splices LINE_TR/LINE_EXPLAIN literals, retargets
   YT_ID/PROGRESS_SONG/titles/slug refs/chip, adds per-song _redirects lines
   ABOVE the generic fallthrough.
   Manifest needs FOUR classes: ja-JP word (key=jp), en-US word _en (key=en_speak),
   en-US section intros (key=speak_en), en-US explainers (key=explain text).
7. GATE — tools/validate_song.py + tools/validate_tts_safety.py must both pass;
   tools/bump_asset_versions.py; deploy to the random slug; verify live headless;
   hand the bare URL.

Pitch data (PITCH_DATA/PITCH_TIMING) is optional at preview time — the word card
falls back to a plain mora row; generate via the Manaoke Pitch project before promote.

## Hard-won page invariants (round 3-4, 2026-06-11) — the template encodes these; never regress them
- The word-card overlay pins INLINE z-index 2147483647 !important: anything that must stack above it (e.g. the scene lightbox) must be appended INSIDE .pitch-overlay, and inline opacity:1!important means fades must drive the inline value via style.setProperty(...,'important') + transition — CSS animations lose to !important.
- All overlay/lightbox taps bind pointerup + click with a ~400ms dedupe (click-only handlers get eaten by the overlay's capture-phase ghost-click swallow on iOS). Verify with TOUCH events (puppeteer hasTouch + touchscreen.tap), never mouse-only.
- closeCards hides _sheetBackNode (visibility) for the whole close+reparent window — the back face paints independently of the panel fade and double-flashes otherwise.
- Per-tick reveal path budget: NO getBoundingClientRect loops per tick (row-clamp geometry is cached per line/row/width on the textEl), NO :has()/sibling-combinator selectors on the lyric list (distance fade = .dist-* classes set in positionCards). The active line's scale means all wipe measurements multiply by k = offsetWidth/rect.width.
- Mastery ring hides below 5% progress (a 2% arc reads as screen junk).
- Every new silhouette/song BUILD DIR needs its own three _redirects lines above the generic fallthrough (validator E4 catches it when forgotten).
- EVERY sung syllable must be a tappable study word — kana included, not just kanji. The romaji line gap-fills from kana_timings, so a span with no study word still SHOWS its romaji while being unstudyable/unhearable in isolation. That hid the question particle か in 言えるか (いつかは言えるか / あんたに言えるか) — it appeared in the line and romaji but had no word card, so the one syllable that turns the line into a question couldn't be tapped or heard. validate_song E1 now ERRORS on any uncovered kana span (was a silent warning). A genuinely meaningless syllable may be whitelisted via data.json "coverage_exceptions": ["<span>"], but a real particle never should be — give it its own particle word (jp/rom/en/hint/context/gloss + the JP + 3 EN clips like every other card). When you add such a word, run tools/validate_song.py until E1/E4/E5 are clean.

## Reveal lock — verify_reveal.js (run before EVERY deploy that touches the song page)
`cd /tmp && node <repo>/tools/songcraft/verify_reveal.js <build-url> <fromSec> <toSec> <lineIdx> [width] [remPx]`
Drives the LIVE page with a fake clock (wraps the YT instance from YT.get('player') — the page's
own tick consumes it, all production reveal code runs unmodified) and asserts frame-by-frame:
band stays inside the line's rows, fill never moves backwards, sung rows never dim again
(real-pixel luminance check — catches "row 1 re-reveals when row 2 starts"). lineIdx counts the
intro card as 0; times are VIDEO time (add yt_offset_ms). remPx 21 forces lines to wrap like an
iOS larger-text setting — ALWAYS test a wrapped line. The harness is vacuity-guarded: a run where
the wipe never moves FAILS (the first version passed vacuously; never trust a reveal test that
doesn't prove the wipe moved). Reference suite: inochi line 2 @17-23.4s, silhouette line 1
@24-27.4s + line 46 @142.2-148.5s, both at rem 0 and 21. dcmzsn (round-2 build) is the historical
FAIL case if you need to check the harness still detects regressions.

## Card stress harness — stress_cards.js (run on every build that touches cards/audio/images)
`cd /tmp && node <repo>/tools/songcraft/stress_cards.js <build-url>`
Drives EVERY word card under iPhone touch emulation and reports defects: front-face void ratio
(the nanbo half-empty-card class), image frames stuck half-loaded, missing/wrong jisho links,
front JP audio non-200, lightbox click-through (taps where the share button hides under an open
photo and asserts it does NOT fire — trailing CLICK events hit-test through a closing lightbox
unless a capture-phase swallow eats them), and single-character lyric orphan rows (ね alone on
row 2 — prevented by text-wrap:balance on .card-text/.card-romaji).

## EN-clip read-back gate — verify_en_audio.py (run when ANY card text changes)
`conda activate qwentts && python3 tools/songcraft/verify_en_audio.py <build-dir> [--fix]`
The manifest is the text↔file contract and validate_song E5 proves manifest↔data — but nothing
proved the AUDIO still says the text. Card copy evolved across rounds while old mp3s kept playing
("hear the definition" read a definition that wasn't on screen — the owner caught it on には). This
whispers every en-US manifest clip + every ctx clip + every gloss clip against its CURRENT text;
--fix regenerates mismatches with Kokoro from the contract text. First run found ~30 stale inochi
clips. small.en can false-positive on single words (photocopying → "folder copying") — re-check
flags with large-v3 before believing them. RULE: any edit to en_speak / context / gloss /
LINE_EXPLAIN / section speak_en is NOT done until this gate passes.
PASS 0 (round 11, the nanbo-ctx bug): exits 2 BEFORE whispering if any EN-spoken contract text
contains CJK — なんぼ's context embedded どんなに with no clean-EN rewrite, the EN voice read raw
Japanese, and the read-back match couldn't catch it (the garbage "matches" its own garbage text).
Clean English is now a precondition of the contract, not a convention. JP belongs only in
display-only fields (hint) and jp/jp_speak.

## Learner-copy spec (round 11, pedagogy redesign — .local-preview/REFINE-2026-06-11/EXPERT-SPEC.md)
The spec is the authoring contract for all spoken/written learner copy. Core: the 3-second anchor.
- Word by Word = an audio DRILL, not a lecture: per word, native JP clip → 250ms → 1-4-word EN
  gloss clip → 400ms (playWordDrill; parts in ln.drillParts; gloss clips at
  audio/en/word_<sec>_<uid>_gloss.mp3, contract = w.gloss, verified by verify_en_audio).
  Glosses recycle the displayed line translation. NO spoken grammar metadata anywhere — it lives
  in `hint` (display-only, MAY contain JP).
- Explainer is EARNED: LINE_EXPLAIN only contains lines whose meaning exceeds the displayed
  translation; absent key = pill doesn't render (no section speak_en fallback). ≤22 words,
  "She's/He's saying:" template.
- Word card front en_speak ≤7 words (gloss + one hook); back context exactly 2 sentences ≤25
  words, clean EN. Depth beyond that is the links row's job: jisho.org + YouGlish
  (youglish.com/pronounce/<word>/japanese — "hear it in the wild").

## Audio lifecycle philosophy (round 11)
Audio belongs to the surface that started it; LEAVING that surface silences it. One choke point:
stopVocabAudio() (now also kills the word-card overlay's audio via stopWordCardAudio()) is called
from every surface exit — card swipe, sheet close, song/podcast start — and setFlipped() calls
stopWordCardAudio() so flipping a card stops the face you left mid-sentence. Any NEW audio source
must (a) register so stopVocabAudio can kill it, and (b) call the stop chain before it starts.

## Content policies (decided 2026-06-12 with the owner)
- Focus: help people SING — hear it, pronounce it, feel it, get back to the song. Deep dives are
  OFFLOADED: every card back carries a jisho.org link (pb-jisho). Don't pile more dictionary
  detail onto cards; link out instead.
- Scene images: every CONTENT word gets one; particles/function words are text-only BY DESIGN.
  If a generated metaphor doesn't TEACH the word at a glance (nanbo's locket), delete the image —
  a text-only card beats a confusing picture.
- JP word clips: kana-forced jp_speak for any non-obvious reading; whisper read-back gates with
  NO length exemptions; AivisSpeech (deterministic OpenJTalk) is the fallback when neural engines
  fumble a word — Aida (female) for inochi-voiced songs, TANAKA (male) for silhouette-voiced.
  jp_speak ≠ jp requires ja-JP manifest aliases + pitch.json key aliases.
- timing.json must be re-aligned (align via MMS_FA, ~1s/clip) EVERY time any JP clip changes —
  stale alignment = visible kana-lighting desync ("disjointed").

## Mask-algebra lock — verify_mask_algebra.js (run alongside verify_reveal)
`cd /tmp && node <repo>/tools/songcraft/verify_mask_algebra.js <build-url> [lineIdx] [remPx]`
Timing-free companion to verify_reveal: freezes the page's wipe-var writes, hand-sets the vars
for "row 1 done" vs "band on row 2, fill restarted", and asserts from real pixels that row 1
STAYS lit. Catches the mask layer-order/composite trap directly: mask-composite evaluates
BOTTOM-UP, so the rows-above gradient must be the FIRST (topmost) mask-image layer with
`mask-composite:add,intersect,add` (= above ∪ (fill ∩ band)). With the fill layer first, the
algebra silently becomes fill ∩ (band ∪ above) and row 1 re-reveals in lockstep with row 2 —
shipped exactly that way for weeks because verify_reveal's matrix never drove a wrapped line
non-vacuously (the wrapped silhouette-bridge run failed VACUOUS and was left red). Two rules:
a red/vacuous matrix cell is an UNVERIFIED claim, not a flaky test — fix the run or the claim;
and any edit near .lyric-layer--sung / .rom-layer--sung mask-image MUST keep the layer order
[rows-above, fill, row-band] in BOTH webkit and standard blocks (comment in the CSS says
ORDER IS LOAD-BEARING). Default args (line 2, rem 19) wrap on inochi; script exit-2s if the
chosen line doesn't wrap so it can't pass vacuously.

Both locks are blindness-guarded (2026-06-12): goToLine doesn't always land the card in the
viewport (far lines, big rem) and offscreen cards aren't PAINTED — screenshots come back black
and black-vs-black comparisons pass. Both tools now scrollIntoView the active card and FAIL if
the brightest sampled pixel row is near-black. verify_reveal's row-dim peak is also built from
the WHOLE run, not just post-pass frames — the re-reveal bug collapses luminance exactly AT the
pass moment and then only brightens, so a post-pass-only peak was vacuously satisfied (that's
how the mask-order bug survived the matrix). Discrimination check after harness edits: old
builds qm92iw / m3ftb3 must FAIL the wrapped cells, v4e96c / uiahm5 must PASS.

## Round-12 motion engine (2026-06-12, builds 6c279a / 302bbe)
Full spec + panel history: .local-preview/REFINE-2026-06-12-motion/ (FINAL-SPEC.md is the
contract; INVENTORY / PERF-AUDIT / SPEC-APPLE / SPEC-PEDAGOGY are the inputs; WORKLOG.md is
what actually landed). The pieces an editor must not break:
- estClock (in the song page, above startTick): interpolates YT's coarse getCurrentTime
  (holds 100–250ms on iOS) — advances by wall-clock dt, decays drift vs the freshest sample
  at 10%/frame, snaps >80ms, never lags >40ms. v2 shape is deliberate: pure pursuit-slew
  equilibrates a frame-budget behind and rides the lag clamp, eating the visual lead.
  Resync hooks: onPlayerState, goToLine seek, loop seek, applyPlaybackRate (rate).
- The wipe runs in rAF (window._wipeRaf) at t_vis = est + 0.060 (vision-leads-audio binding);
  auto-advance/labels/loop stay on the 60ms housekeeping interval at raw time. Paused = idle
  (one update per raw change for paused seeks).
- buildWipeGeom caches ALL geometry (token boxes via offset* = pre-transform, so no scale
  compensation; row bands; rom span) once per line-change/resize/fitCardText. ZERO DOM reads
  in the frame path — if you add a read inside updateHighlight you've reintroduced the layout
  thrash the round existed to kill. invalidateWipeGeom() marks dirty.
- Per-char lift = .pre (−3%, onset−150ms) / .hit (−8%, onset) / settle at charEnd (650ms),
  BOTH text layers in the same frame (single-layer = ghosting at the mask edge). No scale on
  chars, ever (glyph metrics break the wipe math).
- .wipe-glow strip = the only glow (drop-shadow filters on the wipe are gone); it rides
  --line-fill-px/--wipe-row-mid-px by transform, opacity-only lifecycle in _applyGlow.
- Handoff: .is-live carries the 1.06 pop; scheduleHandoff empties the stage during gaps
  ≥900ms and pre-rises the next line (.pre-live) at onset−750ms. Instrumental cards never pop.
- Motion tokens live in :root (--dur-*, --ease-*, --spring-pop/gentle). The bounce bezier
  cubic-bezier(.34,1.56,.64,1) is banned page-wide; press = :active scale .94 @50ms, release
  = 460ms var(--spring-pop). Never `transition: all` in new rules.
- Drill cadence: JP → 350ms → gloss → 700ms; visual track = .drill-live dims rows to .35,
  .drill-hot row rises 200ms before its clip and holds through the gloss.
- Word card: dim (.pd-dim, var(--pd-dim-bg)) + blur (.pd-blur, NEVER animated — snaps on at
  dim transitionend) backdrop split; card FLIPs from the tapped row; 350ms still beat between
  entry settle and pitch audio (150ms on replays); flip height swaps in ONE write at the 90°
  crossing (92/105ms timers + transitionend backstop), no height transition.
- Probes: probe_round12.js <build-url> (engine cadence, clock error, geom-call discipline,
  pause look, backdrop split, still beat, flip height/audio-law, drill isolation). Run
  probe_round11.js too — round-11 audio law is regression-tested there. Headless note: page
  globals are top-level let/const — bare identifiers in evaluate, never window.foo; re-query
  the active card per frame when sampling fill (short lines hand off mid-sample).
