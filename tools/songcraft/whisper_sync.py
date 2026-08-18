#!/usr/bin/env python3
"""
whisper_sync.py — align a song's lyric timing to its actual YouTube vocal by
FORCED ALIGNMENT (the robust method; the name is kept for the pipeline).

The problem: line-level lyric sources (LRCLIB) are timed to the studio track and
are often many seconds off the video (踊り子 was 12s early); even syllable-level
Apple TTML is a few hundred ms off the video edit. Onset-only or whisper-
transcription sync is fragile — an instrumental intro gets hallucinated as words,
and breathy ad-libs (死ぬのがいいわ opens with "はぁ" moans) look like the first
line. So we don't guess where the vocal is; we already KNOW the lyrics, so we
ask a CTC forced aligner WHEN each known lyric word is sung.

Pipeline:
  1. Demucs isolates the vocal stem (instrumental + its hallucinations gone).
  2. The lyric lines are romanized (pykakasi hepburn; fugashi word split) and
     force-aligned to the stem (the same MMS aligner the podcast uses — given
     the real words it rarely drifts onto ad-libs or instrument hits, but it
     CAN smear: runs of identical repeated lines (hooks/scat like 踊り子's
     とぅるるる) give CTC nothing to disambiguate, and it has parked one
     repeat over a whole instrumental break, stretching tokens to 8-9s. That
     corruption shipped to 3 of 5 live songs, so --apply now runs a hard
     timing-plausibility gate (see plausibility_gate) before writing).
  3. Each line's begin/end is set from its first/last aligned word; the line's
     existing words + kana are shifted by that per-line delta (so Apple's
     accurate intra-line timing is preserved, just re-anchored to the video).

The first sung line lands exactly on the first sung word — the intro silence /
ad-libs are skipped automatically (owner: "bypass the silence in the beginning").

Writes corrected timing back into builds/<key>.lyrics.json; caller re-runs
content_to_data + assemble. Vocal stem is cached under /tmp.

Run with the parler python:
  python whisper_sync.py <key> --yt <id> [--apply]
Without --apply it only reports the proposed correction (dry run).

--words mode (opt-in, universal LRCLIB word-timing):
  python whisper_sync.py <key> --yt <id> --words --apply [--out PATH]
The same CTC forced alignment already pins WHEN each lyric word is sung; --words
writes those onsets into each line's words[] as Apple-TTML-shaped
{text, begin_ms, end_ms}. Word times are anchored INTO each line's EXISTING
[begin_ms, end_ms] via a per-line affine map, so they live on the same
(already video-anchored) clock as the line times and never depend on the raw
CTC global offset/scale. This gives LRCLIB songs (line-level only, e.g. 踊り子)
a true word-level kanji reveal identical to the Apple-TTML songs. --words leaves
line begin/end UNCHANGED and does NOT touch Apple-sourced flows (those already
carry real words[] from TTML); it is purely additive. `--out` stages the result
to an arbitrary path instead of overwriting builds/<key>.lyrics.json.
"""
import argparse, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

BUILDS = Path(__file__).resolve().parent / 'builds'
TMP = Path('/tmp')          # fixed cache dir (macOS tempfile.gettempdir() varies)
CORPUS = Path(__file__).resolve().parent / 'corpus'   # durable, gitignored

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import timing_overrides     # manual-edit sidecar, re-applied before every write


def _write_through(src, dst):
    """Best-effort durable copy into corpus/ (temp + os.replace, so a crash
    can't leave a torn wav). /tmp is lost on reboot; corpus/ means a reboot
    never forces a YouTube re-download or a Demucs re-run."""
    import os
    try:
        if dst.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_suffix(dst.suffix + '.part')
        shutil.copy2(src, part)
        os.replace(part, dst)
        print(f'[cache] wrote through {src.name} -> {dst}')
    except Exception as ex:
        print(f'[cache] write-through to {dst} failed ({ex}) — continuing', file=sys.stderr)


