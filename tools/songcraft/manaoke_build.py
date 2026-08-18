#!/usr/bin/env python3
"""
Manaoke Song Page Builder — pipeline orchestrator.

One command per song, one linear sequence of steps. Each step knows:
  - what it does (a plain-English blurb, teach-don't-decorate),
  - who owns it — `local` (this code runs it), `cli` (hand the prompt to Claude
    Code / a CLI you drive), or `external` (a signed-in tab / a hosted service),
  - whether it can run automatically right now, and the exact command/prompt.

State lives in tools/songcraft/builds/<key>.build_state.json (NOT in the deploy
dir — deploy dirs stay lean). The HTML dashboard (builder/index.html) reads that
JSON so you can watch the sequence, flip ownership, tick a step done, or copy a
step's command to run it yourself.

Usage:
  manaoke_build.py init <key> --title-jp .. --title-en .. --artist .. \
        --artist-en .. --yt <id> --art <url> --apple <url> --level Intermediate \
        --template inochi-mijikashi-e03jz0 --slug <key>-<rand>
  manaoke_build.py status <key>
  manaoke_build.py run <key> <step>        # run one step
  manaoke_build.py run <key> --auto        # run every auto-able step in order, stop at a gate
  manaoke_build.py set <key> <step> --owner cli --done   # flip ownership / mark done
  manaoke_build.py dash [<key>]            # (re)write builder/index.html + builds/index.json
  manaoke_build.py doctor [--fast]         # preflight: datasets/envs/models/binaries/tokens/
                                           # services PASS/WARN/FAIL table; exit 1 on FAIL;
                                           # --fast skips the slow env-import sweeps
  manaoke_build.py promote <key> [--dry-run]
                                           # repoint root SONGS[] to the current slug dir +
                                           # refresh cardAccent; --dry-run prints the diff only
  manaoke_build.py rebuild <key> [--dry-run] [--fresh-slug] [--template <dir>]
                                           # plan staleness vs builds/<folder>.deps.json,
                                           # then re-run assemble→validate (never deploys)
  manaoke_build.py rebuild --all [--dry-run]   # staleness table for every song; rebuild the stale
  manaoke_build.py rebuild --why <clip|sha8>   # which songs/lines/concats ship this clip (report only)
  manaoke_build.py lexicon add <word> --kana <reading> [--carrier ..] [--reason ..] [--clip ..]
  manaoke_build.py lexicon list            # the site-wide pronunciation memory (E15)
  manaoke_build.py lexicon check [<key>]   # run the E15 gate on built song(s) — "the owner heard a bad clip"
  manaoke_build.py gradient set <key> [--c1 #hex --c2 --c3 --hi] [--fb "#a,#b,#c"]
                                 [--speed F] [--motion drift|orbit|sway|pulse]
                                 [--amp F] [--force-pale]
                                           # write design.gradient into builds/<key>.content.json
  manaoke_build.py gradient set --all [..] # same dials as site-wide builds/gradient.defaults.json
  manaoke_build.py gradient show [<key>]   # effective values (cover / default / override per field)
  manaoke_build.py gradient clear <key> [--colors|--motion]   # drop override subset
"""
import argparse, json, os, re, secrets, shutil, string, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_io          # flock + atomic-replace for the shared JSON state
                         # (backlog f8a1fe93 — Denmoku jobs + CLI walks write
                         # build_state concurrently; torn JSON strands builds)

ROOT = Path(__file__).resolve().parents[2]          # ~/manaoke-site
SONGS = ROOT / 'songs'
BUILDS = Path(__file__).resolve().parent / 'builds'
BUILDS.mkdir(exist_ok=True)
DASH = Path(__file__).resolve().parent / 'builder' / 'index.html'

# ---- the canonical pipeline (order matters) --------------------------------
# owner: local | cli | external          auto: orchestrator can run it unattended
# optional: NOT on the road to shipping. The --auto walk steps over an optional
#   step instead of stopping at it, and it is left out of the done/total count,
#   so a song that is finished actually reads finished. Podcast is the case that
#   forced this (2026-07-28): it is a manual hand-off sitting between drill_concat
#   and the validators, so the walk halted there every single time and every
#   shipped song had to be hand-marked done to get past a step the owner had already
#   ruled optional. Run it deliberately (`run <key> podcast`) when you want it.
STEPS = [
    dict(key='grab_song', title='Grab the song', owner='local', auto=True,
         blurb='Lock the song identity: JP/EN title, artist, YouTube id, iTunes '
               'artwork, Apple Music url, difficulty. Everything downstream keys off this.'),
    dict(key='lyrics', title='Timed lyric sheet', owner='local', auto=True,
         blurb='Fetch timed lyrics automatically — tries Apple word-level (only if a '
               'token is configured, see fetch_timed_lyrics.py), then NetEase '
               'word-level, then LRCLIB line-level. Self-contained: all sources are '
               'vendored in tools/songcraft/lyric_sources/, no sibling repos needed. '
               'Line-level results get upgraded to word-level by the sync step.',
         cmd='python3 tools/songcraft/fetch_timed_lyrics.py <key>   '
             '# --source apple|netease|lrclib to force one; --force to refetch'),
    dict(key='whisper_sync', title='Line the words up with the singing', owner='local', auto=True,
         blurb='Listens to the song and moves every lyric line to the moment it is '
               'actually sung, so the words light up in time. It separates the singing '
               'from the band first, which is why it takes a few minutes. If the lyric '
               'sheet only had whole lines, this is also where each word inside a line '
               'gets its own moment. Needs demucs + ctc-forced-aligner in the parler env.',
         cmd='python3 tools/songcraft/whisper_sync.py <key> --yt <id> --apply   # Apple TTML\n'
             'python3 tools/songcraft/whisper_sync.py <key> --yt <id> --words --apply   # LRCLIB'),
    dict(key='scaffold', title='Cut the lines into word cards', owner='local', auto=True,
         blurb='Cuts every line into the tappable word cards people study, and fills '
               'in everything a machine can know on its own: the reading, the romaji, '
               'how the word is spoken, a dictionary gloss. What it deliberately leaves '
               'blank — the translations, the explanations, the teaching voice — is the '
               'list the next step works from. It never overwrites cards that already '
               'have writing in them.',
         cmd='/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python '
             'tools/songcraft/scaffold.py <key>   # --force replaces a stale skeleton'),
    dict(key='author_data', title='Author the study data', owner='cli', auto=False,
         blurb='Fill the empty fields the skeleton left: a translation and an '
               'explainer for every line, a spoken definition and in-line context for '
               'every card, section names and intros. The mechanical half (cards, '
               'readings, romaji, particles, sections) is already written by scaffold — '
               'this step is the teaching voice only. AI-drafts it, you review.',
         cmd='PROMPT: Fill the empty fields in builds/<key>.content.json to the '
             'SONG-CONTRACT for <TITLE> — line_tr + line_explain for every line, '
             'en_speak/context/gloss for every card, section name/description/speak_en. '
             'Do not re-segment: the cards are gate-clean already. Spoken English only '
             'in en_speak/context/gloss/speak_en; lone は → jp_speak わ.'),
    dict(key='assemble', title='Assemble the page', owner='local', auto=True,
         blurb='Clone the current production page, inject this song\'s data.json + '
               'manifest, splice the LINE_TR / LINE_EXPLAIN literals, retarget the '
               'YouTube id, titles, slug refs and version chip, and add the per-song '
               '_redirects lines that point audio at this song\'s shared assets. Runs '
               'content_to_data first, which emits builds/<key>.audio_jobs.json — the job '
               'list en_audio/jp_audio consume, which is why assemble precedes the audio '
               'steps in the DAG. Palette is fully universal now: cover_palette derives '
               'EVERY themed color from the artwork — field-c1/2/3/hi, the fb1-3 bloom '
               'accents, the base1-3 radial, and the body-g1-4 body gradient — the template '
               'has zero baked palette literals left. In one line: this is where the '
               'song becomes a real web page. QA the colors standalone: '
               'tools/songcraft/verify_palette.py.',
         cmd='python3 tools/songcraft/manaoke_build.py run <key> assemble'),
    dict(key='en_audio', title='English voice', owner='local', auto=True,
         blurb='Render the spoken English (definitions, in-line context, section '
               'intros, line explanations) with Kokoro am_michael, two-pass '
               'loudnorm to -16 LUFS. The English voice never speaks Japanese. '
               'ONE gen_audio.py pass renders BOTH the EN and JP clips (it reads the '
               'audio_jobs.json content_to_data emits in assemble — so assemble runs first).',
         cmd='/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python '
             'tools/songcraft/gen_audio.py <key> <key>   '
             '# args: <build-key> <asset-folder>; ONE pass renders EN + JP (what run_audio runs, NOT gen_en_audio.py)'),
    dict(key='jp_audio', title='Japanese voice', owner='local', auto=True,
         blurb='Render every Japanese word + full line with the JP voice engine '
               '(Kokoro jf_alpha; lone particles routed to real voices); a lone は is '
               'spoken わ; short garbled words get swapped for a real human recording. '
               'Same gen_audio.py pass as the English voice — there is no separate JP script.',
         cmd='/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python '
             'tools/songcraft/gen_audio.py <key> <key>   '
             '# args: <build-key> <asset-folder>; the same gen_audio pass renders JP too (what run_audio runs)'),
    dict(key='pronunciation', title='Listen back to every spoken word', owner='local', auto=True,
         blurb='Plays back every Japanese clip the box just made and checks it says '
               'the right thing. Anything the machine voice mangles is replaced with a '
               'real human recording of that exact word. Runs '
               'on the FINAL served mono-mp3 clips so what ships is what was verified — '
               'owner: "this should be happening as we go." Now QAs particles too: two '
               'read-back attempts must BOTH flag the same wrong sound (corroboration, '
               '1-mora words are the noisiest for whisper) before a swap fires. And '
               'gen_audio.py routes lone particles (にとものがでやかねよなへをはぞさお) '
               'around Kokoro entirely — a curated human clip from tools/human_audio/library/ '
               'or a loud failure with the exact Qwen3 phrase-cut command, never a silent bad TTS.',
         cmd='conda run -n parler python tools/songcraft/verify_jp_pronunciation.py <slug> <folder> --fix'),
    dict(key='pitch', title='Pitch-accent data', owner='local', auto=True,
         blurb='Works out whether each word rises or falls when a Japanese speaker '
               'says it — the moving line on the pitch card. Written to '
               '_assets/<folder>/pitch_data/pitch.json, keyed by '
               'jp_speak||jp. Engine is pyopenjtalk-plus + kanjium (vendored at '
               'tools/songcraft/pitch_pipeline/, runs in the qwentts env); gen_pitch is '
               'INCREMENTAL so only new words compute. The card falls back to a plain '
               'mora row for any word still missing, so a miss degrades, never breaks.',
         cmd='/opt/homebrew/Caskroom/miniforge/base/envs/qwentts/bin/python '
             'tools/songcraft/gen_pitch.py <slug> <folder>'),
    dict(key='drill_concat', title='Stitch the word clips into drills', owner='local', auto=True,
         blurb='Concatenate each line\'s word-by-word drill into one mp3 (word JP → '
               'gloss → … → full line → explanation) and re-inject the timing map, so '
               'the drill plays with a single tap instead of many flaky fetches.',
         cmd='python3 tools/songcraft/build_drill_concat.py songs/<slug>'),
    # The page is assembled BEFORE any audio exists, so rendering the clips
    # always invalidates the cache key baked into it — E18 fails by
    # construction on every first build, and "run all (auto)" could never
    # finish one: it stopped at validate and waited for a person who happened
    # to know that "re-assemble" was the answer. The re-assemble was always
    # part of the recipe (the assemble step's own note says the drill concat
    # re-runs on it); it just was not part of the ladder. Now it is, so the
    # walk closes its own loop.
    dict(key='reassemble', title='Rebuild the page for the new audio',
         owner='local', auto=True,
         blurb='Re-assemble the page now that the clips exist: fresh AUDIO_V + drill '
               '?v= cache keys, so devices fetch the new bytes instead of replaying '
               'a year-old cached clip. Mechanical — same assemble, second pass.',
         cmd='python3 tools/songcraft/manaoke_build.py run <key> reassemble'),
    dict(key='podcast', title='Podcast (optional extra)', owner='cli', auto=False, optional=True,
         blurb='Deep-research the song, write a two-host script (English host never '
               'speaks Japanese; a dedicated Japanese voice says the lyrics), and '
               'render it. Owned by whichever model does the research + TTS.',
         cmd='PROMPT: Deep-research <TITLE> by <ARTIST>; write podcast_script as '
             '[speaker,text,startSec] with a JP reader for every Japanese line; render.'),
    dict(key='podcast_align', title='Podcast word timing', owner='local', auto=True, optional=True,
         blurb='Force-align the finished podcast audio to its script so every word '
               '(English and Japanese) highlights exactly when spoken in Immerse.',
         cmd='conda run -n parler python tools/podcast_align/align_podcast.py '
             '--audio <podcast.mp3> --data songs/<slug>/data.json'),
    dict(key='validate', title='Check everything before it ships', owner='local', auto=True,
         blurb='The last look before anyone else sees it: is every word covered, does '
               'every clip exist and match the page, is any English voice about to try '
               'to speak Japanese. Runs validate_song + validate_tts_safety + the '
               'every-line drill-tail check. All '
               'must pass 0 before deploy. validate_song now also runs E8: a particle '
               'word (は/へ/を) MUST author its spoken form in jp_speak (わ/え/お) — '
               'catches the raw-particle-to-TTS bug at authoring time, before audio '
               'even renders.',
         cmd='python3 tools/validate_song.py songs/<slug> && '
             'python3 tools/validate_tts_safety.py songs/<slug>/tts_manifest.json'),
    # "Norelco" is this card's in-house nickname and it stays in the code and the
    # docs, but a step TITLE is read by whoever is building a song — it has to say
    # what the step does in words that need no glossary.
    dict(key='landing_card', title='Make its card for the library page', owner='local', auto=True,
         blurb='Add this song\'s card to the library landing: real album '
               'jacket + a card accent that MATCHES the song page\'s living-gradient '
               'dominant. (Root landing itself is only repointed on promote.)',
         cmd='python3 tools/songcraft/manaoke_build.py run <key> landing_card'),
    dict(key='deploy', title='Deploy a preview', owner='local', auto=False,
         blurb='Commit + push so Cloudflare serves the page at its private random-slug '
               'URL, then verify it live headless and hand over the bare URL. NOT auto: '
               'deploying is a commit + push, and the standing rule is to push ONCE at the '
               'end (never incrementally) — so this is the operator\'s call, a manual gate.',
         cmd='git add -A && git commit -m "<key> preview" && git push'),
    dict(key='promote', title='Put it on the main page', owner='local', auto=False,
         blurb='Only when you say keep it: repoint the root landing\'s SONGS[] url '
               'for this song to the current preview dir (and refresh its cardAccent '
               'from the build). Never automatic. --dry-run shows the exact '
               'index.html diff without writing; the real run prints the diff it '
               'applied and journals a lessons entry. Refuses when the dir is '
               'missing, validate isn\'t done, or the song isn\'t on the landing yet '
               '(adding a card is landing_card.py --promote\'s job).',
         cmd='python3 tools/songcraft/manaoke_build.py promote <key> --dry-run   '
             '# review the diff, then drop --dry-run'),
]
STEP_BY_KEY = {s['key']: s for s in STEPS}


def state_path(key): return BUILDS / f'{key}.build_state.json'


def _sync_steps(st):
    """Reconcile a persisted song's steps against the canonical STEPS above —
    order, title, blurb, owner, auto, optional, cmd. new_state() bakes a COPY of each
    field into the song's build_state.json at init time, so a later edit to
    STEPS (a reworded blurb, a new step like `pronunciation`) would otherwise
    never reach an already-`init`'d song. Only status/note/artifacts, the
    per-run facts, are preserved per step; everything else always tracks the
    code. Missing steps are inserted (pending) in their canonical position;
    steps no longer in STEPS are dropped."""
    existing = {s['key']: s for s in st.get('steps', [])}
    steps = []
    for s in STEPS:
        ex = existing.get(s['key'], {})
        row = dict(key=s['key'], title=s['title'], blurb=s['blurb'],
                   owner=s['owner'], auto=s['auto'], cmd=s.get('cmd', ''),
                   optional=bool(s.get('optional')),
                   status=ex.get('status', 'pending'),
                   note=ex.get('note', ''),
                   artifacts=ex.get('artifacts', []))
        # A hand-off step the walk stopped at used to be recorded 'blocked'
        # (2026-07-28: now 'waiting'). Convert the ones already on disk — a
        # step nobody has done yet is not a failure, and the dot colour says so.
        if row['status'] == 'blocked' and row['owner'] != 'local':
            row['status'] = 'waiting'
        # per-run runner-liveness facts (the stale-running reaper reads these);
        # only meaningful alongside status=='running' but always preserved so a
        # reconcile pass never erases the evidence the reaper needs.
        for k in ('pid', 'run_started'):
            if k in ex:
                row[k] = ex[k]
        steps.append(row)
    # `scaffold` joined the DAG on 2026-07-29, after nine songs had already been
    # authored by hand. Their content.json IS the skeleton (and more), so leaving
    # the newly-inserted step 'pending' would report a finished song as one step
    # short forever. Backfill it from the file that proves it happened.
    # Same story for `reassemble` (2026-07-29): a song that already passed
    # validate has, by definition, a page whose cache keys match its clips —
    # the second assemble happened, by hand, before the ladder knew about it.
    done = {r['key'] for r in steps if r['status'] == 'done'}
    for row in steps:
        if (row['key'] == 'reassemble' and row['status'] == 'pending'
                and 'validate' in done):
            row['status'] = 'done'
            row['note'] = ('[reassemble] this song already passed the audio '
                           'cache-key gate, so the second assemble had happened '
                           'before this step joined the pipeline.')
    for row in steps:
        if (row['key'] == 'scaffold' and row['status'] == 'pending'
                and (BUILDS / f'{st["key"]}.content.json').exists()):
            row['status'] = 'done'
            row['note'] = ('[scaffold] the study data was already written when this '
                           'step was added to the pipeline — nothing to skeleton.')
    st['steps'] = steps
    return st


# ---- stale-running reaper (backlog e03ef4d2) --------------------------------
# A SIGTERM/SIGKILL (Denmoku Stop, a crash, a reboot) can strand a step on
# 'running' forever — do_run's BaseException handler never fires. Every runner
# records its own pid (os.getpid()) when it flips a step to 'running' (works
# for direct CLI runs AND the Denmoku server, which shells manaoke_build.py as
# a subprocess — the recorded pid is the step-runner process either way). Any
# later invocation (load()/dash/state writes) reaps: a 'running' step whose
# recorded pid is dead (or was never recorded) and is older than REAP_AGE_S
# flips to 'failed' so the dashboard stops showing a phantom pulse.
REAP_AGE_S = 600


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, someone else's — still alive
    except (OSError, TypeError, ValueError):
        return False


