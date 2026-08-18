#!/usr/bin/env python3
"""Phrase-cut: get a natural, SHORT particle/word clip by synthesizing it inside
a carrier phrase (where TTS says it correctly and briefly) and slicing it out by
Whisper word-timestamp. The fix for TTS elongating isolated particles into
exclamations (の -> "nooo", は -> "waaa") when there's no human dictionary clip.

Run in `parler` env. Usage (as a module or tweak MAIN below):
  python3 phrase_cut.py 'の:子供の頃:word_v1_no'   # target:carrier:out_stem
Outputs a -16 LUFS / 44.1k wav into _assets/inochi-mijikashi/audio/jp/<stem>.wav
(edit OUT_DIR per song). Verifies read-back; refuses if the cut doesn't say the target.
"""
import os, sys, glob, json, subprocess, re, tempfile
os.environ.setdefault('HF_HUB_OFFLINE', '1')
import soundfile as sf, numpy as np
import jaconv
from pykakasi import kakasi
from mlx_audio.tts.generate import generate_audio
from faster_whisper import WhisperModel
import clip_physics

OUT_DIR = os.environ.get('PHRASE_CUT_OUT') or '<repo>/songs/_assets/inochi-mijikashi/audio/jp'
TMP = os.environ.get('PHRASE_CUT_TMP') or os.path.join(tempfile.gettempdir(), 'phrase_cut')
os.makedirs(TMP, exist_ok=True)
MODEL = 'mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit'
INSTRUCT = '穏やかな大人の朗読'
PAD_PRE, PAD_POST, FADE = 0.02, 0.03, 0.010   # seconds
kks = kakasi()
def hira(s):
    # shared fold: short Latin letter runs read as letter names (OL→おーえる),
    # mirroring clip_physics/install_word/gen_audio so 'OLさん' matches オーエルさん
    s = clip_physics._fold_letter_names(s)
    return re.sub(r'[^ぁ-ゖー]', '', jaconv.kata2hira(''.join(d['hira'] for d in kks.convert(s))))

_wm = None
def wm():
    global _wm
    if _wm is None: _wm = WhisperModel('large-v3', device='cpu', compute_type='int8')
    return _wm

def loudnorm_single(src, dst):  # single-pass: works on sub-second clips
    subprocess.run(['ffmpeg', '-y', '-i', src, '-af',
        'loudnorm=I=-16:TP=-1.5:LRA=11', '-ar', '44100', '-codec:a', 'pcm_s16le', dst],
        capture_output=True, check=True)

def trim_tail_energy(seg, sr):
    """Retreat the cut end to the deepest energy valley in the final 120ms —
    but only when the tail dips well below voiced energy and RISES again
    after the valley. That shape means the window caught the onset of the
    NEXT word (the きっと/さぞ artifact class: whisper's word_end ran late and
    the shipped clip ended in a fresh burst of someone else's phoneme).
    A natural decaying tail is left untouched."""
    n = max(1, int(0.010 * sr))
    nf = len(seg) // n
    if nf < 6:
        return seg
    fr = seg[:nf * n].reshape(nf, n)
    db = 20 * np.log10(np.maximum(np.sqrt((fr ** 2).mean(1)), 1e-9))
    voiced = db[db > -50]
    if voiced.size == 0:
        return seg
    med = float(np.median(voiced))
    k = min(nf - 2, 12)                      # inspect the final 120ms
    tail = db[nf - k:]
    j = int(np.argmin(tail))
    if tail[j] < med - 12 and j < k - 1 and float(tail[-1]) > float(tail[j]) + 6:
        return seg[:(nf - k + j + 1) * n]
    return seg

