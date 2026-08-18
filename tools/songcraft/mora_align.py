#!/usr/bin/env python3
"""mora_align.py — Prototype A: mora-token CTC alignment (per-line windowed).

The shipped mora timing (content_to_data.timed_morae) divides each licensed
word window EVENLY among its morae — a held vowel gets the same slice as a
flicked consonant. This module asks the same MMS CTC forced aligner that owns
our word timing to time each MORA instead: per line, one romaji token per mora
(same moraize() segmentation as content_to_data, so counts match by
construction), aligned INSIDE the line's existing anchored [begin,end] window
(stem sliced to the span ±pad; per-line windowing keeps CTC from parking
repeats over instrumentals — the whole-song failure mode).

Three timing schemes over the same mora tokens (all emitted in the same
production shape {line_idx: [{kana, rom, begin_ms, end_ms, cscore, word_i,
word, src}]}):
  ctc      per-line windowed CTC mora alignment (this prototype's candidate)
  even     even division of each word window (the shipped baseline)
  weighted phoneme-class-weighted division (consonants short, vowels long via
           pyopenjtalk phoneme classes) — a zero-model control

Run with the parler python (pyopenjtalk/jaconv + ctc_forced_aligner live there):
  .../envs/parler/bin/python mora_align.py <key> --lyrics <lyrics.json> \
      --stem <vocals.wav> --out <path.json> [--mode ctc|even|weighted] [--pad-ms 500]
The CLI never reads or writes builds/ — callers stage lyrics and pick --out
(whisper_sync --morae is the production entry that targets builds/).
"""
import argparse, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import content_to_data as c2d  # moraize / reading_hira / mora_rom / strip_echo / CJK

SR = 16000
_AZ = re.compile(r'[^a-z]')


def _clean(s):
    return _AZ.sub('', (s or '').lower())


def line_words(line):
    """The exact word-window list content_to_data feeds timed_morae: words[]
    with the echo tail walked off against the stripped display text, or the
    whole line window when the line has no words[]. [{text, begin_ms, end_ms}]."""
    text = c2d.strip_echo(line.get('text', ''))
    words = [{'text': w['text'], 'begin_ms': int(w['begin_ms']), 'end_ms': int(w['end_ms'])}
             for w in (line.get('words') or []) if c2d.strip_echo(w['text']).strip()]
    cur, kept = 0, []
    for w in words:
        wt = w['text']
        while cur < len(text) and text[cur].isspace():
            cur += 1
        if text[cur:cur + len(wt)] == wt:
            kept.append(w); cur += len(wt)
        else:
            break  # echo tail starts here
    words = kept
    if not words:
        if not text.strip():
            return []
        return [{'text': text, 'begin_ms': int(line['begin_ms']), 'end_ms': int(line['end_ms'])}]
    return [{'text': c2d.strip_echo(w['text']), 'begin_ms': w['begin_ms'], 'end_ms': w['end_ms']}
            for w in words]


def word_morae(word_text):
    """One entry per mora, SAME segmentation as content_to_data.timed_morae:
    CJK -> pyopenjtalk reading -> moraize(); latin/digits -> single token.
    [{kana, rom}] (rom == '' when nothing latin survives — caller interpolates)."""
    if c2d.CJK.search(word_text):
        hira = c2d.reading_hira(word_text)
        morae = c2d.moraize(hira) or [word_text]
        return [{'kana': m, 'rom': _clean(c2d.mora_rom(m))} for m in morae]
    return [{'kana': word_text, 'rom': _clean(word_text)}]


def line_mora_tokens(line):
    """[{word_i, word, kana, rom, wb, we}] for one line (wb/we = the word's
    licensed window, the even/weighted division domain)."""
    out = []
    for wi, w in enumerate(line_words(line)):
        for m in word_morae(w['text']):
            out.append({'word_i': wi, 'word': w['text'], 'kana': m['kana'],
                        'rom': m['rom'], 'wb': w['begin_ms'], 'we': w['end_ms']})
    return out


# ------------------------------------------------------------ zero-model rows
def even_entries(toks):
    """Shipped-baseline timing: each word window divided evenly among its
    morae (bit-identical policy to content_to_data.timed_morae)."""
    out = []
    for wi, group in _by_word(toks):
        b, e, n = group[0]['wb'], group[0]['we'], len(group)
        step = (e - b) / max(n, 1)
        for i, t in enumerate(group):
            out.append(_entry(t, b + i * step, b + (i + 1) * step, None, 'even'))
    return out


_PH_W = {'a': 1.0, 'i': 1.0, 'u': 1.0, 'e': 1.0, 'o': 1.0, 'N': 0.9, 'cl': 0.7}
_g2p_cache = {}


