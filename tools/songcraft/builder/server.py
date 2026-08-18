#!/usr/bin/env python3
"""Denmoku v2 — localhost app server for the songcraft builder.

The CLI (manaoke_build.py) stays the source of truth: this server only runs the
same commands as subprocesses and reads the same state files. The static file://
dashboard (manaoke_build.py dash) keeps working unchanged.

Endpoints (contract: docs/denmoku-v2-api.md):
    GET  /                    : full dashboard HTML (render_dashboard.render_server_html())
    GET  /api/state           : {gen, builds, lexicon_count, backlog_open, job, queue}
    POST /api/run             : body {key, step|null} → {job_id}  (step null = --auto)
    POST /api/stop            : body {job_id} → {ok}   (SIGTERM pgroup, SIGKILL after 10s)
    POST /api/set             : body {key, step, done, note?} → {ok, output}  (sync)
    POST /api/init            : body {key, title_jp?, ...} → {ok, output, job_id}
                                (sync init, then AUTO-QUEUES the prep walk:
                                lyrics + waveform + forced alignment, halting at
                                author_data — see _serve_init)
    GET  /api/jobs            : {active, recent[≤20]}
    GET  /api/log?id=&offset= : {chunk, offset, state, rc}
    GET  /api/search?q=       : iTunes (+ Deezer fallback) proxy + direct source probe
    GET  /api/probe?title=&artist=&duration_ms=&apple_url=&itunes_id=
                              : deep per-source probe (granularity + preview)
                                for the ONE picked candidate, ~9s budget
    POST /api/refetch_lyrics  : body {key} → fetch_timed_lyrics.py <key> --force
                                job (REPLACES all line/word timings)

v2.1 addendum (docs/denmoku-v2.1-addendum.md):
    GET  /api/ytmatch?title=&artist=&duration_ms= : ranked yt-dlp search
    GET  /api/timing/{key}    : timing read model (lines, flags, lrclib residuals,
                                sidecar override pins per line/word)
    POST /api/timing/set      : body {key, line, begin_ms, end_ms} → timing_edit.py (sync)
    POST /api/timing/adopt    : body {key, line} → timing_edit.py adopt lrclib (sync)
    POST /api/timing/word     : body {key, line, word, begin_ms[, end_ms]}
    POST /api/timing/hold     : body {key, line, word, at_ms | clear:true}
    POST /api/timing/worddel  : body {key, line, word}
    POST /api/timing/wordadd  : body {key, line, word, text[, where, reading]}
    POST /api/timing/wordedit : body {key, line, word[, text, reading, line_kana]}
                                → timing_edit.py word (sync; the drag-a-word save)
    GET  /api/peaks/{key}     : builds/<key>.peaks.json (waveform min/max bins)

New song step 2 (no build key exists yet — everything below keys off the
YouTube id or the cover url):
    POST /api/startprobe      : body {yt[, force]} → start measuring where the
                                music begins (downloads the corpus wav if this
                                Mac has never seen the video). Returns at once.
    GET  /api/startprobe/{yt} : {state: none|running|ready|error, music_start_ms,
                                bin_ms, duration_ms, peaks[[min,max]…]}
    GET  /api/startclip/{yt}?b=&e= : a from-zero WAV of that window, to HEAR the
                                start before the song is added
    GET  /api/palette?art=    : the colors assemble would derive from that cover
                                {c1,c2,c3,hi,card_accent,fb[3],base[3],body[4]}
    POST /api/start           : body {key, ms} | {key, auto:true} → set where an
                                EXISTING song starts (manaoke_build.py start;
                                patches meta + lyrics.json, page needs a rebuild)
    POST /api/remove          : body {key, confirm} → manaoke_build.py remove
                                (moves the song's files to builds/_trash/;
                                confirm must equal key; refuses while live)
    GET  /preview/<slug>/...  : the assembled songs/<slug>/ page pre-deploy, with
                                audio|pitch_data|images resolved to songs/_assets/
                                like the prod Pages Function (+ /fonts passthrough).
                                When corpus/hq_<ytid>.wav exists, the served page's
                                YouTube IFrame API is swapped for __ytsim.js (same
                                Player surface, audio = the downloaded YT stream)
                                so previews play even where the embed refuses.
    GET  /api/words/{key}     : word-clip read model (word_meta ⋈ manifest ⋈ provenance)
    POST /api/word/audition   : body {key, uid} → gather candidate takes (sync, ≤10s)
    POST /api/word/push       : body {key, uid, candidate, pin} → install_word.py job
    GET  /assets/<song>/...   : songs/_assets/<song>/... (audio only, traversal-guarded)
    GET  /auditions/...       : builder/auditions/...    (audio only, traversal-guarded)

Stdlib only. Bind 127.0.0.1. Preferred port 8773, kernel-assigned fallback;
actual URL written to builder/.app-url after bind.
ONE pipeline job at a time — a single worker thread consumes a FIFO queue.
The server never runs heavy steps on its own initiative; it only does what
the UI asks.
"""
import base64
import concurrent.futures
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import wave
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent            # tools/songcraft/builder
SONGCRAFT = HERE.parent                           # tools/songcraft
ROOT = SONGCRAFT.parents[1]                       # ~/manaoke-site
BUILDS = SONGCRAFT / 'builds'
JOBLOGS = HERE / 'joblogs'
JOBLOGS.mkdir(exist_ok=True)
BYO_DIR = HERE / 'cache' / 'byo'                  # gitignored: pasted sheets
AUDITIONS = HERE / 'auditions'                    # gitignored candidate takes
ASSETS = ROOT / 'songs' / '_assets'
HUMAN = ROOT / 'tools' / 'human_audio'
MANAOKE_BUILD = SONGCRAFT / 'manaoke_build.py'
TIMING_EDIT = SONGCRAFT / 'timing_edit.py'
INSTALL_WORD = HUMAN / 'install_word.py'
LEXICON_PATH = SONGCRAFT / 'pronunciation_lexicon.json'
BACKLOG_PATH = BUILDS / 'backlog.json'
PY3 = 'python3'  # manaoke_build.py stays on system python3; it shells heavy
                 # steps into their conda envs itself.
# content_to_data.py needs pyopenjtalk (kana_timings); under plain python3 it
# silently degrades (HAVE_JA=False → empty morae → a lying preview). The light
# per-edit reveal refresh (/api/preview_data) shells THIS interpreter, exactly
# as run_assemble does (manaoke_build.PARLER).
PARLER_PY = '/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python'
CONTENT_TO_DATA = SONGCRAFT / 'content_to_data.py'
PEAKS_PY = SONGCRAFT / 'peaks.py'
START_PROBE_PY = SONGCRAFT / 'start_probe.py'
YTDLP = shutil.which('yt-dlp') or '/opt/homebrew/bin/yt-dlp'

# --dev instances are a read-only mirror for headless UI work: they never write
# .app-url (so the real Denmoku's Dock pointer survives) and refuse every
# mutating POST (so two servers never run their FIFO workers against shared
# builds/). Set by serve(dev=True).
DEV_MODE = False

# Per-key write lock: the light preview refresh writes songs/<slug>/data.json —
# the SAME file the assemble job writes. This serialises preview_data vs the
# FIFO worker vs a second preview_data (ThreadingHTTPServer runs handlers in
# parallel threads). Held blocking by the worker around each job; acquired
# non-blocking by preview_data (409 on contention).
_KEY_LOCKS = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(key):
    with _KEY_LOCKS_GUARD:
        lk = _KEY_LOCKS.get(key)
        if lk is None:
            lk = _KEY_LOCKS[key] = threading.Lock()
        return lk


def _key_job_active(key):
    """True if the worker is running or has queued a job for this key — the
    sync writers 409 rather than race an assemble mid-write of the same files."""
    with _JOBS_LOCK:
        if _ACTIVE_JOB and _ACTIVE_JOB.get('key') == key:
            return True
        return any(j.get('key') == key for j in _JOB_QUEUE)

sys.path.insert(0, str(SONGCRAFT))
sys.path.insert(0, str(ROOT / 'tools'))           # validate_song helpers
import content_edit                               # the study-text writer
import timing_edit                                # lrclib fetch/match + norm
import timing_overrides                           # sidecar read model (⚓ pins)
import validate_song                              # line_tr_key / is_lyric_line
try:
    import render_dashboard
except Exception:
    render_dashboard = None  # UI may be mid-build; GET / serves a 503 page.


# ── job registry: one worker thread, FIFO queue ─────────────────────────
# ONE pipeline job at a time. Jobs live in-process only (single-user local
# tool — nothing needs to survive a restart; the build_state files on disk
# are the durable record). _JOB_EVENTS bumps on every job state change so
# /api/state's gen moves whenever anything the UI shows could have changed.
_JOBS_LOCK = threading.Lock()
_JOBS_CV = threading.Condition(_JOBS_LOCK)
_JOB_QUEUE = []          # FIFO of job dicts waiting to run
_ACTIVE_JOB = None       # the job the worker is running right now
_RECENT_JOBS = []        # finished jobs, newest first, trimmed to 20
_JOB_EVENTS = 0


def _bump_events_locked():
    global _JOB_EVENTS
    _JOB_EVENTS += 1


def _job_new(key, step, cmd=None):
    return {
        'id': uuid.uuid4().hex[:12],
        'key': key,
        'step': step,               # None = run <key> --auto
        'state': 'queued',          # queued → running → done | error | stopped
        'rc': None,
        'started_at': None,
        'ended_at': None,
        # internals (stripped by _job_public):
        'cmd': cmd,                 # explicit argv override (word-push jobs);
                                    # None = the standard manaoke_build run
        'proc': None,
        'stop_requested': False,
    }


def _job_public(job):
    """The JOB shape the contract promises — no proc handles, no flags."""
    return {k: job[k] for k in
            ('id', 'key', 'step', 'state', 'rc', 'started_at', 'ended_at')}


def _job_log_path(job_id):
    return JOBLOGS / f'{job_id}.log'


def _find_job(job_id):
    """Look a job up across active / queue / recent. Callers hold no lock —
    take it here and return the (mutable, shared) dict."""
    with _JOBS_LOCK:
        if _ACTIVE_JOB and _ACTIVE_JOB['id'] == job_id:
            return _ACTIVE_JOB
        for j in _JOB_QUEUE:
            if j['id'] == job_id:
                return j
        for j in _RECENT_JOBS:
            if j['id'] == job_id:
                return j
    return None


def _job_worker():
    """Consume the FIFO queue forever, one job at a time. Each job is a
    manaoke_build.py subprocess in its own session (so /api/stop can SIGTERM
    the whole process group) with stdout+stderr merged into joblogs/<id>.log."""
    global _ACTIVE_JOB
    while True:
        with _JOBS_CV:
            while not _JOB_QUEUE:
                _JOBS_CV.wait()
            job = _JOB_QUEUE.pop(0)
            _ACTIVE_JOB = job
            job['state'] = 'running'
            job['started_at'] = time.time()
            _bump_events_locked()

        cmd = job.get('cmd')
        if cmd is None:
            cmd = [PY3, str(MANAOKE_BUILD), 'run', job['key']]
            cmd += ['--auto'] if job['step'] is None else [job['step']]
        rc = None
        # Hold the per-key write lock for the job's duration so a concurrent
        # /api/preview_data (which rewrites the same data.json) can't interleave.
        klock = _key_lock(job['key']) if job.get('key') else None
        if klock:
            klock.acquire()
        try:
            with open(_job_log_path(job['id']), 'wb') as logf:
                logf.write((' '.join(cmd) + '\n').encode('utf-8'))
                logf.flush()
                # PYTHONUNBUFFERED: a pipeline step writes to a FILE here, not
                # a terminal, so Python buffers its prints and the log stays
                # empty until the process exits. The job bar's whole job is to
                # show what is happening WHILE it happens — through an audio
                # pass that is twenty minutes of a blank bar, which reads as
                # hung. The env is otherwise inherited untouched (see the
                # MANAOKE_CHAIN_EXEC note in _serve_word_push).
                proc = subprocess.Popen(
                    cmd, cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env={**os.environ, 'PYTHONUNBUFFERED': '1'},
                )
                with _JOBS_LOCK:
                    job['proc'] = proc
                rc = proc.wait()
        except Exception as e:
            try:
                with open(_job_log_path(job['id']), 'ab') as logf:
                    logf.write(f'\n[server] job failed to launch: {e}\n'.encode('utf-8'))
            except OSError:
                pass
        finally:
            if klock:
                klock.release()

        with _JOBS_CV:
            job['rc'] = rc
            job['ended_at'] = time.time()
            if job['stop_requested']:
                job['state'] = 'stopped'
            elif rc == 0:
                job['state'] = 'done'
            else:
                job['state'] = 'error'
            job['proc'] = None
            _ACTIVE_JOB = None
            _RECENT_JOBS.insert(0, job)
            del _RECENT_JOBS[20:]
            _bump_events_locked()


def _stop_job(job):
    """SIGTERM the job's process group; escalate to SIGKILL after 10s if it
    ignores the term. Queued jobs are just plucked from the queue."""
    with _JOBS_CV:
        job['stop_requested'] = True
        if job in _JOB_QUEUE:
            _JOB_QUEUE.remove(job)
            job['state'] = 'stopped'
            job['ended_at'] = time.time()
            _RECENT_JOBS.insert(0, job)
            del _RECENT_JOBS[20:]
            _bump_events_locked()
            return
        proc = job.get('proc')
    if proc is None or proc.poll() is not None:
        return  # already finished — stop is a no-op
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return

    def _escalate():
        time.sleep(10)
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    threading.Thread(target=_escalate, daemon=True,
                     name=f'kill-{job["id"]}').start()


# ── state readers (fresh from disk every call — the CLI owns the files) ──

def _state_paths():
    return sorted(BUILDS.glob('*.build_state.json'))


def _compute_gen():
    """sha1 over (each build_state path, mtime, size) + the in-process
    job-event counter — changes whenever anything the UI shows could have."""
    h = hashlib.sha1()
    for p in _state_paths():
        try:
            stt = p.stat()
            h.update(f'{p.name}|{stt.st_mtime}|{stt.st_size}\n'.encode('utf-8'))
        except OSError:
            pass
    with _JOBS_LOCK:
        h.update(str(_JOB_EVENTS).encode('utf-8'))
    return h.hexdigest()


def _read_builds():
    builds = []
    for p in _state_paths():
        try:
            builds.append(json.loads(p.read_text()))
        except Exception:
            pass  # a mid-write / corrupt state file must never 500 the poll
    return builds


_STALE_LOCK = threading.Lock()
_STALE_CACHE = {'gen': None, 'at': 0.0, 'map': {}}
_STALE_TTL = 4.0                     # seconds — a burst of asks is one walk


def _stale_map(force=False):
    """{key: cheap_stale} for every song, recomputed from disk.

    Not free (~0.6s across the library: a template tree hash + an audio walk
    per song), so it is cached against the same `gen` /api/state reports —
    anything that could change staleness (a build_state write, a job event)
    moves gen and invalidates this."""
    gen = _compute_gen()
    with _STALE_LOCK:
        if (not force and _STALE_CACHE['gen'] == gen
                and (time.time() - _STALE_CACHE['at']) < _STALE_TTL):
            return _STALE_CACHE['map']
    out = {}
    try:
        import manaoke_build as mb
        for p in _state_paths():
            try:
                st = mb._sync_steps(json.loads(p.read_text()))
                out[st['key']] = mb._cheap_stale(st)
            except Exception:
                pass          # one unreadable song must not blank the others
    except Exception:
        return {}             # no manaoke_build → the UI keeps its baked copy
    with _STALE_LOCK:
        _STALE_CACHE.update({'gen': gen, 'at': time.time(), 'map': out})
    return out


def _lexicon_count():
    try:
        return len(json.loads(LEXICON_PATH.read_text()).get('words', {}))
    except Exception:
        return 0


def _backlog_open():
    try:
        items = json.loads(BACKLOG_PATH.read_text())
        return sum(1 for it in items if it.get('status') == 'open')
    except Exception:
        return 0


# ── /api/search helpers: iTunes proxy + direct source availability ──────

_KAKASI = None
_KAKASI_TRIED = False


def _romanize(text):
    """pykakasi if it happens to be importable under plain python3; the title
    itself otherwise (the ASCII fold in _key_suggestion handles the rest)."""
    global _KAKASI, _KAKASI_TRIED
    if not _KAKASI_TRIED:
        _KAKASI_TRIED = True
        try:
            import pykakasi
            _KAKASI = pykakasi.kakasi()
        except Exception:
            _KAKASI = None
    if _KAKASI is not None:
        try:
            return ' '.join(w.get('hepburn', '') for w in _KAKASI.convert(text))
        except Exception:
            pass
    return text


def _key_suggestion(title, itunes_id):
    """lowercase-romanized-hyphenated title; if romanization leaves no ASCII
    at all, fall back to the itunes_id (never invent characters)."""
    folded = unicodedata.normalize('NFKD', _romanize(title or ''))
    folded = folded.encode('ascii', 'ignore').decode('ascii')
    words = re.findall(r'[a-z0-9]+', folded.lower())
    return '-'.join(words) if words else str(itunes_id or '')


def _artist_en_suggestion(artist):
    """Romanized artist name for the box to PREFILL "artist (en)".

    A blank here is not harmless: assemble only substitutes the template's
    identity when this song has a replacement, so an empty artist_en leaves the
    TEMPLATE's English artist ("CreepHyp") sitting in the clone — invisible on
    screen (.u-artist-en is display:none) but in the HTML, in a code comment,
    and enough to fail the parity gate with a diff that never names the cause.
    Found by adding マリーゴールド with the field left blank, 2026-07-29.

    Romanization is right for the common case (あいみょん → Aimyon) and it is a
    suggestion, not a decision — the field stays editable.

    An artist already written in Latin letters (YOASOBI, Vaundy, KANA-BOON) has
    nothing to romanize, and the old code returned '' for exactly that case —
    same blank, same CreepHyp left in the clone, on every second J-pop act.
    Their English name IS their name: hand it back unchanged. Found by adding
    YOASOBI from the box, 2026-07-30."""
    name = (artist or '').strip()
    if not name:
        return ''
    if not re.search(r'[぀-ヿ㐀-鿿]', name):
        return name                   # already Latin — that IS the English name
    rom = _romanize(name).strip()
    if not rom or rom == name:
        return ''                     # no romanizer available
    return ' '.join(w.capitalize() for w in rom.split())