def reap_stale_running(st):
    """Flip dead 'running' steps to 'failed'. Returns the reaped step keys."""
    reaped = []
    now = int(time.time())
    for s in st.get('steps', []):
        if s.get('status') != 'running':
            continue
        pid = s.get('pid')
        if pid and _pid_alive(pid):
            continue                      # a live runner owns it — hands off
        started = s.get('run_started') or st.get('updated') or 0
        if now - started < REAP_AGE_S:
            continue                      # too fresh to judge — let it breathe
        when = (time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started))
                if started else 'unknown')
        s['status'] = 'failed'
        s['note'] = (f"reaped: runner died (pid {pid or 'unrecorded'}, "
                     f"running since {when}) — re-run the step.")
        s.pop('pid', None)
        s.pop('run_started', None)
        reaped.append(s['key'])
    return reaped


def load(key):
    p = state_path(key)
    if not p.exists():
        sys.exit(f'no build state for {key!r} — run: manaoke_build.py init {key} ...')
    st = _sync_steps(json.loads(state_io.locked_read(p)))
    reaped = reap_stale_running(st)
    if reaped:
        st['updated'] = int(time.time())
        state_io.locked_write(p, json.dumps(st, ensure_ascii=False, indent=2))
        for skey in reaped:
            print(f'[reap] {key}.{skey}: was stuck on running with a dead runner — '
                  f'flipped to failed.', file=sys.stderr)
    return st


def save(st):
    st['updated'] = int(time.time())
    state_io.locked_write(state_path(st['key']),
                          json.dumps(st, ensure_ascii=False, indent=2))


def new_state(key, meta, template, slug):
    steps = []
    for s in STEPS:
        steps.append(dict(key=s['key'], title=s['title'], blurb=s['blurb'],
                          owner=s['owner'], auto=s['auto'], cmd=s.get('cmd', ''),
                          optional=bool(s.get('optional')),
                          status='pending', note='', artifacts=[]))
    return dict(key=key, meta=meta, template=template, slug=slug,
                created=int(time.time()), updated=int(time.time()), steps=steps)


def get_step(st, skey):
    for s in st['steps']:
        if s['key'] == skey:
            return s
    sys.exit(f'unknown step {skey!r}')


# ---- step runners (the ones the orchestrator can own) ----------------------

HERE = Path(__file__).resolve().parent
PARLER = '/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python'
QWENTTS = '/opt/homebrew/Caskroom/miniforge/base/envs/qwentts/bin/python'  # pyopenjtalk-plus (pitch)


# Library chatter that says nothing about this build: torch/transformers
# deprecation notices and their indented continuation lines. They arrive on
# stderr, and stderr is where a step's ONE actionable message also arrives —
# the "cut a real voice for this word" instruction that tells you what to do
# next. Keeping the tail of stderr kept the warnings and threw the instruction
# away: en_audio reported "3 failed" and not one reason why
# (STRAWBERRY ANNIVERSARY, 2026-07-30).
_NOISE = re.compile(r'\b(UserWarning|FutureWarning|DeprecationWarning|'
                    r'RuntimeWarning)\b')


def _drop_noise(text):
    """Python prints a warning as exactly two lines: the message, then the one
    source line that triggered it. Drop the pair and nothing else — an earlier
    version ate every indented line after a warning, which is the shape of the
    instructions a failed step prints."""
    keep, pending = [], False
    for line in (text or '').splitlines():
        if _NOISE.search(line):
            pending = True
            continue
        if pending:
            pending = False
            if line[:1] in (' ', '\t'):
                continue                # the source line under the warning
        keep.append(line)
    return '\n'.join(keep)


def _sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True)
    out = (r.stdout[-2500:] + _drop_noise(r.stderr)[-1200:]).strip()
    return r.returncode == 0, out


def _git(args, cwd=None):
    """One git call from the repo root. Returns (ok, combined_output)."""
    r = subprocess.run(['git'] + args, cwd=cwd or ROOT,
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _require_clean_main():
    """A ship/promote push targets origin/main. Refuse if HEAD isn't the main
    branch or a rebase/merge is mid-flight — otherwise a scoped commit lands on a
    detached HEAD and the bare push fails after the fact (the state is then half
    applied). Exits with a plain message; returns nothing on success."""
    ok, ref = _git(['symbolic-ref', '--quiet', 'HEAD'])
    if not ok or ref.strip() != 'refs/heads/main':
        sys.exit(f'REFUSED: not on the main branch (HEAD = {ref.strip() or "detached"}). '
                 f'Finish the branch/rebase and switch back to main first.')
    for marker in ('rebase-merge', 'rebase-apply', 'MERGE_HEAD', 'CHERRY_PICK_HEAD'):
        if (ROOT / '.git' / marker).exists():
            sys.exit(f'REFUSED: a git {marker} is in progress — finish it first.')


def _folder(st):
    return st['meta'].get('slug') or st['key']


def run_assemble(st):
    """content.json (+licensed lyrics) → data.json/manifest/line-maps, then clone
    the template page onto songs/<slug> with splice/retarget/palette/_redirects."""
    key, slug = st['key'], st['slug']
    if not (BUILDS / f'{key}.content.json').exists():
        return False, f'no builds/{key}.content.json — author_data (the teaching) must land first.'
    if not (BUILDS / f'{key}.lyrics.json').exists():
        return False, f'no builds/{key}.lyrics.json — the licensed lyrics step must land first.'
    ok, out = _sh([PARLER, str(HERE / 'content_to_data.py'), key, slug])
    if not ok:
        return False, 'content_to_data failed:\n' + out
    m = st['meta']
    ok2, out2 = _sh([PARLER, str(HERE / 'assemble_page.py'), key, slug, _folder(st), st['template'],
                     '--yt', m.get('yt', ''), '--title-jp', m.get('title_jp', ''),
                     '--artist', m.get('artist', ''), '--art', m.get('art', '')])
    out = out + '\n' + out2
    # assemble regenerates index.html, which drops the DRILL_MAP patch + the ?v
    # asset hashes — so always re-run those here (bake it in so it can't be forgotten).
    if ok and ok2:
        # DAG order: a FRESH song assembles BEFORE audio renders (assemble emits the
        # audio_jobs.json the audio steps consume), so on the first pass there are no
        # clips to concat — skip the drill concat with a note; it runs on the
        # re-assemble after en_audio/jp_audio. A re-assemble on a built song (clips
        # already present) chains drill_concat exactly as before. Bump always runs
        # (data.json / manifest changed regardless).
        audio_jp = ROOT / 'songs' / '_assets' / _folder(st) / 'audio' / 'jp'
        has_clips = audio_jp.is_dir() and (any(audio_jp.glob('*.mp3')) or any(audio_jp.glob('*.wav')))
        if has_clips:
            okd, outd = _sh(['python3', str(HERE / 'build_drill_concat.py'), f'songs/{slug}', '--force'])
            out += '\n[drill] ' + (outd.splitlines()[-1] if outd else '')
            ok2 = ok2 and okd
        else:
            out += ('\n[drill] skipped: no rendered JP clips yet — first-pass assemble runs '
                    'BEFORE audio; drill concat re-runs on the re-assemble after '
                    'en_audio/jp_audio.')
        okb, outb = _sh(['python3', str(ROOT / 'tools' / 'bump_asset_versions.py')])
        out += '\n[bump] ' + (outb.splitlines()[-1] if outb else '')
        # faithful-clone guard: the built page must differ from the template
        # ONLY in data/palette/paths. Any structural drift (a stray patch, a
        # template that fell behind) fails the build so the next song can't
        # silently diverge from the reference. This is what keeps Silhouette
        # (and every future song) inheriting everything "creep hype" nails.
        okp, outp = _sh(['python3', str(HERE / 'parity_audit.py'), slug, st['template']])
        out += '\n[parity] ' + (outp.strip().splitlines()[0] if outp else '')
        if not okp:
            out += '\n' + outp
        ok2 = ok2 and okp
        # audio-version guard: no playback site (card/pitch/drill/podcast) may
        # bypass _withAudioV — an unversioned player replays year-cached bytes
        # after a clip swap (the "still sounds like TTS" trap). Lint both the
        # template and the built page.
        # …the template by its RESOLVED dir: the recorded one can be gone
        # (pruned 2026-07-28), and linting a missing path was three lines of
        # traceback in the middle of a passing build.
        import template_dir
        tdir = template_dir.resolve_template(st['template'], quiet=True)
        for target in ([tdir.name] if tdir else []) + [slug]:
            okl, outl = _sh(['python3', str(HERE / 'lint_template.py'), f'songs/{target}'])
            out += '\n[lint] ' + (outl.strip().splitlines()[0] if outl else '')
            if not okl:
                out += '\n' + outl
            ok2 = ok2 and okl
        # deps re-stamp: assemble_page recorded page.out_sha8 of its INTERMEDIATE
        # html; the drill splice + bump above rewrote the file since, so re-hash
        # the final bytes or every future `rebuild` plan reports phantom drift.
        if ok2:
            import hashlib as _hl
            dp = BUILDS / f'{_folder(st)}.deps.json'
            page_file = SONGS / slug / 'index.html'
            if dp.exists() and page_file.exists():
                deps = json.loads(dp.read_text())
                deps.setdefault('page', {})['out_sha8'] = _hl.sha256(page_file.read_bytes()).hexdigest()[:8]
                dp.write_text(json.dumps(deps, indent=1))
                out += '\n[deps] page.out_sha8 re-stamped to final bytes'
    return (ok and ok2), out, [str(SONGS / slug / 'index.html')]


def run_audio(st):
    ok, out = _sh([PARLER, str(HERE / 'gen_audio.py'), st['key'], _folder(st)])
    return ok, out


def run_pitch(st):
    # pitch-accent engine (pyopenjtalk-plus + kanjium) lives in the qwentts env;
    # gen_pitch is incremental (only new words trigger the heavy import), so this
    # is cheap on a rebuild whose word set didn't change.
    ok, out = _sh([QWENTTS, str(HERE / 'gen_pitch.py'), st['slug'], _folder(st)])
    return ok, out


def run_landing_card(st):
    m = st['meta']
    acc = (BUILDS / f'{st["key"]}.cardaccent.txt')
    accent = acc.read_text().strip() if acc.exists() else '#888888'
    ok, out = _sh(['python3', str(HERE / 'landing_card.py'), st['slug'],
                   '--title-jp', m.get('title_jp', ''), '--artist', m.get('artist', ''),
                   '--art', m.get('art', ''), '--accent', accent])
    return ok, out


def run_validate(st):
    slug = st['slug']
    d = SONGS / slug
    if not d.exists():
        return False, 'no build dir yet (assemble first)'
    out = []
    ok = True
    # Acoustic sweep FIRST (parler env — numpy/ffmpeg physics): refreshes
    # builds/<folder>.clip_suspects.json so validate_song's E19 judges fresh
    # verdicts. The sweep itself never fails the step — a missing or stale
    # sidecar is exactly what E19 errors on, loudly.
    sw = subprocess.run([PARLER, str(HERE / 'sweep_clip_physics.py'), st['key']],
                        cwd=ROOT, capture_output=True, text=True, timeout=1200)
    out.append('$ sweep_clip_physics.py (acoustic clip physics -> E19)\n'
               + (sw.stdout or sw.stderr or '')[-1200:])
    for cmd in (['python3', 'tools/validate_song.py', f'songs/{slug}'],
                ['python3', 'tools/validate_tts_safety.py', f'songs/{slug}/tts_manifest.json']):
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out.append(f'$ {" ".join(cmd)}\n{r.stdout[-1500:]}{r.stderr[-500:]}')
        ok = ok and r.returncode == 0
    # Segmentation granularity check (needs fugashi → parler python). NON-FATAL:
    # the canon rule is "one dictionary lookup per card" (inochi 消えて|しまう,
    # 長生き|する), but existing live songs carry deferred strays the owner chose to
    # backlog, so a rebuild for an unrelated reason must not be blocked. It runs
    # LOUD instead — any over-merge on the song being built is printed with its
    # suggested split and a pointer to the backlog, so it can never slip by
    # silently again (the gap that shipped Headlong's 付けてほしい). Split it, or
    # add a genuine whole-cluster to KEEP_WHOLE in validate_segmentation.py.
    seg = subprocess.run([PARLER, str(HERE / 'validate_segmentation.py'), f'songs/{slug}'],
                         cwd=ROOT, capture_output=True, text=True)
    seg_txt = (seg.stdout or '').strip()
    if seg.returncode == 0 and '✗' not in seg_txt:
        out.append('$ validate_segmentation.py\n' + (seg_txt or '✓ SEGMENTATION: clean.'))
    else:
        out.append('$ validate_segmentation.py\n' + seg_txt +
                   '\n[segmentation] non-word card(s) above are NON-FATAL but real — '
                   'break them into their dictionary-word parts (jisho is the arbiter). '
                   'Track: python3 tools/songcraft/backlog.py import-segmentation')
    # Alignment scorecard (the owner's standing order 2026-07-07: mora-level checked
    # CONSTANTLY, benchmarked vs external refs + the acoustic listen gate,
    # history appended so convergence toward 100% is visible). NON-FATAL for
    # now — it runs LOUD and writes builds/<key>.alignment_score.json +
    # alignment_history.jsonl; harden thresholds once baselines settle.
    sc = subprocess.run([PARLER, str(HERE / 'alignment_scorecard.py'), st['key']],
                        cwd=ROOT, capture_output=True, text=True, timeout=2400)
    out.append('$ alignment_scorecard.py (informational — the how-close-to-100 number)\n'
               + (sc.stdout or sc.stderr or '')[-1200:])
    # Creep-hype benchmark (owner, 2026-07-31: "our benchmark is creep hype,
    # creep hype gets everything correct"). parity_audit proves the page is a
    # faithful clone of the TEMPLATE; this measures the RENDERED page against
    # the promoted クリープハイプ page — the reference that actually works.
    # Candidate = the Denmoku pre-deploy preview (prod-faithful asset routing);
    # benchmark = whatever root SONGS[] promotes for inochi (the script reads
    # it). Measured drift FAILS validate; a benchmark that cannot run is LOUD
    # but non-fatal so a CLI-only validate isn't blocked by infrastructure.
    try:
        import urllib.request
        urllib.request.urlopen('http://127.0.0.1:8773/api/state', timeout=3)
        _dm_up = True
    except Exception:
        _dm_up = False
    if not _dm_up:
        out.append('$ bench_vs_creephype.js\nSKIPPED — Denmoku server (:8773) not '
                   'running, so there is no pre-deploy page to measure. Start it '
                   'and re-run validate: the page must measure like creep hype.')
    else:
        try:
            bench = subprocess.run(
                ['node', str(HERE / 'bench_vs_creephype.js'),
                 f'http://127.0.0.1:8773/preview/{slug}/'],
                cwd=ROOT, capture_output=True, text=True, timeout=300)
            btail = ((bench.stdout or '') + (bench.stderr or ''))[-1500:]
            out.append('$ bench_vs_creephype.js (measured against the promoted '
                       'creep hype page)\n' + btail)
            if bench.returncode == 1:
                ok = False          # measured drift from the benchmark page
            elif bench.returncode != 0:
                out.append('[bench] the benchmark harness could not run (see '
                           'above) — this is NOT a pass; fix the harness and '
                           're-run validate.')
        except subprocess.TimeoutExpired:
            out.append('$ bench_vs_creephype.js\nTIMED OUT after 300s — NOT a '
                       'pass; re-run validate once the machine is unloaded.')
    return ok, '\n'.join(out)


def run_generic(st, step):
    """Steps the orchestrator can't fully own yet: surface the exact command/prompt.
    PROMPT: steps now route through the BYOM seam (ai_provider.py) — under the
    default handoff backend the behavior is byte-identical to the old wall
    (print the rendered prompt, stop); under api/local the backend DRAFTS and
    the draft lands in builds/<key>.ai_drafts/ for review. The validators stay
    the gate either way: BYOM changes who drafts, never who approves."""
    cmd = (step.get('cmd') or '').replace('<key>', st['key']).replace('<slug>', st['slug'])
    title = st['meta'].get('title_en') or st['key']
    cmd = cmd.replace('<TITLE>', title).replace('<ARTIST>', st['meta'].get('artist_en', ''))
    owner = step['owner']
    if cmd.startswith('PROMPT:'):
        import ai_provider
        prompt = cmd[len('PROMPT:'):].strip()
        try:
            ai_provider.complete(step['key'], prompt, key=st['key'])
            drafts = sorted((BUILDS / f"{st['key']}.ai_drafts").glob(f"{step['key']}-*.json"))
            return None, (f"[{owner}] draft ready for review: {drafts[-1].relative_to(ROOT)}\n"
                          f"  Review/merge into builds/{st['key']}.content.json, then mark "
                          f"the step done — a draft is never done by itself.")
        except ai_provider.HandoffRequested as h:
            note = f'  ({h.note})\n' if h.note else ''
            return None, (f'[{owner}] Hand this to Claude Code / your CLI:\n'
                          f'{note}  PROMPT: {h.prompt}')
    verb = {'cli': 'Hand this to Claude Code / your CLI',
            'external': 'Run this yourself (needs a signed-in tab / a service)',
            'local': 'Command'}[owner]
    return None, f'[{owner}] {verb}:\n  {cmd}'


def run_drill_concat(st):
    ok, out = _sh(['python3', str(HERE / 'build_drill_concat.py'), f'songs/{st["slug"]}', '--force'])
    return ok, out


def run_pronunciation(st):
    """Whisper read-back QA on the final served JP clips; mispronounced words are
    swapped for exact human recordings from the vocal dictionary. Runs BEFORE
    assemble/drill_concat in the DAG, so any swapped clip is picked up by the
    later drill concat automatically. Non-fatal: a flaky read-back never blocks
    the build (ok reflects the tool exiting cleanly, not how many it flagged)."""
    tool = HERE / 'verify_jp_pronunciation.py'
    if not tool.exists():
        return True, '[pronunciation] tool missing — skipped (no read-back QA available).'
    ok, out = _sh([PARLER, str(tool), st['slug'], _folder(st), '--fix'])
    return ok, out


def ensure_peaks(key):
    """Build builds/<key>.peaks.json if the song's audio is on this Mac.

    The Timing tab draws its waveform from this file and NOTHING used to write
    it as part of the pipeline — so a freshly added song opened Timing with a
    blank strip and a 404, and the drag-a-word editor (the entire point of that
    tab) could not draw. Never fatal: no audio yet just means no wave yet.

    Called twice on purpose. After `lyrics` there is usually already a
    corpus/wsync_<yt>.wav — the New song screen's start probe downloaded it to
    draw its own waveform — so the mix lane can be drawn immediately. After
    `whisper_sync` the Demucs vocal stem exists, so this second pass adds the
    vocals lane, which is the one you actually align against."""
    ok, _out = _sh(['python3', str(HERE / 'peaks.py'), key])
    # peaks.py narrates per lane on stderr ("no vocals wav … run whisper_sync"),
    # which reads as a warning in a step note even when everything is fine. Say
    # what the editor actually got instead.
    try:
        doc = json.loads((BUILDS / f'{key}.peaks.json').read_text())
        lanes = ' + '.join(sorted(doc.get('lanes') or {}))
    except (OSError, ValueError):
        return ok, '[peaks] no song audio on this Mac yet — the waveform draws once it lands'
    return ok, f'[peaks] waveform ready ({lanes})'


def run_lyrics(st):
    """Real runner: fetch timed lyrics from the vendored sources (Apple word-level
    when a token is configured → NetEase word-level → LRCLIB line-level). Replaces
    the old external hand-off via ~/lyricool; see fetch_timed_lyrics.py."""
    import fetch_timed_lyrics
    try:
        ok, msg = fetch_timed_lyrics.fetch_for_key(st['key'])
    except Exception as e:
        return False, f'[lyrics] fetch failed: {e}'
    if ok:
        msg += '\n' + ensure_peaks(st['key'])[1]
    return ok, msg


def run_whisper_sync(st):
    """Real runner: force-align the lyric timing to the song's YouTube vocal (line +
    word onsets) and measure music_start_ms. HEAVY (demucs stem isolation + CTC
    forced alignment) — minutes, not seconds. Uses --words --apply per BUILDER.md
    Round 5/6 so an LRCLIB (line-level) source gets a true word-level reveal too."""
    yt = st['meta'].get('yt', '')
    if not yt:
        return False, '[whisper_sync] no YouTube id in meta — cannot force-align.'
    print('[whisper_sync] running demucs vocal isolation + CTC forced alignment on the '
          'full YouTube audio — this takes several minutes.', flush=True)
    ok, out = _sh([PARLER, str(HERE / 'whisper_sync.py'), st['key'], '--yt', yt,
                   '--words', '--apply'])
    # the Demucs stem exists now — redraw the wave with its vocals lane
    if ok:
        out += '\n' + ensure_peaks(st['key'])[1]
    return ok, out


def run_scaffold(st):
    """Real runner: the deterministic content.json skeleton (scaffold.py).

    Wired into the walk 2026-07-29. Before that, scaffold.py was called by
    NOTHING — the walk stopped at author_data and handed a person a blank page
    when a gate-clean skeleton was one command away. It refuses to overwrite an
    existing content.json, and so does this: an authored file is human work.
    Needs the parler env (fugashi + unidic_lite + jaconv)."""
    key = st['key']
    dst = BUILDS / f'{key}.content.json'
    if dst.exists():
        return True, (f'[scaffold] builds/{key}.content.json is already there — left it '
                      f'alone. To rebuild the skeleton from scratch (loses anything '
                      f'written into it): scaffold.py {key} --force')
    ok, out = _sh([PARLER, str(HERE / 'scaffold.py'), key])
    return ok, out


def run_podcast_align(st):
    """Real runner: force-align the finished podcast audio to its script so every
    word (EN + JP) highlights when spoken in Immerse. Mirrors how the two shipped
    songs ran it (align_podcast.py --audio <folder>_podcast.mp3 --data <slug>/data.json)."""
    folder = _folder(st)
    podcast = ROOT / 'songs' / '_assets' / folder / 'audio' / f'{folder}_podcast.mp3'
    data = SONGS / st['slug'] / 'data.json'
    if not podcast.exists():
        return False, (f'[podcast_align] no podcast audio at '
                       f'songs/_assets/{folder}/audio/{folder}_podcast.mp3 — render the '
                       f'podcast first.')
    if not data.exists():
        return False, f'[podcast_align] no data.json at songs/{st["slug"]}/data.json — assemble first.'
    print('[podcast_align] force-aligning the full podcast audio to its script '
          '(CTC MMS aligner) — takes a minute.', flush=True)
    ok, out = _sh([PARLER, str(ROOT / 'tools' / 'podcast_align' / 'align_podcast.py'),
                   '--audio', str(podcast), '--data', str(data)])
    return ok, out


RUNNERS = {'assemble': run_assemble, 'reassemble': run_assemble, 'validate': run_validate,
           'en_audio': run_audio, 'jp_audio': run_audio, 'pitch': run_pitch,
           'landing_card': run_landing_card, 'drill_concat': run_drill_concat,
           'pronunciation': run_pronunciation, 'whisper_sync': run_whisper_sync,
           'podcast_align': run_podcast_align, 'scaffold': run_scaffold,
           'lyrics': run_lyrics}   # kits retired 2026-07-12 (product simplify)


def do_run(key, skey, auto):
    st = load(key)
    order = [s['key'] for s in st['steps']]
    if auto:
        # ALL not-done steps, in DAG order — the walk must reach a pending
        # manual/cli/external step and stop there loudly. Filtering to auto
        # steps only (the old behavior) skipped a pending `lyrics` gate and
        # let whisper_sync run without its input (kaijuu-no-hana-uta,
        # 2026-07-06).
        # …minus the optional ones. An optional step is not on the road to
        # shipping, so halting the walk at it (podcast, every time) stopped the
        # walk BEFORE the validators and the landing card. Named explicitly —
        # `run <key> podcast` — it still runs.
        targets = [s for s in st['steps']
                   if s['status'] != 'done' and not s.get('optional')]
    else:
        targets = [get_step(st, skey)]
    for step in targets:
        s_def = STEP_BY_KEY[step['key']]
        print(f'\n=== {step["key"]}: {step["title"]}  [{step["owner"]}] ===')
        runner = RUNNERS.get(step['key'])
        if runner and step['auto']:
            # flip the dot to 'running' BEFORE the runner starts so an open
            # dashboard shows the pulse during long steps (audio, whisper_sync);
            # the dashboard only arms its auto-refresh while a step is running.
            # Record THIS process's pid + start time so the stale-running reaper
            # can tell a live run from a SIGTERM'd corpse (backlog e03ef4d2) —
            # os.getpid() is the step runner both for direct CLI runs and for
            # the Denmoku server (which shells manaoke_build.py as a subprocess).
            step['status'] = 'running'
            step['pid'] = os.getpid()
            step['run_started'] = int(time.time())
            step['note'] = f'[running] started {time.strftime("%Y-%m-%d %H:%M:%S")}'
            save(st); write_dashboard()
            try:
                res = runner(st)
            except BaseException as e:
                # a crash / Ctrl-C must never leave the step stuck on 'running'
                step['status'] = 'blocked'
                step['note'] = f'[crashed] {type(e).__name__}: {e}'[:4000]
                step.pop('pid', None); step.pop('run_started', None)
                save(st); write_dashboard()
                raise
            ok, msg = res[0], res[1]
            arts = res[2] if len(res) > 2 else []
            step['status'] = 'done' if ok else 'blocked'
            step.pop('pid', None); step.pop('run_started', None)
            step['note'] = (msg or '')[:4000]
            if arts:
                step['artifacts'] = arts
            print(msg)
            # write the dashboard after EVERY step so an open file:// dashboard
            # (auto-refreshing) shows the dot flip live during an --auto walk.
            save(st); write_dashboard()
            if not ok and auto:
                print(f'\n[stop] {step["key"]} is a gate — resolve, then re-run.')
                break
        else:
            # Refuse to SILENTLY skip: in the --auto walk an auto=True step with no
            # runner would otherwise fall through run_generic (print + continue),
            # leaving it "pending" while the walk marched on. Stop loudly instead.
            if auto and step['auto'] and not runner:
                print(f'\n[stop] {step["key"]} is flagged auto=True but has NO runner wired '
                      f'in RUNNERS — refusing to silently skip it. Wire a runner or set the '
                      f'step auto=False. Halting the --auto walk here.')
                save(st); write_dashboard(); break
            _, msg = run_generic(st, s_def)
            print(msg)
            if step['status'] == 'pending':
                # 'waiting', not 'blocked'. A hand-off step is the walk arriving
                # exactly where it should and handing you the baton; painting it
                # the same alarm red as a crashed runner said something went
                # wrong when nothing did.
                step['status'] = 'waiting' if step['owner'] != 'local' else 'pending'
            step['note'] = msg[:4000]
            save(st); write_dashboard()
            if auto and not step['auto']:
                print(f'\n[stop] {step["key"]} is owned by "{step["owner"]}" — '
                      f'do it, then: manaoke_build.py set {key} {step["key"]} --done')
                break
    save(st)
    write_dashboard()


def do_status(key):
    st = load(key)
    m = st['meta']
    print(f"\n{m.get('title_jp','')}  {m.get('title_en','')}  — {m.get('artist_en','')}")
    print(f"slug: {st['slug']}   template: {st['template']}\n")
    glyph = {'done': '✔', 'pending': '·', 'blocked': '⚠', 'running': '…', 'skipped': '–',
             'waiting': '▸', 'failed': '✗'}
    for s in st['steps']:
        print(f"  {glyph.get(s['status'],'?')} {s['key']:<14} [{s['owner']:<8}] {s['title']}")


def do_set(key, skey, owner, done, status, note):
    st = load(key)
    s = get_step(st, skey)
    if owner: s['owner'] = owner
    if status: s['status'] = status
    if done: s['status'] = 'done'
    if note is not None: s['note'] = note
    save(st); write_dashboard()
    print(f'{skey}: owner={s["owner"]} status={s["status"]}')


def itunes_art(title, artist):
    """Grab the album artwork from the public iTunes Search API (no auth, no key)
    when --art is omitted — the "watch it pull in the album artwork" the memo asked
    for. Vendored (not imported) from ~/lyricool/itunes.py: derives the 400x400bb.jpg
    URL from the best song result's artworkUrl100. Tries the JP storefront first
    (most Manaoke songs are JP catalog), then US. Returns '' on any miss."""
    if not (title and artist):
        return ''
    import urllib.parse, urllib.request
    title_l, artist_l = title.lower(), artist.lower()

    def _score(r):
        t = (r.get('trackName') or '').lower()
        a = (r.get('artistName') or '').lower()
        s = 0
        if t == title_l: s += 4
        elif title_l in t or t in title_l: s += 2
        if a == artist_l: s += 4
        elif artist_l in a or a in artist_l: s += 2
        return s

    for country in ('jp', 'us'):
        qs = urllib.parse.urlencode({'term': f'{artist} {title}', 'entity': 'song',
                                     'country': country, 'limit': 5})
        try:
            with urllib.request.urlopen(f'https://itunes.apple.com/search?{qs}', timeout=6) as r:
                data = json.loads(r.read().decode('utf-8', errors='replace'))
        except Exception:
            continue
        best = max(data.get('results') or [], key=_score, default=None)
        if best and best.get('artworkUrl100'):
            url = best['artworkUrl100'].replace('100x100bb.jpg', '400x400bb.jpg')
            print(f'[grab_song] iTunes ({country}): {best.get("trackName")} — '
                  f'{best.get("artistName")}\n            → {url}')
            return url
    print('[grab_song] iTunes lookup found no artwork — pass --art explicitly.')
    return ''


def do_init(a):
    # The English title is required at birth, not just in the New song box.
    # Denmoku guards it in the browser, but `init` is the writer — and a song
    # created without one ships data.json with an empty title_en and hands the
    # authoring step a prompt that calls the song by its key. Same refusal as
    # the `identity` verb, so the two paths can't disagree.
    if not str(a.title_en or '').strip():
        sys.exit('REFUSED — --title-en is required. Without it the page ships '
                 'with no English name and the study hand-off calls the song '
                 f'{a.key!r}. Nothing downstream can work it out: a '
                 'romanization of the Japanese title is not the English name.')
    art = a.art
    if not art:
        print('[grab_song] no --art given — looking up album artwork on iTunes…')
        art = itunes_art(a.title_jp or a.title_en, a.artist or a.artist_en)
    meta = dict(title_jp=a.title_jp, title_en=a.title_en, artist=a.artist,
                artist_en=a.artist_en, yt=a.yt, art=art, apple=a.apple,
                level=a.level, slug=a.key,
                # picked catalog candidate's track length — fetch_timed_lyrics
                # falls back to it when the apple URL is blank (0 = unknown)
                duration_ms=int(getattr(a, 'duration_ms', 0) or 0))
    # A start point set by hand in Denmoku. whisper_sync uses this INSTEAD of
    # measuring the onset itself — the auto measurement is "first sound above a
    # threshold", which is not the same question as "where should this song
    # start", and only a human listening can settle the difference.
    ms = int(getattr(a, 'music_start_ms', 0) or 0)
    if ms > 0:
        meta['music_start_ms'] = ms
        meta['music_start_src'] = 'manual'
    # deploy-dir prefix must equal meta['slug'] (the asset folder) — the
    # functions/songs/[dir]/* proxy derives the folder from the dir name.
    slug = a.slug or (meta['slug'] + '-' + ''.join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(6)))
    st = new_state(a.key, meta, a.template, slug)
    # grab_song is satisfied by init itself
    get_step(st, 'grab_song')['status'] = 'done'
    get_step(st, 'grab_song')['note'] = json.dumps(meta, ensure_ascii=False)
    save(st); write_dashboard()
    # Colors eyedropped off the cover before the song existed. content.json is
    # the authored file and won't exist until author_data, so the pick lands in
    # its own layer (assemble_page.load_gradient_design merges it).
    design = (getattr(a, 'design', '') or '').strip()
    if design:
        import assemble_page as ap
        try:
            g = json.loads(design)
        except json.JSONDecodeError as e:
            sys.exit(f'--design is not JSON: {e}')
        g = (g.get('gradient') if isinstance(g, dict) and 'gradient' in g else g) or {}
        ap.validate_gradient_block(g, '--design')
        if g:
            ap.design_json_path(a.key).write_text(
                json.dumps({'gradient': g}, ensure_ascii=False, indent=1) + '\n')
            print(f'wrote builds/{a.key}.design.json  {json.dumps(g, ensure_ascii=False)}')
    print(f'initialized {a.key} → songs/{slug} (template {a.template})')
    if ms > 0:
        print(f'start point set by hand: {ms}ms ({ms/1000:.2f}s) — the sync step '
              f'will use this instead of measuring.')
    do_status(a.key)


