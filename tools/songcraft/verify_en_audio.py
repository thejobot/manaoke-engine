#!/usr/bin/env python3
"""EN-clip read-back sweep — closes the text↔audio gap the validators missed.

The manifest is the contract: [lang, key, spoken, filename]. validate_song E5
proves manifest↔data agree; nothing proved the AUDIO still says the spoken
text. Result: card text gets rewritten, the old mp3 keeps playing ("hear the
definition" reading a definition that isn't on screen — the ni-wa bug).

Sweeps every en-US manifest entry + every *_ctx.mp3 and *_gloss.mp3 (ctx and
gloss clips are direct-path, not manifest'd; contracts are data.json
context_speak||context and gloss): whisper (small.en) the clip, compare
normalized transcripts, flag mismatches.

LANGUAGE-SAFETY PRECHECK (round 11, the nanbo-ctx bug): any English-spoken
contract text containing CJK is an immediate FAIL before a single clip is
whispered. An EN voice reading どんなに produces garbage that still "matches"
its own text under normalization — the only safe contract text is clean
English. JP belongs in display-only fields (hint) or jp_speak.

Usage (qwentts env):
  python3 tools/songcraft/verify_en_audio.py <build-dir> [--fix]
--fix regenerates flagged clips with Kokoro am_michael @0.95 + loudnorm
(needs the kokoro venv python on PATH var KOKORO_PY, defaults to the
JP TTS Research engines venv). CJK violations are never auto-fixed — they
are authoring errors; fix the text.

Exit 0 = all clips match, 1 = mismatches (listed; fixed if --fix),
2 = CJK in an EN-spoken contract text.
"""
import json, os, re, subprocess, sys
from difflib import SequenceMatcher
from pathlib import Path

BUILD = Path(sys.argv[1])
FIX = '--fix' in sys.argv
# default = the parler env (kokoro 0.9.4 verified importable there) — the old
# default pointed at a venv that no longer exists (~/Desktop/manaoke test
# tools/voice stuff/...), so --fix silently depended on $KOKORO_PY being set.
KOKORO_PY = os.environ.get('KOKORO_PY',
    '/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python')

manifest = json.load(open(BUILD / 'tts_manifest.json'))
data = json.load(open(BUILD / 'data.json'))

CJK = re.compile(r'[぀-ヿ㐀-鿿ｦ-ﾟ]')

# resolve the song's asset dir from _redirects (same rule Cloudflare applies)
def asset_dir():
    rules = open(BUILD.parent.parent / '_redirects').read().splitlines()
    name = BUILD.name
    for ln in rules:
        parts = ln.split()
        if len(parts) >= 2 and parts[0].startswith(f'/songs/{name}/audio/'):
            return Path('songs') / Path(parts[1]).relative_to('/songs').parent.parent
    for ln in rules:
        parts = ln.split()
        if len(parts) >= 2 and parts[0].startswith('/songs/:dir/audio/'):
            return Path('songs') / Path(parts[1]).relative_to('/songs').parent.parent
    raise SystemExit('no redirect rule found')
ADIR = asset_dir()

def rom_uid(rom):
    return re.sub(r'^-+|-+$', '', str(rom or '').replace(' ', '-').replace('·', '').replace('/', '_'))

# ── Pass 0: language safety on every EN-spoken contract text ─────────────
cjk_bad = []
for e in manifest:
    if e[0] == 'en-US' and CJK.search(str(e[2])):
        cjk_bad.append((e[3], e[2]))
for sec in data['sections']:
    if CJK.search(str(sec.get('speak_en') or '')):
        cjk_bad.append((f"section {sec['id']} speak_en", sec['speak_en']))
    for w in sec.get('words', []):
        uid = w.get('uid') or rom_uid(w['rom'])
        ctx = w.get('context_speak') or w.get('context')
        for field, val in (('context(_speak)', ctx), ('en_speak', w.get('en_speak')),
                           ('gloss', w.get('gloss'))):
            if val and CJK.search(str(val)):
                cjk_bad.append((f"{sec['id']}/{uid} {field}", val))
if cjk_bad:
    print(f'{len(cjk_bad)} EN-spoken contract texts contain Japanese — an EN voice')
    print('cannot speak these. Fix the TEXT (move JP to hint/display fields):')
    for label, val in cjk_bad:
        print(f'  CJK {label}: {str(val)[:80]}')
    sys.exit(2)

jobs = []   # (expected_text, path, label)
for e in manifest:
    if e[0] != 'en-US': continue
    jobs.append((e[2], ADIR / e[3], e[3]))
