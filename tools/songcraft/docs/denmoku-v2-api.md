# Denmoku v2 — server contract (v1, 2026-07-06)

The denmoku becomes a localhost app. The CLI (`manaoke_build.py`) stays the source of
truth — the server only runs the same commands as subprocesses and reads the same state
files. The static `file://` dashboard (`manaoke_build.py dash`) keeps working unchanged.

## Process model

- `tools/songcraft/builder/server.py` — stdlib only (http.server ThreadingHTTPServer,
  json, subprocess, threading, urllib for outbound). NO pip deps.
- Preferred port 8773, dynamic fallback to kernel-assigned if taken (copy LyriCool's
  `serve()` pattern from ~/lyricool/server.py). After binding, write the actual URL
  (`http://127.0.0.1:<port>/`) to `tools/songcraft/builder/.app-url`.
- Launched by `tools/songcraft/builder/start.sh` via ~/Denmoku.app
  (JLWorkDir=`manaoke-site/tools/songcraft/builder`). start.sh: export PATH incl.
  /opt/homebrew/bin + ~/.local/bin, call `"$HOME/.local/bin/reap-orphan-port" 8773`,
  auto-open the .app-url in the default browser after a 1s delay (subshell), then
  `exec python3 server.py`.
- ONE pipeline job at a time: a single worker thread consumes a FIFO queue. Jobs run
  `subprocess.Popen([PY3, MANAOKE_BUILD, ...], start_new_session=True)` with stdout+stderr
  merged, streamed to `builder/joblogs/<job_id>.log` (dir gitignored). PY3 = plain
  `python3` — manaoke_build.py itself shells heavy steps into their conda envs.
- Server must never run heavy steps on its own initiative; it only does what the UI asks.

## Endpoints

GET  /                    → full dashboard HTML: `render_dashboard.render_server_html()`
                            (fresh per request; no caching).
GET  /api/state           → {"gen": str, "builds": [BUILD...], "lexicon_count": int,
                             "backlog_open": int, "job": JOB|null, "queue": [JOB...]}
                            gen = sha1 over (each build_state path, mtime, size) + an
                            in-process job-event counter — changes whenever anything
                            the UI shows could have changed.
GET  /api/stale           → {"stale": {<key>: {"state": str, "reasons": [str],
                             "warn": bool, "cmd": str}}} — `manaoke_build._cheap_stale`
                            per song, measured from disk now. Deliberately NOT folded
                            into /api/state: it re-hashes the template tree and walks
                            each song's audio set (~0.6s for the library), far too heavy
                            for the 2s poll. Cached against the same `gen`, 4s TTL.
                            The UI seeds `STALE` from the copy baked into the page at
                            render time and refreshes from here at boot, on tab-visible,
                            and at the end of every job. Before this the baked copy was
                            the ONLY source, so "rebuild this page" could never clear the
                            banner that asked for it without a full ↻ reload — the
                            button looked broken. [added 2026-07-31]
GET  /denmoku.webmanifest → the Add-to-Home-Screen manifest (standalone display, own
                            icon). Separate path from /manifest.json, which _serve_preview
                            hands back from the repo root for the public site.
GET  /denmoku-icon.png    → builder/denmoku-icon-512.png — same artwork as ~/Denmoku.app,
                            so the phone icon and the Mac app icon match. [added 2026-07-31]
POST /api/run             body {"key": str, "step": str|null} → step null/absent = --auto
                            (`run <key> --auto`), else `run <key> <step>`.
                            → {"job_id": str} (queued if busy).
POST /api/stop            body {"job_id": str} → SIGTERM the process group; escalate
                            SIGKILL after 10s. → {"ok": true}
