# Manaoke builder audit — 2026-07-10 (part 2: the songcraft pipeline)

Companion to `2026-07-10-openspec-audit.md` (part 1, the site surface). Part 1
audited what the builder emits; this audits the builder itself — orchestrator,
assemble, timing chain, audio chain, validators, Denmoku, ops loop — and
traces every part-1 finding to its producing code. Method: 7 living specs
extracted to `openspec/specs/builder-*`, then a 46-agent audit with every
medium/high claim independently re-reproduced (several got REFUTED and are
excluded; notable: verify_sync IS wired into validate via an indirect chain,
and the whisper_sync default path does NOT bypass the plausibility gate).

## 1. Root-cause traces: where the part-1 findings actually come from

| backlog id | symptom | root class | producing/missing code |
|---|---|---|---|
| 9aa25510 | live E18 stale wave | gate gap | gates only ever run on build_state's preview slug; nothing re-validates root SONGS[] dirs (manaoke_build.py:462) |
| c325f135 | dead inochi podcast | gate gap | assemble_page.py:650 mints PODCAST_URL by pure convention, no existence check; the existence-checking runner was bypassed by a hand `set --done` ("retrofit: shipped v098 assets preserved") |
| eade4de2 | kana misreadings | builder bug | content_to_data.py:80-84 — pyopenjtalk.g2p per APPLE SUNG-TIMING TOKEN; Apple splits mid-word (休/みの日には/母/さん/と), so g2p reads fragments without context |
| 5f9ab9f4 | づ→ず folding | builder bug | same site — g2p(kana=True) returns PHONEMIC kana (/zu/), so づ is unrecoverable from G2P even with full context; only literal text copy fixes it |
| 55a6fee0 | ema podcast karaoke dead | state gap | align_podcast RAN (build_state note: 729 tokens) but writes pairs only into data.json; persist-back to content.json is a BUILDER.md sentence, not software — the next content_to_data rerun clobbered it |
| 8bf5176f | promote=done lies | state gap | dispatch_rebuild's fresh-slug reset (manaoke_build.py:1198) was added 07-09 (fcf6804) — two days AFTER the wave; deploy=done was true, promote=done is the lie |
| 689cf375 | frozen sheet glyph | template code | updateCardPlayIcons scans .card only; sibling updateLoopStates documents the exact sheet special-case it lacks |
| f9e95495 | と claims scat | template code + validator mirror | tokenize/collectStudyWords greedy-match before exceptions (template:6336); validate_song coverage_walk has the same order |
| c9488acd, 646bc61e, 1040b1d3, 68cc160b | translations/romaji/readings | content authoring | authored at the cli-owned author_data step; pipeline transports faithfully; zero semantic gates |
| 640eb23b | swipe telemetry | template code | added 5cd2b6f (06-07 debug round), never removed; parity ENFORCES its propagation; lint has no third-party-endpoint rule |
| 9a984650 | 1時/XTC hidden | builder bug | content_to_data.py:329-345 auto-whitelists EVERY Latin and digit run into coverage_exceptions — ema's content.json has []; the shipped list is generated |
| 81a52626 | validator lies green | builder bug | validate_segmentation.py:196 literal `return 0 if a.all else 1`; skip path leaves findings empty → clean banner |
| 401bbac7 | delivery cluster | operational | hand-authored config (Functions layer added without header verification) |
| 1618bc24 | landing head | not builder | landing is hand-maintained; no template mints it |
| 3267ff06 | dead code | template code | feature-level removals never coverage-pruned; parity propagates debris by design |