# ---- promote: repoint the root landing, as a script (backlog f2ef67ab) ------
# Current-era semantics (2026-07-06 promotion wave): a promote REPOINTS the
# root landing's SONGS[] url for this song to the build_state's current slug
# dir (+ refreshes that entry's cardAccent from builds/<key>.cardaccent.txt).
# No v0N copy is minted. Adding a NEW card to the landing is landing_card.py
# --promote's job — this verb only ever repoints an existing one.


def _landing_entry_span(html, folder):
    """(start, end, current_dir) of the SONGS[] entry whose url points at this
    song's asset folder (url: '/songs/<folder>-<anything>/'), or None. Span =
    the entry's balanced {…} object literal (entries hold no other braces —
    the art svg template literal is brace-free)."""
    m = re.search(rf"url:\s*'/songs/({re.escape(folder)}-[a-z0-9.]+)/'", html)
    if not m:
        return None
    i, depth = m.start(), 0
    while i >= 0:                       # back to the '{' that opens this entry
        c = html[i]
        if c == '}':
            depth += 1
        elif c == '{':
            if depth == 0:
                break
            depth -= 1
        i -= 1
    if i < 0:
        return None
    j, depth = i + 1, 1
    while j < len(html) and depth:      # forward to its matching '}'
        if html[j] == '{':
            depth += 1
        elif html[j] == '}':
            depth -= 1
        j += 1
    return i, j, m.group(1)


def _ship_pathspecs(key, slug, folder):
    """The files that make up ONE song's preview — scoped so a ship commit never
    sweeps unrelated working-tree WIP (the deploy step's `git add -A` hazard).
    Git expands the `builds/<key>.*` wildcard itself (no shell).

    fonts/<folder> belongs here: the page loads its own subset woff2 from that
    directory, and gen_fonts writes it OUTSIDE songs/. A first ship without it
    pushed a page whose every font 404s and silently falls back to the system
    stack — caught publishing koiseyootome, 2026-07-30.

    _redirects is deliberately NOT here. Its per-slug lines never fire (the
    Pages Function resolves assets from the dir-name prefix), and the file is
    shared with every other song, so staging it would sweep unrelated
    working-tree edits into a song's commit — the exact thing this scoping
    exists to prevent."""
    return [f'tools/songcraft/builds/{key}.*',
            f'songs/{slug}',
            f'songs/_assets/{folder}',
            f'fonts/{folder}']


def do_ship(key, dry_run=False):
    """Ship <key>: commit THIS song's preview dir + build files + shared assets
    and push once, so Cloudflare serves the private random-slug URL. Scoped to
    the song (never `git add -A`); commit and push are separate so a push that
    fails on network / non-fast-forward is re-runnable (the local commit stays,
    a re-run just re-pushes). Does NOT repoint the root landing — that's promote."""
    st = load(key)
    slug, folder = st['slug'], _folder(st)
    d = SONGS / slug
    if not d.is_dir():
        sys.exit(f'ship REFUSED: songs/{slug} does not exist — build the preview '
                 f'first (manaoke_build.py run {key} assemble).')
    pathspecs = _ship_pathspecs(key, slug, folder)
    if dry_run:
        ok, out = _git(['status', '--porcelain', '--'] + pathspecs)
        print(f'[dry-run] ship {key} → songs/{slug}\nwould stage + commit + push:\n' +
              (out or '(nothing changed under this song — already shipped)'))
        return True
    _require_clean_main()
    # Mark the deploy step done BEFORE staging so the shipped commit CAPTURES the
    # updated build_state (else it stays dirty in the tree forever). The ladder
    # never trusts this flag anyway — `pushed` is derived from songs/<slug> on
    # origin — so a marked-but-unpushed state is self-correcting on a re-run.
    step = get_step(st, 'deploy')
    step['status'] = 'done'
    step['note'] = f'shipped {time.strftime("%Y-%m-%d %H:%M:%S")}: songs/{slug}'
    save(st); write_dashboard()
    _git(['add', '--'] + pathspecs)
    staged_dirty, _ = _git(['diff', '--cached', '--quiet', '--'] + pathspecs)
    if not staged_dirty:            # `--quiet` exits 1 (ok=False) when changes exist
        okc, outc = _git(['commit', '-m', f'{key}: ship preview {slug}', '--'] + pathspecs)
        if not okc:
            sys.exit(f'ship: commit failed:\n{outc}')
        print(f'committed songs/{slug} (+ build files, shared assets)')
    else:
        print(f'nothing new to commit under {key} — checking for an unpushed commit…')
    okp, outp = _git(['push', 'origin', 'HEAD:main'])
    if not okp:
        hint = ('someone pushed to the main branch first — pull, then re-run ship'
                if 'non-fast-forward' in outp or 'rejected' in outp
                else 'no network / push was rejected — fix it, then re-run ship '
                     '(your commit is saved locally; re-run to re-push it)')
        sys.exit(f'ship: push failed — {hint}:\n{outp}')
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import lessons
        lessons.journal('manual', key,
                        f'shipped the preview to Cloudflare: songs/{slug}',
                        detail='scoped commit + single push (not git add -A); '
                               'root landing untouched (promote is separate)',
                        source='manaoke_build ship')
    except Exception:
        pass
    print(f'\nlive shortly at the private link:  https://manaoke.app/songs/{slug}/\n'
          f'(the root landing still points at the old version — run promote to put '
          f'this on the main page.)')
    return True