def _itunes_search(query):
    """iTunes Search API, JP storefront first (most Manaoke songs are JP
    catalog), retry US if fewer than 3 hits. The US retry MERGES after the JP
    hits (deduped by normalized title+artist) — it used to REPLACE, dropping
    1-2 real JP hits whenever both storefronts came back thin. Returns the
    raw result dicts."""
    results = []
    seen = set()
    for country in ('JP', 'US'):
        qs = urllib.parse.urlencode({'term': query, 'media': 'music',
                                     'entity': 'song', 'limit': 12,
                                     'country': country})
        try:
            with urllib.request.urlopen(
                    f'https://itunes.apple.com/search?{qs}', timeout=4) as r:
                data = json.loads(r.read().decode('utf-8', errors='replace'))
            batch = data.get('results') or []
        except Exception:
            batch = []
        for r in batch:
            k = (_norm_name(r.get('trackName')), _norm_name(r.get('artistName')))
            if k in seen:
                continue
            seen.add(k)
            results.append(r)
        if len(results) >= 3:
            break
    return results


def _norm_name(s):
    return (s or '').strip().lower()


def _probe_sources(title, artist, duration_ms, timeout):
    """Ask the vendored lyric sources directly (fetch_timed_lyrics.availability:
    NetEase + LRCLIB, no LyriCool or any sibling repo involved) whether they
    know this candidate. Returns {"netease": bool|None, "lrclib": bool|None} —
    None renders as '?' in the UI."""
    if timeout <= 0.05:
        return None
    try:
        import fetch_timed_lyrics
        return fetch_timed_lyrics.availability(
            title, artist, duration_ms or None, timeout=min(timeout, 2.5))
    except Exception:
        return None


# Deep probes are slow (~seconds) and the operator flips between candidates —
# memoize per (title, artist, duration_ms) for the server run. Only resolved
# probes are cached: an all-unknown result (every source timed out) stays
# uncached so the next look retries.
_PROBE_MEMO = {}
_PROBE_LOCK = threading.Lock()


def _deep_probe(title, artist, duration_ms, apple_url):
    """GET /api/probe backing: fetch_timed_lyrics.probe_sources for the ONE
    picked candidate — truthful per-source granularity (Apple = parsed TTML,
    NetEase = yrc vs lrc, LRCLIB = synced vs plain) + previews + auto_pick."""
    memo_key = (title, artist, duration_ms or 0)
    with _PROBE_LOCK:
        hit = _PROBE_MEMO.get(memo_key)
    if hit is not None:
        return hit
    import fetch_timed_lyrics
    payload = fetch_timed_lyrics.probe_sources(
        title, artist, duration_ms or None, apple_url, timeout=8.5)
    if any((payload.get(s) or {}).get('has') is not None
           for s in ('apple', 'netease', 'lrclib')):
        with _PROBE_LOCK:
            _PROBE_MEMO[memo_key] = payload
    return payload


def _deezer_search(query, timeout):
    """Deezer keyless search — the fallback catalog when iTunes (JP+US) is
    thin. Returns the raw track dicts ([] on any failure)."""
    if timeout <= 0.3:
        return []
    qs = urllib.parse.urlencode({'q': query, 'limit': 12})
    try:
        with urllib.request.urlopen(
                f'https://api.deezer.com/search?{qs}', timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8', errors='replace'))
        return data.get('data') or []
    except Exception:
        return []


def _search_candidates(query):
    """The /api/search payload: ≤12 candidates (iTunes JP→US, Deezer merged in
    when iTunes is thin — addendum §Add-song 1), each with art + a
    key_suggestion + per-source lyric availability. Total budget ≤4s. The
    probes hit NetEase/LRCLIB directly (vendored lyric_sources, self-contained)
    in parallel threads with a ≤2.9s cap — one probe's wall time,
    not twelve — inside the same 4s budget."""
    deadline = time.time() + 4.0
    cands = []
    seen = set()                      # dedupe by lowercase (title, artist)
    for r in _itunes_search(query)[:12]:
        art100 = r.get('artworkUrl100') or ''
        title = r.get('trackName') or ''
        artist = r.get('artistName') or ''
        itunes_id = r.get('trackId')
        seen.add((_norm_name(title), _norm_name(artist)))
        cands.append({
            'title': title,
            'artist': artist,
            'album': r.get('collectionName') or '',
            'duration_ms': int(r.get('trackTimeMillis') or 0),
            'art100': art100,
            'art400': art100.replace('100x100bb.jpg', '400x400bb.jpg'),
            'apple_url': r.get('trackViewUrl') or '',
            'itunes_id': itunes_id,
            'key_suggestion': _key_suggestion(title, itunes_id),
            'artist_en_suggestion': _artist_en_suggestion(artist),
            'source_catalog': 'itunes',
        })
    if len(cands) < 3:
        for r in _deezer_search(query, min(3.0, deadline - time.time())):
            title = r.get('title') or ''
            artist = (r.get('artist') or {}).get('name') or ''
            if (_norm_name(title), _norm_name(artist)) in seen:
                continue
            seen.add((_norm_name(title), _norm_name(artist)))
            album = r.get('album') or {}
            cands.append({
                'title': title,
                'artist': artist,
                'album': album.get('title') or '',
                'duration_ms': int(r.get('duration') or 0) * 1000,
                'art100': album.get('cover_medium') or album.get('cover') or '',
                'art400': album.get('cover_xl') or '',
                'apple_url': '',
                'itunes_id': None,
                'key_suggestion': _key_suggestion(title, r.get('id')),
                'artist_en_suggestion': _artist_en_suggestion(artist),
                'source_catalog': 'deezer',
            })
    cands = cands[:12]
    budget = min(2.9, deadline - time.time())
    probed = [None] * len(cands)

    def probe(i, c):
        probed[i] = _probe_sources(c['title'], c['artist'], c['duration_ms'], budget)

    threads = [threading.Thread(target=probe, args=(i, c), daemon=True)
               for i, c in enumerate(cands)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0.1, deadline - time.time()))
    for c, s in zip(cands, probed):
        c['sources'] = s               # a probe still in flight stays null
    return cands


# ── /api/ytmatch: ranked yt-dlp search (addendum §Add-song 2) ────────────

def _yt_title_key(s):
    """A video/track title reduced to the characters that carry its identity:
    case-folded, width-normalized, punctuation and spaces gone. Used only to
    ask 'does this video name the track at all' — never shown."""
    s = unicodedata.normalize('NFKC', s or '').lower()
    return ''.join(ch for ch in s if ch.isalnum())


def _yt_search(query, n, timeout):
    """One yt-dlp flat search → raw entry dicts ([] if it fails). Failing one
    query must not lose the other's results."""
    try:
        r = subprocess.run(
            [YTDLP, '--dump-json', '--flat-playlist', f'ytsearch{n}:{query}'],
            capture_output=True, text=True, timeout=timeout)
    except Exception:
        return []
    out = []
    for line in r.stdout.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _yt_candidates(title, artist, duration_ms):
    """yt-dlp flat search for the track, ranked: does the video even name this
    song, then different-recording markers, then channel trust (artist name in
    channel, 'official'/'Topic'), then duration band (±10s exact, ±35s near —
    official MVs routinely run 10-30s past the track for intros/outros, and
    they are what the song pages embed), then closest delta, then view_count.
    --flat-playlist entries may lack view_count/duration — treated as
    0/unknown, never a crash.

    TWO searches, merged. Plain '<title> <artist>' returns whatever YouTube
    thinks is popular for the artist, which for an album title track is often
    the album's OTHER songs (STRAWBERRY ANNIVERSARY / 髭: the plain search
    returned six videos, none of them the song — the album's title track was
    nowhere in the top ten). Adding 'topic' pulls in the auto-generated
    per-track uploads, which carry the exact album audio and so land in the
    ±10s band. Run in parallel: two searches, one search's wall time."""
    queries = [f'{title} {artist}'.strip(), f'{title} {artist} topic'.strip()]
    raw, seen = [], set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        for entries in ex.map(lambda q: _yt_search(q, 8, 12), queries):
            raw.extend(entries)
    cands = []
    for e in raw:
        vid = e.get('id') or ''
        if not vid or vid in seen:
            continue
        seen.add(vid)
        dur = e.get('duration')
        cands.append({
            'id': vid,
            'title': e.get('title') or '',
            'channel': e.get('channel') or e.get('uploader') or '',
            'duration_ms': int(dur * 1000) if isinstance(dur, (int, float)) else None,
            'view_count': int(e['view_count']) if isinstance(e.get('view_count'), (int, float)) else None,
            'url': e.get('url') or f'https://www.youtube.com/watch?v={vid}',
            'thumb': f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg',
        })

    # A different RECORDING can sit closer in duration than the official MV
    # of the track the user picked (エマ: '(alt ver.)' 3:32 outranked the MV
    # 3:53 vs the 3:27 album track). Duration can't tell recordings apart —
    # variant markers in the VIDEO title that the TRACK title lacks can.
    VARIANT = re.compile(r'alt\s*ver|live|ライブ|cover|カバー|remix|リミックス|'
                         r'acoustic|アコースティック|instrumental|inst\.|'
                         r'off vocal|karaoke|カラオケ|stay home|ver\.|version', re.I)
    want = _yt_title_key(title)

    def rank(c):
        delta = (abs(c['duration_ms'] - duration_ms)
                 if c['duration_ms'] is not None and duration_ms else None)
        band = 2 if delta is None else (0 if delta <= 10000 else
                                        1 if delta <= 35000 else 2)
        ch = _norm_name(c['channel'])
        boost = (2 * (bool(artist) and _norm_name(artist) in ch)
                 + ('official' in ch or 'topic' in ch))
        variant = bool(VARIANT.search(c['title'])) and not VARIANT.search(title)
        # Nothing below this line can tell one SONG from another, and the
        # channel-trust boost outranks duration — so before this check the
        # artist's most-viewed unrelated video beat the actual track
        # (エビバデハピ, 3:17, ranked first for a 4:27 song). A video whose
        # name doesn't contain the track's name loses to one whose does.
        # Promotion only: when nothing matches, every candidate scores the
        # same here and the old order stands.
        named = bool(want) and want in _yt_title_key(c['title'])
        return (not named,
                variant,          # different-recording markers lose next
                -boost,
                band,
                delta if delta is not None else 1 << 40,
                -(c['view_count'] or 0))

    cands.sort(key=rank)
    return cands


# ── timing read model (addendum §Timing tab) ─────────────────────────────
# The flag thresholds port validate_song's E16 timing-plausibility checks;
# e16_morae is copied here because it is local to run_checks (not importable).
E16_KANA = re.compile(r'[぀-ゟ゠-ヿ]')
E16_JP = re.compile(r'[぀-ヿ㐀-鿿]')


def _e16_morae(text):
    """validate_song run_checks e16_morae: kana = 1 mora, kanji ≈ 2."""
    m = 0
    for ch in text:
        if E16_KANA.match(ch):
            m += 1
        elif E16_JP.match(ch):
            m += 2
    return m


# lrclib results per key, fetched once per server run ("miss" included — the
# disk cache under builder/cache/ only stores hits, see timing_edit.lrclib_rows).
_LRCLIB_MEMO = {}
_LRCLIB_LOCK = threading.Lock()


def _lrclib_rows_cached(key, doc):
    with _LRCLIB_LOCK:
        if key in _LRCLIB_MEMO:
            return _LRCLIB_MEMO[key]
    rows = timing_edit.lrclib_rows(key, doc)
    with _LRCLIB_LOCK:
        _LRCLIB_MEMO[key] = rows
    return rows


def _git_out(args):
    """One git call from the repo root, quietly. Returns (ok, output)."""
    try:
        r = subprocess.run(['git'] + args, cwd=str(ROOT), capture_output=True,
                           text=True, timeout=15)
    except Exception:
        return False, ''
    return r.returncode == 0, (r.stdout + r.stderr).strip()


_PUSHED_MEMO = {}          # (head, origin, index_mtime, bucket) -> {key: bool}
_PUSHED_LOCK = threading.Lock()