Part-1 corrections this pass forced (part 1's doc updated where wrong):
- The inochi podcast was NEVER deleted — no mp3 ever existed in git at any
  path. v098 played it from legacy R2 (audio.manaoke.app); the 07-07 retrofit
  repointed the page at a relative `_assets` path nobody populated. Recovery:
  try pulling the R2 object first; regenerate only if it's gone.
- The moraizer is NOT a per-kanji table and NOT kakasi. It's
  pyopenjtalk.g2p(kana=True) invoked once per Apple timing token. Doubling
  (04f91748) is the same root: isolated 汚 token → full kun reading けがれ +
  next token れた. Reproduced offline in the parler env.
- validate_song W1 ALREADY DETECTS the kana-line defect class and fired on
  the live pages (W1=9 inochi, 24 silhouette2, 39 odoriko) — warn-level
  policy shipped it. The kana fix should PROMOTE W1, not invent a new gate.
- ema's ~100 hand edits: the CTC identical-repeat smear class is already
  shipped software (not an unpromoted scream). The real signal is below.

## 2. New CONFIRMED builder findings (each independently re-reproduced)

State machine / orchestrator (manaoke_build.py):
- promote gates on the RECORDED validate flag; nothing invalidates it when
  inputs mutate afterward — a validated-then-edited dir promotes cleanly
  (907-911). Live demo: ema's current WIP would promote today.
- ship's scoped commit omits `fonts/<folder>/` and `_redirects` (822-828)
  while assemble generates/retargets both — a shipped preview can serve
  absent font bytes and leaves the tree dirty. Also: deploy marked done
  BEFORE commit/push; a push failure exits leaving done recorded.
- dispatch_rebuild bypasses do_run's running/pid/crash discipline — an
  interrupted rebuild leaves 'done' steps over a half-mutated world.
- write_dashboard read-modify-writes EVERY build_state on every invocation
  (no dirty check) — lost-update window with any concurrent writer; corrupt
  state files are silently dropped from the dashboard (except: pass).
- No step's done derives from artifacts (full enumeration in the spec);
  `set --status` accepts ANY string; deps planner is blind to lyrics.json,
  timing_overrides, and tool recipes; rebuild has no --force.
- The deploy step's surfaced cmd still says `git add -A` — the exact hazard
  ship's own docstring warns about (would sweep the ema WIP today).

Assemble (assemble_page.py):
- No atomicity: raw template is copied over songs/<slug>/index.html BEFORE
  inputs are validated (line 500); mid-run failure leaves a template-identity
  page in a deployable dir. No temp+rename anywhere.
- One 80-line try/except (631-711) swallows the ENTIRE identity/podcast/kit/
  fonts retarget — an unreadable content.json ships a page titled inochi
  with rc 0, and parity PASSES because identical-to-template is parity-clean.
- Palette fallback is rc-0 and ungated: art miss keeps inochi's palette,
  stale cardaccent.txt survives, verify_palette.py exists but is wired into
  NOTHING. Conditional retargets fail open (empty yt keeps inochi's YT_ID).

Gate coverage (the classes that ship with everything green):
- DRILL_MAP line coverage asserted by nothing — build_drill_concat silently
  drops lines with a missing JP clip (exit 0) and E18 checks only refs
  PRESENT in the html: an empty drill map passes vacuously. (high)
- Gloss clips (word_*_gloss.mp3) covered by no gate; drill silently skips.
- E15 disables itself on a corrupt lexicon (except → {} → gate skipped).
- E18 degrades to a WARNING when the fresh-AUDIO_V compute throws — the
  gate passes exactly when it cannot verify.
- validate_tts_safety passes vacuously when data.json/audio_jobs.json are
  missing or the jp_token_detect import fails (the exact hole that once
  shipped odoriko "Dokka", per its own docstring).
- data.json / tts_manifest ?v= freshness has NO gate — and the trap is
  REACHABLE: run_podcast_align and Denmoku's preview freshen both rewrite
  data.json AFTER assemble's bump; year-immutable stale JSON, no E-code.
- Fonts: gen_fonts failure is non-fatal, no validator checks existence,
  ?v=, or glyph coverage — wrong-song glyph coverage ships green.
- pitch_data fetched with .catch(()=>null), validated by nothing — the
  whole pitch-card system can silently degrade library-wide.
- Landing SONGS[] validated by nothing (dir existence, third-party artUrl
  health — onerror=this.remove() means the SVG fallback NEVER renders when
  artUrl is set — accent match outside promote).
- E14 checks kit presence/size only, never freshness vs current data.json.

Timing chain:
- fetch_timed_lyrics --force does NOT re-apply the overrides sidecar at its
  output edge (the one writer violating timing_overrides' own contract);
  Denmoku's refetch reaches it. Three contradictory doc stories about
  refetch survival (CLAUDE.md promises re-apply; server.py says destructive
  by design; timing_overrides.py's contract says every writer re-applies).
- Refetch leaves stale mora_timings.json — the exact "stale-yet-plausible"
  hazard whisper_sync's own comments name — and drops music_start_ms.
- norm_text has no NFKC fold (fetch's _fold DOES) — any width/punctuation
  drift between sources orphans a line's overrides.
- whisper_sync --force writes with a stdout notice only — no journal, no
  marker, no downstream re-check.
- NEW moraizer class #4: moraize() SMALL set omits small vowels ぁぃぅぇぉ/ゎ —
  ウェイヴ splits into phantom zero-width morae, SHIPPED in the ema-1gx514
  WIP replacement; mora_align imports the same function, so CTC alignment
  inherits the wrong token count.

Audio chain / Denmoku / ops:
- verify_jp_pronunciation --fix swaps the served mp3 with a bespoke
  single-pass loudnorm, never updates the .wav master (which the Anki kit
  PREFERS — kits ship the old mispronunciation), and bypasses read-back,
  physics, and rollback. Repairs must ride install_word's gated path. (high)
- Denmoku has ZERO auth while /api/ship and /api/promote can commit+push
  and repoint production — exposed to the whole tailnet (serve + raw TCP
  passthrough verified).
- GET /preview/<slug>/ rewrites songs/<slug>/data.json in the working tree
  (freshen) — a page VIEW is the trigger for the podcast-pair clobber and
  the ?v= staleness above.
- generate_podcast renders into tts_output/ (outside the deployed tree);
  the copy into _assets is manual — the class that produced c325f135.
- THE EMA SIGNAL, quantified: 274 timing ops → 75 durable sidecar entries.
  76% of word nudges pulled onsets EARLIER (median 190ms); 64/98 line ops
  were end-only extensions (median 244ms). Two systematic, learnable aligner
  biases; the sidecars already contain the training labels; nothing consumes
  them. Open item e1fa6f39 covers only 8/174 of these edits.

## 3. What the builder verifiably does RIGHT (re-confirmed under audit)

state_io flock+atomic-replace holds under 120k-op concurrency tests; the
stale-running reaper works as documented for do_run; E18/E19 exist and fire
(E18 is what caught the live wave); parity gate genuinely prevents
structural drift; the overrides sidecar DOES durably re-apply ms nudges,
holds, token edits and reading pins through re-sync and re-assemble;
install_word's full chain (two-pass loudnorm → wav master → mp3 →
provenance → read-back → physics → rollback) is the strongest path in the
repo; verify_sync IS wired into validate; the lessons/backlog capture loop
journals every manual op automatically.

## 4. Plan deltas (openspec/changes/)

Five NEW changes (builder-side roots):
1. `builder-state-truth` — done derives from artifacts; promote re-validates;
   dispatch_rebuild crash discipline; ship pathspecs + ordering; dashboard
   write hygiene; deps planner inputs + rebuild --force.
2. `assemble-mint-safety` — atomic assemble; narrow the identity try/except;
   fail-closed retargets (podcast/yt/title); palette gate (wire
   verify_palette); fonts gate; landing_card escaping; _redirects prune.
3. `gate-completion` — the missing-gate batch: DRILL_MAP coverage, gloss
   clips, E15/E18 fail-closed, tts_safety vacuous passes, data.json ?v=
   twin-of-E18 (+auto-bump after any data.json writer), fonts, pitch_data,
   kit freshness, landing_audit; repairs ride install_word.
4. `timing-truth` — fetch re-applies sidecar at its edge; NFKC in norm_text;
   mora_timings invalidation; --force journaling; one refetch story across
   docs/UI; aligner bias calibration from sidecar labels (the ema signal).
5. `denmoku-hardening` — auth on write verbs (ship/promote first); preview
   freshen writes to a shadow, not the tree; word-push chain default-on.

Updated part-1 changes:
- `kana-line-integrity`: design.md rewritten to the real mechanism
  (per-Apple-token pyopenjtalk; text-first copy is THE rendaku fix; promote
  W1 to error rather than invent a gate; + small-vowel moraize fix).
- `restore-live-gate-integrity`: podcast recovery is R2-first (never existed
  in git); persist-back of aligned scripts becomes software; rebuild chain
  gains podcast_align.

Execution order stays: restore-live-gate-integrity → kana-line-integrity →
then builder-state-truth + gate-completion (they protect everything after) →
the rest as the owner prioritizes.

## 5. Provenance

Workflow wf_ac3e2588-46b: 46 agents (7 spec, 6 audit, 33 verify), 3.8M
tokens, 0 errors. Specs: openspec/specs/builder-{orchestrator, assemble,
lyrics-timing, audio-chain, validators, denmoku, ops-loop}/spec.md (~85
requirements). All REFUTED/UNCERTAIN claims excluded from this report; the
notable refutations are listed in §1's preamble and §2 was verified line-by-
line against source. Read-only throughout: no state mutated, no pipeline
steps run, no bare audio fetches, ema WIP untouched.