def do_start(key, ms=None, auto=False):
    """Set (or clear) where the song starts, without a re-sync.

    The value lives in three places: build_state meta (so a future sync keeps
    it), builds/<key>.lyrics.json song.music_start_ms (what content_to_data
    reads), and from there data.json → the page. Patching the first two and
    rebuilding is seconds; re-running the sync step to move one number would
    cost a Demucs separation and a forced alignment.

    ms=None + auto=True drops back to the measurement.
    """
    st = load(key)
    meta = st.setdefault('meta', {})
    lp = BUILDS / f'{key}.lyrics.json'
    if auto:
        meta.pop('music_start_ms', None)
        meta.pop('music_start_src', None)
        print(f'{key}: start point back to automatic — the sync step will '
              f'measure it again.')
    else:
        ms = max(0, int(ms))
        meta['music_start_ms'] = ms
        meta['music_start_src'] = 'manual'
        print(f'{key}: starts at {ms}ms ({ms/1000:.2f}s), set by hand.')
    save(st)
    if not auto and lp.exists():
        try:
            lyr = json.loads(lp.read_text())
        except Exception as e:
            sys.exit(f'unreadable {lp.name}: {e}')
        lyr.setdefault('song', {})['music_start_ms'] = ms
        lp.write_text(json.dumps(lyr, ensure_ascii=False, indent=1) + '\n')
        print(f'patched builds/{lp.name}')
        print(f'\nnow rebuild the page so it ships:\n'
              f'  python3 tools/songcraft/manaoke_build.py rebuild {key}')
    elif not auto:
        print('(no lyric sheet yet — the sync step will pick this up when it runs.)')
    write_dashboard()
    return True


# The seven strings that say WHICH SONG this is. Settable only at `init` until
# now, which meant a typo — or the blank English title the New song box used to
# accept — could only be fixed by hand-editing two JSON files. Two walks in a
# row did exactly that (mariigoorudo, 2026-07-29 and 07-30).
IDENTITY_FIELDS = ('title_jp', 'title_en', 'artist', 'artist_en',
                   'yt', 'apple', 'art')
# content.json spells the youtube id out; build_state meta keeps it short
IDENTITY_CONTENT_KEY = {'yt': 'youtube_id'}


def do_identity(a):
    """Correct a song's names and links after the song exists.

    TWO homes, both written every time: build_state `meta` (what scaffold reads
    when it builds the skeleton) and builds/<key>.content.json (what
    content_to_data copies into data.json, and what assemble's identity retarget
    reads to swap the template's strings out of the clone). Writing one and not
    the other is the entire reason this verb exists — a page whose data.json
    disagrees with its own build state fails the parity gate with a diff that
    never names the cause.

    An unpassed flag means "leave that field alone"; a passed empty string
    clears it — except title_en, which is refused empty (see below).
    """
    key = a.key
    st = load(key)
    meta = st.setdefault('meta', {})
    cpath = BUILDS / f'{key}.content.json'
    content = None
    if cpath.exists():
        try:
            content = json.loads(cpath.read_text())
        except json.JSONDecodeError as e:
            sys.exit(f'unreadable {cpath.name}: {e}')

    changed = []
    for f in IDENTITY_FIELDS:
        v = getattr(a, f, None)
        if v is None:                       # flag not passed = don't touch it
            continue
        v = v.strip()
        ck = IDENTITY_CONTENT_KEY.get(f, f)
        old = str(meta.get(f, '') or '')
        oldc = str((content or {}).get(ck, '') or '')
        if v == old and (content is None or v == oldc):
            continue
        meta[f] = v
        if content is not None:
            content[ck] = v
        was = old if (content is None or old == oldc) else f'{old} / {oldc}'
        changed.append((f, was, v))

    if not changed:
        print(f'{key}: nothing to change — every field passed already reads '
              f'that way in both files.')
        return True

    # An empty English title is not a cosmetic gap, and nothing downstream can
    # work it out: data.json ships an empty title_en (so the page has no
    # English name and the share text loses it) and the author_data hand-off
    # prompt falls back to the KEY — "mariigoorudo" instead of "Marigold"
    # (see the <TITLE> fill-in above). Never write the blank.
    if not str(meta.get('title_en', '')).strip():
        sys.exit('REFUSED — that would leave title_en empty. The page would '
                 'ship with no English name and the study hand-off would call '
                 f'the song {key!r}. Pass --title-en "<the English name>".')

    save(st)
    if content is not None:
        cpath.write_text(json.dumps(content, ensure_ascii=False, indent=2) + '\n')
    for f, was, now in changed:
        print(f'  {f}: {was!r} → {now!r}')
    print(f'{key}: wrote builds/{key}.build_state.json'
          + (f' + builds/{cpath.name}' if content is not None
             else ' (no content.json yet — scaffold will pick these up)'))

    # A new cover URL with the old cover still cached locally would keep the
    # OLD palette: assemble reads builds/<key>.art.jpg before the network.
    if any(f == 'art' for f, _w, _n in changed):
        cache = BUILDS / f'{key}.art.jpg'
        if cache.exists():
            # never let a housekeeping hiccup abort the verb after the two
            # files are already written — say it and carry on
            try:
                cache.replace(BUILDS / f'{key}.art.stale.jpg')
                print(f'  moved the cached cover aside ({cache.name} → '
                      f'{key}.art.stale.jpg) so the new one is fetched and the '
                      f'palette comes off it.')
            except OSError as e:
                print(f'  ⚠ could not move the cached cover aside ({e}) — '
                      f'delete builds/{cache.name} by hand or the palette will '
                      f'keep coming off the OLD cover.')

    # The built page bakes these strings in, so on an assembled song the page
    # now says the old thing. content.json is a recorded page input, so the
    # staleness plan already sees it — but that surface only offers a command
    # to paste in a terminal. Reopen the rungs that write the strings so the
    # box's own "run everything it can" is the fix.
    if get_step(st, 'assemble')['status'] == 'done':
        why = ', '.join(f for f, _w, _n in changed)
        reopened = []
        for skey in ('reassemble', 'landing_card', 'validate'):
            s = get_step(st, skey)
            if s['status'] == 'done':
                s['status'] = 'pending'
                s['note'] = (f'[identity] open again — {why} changed after this '
                             f'page was built, and the page bakes those strings '
                             f'in. Re-run to put the new ones on the page.')
                reopened.append(skey)
        if reopened:
            save(st)
            print('\nthe built page still says the old thing, so these are '
                  'open again: ' + ', '.join(reopened))
            print('  → in Denmoku press "run everything it can", or run:\n'
                  f'     python3 tools/songcraft/manaoke_build.py run {key}')
    write_dashboard()
    return True


def do_remove(key, dry_run=False, force=False):
    """Retire <key>: move every file this song owns into builds/_trash/<key>-<ts>/.

    NOTHING is deleted. A song is spread across SIX places (build state +
    builds/<key>.*, the page dir songs/<slug>/, the shared audio at
    songs/_assets/<key>/, its subset fonts at fonts/<key>/, its three
    _redirects rewrites, and — if promoted — a root SONGS[] card), and the
    only safe way to take one back out is to move the lot somewhere recoverable
    and say exactly what moved. Restoring is `mv` back.

    This used to cover only four of the six. The two it missed both leak:
    fonts/<key>/ survived a removal and got silently reused by the next song
    that took the same key (so a font bug outlived the rebuild meant to fix
    it), and the _redirects rules accumulated 3 dead lines per removed slug —
    33 of 83 lines were orphans when this was found (2026-07-29), against a
    Cloudflare ceiling of ~100 rules that this project has already hit once
    ("one rule away from dying = no sound", BUILDER.md). A leak with a hard
    ceiling is a deploy failure with a delay on it.

    REFUSES while the song is live on the landing: pulling the files out from
    under a root SONGS[] entry ships a 404 to real visitors. Retire it from the
    landing first (or pass --force, which then also strips the card)."""
    st = load(key)
    slug = st.get('slug') or ''
    folder = (st.get('meta') or {}).get('slug') or key
    root_html = ROOT / 'index.html'
    live = None
    if root_html.exists():
        span = _landing_entry_span(root_html.read_text(), folder)
        if span:
            live = span[2]
    if live and not force:
        sys.exit(f'{key} is LIVE on the landing (root SONGS[] → /songs/{live}/).\n'
                 f'Removing its files now would 404 that card for real visitors.\n'
                 f'Take the card off the landing first, or re-run with --force '
                 f'(which strips the card too).')

    # Paths are recorded RELATIVE TO THE REPO ROOT and the trash mirrors that
    # tree, so undoing is a plain `mv` of the same path back — no decoding a
    # mangled filename to work out where a file came from.
    moves = []                                  # (src, repo-relative path)
    for p in sorted(BUILDS.glob(f'{key}.*')):
        moves.append((p, p.relative_to(ROOT).as_posix()))
    if slug and (SONGS / slug).exists():
        moves.append((SONGS / slug, f'songs/{slug}'))
    if (SONGS / '_assets' / folder).exists():
        moves.append((SONGS / '_assets' / folder, f'songs/_assets/{folder}'))
    if (ROOT / 'fonts' / folder).is_dir():
        moves.append((ROOT / 'fonts' / folder, f'fonts/{folder}'))
    # the song's own _redirects rewrites (assemble adds 3 per slug)
    red_path = ROOT / '_redirects'
    red_kept, red_dropped = [], []
    if slug and red_path.exists():
        for ln in red_path.read_text().splitlines():
            (red_dropped if f'/songs/{slug}/' in ln else red_kept).append(ln)
    if not moves and not red_dropped:
        sys.exit(f'nothing on disk for {key!r} — already removed?')

    stamp = time.strftime('%Y%m%d-%H%M%S')
    trash = BUILDS / '_trash' / f'{key}-{stamp}'
    print(f'{"would move" if dry_run else "moving"} {len(moves)} item(s) into '
          f'builds/_trash/{trash.name}/:')
    for _, label in moves:
        print(f'  {label}')
    if red_dropped:
        print(f'  + {len(red_dropped)} _redirects rule(s) for {slug} '
              f'(recorded in REMOVED.json so undo can put them back)')
    if live:
        print(f'  + the root SONGS[] card pointing at /songs/{live}/  (--force)')
    if dry_run:
        print('\ndry run — nothing moved.')
        return True

    trash.mkdir(parents=True, exist_ok=True)
    for src, rel in moves:
        dst = trash / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    if red_dropped:
        red_path.write_text('\n'.join(red_kept) + '\n')
        print(f'_redirects: dropped {len(red_dropped)} rule(s) for {slug} '
              f'({len(red_kept)} lines left)')
    (trash / 'REMOVED.json').write_text(json.dumps(
        {'key': key, 'slug': slug, 'folder': folder, 'when': stamp,
         'was_live_at': live, 'paths': [rel for _, rel in moves],
         'redirects_removed': red_dropped,
         'undo': 'copy each path back to the repo root, keeping the same '
                 'relative path; paste redirects_removed back into _redirects '
                 'above the generic /songs/ fallthrough'},
        ensure_ascii=False, indent=2))
    if live:
        html = root_html.read_text()
        span = _landing_entry_span(html, folder)
        if span:
            i, j, _ = span
            # eat a trailing comma + the blank line the entry sat on
            k = j
            while k < len(html) and html[k] in ', ':
                k += 1
            if html[k:k+1] == '\n':
                k += 1
            (trash / 'landing-entry.js').write_text(html[i:j])
            root_html.write_text(html[:i] + html[k:])
            print(f'stripped the landing card (saved to '
                  f'builds/_trash/{trash.name}/landing-entry.js)')
    write_dashboard()
    print(f'\n{key} removed. Everything is in builds/_trash/{trash.name}/ — '
          f'move it back to undo.')
    if live:
        print('The landing changed: commit + push the root index.html to make '
              'that real on the site.')
    return True


def do_promote(key, dry_run=False, push=False):
    """Promote <key>: repoint root SONGS[] to songs/<slug> + refresh cardAccent.
    Refuses loudly instead of guessing; --dry-run prints the diff, writes nothing.
    With --push, commit + push the root index.html repoint (one scoped commit)."""
    import difflib
    st = load(key)
    slug, folder = st['slug'], _folder(st)
    d = SONGS / slug
    # -- refusals (a promote must never point the public landing at junk) -----
    if not d.is_dir():
        sys.exit(f'promote REFUSED: songs/{slug} does not exist on disk — assemble '
                 f'the preview first (manaoke_build.py run {key} assemble).')
    missing = [n for n in ('index.html', 'data.json') if not (d / n).exists()]
    if missing:
        sys.exit(f'promote REFUSED: songs/{slug} fails the sanity check — '
                 f'missing {", ".join(missing)}.')
    vstep = get_step(st, 'validate')
    if vstep['status'] != 'done':
        sys.exit(f'promote REFUSED: the validate step is {vstep["status"]!r}, not '
                 f'done — gates first: python3 tools/songcraft/manaoke_build.py '
                 f'run {key} validate')
    # Byte-truth preflight: the checkmark above says validate RAN once; this
    # re-derives the cache-key/parity/podcast facts from the files as they are
    # RIGHT NOW (the 2026-07-07 wave shipped stale exactly because bytes moved
    # after the checkmark). Fail-closed: unverifiable == refused.
    try:
        import validate_live as _vl
        _finds = _vl.check_dir(slug)
    except Exception as _e:
        _finds = [f'preflight could not run: {type(_e).__name__}: {_e}']
    if _finds:
        sys.exit(f'promote REFUSED: songs/{slug} fails the live-integrity '
                 f'preflight:\n  - ' + '\n  - '.join(_finds)
                 + f'\nRebuild first: python3 tools/songcraft/manaoke_build.py '
                 f'rebuild {key}')
    if not LANDING.exists():
        sys.exit('promote REFUSED: no root index.html to repoint.')
    html = LANDING.read_text()
    span = _landing_entry_span(html, folder)
    if span is None:
        accf = BUILDS / f'{key}.cardaccent.txt'
        accent = accf.read_text().strip() if accf.exists() else '#??????'
        m = st['meta']
        sys.exit(f'promote REFUSED: {key} ({folder}) is not on the landing yet; use '
                 f'landing_card.py --promote first — promote only repoints an '
                 f'existing SONGS[] card, it never adds one. Add the card:\n'
                 f'  python3 tools/songcraft/landing_card.py {slug} '
                 f'--title-jp {m.get("title_jp","")!r} --artist {m.get("artist","")!r} '
                 f'--art {m.get("art","")!r} --accent {accent!r} --promote')
    i, j, cur_dir = span
    entry = html[i:j]
    new_entry = entry.replace(f'/songs/{cur_dir}/', f'/songs/{slug}/')
    accf = BUILDS / f'{key}.cardaccent.txt'
    accent = accf.read_text().strip() if accf.exists() else ''
    if accent:
        new_entry = re.sub(r"cardAccent:\s*'#[0-9a-fA-F]{6}'",
                           f"cardAccent: '{accent}'", new_entry, count=1)
    new_html = html[:i] + new_entry + html[j:]
    # _redirects sanity — warn, don't refuse (the functions/songs/[dir] layer
    # routes assets by dir-name prefix; the 3 per-slug lines are the documented
    # fallback + a validate_song requirement).
    red = ROOT / '_redirects'
    if red.exists() and f'/songs/{slug}/audio/*' not in red.read_text():
        print(f'⚠ _redirects carries no per-slug rules for {slug} — assemble usually '
              f'adds them; check before deploying (validate_song requires them).')
    # Ordering guard: promoting to a slug that was never pushed points
    # manaoke.app at a dir Cloudflare has never built → the landing card 404s.
    if push and not dry_run:
        _require_clean_main()
        _ok_s, out_status = _git(['status', '--porcelain', '--', f'songs/{slug}'])
        _ok_a, out_ahead = _git(['rev-list', '--count', 'origin/main..HEAD',
                                 '--', f'songs/{slug}'])
        unpushed = bool(out_status.strip()) or (out_ahead.strip() not in ('', '0'))
        if unpushed:
            sys.exit(f'promote --push REFUSED: songs/{slug} has changes that are not '
                     f'on the main branch yet. Ship it first (put it online) so '
                     f'manaoke.app has a page to point at, then promote.')
    if new_html == html:
        print(f'root landing already points at songs/{slug}'
              + (f' with cardAccent {accent}' if accent else '') + ' — nothing to do.')
        return
    diff = ''.join(difflib.unified_diff(
        html.splitlines(keepends=True), new_html.splitlines(keepends=True),
        fromfile='index.html (live root)',
        tofile=f'index.html (promote {key}: {cur_dir} -> {slug})'))
    print(diff)
    if dry_run:
        print('[dry-run] no bytes written — the diff above is exactly what a real '
              'promote would apply.')
        return
    LANDING.write_text(new_html)
    print(f'root landing repointed: /songs/{cur_dir}/ -> /songs/{slug}/'
          + (f'  (cardAccent {accent})' if accent else ''))
    step = get_step(st, 'promote')
    step['status'] = 'done'
    step['note'] = (f'promoted {time.strftime("%Y-%m-%d %H:%M:%S")}: root SONGS[] '
                    f'{cur_dir} -> {slug}' + (f', cardAccent {accent}' if accent else ''))
    save(st); write_dashboard()
    # journal the promotion (lessons loop) — best-effort, a journal failure
    # never breaks a promote that already landed (same pattern as lexicon add).
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import lessons
        lessons.journal('manual', key,
                        f'promoted to the root landing: {cur_dir} -> {slug}',
                        detail=(f'cardAccent {accent or "(unchanged)"}; validate was '
                                f'done; scripted promote (no freehand root edit)'),
                        source='manaoke_build promote')
    except Exception:
        pass
    if not push:
        print('\ndeploy stays the manual gate — commit + push once, on your word.')
        return
    # --push: one scoped commit of the root repoint, then push. Separate steps
    # so a failed push leaves a re-runnable local commit (same as ship).
    _git(['add', '--', 'index.html'])
    staged_dirty, _ = _git(['diff', '--cached', '--quiet', '--', 'index.html'])
    if not staged_dirty:
        okc, outc = _git(['commit', '-m',
                          f'promote {key}: root SONGS[] -> {slug}', '--', 'index.html'])
        if not okc:
            sys.exit(f'promote: commit of index.html failed:\n{outc}')
    okp, outp = _git(['push', 'origin', 'HEAD:main'])
    if not okp:
        hint = ('someone pushed first — pull, then re-run promote'
                if 'non-fast-forward' in outp or 'rejected' in outp
                else 'no network / push rejected — fix it, then re-run promote '
                     '(your commit is saved locally)')
        sys.exit(f'promote: push failed — {hint}:\n{outp}')
    print(f'\non the main page now:  https://manaoke.app/  →  songs/{slug}/')