def download_audio(yt_id):
    """16k mono full-song wav. CORPUS-FIRST: the durable local copy wins, then
    the /tmp cache, then a corpus hq mix downmixed locally, and only then
    yt-dlp — whose fresh download is written through to corpus/ immediately."""
    corp = CORPUS / f'wsync_{yt_id}.wav'
    if corp.exists():
        return corp
    out = TMP / f'wsync_{yt_id}.wav'
    if out.exists():
        _write_through(out, corp)          # heal the corpus gap while warm
        return out
    chq = CORPUS / f'hq_{yt_id}.wav'
    if chq.exists():                       # derive locally — no network needed
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(chq),
                        '-ar', '16000', '-ac', '1', str(out)], capture_output=True)
        if out.exists():
            print(f'[cache] derived {out.name} from corpus hq (no download)')
            _write_through(out, corp)
            return out
    url = f'https://www.youtube.com/watch?v={yt_id}'
    cmd = ['yt-dlp', '-x', '--audio-format', 'wav', '--audio-quality', '0',
           '--postprocessor-args', '-ar 16000 -ac 1', '-o',
           str(out.with_suffix('')) + '.%(ext)s', url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not out.exists():
        raise SystemExit(f'yt-dlp failed:\n{r.stderr[-1500:]}')
    _write_through(out, corp)
    return out


def full_mix_path(yt_id):
    """Existing full-mix wav (corpus first, then /tmp) or None."""
    for p in (CORPUS / f'hq_{yt_id}.wav', TMP / f'hq_{yt_id}.wav'):
        if p.exists():
            return p
    return None


def vocal_stem(yt_id):
    """Isolate the vocal stem with Demucs (cached). A separated vocal has energy
    only where someone sings, so the aligner never latches onto instrumental.
    /tmp cache first (warm), then the durable corpus/ copy, then a fresh
    Demucs run whose output is written through to corpus/."""
    stem = TMP / 'demucs' / 'htdemucs' / f'hq_{yt_id}' / 'vocals.wav'
    cstem = CORPUS / 'demucs' / 'htdemucs' / f'hq_{yt_id}' / 'vocals.wav'
    if stem.exists():
        _write_through(stem, cstem)        # heal the corpus gap while warm
        return stem
    if cstem.exists():
        return cstem
    wav = download_audio(yt_id)
    hq = full_mix_path(yt_id)
    if hq is None:
        hq = TMP / f'hq_{yt_id}.wav'
        subprocess.run(['ffmpeg', '-y', '-i', str(wav), '-ar', '44100', '-ac', '2', str(hq)],
                       capture_output=True)
        _write_through(hq, CORPUS / f'hq_{yt_id}.wav')
    # torchaudio 2.11 needs torchcodec just to SAVE, so drive demucs via its API
    import numpy as np, soundfile as sf, torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    model = get_model('htdemucs'); model.cpu().eval()
    vi = model.sources.index('vocals')
    y, sr = sf.read(str(hq))
    if y.ndim == 1:
        y = np.stack([y, y], 1)
    t = torch.tensor(y.T, dtype=torch.float32).unsqueeze(0)
    ref = t.mean(0); t = (t - ref.mean()) / (ref.std() + 1e-8)
    with torch.no_grad():
        srcs = apply_model(model, t, split=True, overlap=0.1, progress=False)[0]
    voc = srcs[vi] * (ref.std() + 1e-8) + ref.mean()
    stem.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(stem), voc.T.numpy(), sr)
    _write_through(stem, cstem)
    return stem


def align_lines(key, yt_id):
    """Force-align the lyric lines to the vocal stem. Returns {line_idx:
    (begin_ms, end_ms)} for every line that has alignable words."""
    import os, tempfile as tf
    import onnxruntime as ort
    import fugashi
    from pykakasi import kakasi
    from ctc_forced_aligner import (generate_emissions, get_alignments, get_spans,
        load_audio, postprocess_results, preprocess_text, Tokenizer,
        ensure_onnx_model, MODEL_URL)

    tagger = fugashi.Tagger(); kks = kakasi()
    def romaji(s): return ''.join(seg['hepburn'] for seg in kks.convert(s)).lower()
    def clean(s): return re.sub(r'[^a-z]', '', s)
    def is_jp(t): return bool(re.search(r'[぀-ヿ㐀-鿿]', t))
    def seg(text):
        out = []
        for chunk in re.split(r'(\s+)', text):
            if chunk == '' or chunk.isspace():
                continue
            out.extend([w.surface for w in tagger(chunk)] if is_jp(chunk) else chunk.split())
        return out

    lyr = json.loads((BUILDS / f'{key}.lyrics.json').read_text())
    line_idx = [i for i, L in enumerate(lyr['lines']) if L.get('text', '').strip()]
    toks = []
    for i in line_idx:
        for tk in seg(lyr['lines'][i]['text']):
            rom = clean(romaji(tk)) if is_jp(tk) else clean(tk.lower())
            # keep the surface `text` (fugashi surfaces concat to the line text
            # minus whitespace — the shape tokensFromWords walks) so --words can
            # emit Apple-shaped {text, begin_ms, end_ms}.
            toks.append({'line': i, 'text': tk, 'rom': rom or None})
    words = [t['rom'] for t in toks if t['rom']]
    if not words:
        return None, lyr, toks, line_idx

    stem = vocal_stem(yt_id)
    wav = tf.NamedTemporaryFile(suffix='.wav', delete=False).name
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(stem), '-ar', '16000',
                    '-ac', '1', wav], check=True)
    mp = os.path.expanduser('~/.cache/ctc_forced_aligner/model.onnx')
    ensure_onnx_model(mp, MODEL_URL)
    sess = ort.InferenceSession(mp, providers=['CPUExecutionProvider'])
    audio = load_audio(wav)
    emissions, stride = generate_emissions(sess, audio, batch_size=8)
    ts, txts = preprocess_text(' '.join(words), romanize=False, language='eng', split_size='word')
    segs, scores, blank = get_alignments(emissions, ts, Tokenizer())
    spans = get_spans(ts, segs, blank)
    wts = postprocess_results(txts, spans, stride, scores)
    if len(wts) != len(words):
        raise SystemExit(f'alignment drift: {len(wts)} stamps != {len(words)} words')

    wi = 0
    for t in toks:
        if t['rom']:
            t['begin'] = float(wts[wi]['start']) * 1000.0
            t['end'] = float(wts[wi]['end']) * 1000.0
            t['cscore'] = float(wts[wi].get('score', 0.0))
            wi += 1
    per_line = {}
    for i in line_idx:
        ws = [t for t in toks if t['line'] == i and t.get('begin') is not None]
        if ws:
            per_line[i] = (round(ws[0]['begin']), round(ws[-1]['end']))

    # CTC can PARK trailing lines over the instrumental outro when the lyrics'
    # tail isn't actually sung in the video (踊り子's final とぅるるる block lands
    # at 240s where the vocal stem is silent). Pull any line whose start sits
    # past the last real vocal energy back into the true vocal tail.
    last_v = _last_vocal_ms(stem)
    if last_v:
        tail = [i for i in line_idx if i in per_line and per_line[i][0] > last_v + 400]
        if tail:
            keep = [i for i in line_idx if i in per_line and i not in tail]
            anchor = max(per_line[k][0] for k in keep) if keep else 0
            span = max(last_v - anchor, len(tail) * 300)
            step = span / (len(tail) + 1)
            for n, i in enumerate(sorted(tail), 1):
                nb = round(anchor + step * n)
                per_line[i] = (nb, nb + 900)
            print(f'[sync]   clamped {len(tail)} tail line(s) parked past last vocal '
                  f'({round(last_v)}ms) back into {round(anchor)}–{round(last_v)}ms')
    return per_line, lyr, toks, line_idx


