#!/usr/bin/env python3
"""Acoustic clip physics — the detector for the いい/の defect class.

Short JP study clips whose truncated or contaminated takes PASS transcription
read-back: a hard-cut いい transcribes as いえ (sounds obviously wrong),
a carrier-cut whose window caught the NEXT word's onset transcribes fine
(whisper ignores the trailing burst), an over-long window transcribes as a
superstring the containment check accepts. Transcription can't hear any of
this — duration and envelope physics can.

Two measurements, three verdicts:

  DURATION  — voiced span vs the word's weighted mora count (long vowels,
              geminates and bare-vowel repeats add weight: they must be
              SUSTAINED, so a truncated take comes up short).
  ENVELOPE  — energy at the effective end of the signal. A natural take
              decays into silence; a cut take ends mid-energy, and a window
              that caught the next word ends in a fresh burst.

  verdict 'fail'    — hard physics violation, the clip cannot be right
  verdict 'suspect' — marginal take worth a human ear (Denmoku strip)
  verdict 'pass'    — physically plausible

Calibrated 2026-07-07 against all 583 library jp word clips plus the four
recovered known-bad takes (いい 0.29s hot tail, の 0.23s, よ 0.23s,
こと 0.31s — physics_fixtures/bad/) and their known-good replacements
(physics_fixtures/good/). The locked thresholds catch 4/4 bads and pass
4/4 goods; `--selftest` re-proves that on every run. Raising any threshold
needs a new calibration pass, not a hunch.

Used by: gen_audio.py (every synthetic ja render), phrase_cut.py (per-take
verify + energy tail trim), install_word.py (gate before pin, via
subprocess), sweep_clip_physics.py (library-wide sweep -> clip_suspects
sidecar -> validate_song E19 + the Denmoku words-tab suspect strip).

Needs the parler env (numpy; pykakasi/jaconv for kanji folding) + ffmpeg.

CLI:
  clip_physics.py <clip> --kana <reading> [--json]
  clip_physics.py --selftest
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / 'physics_fixtures'

# Bump on ANY change to thresholds OR the mora/judgment model — the sweep's
# incremental reuse keys on this id, so a stale id silently keeps old verdicts.
THRESHOLDS_ID = 'v3-2026-07-07'

FRAME_S = 0.010          # RMS frame size
FLOOR_DB = -50.0         # audibility floor relative to full scale
EDGE_S = 0.025           # effective-end / effective-start window

# fail tier
MIN_SPEECH_ABS = 0.08        # below this there is no word at all
MIN_SPEECH_PER_MORA = 0.082  # physical floor, voiced span per weighted mora
SHORT_FILE_S = 0.34          # the truncated-take class: tiny file...
SHORT_FILE_TAIL_DB = -8.0    # ...that still ends near voiced energy
CUT_TAIL_MAX_DB = -2.0       # sustained hot end: hottest frame in last 25ms
CUT_TAIL_MEAN_DB = -4.0      # ...and the mean of that window, vs voiced median

# suspect tier
SUS_TAIL_MAX_DB = -4.0
SUS_SPEECH_PER_MORA = 0.10
MAX_SPEECH_PER_MORA = 0.42   # over-content: may hold more than the word


# ---------------------------------------------------------------- kana / morae

_KKS = None
_YOON = set('ゃゅょゎャュョヮ')          # merge into the previous mora, no extra time
_SMALL_VOWELS = set('ぁぃぅぇぉァィゥェォ')  # written long vowels (あぁ, ねぇ): sustained
_VOWEL_OF = {}
for _row, _v in [('あかがさざただなはばぱまやらわ', 'あ'),
                 ('いきぎしじちぢにひびぴみり', 'い'),
                 ('うくぐすずつづぬふぶぷむゆる', 'う'),
                 ('えけげせぜてでねへべぺめれ', 'え'),
                 ('おこごそぞとどのほぼぽもよろを', 'お')]:
    for _ch in _row:
        _VOWEL_OF[_ch] = _v


# Short Latin letter runs (<=3) fold to their Japanese letter-name readings
# BEFORE kakasi, mirroring the digit fold in the read-back gates: whisper
# renders オーエルさん as 'OLさん', and stripping the letters would compare
# さん against おーえるさん. Per-letter names only — a real English word
# (4+ letters, e.g. 'move') is left alone so letters can't counterfeit it.
_LETTER_NAMES = {
    'a': 'えー', 'b': 'びー', 'c': 'しー', 'd': 'でぃー', 'e': 'いー',
    'f': 'えふ', 'g': 'じー', 'h': 'えいち', 'i': 'あい', 'j': 'じぇー',
    'k': 'けー', 'l': 'える', 'm': 'えむ', 'n': 'えぬ', 'o': 'おー',
    'p': 'ぴー', 'q': 'きゅー', 'r': 'あーる', 's': 'えす', 't': 'てぃー',
    'u': 'ゆー', 'v': 'ぶい', 'w': 'だぶりゅー', 'x': 'えっくす',
    'y': 'わい', 'z': 'ぜっと',
}


def _fold_letter_names(s):
    return re.sub(r'(?<![A-Za-z])[A-Za-z]{1,3}(?![A-Za-z])',
                  lambda m: ''.join(_LETTER_NAMES[c] for c in m.group(0).lower()),
                  s)


_DIGIT_NAMES = {ord(a): b for digits in ('0123456789', '０１２３４５６７８９')
                for a, b in zip(digits, ['ぜろ', 'いち', 'に', 'さん', 'よん',
                                         'ご', 'ろく', 'なな', 'はち', 'きゅう'])}


def to_hira(text):
    """Fold any spoken form (kanji, katakana, digits, letter names, romaji
    leftovers) to bare hiragana — the same kakasi path the read-back gates
    use. Caveat: kakasi picks ONE reading for a kanji; author jp_speak with
    the sung reading for ambiguous kanji (the E8 culture) so this never has
    to guess."""
    global _KKS
    import jaconv
    if _KKS is None:
        import pykakasi
        _KKS = pykakasi.kakasi()
    s = _fold_letter_names(str(text).translate(_DIGIT_NAMES))
    out = ''.join(item['hira'] for item in _KKS.convert(s))
    out = jaconv.kata2hira(out)
    return re.sub(r'[^ぁ-ゖー]', '', out)


def mora_profile(kana):
    """(n_morae, n_special, hira) for a spoken form.

    n_morae counts every mora INCLUDING ー, っ and ん (they all take real
    time — unlike gen_audio._mora_count, which strips them for its
    "how short is this word" routing question).
    n_special counts the sustain-critical morae: long-vowel marks, geminates,
    and long vowels written as bare-vowel repeats (いい) or お+う / え+い.
    Weighted morae = n_morae + n_special."""
    h = to_hira(kana)
    small_big = dict(zip('ぁぃぅぇぉ', 'あいうえお'))
    morae = []
    for ch in h:
        if ch in _YOON and morae:
            morae[-1] += ch              # きゃ = one mora, no extra time
        elif ch in _SMALL_VOWELS and morae:
            big = small_big.get(ch, ch)
            prev_base = morae[-1][0]
            morae[-1] += ch
            # a small vowel MATCHING the previous vowel is a written long
            # vowel (あぁ, ねぇ) — sustained, add a ー pseudo-mora. A small
            # vowel changing the vowel is a foreign-sound compound
            # (ふぁ, てぃ, ちぇ) — one ordinary mora, no extra time.
            if _VOWEL_OF.get(prev_base) == big or prev_base == 'ー':
                morae.append('ー')
        else:
            morae.append(ch)
    n = len(morae)
    special = 0
    prev = None
    for m in morae:
        b = m[0]
        if b == 'ー':
            special += 1
            continue                     # keeps prev: っー sequences stay sane
        if b == 'っ':
            special += 1
            prev = None
            continue
        if b in 'あいうえお' and prev is not None:
            # a bare vowel extending the previous mora's vowel is sustained:
            # いい (い after い), ねえ (え after ね), plus おう / えい patterns
            if _VOWEL_OF.get(prev) == b \
                    or (b == 'う' and _VOWEL_OF.get(prev) == 'お') \
                    or (b == 'い' and _VOWEL_OF.get(prev) == 'え'):
                special += 1
        prev = b if b != 'ん' else None
    return n, special, h


# ------------------------------------------------------------------- measure

def load_mono(path):
    """Decode to mono float32 @48k via ffmpeg (uniform for wav and mp3).
    Any decode problem — including ffmpeg itself missing — returns (None, 0)
    so judge() can turn it into an honest 'fail' instead of a traceback."""
    try:
        r = subprocess.run(
            ['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'f32le', '-ac', '1',
             '-ar', '48000', '-'],
            capture_output=True)
    except OSError:
        return None, 0
    if r.returncode != 0 or not r.stdout:
        return None, 0
    return np.frombuffer(r.stdout, dtype=np.float32), 48000


def measure(path):
    """Envelope metrics for one clip, or None when it can't be decoded."""
    x, sr = load_mono(path)
    if x is None or len(x) < int(0.02 * sr):
        return None
    n = int(FRAME_S * sr)
    nf = len(x) // n
    if nf < 2:
        return None
    frames = x[:nf * n].reshape(nf, n)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    file_dur = len(x) / sr

    audible = np.where(db > FLOOR_DB)[0]
    if audible.size == 0:
        return dict(file_dur=round(file_dur, 3), speech_dur=0.0, silent=True,
                    tail_max=0.0, tail_mean=0.0, head_max=0.0)
    first, last = int(audible[0]), int(audible[-1])
    speech_dur = (last - first + 1) * FRAME_S
    voiced = db[first:last + 1]
    med = float(np.median(voiced[voiced > FLOOR_DB]))

    k = max(1, int(EDGE_S / FRAME_S))
    tail = db[max(first, last - k + 1):last + 1]
    head = db[first:min(last + 1, first + k)]
    return dict(
        file_dur=round(file_dur, 3),
        speech_dur=round(speech_dur, 3),
        med_db=round(med, 1),
        tail_max=round(float(np.max(tail)) - med, 1),
        tail_mean=round(float(np.mean(tail)) - med, 1),
        head_max=round(float(np.max(head)) - med, 1),
        silent=False,
    )


