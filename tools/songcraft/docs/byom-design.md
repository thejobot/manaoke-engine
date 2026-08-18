# BYOM — bring-your-own-model for the pipeline's AI steps (design)

Status: DESIGN ONLY (2026-07-06). Nothing here is implemented; `ai_provider.py`
does not exist yet. the owner's ask: "For the parts that need AI can we make it a
bring your own model? So either in cli through mcp or whatever you call it,
add your own api key, or use local model."

The core idea: the two `owner=cli` steps (and the one hand-authoring side
tool) currently end at a wall — `manaoke_build.py` prints a `PROMPT:` string
and stops, and the denmoku's "Copy the whole run" explicitly filters those
steps out (`render_dashboard.py`: `/^\s*(PROMPT:|#|\()/` = not runnable). BYOM
replaces that wall with a provider seam: the SAME prompt can be (a) printed
for hand-off exactly as today, (b) sent to the Anthropic API with a key from
`.env`, or (c) sent to any local OpenAI-compatible server. The validators stay
the gate either way — BYOM changes who DRAFTS, never who APPROVES
(`validate_song.py`, `validate_tts_safety.py`, `validate_segmentation.py`,
`parity_audit.py` run unchanged, and the owner still reviews the teaching).

## 1. Inventory — what actually needs an LLM today

Audited against `manaoke_build.py` STEPS, BUILDER.md, and every script in
`tools/` (grep for prompts / Claude / anthropic / openai: the ONLY LLM usage
in the repo is the two `PROMPT:` cmd strings in `manaoke_build.py` and the
hand-authoring instructions in `line_explainers.py` — there is no SDK import
anywhere; `.env` holds only `GOOGLE_TTS_KEY`).

| Step / tool | Owner today | What the AI actually produces | Quality sensitivity | Measured volume (4 shipped songs) |
|---|---|---|---|---|
| `author_data` (STEPS, `auto=False`) | cli — "AI-drafts it, you review" | The teaching half of `builds/<key>.content.json`: `words[]` (78–120/song, each with jp / romaji / jp_speak / en gloss / en_speak spoken definition / context / gloss_drill / particle flag / pitch), `sections[].speak_en` intros, `line_tr` (~17–20 lines), `line_explain` (~17–20 "She's saying:" explainers), `trivia` (5–8 cards; contract allows 11), `grammar` (5–8), `coverage_exceptions` | HIGHEST. Must obey the segmentation canon (jisho/JMdict arbiter), E8 (particle jp_speak = spoken form), TTS-safety wording (spoken EN never contains raw JP — E11 catches it downstream), and it IS the product's teaching voice | authored fields = 39–59 KB JSON per song (headlong 46KB, odoriko 45KB, shinunoga 39KB, silhouette 59KB) ≈ 15–25k output tokens (JP ≈ 1 token/char, JSON scaffolding ≈ 3–4 chars/token). Input: SONG-CONTRACT.md (820 lines) + lyrics.json + canon rules + one reference content.json ≈ 40–60k tokens |
| `podcast` (STEPS, `auto=False`) | cli — "owned by whichever model does the research + TTS" | Deep research on the song/artist, then `podcast_script` as `[speaker, text, startSec]` (63–69 entries/song) with `{"clip": ...}` markers for JP citations. Rendering is NOT an LLM job (`generate_podcast.py` = Google Chirp3; alignment = local MMS CTC) | HIGH for research accuracy (trivia rule: "specific and researched, not generic filler") and language safety (HOST never speaks JP — E11/`jp_token_detect`). Tone: "two friends fascinated by the song". Needs WEB access — this is the step a local model structurally can't own | script = 16–34 KB per song ≈ 6–12k output tokens; input = research digests + data.json context ≈ 30–80k tokens; plus the web-search cost itself |
| `line_explainers.py` gap-fill | hand-authored (its docstring: "AUTHORING the explanations (the only non-mechanical part)") | The missing `LINE_EXPLAIN` entries when a re-clone/new line opens a gap: `check --template gaps.json` emits the worklist, a human/AI fills `{jp_line: "She's saying: ..."}`, `build` renders + wires | HIGH per-entry (same nuance bar as line_explain in author_data) but tiny volume | a handful of lines at a time; < 2 KB |