def _last_vocal_ms(stem):
    """Last time (ms) the vocal stem has real singing energy."""
    import numpy as np, soundfile as sf
    y, sr = sf.read(str(stem))
    if y.ndim > 1:
        y = y.mean(axis=1)
    hop = int(sr * 0.05)
    n = len(y) // hop
    rms = np.sqrt(np.array([np.mean(y[i*hop:(i+1)*hop]**2) for i in range(n)]) + 1e-12)
    floor = np.percentile(rms, 30) + 0.10 * (np.percentile(rms, 95) - np.percentile(rms, 30))
    active = np.where(rms > floor)[0]
    return float(active[-1] * hop / sr * 1000.0) if len(active) else None


def manual_start_ms(key):
    """The start point a human set in Denmoku (build_state meta), or None when
    nobody has touched it and the measurement should stand."""
    p = BUILDS / f'{key}.build_state.json'
    if not p.exists():
        return None
    try:
        meta = (json.loads(p.read_text()).get('meta') or {})
    except Exception:
        return None
    if meta.get('music_start_src') != 'manual':
        return None
    try:
        ms = int(meta.get('music_start_ms') or 0)
    except (TypeError, ValueError):
        return None
    return ms if ms >= 0 else None


def music_start_ms(wav):
    """First time (ms) the FULL-MIX audio has real musical energy — the intro
    card's countdown should start HERE, not at t=0, when a video has a long
    silent pre-roll (踊り子's MV has ~6.6s of silence before the music). RMS in
    100ms windows; onset = first window above 10% of the peak window energy."""
    import numpy as np, soundfile as sf
    y, sr = sf.read(str(wav))
    if y.ndim > 1:
        y = y.mean(axis=1)
    win = int(sr * 0.1) or 1
    n = len(y) // win
    if n <= 0:
        return 0
    rms = np.sqrt(np.array([np.mean(y[i*win:(i+1)*win]**2) for i in range(n)]) + 1e-12)
    thr = 0.10 * float(rms.max())
    above = np.where(rms > thr)[0]
    return int(round(float(above[0]) * win / sr * 1000.0)) if len(above) else 0


def apply_alignment(lyr, per_line):
    """Set each line's begin/end from the alignment; shift that line's words +
    kana by the same per-line delta (preserving accurate intra-line timing)."""
    lines = lyr['lines']
    for i, (nb, ne) in per_line.items():
        L = lines[i]
        delta = nb - L['begin_ms']
        L['begin_ms'], L['end_ms'] = nb, max(ne, nb + 200)
        for w in L.get('words', []):
            w['begin_ms'] = max(0, int(round(w['begin_ms'] + delta)))
            w['end_ms'] = max(0, int(round(w['end_ms'] + delta)))
        for k in L.get('kana_timings', []):
            k['begin_ms'] = max(0, int(round(k['begin_ms'] + delta)))
            k['end_ms'] = max(0, int(round(k['end_ms'] + delta)))
    # monotonic guard: a line begin can't precede the previous line's begin
    prev = -1
    for L in lines:
        if L.get('text', '').strip():
            if L['begin_ms'] < prev:
                L['begin_ms'] = prev
            prev = L['begin_ms']
    return lyr