# --------------------------------------------------------------------- judge

def judge(metrics, kana):
    """(verdict, reasons, profile) — 'fail' | 'suspect' | 'pass'.

    Reasons are operator-facing plain English; keep them that way."""
    n, special, h = mora_profile(kana)
    w = max(n + special, 1)
    prof = dict(morae=n, special=special, weighted=w, hira=h)
    if metrics is None:
        return 'fail', ['the audio could not be decoded'], prof
    if metrics.get('silent') or metrics['speech_dur'] < MIN_SPEECH_ABS:
        return 'fail', ['the clip is silent (or nearly so)'], prof

    sd, fd = metrics['speech_dur'], metrics['file_dur']
    t_max, t_mean = metrics['tail_max'], metrics['tail_mean']
    fails, sus = [], []

    # connected speech compresses: long utterances (full lines) run a faster
    # per-mora rate than isolated citation words, so the floor eases past 12
    per_mora_floor = MIN_SPEECH_PER_MORA if w <= 12 else 0.070
    if sd < per_mora_floor * w:
        fails.append(f'{sd:.2f}s of speech cannot hold {w} morae '
                     f'({h}) — the take is truncated')
    if fd < SHORT_FILE_S and t_max > SHORT_FILE_TAIL_DB:
        fails.append(f'a {fd:.2f}s take that still ends near voiced energy '
                     f'(tail {t_max:+.1f}dB) — cut mid-word')
    if t_max > CUT_TAIL_MAX_DB and t_mean > CUT_TAIL_MEAN_DB:
        fails.append(f'sustained energy at the very end '
                     f'(tail {t_max:+.1f}/{t_mean:+.1f}dB) — cut mid-sound '
                     f'or the window caught the next word')
    if fails:
        return 'fail', fails, prof

    if t_max > SUS_TAIL_MAX_DB:
        sus.append(f'the take may be clipped at the end (tail {t_max:+.1f}dB)')
    if sd / w > MAX_SPEECH_PER_MORA:
        sus.append(f'unusually long for its reading ({sd:.2f}s for {w} '
                   f'morae) — may contain extra material')
    if sd < SUS_SPEECH_PER_MORA * w:
        sus.append(f'short for {w} morae ({sd:.2f}s) — worth a listen')
    if sus:
        return 'suspect', sus, prof
    return 'pass', [], prof


