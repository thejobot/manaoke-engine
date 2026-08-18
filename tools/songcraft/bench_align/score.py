#!/usr/bin/env python3
"""score.py — text-anchored word-onset scoring of an alignment hypothesis
against a NetEase YRC word-level reference.

Matching is LINE-anchored then CHARACTER-anchored. Line-first matters: these
songs repeat identical hook/chorus lines many times, and a flat char-stream
difflib match can pair ref occurrence N with hyp occurrence N-1 — a 20s+
phantom "error" that is a scorer artifact, not aligner behavior. So:

  1. Both sides are reduced to a sequence of normalized line texts (katakana
     folded to hiragana, punctuation/whitespace stripped, NFKC, lowercased)
     and difflib pairs EQUAL lines in order (occurrence-safe).
  2. Inside a paired line the two normalized strings are identical, so words
     pair by character offset: a hyp word (fugashi token / display word)
     matches the ref word (YRC per-character) starting at the same offset.

Onset error = hyp_onset - ref_onset per matched word.

Metrics (per song, per hypothesis):
  n_matched, MAE, MedAE, PCO@0.1/0.2/0.3s — each both RAW and
  OFFSET-CORRECTED (per-song median delta subtracted; the YRC clock is the
  studio track, ours is the YouTube video, so a constant offset is expected
  and the corrected numbers are the honest aligner-quality measure).
Plus a worst-10 table (by |corrected delta|) so failure patterns are visible.
"""
import difflib, json, re, statistics, unicodedata
from pathlib import Path

# credit lines NetEase prepends (fake 1s-per-line timing) — never score them
_CREDIT = re.compile(r'^\s*(作词|作曲|编曲|編曲|词|曲)\s*[:：]')
_KEEP = re.compile(r'[a-z0-9぀-ゟ゠-ヿ㐀-鿿]')


def _norm(s):
    """NFKC, lowercase, katakana->hiragana, keep only letters/digits/JP."""
    s = unicodedata.normalize('NFKC', s or '').lower()
    out = []
    for ch in s:
        o = ord(ch)
        if 0x30A1 <= o <= 0x30F6:          # katakana -> hiragana
            ch = chr(o - 0x60)
        if _KEEP.match(ch):
            out.append(ch)
    return ''.join(out)


def _line_entry(words):
    """[{text, begin_ms}, ...] -> {'norm', 'words': [(off, norm, begin_ms)]}.
    begin_ms may be None (kept for the norm string, excluded from pairing)."""
    norm_parts, starts, pos = [], [], 0
    for w in words:
        n = _norm(w['text'])
        if not n:
            continue
        if w.get('begin_ms') is not None:
            starts.append((pos, n, float(w['begin_ms'])))
        norm_parts.append(n)
        pos += len(n)
    return {'norm': ''.join(norm_parts), 'words': starts}


def ref_lines(ref):
    """LyriCool universal payload -> line entries (word-level lines only)."""
    out = []
    for L in ref['lines']:
        if _CREDIT.match(L.get('text', '')):
            continue
        ws = [w for w in (L.get('words') or []) if w.get('begin_ms') is not None]
        if not ws:
            continue
        e = _line_entry(ws)
        if e['norm']:
            out.append(e)
    return out


def hyp_lines_raw(hyp):
    """dump_align.py raw_tokens -> line entries (unaligned tokens keep their
    place in the line text but are not pairable)."""
    by_line = {}
    for t in hyp['raw_tokens']:
        by_line.setdefault(t['line'], []).append(
            {'text': t['text'], 'begin_ms': t.get('begin_ms')})
    out = []
    for i in sorted(by_line):
        e = _line_entry(by_line[i])
        if e['norm']:
            out.append(e)
    return out


def hyp_lines_shipped(lyr):
    """builds/<key>.lyrics.json (or apple_lyrics) -> line entries."""
    out = []
    for L in lyr['lines']:
        ws = L.get('words') or []
        if not ws:
            continue
        e = _line_entry(ws)
        if e['norm']:
            out.append(e)
    return out