# ---- rebuild: plan-then-dispatch against the deps manifest ------------------
# builds/<folder>.deps.json (written by assemble_page/build_drill_concat, or
# adopted by emit_deps.py) records the content-hash of everything a built song
# was made from. `rebuild` re-hashes the current world against it and only then
# dispatches the proven runner chain — so "is anything stale?" is a read-only
# question, and "rebuild it" is the same code path the --auto walk trusts.


def _songcraft_mods():
    """assemble_page + build_drill_concat, imported lazily (their module-level
    imports are stdlib-only, so system python3 is fine) — same import style as
    emit_deps.py; only the rebuild/dash verbs need their sha8 / tree_sha8 /
    audio_version / tool_recipe helpers."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import assemble_page as ap
    import build_drill_concat as bdc
    return ap, bdc


def _load_deps(folder):
    p = BUILDS / f'{folder}.deps.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(f'[rebuild] unreadable {p.name} ({e}) — treating as missing', file=sys.stderr)
        return None


def plan_rebuild(st, template=None):
    """Hash the current world against builds/<folder>.deps.json and report what
    is stale WITHOUT rendering anything (no puppeteer, no ffmpeg): template tree,
    page inputs, recipe tool sha8s and the AUDIO_V clip walk are re-hashed; each
    drill line's RECORDED inputs are re-hashed on disk — the same predicate
    build_drill_concat's incremental skip checks, minus the seg re-derivation
    (planning never re-extracts the drill order). Returns a plan dict; a missing
    deps.json means everything is stale (first run — adopt via emit_deps.py)."""
    ap, bdc = _songcraft_mods()
    folder = _folder(st)
    plan = dict(key=st['key'], folder=folder, slug=st['slug'],
                missing_deps=False, page=[], audio=[], drill_stale={}, drill_total=0)
    man = _load_deps(folder)
    if man is None:
        plan['missing_deps'] = True
        return plan

    # -- page axis: template tree/index, recorded inputs, recipe, out bytes ----
    tdir_name = template or st['template']
    rec_t = man.get('template') or {}
    tdir = SONGS / tdir_name
    if rec_t.get('dir') != tdir_name:
        plan['page'].append(f"template dir {rec_t.get('dir')} → {tdir_name}")
    if not tdir.is_dir():
        plan['page'].append(f'template dir songs/{tdir_name} missing')
    elif ap.tree_sha8(tdir) != rec_t.get('tree_sha8'):
        plan['page'].append(f"template drift {rec_t.get('tree_sha8')}→{ap.tree_sha8(tdir)}")
    elif ap.sha8(tdir / 'index.html') != rec_t.get('index_sha8'):
        plan['page'].append(f"template index drift {rec_t.get('index_sha8')}"
                            f"→{ap.sha8(tdir / 'index.html')}")
    page = man.get('page') or {}
    for rel, want in (page.get('inputs') or {}).items():
        f = ROOT / rel
        if not f.exists():
            plan['page'].append(f'input {rel} missing')
        else:
            got = ap.sha8(f)
            if got != want:
                plan['page'].append(f'input {rel} {want}→{got}')
    rec_asm = (man.get('recipe') or {}).get('assemble_page') or {}
    cur_asm = ap.sha8(ap.__file__)
    if rec_asm.get('tool_sha8') != cur_asm:
        plan['page'].append(f"recipe change (assemble_page {rec_asm.get('tool_sha8')}→{cur_asm})")
    if st['slug'] != man.get('deploy_slug'):
        plan['page'].append(f"slug rotated {man.get('deploy_slug')} → {st['slug']} (page must re-assemble)")
    out_f = ROOT / (page.get('out') or '')
    if not (page.get('out') and out_f.exists()):
        plan['page'].append(f"out {page.get('out')} missing")
    elif ap.sha8(out_f) != page.get('out_sha8'):
        plan['page'].append(f"out {page.get('out')} bytes {page.get('out_sha8')}→{ap.sha8(out_f)}")

    # -- audio axis: recorded AUDIO_V vs a fresh walk of the clip set ----------
    av_cur, clips_cur = ap.audio_version(folder)
    rec_clips = man.get('clips') or {}
    lines = (man.get('drill') or {}).get('lines') or {}
    if page.get('audio_v') != av_cur:
        plan['audio'].append(f"audio_v {page.get('audio_v')}→{av_cur}")
        changed = [rel for rel in sorted(set(rec_clips) | set(clips_cur))
                   if rec_clips.get(rel) != clips_cur.get(rel)]
        for rel in changed[:8]:
            path = f'songs/_assets/{folder}/audio/{rel}'
            n = sum(1 for prev in lines.values()
                    if any(r.get('path') == path for r in (prev.get('inputs') or [])))
            plan['audio'].append(f"clip {rel} {rec_clips.get(rel) or 'new'}"
                                 f"→{clips_cur.get(rel) or 'gone'} → {n} drill line(s)")
        if len(changed) > 8:
            plan['audio'].append(f'… +{len(changed) - 8} more clips changed')

    # -- drill axis: per-line mirror of build_drill_concat's incremental skip --
    # (recipe match + recorded inputs' bytes unmoved + out bytes match); an
    # adopted line with EMPTY inputs is unverifiable — always stale.
    recipe_ok = (man.get('recipe') or {}).get('build_drill_concat') == bdc.tool_recipe()
    asset_root = ROOT / 'songs' / '_assets' / folder
    plan['drill_total'] = len(lines)
    for lk, prev in lines.items():
        why = None
        if not recipe_ok:
            why = 'recipe change (build_drill_concat tool/params)'
        elif not prev.get('inputs'):
            why = 'inputs unverifiable (adopted with empty inputs)'
        else:
            for row in prev['inputs']:
                f = ROOT / row.get('path', '')
                if not f.exists():
                    why = f"input {row.get('path')} missing"; break
                got = ap.sha8(f)
                if got != row.get('sha8'):
                    why = f"input {Path(row['path']).name} {row.get('sha8')}→{got}"; break
            if why is None:
                mp3 = asset_root / (prev.get('out') or '')
                if not (prev.get('out') and prev.get('out_sha8') and prev.get('audio')
                        and prev.get('windows') and mp3.exists()):
                    why = 'out missing/unrecorded'
                elif ap.sha8(mp3) != prev['out_sha8']:
                    why = f"out bytes {prev['out_sha8']}→{ap.sha8(mp3)}"
        if why:
            plan['drill_stale'][lk] = why

    plan['detail'] = dict(tree=rec_t.get('tree_sha8', '?'), inputs=len(page.get('inputs') or {}),
                          recipe=rec_asm.get('tool_sha8', '?'), out=page.get('out_sha8', '?'),
                          audio_v=av_cur, clips=len(clips_cur))
    return plan


def _plan_reasons(plan):
    """Flatten a plan into the combined-table reason strings."""
    if plan['missing_deps']:
        return [f"no manifest — everything stale; adopt the current built state: "
                f"python3 tools/songcraft/emit_deps.py {plan['folder']} {plan['slug']}"]
    rs = list(plan['page']) + list(plan['audio'])
    if plan['drill_stale']:
        rs.append(f"{len(plan['drill_stale'])}/{plan['drill_total']} drill lines stale")
    return rs


def print_plan(plan):
    print(f"\nplan: {plan['key']}  (folder {plan['folder']} · slug {plan['slug']} "
          f"· builds/{plan['folder']}.deps.json)")
    if plan['missing_deps']:
        print(f"  no builds/{plan['folder']}.deps.json — EVERYTHING is stale (no recorded "
              f"world to compare against). If the song is already built and sounds right, "
              f"adopt it without re-rendering:\n"
              f"    python3 tools/songcraft/emit_deps.py {plan['folder']} {plan['slug']}")
        return
    d = plan['detail']

    def axis(name, reasons, fresh_note):
        print(f"  {name:<8} {'STALE' if reasons else 'fresh':<6} {'' if reasons else fresh_note}")
        for r in reasons:
            print(f"           - {r}")
    axis('page', plan['page'],
         f"template {d['tree']} · {d['inputs']} inputs · recipe {d['recipe']} · out {d['out']}")
    axis('audio_v', plan['audio'], f"{d['audio_v']} ({d['clips']} clips)")
    drill_rs = [f"{(lk[:22] + '…') if len(lk) > 22 else lk}: {why}"
                for lk, why in plan['drill_stale'].items()]
    axis('drill', drill_rs,
         f"{plan['drill_total']}/{plan['drill_total']} concat lines verified against recorded inputs")


def dispatch_rebuild(st, fresh_slug=False, template=None, why=None):
    """Re-run the stale song through the PROVEN chain: run_assemble (which
    already does content_to_data → assemble → drill concat → bump → parity →
    lint in the trusted order) then run_validate — the exact runners the --auto
    walk uses, halting on the first failure the same way. NEVER deploys/pushes:
    the deploy step stays the operator's manual gate (push once, at the end)."""
    if template:
        st['template'] = template
    if fresh_slug:
        old = st['slug']
        # slug prefix MUST be the ASSET FOLDER (meta.slug), not the build key:
        # functions/songs/[dir]/* derives the folder as dir-minus-last-segment,
        # so a silhouette-* dir would proxy to _assets/silhouette instead of
        # _assets/silhouette2 (the 2026-07-04 "audio serves html" incident).
        st['slug'] = _folder(st) + '-' + ''.join(
            secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))
        print(f"fresh slug: {old} → {st['slug']}  (songs/{old}/ stays on disk untouched)")
        # The new slug has never been shipped or promoted, so the old deploy/
        # promote 'done' flags now LIE (they cite the previous slug). Reset them
        # to pending — the honest signal downstream (Denmoku, chips) is the
        # file-derived one (root SONGS[] slug == build_state slug), but keeping
        # the step status honest stops it citing a superseded dir.
        for skey in ('deploy', 'promote'):
            s = get_step(st, skey)
            s['status'] = 'pending'
            s['note'] = f'reset by fresh-slug rebuild: {old} → {st["slug"]}'
    save(st)
    # pitch sits between assemble (emits data.json it reads) and validate; it is
    # incremental (no new words → no-op), so it's safe to run every rebuild and a
    # word-set change (e.g. a re-segmentation) regenerates the contours in place.
    for skey in ('assemble', 'pitch', 'validate'):
        step = get_step(st, skey)
        print(f'\n=== {skey}: {step["title"]}  [{step["owner"]}] ===')
        res = RUNNERS[skey](st)
        ok, msg = res[0], res[1]
        step['status'] = 'done' if ok else 'blocked'
        step['note'] = ((f'why: {why} — ' if why else '') + (msg or ''))[:4000]
        if len(res) > 2 and res[2]:
            step['artifacts'] = res[2]
        print(msg)
        save(st); write_dashboard()
        if not ok:
            print(f'\n[stop] {skey} is a gate — resolve, then re-run: '
                  f'manaoke_build.py rebuild {st["key"]}')
            return False
    # deploy stays the operator's call — print the reminder exactly like the
    # deploy STEP does in the walk (never git add/commit/push from here).
    dep = STEP_BY_KEY['deploy']
    print(f'\n=== deploy: {dep["title"]}  [{dep["owner"]}] ===')
    _, msg = run_generic(st, dep)
    print(msg)
    return True


def do_rebuild(key, dry_run=False, fresh_slug=False, template=None, why=None):
    st = load(key)
    plan = plan_rebuild(st, template)
    print_plan(plan)
    stale = bool(plan['missing_deps'] or plan['page'] or plan['audio'] or plan['drill_stale'])
    if fresh_slug and not stale:
        # a fresh preview dir was asked for (e.g. cache-poison recovery): the
        # world may be fresh, but the page must re-assemble into the new dir.
        print('\n--fresh-slug requested — re-assembling into a new preview dir.')
        stale = True
    if why and not stale:
        # a why-rebuild is always intentional: the caller knows an asset moved
        # (repaired clip, restored podcast) even when the deps manifest can't
        # see it — re-assemble so AUDIO_V covers the current bytes.
        print(f'\n--why {why} — forcing re-assemble so fresh URLs cover the change.')
        stale = True
    if not stale:
        print('\neverything fresh — nothing to rebuild.')
        return
    if dry_run:
        return
    dispatch_rebuild(st, fresh_slug=fresh_slug, template=template, why=why)


def do_rebuild_all(dry_run=False):
    """Combined staleness table: every song with build state, plus legacy
    deps-only folders (adopted songs never init'd). The folder that IS the
    current template (inochi) is reported and skipped — rebuilding the template
    from itself is meaningless. Stale songs rebuild sequentially in table
    order, halting on the first gate failure exactly like the --auto walk."""
    plans, states, covered = [], {}, set()
    for p in sorted(BUILDS.glob('*.build_state.json')):
        key = p.name[:-len('.build_state.json')]
        st = load(key)
        states[key] = st
        covered.add(_folder(st))
        plans.append((key, plan_rebuild(st), True))
    cur_templates = {s['template'] for s in states.values()}
    for p in sorted(BUILDS.glob('*.deps.json')):
        folder = p.name[:-len('.deps.json')]
        if folder in covered:
            continue
        man = _load_deps(folder)
        if man and man.get('deploy_slug') in cur_templates:
            plans.append((folder, dict(template_self=True), False))
            continue
        # legacy adopted song without build state: plan (report-only) from a
        # pseudo-state assembled out of the manifest itself.
        pseudo = dict(key=(man or {}).get('key', folder),
                      slug=(man or {}).get('deploy_slug', folder),
                      template=((man or {}).get('template') or {}).get('dir', ''),
                      meta={'slug': folder})
        plans.append((folder, plan_rebuild(pseudo), False))

    print(f"\n{'song':<18} {'state':<7} reasons")
    stale_keys = []
    for name, plan, has_state in sorted(plans, key=lambda t: t[0]):
        if plan.get('template_self'):
            print(f"{name:<18} {'—':<7} template (self) — skip")
            continue
        reasons = _plan_reasons(plan)
        state = 'stale' if reasons else 'fresh'
        shown = '; '.join(reasons[:4]) + (f' … +{len(reasons) - 4} more' if len(reasons) > 4 else '')
        print(f"{name:<18} {state:<7} {shown or '—'}")
        if reasons and not has_state:
            print(f"{'':<18} {'':<7} (no build state — report only; adopt/init before rebuilding)")
        elif reasons:
            stale_keys.append(name)
    if dry_run or not stale_keys:
        return
    for key in stale_keys:
        print(f'\n––– rebuilding {key} –––')
        if not dispatch_rebuild(states[key]):
            print(f'\n[stop] {key} failed a gate — halting the --all walk here.')
            break


def do_rebuild_why(needle):
    """Where does this clip ship? Scan every deps manifest (clips map + each
    drill line's concat inputs) and every clip_provenance manifest. Given a
    sha8 the match is a byte-identity JOIN — a curated-library recording is
    COPIED into many songs' clip sets under different rels (事__こと.mp3 ships
    as odoriko word_v1_koto AND shinunoga word_v2_koto), and the sha8 finds
    every copy. Report only; never renders."""
    is_sha = bool(re.fullmatch(r'[0-9a-f]{8}', needle))
    rel_needle = re.sub(r'^songs/_assets/[^/]+/audio/', '', needle)
    if rel_needle.startswith('audio/'):
        rel_needle = rel_needle[len('audio/'):]
    print(f"\nwhy {needle}  ({'sha8 join' if is_sha else 'clip path'} — report only, nothing renders)")
    report, shas, n_lines = {}, set(), 0

    def add(song, line):
        report.setdefault(song, []).append(line)
    for p in sorted(BUILDS.glob('*.clip_provenance.json')):
        song = p.name[:-len('.clip_provenance.json')]
        try:
            prov = json.loads(p.read_text())
        except Exception:
            continue
        for rel, e in prov.items():
            if (e.get('sha8') == needle) if is_sha else (rel == rel_needle):
                add(song, f"prov   {rel}  {e.get('source')} — {e.get('src', '')}")
                shas.add(e.get('sha8'))
    for p in sorted(BUILDS.glob('*.deps.json')):
        song = p.name[:-len('.deps.json')]
        man = _load_deps(song)
        if not man:
            continue
        for rel, sha in (man.get('clips') or {}).items():
            if (sha == needle) if is_sha else (rel == rel_needle):
                add(song, f"clip   {rel}  sha8 {sha}")
                shas.add(sha)
        for lk, line in ((man.get('drill') or {}).get('lines') or {}).items():
            for row in (line.get('inputs') or []):
                if (row.get('sha8') == needle) if is_sha \
                        else row.get('path', '').endswith('/' + rel_needle):
                    disp = (lk[:22] + '…') if len(lk) > 22 else lk
                    role = row.get('role', '') + (f" w{row['word']}" if 'word' in row else '')
                    add(song, f"drill  {disp}  → {line.get('out', '?')} ({role})")
                    n_lines += 1
                    break   # one hit per line: the concat contains it
    if not report:
        print('  nothing references this clip (checked deps clips/drill inputs + provenance).')
        return
    for song in sorted(report):
        print(f'\n  {song}')
        for ln in report[song]:
            print(f'    {ln}')
    print(f'\n  {n_lines} drill line(s) across {len(report)} manifest(s)')
    if not is_sha and (shas - {None}):
        print(f"  tip: same bytes may ship under other names — rerun with the sha8 "
              f"({', '.join(sorted(shas - {None}))}) to fan out across curated-library copies.")


# ---- lexicon: the site-wide pronunciation memory (cross-song config) --------
# NOT a STEPS entry: the lexicon spans every song (PRONUNCIATION-POLICY.md), so
# it is a config surface here, not a pipeline step (_sync_steps means the step
# schema never needs migrating for it). The fold twins below are byte-copies of
# gen_audio.py's (validate_song.py fold_kana is the third twin) — the three MUST
# stay in lockstep or lexicon keys stop matching across the gates.
LEXICON_PATH = HERE / 'pronunciation_lexicon.json'
STRIP_KANA = re.compile(r'[。、，．！？!?,.\sーっッ]')
_FOLD_SMALL = str.maketrans('ぁぃぅぇぉ', 'あいうえお')
KANA_ONLY = re.compile(r'^[ぁ-ゖァ-ヺー]+$')


def _fold_kana(kana):
    """The lexicon key form: STRIP_KANA then widen small vowels (same fold the
    read-back gate uses)."""
    return STRIP_KANA.sub('', kana or '').translate(_FOLD_SMALL)


def _lone_particles():
    """gen_audio.py's LONE_PARTICLES, read from its SOURCE — not imported (this
    file stays on system python3; gen_audio's siblings pull kokoro etc. at call
    time and its set may grow, so read it fresh rather than copy it)."""
    m = re.search(r"^LONE_PARTICLES = set\('([^']+)'\)",
                  (HERE / 'gen_audio.py').read_text(), re.M)
    return set(m.group(1)) if m else set()