Not LLM work, deliberately out of BYOM scope: `whisper_sync` (demucs + CTC
forced alignment), `verify_jp_pronunciation` / read-back gates (Whisper),
`align_podcast` (MMS aligner), `gen_pitch` (pyopenjtalk-plus + kanjium) — all
already-local ML with no prompt interface. `gen_word_images.py` is a local
SDXL diffusion pipeline (optional dual-coding scene images) — an IMAGE model,
already "local by default"; if it ever grows a hosted backend it should reuse
this same config section, but it is not part of the LLM shim. TTS engines
have their own recipe doc (`reference_manaoke_audio_recipe`).

## 2. The provider abstraction — `tools/songcraft/ai_provider.py`

Stdlib-only shim (matching the house style: `backlog.py`, `server.py` are
stdlib-only; the `api` backend uses `urllib.request` against the Messages API
rather than pulling in the `anthropic` SDK, so nothing new gets installed).

```python
# sketch, not implementation
class HandoffRequested(Exception):
    """Raised by the handoff backend: carries the fully-rendered prompt.
    The orchestrator catches it, prints the prompt, and stops at the gate —
    byte-identical to today's behavior."""

def complete(step: str, prompt: str, *, system: str = '', max_tokens: int = 32000) -> str:
    """Route one drafting request through the configured backend for `step`.
    Returns the model's text. handoff backend never returns — it raises
    HandoffRequested."""
```

Three backends behind that one call:

### (a) `handoff` — the default, zero config, zero behavior change

Exactly today's flow, formalized. `complete()` raises `HandoffRequested(prompt)`;
`do_run` catches it, prints the prompt text (the same `PROMPT:` string the
dashboard chevron shows today, now with the song's real title/artist/paths
spliced in instead of `<TITLE>`/`<ARTIST>` placeholders — a strict
improvement), marks the step blocked, and stops. You paste it into Claude
Code, paste the result back into `builds/<key>.content.json`, mark the step
done. No key, no network, no new failure modes. This stays the default
forever so a fresh clone of the repo behaves exactly as documented in
BUILDER.md.

### (b) `api` — Anthropic API key from the repo `.env`

- Key: `ANTHROPIC_API_KEY=` in the existing gitignored `~/manaoke-site/.env`
  (same file and loading pattern as `GOOGLE_TTS_KEY`).
- Endpoint: `POST https://api.anthropic.com/v1/messages`, streaming (author_data
  outputs run 15–25k tokens — stream to avoid HTTP timeouts), `max_tokens`
  sized per step.
- Model: configurable (see config below). Default `claude-opus-4-8` — the
  teaching content is the product; don't default to a cheaper drafter.
  `author_data` should also send `output_config: {format: {type: "json_schema", ...}}`
  so the draft is guaranteed-parseable content.json fields.
- The `podcast` step's research half would declare the server-side
  `web_search` tool (`web_search_20260209`) so the API backend can actually
  do the deep-research part; search invocations bill separately from tokens.

Cost per song (list prices 2026-07; input/output per MTok — Opus 4.8 $5/$25,
Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5; estimates use the measured volumes above
and should be re-measured on first real runs):

| Step | Tokens (in / out) | Opus 4.8 | Sonnet 4.6 | Haiku 4.5 |
|---|---|---|---|---|
| author_data (one pass) | ~50k / ~25k | ~$0.88 | ~$0.53 | ~$0.18 |
| author_data (+1 revision pass) | ~75k / ~20k | ~$0.88 | ~$0.53 | ~$0.18 |
| podcast research+script | ~60k / ~10k | ~$0.55 + web-search fees | ~$0.33 + fees | not recommended |
| line_explainers gap-fill | ~10k / ~1k | ~$0.08 | ~$0.05 | ~$0.02 |
| Whole song, Opus everywhere, incl. a revision round | | ~$2.50–3.00 | ~$1.50 | — |

