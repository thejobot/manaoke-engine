# Library Rollout — how improvements reach every song (and only when meant to)

Two opposite requirements, one doc:
1. A real improvement (template feature, timing algorithm, validator) must be
   able to roll across the WHOLE library efficiently — never hand-edit N pages.
2. An improvement to ONE song must NOT slipstream into the others unreviewed.

## The rollout mechanism (deliberate path)

- **Structure lives in the template** (`songs/inochi-mijikashi-e03jz0`).
  Change the template → re-assemble each song via songcraft
  (`manaoke_build.py rebuild` / per-song `assemble`) → `parity_audit.py`
  gates every clone → `validate_song.py` E-checks each → fresh random-slug
  previews → the owner approves → promote. Never hand-edit five pages.
- **Per-song data lives in `builds/<key>.{content,lyrics}.json`** — the only
  sanctioned per-song levers. Everything else regenerates from them.
- **Freshness** is tracked by `builds/<key>.deps.json` content hashes
  (`plan_rebuild`). If a manifest is missing/stale the plan degrades to
  "everything stale" — loud, not silent.
- **All five (now six) songs are on this base.** The inochi retrofit
  (2026-07-07) was the last holdout; `reassemble --all` is now meaningful
  across the whole library.

## Shared-asset blast radius (the leak points)

These files are SHARED across songs. A write here changes OTHER songs' pages
and/or the LIVE site instantly — treat each write as a mini-rollout with a
stated blast radius, never a casual fix:

| Shared thing | Reaches | Rule |
|---|---|---|
| the template page | every song on next re-assemble | template edits only via the rollout path above |
| `pronunciation_lexicon.json` | every song's audio decisions | adding an entry is fine (gate-only); removing/loosening needs a cross-song re-validate |
| `tools/human_audio/library/` | any song containing that word | record WHICH song requested the clip in provenance |
| `builds/gradient.defaults.json` | any song without explicit palette | prefer per-song gradient sets |
| `songs/_assets/<song>/audio/` | ALL LIVE DIRS of that song, instantly | see below — the drill-concat trap |
| `_redirects` | site-wide routing | append per-slug blocks only; watch the ~100-splat CF cap |

**The drill-concat trap (bit us on inochi):** drill concat mp3s are
hash-named by LINE TEXT, and every page carries its OWN inline DRILL_MAP
timings. Regenerating a clip that feeds a concat, without rebuilding the
concat AND re-assembling the page in the same pass, desyncs every OTHER live
dir of that song (their baked timings now describe different audio). So:
clip regen + `build_drill_concat` + re-assemble + fresh slug = ONE pass,
never piecemeal. (This is why the inochi retrofit did NOT casually fix its
2 legacy EN clips — backlog bf77fd10 does it as one modernization pass.)

## The AI-isolation boundary

AI (Claude) may improve parts of a song — teaching text, timing nudges, word
audio — under these constraints:
- Per-song improvements touch ONLY `builds/<key>.*` + that song's
  `_assets/<song>/` (with the drill-concat rule above) + a fresh preview slug.
- Anything that would change other songs (template, lexicon semantics, shared
  library clips, validator thresholds) is proposed via backlog/one-liner and
  ships only through the deliberate rollout path with the owner's word.
- Validators are the boundary's teeth: parity (structural drift), E-checks
  (contract), segmentation (canon), TTS safety (language). An AI change that
  fails a gate is a stopped change, not a shipped one.

## Known gaps (backlogged, from the 2026-07-07 veteran audit)

- parity runs only at assemble-time on previews — nothing re-audits the LIVE
  dirs against the template (drift window). (backlog f8ae38e6)
- Root `SONGS[]` (what is actually live) is not reconciled against build
  states anywhere. (backlog be19689d)
- ~~`build_state.json` writes are not atomic and have no cross-process lock~~
  **SHIPPED 2026-07-07** — `state_io.py` (flock + atomic replace).
- ~~Stop/SIGTERM strands a step on `running`~~ **SHIPPED 2026-07-07** —
  stale-running reaper (dead-pid `running` steps reap to `failed` after 10 min).
- ~~deploy/promote are honor-system prints~~ **SHIPPED 2026-07-07** —
  `manaoke_build.py promote <key> --dry-run` prints the root diff; real run applies it.
- ~~No preflight `doctor`~~ **SHIPPED 2026-07-07** —
  `manaoke_build.py doctor [--fast]` (27 preflight checks).