POST /api/set             body {"key": str, "step": str, "done": bool, "note": str|null}
                            → runs `set <key> <step> --done|--undone [--note ...]`
                            synchronously (it's instant). → {"ok": true, "output": str}
POST /api/init            body {"key","title_jp","title_en","artist","artist_en","yt",
                            "apple","art"} (only key required; pass provided fields as
                            the matching init flags) → runs `init` synchronously →
                            {"ok": bool, "output": str}
POST /api/identity        body {"key", plus any of "title_jp","title_en","artist",
                            "artist_en","yt","apple","art"} → runs `identity <key>`
                            synchronously with ONLY the fields sent (an absent field is
                            left alone; a sent empty string clears it, except title_en
                            which the CLI refuses empty). Writes build_state meta AND
                            builds/<key>.content.json together, and reopens reassemble /
                            landing_card / validate when the page is already built,
                            because the page bakes those strings in.
                            → {"ok": bool, "output": str}   [added 2026-07-30]
POST /api/rebuild         body {"key"[, "fresh_slug": true]} → queues
                            `rebuild <key> [--fresh-slug]`. The counterpart to
                            the red STALE: TEMPLATE chip and the library's
                            "built from an older version" line: before this the
                            box could only REPORT that a page was out of date —
                            run all (auto) walks the step list, finds every step
                            already done, and parks on deploy with the old page
                            still on disk.  → {"job_id": str}   [added 2026-07-30]
GET  /api/content/<key>   → the Writing tab's whole model:
                            {"key","title","blanks": int,"author_done": bool,
                             "drafts": [filename...],
                             "sections":[{"id","name","short_name","subtitle",
                                          "description","speak_en","note",
                                          "blank":[field...]}],
                             "lines":[{"i","jp","section","tr_en","tr_full",
                                       "explain","blank":[...]}],
                             "words":[{"i","jp","section","en","en_speak","hint",
                                       "context","gloss","jp_speak","rom",
                                       "particle","blank":[...]}]}
                            `blank` lists only the fields the page actually needs
                            (note/hint/tr_full are optional and never counted).
                            404 when the scaffold step hasn't run.   [added 2026-07-30]
POST /api/content/save    body {"key", "edits":[{"kind":"word"|"section"|"line",
                            "id": int|section-id, "field", "value"[, "jp"]}]}
                            → content_edit.apply_edits: all-or-nothing. Refuses
                            (409) text a gate would refuse later — Japanese in a
                            spoken-English box, a card with no romaji, a Japanese
                            voice line with no Japanese — deletes the recording of
                            the OLD words (gen_audio skips clips that already
                            exist, and a card's filename is its section+romaji, not
                            its text, so an edit would otherwise never be heard),
                            keeps any clip cut from a real human voice, and reopens
                            the steps that baked the old text into the page.
                            `jp` on a word edit is an optimistic-concurrency check
                            against a stale card index.
                            → {"ok","saved","errors","warnings","clips","reopened",
                               "blanks"}   [added 2026-07-30]
GET  /api/jobs            → {"active": JOB|null, "recent": [JOB...max 20]}
GET  /api/log?id=&offset= → {"chunk": str, "offset": int, "state": str, "rc": int|null}
                            (read from the job's log file starting at byte offset).
GET  /api/search?q=       → {"results": [CANDIDATE...max 12]} — proxy the iTunes Search
                            API (https://itunes.apple.com/search?term=..&media=music&
                            entity=song&limit=12&country=JP, retry country=US if <3
                            results). CANDIDATE = {"title","artist","album",
                            "duration_ms","art100","art400" (artworkUrl100 upscaled to
                            400x400bb), "apple_url" (trackViewUrl), "itunes_id",
                            "key_suggestion" (lowercase-romanized-hyphenated title),
                            "sources": {"netease": bool|null, "lrclib": bool|null}}.
                            sources: if ~/lyricool/.app-url exists AND responds, query
                            LyriCool's /api/search for the candidate (title + artist)
                            and report per-source availability; on any failure use null
                            (UI renders "?"). Total search budget ≤ 4s; never block on
                            LyriCool longer than 1.5s.

JOB = {"id": str, "key": str, "step": str|null, "state":
"queued|running|done|error|stopped", "rc": int|null, "started_at": float|null,
"ended_at": float|null}

Errors: non-2xx with {"error": str}. All JSON responses `Content-Type: application/json;
charset=utf-8`. Bind 127.0.0.1 only. No auth (localhost, single user).

## UI (render_dashboard.py)

Add `render_server_html()` — same page as the file:// dashboard (SAME aesthetic: jacket
grid, LCD search, gradient lab, step chevrons, owner pills — do NOT redesign) with a
`SERVER_MODE = true` flag that changes behavior:

- No reload ticker at all. Poll `/api/state` every 2s (only while
  `document.visibilityState === 'visible'`); if `gen` changed, re-render the dynamic
  regions IN PLACE (tile progress bars/status, step rows' dots+notes, backlog badge)
  preserving open chevrons and scroll. Full-page re-render never happens.
- Step rows: auto steps get a Run button (POST /api/run {key, step}); each song header
  gets "Run all (auto)" (step:null). manual/cli/external steps get "Mark done" /
  "Undo" (POST /api/set). Buttons disable while a job is active (except Stop).
- Job bar: fixed bottom bar visible whenever job or queue is non-empty: song + step +
  elapsed, live log tail (poll /api/log with the growing offset, autoscroll,
  monospace, ~12 lines max-height), Stop button. On job end: flash state (done/error),
  keep last log visible until dismissed.
- New Song tile → real flow: text input → GET /api/search → candidate cards (400px art
  thumb, title/artist/album, mm:ss duration, source pills NetEase/LRCLIB with
  yes/no/?) → tap to pick → confirm form prefilled (key_suggestion editable, titles,
  apple_url, art; yt REQUIRED manual paste — YouTube ID or URL, extract the ID) →
  POST /api/init → on ok, refresh state and navigate to that song's build view.
- Keep the file:// mode fully working: `dash` still writes builder/index.html with the
  existing conditional-reload behavior. server JS is additive (template branches on
  SERVER_MODE).

## Files touched

- NEW  tools/songcraft/builder/server.py
- NEW  tools/songcraft/builder/start.sh (chmod +x)
- EDIT tools/songcraft/render_dashboard.py (render_server_html + SERVER_MODE JS)
- NEW  ~/Denmoku.app (Info.plist com.example.denmoku, JLWorkDir above; binary via
       ~/.local/share/app-launcher/install.sh)
- EDIT ~/.local/share/app-launcher/README.md port table: add 8773 Denmoku,
       drop the two retired ManaokeStudio rows
- EDIT manaoke-site/.gitignore: `.app-url`, `tools/songcraft/builder/joblogs/`
- EDIT tools/songcraft/BUILDER.md: short "Server mode (Denmoku.app)" section

## Testing rules

Any test server MUST run inside tmux (the orphan-port reaper kills ppid=1 listeners)
and MUST be killed at the end. Never run real heavy pipeline steps on a real song.
The no-op `furigana`/`compress` steps that used to be the safe test target were
deleted (2026-07-28) — test /api/run against a throwaway build_state instead
(e.g. builds/zztest.build_state.json), then delete it and regenerate the dashboard
(`manaoke_build.py dash`). Note that /api/init now auto-queues `run <key> --auto`,
so adding a throwaway song from the UI really does start the pipeline: use a
throwaway key and `/api/remove` it (plus its corpus wav) when you're done.