_GATE_KANA = re.compile(r'[぀-ゟ゠-ヿ]')
_GATE_JP = re.compile(r'[぀-ヿ㐀-鿿]')


def _gate_norm(s):
    """Normalized line text for identity grouping: strip a trailing (×N) repeat
    marker and all whitespace."""
    s = re.sub(r'\s*\(×\d+\)\s*$', '', s or '')
    return re.sub(r'\s+', '', s)


def _gate_morae(text):
    """Approximate morae: each kana counts 1, each kanji counts 2."""
    m = 0
    for ch in text:
        if _GATE_KANA.match(ch):
            m += 1
        elif _GATE_JP.match(ch):
            m += 2
    return m


def _gate_median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def plausibility_gate(lyr, per_line, toks):
    """Hard timing-plausibility gate, run AFTER alignment and BEFORE writing.
    CTC forced alignment smears on runs of identical repeated lines (hooks/
    scat) — it parked 踊り子's とぅるるる repeats over the instrumental break,
    stretching one line to 24s and single tokens to 9s, and that shipped to
    3 of 5 live songs. Two roles:

    CORRECTION (e): for runs of >=3 identical adjacent lines the per-line CTC
    result is NOT trusted — those lines' begin/end are rewritten to
    source + median_delta (median over the non-run lines), i.e. the whole-song
    shift is applied but the source's own per-line spacing is kept. Flagged
    in stdout; per_line is mutated in place.

    CHECKS (a)-(d), each violation reported with line idx / text / expected vs
    got:
      (a) per-line residual outlier: |delta - median_delta| > 3000ms
          (delta = aligned_begin - source_begin);
      (b) identical-text duration consistency: max/min line-duration ratio
          over lines with identical normalized text must be <= 2.5;
      (c) no single aligned word token may span > 4000ms;
      (d) line mora-rate within [1.0, 14] morae/sec (kana=1, kanji=2; lines
          with no Japanese chars are skipped).

    Returns (violations, median_delta); caller refuses to write (and exits
    non-zero) on any violation unless --force."""
    lines = lyr['lines']
    idxs = sorted(per_line)
    norm = {i: _gate_norm(lines[i].get('text', '')) for i in idxs}

    # runs of >=3 identical ADJACENT lines (adjacent in lyric-line order)
    run_idx = set()
    k = 0
    while k < len(idxs):
        j = k
        while j + 1 < len(idxs) and norm[idxs[j + 1]] == norm[idxs[k]] and norm[idxs[k]]:
            j += 1
        if j - k + 1 >= 3:
            run_idx.update(idxs[k:j + 1])
        k = j + 1

    deltas = {i: per_line[i][0] - lines[i]['begin_ms'] for i in idxs}
    med_pool = [deltas[i] for i in idxs if i not in run_idx] or list(deltas.values())
    med = _gate_median(med_pool)

    # (e) identical-run correction: per-line CTC untrusted inside a run.
    for i in sorted(run_idx):
        sb, se = lines[i]['begin_ms'], lines[i]['end_ms']
        nb, ne = int(round(sb + med)), int(round(se + med))
        ob, oe = per_line[i]
        per_line[i] = (nb, ne)
        deltas[i] = med
        print(f'[gate] line {i} {lines[i].get("text", "")[:24]!r}: run of >=3 '
              f'identical lines — per-line CTC untrusted; using source + median '
              f'shift {med:+.0f}ms instead: begin {ob}->{nb}, end {oe}->{ne}')

    violations = []
    # (a) per-line residual outlier
    for i in idxs:
        if abs(deltas[i] - med) > 3000:
            violations.append(
                ('a', f'line {i} {lines[i].get("text", "")[:30]!r}: shift '
                      f'{deltas[i]:+.0f}ms vs median {med:+.0f}ms — expected '
                      f'within ±3000ms of median, got |diff|='
                      f'{abs(deltas[i] - med):.0f}ms'))
    # (b) identical-text duration consistency
    groups = {}
    for i in idxs:
        if norm[i]:
            groups.setdefault(norm[i], []).append(i)
    for txt, gi in groups.items():
        if len(gi) < 2:
            continue
        durs = {i: max(per_line[i][1] - per_line[i][0], 1) for i in gi}
        ratio = max(durs.values()) / min(durs.values())
        if ratio > 2.5:
            detail = ', '.join(f'line {i}={durs[i]}ms' for i in gi)
            violations.append(
                ('b', f'identical text {txt[:20]!r} on lines {gi}: duration '
                      f'ratio {ratio:.2f} — expected <= 2.5 ({detail})'))
    # (c) single-token span
    for t in toks:
        if t.get('begin') is None:
            continue
        span = t['end'] - t['begin']
        if span > 4000:
            violations.append(
                ('c', f'line {t["line"]} token {t["text"]!r}: aligned span '
                      f'{span:.0f}ms — expected <= 4000ms'))
    # (d) mora-rate
    for i in idxs:
        text = lines[i].get('text', '')
        if not _GATE_JP.search(text):
            continue
        dur_s = max(per_line[i][1] - per_line[i][0], 1) / 1000.0
        rate = _gate_morae(text) / dur_s
        if not (1.0 <= rate <= 14):
            violations.append(
                ('d', f'line {i} {text[:30]!r}: mora-rate {rate:.2f}/s over '
                      f'{dur_s * 1000:.0f}ms — expected within [1.0, 14]'))
    return violations, med


