# Manaoke full audit — 2026-07-10 (OpenSpec-driven)

Method: OpenSpec 1.6.0 (`openspec/` now initialized at repo root, `.cfignore`d)
was used as the audit skeleton — 8 living specs extracted from the current
codebase into `openspec/specs/`, then a 30-agent audit swept the live site
(UI + localization focus) with every medium/high finding independently
re-reproduced by an adversarial verifier before landing here. Every claim
below carries evidence (command output, file:line, or a screenshot under the
session scratchpad `audit/` dir). Live set audited = root `SONGS[]` as of
today: silhouette2-o8mugf, inochi-mijikashi-ry4rk0, shinunoga-b3qfut,
odoriko-q4f3rn, headlong-u0o2p4, ema-6rs1ij.

The plan to execute lives in `openspec/changes/` (five proposals, see §4).

---

## 1. What is verifiably WORKING

The product surface is in genuinely strong shape. All of the following were
exercised on the LIVE site, not inferred:

- Zero JS errors, zero page errors, zero failed site-asset requests on the
  landing and all six song pages, across 375/566/1100 widths and both theme
  states (only YouTube's own qoe beacons abort — normal embed telemetry).
- Byte-level structural parity: every live dir passes `parity_audit.py`
  against the template — the clone contract holds.
- Topbar mockup geometry holds at every breakpoint on every song: back chip
  pinned, art top-right inside the bar, title+pill share one center axis.
- Living-gradient canon holds: pixel sampling found no whitish tones behind
  the white lyric text on any song (brightest field pixel per song measured;
  shinunoga's B&W grey world keeps ~5:1 text contrast).
- Card-accent canon holds for all six songs — `cardAccent` byte-matches the
  pipeline's `builds/<key>.cardaccent.txt`, same colour world as each page's
  `--field-c1`.
- Human-driven interaction sweep (default visible UI only): tap-to-start,
  karaoke mid-word wipe, slow toggle, loop toggle, book sheet open/close +
  auto-pause, word cards flip to definition+context with audio, share payload
  (correct lyric + youtu.be timestamp), back chip → landing, study-mode swipe
  navigates WITHOUT autoplay (canon holds).
- Gates: E1–E17 + E19 = 0 errors on ALL six live dirs; TTS language safety
  clean over 1,493 manifest clips; segmentation canon clean — independent
  re-check of all 577 study cards against local JMdict found zero
  under-splits / over-splits beyond documented exceptions; syllable coverage
  (言えるか-か class) clean by two independent methods on all six songs.
- EN gloss speakability clean across the library (no bare symbols, no empty
  glosses); per-line kana shipped complete on every live page (ema's known
  empty-kana backlog item b2300530 is builder-side only).
- Translation register matches canon where sampled: hard content unsoftened
  (ピンサロ嬢, 死ぬのがいいわ), Kansai celebrated, trivia is specific and sourced.
- Landing: boombox scene correct at all sizes, six real jackets load, rail
  scrolls, CRT state machine reacts, deleted preview dirs fall back safely,
  fonts self-hosted, no non-promoted slugs linked.
- Pipeline hygiene: doctor 23 PASS / 1 expected WARN, `_redirects` at 54 of
  ~100 cap with all live dirs covered, no dead two-wildcard `_headers` rules,
  scripted promotes were used for all six songs on 2026-07-07.

## 2. Findings — CONFIRMED (each independently reproduced)

### 2.1 The one systemic failure: live pages ship stale audio keys (E18)

ALL SIX live dirs FAIL `validate_song` on E18 and nothing else. Commit
a2021e0 (2026-07-07 15:13, the clip-physics いい/の cure) rewrote shared
`_assets` audio bytes AFTER the live dirs were assembled (7237b50, 11:01)
and promoted (12:33). The same commit minted re-assembled replacement dirs
for five songs — all five re-validated today, PASS 0 errors:

| song | live (E18 FAIL) | ready replacement (PASS 0 errors) |
|---|---|---|
| silhouette2 | o8mugf (12 stale keys) | silhouette2-l69lx1 |
| inochi-mijikashi | ry4rk0 (2) | inochi-mijikashi-et2yqu (but see 2.2) |
| shinunoga | b3qfut (4) | shinunoga-4fqv47 |
| odoriko | q4f3rn (9) | odoriko-vxzjx6 |
| headlong | u0o2p4 (2) | headlong-t8bfuw |
| ema | 6rs1ij (4) | ema-1gx514 is mid-timing-session; re-validate before ship |

User impact: drill concats changed bytes under unchanged `?v=` URLs —
returning users replay year-immutable OLD audio; new users get NEW bytes
against OLD in-page timing maps (subtle drill-highlight desync); the
clip-physics cure is not reliably reaching anyone. This is exactly the class
E18 exists to catch; it fired — but nothing runs E18 against the LIVE dirs
after promote day (backlog f8ae38e6 predicted this gap).
Also: all six `build_state` files read deploy/promote = done for the
replacement slugs that were never promoted — the state machine recorded the
wave as shipped when only the commit landed.

→ Plan: `openspec/changes/restore-live-gate-integrity/`

### 2.2 Dead feature live: the inochi podcast 404s (flagship song)

`songs/_assets/inochi-mijikashi/audio/` contains NO podcast mp3 (every other
song has one) while the live page bakes
`PODCAST_URL='audio/inochi-mijikashi_podcast.mp3'`. Live probe (cache-safe
`?v=&cb=`): returns 200 **text/html** (SPA fallback) vs ema's control
audio/mpeg — the Immerse podcast player on inochi gets HTML bytes and cannot
play. [Corrected by part 2: the mp3 NEVER existed in git — v098 played it
from legacy R2 (audio.manaoke.app) and the retrofit repointed the page at an
`_assets` path nobody populated; recovery is R2-first.] No gate checks that
PODCAST_URL resolves to a real asset — the replacement dir et2yqu validates
PASS with the same dead pointer.

→ Plan: restore/regenerate the mp3 (aligned script is in data.json), rebuild
inochi with fresh AUDIO_V (needed for 2.1 anyway), add a PODCAST_URL
existence gate. In `restore-live-gate-integrity`.

### 2.3 Localization: the kana_timings line is systematically less
trustworthy than the study cards

The per-word study cards are clean (§1) — but the kana reading line
(`kana_timings`, which drives the romaji wipe and gap-fill) ships three
CONFIRMED moraizer defect classes in 5 of 6 songs:

- Context-blind kanji readings: 休みの日には母さんと → きゅーみのびにわははさんと
  (should be やすみのひ…かあさん; the page's own cards say yasumi/hi/kaa-san);
  笑える→わらいえる; 数えて→かずえて; 火を点ける→てんける; 深夜1時→「1とき」. 12+
  instances verified.
- Okurigana doubling (known 04f91748, now CONFIRMED SHIPPED live): 汚れた →
  けがれれた on inochi line 0.
- Rendaku folding づ→ず: the moraizer NEVER emits づ/ぢ anywhere in the
  library. 気づいてるって→きずいてるって, 守り続けよう→まもりつずけよー
  (silhouette2), 近づいて→ちかずいて (ema), plus known inochi 気付いた
  (e9d4ad3e). Even when the lyric text itself contains づ, the kana line
  rewrites it to ず.

No validate gate compares kana_timings against the (correct) card readings
or the lyric text — that's why this class ships silently.

→ Plan: `openspec/changes/kana-line-integrity/` (cure the generator, add the
gate, regenerate all affected songs).

### 2.4 Localization: translation/content fixes (specific, small)

- silhouette2 たくさんあっただろう inline EN duplicates the previous line's
  clause ("we don't even remember." twice); "there must have been so many"
  never renders (index.html:6539; the けど sibling at :6563 does it right).
- odoriko 回り出した parsed as an independent clause ("It started spinning,
  and…") in two chorus LINE_TRs + the line_explain — it's a relative clause
  on あの子と僕(の未来).
- odoriko と particle card claims the と of とぅるるる scat on 16 lines (the
  tokenizer greedy-matches before consulting coverage_exceptions, and the
  card has no only_lines) — the study sheet teaches "and/with" against
  vocables.
- ema coverage_exceptions hide sung lexical items: 1 (いち of 深夜1時) and
  XTC have no card.
- Romaji convention split: 4 inochi v3 cards use macrons (jō, narō, kopī)
  vs wapuro everywhere else; を romanized 'wo' vs 'o' per song.
- Reading adjudications for the ear queue (54a0233b): + headlong 明日 card
  'asu' vs kana line あした; ema 方 hou-vs-kata (d40a8c78 adjacent).
- headlong 思考回路 ships unsplit though its split (ff179dfc) is marked done
  — split or record the keep decision.

→ Plan: `openspec/changes/translation-content-fixes/`

### 2.5 UI: two real defects + truth-debt

- Study-sheet toolbar play/pause glyph frozen at PAUSE regardless of state
  (template-wide): `updateCardPlayIcons()` scans only `.card`, never the
  sheet's detached toolbar (template :8195). Toggle works; glyph lies.
- ema is the only song whose Immerse transcript JP lines ship without
  [token,time] pairs — per-word JP podcast karaoke silently disabled (the
  page warns 16x on every load; forced-alignment step never ran for it).
- 375px topbar: 14-char titles (inochi) overlap the back chip — ellipsis
  never engages (measured: .u-title x=52 vs chip x=14..54).
- Dead-code truth debt (spec extraction surfaced, spot-verified): landing
  filter-nav subsystem, CRT lyrics pool, boombox LCD CSS, theme-toggle
  debris, SONGS[].len never rendered, footer Requests/Support href="#";
  song template: entire topbar menu subsystem unreachable (initTopbarMenu
  early-returns, uMenuToggle is a decoy id), scene-image path in pitch cards
  dead (openSceneLightbox unreachable), drillMode dead UI, slowdown
  persistence write-only (rate resets every load), browseMode hidden flag
  still shapes default transport, swipe telemetry POSTs every gesture to a
  third-party worker (the KV-blowout class from June), SONG-CONTRACT +
  CLAUDE.md topbar/swipe/theme sections describe removed designs.
- Landing head: no meta description / og / twitter / canonical (song pages
  have them all); manifest.json orphaned (no <link rel=manifest>, wrong
  legacy colors, promises "Korean, Spanish"); .cov-experiment/ is deployed
  and publicly fetchable (dev surface, embeds a retired slug).

→ Plan: `openspec/changes/ui-chrome-truth/`

### 2.6 Delivery/runtime hardening leads (verification spike first)

Sharp, mostly-unverified-by-design leads from the spec pass worth one
focused spike: whether `_headers` rules actually attach to Pages-Function
responses (if not, ALL lean-dir audio serves with default caching — the
cellular story changes); Clear-Site-Data on `/*.html` wipes origin cache via
the no-trailing-slash song URL, evicting prewarmed clips; the `_redirects`
tail catch-all maps ANY unlisted dir's audio to inochi's assets
(wrong-song-audio fallback hazard); drill concat player has no stall
watchdog (the freeze class the chained fallback already guards); a failed
tts_manifest boot fetch silently degrades every clip to robo speechSynthesis;
pitch-card audio has no retry; prewarm caches drill URLs bare while playback
requests them with ?v=; scene images have no ?v= / no E18-style gate.

→ Plan: `openspec/changes/audio-delivery-hardening/`

## 3. Known-state cross-check

45 open backlog items (14 = the learner drill-transport agenda, the owner's stated
north star). This audit's findings were deduped against all of them — known
ids are cited inline above. The 2026-07-07 wave's own achievements
re-verified as real: segmentation debt zero, prune done, atomic build_state
writes, E18/E19 gates exist and work (E18 is literally what caught 2.1).

## 4. The plan (execution order)

1. `restore-live-gate-integrity` — promote the five validated replacement
   dirs (the owner's word, scripted promote), restore the inochi podcast + rebuild
   inochi, finish ema after its timing session, backfill build_state truth,
   wire validate/parity against LIVE dirs into the loop (f8ae38e6), add the
   PODCAST_URL existence gate. Smallest effort, largest user impact.
2. `kana-line-integrity` — cure the moraizer (context-aware readings,
   rendaku preservation, doubling fix), add the kana-vs-cards gate,
   regenerate + re-assemble affected songs. The localization core.
3. `translation-content-fixes` — the §2.4 list, per-song content rounds
   through the pipeline.
4. `ui-chrome-truth` — sheet glyph fix, ema podcast alignment, 375px title
   clamp, landing head/meta/manifest, dead-code prune batches (coverage
   playbook), telemetry removal, doc truing.
5. `audio-delivery-hardening` — verification spike, then the watchdog/retry/
   cache-key hardening batch.

After integrity is restored, the next product move per the owner's standing order
is the learner drill-transport cluster (b2232089, f5db08d0, 040f89cd,
5ae2e376, 6bfccc3a, c568890f, ced572cc, 5345a743) — deliberately NOT
duplicated into an OpenSpec change here; the backlog already specifies it.

## 5. Provenance

- Specs extracted: `openspec/specs/{landing-library, song-page-chrome,
  sing-mode, study-mode, word-audio-runtime, localization-data,
  podcast-and-kits, delivery-infra}/spec.md` — 92 requirements documenting
  CURRENT behavior, each grounded in code read during extraction.
- Workflow run wf_8d4da5de-719: 30 agents (8 spec, 9 audit, 13 verify),
  2.4M tokens, all med/high findings adversarially re-reproduced from
  scratch; UNCERTAIN/REFUTED items were dropped or downgraded to leads.
- Screenshots: session scratchpad `audit/{landing,songpage,songpages4,
  inochi,ema,driver}/` (375/566/1100 × light/dark matrices + interaction
  states).
- No repo state was mutated by the audit beyond `openspec/` + `.claude/`
  (new, cfignored), this report, and backlog intake; the in-flight ema
  timing session's uncommitted files were left untouched. No bare audio
  URLs were fetched (probes used the sanctioned `?v=&cb=` form).