def _git_fingerprint():
    # Must move on BOTH transitions that flip `pushed`: a commit (HEAD/index) AND
    # a bare `git push` (only origin/main moves). A short time bucket also picks
    # up an un-staged working-tree edit under songs/<slug> within a few seconds.
    _o1, head = _git_out(['rev-parse', 'HEAD'])
    _o2, origin = _git_out(['rev-parse', 'origin/main'])
    try:
        idx = (ROOT / '.git' / 'index').stat().st_mtime
    except OSError:
        idx = 0.0
    return (head.strip(), origin.strip(), idx, int(time.time() // 3))


def _song_pushed(key, slug, folder):
    """True when the DEPLOYED bytes (the preview dir + shared assets) are
    committed AND not ahead of origin/main — i.e. Cloudflare has them. Keyed ONLY
    on the cfignore-deployed surfaces (songs/<slug>, songs/_assets/<folder>), NOT
    the churny builds/<key>.* sidecars: build_state.json is rewritten after every
    ship/save and would otherwise masquerade as 'CF is missing bytes'. Memoized
    on the git fingerprint so a burst of edits doesn't fork git per call; never
    runs in the 2s poll."""
    if not slug:
        return False
    fp = _git_fingerprint()
    with _PUSHED_LOCK:
        bucket = _PUSHED_MEMO.get(fp)
        if bucket is not None and key in bucket:
            return bucket[key]
    paths = [f'songs/{slug}', f'songs/_assets/{folder}']
    _ok1, dirty = _git_out(['status', '--porcelain', '--'] + paths)
    _ok2, ahead = _git_out(['rev-list', '--count', 'origin/main..HEAD', '--'] + paths)
    pushed = (not dirty.strip()) and ahead.strip() in ('', '0')
    with _PUSHED_LOCK:
        _PUSHED_MEMO.clear()               # only the current fingerprint matters
        _PUSHED_MEMO.setdefault(fp, {})[key] = pushed
    return pushed


def _root_slug(folder):
    """The slug the public landing (root SONGS[] url) currently points at for
    this song — the ONLY honest 'what's live' signal (build_state.promote lies:
    it stays 'done' citing a superseded slug after a fresh-slug rebuild)."""
    try:
        html = (ROOT / 'index.html').read_text()
    except OSError:
        return None
    m = re.search(r"url:\s*'/songs/(" + re.escape(folder) + r"-[a-z0-9]+)/'", html)
    return m.group(1) if m else None


def _content_authored(path):
    """Has anyone actually WRITTEN in this content.json, or is it still the
    machine skeleton? scaffold.py stamps `_scaffold` and leaves every creative
    field empty on purpose; that file existing is not a receipt for human work.
    It becomes one the moment any teaching field carries text — a translation, an
    explainer, a spoken definition, a section name, trivia, grammar."""
    try:
        c = json.loads(path.read_text())
    except Exception:
        return False
    if not c.get('_scaffold'):
        return True                       # hand-authored / pre-scaffold file
    # line_explain values are strings; line_tr values are {en, full} dicts on
    # every song built since the four-verbs scaffold. Reading both as strings
    # threw AttributeError from inside _song_state, which killed the whole
    # Timing tab (500) for any scaffold-born song with a written translation.
    def _written(v):
        if isinstance(v, dict):
            return any(str(x or '').strip() for x in v.values())
        return bool(str(v or '').strip())
    for m in ('line_tr', 'line_explain'):
        if any(_written(v) for v in (c.get(m) or {}).values()):
            return True
    for w in (c.get('words') or []):
        if any((w.get(f) or '').strip() for f in ('en_speak', 'context', 'gloss', 'hint')):
            return True
    for s in (c.get('sections') or []):
        if any((s.get(f) or '').strip() for f in ('name', 'subtitle', 'description', 'speak_en')):
            return True
    return bool(c.get('trivia') or c.get('grammar'))


def _song_state(key):
    """The edit→ship→promote truth model, derived from files + git (never from
    the lying build_state step statuses). Drives the Timing-tab state chips."""
    st = _read_build_state(key)
    slug = (st or {}).get('slug') or ''
    folder = _asset_folder(key) or key
    songdir = ROOT / 'songs' / slug if slug else None
    deps = BUILDS / f'{folder}.deps.json'
    built = bool(slug) and songdir is not None and songdir.is_dir() and deps.is_file()
    # edited = a timing/word source is newer than the last assemble (deps.json is
    # rewritten by assemble; the light preview refresh touches data.json only, so
    # a refreshed preview does NOT clear this — exactly what we want).
    edited = False
    if built:
        try:
            built_ts = deps.stat().st_mtime
        except OSError:
            built_ts = 0
        src = 0.0
        for p in (BUILDS / f'{key}.lyrics.json',
                  BUILDS / f'{key}.timing_overrides.json',
                  BUILDS / f'{key}.content.json',
                  BUILDS / f'{key}.clip_provenance.json'):
            try:
                src = max(src, p.stat().st_mtime)
            except OSError:
                pass
        # a Words-tab clip swap rewrites _assets/<folder>/audio bytes but leaves
        # AUDIO_V stale until re-assemble (the E18 immutable-audio trap). Fold the
        # newest audio-dir mtime in so the ladder forces "apply my changes" (a
        # rebuild that rotates AUDIO_V) BEFORE ship can offer to publish it.
        audiodir = ASSETS / folder / 'audio'
        try:
            for sub in ('jp', 'en'):
                d = audiodir / sub
                if d.is_dir():
                    src = max(src, d.stat().st_mtime,
                              max((f.stat().st_mtime for f in d.iterdir()
                                   if f.is_file()), default=0.0))
        except OSError:
            pass
        edited = built_ts > 0 and src > built_ts + 1.0    # 1s slack for write order
    root = _root_slug(folder)
    promoted = bool(slug) and root == slug
    pushed = _song_pushed(key, slug, folder) if built else False
    # when the last human edit landed (the ladder's "last saved" clock).
    # NOT lyrics.json: the automatic lyric fetch writes that, so keying off it
    # made a song nobody had touched report "last saved 10:21 PM" seconds after
    # it was added. timing_edit.py records EVERY edit in the overrides sidecar —
    # that one is always a receipt. content.json only counts once somebody has
    # WRITTEN in it: since 2026-07-29 the walk scaffolds a machine skeleton at
    # that path, and counting its mtime brought the same lie back ("last saved
    # 12:28 AM" for work nobody did).
    saved_at = 0.0
    try:
        saved_at = (BUILDS / f'{key}.timing_overrides.json').stat().st_mtime
    except OSError:
        pass
    content = BUILDS / f'{key}.content.json'
    authored = _content_authored(content)
    if authored:
        try:
            saved_at = max(saved_at, content.stat().st_mtime)
        except OSError:
            pass
    return {
        'scaffolded': content.exists() and not authored,
        'slug': slug, 'folder': folder, 'root_slug': root,
        'preview_url': (f'/preview/{slug}/' if slug else None),
        'live_url': (f'https://manaoke.app/songs/{slug}/' if slug else None),
        'saved_at': int(saved_at) or None,
        'edited': edited, 'built': built, 'pushed': pushed, 'promoted': promoted,
        'has_corpus': _ytsim_audio(slug) is not None if slug else False,
    }


def _romuid(rom):
    """Mirror of the song page's _romUid — content-word rom → clip uid, so a
    study word joins to songs/_assets/<folder>/audio/jp/word_<sec>_<uid>.mp3."""
    return (rom or '').replace(' ', '-').replace('·', '').replace('/', '_').strip('-')


def _study_index(key):
    """content.json → (sec_by_id, line_sections, line_words). Study words carry
    the teaching (rom, en, gloss, jp_speak); timing tokens don't. line_words[i] is
    the study words that belong to content line i — assigned by greedily consuming
    each section's words (in reading order) into the lines that contain them, so a
    reused surface (three distinct に in one section) maps to the right line
    instead of all three landing on every に token. Returns (None, [], []) with no
    content.json (e.g. mid-pipeline)."""
    try:
        c = json.loads((BUILDS / f'{key}.content.json').read_text())
    except Exception:
        return None, [], []
    sec_by_id = {}
    for s in (c.get('sections') or []):
        sec_by_id[s.get('id')] = {'id': s.get('id'), 'name': s.get('name') or '',
                                  'short_name': s.get('short_name') or ''}
    clines = c.get('lines') or []
    line_sections = [ln.get('section') for ln in clines]
    # Study words live in ONE of two content layouts (per song): a flat top-level
    # `words[]` each carrying its own `section` (shinunoga/odoriko/headlong/
    # silhouette2), OR nested under `sections[].words` (ema/inochi). Read BOTH,
    # stamping the section id on nested words — else nested-layout songs show
    # "no study word" for every token though the teaching exists.
    words_by_sec = {}
    for w in (c.get('words') or []):
        words_by_sec.setdefault(w.get('section'), []).append(w)
    for s in (c.get('sections') or []):
        sid = s.get('id')
        for w in (s.get('words') or []):
            if not w.get('section'):
                w = dict(w); w['section'] = sid
            words_by_sec.setdefault(w.get('section'), []).append(w)
    line_words = [[] for _ in clines]
    cursor = {}                          # per-section consumption pointer
    for i, cln in enumerate(clines):
        sec = cln.get('section')
        jp = cln.get('jp') or ''
        words = words_by_sec.get(sec) or []
        cur = cursor.get(sec, 0)
        pos = 0
        while cur < len(words):
            wjp = (words[cur].get('jp') or '').strip()
            if not wjp:
                cur += 1
                continue
            idx = jp.find(wjp, pos)
            if idx < 0:
                break                    # not in the rest of THIS line → a later line owns it
            line_words[i].append(words[cur])
            pos = idx + len(wjp)
            cur += 1
        cursor[sec] = cur
    return sec_by_id, line_sections, line_words


def _study_for_token(token, line_study, folder, prov, lex):
    """Every study word (from THIS line's own study words) contained in this timing
    token — a token like ホラでも holds ホラ + でも. Joined to its clip +
    provenance + pin. Best-effort: coverage is 44-100% song-by-song, so [] is
    normal and the UI shows 'no study data'. Ordered by position within the token."""
    tok = (token or '').strip()
    if not tok or not line_study:
        return []
    out = []
    for w in line_study:
        jp = (w.get('jp') or '').strip()
        sec_id = w.get('section')
        if not jp or not sec_id:
            continue
        if jp == tok or (jp in tok) or (tok in jp and len(tok) >= 2):
            uid = _romuid(w.get('rom') or '')
            rel = f'jp/word_{sec_id}_{uid}.mp3'
            fp = ASSETS / folder / 'audio' / rel
            p = prov.get(rel)
            lex_entry = lex.get(_fold_kana(w.get('jp_speak') or jp))
            out.append({
                'jp': jp, 'rom': w.get('rom') or '', 'en': w.get('en') or '',
                'gloss': w.get('gloss') or '', 'particle': bool(w.get('particle')),
                'uid': uid, 'sec': sec_id,
                'clip_url': f'/assets/{folder}/audio/{rel}',
                'clip_exists': fp.is_file(),
                'provenance': ({'source': p.get('source')} if p else None),
                'pinned': lex_entry is not None,
                'pos': tok.find(jp),
            })
    out.sort(key=lambda d: (d['pos'] if d['pos'] >= 0 else 1 << 20, -len(d['jp'])))
    return out


# ── kana → romaji (for the reveal's per-part romaji highlight) ───────────
# When one study word spans several timing tokens (かけ+てく share かけてく /
# "kaketeku"), each token's reveal chip should light ITS slice of the romaji.
# Stdlib-only hepburn table; a token that isn't pure kana (or any mismatch
# vs the study rom) just yields no highlight — never a wrong one.

_K2R = {
    'きゃ': 'kya', 'きゅ': 'kyu', 'きょ': 'kyo', 'しゃ': 'sha', 'しゅ': 'shu',
    'しょ': 'sho', 'ちゃ': 'cha', 'ちゅ': 'chu', 'ちょ': 'cho', 'にゃ': 'nya',
    'にゅ': 'nyu', 'にょ': 'nyo', 'ひゃ': 'hya', 'ひゅ': 'hyu', 'ひょ': 'hyo',
    'みゃ': 'mya', 'みゅ': 'myu', 'みょ': 'myo', 'りゃ': 'rya', 'りゅ': 'ryu',
    'りょ': 'ryo', 'ぎゃ': 'gya', 'ぎゅ': 'gyu', 'ぎょ': 'gyo', 'じゃ': 'ja',
    'じゅ': 'ju', 'じょ': 'jo', 'びゃ': 'bya', 'びゅ': 'byu', 'びょ': 'byo',
    'ぴゃ': 'pya', 'ぴゅ': 'pyu', 'ぴょ': 'pyo', 'ふぁ': 'fa', 'ふぃ': 'fi',
    'ふぇ': 'fe', 'ふぉ': 'fo', 'てぃ': 'ti', 'でぃ': 'di', 'うぃ': 'wi',
    'うぇ': 'we', 'うぉ': 'wo', 'とぅ': 'tu', 'どぅ': 'du', 'ゔぁ': 'va',
    'ゔぃ': 'vi', 'ゔぇ': 've', 'ゔぉ': 'vo', 'しぇ': 'she', 'ちぇ': 'che',
    'じぇ': 'je',
    'あ': 'a', 'い': 'i', 'う': 'u', 'え': 'e', 'お': 'o',
    'か': 'ka', 'き': 'ki', 'く': 'ku', 'け': 'ke', 'こ': 'ko',
    'が': 'ga', 'ぎ': 'gi', 'ぐ': 'gu', 'げ': 'ge', 'ご': 'go',
    'さ': 'sa', 'し': 'shi', 'す': 'su', 'せ': 'se', 'そ': 'so',
    'ざ': 'za', 'じ': 'ji', 'ず': 'zu', 'ぜ': 'ze', 'ぞ': 'zo',
    'た': 'ta', 'ち': 'chi', 'つ': 'tsu', 'て': 'te', 'と': 'to',
    'だ': 'da', 'ぢ': 'ji', 'づ': 'zu', 'で': 'de', 'ど': 'do',
    'な': 'na', 'に': 'ni', 'ぬ': 'nu', 'ね': 'ne', 'の': 'no',
    'は': 'ha', 'ひ': 'hi', 'ふ': 'fu', 'へ': 'he', 'ほ': 'ho',
    'ば': 'ba', 'び': 'bi', 'ぶ': 'bu', 'べ': 'be', 'ぼ': 'bo',
    'ぱ': 'pa', 'ぴ': 'pi', 'ぷ': 'pu', 'ぺ': 'pe', 'ぽ': 'po',
    'ま': 'ma', 'み': 'mi', 'む': 'mu', 'め': 'me', 'も': 'mo',
    'や': 'ya', 'ゆ': 'yu', 'よ': 'yo',
    'ら': 'ra', 'り': 'ri', 'る': 'ru', 'れ': 're', 'ろ': 'ro',
    'わ': 'wa', 'ゐ': 'wi', 'ゑ': 'we', 'を': 'wo', 'ん': 'n', 'ゔ': 'vu',
}
# hepburn spelling variants a study rom might use for the same syllable
_R_VAR = {'shi': ('si',), 'chi': ('ti',), 'tsu': ('tu',), 'fu': ('hu',),
          'ji': ('zi', 'di'), 'zu': ('du',), 'wo': ('o',), 'n': ('m', "n'")}


def _kana_syls(tok):
    """Token text → list of romaji syllables, or None when it isn't pure
    kana. っ = gemination marker ('*'), ー/small-vowels = long-vowel ('~')."""
    s = ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in tok)
    out, i = [], 0
    while i < len(s):
        two = s[i:i + 2]
        if two in _K2R:
            out.append(_K2R[two]); i += 2; continue
        c = s[i]
        if c == 'っ':
            out.append('*'); i += 1; continue
        if c == 'ー' or c in 'ぁぃぅぇぉ':
            out.append('~'); i += 1; continue
        if c in _K2R:
            out.append(_K2R[c]); i += 1; continue
        return None
    return out


def _rom_consume(rom, syls, start):
    """Match syllables against rom (the study word's romaji) from `start`.
    Returns the end index, or None on any mismatch. Separators (space, -, ·,
    ') between syllables are consumed. Tolerant of hepburn variants, ん→m,
    gemination (っ = next consonant doubled) and long vowels (ー = the
    previous vowel again, possibly macroned)."""
    MACRON = {'ā': 'a', 'ī': 'i', 'ū': 'u', 'ē': 'e', 'ō': 'o'}
    i = start
    for si, syl in enumerate(syls):
        while i < len(rom) and rom[i] in " -·'":
            i += 1
        low = rom[i:].lower()
        if syl == '*':                          # gemination: next consonant char
            nxt = syls[si + 1] if si + 1 < len(syls) else ''
            cons = (nxt[:1] if nxt and nxt[0] not in 'aiueo~*' else '')
            if cons and low.startswith(cons):
                i += 1
            elif cons == 'c' and low.startswith('t'):   # っち → tchi
                i += 1
            else:
                return None
            continue
        if syl == '~':                          # long vowel: repeat/macron
            prev = rom[i - 1:i].lower()
            if low[:1] == prev and prev in 'aiueo':
                i += 1
            elif rom[i - 1:i] in MACRON:        # already absorbed by a macron
                pass
            else:
                return None
            continue
        cands = (syl,) + _R_VAR.get(syl, ())
        hit = next((c for c in cands if low.startswith(c)), None)
        if hit is None and len(syl) >= 2 and syl[-1] in 'aiueo':
            # macron absorption: 'ko'+'~' rendered as 'kō'
            base = syl[:-1]
            if low.startswith(base) and rom[i + len(base):i + len(base) + 1] in MACRON:
                i += len(base) + 1
                continue
        if hit is None:
            return None
        i += len(hit)
    return i


# romaji → hiragana (reverse hepburn). Many word_meta entries carry the KANJI
# surface in their kana field (jp_speak repeated the kanji), which made every
# reading-keyed human tier (NHK corpus / Tofugu / JPod101) MISS — the "only my
# TTS voice shows up" bug. The rom uid always exists, so derive the reading
# from it. Returns '' when a token can't be parsed (caller keeps the old kana).
_R2K = {
    'kya': 'きゃ', 'kyu': 'きゅ', 'kyo': 'きょ', 'gya': 'ぎゃ', 'gyu': 'ぎゅ',
    'gyo': 'ぎょ', 'sha': 'しゃ', 'shu': 'しゅ', 'sho': 'しょ', 'cha': 'ちゃ',
    'chu': 'ちゅ', 'cho': 'ちょ', 'nya': 'にゃ', 'nyu': 'にゅ', 'nyo': 'にょ',
    'hya': 'ひゃ', 'hyu': 'ひゅ', 'hyo': 'ひょ', 'mya': 'みゃ', 'myu': 'みゅ',
    'myo': 'みょ', 'rya': 'りゃ', 'ryu': 'りゅ', 'ryo': 'りょ', 'bya': 'びゃ',
    'byu': 'びゅ', 'byo': 'びょ', 'pya': 'ぴゃ', 'pyu': 'ぴゅ', 'pyo': 'ぴょ',
    'shi': 'し', 'chi': 'ち', 'tsu': 'つ', 'she': 'しぇ', 'che': 'ちぇ',
    'ka': 'か', 'ki': 'き', 'ku': 'く', 'ke': 'け', 'ko': 'こ',
    'ga': 'が', 'gi': 'ぎ', 'gu': 'ぐ', 'ge': 'げ', 'go': 'ご',
    'sa': 'さ', 'su': 'す', 'se': 'せ', 'so': 'そ',
    'za': 'ざ', 'ji': 'じ', 'zu': 'ず', 'ze': 'ぜ', 'zo': 'ぞ',
    'ta': 'た', 'te': 'て', 'to': 'と', 'da': 'だ', 'de': 'で', 'do': 'ど',
    'na': 'な', 'ni': 'に', 'nu': 'ぬ', 'ne': 'ね', 'no': 'の',
    'ha': 'は', 'hi': 'ひ', 'fu': 'ふ', 'he': 'へ', 'ho': 'ほ',
    'ba': 'ば', 'bi': 'び', 'bu': 'ぶ', 'be': 'べ', 'bo': 'ぼ',
    'pa': 'ぱ', 'pi': 'ぴ', 'pu': 'ぷ', 'pe': 'ぺ', 'po': 'ぽ',
    'ma': 'ま', 'mi': 'み', 'mu': 'む', 'me': 'め', 'mo': 'も',
    'ya': 'や', 'yu': 'ゆ', 'yo': 'よ',
    'ra': 'ら', 'ri': 'り', 'ru': 'る', 're': 'れ', 'ro': 'ろ',
    'wa': 'わ', 'wo': 'を', 'ja': 'じゃ', 'ju': 'じゅ', 'jo': 'じょ',
    'fa': 'ふぁ', 'fi': 'ふぃ', 'fe': 'ふぇ', 'fo': 'ふぉ',
    'ti': 'てぃ', 'di': 'でぃ', 'tu': 'とぅ', 'du': 'どぅ',
    'va': 'ゔぁ', 'vi': 'ゔぃ', 've': 'ゔぇ', 'vo': 'ゔぉ', 'vu': 'ゔ',
    'wi': 'うぃ', 'we': 'うぇ',
    'a': 'あ', 'i': 'い', 'u': 'う', 'e': 'え', 'o': 'お', 'n': 'ん',
}
_MACRON2 = {'ā': 'aa', 'ī': 'ii', 'ū': 'uu', 'ē': 'ee', 'ō': 'ou'}
_HAN = re.compile(r'[一-鿿]')


def _kana_from_rom(rom):
    s = ''.join(_MACRON2.get(c, c) for c in (rom or '').lower())
    s = re.sub(r"[ \-·']", '', s)
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if not c.isalpha():
            return ''
        # ん before a consonant (or at the end); "n'" is an explicit ん
        if c == 'n' and (i + 1 >= len(s) or s[i + 1] not in 'aiueoy'):
            out.append('ん'); i += 1; continue
        # gemination: a doubled consonant (kitto) / tch (matcha) → っ
        if c not in 'aiueon' and i + 1 < len(s) \
                and (s[i + 1] == c or (c == 't' and s[i + 1:i + 3] == 'ch')):
            out.append('っ'); i += 1; continue
        hit = next((L for L in (3, 2, 1) if s[i:i + L] in _R2K), None)
        if hit is None:
            return ''
        out.append(_R2K[s[i:i + hit]]); i += hit
    return ''.join(out)


def _kana_candidates(rom):
    """Possible readings for a rom, best-guess first. Hepburn ō/ē are
    ambiguous (おう vs おお, えい vs ええ) — offer both so a dictionary-guarded
    lookup (surface must ALSO match) can pick the true one. Readings the
    dictionaries reject just miss, never mis-hit."""
    base = _kana_from_rom(rom)
    if not base:
        return []
    out = [base]
    if 'ō' in (rom or '').lower():
        alt = _kana_from_rom((rom or '').lower().replace('ō', 'oo'))
        if alt and alt not in out:
            out.append(alt)
    if 'ē' in (rom or '').lower():
        alt = _kana_from_rom((rom or '').lower().replace('ē', 'ee'))
        if alt and alt not in out:
            out.append(alt)
    return out


def _effective_kana(m):
    """The reading to hunt/synthesize with: word_meta kana when it IS kana,
    else derived from the rom uid (many entries repeat the KANJI in the kana
    field — the 'only my TTS voice' bug). Shared by audition AND push so the
    take that was auditioned is the take the read-back gate expects."""
    kana = m.get('kana') or ''
    if kana and not _HAN.search(kana):
        return kana
    derived = _kana_from_rom(m.get('rom') or '')
    return derived or kana


def _attach_rom_ranges(words):
    """For each study word that spans SEVERAL timing tokens on this line,
    stamp each token's study entry with rom_hl=[start,end) — its slice of the
    study rom. Silent no-op on anything that doesn't match cleanly."""
    groups = {}
    for wi, w in enumerate(words):
        tok = (w.get('text') or '').strip()
        for s in (w.get('study') or []):
            if s.get('uid') and s.get('jp') and s['jp'] != tok:
                groups.setdefault((s['uid'], s.get('sec')), []).append((wi, s))
    for (_uid, _sec), members in groups.items():
        if len(members) < 2:
            continue
        jp = members[0][1].get('jp') or ''
        rom = members[0][1].get('rom') or ''
        if not jp or not rom:
            continue
        # a repeated multi-token word (かけ てく … かけ てく) shows up as ONE
        # group — split it into consecutive runs whose token texts rebuild jp,
        # and range each run independently
        run = []
        acc = ''
        for wi, s in members:
            tok = (words[wi].get('text') or '').strip()
            if not (jp[len(acc):].startswith(tok)):
                run, acc = [], ''            # desync — restart at this member
                if not jp.startswith(tok):
                    continue
            run.append((wi, s))
            acc += tok
            if acc != jp:
                continue
            toks = [(words[w].get('text') or '').strip() for w, _ in run]
            syl_lists = [_kana_syls(t) for t in toks]
            if all(sl for sl in syl_lists):
                cur = 0
                ranges = []
                ok = True
                for sl in syl_lists:
                    while cur < len(rom) and rom[cur] in " -·'":
                        cur += 1
                    end = _rom_consume(rom, sl, cur)
                    if end is None:
                        ok = False
                        break
                    ranges.append([cur, end])
                    cur = end
                while cur < len(rom) and rom[cur] in " -·'":
                    cur += 1
                if ok and cur == len(rom):
                    for (w, s2), rng in zip(run, ranges):
                        s2['rom_hl'] = rng
            run, acc = [], ''


def _timing_payload(key):
    """GET /api/timing/{key}: every line of builds/<key>.lyrics.json with
    duration/mora-rate, E16-style flags, its words, and (when LRCLIB knows the
    song) the matched source begin + residual vs the median sync offset."""
    lyr_path = BUILDS / f'{key}.lyrics.json'
    if not lyr_path.exists():
        return None
    doc = json.loads(lyr_path.read_text())
    lines = doc.get('lines') or []
    rows = _lrclib_rows_cached(key, doc)
    lrc_begins, median = timing_edit.match_lrclib(lines, rows)

    # sidecar overrides (builds/<key>.timing_overrides.json): which lines and
    # words carry a HUMAN decision — the UI renders those pinned (⚓) so the
    # operator can tell hand-set times from aligner output at a glance.
    ov_lines, ov_words = set(), {}
    try:
        resolved, _orph = timing_overrides.resolve(
            lines, [e for e in (timing_overrides.load(key).get('entries') or [])
                    if (e.get('scope') or '') in ('line', 'word')])
        for li, e in resolved:
            if e.get('word_idx') is None:
                ov_lines.add(li)
            else:
                ov_words.setdefault(li, set()).add(int(e['word_idx']))
    except Exception:
        pass                      # a broken sidecar must never 500 the tab

    # identical-text duration groups for the repeat-consistency flag
    durs_by_text = {}
    for ln in lines:
        if not validate_song.is_lyric_line(ln):
            continue
        k = validate_song.line_tr_key(ln.get('text') or '')
        if k:
            durs_by_text.setdefault(k, []).append(
                max(int(ln['end_ms']) - int(ln['begin_ms']), 1))
    flagged_repeat = {k for k, ds in durs_by_text.items()
                      if len(ds) > 1 and max(ds) / min(ds) > 2.5}

    # section + study-word context: content.json carries the teaching (rom, en,
    # gloss) and the per-line section id; the timing file has neither. Line↔
    # section joins by index (assemble pairs authored/timed lines in exact
    # order), with a normalized-text fallback for safety.
    folder = _asset_folder(key) or key
    sec_by_id, line_sections, line_words = _study_index(key)
    try:
        prov = json.loads((BUILDS / f'{key}.clip_provenance.json').read_text())
    except Exception:
        prov = {}
    try:
        lex = json.loads(LEXICON_PATH.read_text()).get('words', {})
    except Exception:
        lex = {}
    content_by_norm = {}
    content_kana = {}                      # content-line index → full hiragana reading
    if line_sections and sec_by_id is not None:
        try:
            c = json.loads((BUILDS / f'{key}.content.json').read_text())
            for ci, cln in enumerate(c.get('lines') or []):
                nk = validate_song.line_tr_key(cln.get('jp') or '')
                if nk:
                    content_by_norm.setdefault(nk, ci)
                content_kana[ci] = (cln.get('kana') or '').strip()
        except Exception:
            pass

    def _content_idx(i, text):
        """The content-line index for timing line i (index join primary, then
        normalized-text fallback) — or None."""
        if sec_by_id is not None and i < len(line_sections):
            return i
        if content_by_norm:
            return content_by_norm.get(validate_song.line_tr_key(text))
        return None

    def _section_for(ci):
        if ci is None or sec_by_id is None or not (0 <= ci < len(line_sections)):
            return None
        sid = line_sections[ci]
        return sec_by_id.get(sid) if sid else None

    out_lines = []
    for i, ln in enumerate(lines):
        text = ln.get('text') or ''
        b, e = int(ln.get('begin_ms') or 0), int(ln.get('end_ms') or 0)
        dur = e - b
        morae = _e16_morae(text)
        rate = round(morae / (dur / 1000.0), 2) if (morae and dur > 0) else None
        flags = []
        if dur > 20000:
            flags.append('line>20s')
        if any(int(w.get('end_ms') or 0) - int(w.get('begin_ms') or 0) > 4000
               for w in (ln.get('words') or [])):
            flags.append('token>4s')
        if rate is not None and E16_JP.search(text) and not (1 <= rate <= 14):
            flags.append('mora-rate')
        if validate_song.line_tr_key(text) in flagged_repeat:
            flags.append('repeat-ratio>2.5')
        lb = lrc_begins[i]
        residual = (b - (lb + median)) if (lb is not None and median is not None) else None
        wset = ov_words.get(i) or set()
        ci = _content_idx(i, text)
        section = _section_for(ci)
        line_study = (line_words[ci] if (ci is not None and 0 <= ci < len(line_words))
                      else [])
        out_words = [{'text': w.get('text') or '',
                      'begin_ms': int(w.get('begin_ms') or 0),
                      'end_ms': int(w.get('end_ms') or 0),
                      'hold_ms': (int(w['hold_ms'])
                                  if w.get('hold_ms') is not None else None),
                      'kana': w.get('kana') or None,   # human reading override
                      'override': wi in wset,
                      'study': _study_for_token(w.get('text') or '', line_study,
                                                folder, prov, lex)}
                     for wi, w in enumerate(ln.get('words') or [])]
        _attach_rom_ranges(out_words)      # multi-token study words → per-part rom
        out_lines.append({
            'i': i, 'text': text, 'begin_ms': b, 'end_ms': e, 'dur_ms': dur,
            'morae': morae, 'mora_rate': rate, 'flags': flags,
            'override': 'line' if i in ov_lines else None,
            'section': section,
            'kana': content_kana.get(ci, ''),   # full hiragana reading (kanji help)
            'translation': ln.get('translation') or '',
            'words': out_words,
            'sources': {'lrclib': {'begin_ms': lb} if lb is not None else None},
            'residual_ms': residual,
        })
    slug = _build_slug(key)
    song = doc.get('song') or {}
    return {
        'key': key, 'slug': slug, 'lines': out_lines,
        'median_delta_ms': median,
        'duration_ms': int(song['duration_ms']) if song.get('duration_ms') else None,
        # set by the lyric fetch when the only sheet it could get belongs to a
        # DIFFERENT recording (a live take, an instrumental). The lines below
        # will not sit on this video and no amount of nudging fixes that, so the
        # tab has to say it out loud.
        'variant_warning': song.get('variant_warning') or '',
        'state': _song_state(key),
    }


# ── words read model + audition (addendum §Words tab) ────────────────────

def _read_build_state(key):
    try:
        return json.loads((BUILDS / f'{key}.build_state.json').read_text())
    except Exception:
        return None


def _build_slug(key):
    st = _read_build_state(key)
    return (st or {}).get('slug') or ''


def _asset_folder(key):
    """manaoke_build._folder: the songs/_assets/<folder> name for a build."""
    st = _read_build_state(key)
    if st is None:
        return None
    return st.get('meta', {}).get('slug') or st.get('key')


_FOLD_SMALL = str.maketrans('ぁぃぅぇぉ', 'あいうえお')
_STRIP_KANA = re.compile(r'[。、，．！？!?,.\sーっッ]')


def _fold_kana(kana):
    """The pronunciation-lexicon key form (mirror of gen_audio._fold_kana)."""
    return _STRIP_KANA.sub('', kana or '').translate(_FOLD_SMALL)


WORD_REL = re.compile(r'^jp/word_([^_]+)_(.+)\.mp3$')

# Lone particles TTS engines routinely mangle (clipped vowels, hallucinated
# syllables) — a clip for one of these is suspect unless a human/curated or
# NHK-corpus source spoke it.
SUSPECT_PARTICLES = set('にとものがでやかねよなへをはぞさお')


def _word_suspect(surface, source):
    """Words-tab triage: (suspect, plain-English why). Heuristic only — it
    surfaces clips worth the operator's EAR, it never blocks anything:
    fallback-voice provenance (qwen/aivis carrier cuts), a kokoro dictionary
    miss, or a lone particle nothing human/curated spoke."""
    src = source or ''
    if src.startswith('qwen') or src.startswith('aivis') or src.startswith('google'):
        return True, 'a fallback voice spoke this — worth a listen'
    if src == 'kokoro_dictmiss':
        return True, 'the standard voice had to guess this reading'
    if surface in SUSPECT_PARTICLES and not (
            src.startswith('curated') or src.startswith('nhk')):
        return True, 'a lone particle — synthetic voices often mangle these'
    return False, ''


def _word_entries(key):
    """word_meta rows parsed + joined: [(out_rel, sec, uid, meta)], ordered by
    the slug dir's tts_manifest (the page's own order), word_meta-only strays
    last. Returns (entries, folder, slug) or (None, err_msg, None)."""
    slug = _build_slug(key)
    folder = _asset_folder(key)
    if not folder:
        return None, f'no build state for {key!r}', None
    try:
        word_meta = json.loads((BUILDS / f'{key}.word_meta.json').read_text())
    except Exception as e:
        return None, f'no word_meta for {key!r} ({e})', None
    manifest_order = {}
    try:
        manifest = json.loads((ROOT / 'songs' / slug / 'tts_manifest.json').read_text())
        for pos, entry in enumerate(manifest):
            path = entry[3] if len(entry) > 3 else ''
            if path.startswith('audio/'):
                manifest_order.setdefault(path[len('audio/'):], pos)
    except Exception:
        pass  # no built slug dir yet — word_meta order stands
    entries = []
    for out_rel, m in word_meta.items():
        mt = WORD_REL.match(out_rel)
        if not mt:
            continue
        entries.append((out_rel, mt.group(1), mt.group(2), m))
    entries.sort(key=lambda t: (manifest_order.get(t[0], 1 << 30),))
    return entries, folder, slug


def _words_payload(key):
    """GET /api/words/{key}: every ja word clip with its serving url, on-disk
    existence, provenance, and lexicon pin state."""
    entries, folder, slug = _word_entries(key)
    if entries is None:
        return None, folder
    try:
        prov = json.loads((BUILDS / f'{key}.clip_provenance.json').read_text())
    except Exception:
        prov = {}
    try:
        lex_words = json.loads(LEXICON_PATH.read_text()).get('words', {})
    except Exception:
        lex_words = {}
    # Acoustic sweep verdicts (sweep_clip_physics.py -> validate E19): a clip
    # physics calls 'fail' or 'suspect' joins the ear strip REGARDLESS of
    # provenance source — curated clips can be marginal too. Keyed by the
    # asset folder, like the sweep and the validator.
    try:
        phys = json.loads((BUILDS / f'{folder}.clip_suspects.json')
                          .read_text()).get('clips', {})
    except Exception:
        phys = {}
    words = []
    for out_rel, sec, uid, m in entries:
        p = prov.get(out_rel)
        kana = m.get('kana') or ''
        surface = m.get('surface') or ''
        source = (p or {}).get('source')
        lex_entry = lex_words.get(_fold_kana(kana))
        suspect, why = _word_suspect(surface, source)
        ph = phys.get(out_rel)
        if ph and ph.get('verdict') != 'pass':
            # only surface a verdict that still describes the SERVED bytes —
            # right after a Words-tab fix the sidecar is stale until the next
            # sweep, and accusing a just-fixed clip reads as the fix failing
            fpath = ASSETS / folder / 'audio' / out_rel
            try:
                fresh = hashlib.sha256(fpath.read_bytes()).hexdigest()[:8] == ph.get('sha8')
            except OSError:
                fresh = False
            if fresh:
                phys_why = '; '.join(ph.get('reasons') or []) or 'flagged by clip physics'
                if ph.get('verdict') == 'fail':
                    phys_why = 'physics FAIL: ' + phys_why
                suspect, why = True, (f'{why} · {phys_why}' if why else phys_why)
        words.append({
            'uid': uid,
            'sec': sec,
            'surface': surface,
            'kana': kana,
            'rom': m.get('rom') or '',
            'file': f'/assets/{folder}/audio/{out_rel}',
            'exists': (ASSETS / folder / 'audio' / out_rel).is_file(),
            'provenance': ({'source': p.get('source'), 'sha8': p.get('sha8')}
                           if p else None),
            'pinned': lex_entry is not None,
            'suspect': suspect,
            'suspect_why': why,
            'lex_reason': (lex_entry or {}).get('reason') or '',
        })
    return {'key': key, 'slug': slug, 'words': words}, None


def _safe_uid(uid):
    """Audition dir names come from rom uids — refuse anything path-shaped."""
    return bool(uid) and bool(re.fullmatch(r'[A-Za-z0-9._-]+', uid)) \
        and '..' not in uid


# install_word.py grows a --chain flag in a sibling effort; sniff its argparse
# at push time (cached by mtime+size — a stat() per push, one read per file
# change) so the UI picks the flag up the moment it lands, no coordination.
_CHAIN_RE = re.compile(r'add_argument\(\s*[\'"]--chain[\'"]')
_CHAIN_CACHE = {'sig': None, 'has': False}


def _install_word_has_chain():
    try:
        stt = INSTALL_WORD.stat()
        sig = (stt.st_mtime, stt.st_size)
    except OSError:
        return False
    if _CHAIN_CACHE['sig'] != sig:
        try:
            _CHAIN_CACHE['has'] = bool(_CHAIN_RE.search(INSTALL_WORD.read_text()))
        except OSError:
            _CHAIN_CACHE['has'] = False
        _CHAIN_CACHE['sig'] = sig
    return _CHAIN_CACHE['has']


def _audition_candidates(key, uid):
    """POST /api/word/audition: gather candidate takes for one word into
    builder/auditions/<key>/<uid>/ and return served urls. ≤10s budget,
    partial results fine. Sources, in order (addendum §Words tab):
    current installed clip (+ its .bak siblings — previous takes, so a bad
    re-install can be rolled back by ear) → library cache (dual-form read:
    '<surface>__<kana>.mp3' then bare '<surface>.mp3') → local NHK/yomichan
    corpus, one candidate per tier that resolves (nhk / shinmeikai / forvo /
    jpod — corpus.py, homophone-safe) → Tofugu offline corpus → JPod101
    online (skipped when the library or the local jpod tier already has the
    word — those ARE that download). First corpus call in a server process
    builds its indexes (~1-2s, then cached in-process — well inside the
    budget). LAST: generated takes via Google Cloud TTS (3 ja-JP Neural2
    voices, keyed from .env) — the only tier that exists for conjugated /
    colloquial forms no dictionary recorded; a note entry says so whenever
    every human tier missed."""
    deadline = time.time() + 10.0
    entries, folder, _slug = _word_entries(key)
    if entries is None:
        return None, folder
    match = next(((rel, sec, u, m) for rel, sec, u, m in entries if u == uid), None)
    if match is None:
        return None, f'no word with uid {uid!r} in {key}.word_meta.json'
    out_rel, sec, _u, m = match
    surface = m.get('surface') or ''
    # word_meta often repeats the KANJI in its kana field — every reading-keyed
    # human tier then misses ("only my TTS voice"). _effective_kana derives the
    # true reading from the rom uid; _kana_candidates adds the ō/ē-ambiguity
    # variants for the dictionary-guarded lookups.
    kana = _effective_kana(m)
    kana_variants = [kana] if (m.get('kana') and kana == m.get('kana')) \
        else (_kana_candidates(m.get('rom') or '') or [kana])
    dest = AUDITIONS / key / uid
    dest.mkdir(parents=True, exist_ok=True)
    cands = []

    def add(fname, src_path, label, source):
        try:
            shutil.copyfile(src_path, dest / fname)
        except OSError:
            return
        cands.append({'label': label, 'url': f'/auditions/{key}/{uid}/{fname}',
                      'source': source})

    # 1. current installed clip
    cur = ASSETS / folder / 'audio' / out_rel
    if cur.is_file():
        try:
            prov = json.loads((BUILDS / f'{key}.clip_provenance.json').read_text())
            src = (prov.get(out_rel) or {}).get('source') or 'unknown'
        except Exception:
            src = 'unknown'
        add('current.mp3', cur, f'current · {src}', 'current')

    # 1b. previous takes: .bak siblings of the served mp3 (install_word.py
    #     leaves word_<sec>_<rom>.mp3.bak behind on a mid-install crash, and a
    #     repair pass may park older takes the same way). Newest first.
    baks = sorted(cur.parent.glob(cur.name + '.bak*'),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for n, bak in enumerate(baks, 1):
        add(f'previous{n}.mp3', bak, f'previous · {n}', 'previous')

    # human_audio importables (fetch / tofugu share the library/ slug scheme)
    if str(HUMAN) not in sys.path:
        sys.path.insert(0, str(HUMAN))
    import fetch as ha_fetch
    import tofugu as ha_tofugu

    # 2. library cache hit — dual-form read (fetch.library_lookup): the
    #    committed library holds BOTH '<surface>__<kana>.mp3' and bare
    #    '<surface>.mp3' name forms; slug()-only lookup missed の__の.mp3.
    lib = next((p for kv in kana_variants
                for p in [ha_fetch.library_lookup(surface, kv)] if p), None)
    lib_hit = bool(lib)
    if lib_hit:
        add('library.mp3', lib, f'library · {Path(lib).name}', 'library')

    # 2b. local NHK/yomichan corpus (tools/human_audio/corpus.py) — one
    #     candidate per source tier that resolves, homophone-safe, offline.
    #     Loaded by explicit path (a bare `import corpus` could bind the
    #     tools/songcraft/corpus/ wav dir as a namespace package) and memoized
    #     in sys.modules so the ~1-2s index build happens once per process.
    corpus_jpod_hit = False
    if time.time() < deadline:
        try:
            ha_corpus = sys.modules.get('manaoke_ha_corpus')
            if ha_corpus is None:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    'manaoke_ha_corpus', str(HUMAN / 'corpus.py'))
                ha_corpus = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(ha_corpus)
                sys.modules['manaoke_ha_corpus'] = ha_corpus
            tiers = []
            for kv in kana_variants:
                tiers = ha_corpus.resolve_all(surface, kv)
                if tiers:
                    kana = kv          # dictionary-confirmed reading wins
                    break
        except (Exception, SystemExit) as ex:
            tiers = []                 # corpus absent/unreadable — skip the tier
            print(f'[audition] corpus tier skipped: {type(ex).__name__}: {ex}',
                  file=sys.stderr)
        short_names = {'nhk16': 'nhk', 'shinmeikai8': 'shinmeikai',
                       'forvo': 'forvo', 'jpod': 'jpod'}
        for source, path, note in tiers:
            short = short_names.get(source, source)
            add(f'{short}.mp3', path, f'{short} · {note}', short)
            if source == 'jpod':
                corpus_jpod_hit = True

    # 3. Tofugu offline corpus (resolve only — fetch_one would write into the
    #    committed library/; auditioning must not). index() sys.exits when the
    #    corpus dir is missing, hence SystemExit in the guard.
    if time.time() < deadline and surface not in ha_tofugu.BAD_PARTICLES:
        try:
            path, note = None, ''
            for kv in kana_variants:
                path, note = ha_tofugu.resolve(surface, kv)
                if path:
                    break
        except (Exception, SystemExit):
            path, note = None, ''
        if path:
            add('tofugu.mp3', path,
                f'tofugu · {note} · speaker varies — spot-listen', 'tofugu')
        elif note.startswith('AMBIG'):
            cands.append({'label': f'tofugu · {note} (no clip — ambiguous reading)',
                          'url': None, 'source': 'tofugu'})

    # 4. JPod101 online (fetch.py's attempt chain + miss fingerprint, written
    #    into the audition dir, NOT library/). Skipped on a library hit or a
    #    local-jpod corpus hit — those cached files ARE this same download.
    remaining = deadline - time.time()
    if not lib_hit and not corpus_jpod_hit and remaining > 1.0 \
            and surface not in ha_fetch.BAD_PARTICLES:
        attempts = []
        for kv in kana_variants:
            if kv:
                attempts.append({'kanji': surface, 'kana': kv})
        attempts.append({'kanji': surface})
        for kv in kana_variants:
            if kv and kv != surface:
                attempts.append({'kana': kv})
        for params in attempts:
            remaining = deadline - time.time()
            if remaining <= 0.5:
                break
            url = ha_fetch.EP + '?' + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=ha_fetch.UA)
            try:
                data = urllib.request.urlopen(req, timeout=min(4.0, remaining)).read()
            except Exception:
                break                      # timeout/network — skip the source
            if not data or hashlib.md5(data).hexdigest() == ha_fetch.MISS_MD5:
                continue
            (dest / 'jpod101.mp3').write_bytes(data)
            cands.append({'label': 'jpod101',
                          'url': f'/auditions/{key}/{uid}/jpod101.mp3',
                          'source': 'jpod101'})
            break

    # 5. GENERATED takes — Google Cloud TTS (the podcast engine's key in
    #    .env). Dictionaries only carry base forms, so conjugated/colloquial
    #    words often have NO human recording anywhere — these give the ear
    #    real options beyond the one standard voice. Synth from the KANA (a
    #    kanji surface invites a wrong reading). Cached in the audition dir
    #    so re-opening a word never re-bills. Round-14 precedent: とこ could
    #    only be said by ja-JP-Neural2-C.
    human = {'library', 'nhk', 'shinmeikai', 'forvo', 'jpod', 'jpod101',
             'tofugu'}
    if not any(c.get('source') in human and c.get('url') for c in cands):
        cands.append({'label': 'no human recording of this exact form exists '
                               'in any of our dictionaries (they only carry '
                               'base forms) — the generated voices below are '
                               'the options',
                      'url': None, 'source': 'note'})
    gkey = _google_tts_key()
    if gkey:
        say = kana or surface
        # the spoken text is part of the cache key — a reading fix must
        # re-synthesize, not serve the old take forever
        say8 = hashlib.sha1(say.encode()).hexdigest()[:8]
        for vid, who in (('ja-JP-Neural2-B', 'female'),
                         ('ja-JP-Neural2-C', 'male'),
                         ('ja-JP-Neural2-D', 'male')):
            fname = f'google-{vid.lower()}-{say8}.mp3'
            fp = dest / fname
            if not fp.is_file():
                if time.time() > deadline - 1.0:
                    break
                try:
                    req = urllib.request.Request(
                        'https://texttospeech.googleapis.com/v1/text:synthesize'
                        f'?key={gkey}',
                        data=json.dumps({
                            'input': {'text': say},
                            'voice': {'languageCode': 'ja-JP', 'name': vid},
                            'audioConfig': {'audioEncoding': 'MP3'},
                        }).encode(),
                        headers={'Content-Type': 'application/json'})
                    resp = json.loads(urllib.request.urlopen(
                        req, timeout=min(5.0, deadline - time.time())).read())
                    audio = base64.b64decode(resp.get('audioContent') or '')
                    if not audio:
                        continue
                    fp.write_bytes(audio)
                except Exception:
                    continue               # quota/network — skip this voice
            cands.append({'label': f'google · generated {who} voice '
                                   f'({vid.rsplit("-", 1)[1]})',
                          'url': f'/auditions/{key}/{uid}/{fname}',
                          'source': 'google'})
    return {'candidates': cands}, None