def _distribute(lt, lo, hi):
    """Spread a run of tokens across [lo, hi] proportionally to text length
    (mirrors the page's assignTimes weighting) — used for the unaligned tail/
    gaps so every word gets a monotonic slice, none is dropped."""
    tot = sum(max(len(t['text']), 1) for t in lt) or 1
    span = max(hi - lo, 0.0); acc = 0
    for t in lt:
        w = max(len(t['text']), 1)
        t['mb'] = lo + (acc / tot) * span
        acc += w
        t['me'] = lo + (acc / tot) * span


def build_word_timings(lyr, toks, line_idx, conf_min=None):
    """Write Apple-TTML-shaped words[] into each line from the per-token CTC
    alignment. Word times are anchored INTO each line's existing
    [begin_ms, end_ms] via a per-line affine map, so they stay on the same
    (already video-anchored) clock as the line times and never inherit the raw
    CTC global offset/scale. Tokens with no alignment (or below conf_min, if
    set) are interpolated between confident neighbours — never dropped, never
    overlapping, never out of order, always clamped inside the line span.

    Returns a per-line QA stat list."""
    lines = lyr['lines']
    stats = []
    tightened = []          # lines whose end ran on past the singing
    for i in line_idx:
        L = lines[i]
        B = float(L['begin_ms']); E = float(L['end_ms'])
        if E <= B:
            E = B + 200.0
        lt = [t for t in toks if t['line'] == i]
        if not lt:
            L['words'] = []
            continue
        n = len(lt)

        def conf(t):
            if t.get('begin') is None:
                return False
            if conf_min is not None and t.get('cscore') is not None and t['cscore'] < conf_min:
                return False
            return True

        anchors = [k for k in range(n) if conf(lt[k])]
        n_conf = len(anchors)
        if not anchors:
            # nothing aligned in this line -> even weighted distribution.
            _distribute(lt, B, E)
        else:
            cB = lt[anchors[0]]['begin']; cE = lt[anchors[-1]]['end']
            spanraw = cE - cB
            if spanraw <= 0:
                _distribute(lt, B, E)
            else:
                # A line-level sheet (LRC) has no real line ends — each end is
                # just the next line's begin, so the last line before an
                # instrumental break "lasts" through the whole break. Stretching
                # the CTC span to fill that window smeared マリーゴールド's line
                # 35 across 18.6s for 4s of singing (scale 4.6x), which is a
                # reveal crawling through dead air and a line that never lets
                # go. When the singing is far shorter than the window, believe
                # the singing: end the line where the voice stops.
                if (E - B) - spanraw > 1500 and (E - B) > spanraw * 1.6:
                    E = B + spanraw + 350.0        # a tail for the held vowel
                    L['end_ms'] = int(round(E))
                    tightened.append(i)
                # …and record where the voice actually stopped on EVERY line,
                # tightened or not. end_ms is a display window (a line-level
                # sheet chains it to the next line's begin, so it says nothing
                # about silence); sung_end_ms is a measurement. Anything that
                # needs real silence between lines — where a section starts,
                # whether a line is held or hanging — has to read this, not
                # end_ms. Added 2026-07-29: the scaffold's gap-based sectionizer
                # saw a chained sheet as one unbroken block and cut sections
                # wherever a singer happened to breathe.
                L['sung_end_ms'] = int(round(min(E, B + spanraw + 350.0)))
                scale = (E - B) / spanraw
                for k in anchors:
                    lt[k]['mb'] = B + (lt[k]['begin'] - cB) * scale
                    lt[k]['me'] = B + (lt[k]['end'] - cB) * scale
                # interpolate the leading / trailing / interior non-anchor runs
                first, last = anchors[0], anchors[-1]
                _distribute(lt[0:first], B, lt[first]['mb'])
                _distribute(lt[last + 1:n], lt[last]['me'], E)
                for a1, a2 in zip(anchors, anchors[1:]):
                    if a2 - a1 > 1:
                        _distribute(lt[a1 + 1:a2], lt[a1]['me'], lt[a2]['mb'])

        # monotonic, non-overlapping, clamped-to-line integer times.
        prev = B; words = []
        for t in lt:
            b = min(max(t['mb'], prev), E)
            e = min(max(t['me'], b), E)
            words.append({'text': t['text'], 'begin_ms': int(round(b)), 'end_ms': int(round(e))})
            prev = e
        # CONTINUOUS spans: each word's end = the NEXT word's begin (same line),
        # last word's end = the line end. Onsets (begin_ms) are the perceptual
        # sync and are kept EXACTLY as aligned; only the ends are stretched to
        # close the CTC-spike dead-zones. Apple-TTML word[] are continuous (each
        # end ≈ next begin) and the template's two-row wipe was tuned to that
        # contract — the raw CTC spikes leave 1s+ gaps where no token is
        # "singing", which the wipe reads as a freeze/jump-back at the row wrap.
        # begins are already monotonic non-decreasing, so end==next-begin is
        # always ≥ this begin (never inverts the span).
        for k in range(len(words) - 1):
            words[k]['end_ms'] = words[k + 1]['begin_ms']
        if words:
            words[-1]['end_ms'] = int(round(E))
        L['words'] = words
        # scores of the confident tokens for QA (sum-of-logprob; more negative = weaker)
        sc = [t['cscore'] for k, t in enumerate(lt) if conf(t) and t.get('cscore') is not None]
        stats.append({'line': i, 'n': n, 'n_conf': n_conf,
                      'first_begin': words[0]['begin_ms'], 'B': int(B), 'E': int(E),
                      'worst_score': min(sc) if sc else None})
    lyr['has_word_timing'] = True
    if tightened:
        print(f'[words] {len(tightened)} line(s) ended long after the singing '
              f'stopped (a line-level sheet has no real ends) — pulled back to '
              f'the voice: {", ".join(str(i) for i in tightened)}')
    return stats