def load_lexicon_doc():
    """The FULL lexicon document (version + words). gen_audio/validate_song load
    only .words; add/list need the whole doc to merge-and-write."""
    try:
        return json.loads(LEXICON_PATH.read_text())
    except Exception:
        return {'version': 1, 'words': {}}


def save_lexicon_doc(doc):
    """Serialize in the file's established shape — indent=1 with entry lists
    INLINE (`"allow": ["curated", "qwen"]`). json.dumps(indent=1) would explode
    every list across lines and rewrite the whole file's shape; a merge must
    touch only the added entry (minimal surgical diffs). Entries are flat
    (str / null / list-of-str values), which is all this emitter handles."""
    def val(v):
        if isinstance(v, list):
            return '[' + ', '.join(json.dumps(x, ensure_ascii=False) for x in v) + ']'
        return json.dumps(v, ensure_ascii=False)
    out = ['{', f' "version": {val(doc.get("version", 1))},', ' "words": {']
    words = doc.get('words', {})
    for i, (k, w) in enumerate(words.items()):
        out.append(f'  {val(k)}: {{')
        fields = list(w.items())
        for j, (fk, fv) in enumerate(fields):
            out.append(f'   {val(fk)}: {val(fv)}' + (',' if j < len(fields) - 1 else ''))
        out.append('  }' + (',' if i < len(words) - 1 else ''))
    out += [' }', '}']
    LEXICON_PATH.write_text('\n'.join(out) + '\n')


def do_lexicon_add(a):
    word, kana = a.word, a.kana
    if not kana:
        if KANA_ONLY.match(word or ''):
            kana = word   # a pure-kana word IS its own reading
        else:
            sys.exit(f'{word!r} is not pure kana — pass the spoken reading with --kana '
                     f'(the lexicon is keyed by the FOLDED spoken kana).')
    key = _fold_kana(kana)
    if not key:
        sys.exit(f'--kana {kana!r} folds to nothing (punctuation / 長音 / っ only?) — '
                 f'give the real spoken reading.')
    if key in ('は', 'へ', 'を', 'わ', 'え', 'お'):
        sys.exit(f'refusing key {key!r}: the particle spoken-forms are owned by the E8 '
                 f'jp_speak rule (は→わ etc.) + the canonical human clips (E9), not the '
                 f'lexicon — see validate_song E8/E9 and tools/human_audio/README.md.')
    doc = load_lexicon_doc()
    words = doc.setdefault('words', {})
    if key in words:
        sys.exit(f'{key!r} is already in the lexicon — edit the file if the entry is '
                 f'wrong:\n' + json.dumps({key: words[key]}, ensure_ascii=False, indent=1))
    if key in _lone_particles():
        print(f'⚠ {key!r} is in gen_audio LONE_PARTICLES — lone particles are already '
              f'routed around Kokoro (E9); a lexicon entry only adds value here to PIN '
              f'a specific clip (--clip).')
    words[key] = {'surface': word, 'kana': kana,
                  'carrier': a.carrier or None, 'clip': a.clip or None,
                  'reason': a.reason or '', 'added': time.strftime('%Y-%m-%d'),
                  'fed_by': 'operator'}
    save_lexicon_doc(doc)
    print(f'added {key!r} to {LEXICON_PATH.relative_to(ROOT)}:')
    print(json.dumps({key: words[key]}, ensure_ascii=False, indent=1))
    # LOAD-BEARING: gen_audio SKIPS existing files, so a lexicon-listed word
    # whose already-rendered clip came from Kokoro would keep shipping forever.
    # Delete every such clip now (across ALL songs, via the provenance join);
    # the next gen_audio pass re-renders it down the lexicon route (pin/dict/
    # loud failure) instead.
    deleted = 0
    for p in sorted(BUILDS.glob('*.clip_provenance.json')):
        stem = p.name[:-len('.clip_provenance.json')]
        bs = BUILDS / f'{stem}.build_state.json'
        folder = _folder(json.loads(bs.read_text())) if bs.exists() else stem
        try:
            prov = json.loads(p.read_text())
        except Exception:
            continue
        for rel, e in prov.items():
            spoken = e.get('kana') or e.get('spoken') or ''
            if _fold_kana(spoken) == key and e.get('source') in ('kokoro', 'kokoro_dictmiss'):
                clip = ROOT / 'songs' / '_assets' / folder / 'audio' / rel
                if clip.exists():
                    clip.unlink()
                    deleted += 1
                    print(f'  deleted {clip.relative_to(ROOT)}  '
                          f'({e["source"]} — a listed word never ships from TTS)')
    if deleted:
        print(f'\n{deleted} Kokoro-voiced clip(s) deleted. Re-render + re-chain so AUDIO_V '
              f'rotates and no page serves the year-cached bad bytes:')
        print('  1. gen_audio.py <key> <folder>        (re-renders the deleted clips, lexicon-routed)')
        print('  2. build_drill_concat.py songs/<slug> (drill concats that contained them)')
        print('  3. manaoke_build.py rebuild <key>     (re-assemble splices the new AUDIO_V)')
    else:
        print('\nno shipped Kokoro clip matched — nothing deleted. New renders of this '
              'word are lexicon-routed from here on.')
    write_dashboard()
    # journal the pin (lessons loop) — best-effort: a journal failure never
    # breaks an add that already landed in the lexicon.
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import lessons
        lessons.journal('lexicon', '',
                        f'{word} ({kana}) pinned site-wide'
                        + (f' — {a.reason}' if a.reason else ''),
                        detail=f'key {key!r}, clip {a.clip or "none"}, '
                               f'{deleted} shipped Kokoro clip(s) deleted for re-render',
                        source='manaoke_build lexicon add')
    except Exception:
        pass


def do_lexicon_list():
    words = load_lexicon_doc().get('words', {})
    if not words:
        print('lexicon empty — no words listed.')
        return
    print(f"\n{'word':<8} {'kana':<8} {'allow':<22} {'reason':<44} {'added':<11} fed_by")
    for k, w in words.items():
        allow = ','.join(w['allow']) if w.get('allow') else 'non-TTS (default)'
        reason = (w.get('reason') or '')
        reason = reason[:41] + '…' if len(reason) > 42 else reason
        print(f"{w.get('surface', k):<8} {w.get('kana', k):<8} {allow:<22} "
              f"{reason:<44} {w.get('added', ''):<11} {w.get('fed_by', '')}")
    print(f'\n{len(words)} word(s) — gate: validate_song E15; policy: '
          f'tools/songcraft/PRONUNCIATION-POLICY.md')


def _e15_lines(out):
    """The [E15 ...] header line(s) + their indented ✗/! findings, from a
    validate_song.py report."""
    keep, in_block = [], False
    for ln in out.splitlines():
        if '[E15' in ln:
            keep.append(ln.strip())
            in_block = not ln.rstrip().endswith('ok')
        elif in_block:
            if ln.startswith('    ✗') or ln.startswith('    !'):
                keep.append(ln.strip())
            else:
                in_block = False
    return keep


def do_lexicon_check(key=None):
    """The fast "the owner heard a bad clip" loop: run the full validate_song gate on
    the BUILT page dir(s) and surface just the E15 verdict + findings."""
    keys = [key] if key else sorted(p.name[:-len('.build_state.json')]
                                    for p in BUILDS.glob('*.build_state.json'))
    fails = 0
    for k in keys:
        st = load(k)
        d = SONGS / st['slug']
        if not (d / 'data.json').exists():
            print(f"{k:<12} —     no built page at songs/{st['slug']} (assemble first)")
            continue
        r = subprocess.run(['python3', 'tools/validate_song.py', f"songs/{st['slug']}"],
                           cwd=ROOT, capture_output=True, text=True)
        m = re.search(r'\bE15=(\d+)', r.stdout)
        if m is None:
            fails += 1
            print(f"{k:<12} FAIL  validator crashed on songs/{st['slug']}:\n"
                  f"{(r.stderr or r.stdout)[-600:]}")
            continue
        n = int(m.group(1))
        fails += (n != 0)
        print(f"{k:<12} {'PASS' if n == 0 else 'FAIL':<5} songs/{st['slug']}")
        for ln in _e15_lines(r.stdout):
            print(f'    {ln}')
    if fails:
        sys.exit(1)


# ---- gradient lab: living-field overrides (cross-song config) ---------------
# Like the lexicon, NOT a STEPS entry: design.gradient is a per-song config
# surface (builds/<key>.content.json) + a site-wide defaults file
# (builds/gradient.defaults.json). Schema/validation/pale-thresholds live in
# assemble_page.py (the one splicer) — this is just the write/read UI for them.


def _content_path(key):
    p = BUILDS / f'{key}.content.json'
    if not p.exists():
        sys.exit(f'no builds/{key}.content.json — author_data (the teaching) must land first.')
    return p


def _write_content(p, content, raw):
    """Re-serialize a content.json in its established shape (indent=1,
    ensure_ascii=False, trailing newline preserved) so the diff is only the
    design block — minimal surgical diffs."""
    p.write_text(json.dumps(content, ensure_ascii=False, indent=1) +
                 ('\n' if raw.endswith('\n') else ''))


def _gradient_args_block(a, ap):
    """CLI flags -> a validated design.gradient dict of ONLY the provided keys,
    pale-guarded (HSV V ceilings from assemble_page; --force-pale records the
    escape hatch for verify_palette)."""
    g = {}
    for f in ap.GRAD_COLOR_KEYS:
        v = getattr(a, f)
        if v:
            try:
                g[f] = ap.rgb_hex(ap.hex_rgb(v))
            except ValueError as e:
                sys.exit(f'gradient set: --{f}: {e}')
    if a.fb:
        parts = [x.strip() for x in a.fb.split(',') if x.strip()]
        if len(parts) != 3:
            sys.exit('gradient set: --fb needs exactly 3 comma-separated hex colors ("#a,#b,#c")')
        try:
            g['fb'] = [ap.rgb_hex(ap.hex_rgb(x)) for x in parts]
        except ValueError as e:
            sys.exit(f'gradient set: --fb: {e}')
    if a.speed is not None:
        if a.speed <= 0:
            sys.exit('gradient set: --speed must be > 0 (it divides the --fdur-* base durations)')
        g['speed'] = a.speed
    if a.amp is not None:
        if a.amp < 0:
            sys.exit('gradient set: --amp must be >= 0')
        if a.amp > 2.5:
            print('⚠ amp > 2.5 risks revealing the field edge (the drifting mesh is only oversized 22%).')
        g['amp'] = a.amp
    if a.motion:
        g['motion'] = a.motion
    if not g:
        sys.exit('gradient set: nothing to set — pass at least one of '
                 '--c1/--c2/--c3/--hi/--fb/--speed/--motion/--amp')
    pale = [e for f in ap.GRAD_COLOR_KEYS if f in g for e in [ap.pale_error(f, g[f])] if e]
    pale += [e for hx in g.get('fb', []) for e in [ap.pale_error('fb', hx)] if e]
    if pale:
        if a.force_pale:
            g['force_pale'] = True
            print('⚠ --force-pale: recording pale tones AGAINST the no-whitish-tones rule:')
            for e in pale:
                print('   ' + e)
        else:
            sys.exit('gradient set REFUSED (pale guard):\n  ' + '\n  '.join(pale))
    return g


def do_gradient_set(a):
    ap, _ = _songcraft_mods()
    if getattr(a, 'main', False):
        return do_gradient_set_main(a)
    if a.all:
        # no global gradients — --all takes MOTION DIALS ONLY (the owner's rule)
        badcol = ['--' + f for f in ap.GRAD_COLOR_KEYS if getattr(a, f)] + (['--fb'] if a.fb else [])
        if badcol:
            sys.exit('gradient set --all takes motion dials only (--speed/--motion/--amp) — '
                     'there are no global gradients (colors are per-song or --main). '
                     'Refused: ' + ', '.join(badcol))
    g = _gradient_args_block(a, ap)
    if a.all:
        dp = ap.gradient_defaults_path()
        cur = {}
        if dp.exists():
            try:
                cur = json.loads(dp.read_text())
            except Exception as e:
                sys.exit(f'gradient set --all: unreadable {dp.name}: {e}')
        cur.update(g)
        ap.validate_gradient_block(cur, dp.name)
        dp.write_text(json.dumps(cur, ensure_ascii=False, indent=1) + '\n')
        print(f'wrote {dp.relative_to(ROOT)}  (site-wide defaults; per-song design.gradient wins per key):')
        print(json.dumps(cur, ensure_ascii=False, indent=1))
        print('\napply to every song:\n  python3 tools/songcraft/manaoke_build.py rebuild --all')
        write_dashboard()
        return
    if not a.key:
        sys.exit('gradient set needs <key> or --all')
    cp = _content_path(a.key)
    raw = cp.read_text()
    content = json.loads(raw)
    gcur = content.setdefault('design', {}).setdefault('gradient', {})
    gcur.update(g)                      # merge — never clobber other design keys
    if gcur.get('force_pale') and not a.force_pale:
        # a stale escape hatch must not outlive the pale color it excused
        still = [f for f in ap.GRAD_COLOR_KEYS if f in gcur and ap.pale_error(f, gcur[f])]
        still += [hx for hx in gcur.get('fb', []) if ap.pale_error('fb', hx)]
        if not still:
            gcur.pop('force_pale')
            print('(force_pale dropped — no recorded color is pale anymore)')
    ap.validate_gradient_block(gcur, f'{a.key}.content.json')
    _write_content(cp, content, raw)
    print(f'builds/{a.key}.content.json design.gradient = '
          f'{json.dumps(gcur, ensure_ascii=False)}')
    # cardAccent: an effective c1 override re-drives the SAME chain assemble uses
    merged = ap.load_gradient_design(a.key)
    accf = BUILDS / f'{a.key}.cardaccent.txt'
    if 'c1' in merged:
        card, _base, _body = ap.c1_chain(ap.hex_rgb(merged['c1']))
        accf.write_text(card)
        print(f'CARD_ACCENT={card}  (set landing SONGS[].cardAccent to this; '
              f'rewrote builds/{a.key}.cardaccent.txt)')
    else:
        cur = accf.read_text().strip() if accf.exists() else '(unbuilt)'
        print(f'CARD_ACCENT stays cover-derived: {cur}')
    print(f'\nnow rebuild the page:\n  python3 tools/songcraft/manaoke_build.py rebuild {a.key}')
    write_dashboard()


def _page_gradient(slug):
    """The BUILT page's current living-field values (colors as hex + dials),
    parsed from songs/<slug>/index.html. None when no page is assembled yet."""
    f = SONGS / slug / 'index.html'
    if not f.exists():
        return None
    html = f.read_text()
    out = {}
    for name in ('c1', 'c2', 'c3', 'hi', 'fb1', 'fb2', 'fb3'):
        m = re.search(rf'--field-{name}:(\d+),(\d+),(\d+)', html)
        if m:
            out[name] = '#%02x%02x%02x' % tuple(int(x) for x in m.groups())
    for name in ('base1', 'base2', 'base3'):
        m = re.search(rf'--field-{name}:(#[0-9a-fA-F]{{6}})', html)
        if m:
            out[name] = m.group(1).lower()
    m = re.search(r'--fdur-drift:([\d.]+)s', html)
    out['dials'] = bool(m)               # Round-11 template? (old v098 pages: False)
    if m:
        out['speed'] = round(20.0 / float(m.group(1)), 4)
    m = re.search(r'--field-amp:([\d.]+)', html)
    if m:
        out['amp'] = float(m.group(1))
    m = re.search(r'<html\b[^>]*\bdata-field-motion="([a-z]+)"', html)
    out['motion'] = m.group(1) if m else 'drift'
    return out


def _gradient_sources(key):
    """The recorded per-song design.gradient block ({} when none/unreadable)."""
    per = {}
    cp = BUILDS / f'{key}.content.json'
    if cp.exists():
        try:
            per = (json.loads(cp.read_text()).get('design') or {}).get('gradient') or {}
        except Exception:
            per = {}
    return per


def do_gradient_show(key=None):
    ap, _ = _songcraft_mods()
    dp = ap.gradient_defaults_path()
    defaults = {}
    if dp.exists():
        try:
            defaults = json.loads(dp.read_text())
        except Exception as e:
            print(f'⚠ unreadable {dp.name}: {e}')
    keys = [key] if key else sorted(p.name[:-len('.build_state.json')]
                                    for p in BUILDS.glob('*.build_state.json'))
    print(f"defaults file: {'builds/gradient.defaults.json ' + json.dumps(defaults, ensure_ascii=False) if defaults else '(none)'}")
    base_dials = {'speed': 1.0, 'motion': 'drift', 'amp': 1.0}
    for k in keys:
        st = load(k)
        per = _gradient_sources(k)
        page = _page_gradient(st['slug'])
        print(f"\n{k}  (songs/{st['slug']}"
              + ('' if page else ' — NOT ASSEMBLED')
              + (', old template: no dials' if page and not page.get('dials') else '') + ')')
        print(f"  {'field':<8} {'effective':<28} source")
        for f in ('c1', 'c2', 'c3', 'hi', 'fb', 'speed', 'motion', 'amp'):
            if f in per:
                val, src = per[f], 'override'
            elif f in defaults:
                val, src = defaults[f], 'default'
            elif f in base_dials:
                val, src = base_dials[f], 'base'
            elif page:
                if f == 'fb':
                    val = ','.join(page.get(n, '?') for n in ('fb1', 'fb2', 'fb3'))
                else:
                    val = page.get(f, '?')
                src = 'cover'
            else:
                val, src = '—', 'cover (unbuilt)'
            if isinstance(val, list):
                val = ','.join(val)
            print(f"  {f:<8} {str(val):<28} {src}")
        if per.get('force_pale'):
            print('  ⚠ force_pale recorded — pale guard bypassed for this song')
        accf = BUILDS / f'{k}.cardaccent.txt'
        if accf.exists():
            print(f'  card     {accf.read_text().strip():<28} '
                  + ('c1 override chain' if 'c1' in {**defaults, **per} else 'cover chain'))


def do_gradient_clear(key, colors=False, motion=False):
    ap, _ = _songcraft_mods()
    cp = _content_path(key)
    raw = cp.read_text()
    content = json.loads(raw)
    g = (content.get('design') or {}).get('gradient') or {}
    if not g:
        print(f'{key}: no design.gradient override — nothing to clear.')
        return
    if colors or motion:
        dropped = {}
        if colors:
            for f in ('c1', 'c2', 'c3', 'hi', 'fb', 'force_pale'):
                if f in g:
                    dropped[f] = g.pop(f)
        if motion:
            for f in ('speed', 'motion', 'amp'):
                if f in g:
                    dropped[f] = g.pop(f)
    else:
        dropped, g = dict(g), {}
    content.setdefault('design', {})['gradient'] = g
    if not g:
        content['design'].pop('gradient', None)
    if not content.get('design'):
        content.pop('design', None)
    _write_content(cp, content, raw)
    print(f'{key}: dropped {json.dumps(dropped, ensure_ascii=False)}')
    if g:
        print(f'{key}: kept    {json.dumps(g, ensure_ascii=False)}')
    if 'c1' in dropped:
        print('c1 override dropped — the next rebuild re-derives cardAccent (and the '
              'base/body chain) from the album cover and rewrites cardaccent.txt.')
    print(f'\nnow rebuild the page:\n  python3 tools/songcraft/manaoke_build.py rebuild {key}')
    write_dashboard()