def phrase_cut(target, carrier, out_stem, tries=6, strict=False):
    target_h = hira(target)
    for t in range(tries):
        for f in glob.glob(TMP + f'/_c{t}*'): os.remove(f)
        generate_audio(text=carrier, model=MODEL, voice='Ono_Anna', lang_code='ja',
                       instruct=INSTRUCT, output_path=TMP, file_prefix=f'_c{t}',
                       audio_format='wav', verbose=False)
        wav = sorted(glob.glob(TMP + f'/_c{t}*.wav'))
        if not wav: continue
        carrier_wav = wav[0]
        segs, _ = wm().transcribe(carrier_wav, language='ja', vad_filter=False,
                                  beam_size=5, word_timestamps=True)
        words = [w for s in segs for w in (s.words or [])]
        # find the word whose text matches the target (kana-normalized). Whisper
        # often splits a word across tokens (乗+って) or folds small kana
        # (あぁ→ああ), so match a WINDOW of consecutive tokens on folded hira
        # (small vowels folded; っ kept — どっか must never match どうか).
        fold = lambda s: s.translate(str.maketrans('ぁぃぅぇぉ', 'あいうえお'))
        tgt_f = fold(target_h)
        hit = None
        for i in range(len(words)):
            acc = ''
            for j in range(i, min(i + 4, len(words))):
                acc += fold(hira(words[j].word))
                if acc == tgt_f:
                    hit = (words[i].start, words[j].end)
                    break
                if len(acc) > len(tgt_f):
                    break
            if hit: break
        if hit is None:
            # kanji fragments defeat kakasi (a lone 乗 reads じょう, not のっ) —
            # window-match the RAW transcript text against the raw target too.
            # Iterate start positions from the END so the window is start-tight:
            # the target must begin AT words[i], not merely appear after a
            # swallowed prefix (バスに乗って must cut 乗って, not the whole phrase).
            for i in range(len(words) - 1, -1, -1):
                acc = ''
                for j in range(i, min(i + 4, len(words))):
                    acc += words[j].word.strip()
                    if acc.startswith(target) or (target in acc and j == i):
                        hit = (words[i].start, words[j].end)
                        break
                    if not target.startswith(acc):
                        break
                if hit: break
        if hit is None:  # last resort: target inside one merged token
            w1 = next((w for w in words if tgt_f and tgt_f in fold(hira(w.word))), None)
            if w1: hit = (w1.start, w1.end)
        if hit is None:
            print(f"  try{t}: target {target!r} not isolated in {[w.word for w in words]}")
            continue
        hit_start, hit_end = hit
        data, sr = sf.read(carrier_wav)
        if data.ndim > 1: data = data.mean(1)
        a = max(0, int((hit_start - PAD_PRE) * sr)); b = min(len(data), int((hit_end + PAD_POST) * sr))
        seg = trim_tail_energy(data[a:b].copy(), sr)
        nf = max(1, int(FADE * sr))
        seg[:nf] *= np.linspace(0, 1, nf); seg[-nf:] *= np.linspace(1, 0, nf)
        raw = TMP + f'/cut_{out_stem}.wav'; sf.write(raw, seg, sr)
        # verify against a TMP copy — an existing take at the destination is
        # only replaced by a take that passed BOTH gates (a failed re-cut must
        # neither clobber nor delete a good clip already in _assets).
        cand = TMP + f'/ln_{out_stem}.wav'; loudnorm_single(raw, cand)
        # verify 1: text. <=2-mora targets ALWAYS demand exact folded equality —
        # the default superstring check let 'こと' pass inside 'だいじなこと' and
        # containment let a これでいい cut ship as いい (backlog 0bd85bd1).
        segs2, _ = wm().transcribe(cand, language='ja', vad_filter=False, beam_size=5)
        heard = ''.join(s.text for s in segs2).strip()
        heard_f = fold(hira(heard))
        core = re.sub(r'[ーっ]', '', target_h)
        short = sum(1 for ch in core if ch not in 'ゃゅょぁぃぅぇぉゎ') <= 2
        ok = heard_f == tgt_f if (strict or short) else (tgt_f in heard_f or heard_f in tgt_f)
        # verify 2: clip physics — the take must be a clean 'pass' (duration +
        # envelope vs the reading). Retries are cheap; a marginal take is not.
        phys_why = ''
        if ok:
            phys = clip_physics.check(cand, target)
            if phys['verdict'] != 'pass':
                ok, phys_why = False, ' physics: ' + '; '.join(phys['reasons'])
        dur = len(seg) / sr
        print(f"  try{t}: cut {target!r} [{hit_start:.2f}-{hit_end:.2f}] dur={dur:.2f}s "
              f"heard={heard!r} -> {'OK' if ok else 'retry'}{phys_why}")
        if ok:
            dst = os.path.join(OUT_DIR, out_stem + '.wav')
            os.replace(cand, dst)
            return True
    return False

if __name__ == '__main__':
    args = sys.argv[1:]
    strict = '--strict' in args
    specs = [s for s in args if s != '--strict'] or ['の:子供の頃:word_v1_no']
    for spec in specs:
        target, carrier, stem = spec.split(':')
        print(f"phrase-cut {target} from {carrier!r} -> {stem}" + (' [strict]' if strict else ''))
        if not phrase_cut(target, carrier, stem, strict=strict):
            print(f"  FAILED to isolate {target}")
