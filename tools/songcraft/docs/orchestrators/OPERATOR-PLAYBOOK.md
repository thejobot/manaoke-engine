# Operator Playbook — driving a song through the builder like a human

This is the protocol for building (or retrofitting) a song **through the
builder UI as a human operator would** — verifying at every hand-off instead
of trusting the machine. It was written by actually doing it (エマ, 2026-07-07)
and encodes every place a human's eyes or ears caught something the software
missed. When Claude operates the builder, Claude follows this playbook and
journals every friction point (see IMPROVEMENT-LOOP.md).

## The human's mental model (design target)

The operator wants, in order:
1. **Search, find, set** — type a song, see candidates with art/duration/lyric
   pills, pick one, *know it picked right*.
2. **Know it's working** — every running step shows liveness (log streaming,
   next-up banner); dead air = broken trust.
3. **Listen and observe** — hear the thing before committing: the right video,
   the right timing, the right word audio.
4. **See it before pushing** — a preview of the assembled page BEFORE deploy.
5. **Fix one thing fast** — "this line sounds wrong" / "this word needs
   different audio" must be a short loop, not a rebuild.

## Protocol: NEW SONG (operator run)

1. **Library → ＋ New song → search.** Verify the candidate's album +
   duration against what you know. The lyric pills (NetEase/LRCLIB) say a
   source *has* timed lyrics — they do not prove it's the right version.
2. **YouTube pick — read the titles, not just the delta.** The auto-pick
   ranks channel-trust + duration. KNOWN TRAP (found live, エマ): a
   *different recording* (alt ver./re-record/live) can sit closer in duration
   than the official MV of the track you picked. Open "not this one?" and
   check for `(alt ver.)`, `live`, `cover`, `(Stay Home version)` markers that
   don't match your track. MV padding (+20-30s) is fine — alignment runs on
   the video's real audio.
3. **Add song → run everything it can.** The walk stops at the first
   hand-off. Watch the streamed log at least until whisper_sync starts.
4. **When lyrics land, READ the sheet** (`builds/<key>.lyrics.json` or the
   Timing tab): credits at the top? wrong language? line count sane? The
   fetcher strips leading 作词/作曲 credit runs now, but new junk classes will
   appear — a human reads the first and last 3 lines.
5. **When sync lands, CHECK the edges.** The three known CTC failure classes:
   line-initial intro grabs (first word spans many seconds), identical-line
   runs parked in instrumentals, EN scat smears. The validator flags token
   >6s / line>20s — but ALSO eyeball line 0 and the last line in the Timing
   tab. To verify against reality: the demucs vocal stem is at
   `/tmp/demucs/htdemucs/hq_<yt>/vocals.wav` — an RMS profile
   (`ffmpeg -af astats`) shows the true vocal onset in seconds.
   Fix with `timing_edit.py <key> set <line> --begin --end` (it redistributes
   word timings AND journals the lesson automatically).
6. **author_data** — Claude authors `builds/<key>.content.json` to
   SONG-CONTRACT (§2 + §2.8). Read SONG-CONTRACT.md FULLY first. Ground all
   trivia in sources (WebSearch); never invent facts. Run
   `validate_song.py` + `validate_segmentation.py` on the assembled preview
   BEFORE audio generation — fixing a gloss costs nothing pre-TTS and a
   re-render after.
7. **Audio walk** (en_audio → jp_audio → compress → pronunciation → pitch →
   drill_concat). Hours-class step; the read-back gates re-roll garbled clips.
8. **Fonts (§4.9)** — assemble auto-generates missing subsets via
   `tools/songcraft/gen_fonts.py` (vendored OFL sources in `data/fonts_src/`);
   the remaining manual case is landing DotGothic coverage for a NEW TITLE's
   glyphs — `python3 tools/songcraft/gen_fonts.py --check-landing "<title>"
   [--fix-landing]`.
9. **podcast** — Claude hand-off (content.json carries the script; render is
   pipeline).
10. **validate → landing_card → deploy preview.** All E\* must be 0 (or each
    failure explicitly backlogged with the owner's knowledge). Deploy = one git push
    at the end, never incremental. Hand over the bare URL.
11. **Listen pass on the live preview** — play 3 spot lines like a learner on
    the go: does the reveal track the voice? does word-by-word teach? Fix via
    the Timing/Words tabs, not hand edits.

## Protocol: RETROFIT (bring a shipped page onto the pipeline)

Used for inochi-mijikashi (2026-07-07), the flagship that predated songcraft.

1. **The shipped page is the ground truth.** Reverse-engineer
   `content.json` + `lyrics.json` FROM it (data.json + inline
   LINE_TR/LINE_EXPLAIN), preserving shipped timings EXACTLY —
   `whisper_sync` is deliberately marked done ("do not re-align"); the
   shipped timings are hand-tuned reference data.
2. Build a `build_state.json` with pre-assemble steps `done` + honest
   retrofit notes; run `assemble` through the builder; **parity must pass**.
3. **Benchmark diff** new data.json vs shipped: line text/line timing/word
   timing must be IDENTICAL; kana (mora) timings may differ (regenerated by
   current canon — quantify the drift, 0-1ms where mora sets match);
   context_lines regenerate from editorial → canonical (expected).
4. **Run the modern gates.** The first validate run over a legacy page
   reveals its true debt (inochi: 2 pre-splice EN clips + 5 segmentation
   cards). Backlog each honestly; do NOT silently waive, and do NOT casually
   regenerate audio — see LIBRARY-ROLLOUT.md "shared-asset blast radius".
5. Steps whose assets already exist and match (audio jobs all present) are
   marked done-as-retrofitted, never re-run blind.

## Friction log discipline

Every moment the operator hesitates, guesses, or drops to the CLI is a
product defect. Journal it (`lessons.py add`) or backlog it
(`backlog.py add`) IN THE MOMENT — the improvement loop feeds on these.
Live finds from the first operator run: the dead "run everything" button
(querySelector bound only the first `[data-nu-run]`), the alt-ver. video
trap, blind video/lyrics picks, no lyrics-refetch affordance, no
pre-deploy page preview, `/tmp` npm installs half-reaped by macOS
(→ `.node_tools/`).