def _gradient_lab_state(all_states):
    """The Gradient Lab panel's embedded state: per song the current effective
    palette (parsed from the built page), the recorded override block, art +
    title; plus the defaults file, pale thresholds and fdur bases (so panel JS
    mirrors the CLI exactly)."""
    ap, _ = _songcraft_mods()
    dp = ap.gradient_defaults_path()
    defaults = {}
    if dp.exists():
        try:
            defaults = json.loads(dp.read_text())
        except Exception:
            pass
    songs = []
    for st in all_states:
        try:
            m = st.get('meta') or {}
            acc = BUILDS / f"{st['key']}.cardaccent.txt"
            songs.append(dict(
                key=st['key'], slug=st['slug'],
                title_jp=m.get('title_jp', ''), title_en=m.get('title_en', ''),
                artist=m.get('artist_en') or m.get('artist', ''), art=m.get('art', ''),
                page=_page_gradient(st['slug']),
                design=_gradient_sources(st['key']),
                card=acc.read_text().strip() if acc.exists() else ''))
        except Exception:
            pass                       # one song must never take the panel down
    return dict(songs=songs, defaults=defaults, main=_parse_landing_field(),
                pale=dict(main=ap.PALE_V_MAIN, hi=ap.PALE_V_HI),
                fdur=ap.FDUR_BASES, motions=list(ap.MOTIONS))


# ---- main-page (landing) living field --------------------------------------
# The root index.html carries the SAME living-field recipe as a song page but
# with HAND-BAKED rgba literals (no CSS vars). `gradient set --main` recolors
# those literals from c1/c2/c3/hi/fb, so the owner can design the landing field in the
# Lab and push it — WYSIWYG with the Lab preview (one rgb per var; alphas kept).
# There are NO global gradients (--all is dials-only); the landing is the one
# place a whole designed field lives besides a song.
LANDING = ROOT / 'index.html'
_FIELD_DUR = {'fieldDrift': 22.0, 'fieldBreath': 12.0,
              'fbDriftA': 16.0, 'fbDriftB': 22.0, 'fbDriftC': 14.0}


def _rgba_triplets(block):
    return re.findall(r'rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', block or '')


def _sub_rgba(block, hexes):
    """Rewrite each rgba(R,G,B in `block` to the next hex's rgb (alpha kept)."""
    ap, _ = _songcraft_mods()
    it = iter(hexes)

    def repl(m):
        try:
            r, g, b = ap.hex_rgb(next(it))
        except StopIteration:
            return m.group(0)
        return f'rgba({r},{g},{b}'
    return re.sub(r'rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+', repl, block)


def _landing_block(html, selector):
    m = re.search(re.escape(selector) + r'\{[^}]*\}', html, re.S)
    return m.group(0) if m else None


def _parse_landing_field(html=None):
    """The landing living-field's effective colors — canonical = the visible
    radial blooms + the fb blooms — so the Lab can load them as the 'main'
    baseline. Returns {c1,c2,c3,hi,fb1,fb2,fb3 hex, speed} or None."""
    if html is None:
        if not LANDING.exists():
            return None
        try:
            html = LANDING.read_text()
        except Exception:
            return None
    before = _landing_block(html, '.bg-field::before')
    tri = _rgba_triplets(before)     # 3 linear (c1,c2,c3) + 5 radial (c1,c2,c1,c3,hi)
    if len(tri) < 8:
        return None
    hx = lambda t: '#%02x%02x%02x' % tuple(int(x) for x in t)
    out = {'c1': hx(tri[3]), 'c2': hx(tri[4]), 'c3': hx(tri[6]), 'hi': hx(tri[7])}
    for i, sel in enumerate(('.bg-field .fb1', '.bg-field .fb2', '.bg-field .fb3'), 1):
        t = _rgba_triplets(_landing_block(html, sel))
        if t:
            out[f'fb{i}'] = hx(t[0])
    m = re.search(r'animation:fieldDrift ([\d.]+)s', html)
    out['speed'] = round(_FIELD_DUR['fieldDrift'] / float(m.group(1)), 4) if m else 1.0
    return out


def _recolor_landing_field(html, eff, speed=None):
    """Rewrite index.html's living-field literals from an effective color dict
    (c1,c2,c3,hi,fb1,fb2,fb3 hex). Linear washes + radial blooms take one rgb per
    var (c1,c2,c3 washes; c1,c2,c1,c3,hi radials) so the landing MATCHES the Lab
    preview; the baked alphas/positions are preserved."""
    ap, _ = _songcraft_mods()
    base = ap.darken(ap.hex_rgb(eff['c1']), 0.13, 1.30)      # dark solid backdrop in c1's hue
    html = re.sub(r'(\.bg-field\{[^}]*background:)#[0-9a-fA-F]{6}',
                  lambda m: m.group(1) + base, html, count=1)
    before = _landing_block(html, '.bg-field::before')
    if before:
        html = html.replace(before, _sub_rgba(before,
            [eff['c1'], eff['c2'], eff['c3'], eff['c1'], eff['c2'], eff['c1'], eff['c3'], eff['hi']]))
    for i in (1, 2, 3):
        blk = _landing_block(html, f'.bg-field .fb{i}')
        if blk:
            html = html.replace(blk, _sub_rgba(blk, [eff[f'fb{i}']] * len(_rgba_triplets(blk))))
    if speed and speed != 1:
        for name, base_s in _FIELD_DUR.items():
            html = re.sub(rf'({re.escape(name)} )([\d.]+)s',
                          lambda m, b=base_s: f'{m.group(1)}{round(b / speed, 2)}s', html)
    return html


def do_gradient_set_main(a):
    """Push a designed field onto the PUBLIC landing background (index.html)."""
    ap, _ = _songcraft_mods()
    if not LANDING.exists():
        sys.exit('gradient set --main: no root index.html to recolor.')
    html = LANDING.read_text()
    cur = _parse_landing_field(html) or {}
    g = {}
    for f in ap.GRAD_COLOR_KEYS:
        v = getattr(a, f)
        if v:
            try:
                g[f] = ap.rgb_hex(ap.hex_rgb(v))
            except ValueError as e:
                sys.exit(f'gradient set --main: --{f}: {e}')
    if a.fb:
        parts = [x.strip() for x in a.fb.split(',') if x.strip()]
        if len(parts) != 3:
            sys.exit('gradient set --main: --fb needs exactly 3 comma-separated hex colors ("#a,#b,#c")')
        try:
            g['fb1'], g['fb2'], g['fb3'] = [ap.rgb_hex(ap.hex_rgb(x)) for x in parts]
        except ValueError as e:
            sys.exit(f'gradient set --main: --fb: {e}')
    if not g and a.speed is None:
        sys.exit('gradient set --main: nothing to set — pass --c1/--c2/--c3/--hi, --fb, and/or --speed.')
    pale = [e for f in ('c1', 'c2', 'c3', 'hi') if f in g for e in [ap.pale_error(f, g[f])] if e]
    pale += [e for f in ('fb1', 'fb2', 'fb3') if f in g for e in [ap.pale_error('fb', g[f])] if e]
    if pale and not a.force_pale:
        sys.exit('gradient set --main REFUSED (pale guard — white text/cards sit on the field):\n  '
                 + '\n  '.join(pale))
    eff = {k: cur.get(k) for k in ('c1', 'c2', 'c3', 'hi', 'fb1', 'fb2', 'fb3')}
    eff.update(g)
    missing = [k for k, v in eff.items() if not v]
    if missing:
        sys.exit(f'gradient set --main: the landing has no current {", ".join(missing)} to keep — '
                 f'pass every field the first time ({", ".join("--" + m for m in missing)}).')
    speed = a.speed if a.speed is not None else cur.get('speed')
    new = _recolor_landing_field(html, eff, speed)
    if new == html:
        print('gradient set --main: no change (the landing already carries these values).')
        return
    LANDING.write_text(new)
    print('rewrote index.html living field — the PUBLIC landing background:')
    print('  ' + '  '.join(f'{k}={eff[k]}' for k in ('c1', 'c2', 'c3', 'hi', 'fb1', 'fb2', 'fb3')))
    if speed and speed != 1:
        print(f'  drift speed ×{speed}')
    print('\nThis is the public landing (not a random-slug preview) — review it live, '
          'then it ships on the next push.')
    write_dashboard()


# ---- dashboard -------------------------------------------------------------

def _cheap_stale(st):
    """CHEAP per-song staleness for the dashboard chips — just the two axes a
    glance needs (no per-line drill input re-hash): recorded template tree_sha8
    vs current, recorded audio_v vs a fresh clip-walk hash. 'no manifest' when
    the song's deps.json hasn't been adopted/emitted yet.

    'warn' is what the dashboard paints red. A song whose page has never been
    assembled has no manifest BY DEFINITION — flying a red chip at it minutes
    after it was added reported a problem that doesn't exist, so that case gets
    its own quiet state."""
    folder = _folder(st)
    man = _load_deps(folder)
    if man is None:
        assembled = any(s['key'] == 'assemble' and s['status'] == 'done'
                        for s in st.get('steps', []))
        if not assembled:
            return {'state': 'not built yet', 'reasons': [], 'cmd': '', 'warn': False}
        return {'state': 'no manifest', 'reasons': [], 'warn': True,
                'cmd': f'python3 tools/songcraft/emit_deps.py {folder} {st["slug"]}'}
    ap, _bdc = _songcraft_mods()
    reasons = []
    rec_t = man.get('template') or {}
    tdir = SONGS / (rec_t.get('dir') or st['template'])
    if not (tdir.is_dir() and ap.tree_sha8(tdir) == rec_t.get('tree_sha8')):
        reasons.append('template')
    if (man.get('page') or {}).get('audio_v') != ap.audio_version(folder)[0]:
        reasons.append('audio')
    return {'state': 'stale: ' + '+'.join(reasons) if reasons else 'current',
            'reasons': reasons, 'warn': bool(reasons),
            'cmd': f'python3 tools/songcraft/manaoke_build.py rebuild {st["key"]}'
                   if reasons else ''}


def write_dashboard():
    """Emit builds/index.json (all songs) and render builder/index.html from it."""
    all_states = []
    for p in sorted(BUILDS.glob('*.build_state.json')):
        try:
            st = _sync_steps(json.loads(state_io.locked_read(p)))
            for skey in reap_stale_running(st):
                print(f'[reap] {st.get("key", p.name)}.{skey}: was stuck on running '
                      f'with a dead runner — flipped to failed.', file=sys.stderr)
            state_io.locked_write(p, json.dumps(st, ensure_ascii=False, indent=2))
            all_states.append(st)
        except Exception:
            pass
    state_io.locked_write(BUILDS / 'index.json',
                          json.dumps(all_states, ensure_ascii=False, indent=2))
    # Second data channel (/*__LEX__*/): the pronunciation-lexicon panel + a
    # cheap per-song stale chip (template tree + audio_v only — _cheap_stale).
    # Guarded per song: a chip must never take the whole dashboard down.
    lex = {'words': load_lexicon_doc().get('words', {}), 'stale': {}}
    for st in all_states:
        try:
            lex['stale'][st['key']] = _cheap_stale(st)
        except Exception as e:
            lex['stale'][st['key']] = {'state': f'stale? ({type(e).__name__})',
                                       'reasons': [], 'cmd': '', 'warn': True}
    # Render a fully self-contained dashboard (state embedded) so it works on
    # file:// with no server and no fetch (CLAUDE.md: no local servers for Manaoke).
    try:
        import render_dashboard
        DASH.parent.mkdir(parents=True, exist_ok=True)
        # gl = the per-song + landing gradient state (Gradient Lab is now folded
        # into each song's detail, no separate page). dag = the canonical STEPS
        # sequence, for the New Song walkthrough (which has no build_state yet).
        gl = _gradient_lab_state(all_states)
        dag = [{k: s.get(k) for k in ('key', 'title', 'owner', 'auto', 'blurb', 'cmd')}
               for s in STEPS]
        DASH.write_text(render_dashboard.render(all_states, lex, gl, dag))
    except Exception as e:
        print(f'[dash] render skipped: {e}', file=sys.stderr)
    return all_states


# ---- doctor: preflight the environment before a walk (backlog 53f9a67c) -----
# Every dataset / env / model-cache / binary / token / service a full --auto
# walk touches, checked up front so a 40-minute build doesn't die at minute 35
# on a missing dict. WARN = degraded but runnable (tokens, services, corpus
# warmth); FAIL = a step WILL break. Each check is individually guarded —
# doctor itself must never crash. Heavy imports run inside the conda envs via
# subprocess, never in this (system) python — server.py design note holds.

HOME = Path.home()
HF_HUB = HOME / '.cache' / 'huggingface' / 'hub'
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
PARLER_MODS = ('demucs', 'ctc_forced_aligner', 'kokoro', 'faster_whisper', 'fugashi',
               'pykakasi', 'pyopenjtalk', 'jaconv', 'soundfile', 'onnxruntime', 'PIL')


def _mb(p):
    return f'{Path(p).stat().st_size / 2**20:.1f} MB'


