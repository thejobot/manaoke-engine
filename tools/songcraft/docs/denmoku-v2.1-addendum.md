# Denmoku v2.1 addendum — timing tab, words tab, smarter add-song (2026-07-06)

Extends denmoku-v2-api.md (which stays in force). CLI stays the source of truth: every
mutation goes through a CLI tool the server shells out to; the server never edits state
files directly.

## Language rule (whole UI)

No pipeline jargon in user-facing copy. Never show: init, DAG, build_state, CLI.
"Add song" (not init), "Build steps", "Rebuild page" (assemble), "the box runs it"
style stays. Tool names stay out of copy too: say "lyric sources", not "LyriCool".

## Add-song upgrades

1. /api/search fallback: if iTunes (JP then US) yields < 3 results, also query Deezer
   (https://api.deezer.com/search?q=..., keyless) and merge (dedupe by
   lowercase(title,artist)); CANDIDATE gains "source_catalog": "itunes"|"deezer".
   Deezer art: album.cover_xl. Same 4s total budget.
2. NEW GET /api/ytmatch?title=&artist=&duration_ms= → runs
   `yt-dlp --dump-json --flat-playlist "ytsearch6:<title> <artist>"` (10s timeout;
   yt-dlp is on PATH). Rank: duration within ±10s of duration_ms first (closest wins),
   then boosts for channel containing the artist name / "official" / "Topic", then
   view_count. → {"best": YTC|null, "candidates": [YTC...]} where YTC = {"id",
   "title", "channel", "duration_ms", "view_count", "url", "thumb"}.
3. UI confirm step: on candidate pick, immediately fire /api/ytmatch (spinner on the
   video field). Prefill the best match as a small card (thumb, title, channel,
   duration vs track duration delta) with a "not this one?" expander listing
   alternates + a manual URL/ID field as last resort. The Add song button stays
   disabled until a video is chosen. Duration mismatch > 10s shows a warning chip.

## Timing tab (per song, next to Build/Gradient)

Read model — NEW GET /api/timing/{key}:
{"key", "slug", "lines": [{"i", "text", "begin_ms", "end_ms", "dur_ms", "morae",
 "mora_rate", "flags": [str], "words": [{"text","begin_ms","end_ms"}],
 "sources": {"lrclib": {"begin_ms": int}|null}, "residual_ms": int|null}],
 "median_delta_ms": int|null, "duration_ms": int|null}