def _body_str(body, field, maxlen):
    """A stripped string body field, or '' — a JSON number/list must 400
    upstream, never AttributeError the handler thread."""
    v = body.get(field)
    if not isinstance(v, str):
        return ''
    v = v.strip()
    return v if 0 < len(v) <= maxlen else ''


def _google_tts_key():
    """GOOGLE_TTS_KEY from the repo .env (same file generate_podcast.py
    reads); '' when absent — the generated-takes tier just doesn't appear."""
    try:
        for line in (ROOT / '.env').read_text().splitlines():
            if line.startswith('GOOGLE_TTS_KEY='):
                return line.split('=', 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return ''


# ── static serving: /assets/ + /auditions/ (traversal-guarded, audio only) ─

AUDIO_TYPES = {
    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4',
    '.ogg': 'audio/ogg', '.aac': 'audio/aac', '.flac': 'audio/flac',
}

# the pre-deploy preview (backlog 14531afd) serves whole song pages, so it
# needs the page's own types too — html/json/webp/fonts/etc., not just audio.
PREVIEW_TYPES = dict(AUDIO_TYPES, **{
    '.html': 'text/html; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
    '.vtt': 'text/vtt; charset=utf-8',
    '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2', '.woff': 'font/woff',
    '.pdf': 'application/pdf', '.apkg': 'application/octet-stream',
})

SONGS_DIR = ROOT / 'songs'
PREVIEW_ASSET_SUBDIRS = ('audio', 'pitch_data', 'images')


def _key_for_slug(slug):
    """Build key whose build_state points at songs/<slug>/, or None. A handful
    of small json reads — fine at page-load frequency."""
    for p in BUILDS.glob('*.build_state.json'):
        try:
            if json.loads(p.read_text()).get('slug') == slug:
                return p.name[:-len('.build_state.json')]
        except Exception:
            continue
    return None


def _refresh_preview_data(key, slug):
    """Shared core of the reveal refresh: shell the PARLER content_to_data so
    songs/<slug>/data.json picks up the latest lyric/timing edits. Returns
    (True, out) / (False, out) / (None, '') when the per-key lock is busy."""
    lk = _key_lock(key)
    if not lk.acquire(blocking=False):
        return None, ''
    try:
        r = subprocess.run([PARLER_PY, str(CONTENT_TO_DATA), key, slug],
                           cwd=str(ROOT), capture_output=True, text=True,
                           timeout=120)
    except subprocess.TimeoutExpired:
        return False, 'preview refresh timed out'
    finally:
        lk.release()
    out = (r.stdout + r.stderr).strip()
    return r.returncode == 0, out


def _freshen_preview_if_stale(slug):
    """Before serving /preview/<slug>/'s page, fold in any lyric/timing edits
    newer than its data.json — 'open edits in preview' is then just a link
    (no POST + popup-blocker dance on the phone). Best-effort: busy lock, an
    active job, a missing parler, or a refresh failure all serve the page as
    it stands (stale beats broken)."""
    if DEV_MODE:                       # the read-only mirror must not write
        return
    key = _key_for_slug(slug)
    if not key or not Path(PARLER_PY).exists():
        return
    dj = ROOT / 'songs' / slug / 'data.json'
    try:
        built_ts = dj.stat().st_mtime
    except OSError:
        return
    src = 0.0
    for p in (BUILDS / f'{key}.lyrics.json',
              BUILDS / f'{key}.timing_overrides.json',
              BUILDS / f'{key}.content.json'):
        try:
            src = max(src, p.stat().st_mtime)
        except OSError:
            pass
    if src <= built_ts + 1.0 or _key_job_active(key):
        return
    _refresh_preview_data(key, slug)   # outcome intentionally ignored


def _guarded_file(root, rel, types):
    """(file_path, content_type) for <root>/<rel>, or None. Same traversal
    guard as _resolve_static (resolved path must stay inside root), but with
    a caller-chosen type map."""
    ctype = types.get(Path(rel).suffix.lower())
    if ctype is None or not rel:
        return None
    try:
        target = (root / rel).resolve()
        root_r = root.resolve()
    except OSError:
        return None
    if root_r != target and root_r not in target.parents:
        return None
    if not target.is_file():
        return None
    return target, ctype


def _preview_resolve(url_path):
    """Map a /preview/<slug>/... request to (file, ctype), mirroring the prod
    Pages Function (functions/songs/[dir]/{audio,pitch_data,images}): those
    three subdirs intercept FIRST and resolve to the ONE shared asset set at
    songs/_assets/<folder>/... where folder = the slug minus its trailing
    -<rand> segment ('ema-9235e5' → 'ema'). Everything else serves from the
    lean songs/<slug>/ dir itself. Also handles the site-absolute paths song
    pages use (/fonts/..., /songs/_assets/...) so the preview is faithful.
    Returns (file, ctype), ('redirect', location) or None."""
    if url_path.startswith('/fonts/'):
        return _guarded_file(ROOT / 'fonts', url_path[len('/fonts/'):],
                             PREVIEW_TYPES)
    if url_path.startswith('/songs/_assets/'):
        return _guarded_file(ASSETS, url_path[len('/songs/_assets/'):],
                             PREVIEW_TYPES)
    if url_path.startswith('/songs/'):
        # song pages also self-reference by their PROD absolute path
        # (/songs/<slug>/data.json from the boot loader) — same resolution
        # as /preview/<slug>/ so those fetches work off the box too.
        slug, _, rest = url_path[len('/songs/'):].partition('/')
        if re.fullmatch(r'[a-z0-9][a-z0-9-]*', slug or '') and rest:
            first, _, sub = rest.partition('/')
            if first in PREVIEW_ASSET_SUBDIRS:
                cut = slug.rfind('-')
                folder = slug[:cut] if cut > 0 else slug
                return _guarded_file(ASSETS / folder / first, sub, PREVIEW_TYPES)
            return _guarded_file(SONGS_DIR / slug, rest, PREVIEW_TYPES)
        return None
    if url_path in ('/icon-192.png', '/icon-512.png', '/favicon.ico',
                    '/manifest.json', '/apple-touch-icon.png'):
        return _guarded_file(ROOT, url_path[1:], PREVIEW_TYPES)
    if not url_path.startswith('/preview/'):
        return None
    slug, _, rest = url_path[len('/preview/'):].partition('/')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', slug or ''):
        return None
    if '/' not in url_path[len('/preview/'):]:
        # /preview/<slug> without the trailing slash: the page's relative
        # asset URLs (audio/..., data.json?v=) would resolve against
        # /preview/ — redirect like every static host does.
        return ('redirect', f'/preview/{slug}/')
    if rest in ('', 'index.html'):
        rest = 'index.html'
    first, _, sub = rest.partition('/')
    if first in PREVIEW_ASSET_SUBDIRS:
        cut = slug.rfind('-')
        folder = slug[:cut] if cut > 0 else slug
        return _guarded_file(ASSETS / folder / first, sub, PREVIEW_TYPES)
    return _guarded_file(SONGS_DIR / slug, rest, PREVIEW_TYPES)


def _ytsim_audio(slug):
    """The downloaded audio of the exact YouTube video a /preview/<slug>/ page
    would stream, or None. The slug's own data.json names the video id (no
    build-key mapping — works for hand-made dirs too); the bytes are the
    yt-dlp download the pipeline already keeps (corpus hq_<ytid>.wav, wsync_
    mono fallback, /tmp spillover like _serve_songwav). When this returns a
    file, _serve_preview swaps the page's YouTube IFrame API for __ytsim.js so
    the preview plays the real song even where the embed refuses (Vaundy et
    al. return Video_unavailable on http://127.0.0.1 origins)."""
    try:
        yt = json.loads((SONGS_DIR / slug / 'data.json')
                        .read_text()).get('youtube_id') or ''
    except Exception:
        return None
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', yt):
        return None
    for p in (SONGCRAFT / 'corpus' / f'hq_{yt}.wav',
              SONGCRAFT / 'corpus' / f'wsync_{yt}.wav',
              Path(f'/tmp/hq_{yt}.wav'), Path(f'/tmp/wsync_{yt}.wav')):
        if p.is_file():
            return p
    return None


def _resolve_static(url_path):
    """Map a request path to (file_path, content_type) or None. The resolved
    real path must stay inside its allowed root (symlink/.. traversal-proof)
    and carry an audio extension — anything else is a 404."""
    if url_path.startswith('/assets/'):
        root, rel = ASSETS, url_path[len('/assets/'):]
    elif url_path.startswith('/auditions/'):
        root, rel = AUDITIONS, url_path[len('/auditions/'):]
    else:
        return None
    ctype = AUDIO_TYPES.get(Path(rel).suffix.lower())
    if ctype is None or not rel:
        return None
    try:
        target = (root / rel).resolve()
        root_r = root.resolve()
    except OSError:
        return None
    if root_r != target and root_r not in target.parents:
        return None                      # escaped the root — traversal attempt
    if not target.is_file():
        return None
    return target, ctype


# ── song-window clips (iOS can't SEEK a network wav, only play from 0) ─────

CLIPS = HERE / 'cache' / 'clips'          # gitignored (under builder/cache/)


def _yt_wav(yt, hifi=False):
    """The downloaded audio for a YouTube id (wsync default, hq if hifi), or
    None. Keyed by VIDEO, not song key, so the New song screen can reach it
    before a build key exists."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', yt or ''):
        return None
    corpus = SONGCRAFT / 'corpus'
    for kind in (['hq', 'wsync'] if hifi else ['wsync', 'hq']):
        for p in (corpus / f'{kind}_{yt}.wav', Path(f'/tmp/{kind}_{yt}.wav')):
            if p.is_file():
                return p
    return None


def _corpus_wav(key, hifi=False):
    """The song's aligned-audio wav Path (wsync default, hq if hifi), or None."""
    try:
        st = json.loads((BUILDS / f'{key}.build_state.json').read_text())
        yt = (st.get('meta') or {}).get('yt') or ''
    except Exception:
        return None
    return _yt_wav(yt, hifi)


def _clip_file(tag, src, b_ms, e_ms):
    """A small standalone WAV of just the [b_ms, e_ms] window of `src`, cached
    under builder/cache/clips/ as <tag>_<b>_<e>.wav. Played FROM THE START
    instead of seeking the whole song, because iOS Safari cannot seek a network
    WAV (it only plays one from 0) — the root cause of the silent editor on the
    phone. Pure-Python PCM slice: no ffmpeg, no re-encode, ~instant."""
    b_ms = max(0, int(b_ms))
    e_ms = max(b_ms + 30, int(e_ms))
    out = CLIPS / f'{tag}_{b_ms}_{e_ms}.wav'
    if out.is_file():
        return out
    if src is None:
        return None
    try:
        with wave.open(str(src), 'rb') as w:
            fr, ch, sw, nf = (w.getframerate(), w.getnchannels(),
                              w.getsampwidth(), w.getnframes())
            b_frame = max(0, min(nf, int(b_ms / 1000.0 * fr)))
            e_frame = max(b_frame + 1, min(nf, int(e_ms / 1000.0 * fr)))
            w.setpos(b_frame)
            data = w.readframes(e_frame - b_frame)
        CLIPS.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix('.tmp')
        with wave.open(str(tmp), 'wb') as o:
            o.setnchannels(ch)
            o.setsampwidth(sw)
            o.setframerate(fr)
            o.writeframes(data)
        tmp.replace(out)
        return out
    except Exception:
        return None


def _song_clip_file(key, b_ms, e_ms):
    return _clip_file(key, _corpus_wav(key), b_ms, e_ms)


# ── start probe: where does the music begin? ─────────────────────────────
# The New song screen asks this BEFORE the song exists, so it runs off the
# YouTube id on its own thread (not the per-key FIFO worker, which serialises
# builds). One probe per video at a time; the result file is the cache, so a
# second visit to the same candidate is instant.
_PROBES = {}                     # yt -> {'state': running|ready|error, 'error': str}
_PROBES_LOCK = threading.Lock()


def _probe_error(out):
    """One readable line out of a failed probe. yt-dlp prints a wall of
    update-nags and post-processor warnings before the real reason, and the UI
    has room for a sentence."""
    lines = [ln.strip() for ln in (out or '').splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith('ERROR:'):
            return re.sub(r'^ERROR:\s*(\[[^\]]+\]\s*)?', '', ln)
    return lines[-1] if lines else 'probe failed'


def _probe_worker(yt):
    rc, out = 1, ''
    try:
        r = subprocess.run([PY3, str(START_PROBE_PY), yt],
                           capture_output=True, text=True, timeout=600,
                           cwd=str(ROOT))
        rc, out = r.returncode, (r.stdout + r.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        out = 'the download took too long — is the network up?'
    except Exception as e:
        out = str(e)
    with _PROBES_LOCK:
        _PROBES[yt] = ({'state': 'ready'} if rc == 0 else
                       {'state': 'error', 'error': _probe_error(out)})
    with _JOBS_LOCK:
        _bump_events_locked()


def _probe_doc(yt):
    p = BUILDS / '_probe' / f'{yt}.start.json'
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


# ── HTTP handler ─────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    # iOS Safari's media stack refuses to play byte-range audio/video from an
    # HTTP/1.0 server (the default) — that silently broke every waveform-audio
    # play + the karaoke preview on the phone. Every response here sets a
    # Content-Length, so HTTP/1.1 keep-alive is safe. `timeout` closes idle
    # kept-alive connections so their handler threads don't pile up.
    protocol_version = 'HTTP/1.1'
    timeout = 60

    # ---- helpers ----
    def _send(self, status, body, content_type='text/plain; charset=utf-8',
              extra_headers=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False),
                   'application/json; charset=utf-8')

    def _send_file_ranged(self, path, ctype):
        """Media send with single-range HTTP Range support. The __ytsim
        <video> needs 206 responses for instant seeks, and Safari refuses to
        play media at all from a server that ignores Range."""
        try:
            size = path.stat().st_size
        except OSError:
            return self._send_json(404, {'error': 'not found'})
        start, end, status = 0, size - 1, 200
        m = re.fullmatch(r'bytes=(\d*)-(\d*)',
                         (self.headers.get('Range') or '').strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:                          # suffix form: the last N bytes
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                return self._send(416, b'', ctype, extra_headers={
                    'Content-Range': f'bytes */{size}'})
            status = 206
        try:
            with open(path, 'rb') as f:
                f.seek(start)
                body = f.read(end - start + 1)
        except OSError:
            return self._send_json(404, {'error': 'not found'})
        extra = {'Accept-Ranges': 'bytes', 'Cache-Control': 'no-cache'}
        if status == 206:
            extra['Content-Range'] = f'bytes {start}-{end}/{size}'
        return self._send(status, body, ctype, extra_headers=extra)

    def _read_json_body(self):
        length = int(self.headers.get('Content-Length') or '0')
        raw = self.rfile.read(length) if length else b''
        return json.loads(raw or b'{}')

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def log_message(self, fmt, *args):
        # Quieter log — just method + path + status.
        sys.stderr.write(
            f'{self.command} {self.path} → {args[1] if len(args) > 1 else ""}\n')

    # ---- routes ----
    def do_GET(self):
        if self.path == '/' or self.path.startswith('/?'):
            return self._serve_dashboard()
        if self.path.startswith('/api/state'):
            return self._serve_state()
        if self.path.startswith('/api/stale'):
            return self._serve_stale()
        if self.path.startswith('/denmoku.webmanifest'):
            return self._serve_manifest()
        if self.path.startswith('/denmoku-icon.png'):
            return self._serve_app_icon()
        if self.path.startswith('/api/jobs'):
            return self._serve_jobs()
        if self.path.startswith('/api/log'):
            return self._serve_log()
        if self.path.startswith('/api/search'):
            return self._serve_search()
        if self.path.startswith('/api/probe'):
            return self._serve_probe()
        if self.path.startswith('/api/ytmatch'):
            return self._serve_ytmatch()
        if self.path.startswith('/api/peaks/'):
            return self._serve_peaks()
        if self.path.startswith('/api/startprobe/'):
            return self._serve_startprobe()
        if self.path.startswith('/api/startclip/'):
            return self._serve_startclip()
        if self.path.startswith('/api/palette'):
            return self._serve_palette()
        if self.path.startswith('/api/art'):
            return self._serve_art()
        if self.path.startswith('/api/timing/'):
            return self._serve_timing()
        if self.path.startswith('/api/words/'):
            return self._serve_words()
        if self.path.startswith('/api/content/'):
            return self._serve_content()
        if self.path.startswith('/assets/') or self.path.startswith('/auditions/'):
            return self._serve_static()
        if self.path.startswith('/api/songclip/'):
            return self._serve_songclip()
        if self.path.startswith('/api/songwav/'):
            return self._serve_songwav()
        if self.path.startswith('/preview/') or self.path.startswith('/fonts/') \
                or self.path.startswith('/songs/') \
                or urllib.parse.urlparse(self.path).path in (
                    '/icon-192.png', '/icon-512.png', '/favicon.ico',
                    '/manifest.json', '/apple-touch-icon.png'):
            return self._serve_preview()
        return self._send_json(404, {'error': 'not found'})

    def do_POST(self):
        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            return self._send_json(400, {'error': 'invalid JSON body'})
        # A --dev mirror never mutates shared repo state (its own FIFO worker
        # against the same builds/ would race the real server's). Reads + render
        # only; every write is refused so headless UI drives can't corrupt data.
        if DEV_MODE:
            return self._send_json(409, {
                'error': 'this is a read-only dev instance — no edits, ships, or '
                         'rebuilds here'})
        if self.path == '/api/run':
            return self._serve_run(body)
        if self.path == '/api/stop':
            return self._serve_stop(body)
        if self.path == '/api/set':
            return self._serve_set(body)
        if self.path == '/api/init':
            return self._serve_init(body)
        if self.path == '/api/identity':
            return self._serve_identity(body)
        if self.path == '/api/timing/set':
            return self._serve_timing_edit(body, 'set')
        if self.path == '/api/timing/adopt':
            return self._serve_timing_edit(body, 'adopt')
        if self.path == '/api/timing/word':
            return self._serve_timing_edit(body, 'word')
        if self.path == '/api/timing/hold':
            return self._serve_timing_edit(body, 'hold')
        if self.path == '/api/timing/worddel':
            return self._serve_timing_edit(body, 'worddel')
        if self.path == '/api/timing/wordadd':
            return self._serve_timing_edit(body, 'wordadd')
        if self.path == '/api/timing/wordedit':
            return self._serve_timing_edit(body, 'wordedit')
        if self.path == '/api/word/audition':
            return self._serve_word_audition(body)
        if self.path == '/api/word/push':
            return self._serve_word_push(body)
        if self.path == '/api/content/save':
            return self._serve_content_save(body)
        if self.path == '/api/refetch_lyrics':
            return self._serve_refetch_lyrics(body)
        if self.path == '/api/preview_data':
            return self._serve_preview_data(body)
        if self.path == '/api/rebuild':
            return self._serve_rebuild(body)
        if self.path == '/api/ship':
            return self._serve_ship(body)
        if self.path == '/api/promote':
            return self._serve_promote(body)
        if self.path == '/api/peaks/build':
            return self._serve_peaks_build(body)
        if self.path == '/api/startprobe':
            return self._serve_startprobe_run(body)
        if self.path == '/api/start':
            return self._serve_start(body)
        if self.path == '/api/remove':
            return self._serve_remove(body)
        return self._send_json(404, {'error': 'not found'})

    # ---- route impls ----
    def _serve_dashboard(self):
        """Full dashboard HTML, rendered fresh per request (render_server_html
        reads the state files itself; no caching). If the UI function doesn't
        exist yet (render_dashboard is being built in parallel), serve a 503
        page — the integration phase wires it together."""
        global render_dashboard
        try:
            if render_dashboard is None:
                import render_dashboard as _rd
                render_dashboard = _rd
            html = render_dashboard.render_server_html()
        except (ImportError, AttributeError) as e:
            return self._send(
                503,
                '<!doctype html><meta charset="utf-8"><title>Denmoku</title>'
                '<body style="font:16px/1.5 -apple-system,sans-serif;'
                'background:#0a0a0c;color:#eee;padding:2rem">'
                '<h1>UI not built yet</h1>'
                '<p>render_dashboard.render_server_html() is not available '
                f'yet ({type(e).__name__}). The JSON API is live — this page '
                'appears once the server UI lands.</p>',
                'text/html; charset=utf-8')
        except Exception as e:
            return self._send_json(500, {'error': f'render failed: {e}'})
        self._send(200, html, 'text/html; charset=utf-8', extra_headers={
            'Cache-Control': 'no-store, must-revalidate',
        })

    def _serve_state(self):
        with _JOBS_LOCK:
            job = _job_public(_ACTIVE_JOB) if _ACTIVE_JOB else None
            queue = [_job_public(j) for j in _JOB_QUEUE]
        return self._send_json(200, {
            'gen': _compute_gen(),
            'builds': _read_builds(),
            'lexicon_count': _lexicon_count(),
            'backlog_open': _backlog_open(),
            'job': job,
            'queue': queue,
        })

    def _serve_stale(self):
        """GET /api/stale — per-song staleness, recomputed from disk.

        The dashboard used to read this from a const baked into the HTML at
        render time, so the ONE thing the rebuild button exists to clear could
        not clear itself: you pressed rebuild, waited a minute, watched the job
        finish, and the red 'built from an older version' banner was still
        sitting there. The only cure was the ↻ full reload — which reads as
        'the button did nothing'. Whatever the box paints red, the box has to
        be able to paint green without being reloaded.

        Costs ~0.6s for the whole library (it re-hashes the template tree and
        walks the audio set per song), so it is NOT on the 2s /api/state poll —
        the UI asks for it at boot, when a job ends, and on ↻. Short cache so a
        burst of asks is one walk."""
        return self._send_json(200, {'stale': _stale_map()})

    def _serve_manifest(self):
        """GET /denmoku.webmanifest — so Add to Home Screen gives the owner a real
        standalone app (own icon, no Safari chrome) instead of a bookmark.
        Distinct path from /manifest.json, which _serve_preview hands back from
        the repo root and belongs to the public manaoke.app site."""
        body = json.dumps({
            'name': 'Denmoku — song page builder',
            'short_name': 'Denmoku',
            'id': '/',
            'start_url': '/',
            'scope': '/',
            'display': 'standalone',
            'orientation': 'portrait-primary',
            'background_color': '#0c0b0f',
            'theme_color': '#0c0b0f',
            'icons': [{'src': '/denmoku-icon.png', 'sizes': '512x512',
                       'type': 'image/png'},
                      {'src': '/denmoku-icon.png', 'sizes': '512x512',
                       'type': 'image/png', 'purpose': 'maskable'}],
        }).encode()
        self._send(200, body, 'application/manifest+json',
                   extra_headers={'Cache-Control': 'no-cache'})

    def _serve_app_icon(self):
        """GET /denmoku-icon.png — the home-screen / tab icon. Same artwork as
        ~/Denmoku.app, so the phone icon and the Mac icon are one app."""
        try:
            body = (HERE / 'denmoku-icon-512.png').read_bytes()
        except OSError:
            return self._send_json(404, {'error': 'not found'})
        self._send(200, body, 'image/png',
                   extra_headers={'Cache-Control': 'public, max-age=86400'})

    def _serve_jobs(self):
        with _JOBS_LOCK:
            active = _job_public(_ACTIVE_JOB) if _ACTIVE_JOB else None
            recent = [_job_public(j) for j in _RECENT_JOBS[:20]]
        return self._send_json(200, {'active': active, 'recent': recent})

    def _serve_log(self):
        q = self._query()
        job_id = (q.get('id') or [''])[0]
        try:
            offset = max(0, int((q.get('offset') or ['0'])[0]))
        except ValueError:
            offset = 0
        job = _find_job(job_id)
        if job is None:
            return self._send_json(404, {'error': f'unknown job {job_id!r}'})
        chunk = b''
        try:
            with open(_job_log_path(job_id), 'rb') as f:
                f.seek(offset)
                chunk = f.read(256 * 1024)  # bounded per poll; UI grows offset
        except OSError:
            pass
        return self._send_json(200, {
            'chunk': chunk.decode('utf-8', errors='replace'),
            'offset': offset + len(chunk),
            'state': job['state'],
            'rc': job['rc'],
        })

    def _serve_search(self):
        q = self._query()
        query = (q.get('q') or [''])[0].strip()
        if not query:
            return self._send_json(400, {'error': 'missing q'})
        try:
            results = _search_candidates(query)
        except Exception as e:
            return self._send_json(502, {'error': f'search failed: {e}'})
        return self._send_json(200, {'results': results})

    def _serve_probe(self):
        """GET /api/probe?title=&artist=&duration_ms=&apple_url=&itunes_id= —
        the deep per-source probe for the PICKED candidate (confirm pane).
        Generous budget (~9s), sources parallel; see _deep_probe."""
        q = self._query()
        title = (q.get('title') or [''])[0].strip()
        artist = (q.get('artist') or [''])[0].strip()
        if not title:
            return self._send_json(400, {'error': 'missing title'})
        try:
            duration_ms = int((q.get('duration_ms') or ['0'])[0])
        except ValueError:
            duration_ms = 0
        apple_url = (q.get('apple_url') or [''])[0].strip()
        itunes_id = (q.get('itunes_id') or [''])[0].strip()
        if not apple_url and itunes_id.isdigit():
            apple_url = itunes_id       # apple.parse_song_url accepts a raw id
        try:
            payload = _deep_probe(title, artist, duration_ms, apple_url)
        except Exception as e:
            return self._send_json(502, {'error': f'probe failed: {e}'})
        return self._send_json(200, payload)

    def _serve_ytmatch(self):
        q = self._query()
        title = (q.get('title') or [''])[0].strip()
        artist = (q.get('artist') or [''])[0].strip()
        if not title:
            return self._send_json(400, {'error': 'missing title'})
        try:
            duration_ms = int((q.get('duration_ms') or ['0'])[0])
        except ValueError:
            duration_ms = 0
        try:
            cands = _yt_candidates(title, artist, duration_ms)
        except subprocess.TimeoutExpired:
            return self._send_json(504, {'error': 'yt-dlp search timed out'})
        except FileNotFoundError:
            return self._send_json(502, {'error': 'yt-dlp not found on PATH'})
        except Exception as e:
            return self._send_json(502, {'error': f'ytmatch failed: {e}'})
        return self._send_json(200, {'best': cands[0] if cands else None,
                                     'candidates': cands})

    def _path_key(self, prefix):
        """The {key} segment of /api/<prefix>/{key} (query stripped)."""
        path = urllib.parse.urlparse(self.path).path
        key = path[len(prefix):].strip('/')
        return key if re.fullmatch(r'[a-z0-9][a-z0-9-]*', key or '') else None

    def _serve_timing(self):
        key = self._path_key('/api/timing/')
        if not key:
            return self._send_json(400, {'error': 'bad key'})
        try:
            payload = _timing_payload(key)
        except Exception as e:
            return self._send_json(500, {'error': f'timing read failed: {e}'})
        if payload is None:
            return self._send_json(404, {'error': f'no lyrics for {key!r}'})
        return self._send_json(200, payload)

    def _serve_words(self):
        key = self._path_key('/api/words/')
        if not key:
            return self._send_json(400, {'error': 'bad key'})
        try:
            payload, err = _words_payload(key)
        except Exception as e:
            return self._send_json(500, {'error': f'words read failed: {e}'})
        if payload is None:
            return self._send_json(404, {'error': err})
        return self._send_json(200, payload)

    def _serve_content(self):
        """GET /api/content/<key> — every box the Writing tab draws: the
        section blurbs, the line translations, the word cards, and which of
        them are still empty."""
        key = self._path_key('/api/content/')
        if not key:
            return self._send_json(400, {'error': 'bad key'})
        if not content_edit.content_path(key).exists():
            return self._send_json(404, {
                'error': 'there is no study text for this song yet — run '
                         '“Cut the lines into word cards” first'})
        try:
            return self._send_json(200, content_edit.read_view(key))
        except Exception as e:
            return self._send_json(500, {'error': f'study text unreadable: {e}'})

    def _serve_content_save(self, body):
        """POST /api/content/save {key, edits:[{kind,id,field,value[,jp]}]} —
        write the study text. content_edit owns the file: it refuses text a
        gate would refuse later, throws away the recording of the old words,
        and reopens the steps that baked them into the page."""
        if DEV_MODE:
            return self._send_json(409, {'error': 'this is a read-only dev instance'})
        key = (body.get('key') or '').strip()
        if not key or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key):
            return self._send_json(400, {'error': 'bad key'})
        edits = body.get('edits')
        if not isinstance(edits, list) or not edits:
            return self._send_json(400, {'error': 'nothing to save'})
        if len(edits) > 200:
            return self._send_json(400, {'error': 'too many boxes at once'})
        # A content write mid-assemble races the job reading the same file.
        if _key_job_active(key):
            return self._send_json(409, {'error': 'this song is rebuilding — '
                                         'try again in a moment'})
        lk = _key_lock(key)
        if not lk.acquire(blocking=False):
            return self._send_json(409, {'error': 'this song is busy — '
                                         'try again in a moment'})
        try:
            result = content_edit.apply_edits(key, edits)
        except Exception as e:
            return self._send_json(500, {'error': f'save failed: {e}'})
        finally:
            lk.release()
        if not result.get('ok'):
            return self._send_json(409, result)
        with _JOBS_LOCK:
            _bump_events_locked()   # reopened steps change what the ladder shows
        return self._send_json(200, result)

    def _serve_static(self):
        url_path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        resolved = _resolve_static(url_path)
        if resolved is None:
            return self._send_json(404, {'error': 'not found'})
        target, ctype = resolved
        try:
            body = target.read_bytes()
        except OSError:
            return self._send_json(404, {'error': 'not found'})
        self._send(200, body, ctype, extra_headers={'Cache-Control': 'no-cache'})

    def _serve_songwav(self):
        """GET /api/songwav/<key>[?hifi=1] — the song's own aligned audio so the
        Timing tab can PLAY a line/word window and drive a playhead. Served with
        HTTP Range (206) support — iOS Safari refuses media from a server that
        ignores Range, and the phone is a first-class Denmoku surface. Default
        is the ~7 MB mono wsync wav (light over the tailnet); ?hifi=1 prefers
        the full-quality hq wav (~30-40 MB) for a careful desktop listen."""
        parsed = urllib.parse.urlparse(self.path)
        key = urllib.parse.unquote(parsed.path)[len('/api/songwav/'):].strip('/')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key or ''):
            return self._send_json(400, {'error': 'bad key'})
        try:
            st = json.loads((BUILDS / f'{key}.build_state.json').read_text())
            yt = (st.get('meta') or {}).get('yt') or ''
        except Exception:
            return self._send_json(404, {'error': 'unknown song'})
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', yt):
            return self._send_json(404, {'error': 'no video locked for this song'})
        corpus = SONGCRAFT / 'corpus'
        hifi = urllib.parse.parse_qs(parsed.query).get('hifi', ['0'])[0] == '1'
        order = (['hq', 'wsync'] if hifi else ['wsync', 'hq'])
        cand = []
        for kind in order:
            cand += [corpus / f'{kind}_{yt}.wav', Path(f'/tmp/{kind}_{yt}.wav')]
        for p in cand:
            if p.is_file():
                return self._send_file_ranged(p, 'audio/wav')
        return self._send_json(404, {'error': 'the song audio for this one '
                                     'isn’t on this Mac yet'})

    def _serve_songclip(self):
        """GET /api/songclip/<key>?b=<ms>&e=<ms> — a small WAV of JUST that
        window, played from the start. iOS Safari can't seek a network WAV, so
        the editor's "hear it / this word / what's sung" fetch a from-0 clip
        instead of seeking the whole song."""
        parsed = urllib.parse.urlparse(self.path)
        key = urllib.parse.unquote(parsed.path)[len('/api/songclip/'):].strip('/')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key or ''):
            return self._send_json(400, {'error': 'bad key'})
        q = urllib.parse.parse_qs(parsed.query)
        try:
            b = int(q.get('b', ['0'])[0])
            e = int(q.get('e', ['0'])[0])
        except (ValueError, TypeError):
            return self._send_json(400, {'error': 'b and e must be ms integers'})
        if e <= b:
            return self._send_json(400, {'error': 'e must be after b'})
        clip = _song_clip_file(key, b, e)
        if clip is None:
            return self._send_json(404, {'error': 'the song audio for this one '
                                         'isn’t on this Mac yet'})
        return self._send_file_ranged(clip, 'audio/wav')

    def _serve_peaks(self):
        """GET /api/peaks/<key> — the precomputed waveform peaks JSON
        (builds/<key>.peaks.json, written by peaks.py): 10ms [min,max] int8
        bins, vocals + mix lanes. The Timing tab's waveform strip reads this
        instead of decoding the multi-MB wav."""
        key = self._path_key('/api/peaks/')
        if not key:
            return self._send_json(400, {'error': 'bad key'})
        p = BUILDS / f'{key}.peaks.json'
        if not p.is_file():
            return self._send_json(404, {
                'error': f'no waveform data for {key!r} yet — '
                         f'run: python3 tools/songcraft/peaks.py {key}'})
        try:
            body = p.read_bytes()
        except OSError as e:
            return self._send_json(500, {'error': f'peaks read failed: {e}'})
        return self._send(200, body, 'application/json; charset=utf-8',
                          extra_headers={'Cache-Control': 'no-cache'})

    def _path_yt(self, prefix):
        """The 11-char YouTube id in /<prefix><yt>[?…], or ''."""
        parsed = urllib.parse.urlparse(self.path)
        yt = urllib.parse.unquote(parsed.path)[len(prefix):].strip('/')
        return yt if re.fullmatch(r'[A-Za-z0-9_-]{11}', yt or '') else ''

    def _serve_startprobe(self):
        """GET /api/startprobe/<yt> — {state, music_start_ms, peaks, …} for the
        New song screen's start-point strip. Never starts work itself: POST
        does that, this only reports. state is ready | running | error | none."""
        yt = self._path_yt('/api/startprobe/')
        if not yt:
            return self._send_json(400, {'error': 'bad video id'})
        doc = _probe_doc(yt)
        with _PROBES_LOCK:
            st = dict(_PROBES.get(yt) or {})
        if doc:
            doc['state'] = 'ready'
            doc.pop('wav', None)              # a local path is no business of the UI
            return self._send_json(200, doc)
        if st.get('state') == 'running':
            return self._send_json(200, {'state': 'running', 'yt': yt})
        if st.get('state') == 'error':
            return self._send_json(200, {'state': 'error', 'yt': yt,
                                         'error': st.get('error') or 'probe failed'})
        return self._send_json(200, {'state': 'none', 'yt': yt})

    def _serve_startprobe_run(self, body):
        """POST /api/startprobe {yt} — measure where the music starts. Downloads
        the song's audio if this Mac has never seen it (the SAME corpus wav the
        sync step needs later, so nothing is wasted). Returns immediately;
        poll the GET."""
        if DEV_MODE:
            return self._send_json(409, {'error': 'this is a read-only dev instance'})
        yt = (body.get('yt') or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', yt):
            return self._send_json(400, {'error': 'bad video id'})
        if _probe_doc(yt) and not body.get('force'):
            return self._send_json(200, {'ok': True, 'state': 'ready'})
        with _PROBES_LOCK:
            if (_PROBES.get(yt) or {}).get('state') == 'running':
                return self._send_json(200, {'ok': True, 'state': 'running'})
            _PROBES[yt] = {'state': 'running'}
        threading.Thread(target=_probe_worker, args=(yt,), daemon=True).start()
        return self._send_json(200, {'ok': True, 'state': 'running'})

    def _serve_startclip(self):
        """GET /api/startclip/<yt>?b=&e= — a small WAV of that window, so the
        start point can be HEARD before the song is added. Same from-zero clip
        trick as /api/songclip (iOS Safari can't seek a network wav)."""
        yt = self._path_yt('/api/startclip/')
        if not yt:
            return self._send_json(400, {'error': 'bad video id'})
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            b = int(q.get('b', ['0'])[0])
            e = int(q.get('e', ['0'])[0])
        except (ValueError, TypeError):
            return self._send_json(400, {'error': 'b and e must be ms integers'})
        if e <= b:
            return self._send_json(400, {'error': 'e must be after b'})
        clip = _clip_file(f'yt{yt}', _yt_wav(yt), b, e)
        if clip is None:
            return self._send_json(404, {'error': 'that song’s audio isn’t on '
                                         'this Mac yet'})
        return self._send_file_ranged(clip, 'audio/wav')

    def _serve_art(self):
        """GET /api/art?url=<cover url> — the cover, proxied. The eyedropper
        reads pixels off a canvas, and a canvas painted from another origin is
        tainted (getImageData throws), so the picker would silently do nothing.
        Serving the same bytes from here keeps it same-origin."""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        url = (q.get('url', [''])[0] or '').strip()
        if not url.startswith(('http://', 'https://')):
            return self._send_json(400, {'error': 'url must be http(s)'})
        try:
            import assemble_page as ap
            raw = ap.cover_art_bytes(url)
        except Exception as e:
            return self._send_json(502, {'error': f'art fetch failed: {e}'})
        if not raw:
            return self._send_json(404, {'error': 'couldn’t fetch that cover'})
        kind = 'image/png' if raw[:8] == b'\x89PNG\r\n\x1a\n' else 'image/jpeg'
        return self._send(200, raw, kind,
                          extra_headers={'Cache-Control': 'max-age=3600'})

    def _serve_palette(self):
        """GET /api/palette?art=<url> — the colors assemble WOULD derive from
        this cover, so the New song screen can show the auto-picks (and the
        landing card accent they produce) before anything is built. Same
        cover_palette call the page build uses; no second opinion."""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        art = (q.get('art', [''])[0] or '').strip()
        if not art.startswith(('http://', 'https://')):
            return self._send_json(400, {'error': 'art must be an http(s) url'})
        try:
            import assemble_page as ap
            pal = ap.cover_palette(art)
        except Exception as e:
            return self._send_json(500, {'error': f'palette failed: {e}'})
        if not pal:
            return self._send_json(404, {'error': 'couldn’t read colors off '
                                         'that cover'})
        c1, c2, c3, hi, card, fb, base, body = pal
        return self._send_json(200, {
            'c1': ap.rgb_hex(c1), 'c2': ap.rgb_hex(c2), 'c3': ap.rgb_hex(c3),
            'hi': ap.rgb_hex(hi), 'card_accent': card,
            'fb': [ap.rgb_hex(x) for x in fb],
            'base': list(base), 'body': list(body)})

    def _serve_start(self, body):
        """POST /api/start {key, ms} | {key, auto:true} — set where an EXISTING
        song starts. Shells manaoke_build.py start, which patches build_state
        meta + builds/<key>.lyrics.json; the page still needs a rebuild to ship
        it. Cheap on purpose: re-running the sync step to move one number would
        cost a Demucs separation and a forced alignment."""
        if DEV_MODE:
            return self._send_json(409, {'error': 'this is a read-only dev instance'})
        key = (body.get('key') or '').strip()
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key or ''):
            return self._send_json(400, {'error': 'bad key'})
        if _key_job_active(key):
            return self._send_json(409, {'error': 'this song is building — '
                                         'try again when it finishes'})
        if body.get('auto'):
            args = ['start', key, '--auto']
        else:
            try:
                ms = int(body.get('ms'))
            except (TypeError, ValueError):
                return self._send_json(400, {'error': 'ms must be a whole number '
                                             'of milliseconds'})
            if ms < 0:
                return self._send_json(400, {'error': 'ms can’t be negative'})
            args = ['start', key, str(ms)]
        with _key_lock(key):
            ok, out = self._run_cli_sync(args, timeout=60)
        with _JOBS_LOCK:
            _bump_events_locked()
        return self._send_json(200 if ok else 500,
                               {'ok': ok, 'output': out} if ok else
                               {'ok': False, 'output': out,
                                'error': out or 'couldn’t set the start point'})

    def _serve_remove(self, body):
        """POST /api/remove {key, confirm} — retire a song. Nothing is deleted:
        manaoke_build.py remove moves every file into builds/_trash/, and
        refuses outright while the song is live on the landing."""
        if DEV_MODE:
            return self._send_json(409, {'error': 'this is a read-only dev instance'})
        key = (body.get('key') or '').strip()
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key or ''):
            return self._send_json(400, {'error': 'bad key'})
        if (body.get('confirm') or '').strip() != key:
            return self._send_json(400, {'error': 'type the song’s key to confirm'})
        if _key_job_active(key):
            return self._send_json(409, {'error': 'this song is building — '
                                         'stop the job first'})
        ok, out = self._run_cli_sync(['remove', key], timeout=120)
        with _JOBS_LOCK:
            _bump_events_locked()
        return self._send_json(200 if ok else 500,
                               {'ok': ok, 'output': out} if ok else
                               {'ok': False, 'output': out,
                                'error': out or 'remove failed'})

    def _serve_preview(self):
        """GET /preview/<slug>/... — the assembled song page, served straight
        from songs/<slug>/ BEFORE any deploy (backlog 14531afd), with the
        lean-dir asset convention resolved exactly like production (see
        _preview_resolve). Query strings (?v= cache busters) are ignored for
        resolution, as on Pages."""
        url_path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        # YT-stream simulation (see _ytsim_audio): the shim script + its audio
        # live on reserved __ytsim paths inside the preview namespace, so no
        # real song file can collide with them.
        m = re.fullmatch(r'/preview/([a-z0-9][a-z0-9-]*)/__ytsim\.js', url_path)
        if m:
            try:
                body = (HERE / 'ytsim.js').read_bytes()
            except OSError:
                return self._send_json(404, {'error': 'ytsim.js missing'})
            return self._send(200, body, 'text/javascript; charset=utf-8',
                              extra_headers={'Cache-Control': 'no-cache'})
        m = re.fullmatch(r'/preview/([a-z0-9][a-z0-9-]*)/__ytsim/audio\.wav',
                         url_path)
        if m:
            p = _ytsim_audio(m.group(1))
            if p is None:
                return self._send_json(404, {'error': 'no corpus audio for '
                                             'this song on this machine'})
            return self._send_file_ranged(p, 'audio/wav')
        # the PAGE request (not its assets) folds pending edits into data.json
        # first, so "open edits in preview" is a plain link that's never stale
        m = re.fullmatch(r'/preview/([a-z0-9][a-z0-9-]*)/(index\.html)?',
                         url_path)
        if m:
            _freshen_preview_if_stale(m.group(1))
        resolved = _preview_resolve(url_path)
        if resolved is None:
            return self._send_json(404, {'error': 'not found in this preview'})
        if resolved[0] == 'redirect':
            body = b'moved'
            self.send_response(302)
            self.send_header('Location', resolved[1])
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        target, ctype = resolved
        try:
            body = target.read_bytes()
        except OSError:
            return self._send_json(404, {'error': 'not found'})
        if ctype.startswith('text/html'):
            # Song pages carry their own http→https upgrade guard
            # (`if(location.protocol==='http:'){location.replace('https:...`)
            # — correct in production, fatal through this http://127.0.0.1
            # preview (it reloads onto a TLS port that doesn't exist). The
            # served COPY gets that one guard disarmed; the file on disk is
            # untouched.
            body = re.sub(
                rb"if\(location\.protocol==='http:'\)\{location\.replace\(.*?\);?\}",
                b'/* http\xe2\x86\x92https guard disarmed by the Denmoku preview */',
                body)
            # The guard's CSP sibling: <meta ... upgrade-insecure-requests>
            # rewrites every subresource fetch to https. Harmless on
            # http://127.0.0.1 (a trustworthy origin), fatal on the tailnet
            # address (http://100.x.x.x:8773 — the phone path): fonts,
            # data.json, __ytsim.js all upgrade to an https port nothing
            # terminates → ERR_CONNECTION_CLOSED → "Couldn't load this song".
            body = re.sub(
                rb'<meta http-equiv="Content-Security-Policy" '
                rb'content="upgrade-insecure-requests">',
                b'<!-- upgrade-insecure-requests disarmed by the Denmoku '
                b'preview -->',
                body)
            # Same served-copy-only treatment for the YouTube stream: embeds
            # for some videos refuse playback on an http://127.0.0.1 origin
            # (auth error → Video_unavailable → tap-to-start hangs), so when
            # the downloaded audio of this exact video is on the box, point
            # the page's IFrame-API loader at the __ytsim.js stand-in. The
            # page code runs unchanged against the identical Player surface;
            # the audio bytes ARE the YT stream. File on disk untouched.
            mslug = re.match(r'^/preview/([a-z0-9][a-z0-9-]*)/', url_path)
            if (mslug and b'"https://www.youtube.com/iframe_api"' in body
                    and _ytsim_audio(mslug.group(1)) is not None):
                body = body.replace(
                    b'"https://www.youtube.com/iframe_api"',
                    b'"__ytsim.js"', 1)
        return self._send(200, body, ctype,
                          extra_headers={'Cache-Control': 'no-cache'})

    def _serve_timing_edit(self, body, verb):
        """POST /api/timing/set|adopt|word — shell timing_edit.py synchronously
        (the CLI owns the lyrics file; the server never edits it directly).
        `word` is the drag-a-word save: {key, line, word, begin_ms[, end_ms]}
        → `timing_edit.py <key> word <line> <word> --begin <ms> [--end <ms>]`.
        Clamping/monotonicity live in the verb — the server just relays its
        output so the UI can show exactly what the box decided."""
        if DEV_MODE:
            return self._send_json(409, {'error': 'this is a read-only dev instance'})
        key = (body.get('key') or '').strip()
        line = body.get('line')
        if not key or not isinstance(line, int):
            return self._send_json(400, {'error': 'need key and line (int)'})
        # A timing write mid-assemble races the job that reads the same lyrics
        # file; refuse while a job for this song is queued/running.
        if _key_job_active(key):
            return self._send_json(409, {'error': 'this song is rebuilding — '
                                         'try again in a moment'})
        args = [key, verb, str(line)]
        if verb == 'set':
            begin, end = body.get('begin_ms'), body.get('end_ms')
            if not isinstance(begin, int) or not isinstance(end, int):
                return self._send_json(400, {'error': 'need begin_ms and end_ms (int)'})
            args += ['--begin', str(begin), '--end', str(end)]
        elif verb == 'word':
            word, begin = body.get('word'), body.get('begin_ms')
            if not isinstance(word, int) or not isinstance(begin, int):
                return self._send_json(400, {'error': 'need word (int) and begin_ms (int)'})
            args = [key, 'word', str(line), str(word), '--begin', str(begin)]
            if isinstance(body.get('end_ms'), int):
                args += ['--end', str(body['end_ms'])]
        elif verb == 'hold':
            word = body.get('word')
            if not isinstance(word, int):
                return self._send_json(400, {'error': 'need word (int)'})
            args = [key, 'hold', str(line), str(word)]
            if body.get('clear'):
                args += ['--clear']
            elif isinstance(body.get('at_ms'), int):
                args += ['--at', str(body['at_ms'])]
            else:
                return self._send_json(400, {'error': 'need at_ms (int) or clear'})
        elif verb == 'worddel':
            word = body.get('word')
            if not isinstance(word, int):
                return self._send_json(400, {'error': 'need word (int)'})
            args = [key, 'worddel', str(line), str(word)]
        elif verb == 'wordadd':
            word = body.get('word')
            text = _body_str(body, 'text', 40)
            if not isinstance(word, int) or not text:
                return self._send_json(400, {'error': 'need word (int) and text (≤40 chars)'})
            # --opt=value form: a value starting with '-' must not read as a flag
            args = [key, 'wordadd', str(line), str(word), '--text=' + text,
                    '--where=' + ('before' if body.get('where') == 'before' else 'after')]
            reading = _body_str(body, 'reading', 80)
            if reading:
                args += ['--reading=' + reading]
        elif verb == 'wordedit':
            word = body.get('word')
            if not isinstance(word, int):
                return self._send_json(400, {'error': 'need word (int)'})
            args = [key, 'wordedit', str(line), str(word)]
            text = _body_str(body, 'text', 40)
            if text:
                args += ['--text=' + text]
            if body.get('reading') is not None:
                if not isinstance(body['reading'], str) or len(body['reading']) > 80:
                    return self._send_json(400, {'error': 'reading must be a short string'})
                args += ['--reading=' + body['reading'].strip()]
            lkana = _body_str(body, 'line_kana', 200)
            if lkana:
                args += ['--line-kana=' + lkana]
            if len(args) == 4:
                return self._send_json(400, {'error': 'nothing to change'})
        else:
            args += ['--source', 'lrclib']
        lk = _key_lock(key)
        if not lk.acquire(blocking=False):
            return self._send_json(409, {'error': 'this song is busy — '
                                         'try again in a moment'})
        try:
            r = subprocess.run([PY3, str(TIMING_EDIT)] + args, cwd=str(ROOT),
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return self._send_json(500, {'ok': False, 'error': 'timing_edit timed out'})
        finally:
            lk.release()
        out = (r.stdout + r.stderr).strip()
        ok = r.returncode == 0
        if not ok:
            return self._send_json(500, {'ok': False, 'output': out[-4000:],
                                         'error': out[-4000:] or 'timing_edit failed'})
        # B6 — return the fresh read model so the client patches the affected
        # row + drawer in place (preserving zoom/focus/playhead) instead of the
        # tmFetch→tmRender full re-render that nuked all transient editor state.
        try:
            timing = _timing_payload(key)
        except Exception:
            timing = None
        return self._send_json(200, {'ok': True, 'output': out[-4000:], 'timing': timing})

    def _serve_word_audition(self, body):
        key = (body.get('key') or '').strip()
        uid = (body.get('uid') or '').strip()
        if not key or not _safe_uid(uid):
            return self._send_json(400, {'error': 'need key and a clean uid'})
        try:
            payload, err = _audition_candidates(key, uid)
        except Exception as e:
            return self._send_json(500, {'error': f'audition failed: {e}'})
        if payload is None:
            return self._send_json(404, {'error': err})
        return self._send_json(200, payload)

    def _serve_word_push(self, body):
        """POST /api/word/push — queue install_word.py as a normal job (the
        loudnorm + read-back chain takes minutes; the job bar shows it)."""
        key = (body.get('key') or '').strip()
        uid = (body.get('uid') or '').strip()
        candidate = (body.get('candidate') or '').strip()
        pin = bool(body.get('pin'))
        if not key or not _safe_uid(uid) or not candidate:
            return self._send_json(400, {'error': 'need key, uid, candidate'})
        entries, folder, _slug = _word_entries(key)
        if entries is None:
            return self._send_json(404, {'error': folder})
        match = next(((rel, sec, u, m) for rel, sec, u, m in entries if u == uid), None)
        if match is None:
            return self._send_json(404, {'error': f'no word with uid {uid!r}'})
        _rel, sec, _u, m = match
        # candidate: a served url (/auditions/…, /assets/…) or an abs path
        # under auditions/ or the human_audio library — nothing else.
        src = None
        resolved = _resolve_static(candidate)
        if resolved is not None:
            src = resolved[0]
        else:
            p = Path(candidate)
            if p.is_absolute() and p.suffix.lower() in AUDIO_TYPES:
                try:
                    rp = p.resolve()
                except OSError:
                    rp = None
                if rp is not None and rp.is_file() and any(
                        root == rp.parent or root in rp.parents
                        for root in (AUDITIONS.resolve(),
                                     (HUMAN / 'library').resolve())):
                    src = rp
        if src is None:
            return self._send_json(400, {
                'error': 'candidate must be a served /auditions|/assets url or '
                         'an absolute path under auditions/ or the word library'})
        cmd = [PY3, str(INSTALL_WORD), '--song', folder, '--sec', sec,
               '--rom', uid, '--src', str(src)]
        # honest provenance: the 'curated' default is only true for human
        # dictionary recordings. Generated takes say so; re-installing the
        # current take (the pin flow) carries its EXISTING provenance forward
        # — else a qwen/kokoro take gets laundered into 'curated' and E17
        # would greenlight a synthetic particle.
        if src.name.startswith('google-'):
            cmd += ['--source', 'google']
        elif src.name == 'current.mp3' or src.name.startswith('previous'):
            try:
                prov = json.loads((BUILDS / f'{key}.clip_provenance.json')
                                  .read_text())
                cur_src = (prov.get(_rel) or {}).get('source') or ''
            except Exception:
                cur_src = ''
            if cur_src and not cur_src.startswith('curated'):
                cmd += ['--source', cur_src.split('·')[0].strip()]
        # the reading the AUDITION hunted/synthesized with — the read-back
        # gate must expect the same one (word_meta kana is often the kanji)
        kana = _effective_kana(m)
        if kana:
            cmd += ['--kana', kana]
        if pin:
            cmd += ['--pin']
        if _install_word_has_chain():
            cmd += ['--chain']
        # The job worker's Popen inherits the server's environment untouched,
        # so MANAOKE_CHAIN_EXEC (the chain hand-off hook) passes through as-is
        # — deliberately never set or stripped here.
        job = _job_new(key, f'word-push:{uid}', cmd=cmd)
        with _JOBS_CV:
            _JOB_QUEUE.append(job)
            _bump_events_locked()
            _JOBS_CV.notify()
        return self._send_json(200, {'job_id': job['id']})

    def _enqueue(self, key, step, cmd):
        """Append a cmd-override job to the FIFO and return its id (same shape
        as word-push / refetch)."""
        job = _job_new(key, step, cmd=cmd)
        with _JOBS_CV:
            _JOB_QUEUE.append(job)
            _bump_events_locked()
            _JOBS_CV.notify()
        return job['id']

    def _serve_preview_data(self, body):
        """POST /api/preview_data {key} — the LIGHT reveal refresh: re-run
        content_to_data.py so songs/<slug>/data.json (and its kana_timings) pick
        up the latest lyric/timing edits, WITHOUT the heavy full assemble (no
        template clone, no ffmpeg drill re-cut). The karaoke-preview iframe then
        reloads and shows the reveal against the just-edited timings. Synchronous
        (~1-3s); shelled with the PARLER python (content_to_data needs pyopenjtalk
        or it silently writes empty morae). Per-key locked against the assemble
        worker + a second preview_data."""
        if DEV_MODE:
            return self._send_json(409, {'error': 'this is a read-only dev instance'})
        key = (body.get('key') or '').strip()
        if not key or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key):
            return self._send_json(400, {'error': 'bad key'})
        st = _read_build_state(key)
        slug = (st or {}).get('slug') or ''
        if not slug or not (ROOT / 'songs' / slug).is_dir():
            return self._send_json(400, {'error': 'no built preview for this song yet '
                                         '— rebuild the page first'})
        # Hard-fail rather than silently degrade: under system python3,
        # content_to_data sets HAVE_JA=False and writes EMPTY kana_timings — a
        # preview that lies about the reveal. run_assemble uses parler
        # unconditionally; so do we.
        if not Path(PARLER_PY).exists():
            return self._send_json(500, {'ok': False, 'error': 'the reveal engine '
                                         'isn’t installed on this Mac (needs the '
                                         'Japanese reading tools) — can’t refresh'})
        if _key_job_active(key):
            return self._send_json(409, {'error': 'this song is rebuilding — '
                                         'try again in a moment'})
        ok, out = _refresh_preview_data(key, slug)
        if ok is None:
            return self._send_json(409, {'error': 'this song is busy — '
                                         'try again in a moment'})
        if not ok:
            return self._send_json(500, {'ok': False, 'output': out[-4000:],
                                         'error': out[-4000:] or 'preview refresh failed'})
        return self._send_json(200, {'ok': True, 'output': out[-2000:], 'slug': slug})

    def _serve_ship(self, body):
        """POST /api/ship {key} — put this song's preview online at its private
        random-slug URL (scoped commit + single push). Queued: the git push can
        take a few seconds and the job bar shows it. Does NOT touch the root
        landing (that's promote)."""
        if DEV_MODE:
            return self._send_json(409, {'error': 'this is a read-only dev instance'})
        key = (body.get('key') or '').strip()
        if not key or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key):
            return self._send_json(400, {'error': 'bad key'})
        if not (BUILDS / f'{key}.build_state.json').exists():
            return self._send_json(400, {'error': f'no build state for {key!r}'})
        cmd = [PY3, str(MANAOKE_BUILD), 'ship', key]
        return self._send_json(200, {'job_id': self._enqueue(key, f'ship:{key}', cmd)})

    def _serve_rebuild(self, body):
        """POST /api/rebuild {key[, fresh_slug]} — bring a song's page up to the
        current template.

        The box has always been able to SEE this ("7 songs are behind the
        template — each one needs a rebuild", and a red STALE: TEMPLATE chip on
        the song) and had no way to DO it: the only button on the screen, run
        all (auto), walks the step list, every step is already done, so it runs
        to the end and stops at deploy with the page still old. The fix for what
        the box tells you about has to be reachable from where it tells you.
        Queued — it re-assembles and can take a minute."""
        if DEV_MODE:
            return self._send_json(409, {'error': 'this is a read-only dev instance'})
        key = (body.get('key') or '').strip()
        if not key or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key):
            return self._send_json(400, {'error': 'bad key'})
        if not (BUILDS / f'{key}.build_state.json').exists():
            return self._send_json(400, {'error': f'no build state for {key!r}'})
        cmd = [PY3, str(MANAOKE_BUILD), 'rebuild', key]
        if body.get('fresh_slug'):
            cmd.append('--fresh-slug')
        return self._send_json(200, {'job_id': self._enqueue(key, f'rebuild:{key}', cmd)})

    def _serve_promote(self, body):
        """POST /api/promote {key} — put this song on the main page (manaoke.app):
        repoint root SONGS[] + commit + push. Refuses unless the preview is
        already online (else the landing points at a dir Cloudflare never built
        → 404). Queued."""
        if DEV_MODE:
            return self._send_json(409, {'error': 'this is a read-only dev instance'})
        key = (body.get('key') or '').strip()
        if not key or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key):
            return self._send_json(400, {'error': 'bad key'})
        state = _song_state(key)
        if not state.get('pushed'):
            return self._send_json(409, {'error': 'put it online first — the main '
                                         'page can’t point at a preview that isn’t '
                                         'uploaded yet'})
        cmd = [PY3, str(MANAOKE_BUILD), 'promote', key, '--push']
        return self._send_json(200, {'job_id': self._enqueue(key, f'promote:{key}', cmd)})

    def _serve_peaks_build(self, body):
        """POST /api/peaks/build {key} — regenerate the waveform peaks at a finer
        resolution (bin_ms=2) so zoom-in stays sharp past the default 10ms floor.
        Queued (needs the parler env for numpy/soundfile). Refuses when there's
        no song audio on this Mac (peaks.py can't build without the wav)."""
        if DEV_MODE:
            return self._send_json(409, {'error': 'this is a read-only dev instance'})
        key = (body.get('key') or '').strip()
        if not key or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key):
            return self._send_json(400, {'error': 'bad key'})
        st = _read_build_state(key)
        yt = ((st or {}).get('meta') or {}).get('yt') or ''
        corpus = SONGCRAFT / 'corpus'
        # Mirror peaks.py lane_sources (corpus + /tmp spillover + demucs stems),
        # not just the two corpus wavs — else a wav that lives only in /tmp is
        # falsely refused.
        cand = ([corpus / f'{k}_{yt}.wav' for k in ('hq', 'wsync')] +
                [Path(f'/tmp/{k}_{yt}.wav') for k in ('hq', 'wsync')] +
                [Path(f'/tmp/demucs/htdemucs/hq_{yt}/vocals.wav'),
                 corpus / 'demucs' / 'htdemucs' / f'hq_{yt}' / 'vocals.wav']) if yt else []
        has_audio = any(p.is_file() for p in cand)
        if not has_audio:
            return self._send_json(409, {'error': 'the song audio for this one isn’t '
                                         'on this Mac yet, so the waveform can’t be sharpened'})
        cmd = [PY3, str(PEAKS_PY), key, '--bin-ms', '2']
        return self._send_json(200, {'job_id': self._enqueue(key, 'peaks', cmd)})

    def _serve_refetch_lyrics(self, body):
        """POST /api/refetch_lyrics — queue `fetch_timed_lyrics.py <key>
        --force` as a normal job (backlog c899c32e). Destructive by design:
        it replaces builds/<key>.lyrics.json wholesale, so ALL line/word
        timings — including manual timing_edit fixes — are lost. The UI owns
        the two-tap warning; the server just runs what it's asked.

        With a `sheet` in the body it runs the same job against a sheet the
        person brought instead of the network sources — the bring-your-own
        door, for songs none of the three sources has ever heard of."""
        key = (body.get('key') or '').strip()
        if not key or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key):
            return self._send_json(400, {'error': 'bad key'})
        if not (BUILDS / f'{key}.build_state.json').exists():
            return self._send_json(400, {
                'error': f'no build state for {key!r} — init it first'})
        cmd = [PY3, str(SONGCRAFT / 'fetch_timed_lyrics.py'), key, '--force']
        sheet = body.get('sheet')
        if isinstance(sheet, str) and sheet.strip():
            # Keep the pasted text on disk: the job log names a file, and when
            # an import goes wrong the thing you want to look at is exactly
            # what was handed over, not a reconstruction of it.
            import fetch_timed_lyrics as _ftl
            kind = _ftl.sniff_sheet(sheet) or 'txt'
            ext = {'ttml': 'xml', 'lrc': 'lrc', 'json': 'json'}.get(kind, 'txt')
            BYO_DIR.mkdir(parents=True, exist_ok=True)
            path = BYO_DIR / f'{key}.byo.{ext}'
            path.write_text(sheet, encoding='utf-8')
            cmd += ['--source', 'file', '--file', str(path)]
        job = _job_new(key, 'refetch-lyrics', cmd=cmd)
        with _JOBS_CV:
            _JOB_QUEUE.append(job)
            _bump_events_locked()
            _JOBS_CV.notify()
        return self._send_json(200, {'job_id': job['id']})

    def _serve_run(self, body):
        key = (body.get('key') or '').strip()
        step = body.get('step')
        step = step.strip() if isinstance(step, str) and step.strip() else None
        if not key:
            return self._send_json(400, {'error': 'missing key'})
        if not (BUILDS / f'{key}.build_state.json').exists():
            return self._send_json(400, {
                'error': f'no build state for {key!r} — init it first'})
        job = _job_new(key, step)
        with _JOBS_CV:
            _JOB_QUEUE.append(job)
            _bump_events_locked()
            _JOBS_CV.notify()
        return self._send_json(200, {'job_id': job['id']})

    def _serve_stop(self, body):
        job_id = (body.get('job_id') or '').strip()
        if not job_id:
            return self._send_json(400, {'error': 'missing job_id'})
        job = _find_job(job_id)
        if job is None:
            return self._send_json(404, {'error': f'unknown job {job_id!r}'})
        _stop_job(job)
        return self._send_json(200, {'ok': True})

    def _run_cli_sync(self, args, timeout=90):
        """Run a fast manaoke_build.py verb synchronously (set / init are
        instant). Returns (ok, output)."""
        try:
            r = subprocess.run([PY3, str(MANAOKE_BUILD)] + args, cwd=str(ROOT),
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f'timed out after {timeout}s'
        out = (r.stdout + r.stderr).strip()
        return r.returncode == 0, out[-4000:]

    def _serve_set(self, body):
        key = (body.get('key') or '').strip()
        step = (body.get('step') or '').strip()
        if not key or not step:
            return self._send_json(400, {'error': 'need key and step'})
        done = bool(body.get('done'))
        # the CLI has no --undone flag; un-doing a step = --status pending
        args = ['set', key, step] + (['--done'] if done
                                     else ['--status', 'pending'])
        note = body.get('note')
        if isinstance(note, str) and note:
            args += ['--note', note]
        ok, out = self._run_cli_sync(args)
        with _JOBS_LOCK:
            _bump_events_locked()  # set rewrites build_state; nudge gen anyway
        return self._send_json(200 if ok else 500,
                               {'ok': ok, 'output': out} if ok
                               else {'ok': False, 'output': out,
                                     'error': out or 'set failed'})

    def _serve_identity(self, body):
        """Fix a song's names and links after it exists (CLI verb `identity`).

        Only the fields actually SENT are forwarded, so the box can save one
        field without clearing the six it didn't show. The CLI is the only
        writer — it keeps build_state and content.json in step and reopens the
        rungs that bake the strings into the page."""
        key = (body.get('key') or '').strip()
        if not key:
            return self._send_json(400, {'error': 'missing key'})
        args = ['identity', key]
        for field in ('title_jp', 'title_en', 'artist', 'artist_en',
                      'yt', 'apple', 'art'):
            if field in body and isinstance(body[field], str):
                args += ['--' + field.replace('_', '-'), body[field].strip()]
        if len(args) == 2:
            return self._send_json(400, {'error': 'no fields to change'})
        ok, out = self._run_cli_sync(args)
        with _JOBS_LOCK:
            _bump_events_locked()
        return self._send_json(200 if ok else 500,
                               {'ok': ok, 'output': out} if ok
                               else {'ok': False, 'output': out,
                                     'error': out or 'identity failed'})

    def _serve_init(self, body):
        key = (body.get('key') or '').strip()
        if not key:
            return self._send_json(400, {'error': 'missing key'})
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', key):
            return self._send_json(400, {
                'error': 'key must be lowercase alphanumeric/hyphens'})
        args = ['init', key]
        for field in ('title_jp', 'title_en', 'artist', 'artist_en',
                      'yt', 'apple', 'art'):
            v = body.get(field)
            if isinstance(v, str) and v.strip():
                args += ['--' + field.replace('_', '-'), v.strip()]
        # persist the picked catalog candidate's duration — the lyric fetch
        # falls back to it when the apple URL is blank (meta.duration_ms).
        try:
            dms = int(body.get('duration_ms') or 0)
        except (TypeError, ValueError):
            dms = 0
        if dms > 0:
            args += ['--duration-ms', str(dms)]
        # a start point the user set by hand on the New song screen
        try:
            ms = int(body.get('music_start_ms') or 0)
        except (TypeError, ValueError):
            ms = 0
        if ms > 0:
            args += ['--music-start-ms', str(ms)]
        # colors eyedropped off the cover (validated CLI-side, so junk from a
        # stale tab can't write a half-themed design.json)
        design = body.get('design')
        if isinstance(design, dict) and design:
            args += ['--design', json.dumps(design, ensure_ascii=False)]
        ok, out = self._run_cli_sync(args)
        with _JOBS_LOCK:
            _bump_events_locked()
        if not ok:
            return self._send_json(500, {'ok': False, 'output': out,
                                         'error': out or 'init failed'})
        # …and start the work at once. By this point the New song screen has
        # already settled every question these steps ask — which lyric source,
        # which video, where the music starts — so making the owner come back to a
        # checklist and press run on each of them was pure ceremony. The walk
        # fetches the lyrics, draws the waveform and force-aligns the timing,
        # then halts at author_data (the teaching), which is the first thing
        # that genuinely needs a person. Stoppable from the job bar like any job.
        job_id = self._enqueue(key, f'prep:{key}',
                               [PY3, str(MANAOKE_BUILD), 'run', key, '--auto'])
        return self._send_json(200, {'ok': True, 'output': out, 'job_id': job_id})


def _watch_parent_and_exit(initial_ppid):
    # Suicide when reparented (parent shell / launcher died). Without this,
    # instances spawned by short-lived parents pile up indefinitely on
    # ascending ports. Polls because macOS lacks PR_SET_PDEATHSIG.
    while True:
        time.sleep(2)
        if os.getppid() != initial_ppid:
            os._exit(0)


def serve(host='127.0.0.1', port=8773, detach=False, dev=False):
    """Start the Denmoku server. Blocks until Ctrl-C.

    Tries `port` first; if it's taken (a zombie or another instance), falls
    back to a kernel-assigned free port via bind-to-0. Either way, writes the
    actual URL to builder/.app-url so Dock-click / focus-url / tests have a
    single source of truth.

    dev=True (a read-only mirror for headless UI work): refuse every mutating
    POST, do NOT start the FIFO worker (two workers on shared builds/ = the
    exact race everything else guards), and do NOT clobber .app-url (the real
    Denmoku's Dock pointer must survive). Run it in tmux on a spare port."""
    global DEV_MODE
    DEV_MODE = dev
    ThreadingHTTPServer.allow_reuse_address = True
    if not detach:
        threading.Thread(target=_watch_parent_and_exit, args=(os.getppid(),),
                         daemon=True).start()

    if not dev:
        threading.Thread(target=_job_worker, daemon=True, name='job-worker').start()

    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        if getattr(e, 'errno', None) == 48:  # EADDRINUSE
            print(f'port {port} is taken — falling back to a free port.',
                  file=sys.stderr)
            httpd = ThreadingHTTPServer((host, 0), Handler)
        else:
            raise

    actual_port = httpd.server_port
    url = f'http://{host}:{actual_port}/'
    if not dev:                       # a dev mirror must not steal the Dock pointer
        try:
            (HERE / '.app-url').write_text(url + '\n')
        except OSError:
            pass

    print(f'Denmoku running at {url}' + ('  [DEV — read only]' if dev else ''))
    print('Press Ctrl-C to stop.')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nBye.')
    finally:
        httpd.server_close()


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Denmoku — songcraft builder server')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8773)
    ap.add_argument('--detach', action='store_true',
                    help='Survive parent-process exit (default: die when orphaned)')
    ap.add_argument('--dev', action='store_true',
                    help='Read-only mirror: refuse writes, no job worker, keep '
                         '.app-url intact (for headless UI work on a spare port)')
    args = ap.parse_args()
    serve(args.host, args.port, detach=args.detach, dev=args.dev)