def match(rlines, hlines):
    """Line-then-char anchored pairing. [(ref_word, hyp_word, delta_ms)]."""
    sm = difflib.SequenceMatcher(None, [l['norm'] for l in rlines],
                                 [l['norm'] for l in hlines], autojunk=False)
    pairs = []
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            rl, hl = rlines[i + k], hlines[j + k]
            ref_at = {off: (t, b) for off, t, b in rl['words']}
            for off, ht, hb in hl['words']:
                if off in ref_at:
                    rt, rb = ref_at[off]
                    pairs.append(({'text': rt, 'begin_ms': rb},
                                  {'text': ht, 'begin_ms': hb}, hb - rb))
    return pairs


def _pco(deltas, thr_ms):
    return 100.0 * sum(1 for d in deltas if abs(d) <= thr_ms) / len(deltas)


# ------------------------------------------------------------------ mora leg
# Scores MORA onsets (mora_align.py hypotheses) against the YRC per-character
# reference. Same line-then-offset anchoring as the word leg; the extra work
# is reducing both sides to comparable mora-grained UNITS of the surface text:
#   ref : each normalized YRC char is a unit; small ゃゅょ and ー fold into the
#         previous unit (same folding as content_to_data.moraize), so a folded
#         unit's onset is its FIRST char's onset. A kanji char is one unit —
#         its onset is the onset of the kanji's first mora, interior morae of
#         the kanji reading are not observable in YRC and are excluded.
#   hyp : each aligned mora maps to a surface unit via furigana-style
#         assignment inside its word (kana morae anchor 1:1 forward/backward,
#         a kanji block is scored only at its first mora; unassignable units
#         are dropped, never guessed).
# Units pair by (matched line, char offset in the normalized line text).

_FOLD = set('ゃゅょャュョぁぃぅぇぉゎァィゥェォヮー')   # matches content_to_data SMALL + ー
_KANJI = re.compile(r'[㐀-鿿]')
_LATIN = re.compile(r'[a-z0-9]')


def _surface_units(norm):
    """Normalized text -> [(off, text, type)] mora-grained units.
    type: 'kana' | 'kanji' | 'latin' (a latin/digit run is ONE unit — mora
    hypotheses time latin words as a single token)."""
    units = []
    for k, ch in enumerate(norm):
        if ch in _FOLD and units:
            units[-1][1] += ch
        elif _LATIN.match(ch) and units and units[-1][2] == 'latin' and \
                units[-1][0] + len(units[-1][1]) == k:
            units[-1][1] += ch
        else:
            units.append([k, ch, 'kanji' if _KANJI.match(ch) else
                          ('latin' if _LATIN.match(ch) else 'kana')])
    return [tuple(u) for u in units]


def ref_mora_lines(ref):
    """LyriCool YRC payload -> [{'norm', 'onsets': {off: begin_ms}}]."""
    out = []
    for L in ref['lines']:
        if _CREDIT.match(L.get('text', '')):
            continue
        chars = []                       # (norm_char, onset_ms) per kept char
        for w in (L.get('words') or []):
            if w.get('begin_ms') is None:
                continue
            n = _norm(w['text'])
            for ch in n:                 # YRC is per-char; a rare multi-char
                chars.append((ch, float(w['begin_ms'])))  # token shares onset
        if not chars:
            continue
        norm = ''.join(c for c, _ in chars)
        onsets = {}
        for off, txt, typ in _surface_units(norm):
            onsets[off] = chars[off][1]  # unit onset = its FIRST char's onset
        out.append({'norm': norm, 'onsets': onsets})
    return out


