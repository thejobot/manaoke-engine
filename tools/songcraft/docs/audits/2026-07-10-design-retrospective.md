# Design retrospective — 2026-07-10 (would we design the same system?)

Third doc in the audit series. Question from the owner after parts 1+2: knowing
what we know now, are there obvious tooling improvements — and would we
design the same system? Method: four independent architect reviews
(build-systems, language-data, delivery/front-end, and a pragmatist briefed
to defend the current design), each reading both audit docs, the 15
extracted specs, and source. Their verdicts converged hard.

## 1. The verdict

Yes — roughly 80-85% of the system survives and all four reviewers would
design it the same way again. The shapes are right; the defects concentrate
in enforcement plumbing. Unanimous keepers:

- Static vanilla stack, no npm, pre-deploy Python (zero product defects
  trace to the stack itself).
- URL-entropy releases: immutable random-slug dirs + content-hash cache
  keys + scripted promote. "Content-addressed store design in vernacular
  form" — it's what made the E18 wave detectable AND recoverable.
- Lean dirs + shared _assets + Function routing (structurally dissolved the
  20k-file cap).
- Template-as-live-page + parity byte-clone contract (a tokenized dead
  template would rot; this one ships and therefore stays true).
- The timing_overrides sidecar — the best data design in the repo, and it
  accidentally captures its own improvement data (the aligner labels).
- Validators as stateless recomputations; install_word's gated chain;
  Denmoku as a thin shell over the same CLI; state_io's crash-safe writes.
- The improvement loop itself — the subsystems it touched most are the ones
  that survived hostile review.

## 2. The one structural mistake (all four said it, four ways)

Truth was RECORDED or derived-once-and-warned instead of derived at
decision time and made binding. Manaoke's failures were never missing
detectors: E18 existed and fired (ignored for three days), W1 flagged every
bad kana line it later shipped (warn tier), verify_palette existed (wired
to nothing), promote read a recorded flag, the scorecard ran. They were
verdicts nothing was forced to consume. The pragmatist's line: "point the
detectors you already own at the live set on a schedule and make their
failures binding, and you get most of what a state-machine rewrite would
buy for about a hundred lines."

Three corollary laws the audits proved, worth writing on the wall:

- One producer per artifact. Every canonical-home defect (podcast-pair
  clobber, preview-freshen mutation, data.json's four writers) was a
  derived file quietly acquiring a second writer. The sidecar-replay
  pattern IS the fix shape — generalize it.
- Never derive with a fallible oracle what a human already verified
  upstream. The repo contained the correct kana line all along — E1
  guarantees a verified card reading tiles ~every sung span — and the
  moraizer re-asked a phoneme engine one timing fragment at a time. And
  never let a timing token masquerade as a word.
- Single-file DELIVERY got conflated with single-file SOURCE. The no-build-
  step rule never forbade build-time structure (a Python assembler already
  runs on every song). Ship one file; stop authoring one file. That
  conflation is the common ancestor of the dead-subsystem debris, the
  fail-open regex retargets, and the doc-vs-code drift.

## 3. Obvious improvements, ranked (knowing what we know now)

Do now (mostly refinements to the ten proposed changes):
1. validate_live — standing verification of the root SONGS[] set (E18 +
   parity + asset resolution), wired into doctor + a schedule. If
   everything else slips, keep this piece. [restore-live-gate-integrity]
2. Promote/ship consume derived truth: validate emits a witness artifact
   (input/output sha8s + verdict); promote's gate = witness matches current
   bytes; deploy=done only after push; "what is live" always computed from
   root SONGS[] + git. Recorded build_state demoted to progress
   bookkeeping. [builder-state-truth, sharpened]
3. The kana line becomes COMPOSED, not generated: literal text kana → card
   readings via the coverage walk → human pins → G2P only for residual
   gaps, with per-mora provenance. Makes the whole misreading family
   unrepresentable rather than detected. W1 promoted to error.
   [kana-line-integrity, sharpened]
4. Fail-closed four (E18-cannot-compute, tts_safety vacuous passes,
   DRILL_MAP coverage, data.json ?v= twin with auto-bump via one
   write_data_json choke point) PLUS the warning ratchet: promoted W-counts
   become a budget; any new warning class or count increase blocks promote
   until triaged. A firing detector with no consequence is
   indistinguishable from no detector. [gate-completion, + ratchet]
5. Podcast alignment pairs become a hash-guarded machine artifact
   (builds/<key>.podcast_align.json keyed by the mp3 sha, merged by
   content_to_data at derive time) — restores single-writer on data.json.
   [restore-live-gate-integrity / timing-truth, sharpened]
6. Cache headers as code inside the Pages Functions (and kill the
   _redirects any-dir→inochi catch-all: loud 404 over wrong-song audio).
   Correct even if _headers happens to attach today — converts a platform
   behavior you must keep re-verifying into a contract you own.
   [audio-delivery-hardening, sharpened]
7. Denmoku GET purity + write-verb token. [denmoku-hardening, as proposed]

Next quarter:
8. One baked-artifact freshness contract: assemble records EVERY derived
   thing baked into a page (audio, drill, data.json, fonts, images,
   pitch_data) in a deps.json 'baked' block; ONE gate recomputes and
   compares; new asset classes cannot ship outside the contract. Generalize
   E18 instead of adding one-off gates per class.
9. Declared inputs/outputs + a ~200-line homegrown hash engine growing out
   of plan_rebuild (already ~60% written). Also fixes the honest-structure
   problem: the "19-step DAG" is a straight line that cannot express the
   real assemble-before-AND-after-audio diamond — the two-pass hack behind
   the stale-AUDIO_V class.
10. Template assembled from parts at build time — still shipping one file;
    removals become real deletions; per-module coverage. Landing stays
    hand-maintained but gets a machine-owned SONGS[] region + a validator.
11. Language/timing layer separation (readings module knows no
    milliseconds; distribution function knows no G2P); re-key human
    language decisions to (line, char-span) so the future local-aligner
    switch can't orphan them.
12. Aligner bias correction as a GLOBAL calibration prior from pooled
    sidecar labels (onset-lead ~190ms, end-extension ~244ms), constants
    first, per-song shrinkage only with enough labels; success metric =
    song 8's manual-edit count.
13. Single-writer rule as enforced software: a gate that fails when any
    tool other than the assembler writes into songs/<slug>/.

Someday / explicitly skipped (the pragmatist's veto, adopted): full
data-model refactor, dashboard write machinery, set --status vocabulary as
scheduled work, dead-code prune campaigns (do opportunistically; the swipe
telemetry POST goes out with the next template round regardless), romaji
derived-from-jp_speak (cosmetic), anything that stalls song production.

## 4. The 80/20 and the week

The pragmatist's this-week list, which the panel's do-now set reduces to:
promote the five ready replacement dirs + restore the inochi podcast
(users get desynced audio and a dead player today) → land validate_live →
fix the kana composer with W1 as error → flip the four vacuous-green gates.
Then back to the drill-transport learner cluster — the audits proved the
pipeline is scaffolding that mostly works; the product is the thing worth
the hours.

## 5. Provenance

Panel workflow wf_d668ae33-5b8: 4 agents, xhigh effort, independent reads
of both audit docs + openspec/specs/ + source; verdict spread 80-90%
same-system across all four lenses. Full structured outputs in the session
task file; refinements folded into the affected OpenSpec changes same day.