def regen_mora_timings(key, yt, lyr, lyrics_out):
    """Regenerate <key>.mora_timings.json NEXT TO the lyrics file just written
    — whenever line or word timing changes, the mora file must be rebuilt from
    the new grid (stale-yet-plausible mora timings are the hazard: the kana
    match in content_to_data can't catch times that moved but still parse).
    On ANY failure the old mora file is DELETED so assembly falls back to even
    division rather than consume stale timings."""
    mp = Path(lyrics_out).parent / f'{key}.mora_timings.json'
    try:
        import mora_align
        stem = vocal_stem(yt)
        timings, status = mora_align.align_song(lyr, 'ctc', stem16=stem,
                                                verbose=False)
        mp.write_text(json.dumps({str(k): v for k, v in sorted(timings.items())},
                                 ensure_ascii=False, indent=1))
        n = sum(len(v) for v in timings.values())
        n_fb = sum(1 for s in status.values() if s == 'fallback-even')
        print(f'[morae] regenerated {mp}: {len(timings)} lines, {n} morae'
              + (f' ({n_fb} line(s) fell back to even)' if n_fb else ''))
    except Exception as ex:
        mp.unlink(missing_ok=True)
        print(f'[morae] regeneration FAILED ({ex}) — deleted {mp.name}; '
              f'assembly will use even division. Rerun when fixed:\n'
              f'  python whisper_sync.py {key} --yt {yt} --morae --apply')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('key'); ap.add_argument('--yt', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='write even when the timing-plausibility gate fails '
                         '(gate violations are printed either way)')
    ap.add_argument('--words', action='store_true',
                    help='additive: write per-word timings into lines[].words[] '
                         '(leaves line begin/end unchanged; for LRCLIB songs)')
    ap.add_argument('--morae', action='store_true',
                    help='additive: CTC-align one token per MORA inside each '
                         'line\'s existing anchored window and write '
                         'builds/<key>.mora_timings.json (content_to_data '
                         'consumes it when present — hybrid by default — '
                         'falling back to even division per word on any '
                         'mismatch). Run AFTER line timing is final — the '
                         'line windows are the anchors.')
    ap.add_argument('--no-morae', action='store_true',
                    help='skip the automatic mora_timings regeneration that '
                         'otherwise follows every --apply write (line and '
                         '--words modes) to keep the mora file in step with '
                         'the lyrics grid')
    ap.add_argument('--out', default=None,
                    help='output path (default: builds/<key>.lyrics.json). Use to '
                         'stage the result without overwriting the build file.')
    a = ap.parse_args()

    lyrics_path = BUILDS / f'{a.key}.lyrics.json'
    if not lyrics_path.exists():
        raise SystemExit(
            f'[sync] no timed lyrics yet for "{a.key}" — {lyrics_path} is missing.\n'
            f'Alignment needs the lyric text + rough line times first. Get them with:\n'
            f'  python3 tools/songcraft/fetch_timed_lyrics.py {a.key}\n'
            f'(tries NetEase word-level, then LRCLIB, then Apple if a token is set), '
            f'then re-run this step.')
    lyr = json.loads(lyrics_path.read_text())
    if a.morae:
        if a.words:
            raise SystemExit('--morae and --words are separate passes; run one at a time')
        import mora_align
        stem = vocal_stem(a.yt)
        print(f'[morae] {a.key}: CTC-aligning morae per line inside existing '
              f'line windows...', flush=True)
        timings, status = mora_align.align_song(lyr, 'ctc', stem16=stem)
        n = sum(len(v) for v in timings.values())
        n_ctc = sum(1 for v in timings.values() for e in v if e['src'] == 'ctc')
        n_fb = sum(1 for s in status.values() if s == 'fallback-even')
        outp = Path(a.out) if a.out else (BUILDS / f'{a.key}.mora_timings.json')
        print(f'[morae] {len(timings)} lines, {n} morae ({n_ctc} CTC-timed, '
              f'{n_fb} lines fell back to even).')
        if a.apply:
            outp.write_text(json.dumps({str(k): v for k, v in sorted(timings.items())},
                                       ensure_ascii=False, indent=1))
            print(f'[morae] APPLIED to {outp} — re-run content_to_data + assemble.')
        else:
            print('[morae] dry run (pass --apply to write).')
        return
    begins = [L['begin_ms'] for L in lyr['lines'] if L.get('text', '').strip()]
    print(f'[sync] {a.key}: {len(begins)} lines, first at {begins[0]}ms. '
          f'isolating vocals + forced-aligning...', flush=True)
    per_line, lyr, toks, line_idx = align_lines(a.key, a.yt)
    if not per_line:
        raise SystemExit('no alignable lyric words')
    outp = Path(a.out) if a.out else (BUILDS / f'{a.key}.lyrics.json')

    # Music onset (full-mix): the intro-card countdown starts HERE, not at t=0,
    # so a long silent pre-roll doesn't tick down over dead air. Cached hq wav
    # is the full mix (vocal_stem created it during align; corpus copy wins).
    # A start point set by hand in Denmoku WINS. The auto measurement answers
    # "where does sound first cross a threshold", which is not the same question
    # as "where should this song start" — an MV that fades in under a spoken
    # intro, or a track whose first hit is a soft pad, both measure early. Only
    # a person listening can settle that, so when they have, don't overrule it.
    manual = manual_start_ms(a.key)
    hq = full_mix_path(a.yt)
    if manual is not None:
        lyr.setdefault('song', {})['music_start_ms'] = manual
        auto = f' (auto would have said {music_start_ms(hq)})' if hq is not None else ''
        print(f'[sync] music_start_ms = {manual} — set by hand in the builder{auto}')
    elif hq is not None:
        ms = music_start_ms(hq)
        lyr.setdefault('song', {})['music_start_ms'] = ms
        print(f'[sync] music_start_ms = {ms} (full-mix onset; countdown starts here)')

    if a.words:
        # --words times the words INSIDE each line's existing window, so it is
        # only sound when that window already sits on the video's singing. An MV
        # with a skit intro breaks that silently (ema shipped 20s early,
        # 2026-07-07: 3:53 MV vs 3:27 album grid).
        #
        # The old test for that was "do the track and the video run the same
        # length" — a proxy that answers a different question and got it wrong
        # both ways. マリーゴールド (2026-07-28): the MV is 14s longer than the
        # track because of a long outro, while its line grid sits within a
        # second of the singing all the way through — and the proxy refused the
        # song outright, stopping the whole walk and handing a terminal command
        # to someone standing in front of the app that was supposed to do it.
        # The alignment has already run by this point, so ask the real question
        # instead: how far is each line from where this video actually sings it?
        track_ms = int((lyr.get('song') or {}).get('duration_ms') or 0)
        video_ms = 0
        if hq is not None:
            try:
                import soundfile as _sf
                info = _sf.info(str(hq))
                video_ms = int(info.frames / info.samplerate * 1000)
            except Exception:
                video_ms = 0
        shifts = sorted(nb - lyr['lines'][i]['begin_ms'] for i, (nb, _) in per_line.items())
        med = shifts[len(shifts) // 2]
        onspot = sum(1 for s in shifts if abs(s) <= 2000)
        agree = onspot / len(shifts)
        dur = (f'video {video_ms/1000:.0f}s vs track {track_ms/1000:.0f}s. '
               if video_ms and track_ms else '')
        fit = (f'grid vs sung: median {med:+}ms, {onspot}/{len(shifts)} lines '
               f'within 2s (range {shifts[0]:+} to {shifts[-1]:+}ms)')
        # Trusted: the whole grid sits on the video (median near zero) AND it
        # stays there line by line (a different arrangement scatters even when
        # the median cancels out).
        if abs(med) <= 1500 and agree >= 0.7:
            print(f'[words] {dur}{fit} — the grid is in video time, timing the words in place.')
        else:
            # It is NOT in video time. That is exactly the case the full
            # alignment fixes, and the aligned positions are already in hand —
            # so fix it here rather than asking for a second run by hand.
            print(f'[words] {dur}{fit} — the grid is NOT in video time. '
                  f'Re-aligning the lines first, then timing the words on the fixed grid.')
            violations, gmed = plausibility_gate(lyr, per_line, toks)
            if violations:
                print(f'[gate] TIMING PLAUSIBILITY FAILED — {len(violations)} '
                      f'violation(s) (median shift {gmed:+.0f}ms):')
                for chk, msg in violations:
                    print(f'  ({chk}) {msg}')
                if not a.force:
                    raise SystemExit(
                        '[gate] refusing to write timing. These lines need a person: '
                        'open the Timing tab, fix them by hand, or re-run with --force.')
                print('[gate] --force passed — writing DESPITE gate violations.')
            apply_alignment(lyr, per_line)
        stats = build_word_timings(lyr, toks, line_idx)
        n_lines = len(stats)
        fully = sum(1 for s in stats if s['n_conf'] == s['n'])
        partial = sum(1 for s in stats if 0 < s['n_conf'] < s['n'])
        none = sum(1 for s in stats if s['n_conf'] == 0)
        tot_w = sum(s['n'] for s in stats)
        conf_w = sum(s['n_conf'] for s in stats)
        print(f'[words] {n_lines} lines: {fully} fully-aligned, {partial} partial, '
              f'{none} interpolated-only. words {conf_w}/{tot_w} CTC-confident '
              f'({100*conf_w/max(tot_w,1):.0f}%).')
        # Manual-edit sidecar: build_word_timings just REGENERATED every
        # words[] — re-impose the human decisions before anything is written
        # (and before regen_mora_timings, so overridden word onsets flow into
        # the hybrid mora consumption via this same lyr dict).
        lyr, n_over, over_orph = timing_overrides.apply(lyr, a.key)
        print(f'overrides: {n_over} re-applied, {len(over_orph)} orphaned')
        if a.apply:
            outp.write_text(json.dumps(lyr, ensure_ascii=False, indent=2))
            print(f'[words] APPLIED word timings to {outp} — re-run content_to_data + assemble.')
            if not a.no_morae:
                regen_mora_timings(a.key, a.yt, lyr, outp)
        else:
            print('[words] dry run (pass --apply to write).')
        return

    i0 = min(per_line)
    old0, (new0, _) = lyr['lines'][i0]['begin_ms'], per_line[i0]
    shifts = [nb - lyr['lines'][i]['begin_ms'] for i, (nb, _) in per_line.items()]
    shifts.sort()
    med = shifts[len(shifts)//2]
    print(f'[sync] aligned {len(per_line)}/{len(begins)} lines. '
          f'line 0: {old0}ms -> {new0}ms  (median line shift {med:+}ms, '
          f'range {shifts[0]:+} to {shifts[-1]:+}ms)')

    # Hard timing-plausibility gate (runs BEFORE any write; also corrects
    # identical-line runs in place — see plausibility_gate).
    violations, gmed = plausibility_gate(lyr, per_line, toks)
    if violations:
        print(f'[gate] TIMING PLAUSIBILITY FAILED — {len(violations)} '
              f'violation(s) (median shift {gmed:+.0f}ms):')
        for chk, msg in violations:
            print(f'  ({chk}) {msg}')
        if not a.force:
            raise SystemExit('[gate] refusing to write timing (pass --force to '
                             'override).')
        print('[gate] --force passed — writing DESPITE gate violations.')

    if a.apply:
        apply_alignment(lyr, per_line)
        # Manual-edit sidecar: apply_alignment just rewrote every aligned
        # line's begin/end from CTC — re-impose the human decisions before
        # the write and before the mora regeneration reads this lyr dict.
        lyr, n_over, over_orph = timing_overrides.apply(lyr, a.key)
        print(f'overrides: {n_over} re-applied, {len(over_orph)} orphaned')
        outp.write_text(json.dumps(lyr, ensure_ascii=False, indent=2))
        print(f'[sync] APPLIED to {outp} — re-run content_to_data + assemble.')
        if not a.no_morae:
            regen_mora_timings(a.key, a.yt, lyr, outp)
    else:
        print('[sync] dry run (pass --apply to write).')


if __name__ == '__main__':
    main()