def _mora_weight(kana):
    """Phoneme-class weight of one mora: vowels long, consonants short.
    かー -> 'k a a' -> 0.35+1+1; っ -> 'cl' -> 0.7; latin fallback 1.0/mora."""
    if kana not in _g2p_cache:
        try:
            import pyopenjtalk
            ph = pyopenjtalk.g2p(kana).split()
        except Exception:
            ph = []
        _g2p_cache[kana] = sum(_PH_W.get(p, 0.35) for p in ph) or 1.0
    return _g2p_cache[kana]


def weighted_entries(toks):
    """Control row: word window divided proportionally to phoneme-class
    weights instead of evenly."""
    out = []
    for wi, group in _by_word(toks):
        b, e = group[0]['wb'], group[0]['we']
        ws = [_mora_weight(t['kana']) if c2d.KANA.search(t['kana']) else 1.0
              for t in group]
        tot = sum(ws) or 1.0
        span, acc = e - b, 0.0
        for t, w in zip(group, ws):
            mb = b + (acc / tot) * span
            acc += w
            out.append(_entry(t, mb, b + (acc / tot) * span, None, 'weighted'))
    return out


def _by_word(toks):
    groups = {}
    for t in toks:
        groups.setdefault(t['word_i'], []).append(t)
    return sorted(groups.items())


def _entry(t, b, e, cscore, src):
    return {'kana': t['kana'], 'rom': t['rom'], 'begin_ms': int(round(b)),
            'end_ms': int(round(e)), 'cscore': cscore,
            'word_i': t['word_i'], 'word': t['word'], 'src': src}


# ---------------------------------------------------------------- CTC aligner
class MoraAligner:
    """Loads the MMS-300M ONNX model + the 16k mono stem once; aligns one
    line's mora tokens inside its ±pad window per call."""

    def __init__(self, stem16_wav):
        import os
        import onnxruntime as ort
        from ctc_forced_aligner import load_audio, ensure_onnx_model, MODEL_URL
        mp = os.path.expanduser('~/.cache/ctc_forced_aligner/model.onnx')
        ensure_onnx_model(mp, MODEL_URL)
        self.sess = ort.InferenceSession(mp, providers=['CPUExecutionProvider'])
        self.audio = load_audio(str(stem16_wav))

    def align_line(self, toks, begin_ms, end_ms, pad_ms=500):
        """CTC-align the alignable (rom != '') tokens in [begin-pad, end+pad].
        Mutates toks: sets t['b'], t['e'] (ms, absolute clock) + t['cscore']
        on aligned tokens. Returns n_aligned. Raises on aligner failure."""
        from ctc_forced_aligner import (generate_emissions, get_alignments,
                                        get_spans, postprocess_results,
                                        preprocess_text, Tokenizer)
        alig = [t for t in toks if t['rom']]
        if not alig:
            return 0
        t0 = max(0.0, (begin_ms - pad_ms) / 1000.0)
        t1 = min(len(self.audio) / SR, (end_ms + pad_ms) / 1000.0)
        sl = self.audio[int(t0 * SR):int(t1 * SR)]
        if len(sl) < SR // 10:
            return 0
        emissions, stride = generate_emissions(self.sess, sl, batch_size=8)
        ts, txts = preprocess_text(' '.join(t['rom'] for t in alig),
                                   romanize=False, language='eng', split_size='word')
        segs, scores, blank = get_alignments(emissions, ts, Tokenizer())
        spans = get_spans(ts, segs, blank)
        wts = postprocess_results(txts, spans, stride, scores)
        if len(wts) != len(alig):
            raise RuntimeError(f'mora drift: {len(wts)} stamps != {len(alig)} tokens')
        for t, w in zip(alig, wts):
            t['b'] = (t0 + float(w['start'])) * 1000.0
            t['e'] = (t0 + float(w['end'])) * 1000.0
            t['cscore'] = float(w.get('score', 0.0))
        return len(alig)


def ctc_entries(toks, aligner, begin_ms, end_ms, pad_ms=500):
    """CTC row for one line. Unalignable tokens (empty rom) are interpolated
    between aligned neighbours. Falls back to even division (src marks it)
    when the aligner fails or aligns nothing."""
    for t in toks:
        t.pop('b', None); t.pop('e', None); t['cscore'] = None
    try:
        n = aligner.align_line(toks, begin_ms, end_ms, pad_ms)
    except Exception as ex:
        print(f'[mora] line CTC failed ({ex}); even fallback', file=sys.stderr)
        n = 0
    if not n:
        return [dict(e, src='fallback-even') for e in even_entries(toks)], 'fallback-even'
    out = []
    idx_a = [i for i, t in enumerate(toks) if t.get('b') is not None]
    for i, t in enumerate(toks):
        if t.get('b') is not None:
            out.append(_entry(t, t['b'], t['e'], t['cscore'], 'ctc'))
        else:
            prev_e = next((toks[j]['e'] for j in reversed(idx_a) if j < i), float(begin_ms))
            next_b = next((toks[j]['b'] for j in idx_a if j > i), float(end_ms))
            mid_b = min(prev_e, next_b)
            out.append(_entry(t, mid_b, max(mid_b, next_b), None, 'interp'))
    return out, 'ctc'


