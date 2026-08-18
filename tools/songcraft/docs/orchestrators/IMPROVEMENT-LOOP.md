# The Improvement Loop — how manual fixes become software

The self-improving process. the owner fixes things by hand; the system must learn
from those fixes so the same class of problem never needs hand-fixing twice.
This doc is the orchestrator Claude follows when the owner says **"work on
improvements"** — it simulates the improvement passes the owner has been making,
with an expert team's judgment applied to what the journal shows.

## The loop (5 stages)

1. **CAPTURE — automatic, at the moment of the fix.**
   Every manual intervention already journals to `builds/lessons.jsonl`
   (rendered as `LESSONS.md`): `timing_edit.py`, `install_word.py`,
   `lexicon add`, `backlog.py resolve`, plus hand entries via
   `lessons.py add`. The capture should include the *diff* (before/after),
   not just the anecdote, so a fix is replayable. Friction found while
   OPERATING the builder is captured the same way (see OPERATOR-PLAYBOOK.md).

2. **PROMOTE — recurrence is the signal.**
   When the journal shows the same `kind` of fix N times (segmentation splits
   hit 31 before the JMdict gate existed — that was a scream, not a signal),
   file ONE backlog item: "teach the pipeline to do X." Read both
   `LESSONS.md` and `backlog.py list --status open` at the start of every
   improvements pass; count by kind; the biggest cluster goes first.

3. **SOFTWARE — patterns become validators or fixes, anecdotes stay prose.**
   A lesson graduates to code only when it has a deterministic rule:
   - repeated segmentation fixes → the JMdict headword gate (shipped)
   - repeated timing smears → the whisper_sync plausibility gate + E16 (shipped)
   - credit lines in fetched lyrics → `strip_credits` in the fetcher (shipped 2026-07-07)
   - mislabeled NetEase Chinese translations → honest `zh` labeling (shipped 2026-07-07)
   Prefer a GATE (loud, non-fatal, backlog-feeding) over a silent auto-fix —
   gates teach; silent fixes hide.

4. **APPROVE — the owner, always.**
   Present "here's what I'd automate and why" in one line per item. Never
   silently change cross-song behavior (see LIBRARY-ROLLOUT.md). Non-critical
   findings go to the backlog (`backlog.py add`), not chat nagging.

5. **CLOSE — the journal must shrink.**
   When a rule ships, resolve the backlog items and lessons it subsumes
   (`backlog.py resolve <id>` — which itself journals). A growing LESSONS.md
   means the product is not learning; a shrinking one means it is.

## The expert-team pass (multi-perspective audit)

For a full improvements round, convene three read-only reviewer personas as
subagents, each with a distinct lens, then synthesize:

- **The veteran** (reliability + rollout): where does state corrupt, what is
  unlocked/unfenced, how do improvements reach already-shipped songs, what
  would a second operator need. (2026-07-07 pass: atomic build_state writes,
  cross-process build locks, parity never runs against LIVE dirs, shared-asset
  leak points, SIGTERM strands `running`, `deps doctor`, preflight `doctor`,
  promote as a script.)
- **The recent grad** (operator experience, fresh eyes): walk the flow
  moment-to-moment; find dead air, leaps of faith, jargon. (2026-07-07 pass:
  audio in the Timing tab is the #1 fix — an audio-timing editor with no
  audio; embed the YT player in add-song confirm; show fetched lyrics on
  pick; a Preview-page button before deploy; friendly timing-flag labels.)
- **The visionary** (product soul): the ONE job — "turn a song I love into a
  lesson that makes its words stick — and ship it"; collapse the 19 steps to
  the ~4 human decisions; kill copy-a-command UI ("every printed command is a
  confession"); the learner's core loop (hear word → know meaning, repeatable,
  pace-controlled) beats all tooling work.

Full panel outputs from the 2026-07-07 round are reflected in the backlog
(`backlog.py list`) — re-run the panel only after that agenda is mostly
resolved, or when a new subsystem lands.

## Priorities standing order (from the owner)

- The learner pain that outranks everything: **hearing the word and knowing
  the meaning** — repetition too fast, context words unexplained. Drill
  transport (tap-to-replay, pause/step-back, pace control) is the north star.
- Timed-lyrics ownership: external sources (Apple/NetEase) are REFERENCE
  until the local aligner is 100% (bench_align is the yardstick: offset-
  corrected MedAE ≤50ms AND PCO@0.3 = 100%).
- Non-mission-critical findings → backlog, not conversation.
- The tool will be shared: no sibling-repo dependencies, no personal secrets
  in required paths, friendly words in every operator-facing string.
