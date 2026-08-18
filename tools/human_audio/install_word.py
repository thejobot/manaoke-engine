#!/usr/bin/env python3
"""
install_word.py — install one curated word clip into a song's shared assets.

The denmoku words tab (tools/songcraft/docs/denmoku-v2.1-addendum.md) queues
this as a job (POST /api/word/push); it also works standalone. Chain:

  1. two-pass loudnorm (I=-16:TP=-1.5:LRA=11, linear) of --src → the wav
     MASTER at songs/_assets/<song>/audio/jp/word_<sec>_<rom>.wav
     (44100 Hz mono pcm_s16le — the master format the Anki kit reads)
  2. compress to the SERVED mono 80k mp3 alongside it (jp_to_mp3's exact
     ffmpeg profile; single-file here because jp_to_mp3 --song skips clips
     whose mp3 already exists, which is precisely the clip being replaced)
  3. update builds/<key>.clip_provenance.json (source curated, new sha8, kana)
  4. --pin: manaoke_build.py lexicon add (site-wide "never TTS this again")
  5. read-back verify: transcribe the final mp3 with faster-whisper large-v3
     via the parler env python (same model/policy as gen_audio._readback_ok);
     on mismatch PRINT both readings, restore the .bak masters + provenance,
     and exit non-zero (the denmoku job shows as error).

A .bak of the wav/mp3 masters is taken BEFORE anything is overwritten; any
failure after that point restores them (and the provenance entry) so a bad
install can never strand a song with a broken clip.

Usage:
  python3 install_word.py --song odoriko --sec v1 --rom nee \
      --src /path/to/candidate.mp3 [--kana ねぇ] [--pin] [--dry-run]
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent               # tools/human_audio
ROOT = HERE.parents[1]                               # ~/manaoke-site
SONGCRAFT = ROOT / 'tools' / 'songcraft'
BUILDS = SONGCRAFT / 'builds'
MANAOKE_BUILD = SONGCRAFT / 'manaoke_build.py'
# Same env manaoke_build.py shells its whisper/kokoro steps into (its PARLER).
PARLER = '/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python'

sys.path.insert(0, str(HERE))
from jp_to_mp3 import FFMPEG_ARGS                    # the served-mp3 profile

LOUDNORM = 'loudnorm=I=-16:TP=-1.5:LRA=11'


def sha8(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:8]


def find_key(song):
    """The build key whose asset folder is <song> (manaoke_build._folder:
    meta.slug or key)."""
    for p in sorted(BUILDS.glob('*.build_state.json')):
        try:
            st = json.loads(p.read_text())
        except Exception:
            continue
        if (st.get('meta', {}).get('slug') or st.get('key')) == song:
            return st.get('key')
    return None


def loudnorm_two_pass(src, dst_wav):
    """Two-pass linear loudnorm to the master profile: pass 1 measures, pass 2
    applies measured_* + linear=true, 44100 mono pcm_s16le. Writes via a temp
    file + os.replace (never delete-then-recreate).

    Sub-~0.5s clips (lone particles like よ, 0.38s) gate to input_i=-inf under
    R128 (no complete 400ms block survives the gate) and pass 2 then rejects
    measured_I=-inf. Fallback: re-measure with 1s of appended silence (apad)
    so gating has program blocks — the gated integrated value approximates the
    voiced loudness — and apply the same linear pass 2 to the ORIGINAL source
    (a linear gain + TP limit doesn't require the applied audio to be the
    measured audio)."""
    def measure(af):
        r = subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'info', '-i', str(src),
             '-af', af, '-f', 'null', '-'],
            capture_output=True, text=True)
        if r.returncode != 0:
            return None, f'loudnorm pass 1 failed: {r.stderr[-400:]}'
        m = re.search(r'\{[^{}]*"input_i"[^{}]*\}', r.stderr, re.S)
        if not m:
            return None, 'loudnorm pass 1: no measurement JSON in ffmpeg output'
        return json.loads(m.group(0)), ''
    meas, err = measure(LOUDNORM + ':print_format=json')
    if meas is None:
        return False, err
    if meas['input_i'] == '-inf':
        meas, err = measure('apad=pad_dur=1,' + LOUDNORM + ':print_format=json')
        if meas is None:
            return False, err
        if meas['input_i'] == '-inf':
            return False, ('loudnorm: input_i is -inf even with 1s apad — '
                           'the source appears to be silent')
    af = (f'{LOUDNORM}:measured_I={meas["input_i"]}:measured_TP={meas["input_tp"]}'
          f':measured_LRA={meas["input_lra"]}:measured_thresh={meas["input_thresh"]}'
          f':offset={meas["target_offset"]}:linear=true')
    tmp = str(dst_wav) + '.tmp.wav'
    r = subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(src), '-af', af,
         '-ar', '44100', '-ac', '1', '-c:a', 'pcm_s16le', tmp],
        capture_output=True, text=True)
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, f'loudnorm pass 2 failed: {r.stderr[-400:]}'
    os.replace(tmp, str(dst_wav))
    return True, ''


def wav_to_served_mp3(wav, mp3):
    """jp_to_mp3's exact profile for the one replaced clip (temp + os.replace,
    same as jp_to_mp3.compress_dir)."""
    tmp = str(mp3) + '.tmp'
    r = subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(wav),
         *FFMPEG_ARGS, '-f', 'mp3', tmp],   # .tmp suffix hides the muxer
        capture_output=True, text=True)
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, f'mp3 encode failed: {r.stderr[-400:]}'
    os.replace(tmp, str(mp3))
    return True, ''


# Inline read-back script run under the parler python — mirrors
# gen_audio._readback_ok (large-v3 int8 cpu, ja, no VAD, beam 5,
# kakasi→hira fold, containment check). Prints one JSON line.
# ONE deliberate divergence (2026-07-07): whisper sometimes renders a kana
# particle as an Arabic numeral (the NHK human に transcribes as '2' — every
# pass, plain and JP-prompted, including the long-approved inochi outro_ni
# bytes). kakasi passes digits through and the kana filter then strips them,
# so a CORRECT clip failed the gate. We fold digits to per-digit kana
# readings before converting (per-digit only — '25' becomes にご, never the
# place-value にじゅうご, so digit strings can't counterfeit longer words).
# (gen_audio._readback_ok carries the same fold since 2026-07-07.)
# <=2-mora words demand EXACT equality, not containment — containment is the
# gate hole that let a clip actually saying これでいい pass as いい
# (2026-07-07, backlog 0bd85bd1). Both sides fold through kakasi so a kanji
# spoken form (word_meta kana == surface when jp_speak == jp) compares
# phonetically, mirroring gen_audio.
READBACK_SRC = r'''
import json, re, sys
from faster_whisper import WhisperModel
import jaconv
from pykakasi import kakasi
path, kana = sys.argv[1], sys.argv[2]
model = WhisperModel('large-v3', device='cpu', compute_type='int8')
def hear(prompt=None):
    segs, _ = model.transcribe(path, language='ja', vad_filter=False,
                               beam_size=5, condition_on_previous_text=False,
                               initial_prompt=prompt)
    return ''.join(s.text for s in segs).strip()
heard = hear()
DIGITS = {'0': 'ぜろ', '1': 'いち', '2': 'に', '3': 'さん', '4': 'よん',
          '5': 'ご', '6': 'ろく', '7': 'なな', '8': 'はち', '9': 'きゅう'}
LNAMES = {'a': 'えー', 'b': 'びー', 'c': 'しー', 'd': 'でぃー', 'e': 'いー',
          'f': 'えふ', 'g': 'じー', 'h': 'えいち', 'i': 'あい', 'j': 'じぇー',
          'k': 'けー', 'l': 'える', 'm': 'えむ', 'n': 'えぬ', 'o': 'おー',
          'p': 'ぴー', 'q': 'きゅー', 'r': 'あーる', 's': 'えす', 't': 'てぃー',
          'u': 'ゆー', 'v': 'ぶい', 'w': 'だぶりゅー', 'x': 'えっくす',
          'y': 'わい', 'z': 'ぜっと'}
def hira(s):
    s = jaconv.z2h(s, digit=True, ascii=False, kana=False)
    s = ''.join(DIGITS.get(c, c) for c in s)
    s = re.sub(r'(?<![A-Za-z])[A-Za-z]{1,3}(?![A-Za-z])',
               lambda m: ''.join(LNAMES[c] for c in m.group(0).lower()), s)
    conv = kakasi().convert(s)
    return re.sub(r'[^ぁ-ゖー]', '', jaconv.kata2hira(''.join(d['hira'] for d in conv)))
h, k = hira(heard), hira(kana)
fold = lambda x: x.translate(str.maketrans('ぁぃぅぇぉ', 'あいうえお'))
core = re.sub(r'[ーっ]', '', k)
morae = sum(1 for ch in core if ch not in 'ゃゅょぁぃぅぇぉゎ')
eq = fold(h) == fold(k)
ok = eq if morae <= 2 else (fold(k) in fold(h) or eq)
mode = 'plain'
if not ok and len(fold(k)) >= 5:
    # Long-word hallucination slack (the 蹴って/OLさん class): whisper drops a
    # final ん or letter-names on isolated words. ONLY when the plain hearing
    # is within ONE mora of a 5+-mora reading, a prompted retry must match
    # EXACTLY. Prompt-resistance is the discriminator: genuinely-wrong takes
    # (投稿 for とこ, どうか for どっか) do not flip under prompting.
    a, b = fold(h), fold(k)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
        prev = cur
    if prev[-1] <= 1:
        heard2 = hear(f'{kana}。')
        if fold(hira(heard2)) == fold(k):
            ok, mode, heard = True, 'prompted', heard2
print(json.dumps({'ok': ok, 'heard': heard, 'hira': h, 'mode': mode},
                 ensure_ascii=False))
'''


def readback(mp3, kana):
    """(ok, heard, mode) via the parler env. mode 'prompted' means the plain
    hearing was one mora off a 5+-mora reading and a prompted retry matched
    exactly (recorded in provenance for honesty). A crash/timeout counts as a
    FAILED read-back (we never ship an unverified swap)."""
    try:
        r = subprocess.run([PARLER, '-c', READBACK_SRC, str(mp3), kana],
                           capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return False, '<read-back timed out>', 'plain'
    line = (r.stdout.strip().splitlines() or [''])[-1]
    try:
        d = json.loads(line)
        return bool(d.get('ok')), d.get('heard', ''), d.get('mode', 'plain')
    except json.JSONDecodeError:
        return False, f'<read-back crashed: {(r.stderr or r.stdout)[-300:]}>', 'plain'


def physics(mp3, kana):
    """(ok, verdict, why) from clip_physics.py under the parler env — duration
    + envelope vs the reading, catching what read-back can't hear (truncated
    long vowels, next-word bleed at a cut, over-long windows). Same
    never-ship-unverified stance as the read-back: a crash counts as FAILED."""
    try:
        r = subprocess.run([PARLER, str(HERE / 'clip_physics.py'), str(mp3),
                            '--kana', kana, '--json'],
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, 'fail', 'the physics check timed out'
    line = (r.stdout.strip().splitlines() or [''])[-1]
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return False, 'fail', \
            f'the physics check crashed: {(r.stderr or r.stdout)[-300:]}'
    verdict = d.get('verdict', 'fail')
    why = '; '.join(d.get('reasons') or [])
    return verdict != 'fail', verdict, why


def main():
    ap = argparse.ArgumentParser(description='Install a curated word clip into a song')
    ap.add_argument('--song', required=True, help='asset folder under songs/_assets/')
    ap.add_argument('--sec', required=True, help='section id (word_<sec>_<rom>)')
    ap.add_argument('--rom', required=True, help='rom uid (word_<sec>_<rom>)')
    ap.add_argument('--src', required=True, help='candidate audio file to install')
    ap.add_argument('--kana', default='', help='spoken kana (default: word_meta)')
    ap.add_argument('--source', default='curated',
                    choices=['curated', 'qwen', 'aivis', 'google',
                             'kokoro', 'kokoro_dictmiss'],
                    help='honest provenance of the take: curated for human '
                         'dictionary recordings, qwen for phrase-cut carrier '
                         'cuts, aivis for AivisSpeech renders, google for '
                         'Google Cloud TTS renders; kokoro/kokoro_dictmiss '
                         'only when re-installing (pinning) an existing take '
                         'that already carries that provenance')
    ap.add_argument('--waive-physics', metavar='WHY', default='',
                    help='install even if clip physics says fail, recording '
                         'physics_waiver on the provenance entry (who approved '
                         'the take by ear and why) — validate E19 then warns '
                         'instead of blocking')
    ap.add_argument('--only-this-card', action='store_true',
                    help='install to just this one card — by default a '
                         'verified take is copied to every card of the same '
                         'word (same surface + kana) in the song')
    ap.add_argument('--waive-readback', metavar='WHY', default='',
                    help='install even if the whisper read-back mismatches, '
                         'recording readback_waiver on the provenance entry. '
                         'For the documented whisper-limitation class (isolated '
                         'short words: けって→決定, とこ→ここ) — the waiver must '
                         'say whose ear approved the take and what evidence '
                         'corroborates it. Physics still gates.')
    ap.add_argument('--pin', action='store_true',
                    help='also pin site-wide via manaoke_build.py lexicon add')
    ap.add_argument('--chain', action='store_true',
                    help='after a verified install, print the manaoke_build.py '
                         'rebuild command that re-embeds the clip (drill concats '
                         '+ page); executes it ONLY when MANAOKE_CHAIN_EXEC=1 '
                         '(default: print-only)')
    ap.add_argument('--dry-run', action='store_true',
                    help='print the full plan and touch nothing')
    a = ap.parse_args()

    src = Path(a.src).resolve()
    if not src.is_file():
        sys.exit(f'--src not a file: {src}')
    jp_dir = ROOT / 'songs' / '_assets' / a.song / 'audio' / 'jp'
    if not jp_dir.is_dir():
        sys.exit(f'no such song assets dir: {jp_dir}')
    stem = f'word_{a.sec}_{a.rom}'
    wav = jp_dir / f'{stem}.wav'
    mp3 = jp_dir / f'{stem}.mp3'
    out_rel = f'jp/{stem}.mp3'

    key = find_key(a.song)
    if key is None:
        sys.exit(f'no build_state maps to song folder {a.song!r}')
    prov_path = BUILDS / f'{key}.clip_provenance.json'
    try:
        word_meta = json.loads((BUILDS / f'{key}.word_meta.json').read_text())
    except Exception:
        word_meta = {}
    wm = word_meta.get(out_rel) or {}
    kana = a.kana or wm.get('kana') or ''
    surface = wm.get('surface') or kana
    if not kana:
        sys.exit(f'{out_rel} is not in {key}.word_meta.json — pass --kana '
                 f'(the read-back verify needs the spoken reading).')

    try:
        rel_src = src.relative_to(ROOT)
    except ValueError:
        rel_src = src
    plan = [
        f'song {a.song} (build key {key}), word {surface!r} ({kana}) → {stem}',
        f'1. two-pass {LOUDNORM}:linear=true → 44100 mono pcm_s16le master '
        f'{wav.relative_to(ROOT)}' + ('' if wav.exists() else ' (new file)'),
        f'2. served mp3 (ffmpeg {" ".join(FFMPEG_ARGS)}) → {mp3.relative_to(ROOT)}'
        + ('' if mp3.exists() else ' (new file)'),
        f'3. provenance {prov_path.relative_to(ROOT)}[{out_rel!r}] = '
        f'{{source: {a.source}, src: {rel_src}, sha8: <new>, kana: {kana}}}',
        f'4. pin: ' + (f'manaoke_build.py lexicon add {surface} --kana {kana}'
                       if a.pin else 'no (--pin not set)'),
        f'5. read-back verify mp3 via faster-whisper large-v3 ({PARLER}); '
        f'on mismatch restore .bak masters + provenance and exit 1',
        f'5b. clip physics (duration + envelope vs {kana!r}); a hard fail '
        f'restores everything, a marginal take installs with a loud note',
        f'5c. same take to every other card of this word in the song'
        + (' (SKIPPED: --only-this-card)' if a.only_this_card else ''),
        f'6. chain: ' + (f'manaoke_build.py rebuild {key} --why {out_rel} '
                         + ('(MANAOKE_CHAIN_EXEC=1 — will execute)'
                            if os.environ.get('MANAOKE_CHAIN_EXEC') == '1'
                            else '(print-only; set MANAOKE_CHAIN_EXEC=1 to execute)')
                         if a.chain else 'no (--chain not set)'),
    ]
    print(f'install plan for {out_rel} from {src}:')
    for line in plan:
        print(f'  {line}')
    if a.dry_run:
        print('dry run — nothing touched.')
        return

    # -- backups before any mutation ------------------------------------
    baks = []                                    # (bak_path, live_path)
    for live in (wav, mp3):
        if live.exists():
            bak = Path(str(live) + '.bak')
            shutil.copy2(live, bak)
            baks.append((bak, live))
    try:
        old_prov = json.loads(prov_path.read_text())
    except Exception:
        old_prov = {}
    old_entry = old_prov.get(out_rel)

    def restore(reason):
        for bak, live in baks:
            os.replace(bak, live)                # put the old masters back
        if not any(live == wav for _, live in baks):
            wav.unlink(missing_ok=True)          # wav was new — remove it
        if not any(live == mp3 for _, live in baks):
            mp3.unlink(missing_ok=True)
        try:
            prov = json.loads(prov_path.read_text())
        except Exception:
            prov = {}
        if old_entry is not None:
            prov[out_rel] = old_entry
        else:
            prov.pop(out_rel, None)
        json.dump(prov, open(prov_path, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        sys.exit(f'FAILED — restored previous masters + provenance: {reason}')

    # 1. wav master
    ok, err = loudnorm_two_pass(src, wav)
    if not ok:
        restore(err)
    # 2. served mp3
    ok, err = wav_to_served_mp3(wav, mp3)
    if not ok:
        restore(err)
    # 3. provenance
    prov = dict(old_prov)
    prov[out_rel] = {'source': a.source, 'src': str(rel_src),
                     'sha8': sha8(mp3), 'kana': kana}
    json.dump(prov, open(prov_path, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f'installed {out_rel} (sha8 {prov[out_rel]["sha8"]}), provenance updated')
    # 4. read-back verify BEFORE pinning (a pin of a bad clip would poison
    #    the site-wide lexicon).
    print(f'read-back verifying {mp3.name} (large-v3, parler env) ...')
    ok, heard, rb_mode = readback(mp3, kana)
    if not ok:
        if a.waive_readback:
            print(f'read-back MISMATCH (heard {heard!r}, expected {kana!r}) — '
                  f'WAIVED by operator: {a.waive_readback}')
            prov[out_rel]['readback_waiver'] = a.waive_readback
            json.dump(prov, open(prov_path, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)
        else:
            print(f'read-back MISMATCH: expected {kana!r}, whisper heard {heard!r}',
                  file=sys.stderr)
            restore(f'read-back heard {heard!r}, expected {kana!r}')
    else:
        print(f'read-back ok: heard {heard!r}, expected {kana!r}'
              + (' (prompted retry — plain hearing was one mora off)'
                 if rb_mode == 'prompted' else ''))
        if rb_mode == 'prompted':
            prov[out_rel]['readback'] = 'prompted'
            json.dump(prov, open(prov_path, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)
    # 4b. clip physics — duration + envelope vs the reading. Read-back can't
    #     hear a truncated long vowel or a cut that caught the next word's
    #     onset; physics can. Hard fail rolls back like a read-back mismatch;
    #     a marginal take installs (the operator chose it by ear) but says so.
    ok, verdict, why = physics(mp3, kana)
    if not ok:
        if a.waive_physics:
            print(f'clip physics FAIL ({why}) — WAIVED by operator: {a.waive_physics}')
            prov[out_rel]['physics_waiver'] = a.waive_physics
            json.dump(prov, open(prov_path, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)
        else:
            print(f'clip physics FAIL: {why}', file=sys.stderr)
            restore(f'clip physics says the take cannot be right: {why}. '
                    f'If your ear approves the take anyway, re-run with '
                    f'--waive-physics "who approved and why"')
    if verdict == 'suspect':
        print(f'note: clip physics calls this take marginal ({why}) — '
              f'installing since you picked it, but give it a listen; '
              f'it will sit in the Denmoku ear strip until it passes or is waived.')
    # 5. pin
    if a.pin:
        cmd = ['python3', str(MANAOKE_BUILD), 'lexicon', 'add', surface,
               '--kana', kana,
               '--reason', f'pinned via install_word ({a.song} {stem})']
        try:
            lib_rel = src.relative_to(HERE)      # e.g. library/<slug>.mp3
            cmd += ['--clip', str(lib_rel)]
        except ValueError:
            pass                                 # not a library clip: no pin path
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            # 'already in the lexicon' is fine — the clip itself installed.
            print(f'warning: lexicon add did not apply:\n{out}', file=sys.stderr)
        else:
            print(out)
    # journal the intervention (lessons loop) — best-effort: a journal
    # failure never fails an install that already verified.
    try:
        if str(SONGCRAFT) not in sys.path:
            sys.path.insert(0, str(SONGCRAFT))
        import lessons
        old_src = (old_entry or {}).get('source') or 'none'
        lessons.journal('word', a.song,
                        f'{surface} ({kana}): {old_src} → {a.source} from {rel_src}'
                        + (', pinned' if a.pin else ''),
                        detail=f'{out_rel} sha8 {prov[out_rel]["sha8"]}, '
                               f'read-back heard {heard!r}',
                        source='install_word')
    except Exception:
        pass
    for bak, _ in baks:
        bak.unlink(missing_ok=True)
    # 5c. sibling cards: a word fix means THE WORD, not one card. Every other
    #     card in this song with the same (surface, kana) gets the same
    #     verified take — byte-copies of the masters just made, same
    #     provenance (and waivers). the owner's rule, 2026-07-07: fixing どっか on
    #     the v1 card must not leave the ch card on the old clip.
    #     --only-this-card opts out for a deliberately per-card take.
    if not a.only_this_card:
        for sib_rel, sib_meta in sorted(word_meta.items()):
            if sib_rel == out_rel or not sib_rel.startswith('jp/word_'):
                continue
            if (sib_meta.get('surface'), sib_meta.get('kana')) != (surface, kana):
                continue
            sib_mp3 = jp_dir / Path(sib_rel).name
            sib_wav = sib_mp3.with_suffix('.wav')
            try:
                for src_f, dst_f in ((mp3, sib_mp3), (wav, sib_wav)):
                    tmp = Path(str(dst_f) + '.tmp')
                    shutil.copy2(src_f, tmp)
                    os.replace(tmp, dst_f)
                prov[sib_rel] = dict(prov[out_rel])
                json.dump(prov, open(prov_path, 'w', encoding='utf-8'),
                          ensure_ascii=False, indent=1)
                print(f'sibling card {sib_rel}: same take installed '
                      f'(sha8 {prov[sib_rel]["sha8"]})')
            except OSError as e:
                print(f'sibling card {sib_rel}: copy failed ({e}) — the primary '
                      f'install stands; fix this card separately.', file=sys.stderr)
    # 6. chain: the remediation chain (PRONUNCIATION-POLICY.md) — a replaced
    #    clip isn't truly DONE until drill concats + the page re-embed it.
    #    Print the exact rebuild hand-off; execute it only on explicit opt-in
    #    (MANAOKE_CHAIN_EXEC=1) so the orchestrator is never invoked implicitly.
    if a.chain:
        chain_cmd = (f'python3 tools/songcraft/manaoke_build.py '
                     f'rebuild {key} --why {out_rel}')
        print(f'chain: {chain_cmd}')
        if os.environ.get('MANAOKE_CHAIN_EXEC') == '1':
            r = subprocess.run(['python3', str(MANAOKE_BUILD), 'rebuild', key,
                                '--why', out_rel], cwd=ROOT)
            if r.returncode != 0:
                print(f'chain: rebuild exited {r.returncode} — the installed '
                      f'clip is verified and kept; re-run the rebuild manually',
                      file=sys.stderr)
                sys.exit(r.returncode)
        else:
            print('chain: dry — set MANAOKE_CHAIN_EXEC=1 to execute the rebuild')
    print('done.')


if __name__ == '__main__':
    main()