- Built from tools/songcraft/builds/<key>.lyrics.json (the editable source of truth).
- flags = the E16-style checks (token>4s, identical-text ratio>2.5, mora-rate outside
  [1,14], line>20s) computed server-side (reuse/port validate_song's E16 helpers).
- lrclib: fetch once per key per server run (search by build meta title/artist,
  duration±3s; cache JSON under builder/cache/, gitignored); residual_ms = begin −
  (lrclib_begin + median_delta). null everywhere if no match.
Write model — NEW CLI tools/songcraft/timing_edit.py, server shells it:
  timing_edit.py <key> set  <line_idx> --begin <ms> --end <ms>
  timing_edit.py <key> adopt <line_idx> --source lrclib [--delta <ms>]
  Both rewrite that line in builds/<key>.lyrics.json AND mora-redistribute its words[]
  (mirror content_to_data's timed_morae logic — same method the 2026-07-06 sync fixes
  used), enforce monotonicity vs neighbors (clamp, refuse on overlap with clear error).
Endpoints: POST /api/timing/set {key,line,begin_ms,end_ms}, POST /api/timing/adopt
  {key,line} → run the CLI synchronously, return {"ok","output"}.
UI: table of lines (time mm:ss.x, text, dur, mora-rate, flag chips, residual vs
  lrclib as ±ms). Flagged rows tinted. Row expander: word chips on a horizontal
  time-scale + nudge controls (begin/end steppers ±50ms/±500ms) + "match lyric source"
  button when lrclib present. Sticky header shows median delta + "Rebuild page" button
  = POST /api/run {key, step: "assemble"} (existing job path; page preview updates on
  the song's working slug). After any edit, refetch /api/timing.

## Words tab (per song)

Read model — NEW GET /api/words/{key}:
{"key","slug","words":[{"uid" (rom_uid), "sec", "surface", "kana", "rom",
 "file" ("/assets/<song>/audio/jp/word_<sec>_<rom>.mp3"), "exists": bool,
 "provenance": {"source","sha8"}|null, "pinned": bool (pronunciation_lexicon)}]}
Built from builds/<key>.word_meta.json + songs/<slug>/tts_manifest.json +
builds/<key>.clip_provenance.json + tools/songcraft/pronunciation_lexicon.json.
Audio serving — NEW GET /assets/<song>/... maps to songs/_assets/<song>/... and
GET /auditions/... maps to builder/auditions/... (both: resolve + verify the real path
stays inside the allowed root; audio content-types; no directory listings).
Audition — NEW POST /api/word/audition {key, uid} (synchronous, ≤10s budget):
gather candidates into builder/auditions/<key>/<uid>/ (gitignored):
  1. current installed clip (label "current · <provenance source>")
  2. library cache hit tools/human_audio/library/<surface>__<kana>.mp3 ("library")
  3. Tofugu offline corpus via tools/human_audio/tofugu.py resolve logic ("tofugu",
     include its AMBIG/multi-speaker caveat in the label)
  4. JPod101 via tools/human_audio/fetch.py logic ("jpod101", skip on miss/timeout)
→ {"candidates": [{"label","url","source"}]}. TTS-engine candidates (Kokoro/Qwen/
Google) are a later phase — do NOT wire them yet.
Push — NEW CLI tools/human_audio/install_word.py, run as a JOB (POST /api/word/push
{key, uid, candidate: <server-relative url or abs path under auditions/library>,
 pin: bool} → {"job_id"}):
  install_word.py --song <song> --sec <sec> --rom <rom> --src <path> [--pin --kana ..]
  chain: two-pass loudnorm (I=-16:TP=-1.5:LRA=11, 44100 pcm_s16le) → wav master at
  songs/_assets/<song>/audio/jp/word_<sec>_<rom>.wav → tools/human_audio/jp_to_mp3.py
  --song <song> → update builds/<key>.clip_provenance.json (source curated, new sha8,
  kana) → if --pin: manaoke_build.py lexicon add ... → read-back verify: transcribe
  the final mp3 with faster-whisper (parler env, same policy as gen_audio's read-back;
  on mismatch exit non-zero and PRINT both readings — the job shows as error, the old
  master is restored from a .bak taken before install).
UI: word grid grouped by section: surface (kana) chip + play button + provenance
badge (curated=green, kokoro=grey, qwen/aivis=blue, none=hollow) + pin marker.
Tap word → drawer: play current, "find better takes" → audition candidates with play
buttons + source labels → "use this one" (+ optional "always use for this word" pin
checkbox) → queues the push job (job bar shows progress; on done, refetch /api/words,
cache-bust the audio url with ?t=).

## LyriCool repo fix (separate agent, ~/lyricool)

Its /api/search hangs >3s even when up (observed 2026-07-06), so Denmoku's source
pills always time out to "?". Diagnose and fix in ~/lyricool (likely a missing/long
timeout on NetEase or LRCLIB outbound calls inside the search handler, or serialized
slow probes): every outbound call gets a hard timeout (≤2.5s), sources queried in
parallel threads, partial results returned rather than hanging. /api/search must
answer in <3s with whatever it has. Commit locally in ~/lyricool (its own git repo);
do NOT push.

## Testing rules

Same as v2 (tmux, no heavy steps, cleanup). For /api/word/push tests: do NOT install
over a real word — test install_word.py against a scratch song dir copy or dry-run
flag (add --dry-run that prints the plan and touches nothing), and exercise the full
chain only on one sacrificial word IF a .bak-restore path is verified first; leave
_assets byte-identical at the end (verify with git status).