def hybridize_line(entries, words):
    """The SHIPPED hybrid consumption (content_to_data.hybrid_mora_times),
    applied to one line's raw CTC entries for benching: per word, first mora
    at the shipped word onset, CTC interiors clamped into the word window,
    contiguous spans. words = line_words(line) (word_i indexes it)."""
    out = []
    groups = {}
    for e in entries:
        groups.setdefault(e['word_i'], []).append(e)
    for wi in sorted(groups):
        group = groups[wi]
        w = words[wi]
        times = c2d.hybrid_mora_times(group, w['begin_ms'], w['end_ms'])
        for e, (b, en) in zip(group, times):
            out.append(dict(e, begin_ms=b, end_ms=en,
                            src=('hybrid' if e.get('src') == 'ctc' else e.get('src'))))
    return out


def hybrid_from_ctc(lyr, ctc_map):
    """Whole-song hybrid derived from an existing raw-CTC map (no realign)."""
    timings, status = {}, {}
    for k, entries in ctc_map.items():
        i = int(k)
        words = line_words(lyr['lines'][i])
        timings[i] = hybridize_line(entries, words)
        status[i] = 'hybrid'
    return timings, status


# --------------------------------------------------------------------- driver
def align_song(lyr, mode, stem16=None, pad_ms=500, verbose=True):
    """-> ({line_idx: [entries]}, {line_idx: status}) for every non-empty line."""
    aligner = MoraAligner(stem16) if mode in ('ctc', 'hybrid') else None
    timings, status = {}, {}
    for i, line in enumerate(lyr['lines']):
        if not (line.get('text') or '').strip():
            continue
        toks = line_mora_tokens(line)
        if not toks:
            continue
        if mode == 'even':
            timings[i], status[i] = even_entries(toks), 'even'
        elif mode == 'weighted':
            timings[i], status[i] = weighted_entries(toks), 'weighted'
        else:
            timings[i], status[i] = ctc_entries(toks, aligner, line['begin_ms'],
                                                line['end_ms'], pad_ms)
            if mode == 'hybrid':
                timings[i] = hybridize_line(timings[i], line_words(line))
                if status[i] == 'ctc':
                    status[i] = 'hybrid'
        if verbose:
            print(f'[mora] line {i:3d} {status[i]:>13} {len(timings[i]):3d} morae '
                  f'{line.get("text", "")[:28]!r}', flush=True)
    return timings, status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('key')
    ap.add_argument('--lyrics', required=True, help='lyrics.json (staged copy)')
    ap.add_argument('--stem', default=None,
                    help='16k-or-any mono/stereo vocal stem wav (ctc mode)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--mode', default='ctc',
                    choices=['ctc', 'even', 'weighted', 'hybrid'])
    ap.add_argument('--from-ctc', default=None,
                    help='hybrid mode only: derive from this existing raw-CTC '
                         'json instead of re-running the aligner')
    ap.add_argument('--pad-ms', type=int, default=500)
    a = ap.parse_args()
    lyr = json.loads(Path(a.lyrics).read_text())
    if 'lines' not in lyr:
        raise SystemExit(f'{a.lyrics} has no "lines" key')
    if a.mode == 'hybrid' and a.from_ctc:
        timings, status = hybrid_from_ctc(lyr, json.loads(Path(a.from_ctc).read_text()))
    elif a.mode in ('ctc', 'hybrid') and not a.stem:
        raise SystemExit(f'--stem required for --mode {a.mode}')
    else:
        timings, status = align_song(lyr, a.mode, a.stem, a.pad_ms)
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps({str(k): v for k, v in sorted(timings.items())},
                               ensure_ascii=False, indent=1))
    n = sum(len(v) for v in timings.values())
    n_ctc = sum(1 for v in timings.values() for e in v
                if e['src'] in ('ctc', 'hybrid'))
    print(f'[mora] {a.key} ({a.mode}): {len(timings)} lines, {n} morae '
          f'({n_ctc} CTC-timed) -> {outp}')


if __name__ == '__main__':
    main()