for sec in data['sections']:
    for w in sec.get('words', []):
        uid = w.get('uid') or rom_uid(w['rom'])
        ctx = w.get('context_speak') or w.get('context')
        if ctx:
            p = ADIR / f"audio/en/word_{sec['id']}_{uid}_ctx.mp3"
            if p.exists(): jobs.append((ctx, p, p.name))
        # Word-by-Word drill gloss clips (round 11): contract = w.gloss,
        # direct path, REQUIRED when the word has a gloss (drill 404s
        # degrade to Siri — never ship that).
        if w.get('gloss'):
            p = ADIR / f"audio/en/word_{sec['id']}_{uid}_gloss.mp3"
            jobs.append((w['gloss'], p, p.name))

_NUMS = {'0':'zero','1':'one','2':'two','3':'three','4':'four','5':'five',
         '6':'six','7':'seven','8':'eight','9':'nine','10':'ten'}
def norm(t):
    t = re.sub(r'[^a-z0-9 ]', ' ', str(t).lower())
    t = ' '.join(_NUMS.get(w, w) for w in t.split())
    return re.sub(r'\s+', ' ', t).strip()

print(f'{len(jobs)} clips to verify against their current text', flush=True)
from faster_whisper import WhisperModel
m = WhisperModel('small.en', device='cpu', compute_type='int8')

bad = []
for text, path, label in jobs:
    if not path.exists():
        bad.append((text, path, label, 'MISSING')); print(f'  MISSING {label}', flush=True); continue
    segs, _ = m.transcribe(str(path), vad_filter=False)
    heard = norm(''.join(s.text for s in segs))
    exp = norm(text)
    sim = SequenceMatcher(None, exp, heard).ratio()
    if sim < 0.80:
        bad.append((text, path, label, f'sim={sim:.2f} heard="{heard[:70]}"'))
        print(f'  STALE {label} sim={sim:.2f}', flush=True)

# Escalation tier: whisper cannot reliably transcribe context-free 1-3-word
# clips (small.en hears "cute" as "cued"). NOT a length exemption — flagged
# short clips are re-judged by large-v3; only confirmed mismatches stay bad.
short_flags = [b for b in bad if b[3] != 'MISSING']
if short_flags:
    print(f'  re-checking {len(short_flags)} flags with large-v3', flush=True)
    m2 = WhisperModel('large-v3', device='cpu', compute_type='int8')
    cleared = set()
    for text, path, label, why in short_flags:
        segs, _ = m2.transcribe(str(path), language='en', vad_filter=False)
        heard = norm(''.join(s.text for s in segs))
        if SequenceMatcher(None, norm(text), heard).ratio() >= 0.80:
            cleared.add(label); print(f'  CLEARED {label} (large-v3: "{heard}")', flush=True)
    bad = [b for b in bad if b[2] not in cleared]

print(f'\n{len(bad)} stale/missing of {len(jobs)}', flush=True)
if not bad: sys.exit(0)
if not FIX:
    for t, p, l, why in bad: print(f'  {l}: {why}')
    sys.exit(1)

# --fix: regenerate via Kokoro in a subprocess (this env lacks kokoro)
fix_spec = [[t, str(p)] for t, p, l, why in bad]
gen = r'''
import json, subprocess, sys
from pathlib import Path
jobs = json.load(open(sys.argv[1]))
from kokoro import KPipeline
import soundfile as sf
import numpy as np
pipe = KPipeline(lang_code="a")
def loudnorm(wav, mp3):
    p1 = subprocess.run(["ffmpeg","-y","-i",str(wav),"-af","loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json","-f","null","-"], capture_output=True, text=True)
    j = json.loads(p1.stderr[p1.stderr.rindex("{"):p1.stderr.rindex("}")+1])
    af = (f"loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
          f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}")
    subprocess.run(["ffmpeg","-y","-i",str(wav),"-af",af,"-codec:a","libmp3lame","-q:a","2",str(mp3)], capture_output=True, check=True)
for text, out in jobs:
    # Kokoro slurs bare single words ("of" renders with a spurious L-onset,
    # heard as "Love" by two whisper models). Terminal punctuation fixes the
    # onset. Render text gets a period; the CONTRACT text is unchanged.
    tts = text if text.rstrip().endswith((".", "!", "?")) else text + "."
    chunks = [a for _, _, a in pipe(tts, voice="am_michael", speed=0.95)]
    audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    tmp = out + ".tmp.wav"
    sf.write(tmp, audio, 24000)
    loudnorm(tmp, out)
    Path(tmp).unlink()
    print("  regen", Path(out).name, flush=True)
'''
spec_path = '/tmp/en_fix_jobs.json'
json.dump(fix_spec, open(spec_path, 'w'), ensure_ascii=False)
r = subprocess.run([KOKORO_PY, '-c', gen, spec_path])
sys.exit(r.returncode)