Order of magnitude: a full song's AI drafting on the best model costs less
than a coffee; there is no cost case for degrading `author_data` below
Opus-tier. Prompt-caching the big static prefix (SONG-CONTRACT + canon +
reference content.json, ~50k tokens, `cache_control: {type: "ephemeral"}`)
cuts the revision-round input cost ~90%.

### (c) `local` — any OpenAI-compatible endpoint

`POST {base_url}/v1/chat/completions` with a `model` name — covers Ollama
(`http://127.0.0.1:11434/v1`), LM Studio (`:1234/v1`), and llama.cpp's
`llama-server` (`:8080/v1`) with one code path. No key required (send a dummy
`Authorization` for servers that demand the header). Rule from the standing
localhost policy: if the shim ever STARTS a local model server itself it must
do so inside tmux (the orphan-port reaper kills ppid=1 listeners) — but the
default posture is "you run your server, the shim only connects".

HONEST quality section — local models and JP teaching content:

- The hard constraint isn't fluency, it's JUDGMENT ABOUT JAPANESE
  PEDAGOGY plus rule-following across a 40k-token contract. Small/medium
  open-weight models (7B–70B class) reliably produce plausible-looking
  `words[]` glosses that fail the segmentation canon (over-merged cards,
  invented compound readings), write `jp_speak` with the WRITTEN particle
  (は not わ — the exact E8 bug class), and drift into English-teacher
  register instead of the house voice. Every one of those failures is
  caught by a gate (`validate_song` E8/E12, `validate_segmentation`,
  `check_jp_gates`) — so a local draft can't SHIP wrong, but a draft that
  fails ten gates costs more human time than hand-authoring.
- Steps that TOLERATE a local model: first-draft `line_tr` (line
  translations are short, checkable at a glance, and the owner reviews anyway);
  `gaps.json` pre-fill for `line_explainers.py` (a handful of entries, human
  rewrites in place); trivia PHRASING polish of already-researched facts.