def check(path, kana):
    """One-call convenience: measure + judge -> a JSON-able dict."""
    m = measure(path)
    verdict, reasons, prof = judge(m, kana)
    return dict(clip=str(path), kana=str(kana), verdict=verdict,
                reasons=reasons, metrics=m, **prof,
                thresholds=THRESHOLDS_ID)


# ------------------------------------------------------------------ selftest

def _write_synth(path, segments, sr=48000):
    """Write a 16-bit mono wav from (seconds, amplitude_env) segments, where
    amplitude_env is (start_amp, end_amp) linearly interpolated over a 220Hz
    tone. Lets the selftest probe each judge() rule with a known envelope."""
    import wave
    t_off = 0.0
    chunks = []
    for dur, (a0, a1) in segments:
        n = int(dur * sr)
        t = np.arange(n) / sr + t_off
        env = np.linspace(a0, a1, n)
        chunks.append(env * np.sin(2 * np.pi * 220 * t))
        t_off += dur
    x = np.concatenate(chunks)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32000).astype('<i2').tobytes())


def selftest():
    """Regression-prove the thresholds.

    Leg 1: the recovered ground truth — every physics_fixtures/bad/ clip must
    FAIL, every good/ clip must not.
    Leg 2: synthetic envelopes that isolate each fail rule (a hard-cut
    sustained tone must trip CUT_TAIL, a too-brief decayed tone must trip the
    per-mora speech floor, a clean decayed tone must PASS) — so a threshold
    edit or sign flip that disables one rule can't slip through just because
    the fixtures happen to trip another.
    Returns the number of misjudgments (0 = healthy)."""
    import tempfile
    misses = 0
    for tag, want_fail in (('bad', True), ('good', False)):
        for p in sorted((FIXTURES / tag).glob('*.mp3')):
            kana = p.stem.split('__', 1)[-1]
            r = check(p, kana)
            got_fail = r['verdict'] == 'fail'
            ok = got_fail == want_fail
            if not ok:
                misses += 1
            print(f"  {'ok ' if ok else 'MISJUDGED'} {tag}/{p.name} "
                  f"expected {'fail' if want_fail else 'not-fail'}, "
                  f"got {r['verdict']} {r['reasons']}")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        probes = [
            # constant tone chopped at full amplitude: CUT_TAIL must fire
            ('hard-cut', 'しんごう', [(0.8, (0.3, 0.3))], 'fail'),
            # 0.25s decayed tone then silence, expected 7 morae: speech floor
            ('too-brief', 'おもいだしたら',
             [(0.18, (0.3, 0.3)), (0.07, (0.3, 0.0)), (0.45, (0.0, 0.0))], 'fail'),
            # decayed tone, plausible for 2 morae: must pass clean
            ('clean', 'こと',
             [(0.3, (0.3, 0.3)), (0.15, (0.3, 0.0)), (0.2, (0.0, 0.0))], 'pass'),
        ]
        for name, kana, segs, want in probes:
            p = td / f'{name}.wav'
            _write_synth(p, segs)
            r = check(p, kana)
            ok = (r['verdict'] == want) if want == 'pass' else (r['verdict'] == 'fail')
            if not ok:
                misses += 1
            print(f"  {'ok ' if ok else 'MISJUDGED'} synth/{name} ({kana}) "
                  f"expected {want}, got {r['verdict']} {r['reasons']}")
    print(f'selftest: {"clean" if misses == 0 else f"{misses} misjudged"}')
    return misses


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('clip', nargs='?', help='audio file to check')
    ap.add_argument('--kana', help='expected spoken form (kana or kanji)')
    ap.add_argument('--json', action='store_true',
                    help='print one JSON line (last line of stdout)')
    ap.add_argument('--selftest', action='store_true',
                    help='re-prove thresholds on the ground-truth fixtures')
    a = ap.parse_args(argv)
    if a.selftest:
        return 1 if selftest() else 0
    if not a.clip or not a.kana:
        ap.error('need <clip> and --kana (or --selftest)')
    r = check(a.clip, a.kana)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        m = r['metrics'] or {}
        print(f"{r['verdict'].upper()}  {a.clip}")
        print(f"  reading {r['hira']} = {r['weighted']} weighted morae "
              f"({r['morae']}+{r['special']} sustained)")
        if m and not m.get('silent'):
            print(f"  file {m['file_dur']:.2f}s  speech {m['speech_dur']:.2f}s"
                  f"  tail {m['tail_max']:+.1f}/{m['tail_mean']:+.1f}dB")
        for why in r['reasons']:
            print(f'  - {why}')
    return 0 if r['verdict'] != 'fail' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