def do_doctor(fast=False):
    rows = []

    def run_check(section, name, fn, warn_only=False):
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f'check crashed: {type(e).__name__}: {e}'
        status = 'PASS' if ok is True else ('WARN' if (ok == 'warn' or warn_only) else 'FAIL')
        rows.append((status, section, name, str(detail)))

    # ---- datasets ------------------------------------------------------------
    def ck_jmdict():
        p = HERE / 'data' / 'jmdict_headwords.txt.gz'
        return p.exists(), (f'{p.relative_to(ROOT)} ({_mb(p)})' if p.exists()
                            else 'data/jmdict_headwords.txt.gz MISSING — the '
                                 'segmentation gate is blind without it')

    def ck_openjtalk():
        tar = HERE / 'pitch_pipeline' / 'data' / 'openjtalk_dict.tar.xz'
        sent = HERE / 'pitch_pipeline' / 'data' / 'openjtalk_dict' / 'dictionary' / 'sys.dic'
        if sent.exists():
            return True, (f'extracted (sys.dic {_mb(sent)}); tarball '
                          + ('present' if tar.exists() else 'GONE — extracted copy is the only one'))
        if tar.exists():
            return 'warn', (f'tarball present ({_mb(tar)}) but not extracted — the first '
                            f'pitch run auto-extracts (ensure_dict), adds ~a minute')
        return False, ('neither openjtalk_dict.tar.xz nor the extracted dict exists — '
                       'the pitch step cannot run')

    def ck_kanjium():
        p = HERE / 'pitch_pipeline' / 'data' / 'kanjium_accents.txt'
        return p.exists(), (f'{_mb(p)}' if p.exists()
                            else 'pitch_pipeline/data/kanjium_accents.txt MISSING')

    def ck_library():
        d = ROOT / 'tools' / 'human_audio' / 'library'
        if not d.is_dir():
            # Optional, and absent from a fresh clone by design: these are
            # recordings the project did not make, so they are not shipped.
            # Without them a mangled TTS word is reported, not auto-replaced.
            return 'warn', ('tools/human_audio/library/ not present — optional. '
                          'Human clip swaps are off; the pronunciation step '
                          'still reports what it hears.')
        n = sum(1 for f in os.scandir(d) if f.is_file())
        return (n > 0), f'{n} curated clip(s)' if n else 'library/ is EMPTY'

    def ck_tofugu():
        d = Path(os.environ.get('TOFUGU_DIR') or
                 (HOME / 'Desktop' / 'JP TTS Research' / 'tofugu-wanikani-audio' / 'lib' / 'mp3'))
        if not d.is_dir():
            return False, (f'{d} missing — the OFFLINE word-audio corpus is gone '
                           f'(pronunciation --fix would lean on online JPod101)')
        n = sum(1 for f in os.scandir(d) if f.name.endswith('.mp3'))
        if n < 6000:
            return 'warn', f'only {n} mp3 at {d} (expected ≥6000) — corpus incomplete?'
        return True, f'{n} mp3 at {d}'

    def ck_yomichan():
        d = HOME / 'Desktop' / 'JP TTS Research' / 'yomichan-audio' / 'user_files'
        want = ('nhk16_files', 'shinmeikai8_files', 'jpod_files', 'forvo_files')
        if not d.is_dir():
            return False, f'{d} missing (5.0G human-pronunciation corpus; not yet wired in)'
        miss = [w for w in want if not (d / w).is_dir()]
        if miss:
            return False, f'present but missing dirs: {", ".join(miss)}'
        return True, f'all 4 corpora dirs present (nhk16/shinmeikai8/jpod/forvo; unwired — future resolver)'

    run_check('datasets', 'jmdict headwords', ck_jmdict)
    run_check('datasets', 'openjtalk dict', ck_openjtalk)
    run_check('datasets', 'kanjium accents', ck_kanjium)
    run_check('datasets', 'human word library', ck_library)
    run_check('datasets', 'tofugu corpus', ck_tofugu)
    run_check('datasets', 'yomichan-audio corpus', ck_yomichan, warn_only=True)

    # ---- envs (subprocess imports — the slow part; skipped by --fast) ---------
    if not fast:
        def ck_parler():
            if not Path(PARLER).exists():
                return False, f'{PARLER} MISSING'
            script = ('import importlib\nbad = []\n'
                      f'for m in {list(PARLER_MODS)!r}:\n'
                      '    try: importlib.import_module(m)\n'
                      '    except Exception as e: bad.append(m + ": " + type(e).__name__)\n'
                      'print(", ".join(bad))\nraise SystemExit(1 if bad else 0)')
            r = subprocess.run([PARLER, '-c', script], capture_output=True, text=True,
                               timeout=600)
            if r.returncode == 0:
                return True, f'all {len(PARLER_MODS)} imports ok (demucs…PIL)'
            return False, 'missing: ' + (r.stdout.strip() or r.stderr.strip()[-200:])

        def ck_qwentts():
            if not Path(QWENTTS).exists():
                return False, f'{QWENTTS} MISSING'
            r = subprocess.run([QWENTTS, '-c', 'import pyopenjtalk'],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                return False, 'pyopenjtalk import failed: ' + r.stderr.strip()[-160:]
            smoke = (f'import sys; sys.path.insert(0, {str(HERE)!r})\n'
                     'from pitch_pipeline import get_pitch\n'
                     'from pitch_pipeline.core import to_dict\n'
                     'd = to_dict(get_pitch("桜", "さくら")); assert d')
            r2 = subprocess.run([QWENTTS, '-c', smoke], capture_output=True, text=True,
                                timeout=300, cwd=str(ROOT))
            if r2.returncode != 0:
                return 'warn', ('pyopenjtalk ok, but the pitch_pipeline smoke (桜) '
                                'failed: ' + (r2.stderr.strip()[-160:] or '?'))
            return True, 'pyopenjtalk ok + pitch smoke 桜→さくら ok'

        def ck_syspy():
            r = subprocess.run(['python3', '-c', 'import PIL, genanki'],
                               capture_output=True, text=True, timeout=60)
            return (r.returncode == 0,
                    'PIL + genanki ok' if r.returncode == 0 else
                    'import failed: ' + r.stderr.strip()[-120:] +
                    ' — python3 -m pip install --break-system-packages pillow genanki')

        run_check('envs', 'parler env imports', ck_parler)
        run_check('envs', 'qwentts env imports', ck_qwentts)
        run_check('envs', 'system python3 imports', ck_syspy)

    # ---- model caches ----------------------------------------------------------
    def ck_ctc():
        p = HOME / '.cache' / 'ctc_forced_aligner' / 'model.onnx'
        if not p.exists():
            return False, f'{p} MISSING — whisper_sync downloads ~1.2 GB on first run'
        gb = p.stat().st_size / 2**30
        if gb < 1.0:
            return False, f'{p} truncated ({gb:.2f} GB, expect ~1.2 GB)'
        return True, f'{gb:.1f} GB'

    def ck_htdemucs():
        p = HOME / '.cache' / 'torch' / 'hub' / 'checkpoints' / '955717e8-8726e21a.th'
        return p.exists(), (_mb(p) if p.exists()
                            else f'{p} MISSING — demucs downloads on first whisper_sync')

    def ck_hf(name):
        def _c():
            p = HF_HUB / name
            if not p.is_dir():
                return False, f'{p} MISSING — will hit the HF hub at build time'
            snaps = p / 'snapshots'
            ok = snaps.is_dir() and any(snaps.iterdir())
            return ok, 'snapshot cached' if ok else 'snapshots/ empty — partial download'
        return _c

    run_check('models', 'ctc aligner onnx', ck_ctc)
    run_check('models', 'htdemucs checkpoint', ck_htdemucs)
    run_check('models', 'HF Kokoro-82M', ck_hf('models--hexgrad--Kokoro-82M'))
    run_check('models', 'HF faster-whisper base', ck_hf('models--Systran--faster-whisper-base'))
    run_check('models', 'HF faster-whisper large-v3', ck_hf('models--Systran--faster-whisper-large-v3'))

    # ---- binaries --------------------------------------------------------------
    def ck_bin(name, fallback=None, why=''):
        def _c():
            path = shutil.which(name) or (fallback if fallback and Path(fallback).exists() else None)
            return (bool(path), path or f'{name} not found' + (f' — {why}' if why else ''))
        return _c

    def ck_chrome():
        return Path(CHROME).exists(), (CHROME if Path(CHROME).exists()
                                       else 'Google Chrome missing — headless verify breaks')

    def ck_fonttools():
        # Ask gen_fonts.py itself, don't re-implement the import test here: this
        # check used to run `python3 -c "import fontTools"` and so answered
        # "does the shell running doctor have it?" — PASS in a terminal while
        # every song built from Denmoku.app silently skipped font subsetting
        # (different python3 on that PATH). --which reports the interpreter that
        # will really do the work, after gen_fonts' own re-exec.
        r = subprocess.run(['python3', str(HERE / 'gen_fonts.py'), '--which'],
                           capture_output=True, text=True, timeout=90)
        out = (r.stdout or r.stderr or '').strip().splitlines()
        return (r.returncode == 0,
                (out[-1] if out else 'ok') if r.returncode == 0
                else 'no interpreter here can subset fonts — ' + (out[-1] if out else 'see gen_fonts.py --which'))

    run_check('binaries', 'yt-dlp', ck_bin('yt-dlp', '/opt/homebrew/bin/yt-dlp',
                                           'whisper_sync cannot fetch audio'))
    run_check('binaries', 'ffmpeg', ck_bin('ffmpeg', why='every audio step breaks'))
    run_check('binaries', 'git', ck_bin('git'))
    run_check('binaries', 'wrangler', ck_bin('wrangler', why='R2 upload/deploy tooling'))
    run_check('binaries', 'Google Chrome', ck_chrome)
    run_check('binaries', 'fonttools (subsets)', ck_fonttools)

    # ---- tokens / keys (WARN-only: the walk runs degraded without them) --------
    def ck_apple_token():
        for p in (HERE / '.apple-lyrics.json', HOME / '.lyricool-config.json'):
            if p.exists():
                try:
                    d = json.loads(p.read_text())
                    have = [k for k in ('media_user_token', 'authorization') if d.get(k)]
                    return True, f'{p.name} ({", ".join(have) if have else "present"})'
                except Exception:
                    return 'warn', f'{p.name} present but unparseable'
        return False, ('no .apple-lyrics.json / ~/.lyricool-config.json — Apple '
                       'word-level lyrics off (NetEase/LRCLIB still fetch)')

    def ck_gtts():
        p = ROOT / '.env'
        if not p.exists():
            return False, '.env missing — podcast TTS (Google) unavailable'
        ok = any(ln.startswith('GOOGLE_TTS_KEY=') and ln.strip() != 'GOOGLE_TTS_KEY='
                 for ln in p.read_text().splitlines())
        return ok, 'GOOGLE_TTS_KEY set in .env' if ok else 'no GOOGLE_TTS_KEY in .env — podcast render breaks'

    def ck_cf_token():
        r = subprocess.run(['security', 'find-generic-password', '-s', 'cloudflare-api-token'],
                           capture_output=True, text=True, timeout=15)
        return (r.returncode == 0,
                'in Keychain (svc cloudflare-api-token)' if r.returncode == 0
                else 'not in Keychain — deploy-status checks unavailable')

    run_check('tokens', 'Apple lyrics token', ck_apple_token, warn_only=True)
    run_check('tokens', 'Google TTS key', ck_gtts, warn_only=True)
    run_check('tokens', 'Cloudflare API token', ck_cf_token, warn_only=True)

    # ---- services (WARN-only) ---------------------------------------------------
    def ck_denmoku():
        p = HERE / 'builder' / '.app-url'
        if not p.exists():
            return False, 'builder/.app-url missing — Denmoku never started (~/Denmoku.app)'
        url = p.read_text().strip().rstrip('/')
        import urllib.request
        try:
            with urllib.request.urlopen(url + '/api/state', timeout=3) as r:
                return (r.status == 200), f'{url}/api/state -> {r.status}'
        except Exception as e:
            return False, f'{url} unreachable ({type(e).__name__}) — relaunch ~/Denmoku.app'

    def ck_tailscale():
        ts = shutil.which('tailscale') or '/Applications/Tailscale.app/Contents/MacOS/Tailscale'
        if not Path(ts).exists():
            return False, 'tailscale CLI not found — phone access unknown'
        r = subprocess.run([ts, 'serve', 'status'], capture_output=True, text=True, timeout=10)
        ok = '8773' in (r.stdout + r.stderr)
        return ok, ('serve proxies :8773 — the Denmoku is phone-reachable' if ok
                    else 'no serve rule mentions 8773 — phone access not wired')

    run_check('services', 'Denmoku server', ck_denmoku, warn_only=True)
    run_check('services', 'tailscale serve :8773', ck_tailscale, warn_only=True)

    # ---- corpus / caches (WARN-only: informational warmth) ----------------------
    def ck_corpus():
        cdir = HERE / 'corpus'
        missing, n = [], 0
        for p in sorted(BUILDS.glob('*.build_state.json')):
            try:
                st = json.loads(p.read_text())
            except Exception:
                continue
            yt = (st.get('meta') or {}).get('yt', '')
            if not yt:
                continue
            n += 1
            if not ((cdir / f'hq_{yt}.wav').exists() or (cdir / f'wsync_{yt}.wav').exists()):
                missing.append(f'{st.get("key", "?")} ({yt})')
        if not n:
            return 'warn', 'no songs with a yt id in builds/'
        if missing:
            return 'warn', (f'{n - len(missing)}/{n} songs have durable wavs; missing: '
                            f'{", ".join(missing)} — whisper_sync would re-download those '
                            f'from YouTube')
        return True, f'all {n} songs have durable alignment wavs in corpus/'

    def ck_disk():
        free = shutil.disk_usage(HOME).free / 2**30
        return ((True if free >= 20 else 'warn'),
                f'{free:.0f} GB free on the home volume'
                + ('' if free >= 20 else ' — model/audio caches need headroom'))

    run_check('corpus', 'alignment wav corpus', ck_corpus, warn_only=True)
    run_check('corpus', 'disk headroom', ck_disk, warn_only=True)

    # ---- live set (backlog f8ae38e6: the standing gate on what's promoted) ------
    def ck_live_set():
        import validate_live
        res = validate_live.sweep()
        bad = {s: f for s, f in res.items() if f}
        if not bad:
            return True, (f'{len(res)} live dir(s) serve exactly what they '
                          f'validated as')
        parts = [f'{s}: {f[0]}' + (f' (+{len(f) - 1} more)' if len(f) > 1 else '')
                 for s, f in bad.items()]
        return False, ('LIVE SET DIRTY — ' + ' · '.join(parts)
                       + ' — run tools/songcraft/validate_live.py')

    def ck_unshipped_wave():
        live = {}
        for slug in __import__('validate_live').live_slugs():
            live[slug.rsplit('-', 1)[0]] = slug
        ahead = []
        for p in sorted(BUILDS.glob('*.build_state.json')):
            try:
                st = json.loads(p.read_text())
            except Exception:
                continue
            folder = (st.get('meta') or {}).get('slug') or st.get('key')
            if folder in live and st.get('slug') != live[folder]:
                ahead.append(f"{st.get('key')}: builds at {st.get('slug')}, "
                             f"live is {live[folder]}")
        if ahead:
            return 'warn', 'unshipped wave — ' + ' · '.join(ahead)
        return True, 'every build_state slug matches its live pointer'

    def ck_redirects():
        """Cloudflare honours ~100 rules and this project has already been one
        rule from the cliff ("no sound", BUILDER.md §_redirects cap policy).
        Assemble adds 3 per slug; removal used to add none back, so dead slugs
        accumulated silently — 33 of 83 lines were orphans when this check was
        written. Nothing enforced the documented policy, so nothing noticed."""
        red = ROOT / '_redirects'
        if not red.exists():
            return False, '_redirects is MISSING — every lean song dir loses its assets'
        lines = [ln for ln in red.read_text().splitlines() if ln.strip()]
        orphans = sorted({m.group(1) for ln in lines
                          if (m := re.match(r'/songs/([^/*:]+)/', ln))   # ':dir' = the generic rule
                          and not (SONGS / m.group(1)).is_dir()})
        head = f'{len(lines)}/~100 rules'
        if orphans:
            return 'warn', (f'{head} · {len(orphans)} dead slug(s) still routed: '
                            + ', '.join(orphans[:6])
                            + (f' +{len(orphans) - 6} more' if len(orphans) > 6 else '')
                            + ' — prune them (each costs 3 rules toward the cap)')
        if len(lines) > 85:
            return 'warn', f'{head} — close to the Cloudflare cap; prune superseded previews'
        return True, f'{head}, no dead slugs'

    run_check('live set', 'root SONGS[] integrity', ck_live_set)
    run_check('live set', 'build_state vs live pointers', ck_unshipped_wave,
              warn_only=True)
    run_check('live set', '_redirects cap + orphans', ck_redirects, warn_only=True)

    # ---- report ------------------------------------------------------------------
    print(f'\nmanaoke doctor — preflight ({time.strftime("%Y-%m-%d %H:%M:%S")})'
          + ('   [--fast: env-import checks skipped]' if fast else ''))
    w = max(len(n) for _s, _sec, n, _d in rows)
    cur = None
    for status, section, name, detail in rows:
        if section != cur:
            print(f'\n  {section}')
            cur = section
        print(f'    {status:<4}  {name:<{w}}  {detail}')
    npass = sum(1 for r in rows if r[0] == 'PASS')
    nwarn = sum(1 for r in rows if r[0] == 'WARN')
    nfail = sum(1 for r in rows if r[0] == 'FAIL')
    print(f'\n  {npass} PASS · {nwarn} WARN · {nfail} FAIL — '
          + ('NOT ready for a walk (fix the FAILs above)' if nfail else
             'ready for a walk' + (' (WARNs are degraded-but-runnable)' if nwarn else '')))
    if nfail:
        sys.exit(1)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    pi = sub.add_parser('init'); pi.add_argument('key')
    for f in ('title_jp', 'title_en', 'artist', 'artist_en', 'yt', 'art', 'apple', 'slug'):
        pi.add_argument('--' + f.replace('_', '-'), default='')
    pi.add_argument('--level', default='Intermediate')
    pi.add_argument('--duration-ms', type=int, default=0,
                    help='track length from the picked catalog candidate — '
                         'lyric-fetch fallback when --apple is blank')
    pi.add_argument('--music-start-ms', type=int, default=0,
                    help='where the song starts, set by hand (0 = let the sync '
                         'step measure it)')
    pi.add_argument('--design', default='',
                    help='JSON gradient block eyedropped off the cover, e.g. '
                         '\'{"c1":"#141466"}\' → builds/<key>.design.json')
    pi.add_argument('--template', default='inochi-mijikashi-e03jz0')
    ps = sub.add_parser('status'); ps.add_argument('key')
    pst = sub.add_parser('start', help='set where the song starts (ms), without '
                                       'a re-sync')
    pst.add_argument('key'); pst.add_argument('ms', nargs='?', type=int)
    pst.add_argument('--auto', action='store_true',
                     help='drop the hand-set point and measure it again')
    pid = sub.add_parser('identity', help='fix a song\'s names and links after '
                                          'it exists (writes build state AND '
                                          'content.json — never one alone)')
    pid.add_argument('key')
    for f in IDENTITY_FIELDS:
        # default None, NOT '' — an unpassed flag must not clear the field
        pid.add_argument('--' + f.replace('_', '-'), default=None)
    prm = sub.add_parser('remove', help='retire a song — moves its files to '
                                        'builds/_trash/, deletes nothing')
    prm.add_argument('key')
    prm.add_argument('--dry-run', action='store_true')
    prm.add_argument('--force', action='store_true',
                     help='also strip its card off the root landing')
    pr = sub.add_parser('run'); pr.add_argument('key'); pr.add_argument('step', nargs='?')
    pr.add_argument('--auto', action='store_true')
    pt = sub.add_parser('set'); pt.add_argument('key'); pt.add_argument('step')
    pt.add_argument('--owner'); pt.add_argument('--status'); pt.add_argument('--note')
    pt.add_argument('--done', action='store_true')
    pd = sub.add_parser('dash'); pd.add_argument('key', nargs='?')
    pdoc = sub.add_parser('doctor')
    pdoc.add_argument('--fast', action='store_true',
                      help='skip the env-import checks (parler sweep costs ~30s)')
    pp = sub.add_parser('promote'); pp.add_argument('key')
    pp.add_argument('--push', action='store_true',
                    help='commit + push the root index.html repoint (else print-only)')
    psh = sub.add_parser('ship'); psh.add_argument('key')
    psh.add_argument('--dry-run', action='store_true')
    pp.add_argument('--dry-run', action='store_true')
    pb = sub.add_parser('rebuild'); pb.add_argument('key', nargs='?')
    pb.add_argument('--dry-run', action='store_true'); pb.add_argument('--all', action='store_true')
    pb.add_argument('--fresh-slug', action='store_true'); pb.add_argument('--template')
    pb.add_argument('--why', metavar='CLIP_OR_SHA8')
    pl = sub.add_parser('lexicon'); lsub = pl.add_subparsers(dest='lcmd', required=True)
    la = lsub.add_parser('add'); la.add_argument('word')
    la.add_argument('--kana', default=''); la.add_argument('--carrier', default='')
    la.add_argument('--reason', default=''); la.add_argument('--clip', default='')
    lsub.add_parser('list')
    lc = lsub.add_parser('check'); lc.add_argument('key', nargs='?')
    pg = sub.add_parser('gradient'); gsub = pg.add_subparsers(dest='gcmd', required=True)
    gs = gsub.add_parser('set'); gs.add_argument('key', nargs='?')
    gs.add_argument('--all', action='store_true')
    gs.add_argument('--main', action='store_true')
    for f in ('c1', 'c2', 'c3', 'hi'):
        gs.add_argument('--' + f)
    gs.add_argument('--fb'); gs.add_argument('--speed', type=float)
    gs.add_argument('--amp', type=float)
    gs.add_argument('--motion', choices=('drift', 'orbit', 'sway', 'pulse'))
    gs.add_argument('--force-pale', action='store_true')
    gw = gsub.add_parser('show'); gw.add_argument('key', nargs='?')
    gc = gsub.add_parser('clear'); gc.add_argument('key')
    gc.add_argument('--colors', action='store_true'); gc.add_argument('--motion', action='store_true')
    a = p.parse_args()
    if a.cmd == 'init': do_init(a)
    elif a.cmd == 'status': do_status(a.key)
    elif a.cmd == 'start':
        if not a.auto and a.ms is None:
            p.error('start needs <ms>, or --auto to go back to measuring')
        do_start(a.key, a.ms, a.auto)
    elif a.cmd == 'identity': do_identity(a)
    elif a.cmd == 'remove': do_remove(a.key, a.dry_run, a.force)
    elif a.cmd == 'run': do_run(a.key, a.step, a.auto)
    elif a.cmd == 'set': do_set(a.key, a.step, a.owner, a.done, a.status, a.note)
    elif a.cmd == 'dash': write_dashboard(); print(f'wrote {BUILDS/"index.json"}')
    elif a.cmd == 'doctor': do_doctor(a.fast)
    elif a.cmd == 'promote': do_promote(a.key, a.dry_run, a.push)
    elif a.cmd == 'ship': do_ship(a.key, a.dry_run)
    elif a.cmd == 'rebuild':
        # <key> + --why = REAL rebuild recording the reason (the re-embed
        # chain depends on this — bare --why is the report-only blast-radius
        # scan and renders nothing; the old dispatch sent BOTH forms there).
        if a.key: do_rebuild(a.key, a.dry_run, a.fresh_slug, a.template, why=a.why)
        elif a.why: do_rebuild_why(a.why)
        elif a.all: do_rebuild_all(a.dry_run)
        else: p.error('rebuild needs <key>, --all, or --why <clip|sha8>')
    elif a.cmd == 'lexicon':
        if a.lcmd == 'add': do_lexicon_add(a)
        elif a.lcmd == 'list': do_lexicon_list()
        elif a.lcmd == 'check': do_lexicon_check(a.key)
    elif a.cmd == 'gradient':
        if a.gcmd == 'set': do_gradient_set(a)
        elif a.gcmd == 'show': do_gradient_show(a.key)
        elif a.gcmd == 'clear': do_gradient_clear(a.key, a.colors, a.motion)


if __name__ == '__main__':
    main()