- Steps that DO NOT: nuanced `LINE_EXPLAIN` (the "She's saying:" voice is
  the product's soul — Kansai dialect callouts, cultural context a
  non-Japanese person wouldn't know); `words[]` segmentation + readings
  (canon-critical); podcast RESEARCH (needs live web — a local model can
  only hallucinate trivia, which the style guide explicitly bans: "specific
  and researched, not generic filler". A local model may WRITE the script
  from research notes a human/API pass gathered, but can't gather them).
- Recommended local posture: `local` as the drafter for the tolerant steps
  above, `handoff` or `api` for author_data's words[]/line_explain and the
  podcast research. The per-step override (below) makes that mix a config,
  not a fork.

## 3. Configuration — `[ai]` from `.env` or `providers.json`

Two layers, `.env` for the simple case, `providers.json` for per-step:

```
# ~/manaoke-site/.env  (gitignored, already holds GOOGLE_TTS_KEY)
AI_PROVIDER=handoff            # handoff | api | local   (default handoff)
ANTHROPIC_API_KEY=sk-ant-...   # api backend only
AI_MODEL=claude-opus-4-8       # api default model
AI_LOCAL_BASE_URL=http://127.0.0.1:11434/v1
AI_LOCAL_MODEL=qwen3:32b
```

```jsonc
// tools/songcraft/providers.json (optional, gitignored; wins over .env)
{
  "default":   {"provider": "handoff"},
  "author_data": {"provider": "api", "model": "claude-opus-4-8"},
  "trivia":     {"provider": "api", "model": "claude-haiku-4-5"},
  "line_tr_draft": {"provider": "local", "model": "qwen3:32b"},
  "podcast":    {"provider": "api", "model": "claude-opus-4-8", "web_search": true}
}
```

- Precedence: `providers.json` per-step entry → `providers.json` `default` →
  `.env` `AI_PROVIDER` → `handoff`.
- Per-step override is the point: `author_data` may want the best model
  while `trivia` phrasing can be cheap, and a step can be pinned back to
  `handoff` at any time (that is also the failure fallback: any backend
  error degrades to printing the prompt, never to a broken build state).
- When a step's resolved provider is NOT `handoff`, `manaoke_build.py` can
  legitimately flip that step's effective `auto` to True, and the dashboard's
  "Copy the whole run" no longer needs to exclude it — the
  `/^\s*(PROMPT:|#|\()/` filter keys off the cmd text, so the step's `cmd`
  becomes a real `manaoke_build.py run <key> author_data` invocation under
  api/local. Every draft the shim produces is written to
  `builds/<key>.ai_drafts/<step>-<timestamp>.json` before it touches
  content.json, so review/diff/rollback is a file operation.

## 4. Denmoku over MCP (later)

The denmoku server (`builder/server.py`, running at `http://127.0.0.1:8773/`)
is ALREADY a clean HTTP JSON API over the same CLI: GET `/api/state`,
`/api/jobs`, `/api/log`, `/api/search`, `/api/ytmatch`, `/api/timing/<key>`,
`/api/words/<key>`; POST `/api/run`, `/api/stop`, `/api/set`, `/api/init`,
`/api/timing/set`, `/api/timing/adopt`, `/api/word/audition`,
`/api/word/push`. Every button in the browser is one of these calls, and one
job runs at a time with FIFO queueing and streamed logs.

An MCP wrapper is therefore a thin translation layer, not a rewrite: a small
stdio MCP server (e.g. `tools/songcraft/builder/mcp_server.py`) that exposes
tools like `denmoku_state`, `denmoku_run(key, step)`, `denmoku_init(...)`,
`denmoku_log(job_id)` and forwards them to the running HTTP server
(read-only against the owner's live instance; it never spawns its own). Payoff:

- Any MCP-capable client — Claude Code, the desktop app, phone sessions —
  can drive a build conversationally ("run assemble on headlong, show me the
  log") without shelling into the repo.
- It closes BUILDER.md's honest edge that "sends prompts to the Claude CLI
  isn't literal automation": with the shim's `handoff` backend AND an MCP
  connection, the hand-off loop becomes a round trip inside one Claude
  session — the model calls `denmoku_run`, hits the `HandoffRequested`
  prompt in the job log, drafts the content itself, writes it back, re-runs.
  That is the "bring your own model through MCP" half of the owner's ask; the
  api/local backends are the "api key / local model" half.
- Guardrails carry over for free: MCP calls go through the same `/api/run`
  queue, the same validators, and deploy/promote stay manual gates.

## 5. Migration path

1. **Step 1 — the shim, handoff default (zero behavior change).** Add
   `ai_provider.py` + config loading. `manaoke_build.py` renders the two
   PROMPT strings through it. With no config present every song builds
   byte-identically to today (state files untouched; the only visible
   change is prompts printed with real values instead of placeholders).
2. **Step 2 — wire `author_data`.** `run <key> author_data` under an
   api/local provider drafts content.json's authored fields (schema-
   constrained), writes the draft to `builds/<key>.ai_drafts/`, and merges
   ONLY after `validate_segmentation` + a content lint pass; the step stays
   review-gated (draft ≠ done — the owner's review flips it). Cost logged per run.
3. **Step 3 — wire `podcast`.** Research via the api backend's web_search
   tool (or handoff for the research, local/api for the scriptwrite), script
   emitted in `[speaker,text,startSec]` shape, then the existing untouched
   chain: `validate_tts_safety` E11 → `generate_podcast.py` (Chirp3) →
   `align_podcast.py`.
4. **Later — MCP wrapper** (section 4) and, optionally, folding
   `line_explainers.py` gap-fill through the same `complete()` seam.