def _assign_morae(units, morae):
    """Furigana-style unit<-mora assignment inside one word.
    units: [(off, text, type)] of the word's normalized surface;
    morae: [{'kana', 'begin_ms', ...}] from mora_align (same moraize logic).
    -> [(off, mora_idx)] for the units we can anchor without guessing."""
    def m_norm(k):
        return _norm(k)
    n_u, n_m = len(units), len(morae)
    has_kanji = any(t == 'kanji' for _, _, t in units)
    if not has_kanji and n_u == n_m:
        return [(units[i][0], i) for i in range(n_u)]
    lo_u = lo_m = 0
    hi_u, hi_m = n_u, n_m
    fwd, bwd = [], []
    while lo_u < hi_u and lo_m < hi_m and units[lo_u][2] != 'kanji' \
            and units[lo_u][1] == m_norm(morae[lo_m]['kana']):
        fwd.append((units[lo_u][0], lo_m)); lo_u += 1; lo_m += 1
    while hi_u > lo_u and hi_m > lo_m and units[hi_u - 1][2] != 'kanji' \
            and units[hi_u - 1][1] == m_norm(morae[hi_m - 1]['kana']):
        bwd.append((units[hi_u - 1][0], hi_m - 1)); hi_u -= 1; hi_m -= 1
    mid_u, mid_m = units[lo_u:hi_u], list(range(lo_m, hi_m))
    mid = []
    if mid_u and mid_m:
        if len(mid_u) == len(mid_m):     # e.g. 部屋/へや — one mora per unit
            mid = [(u[0], mi) for u, mi in zip(mid_u, mid_m)]
        else:                            # kanji block: first mora only
            mid = [(mid_u[0][0], mid_m[0])]
    return fwd + mid + list(reversed(bwd))


def hyp_mora_lines(mora_map):
    """mora_align.py output ({line_idx: [entries]}) -> [{'norm', 'units':
    [(off, onset_ms, meta)]}] in line order. meta = {'kana', 'word_n',
    'mora_idx', 'src'} (word_n = morae in the word — the >=3 subset key)."""
    out = []
    for li in sorted(mora_map, key=lambda x: int(x)):
        entries = mora_map[li]
        words = {}                        # word_i -> [entries]
        for e in entries:
            words.setdefault(e['word_i'], []).append(e)
        norm_parts, units, pos = [], [], 0
        for wi in sorted(words):
            group = words[wi]
            n = _norm(group[0]['word'])
            if not n:
                continue
            w_units = [(pos + off, txt, typ)
                       for off, txt, typ in _surface_units(n)]
            for off, mi in _assign_morae(w_units, group):
                e = group[mi]
                units.append((off, float(e['begin_ms']),
                              {'kana': e['kana'], 'word_n': len(group),
                               'mora_idx': mi, 'src': e.get('src', '')}))
            norm_parts.append(n)
            pos += len(n)
        if norm_parts:
            out.append({'norm': ''.join(norm_parts),
                        'units': sorted(units)})
    return out


def match_morae(rlines, hlines):
    """Line-then-offset anchored mora pairing -> [(delta_ms, meta)].
    meta additionally gains 'ref_hold_ms' — the gap from this ref onset to the
    line's next ref onset (a proxy for how long the note is held; the
    vowel-smear failure mode lives in the big-hold bucket).

    YRC sometimes prints a repeated hook TWICE on one line (死ぬのがいいわ×2)
    where our lyrics keep two lines, so after the 1:1 difflib pass a gap
    post-pass pairs an unmatched ref line against the concatenation of 2-3
    consecutive unmatched hyp lines (order-preserving, exact-norm only)."""
    sm = difflib.SequenceMatcher(None, [l['norm'] for l in rlines],
                                 [l['norm'] for l in hlines], autojunk=False)
    pairs = []

    def pair_line(rl, parts):
        """parts = [(hyp_line, offset_shift)] covering rl's norm."""
        offs = sorted(rl['onsets'])
        nxt = {o: (rl['onsets'][offs[z + 1]] - rl['onsets'][o])
               if z + 1 < len(offs) else None for z, o in enumerate(offs)}
        for hl, shift in parts:
            for off, hyp_ms, meta in hl['units']:
                ref_ms = rl['onsets'].get(off + shift)
                if ref_ms is not None:
                    pairs.append((hyp_ms - ref_ms,
                                  meta | {'ref_hold_ms': nxt[off + shift]}))

    blocks = sm.get_matching_blocks()
    for i, j, n in blocks:
        for k in range(n):
            pair_line(rlines[i + k], [(hlines[j + k], 0)])

    prev_r = prev_h = 0                          # merged-line gap post-pass
    for i, j, n in blocks:
        hj = prev_h
        for ri in range(prev_r, i):
            rnorm = rlines[ri]['norm']
            for s in range(hj, j):
                acc, take = '', []
                for t in range(s, min(s + 3, j)):
                    acc += hlines[t]['norm']
                    take.append(t)
                    if acc == rnorm and len(take) >= 2:
                        parts, shift = [], 0
                        for tt in take:
                            parts.append((hlines[tt], shift))
                            shift += len(hlines[tt]['norm'])
                        pair_line(rlines[ri], parts)
                        hj = take[-1] + 1
                        break
                    if len(acc) >= len(rnorm):
                        break
                else:
                    continue
                break
        prev_r, prev_h = i + n, j + n
    return pairs


def mora_metrics(pairs):
    """-> {'n', 'median_offset_ms', 'overall': m, 'w3': m, 'w3_interior': m}
    where m = {n, medae, pco100, pco150, pco200} on OFFSET-CORRECTED deltas
    (per-song median over ALL matched morae — one clock correction per song).
    w3 = morae in words with >=3 morae; w3_interior = those at mora_idx >= 1
    (word-initial onsets are near-identical under any division scheme)."""
    if not pairs:
        return None
    med = statistics.median(d for d, _ in pairs)

    def mset(ds):
        if not ds:
            return None
        return {'n': len(ds),
                'medae': statistics.median(abs(d) for d in ds),
                'mae': statistics.mean(abs(d) for d in ds),
                'pco100': _pco(ds, 100), 'pco150': _pco(ds, 150),
                'pco200': _pco(ds, 200)}

    corr = [(d - med, m) for d, m in pairs]
    holds = {}                       # note-hold buckets: the vowel-smear probe
    for label, lo, hi in (('<250ms', 0, 250), ('250-500ms', 250, 500),
                          ('500-1000ms', 500, 1000), ('>=1s', 1000, 1 << 30)):
        holds[label] = mset([d for d, m in corr
                             if m.get('ref_hold_ms') is not None
                             and lo <= m['ref_hold_ms'] < hi])
    return {
        'n': len(pairs), 'median_offset_ms': med,
        'overall': mset([d for d, _ in corr]),
        'w3': mset([d for d, m in corr if m['word_n'] >= 3]),
        'w3_interior': mset([d for d, m in corr
                             if m['word_n'] >= 3 and m['mora_idx'] >= 1]),
        'holds': holds,
        'worst10': sorted(corr, key=lambda x: -abs(x[0]))[:10],
    }


def metrics(pairs):
    """-> dict with raw + offset-corrected metric sets, worst-10 rows."""
    if not pairs:
        return None
    deltas = [d for _, _, d in pairs]
    med = statistics.median(deltas)
    corr = [d - med for d in deltas]

    def mset(ds):
        return {'mae': statistics.mean(abs(d) for d in ds),
                'medae': statistics.median(abs(d) for d in ds),
                'pco100': _pco(ds, 100), 'pco200': _pco(ds, 200),
                'pco300': _pco(ds, 300)}

    worst = sorted(zip(pairs, corr), key=lambda x: -abs(x[1]))[:10]
    return {
        'n_matched': len(pairs),
        'median_offset_ms': med,
        'raw': mset(deltas),
        'corrected': mset(corr),
        'worst10': [{'text': p[1]['text'], 'ref_ms': p[0]['begin_ms'],
                     'hyp_ms': p[1]['begin_ms'], 'delta_corr_ms': c}
                    for p, c in worst],
    }


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('ref'); ap.add_argument('hyp')
    ap.add_argument('--shipped', action='store_true',
                    help='hyp is a lyrics.json (score its lines[].words[])')
    a = ap.parse_args()
    ref = json.loads(Path(a.ref).read_text())
    hyp = json.loads(Path(a.hyp).read_text())
    hl = (hyp_lines_shipped(hyp) if a.shipped else hyp_lines_raw(hyp))
    print(json.dumps(metrics(match(ref_lines(ref), hl)),
                     ensure_ascii=False, indent=1))
