#!/usr/bin/env python3
"""alignment_scorecard — how close is this song's timing to 100%?

the owner's standing order (2026-07-07, after ema shipped 20s early and even the
flagship's mora fills read slightly off): mora-level alignment gets checked
CONSTANTLY, not once. We always make OUR OWN timing, then benchmark it
against every external reference we can reach (offset-corrected — a constant
video-vs-track offset is expected and reported, residuals are the quality),
plus checks no external DB can give us: does the audio itself contain each
line where we claim (listen gate), and do the mora fills spell the actual
reading. Every run appends to builds/alignment_history.jsonl so convergence
toward 100 is visible build over build.

Four legs, one score:
  external  offset-corrected line/word onset agreement vs NetEase YRC/LRC,
            LRCLIB, bench_align/refs caches. Word-level PCO@0.1/0.2/0.3s when
            the ref has words. Refs that share provenance with our own grid
            are scored but flagged NOT-INDEPENDENT (a 0ms match against your
            own source is a smell, not a pass — that exact circularity shipped
            the ema bug).
  acoustic  verify_sync listen gate: sampled line windows whisper-checked to
            contain their line. The only leg that catches a global offset
            when no independent ref exists.
  mora      assembled kana_timings vs the fugashi/unidic context reading of
            each line: mora-count drift + doubled-mora artifacts (the
            けがれれた class), monotonicity.
  readings  every study card's authored reading (jp_speak, or its romaji for
            kanji-surface cards — the furigana/display truth) vs the fugashi
            context reading of its line, hosted only on token-boundary-aligned
            occurrences (時 inside 時計 is never arbitrated by 時計). A flag
            means ADJUDICATE BY EAR — song slang/dialect can beat the
            dictionary (方=ほう per utaten furigana; 被害者面=づら), and
            sometimes the card is simply wrong (きずいた→きづいた).

The ENGLISH layer (line_tr / line_explain / word en fields) is NOT machine-
scorable — it gets an independent fresh-eyes review pass at author time
(IMPROVEMENT-LOOP.md) and a BYOM automated review later (backlog).

Usage (parler env for acoustic+mora legs):
  conda run -n parler python tools/songcraft/alignment_scorecard.py <key>
      [--slug songs/<dir>] [--skip-acoustic] [--json]
"""
import argparse
import difflib
import json
import re
import statistics
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BUILDS = HERE / 'builds'
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'bench_align'))

import score as bench            # noqa: E402  (bench_align/score.py)
import timing_overrides          # noqa: E402  (manual-edit sidecar)
from lyric_sources import netease, lrclib  # noqa: E402


def _norm(s):
    return bench._norm(s)


def override_pins(lyr, key):
    """Resolve the manual-edit sidecar against this lyric grid.
    Returns (line_pins set, {line_idx: set(word_idx)}, n_orphans). Hand-pinned
    onsets are HUMAN timing, not aligner output — scoring them as aligner
    quality is the same circularity the NOT-INDEPENDENT ref flag guards."""
    entries = timing_overrides.load(key).get('entries') or []
    resolved, orphans = timing_overrides.resolve(lyr.get('lines') or [], entries)
    line_pins, word_pins = set(), {}
    for li, e in resolved:
        if e.get('word_idx') is None:
            line_pins.add(li)
        else:
            word_pins.setdefault(li, set()).add(int(e['word_idx']))
    return line_pins, word_pins, len(orphans)


# ---------------------------------------------------------------- external --
def _ref_from_lines(lines):
    """[{text,begin_ms,words?}] -> bench ref_lines-compatible entries."""
    out = []
    for ln in lines:
        words = ln.get('words') or [{'text': ln['text'], 'begin_ms': ln['begin_ms']}]
        out.append(bench._line_entry(
            [{'text': w['text'], 'begin_ms': w.get('begin_ms')} for w in words]))
    return [l for l in out if l['norm']]


def fetch_refs(key, title, artist, duration_ms):
    """Every reachable external reference: (name, lines, word_level, note)."""
    refs = []
    cache = HERE / 'bench_align' / 'refs' / f'{key}.json'
    if cache.exists():
        try:
            r = json.loads(cache.read_text())
            lines = [l for l in r.get('lines') or []
                     if not re.match(r'^\s*(作词|作曲|编曲|編曲)\s*[:：]', l.get('text') or '')]
            wl = any(len(l.get('words') or []) > 1 for l in lines)
            refs.append(('netease_cache', lines, wl, 'bench_align/refs cache'))
        except Exception:
            pass
    if not refs:      # live NetEase
        try:
            cands = netease.search(f'{title} {artist}', limit=6)
            best = next((c for c in cands if _norm(c['name']) == _norm(title)
                         and (not duration_ms or abs((c.get('duration_ms') or 0) - duration_ms) < 4000)), None)
            if best:
                v = netease.fetch_lyrics(best['id'])
                lines = netease.parse_yrc(v.get('yrc'))
                wl = bool(lines)
                if not lines:
                    lines = netease.parse_lrc(v.get('lrc'))
                lines = [l for l in lines
                         if not re.match(r'^\s*(作词|作曲|编曲|編曲)\s*[:：]', l.get('text') or '')]
                if lines:
                    refs.append(('netease_yrc' if wl else 'netease_lrc', lines, wl,
                                 f"id {best['id']} ({(best.get('duration_ms') or 0) / 1000:.0f}s)"))
        except Exception as e:
            refs.append(('netease', [], False, f'fetch failed: {e}'))
    try:
        rec = lrclib.get_exact(title, artist,
                               duration_sec=round(duration_ms / 1000) if duration_ms else None)
        if rec and rec.get('syncedLyrics'):
            lines = lrclib.parse_lrc(rec['syncedLyrics'])
            if lines:
                refs.append(('lrclib', lines, False, f"id {rec.get('id')}"))
    except Exception:
        pass
    return refs


def score_external(lyr, key, pins=None):
    song = lyr.get('song') or {}
    ours_src = (lyr.get('source') or '')
    # Mask hand-pinned onsets out of the hypothesis: a word with a sidecar
    # override — or any word on a line whose window was hand-set — carries
    # HUMAN timing, so it must not inflate the aligner-quality aggregates.
    # begin_ms=None keeps the word in the normalized line text (char offsets
    # stay valid for pairing) while excluding it from onset scoring.
    line_pins, word_pins = (pins or (set(), {}))[:2]
    n_excluded = 0
    masked = []
    for li, L in enumerate(lyr.get('lines') or []):
        ws = L.get('words') or []
        if ws and (li in line_pins or li in word_pins):
            wp = word_pins.get(li, set())
            ws2 = []
            for wi, w in enumerate(ws):
                drop = li in line_pins or wi in wp
                n_excluded += 1 if (drop and w.get('begin_ms') is not None) else 0
                ws2.append({'text': w.get('text', ''),
                            'begin_ms': None if drop else w.get('begin_ms')})
            masked.append({**L, 'words': ws2})
        else:
            masked.append(L)
    hyp = bench.hyp_lines_shipped({'lines': masked})
    out = []
    for name, lines, word_level, note in fetch_refs(
            key, song.get('name') or key, song.get('artist') or '',
            song.get('duration_ms') or 0):
        if not lines:
            out.append({'ref': name, 'note': note, 'n_matched': 0})
            continue
        m = bench.metrics(bench.match(_ref_from_lines(lines), hyp))
        independent = not (name.startswith('netease') and ours_src.startswith('netease')
                           or name == 'lrclib' and ours_src.startswith('lrclib'))
        entry = {'ref': name, 'note': note, 'word_level': word_level,
                 'independent': independent, **(m or {'n_matched': 0})}
        if n_excluded:
            entry['excluded_overridden_onsets'] = n_excluded
        out.append(entry)
    return out


# ---------------------------------------------------------------- acoustic --
def score_acoustic(key):
    r = subprocess.run([sys.executable, str(HERE / 'verify_sync.py'), key],
                       capture_output=True, text=True, timeout=1800)
    m = re.search(r'SYNC LISTEN GATE: (\d+)/(\d+)', r.stdout)
    hits = re.findall(r'^\s*(\d+)\s+(PASS|FAIL)\s+(\d+)%', r.stdout, re.M)
    if not m:
        return {'error': (r.stdout + r.stderr)[-300:]}
    ok, n = int(m.group(1)), int(m.group(2))
    if 'do not contain' in r.stdout:      # ✗ line reports the FAIL count
        ok = n - ok
    return {'windows': n, 'passed': ok, 'pass_rate': round(100 * ok / max(n, 1)),
            'failed_lines': [int(i) for i, verdict, _ in hits if verdict == 'FAIL']}


# -------------------------------------------------------------------- mora --
_TAGGER = []


def _fugashi_reading(text):
    """Context-aware kana reading of a line via fugashi/unidic — the best
    local arbiter we have (reads 休みの日 as やすみのひ where the kanji
    table said きゅうみのび, 汚れた as よごれた, 瑠璃色 as るりいろ)."""
    from fugashi import Tagger
    import jaconv
    if not _TAGGER:
        _TAGGER.append(Tagger())
    # No tokenizer reads ARABIC digits, so 2人 came back にん and this scorecard
    # flagged the correct authored ふたり as wrong, four times over, on every
    # run (mariigoorudo, 2026-07-29). scaffold.numeral_reading rewrites the
    # digits as the kanji numeral the dictionary knows (二人 → ふたり, 十日 →
    # とおか) — same helper, so the draft and the check can't disagree.
    if re.search(r'\d', text):
        try:
            import scaffold
            r = scaffold.numeral_reading(_TAGGER[0], text)
            if r:
                return r
        except Exception:
            pass
    kana = ''.join((w.feature.kana or w.surface) for w in _TAGGER[0](text))
    return jaconv.kata2hira(kana)


def score_mora(slug_dir):
    try:
        import jaconv
        from fugashi import Tagger  # noqa: F401
    except ImportError:
        return {'error': 'run in the parler env (fugashi)'}
    data = json.loads((ROOT / slug_dir / 'data.json').read_text())
    flags = []
    n_lines = 0
    for i, ln in enumerate((data.get('apple_lyrics') or {}).get('lines') or []):
        kts = ln.get('kana_timings') or []
        if not kts:
            continue
        if not re.search(r'[぀-ヿ㐀-鿿]', ln.get('text') or ''):
            continue      # pure-EN line (shinunoga/headlong) — no JP reading to check
        n_lines += 1
        ours = jaconv.kata2hira(''.join(k['kana'] for k in kts))
        ref = _fugashi_reading(ln['text'])
        ours_f = re.sub(r'[^ぁ-ゖー]', '', ours)
        ref_f = re.sub(r'[^ぁ-ゖー]', '', ref)
        ratio = difflib.SequenceMatcher(None, ours_f, ref_f).ratio()
        doubled = [m.group(0) for m in re.finditer(r'(.)\1', ours_f)
                   if m.group(0) not in ref_f and m.group(1) not in 'ーっん']
        mono = all(kts[j]['begin_ms'] <= kts[j + 1]['begin_ms'] for j in range(len(kts) - 1))
        if ratio < 0.75 or doubled or not mono:
            flags.append({'line': i, 'text': ln['text'][:24], 'similarity': round(ratio, 2),
                          'doubled_morae': doubled, 'monotonic': mono,
                          'ours': ours_f[:30], 'reading': ref_f[:30]})
    return {'lines_checked': n_lines, 'flagged': len(flags), 'flags': flags[:12]}


# ---------------------------------------------------------------- readings --
# は/へ/を are WRITTEN one way and SPOKEN another; authored jp_speak carries
# the spoken form (canon §2.4), fugashi reports the written kana. Equivalent.
_PARTICLE_EQ = {'は': 'わ', 'へ': 'え', 'を': 'お'}


def score_readings(key):
    """Every study word's authored reading (jp_speak → what the JP voice says
    AND what the romaji/furigana display derives from) checked against the
    fugashi context reading of its lyric line. the owner's 'the pronunciations are
    off' dimension — a flag = the card teaches a reading the dictionary
    disagrees with in this context; adjudicate by ear (song slang/dialect can
    legitimately beat the dictionary)."""
    try:
        import jaconv
    except ImportError:
        return {'error': 'run in the parler env (fugashi)'}
    content = json.loads((BUILDS / f'{key}.content.json').read_text())
    lines = [l['jp'] for l in content.get('lines') or []]
    # Ear-adjudicated readings (retention sprint 2026-07-12): the same waiver
    # file E21 honours. Without this, every adjudicated-correct reading
    # (かあさん, あした-in-Kansai-register) re-flags on every run forever and
    # the ear queue drowns in already-answered questions.
    waived = set()
    try:
        for e in json.loads((BUILDS / f'{key}.reading_waivers.json').read_text()):
            if e.get('jp'):
                waived.add(e['jp'])
    except Exception:
        pass
    flags = []
    n = 0
    # words may be nested in sections[] OR a flat top-level array with a
    # `section` ref (content_to_data accepts both; so do we)
    flat = {}
    for w in content.get('words') or []:
        flat.setdefault(w.get('section') or '', []).append(w)
    for sec in content.get('sections') or []:
        for w in (sec.get('words') or flat.get(sec['id'], [])):
            jp = w.get('jp') or ''
            if not re.search(r'[㐀-鿿]', jp):
                continue                      # kana/Latin surface = reading is itself
            if jp in waived:
                continue                      # ear-adjudicated (reading_waivers.json)
            # the authored reading: jp_speak when it's kana; else the card's
            # romaji (the furigana/display truth) converted back to kana
            speak = w.get('jp_speak') or jp
            if not re.search(r'[㐀-鿿]', speak):
                spoken = jaconv.kata2hira(speak)
            else:
                rom = re.sub(r'[\s\-·]', '', (w.get('rom') or '').lower())   # keep ' — it disambiguates n (hon'i)
                for macron, plain in (('ā', 'aa'), ('ī', 'ii'), ('ū', 'uu'), ('ē', 'ee'), ('ō', 'ou')):
                    rom = rom.replace(macron, plain)   # keep vowel LENGTH (ō≈ou)
                spoken = jaconv.kata2hira(jaconv.alphabet2kana(rom))
            hosts = [re.sub(r'\s+', '', l) for l in (w.get('only_lines') or lines) if jp in l]
            if not hosts:
                continue
            from fugashi import Tagger
            if not _TAGGER:
                _TAGGER.append(Tagger())
            # find an occurrence whose span sits ON fugashi token boundaries —
            # 時 found inside 時計 must not be arbitrated by 時計's reading
            ref = None
            for host in hosts:
                toks, pos = [], 0
                for tok in _TAGGER[0](host):
                    toks.append((pos, pos + len(tok.surface), tok))
                    pos += len(tok.surface)
                for m0 in re.finditer(re.escape(jp), host):
                    off, end = m0.start(), m0.end()
                    span = [t for a, b, t in toks if a >= off and b <= end]
                    if span and span[0] is not None and \
                       sum(len(t.surface) for t in span) == len(jp) and \
                       any(a == off for a, b, t in toks) and any(b == end for a, b, t in toks):
                        ref = ''.join(jaconv.kata2hira(t.feature.kana or t.surface) for t in span)
                        break
                if ref:
                    break
            # a card written with digits: the token span reads only the counter
            # (2人 → にん) and flags the correct ふたり. Arbitrate with the kanji
            # numeral form the dictionary knows (see _fugashi_reading).
            if re.search(r'\d', jp):
                try:
                    import scaffold
                    num = scaffold.numeral_reading(_TAGGER[0], jp)
                    if num:
                        ref = num
                except Exception:
                    pass
            if ref is None:
                continue      # never occurs on token boundaries — can't arbitrate
            n += 1
            host = hosts[0]
            ref = _PARTICLE_EQ.get(ref, ref)
            spoken_f = re.sub(r'[^ぁ-ゖー]', '', spoken)
            ref_f = re.sub(r'[^ぁ-ゖー]', '', ref)
            # Sound-fold づ/ぢ → ず/じ on BOTH sides: the rom round-trip spells
            # kizuita きずいた while the derivation keeps orthographic きづいた —
            # identical sounds, eternal false flag (the 気付いた artifact class).
            _zf = lambda s: s.replace('づ', 'ず').replace('ぢ', 'じ')
            spoken_f, ref_f = _zf(spoken_f), _zf(ref_f)
            # romaji 'nn+vowel' is ambiguous (dennou = でんのう, jaconv reads でんおう):
            # accept the alternative n'n parse too before flagging
            alts = {spoken_f}
            if 'nn' in (w.get('rom') or ''):
                r2 = re.sub(r"[\s\-·]", '', w['rom'].lower()).replace('nn', "n'n")
                alts.add(re.sub(r'[^ぁ-ゖー]', '', jaconv.kata2hira(jaconv.alphabet2kana(r2))))
            if spoken_f and ref_f and ref_f not in alts \
                    and all(difflib.SequenceMatcher(None, a, ref_f).ratio() < 0.8 for a in alts):
                flags.append({'section': sec['id'], 'jp': jp,
                              'authored': spoken_f, 'dictionary': ref_f,
                              'line': host[:22]})
    return {'words_checked': n, 'flagged': len(flags), 'flags': flags[:15]}


# -------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('key')
    ap.add_argument('--slug', default='', help='songs/<dir> for the mora leg (default: build_state slug)')
    ap.add_argument('--skip-acoustic', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    lyr = json.loads((BUILDS / f'{a.key}.lyrics.json').read_text())
    st = json.loads((BUILDS / f'{a.key}.build_state.json').read_text())
    slug = a.slug or f"songs/{st['slug']}"

    line_pins, word_pins, n_ov_orphans = override_pins(lyr, a.key)
    n_word_pins = sum(len(s) for s in word_pins.values())

    card = {'key': a.key, 'slug': slug, 'ts': int(time.time()),
            'our_source': lyr.get('source', ''),
            'overrides': {'line_pins': sorted(line_pins),
                          'word_pins': {str(li): sorted(s)
                                        for li, s in sorted(word_pins.items())},
                          'n_line_pins': len(line_pins),
                          'n_word_pins': n_word_pins,
                          'orphaned': n_ov_orphans},
            'external': score_external(lyr, a.key, pins=(line_pins, word_pins)),
            'acoustic': (None if a.skip_acoustic else score_acoustic(a.key)),
            'mora': score_mora(slug),
            'readings': score_readings(a.key)}

    # annotate the mora leg: flags on hand-pinned lines are human timing, not
    # aligner drift — reported separately, never mixed into aligner quality.
    mo = card['mora']
    if isinstance(mo, dict) and 'error' not in mo:
        mo['overridden_line_pins'] = len(line_pins)
        mo['overridden_word_pins'] = n_word_pins
        for fl in mo.get('flags') or []:
            fl['overridden'] = fl['line'] in line_pins or fl['line'] in word_pins

    # headline: the single how-close-are-we number set
    best_word = next((e for e in card['external']
                      if e.get('word_level') and e.get('independent') and e.get('n_matched')), None)
    ac = card['acoustic'] or {}
    card['headline'] = {
        'word_pco300_vs_independent_ref': (best_word or {}).get('corrected', {}).get('pco300'),
        'word_medae_ms': (best_word or {}).get('corrected', {}).get('medae'),
        'acoustic_pass_rate': ac.get('pass_rate'),
        'mora_flagged_lines': (card['mora'] or {}).get('flagged'),
        'reading_flagged_words': (card['readings'] or {}).get('flagged'),
    }

    (BUILDS / f'{a.key}.alignment_score.json').write_text(
        json.dumps(card, ensure_ascii=False, indent=1) + '\n')
    with open(BUILDS / 'alignment_history.jsonl', 'a') as f:
        f.write(json.dumps({'ts': card['ts'], 'key': a.key, **card['headline']},
                           ensure_ascii=False) + '\n')

    if a.json:
        print(json.dumps(card, ensure_ascii=False, indent=1))
        return
    h = card['headline']
    print(f"\n═ alignment scorecard: {a.key} ({slug}) — our source: {card['our_source']}")
    ov = card['overrides']
    if ov['n_line_pins'] or ov['n_word_pins'] or ov['orphaned']:
        print(f"  overrides: {ov['n_line_pins']} line pin(s), "
              f"{ov['n_word_pins']} word pin(s) — hand-pinned onsets EXCLUDED "
              f"from external word aggregates"
              + (f"; ⚠ {ov['orphaned']} orphaned" if ov['orphaned'] else ''))
    for e in card['external']:
        if not e.get('n_matched'):
            print(f"  ext {e['ref']:14} no match ({e.get('note', '')})")
            continue
        c = e['corrected']
        tag = '' if e.get('independent') else '  ⚠ NOT INDEPENDENT of our grid'
        lvl = 'word' if e.get('word_level') else 'line'
        print(f"  ext {e['ref']:14} {lvl}-level  n={e['n_matched']:3}  offset {e['median_offset_ms'] / 1000:+.2f}s  "
              f"MedAE {c['medae']:.0f}ms  PCO@0.3 {c['pco300']:.0f}%{tag}")
    if ac:
        print(f"  acoustic listen gate: {ac.get('passed', '?')}/{ac.get('windows', '?')} windows "
              f"({ac.get('pass_rate', '?')}%)  fails: {ac.get('failed_lines', [])}")
    mo = card['mora'] or {}
    print(f"  mora: {mo.get('flagged', '?')}/{mo.get('lines_checked', '?')} lines flagged")
    for fl in (mo.get('flags') or [])[:5]:
        print(f"    line {fl['line']:2} sim {fl['similarity']}  doubled {fl['doubled_morae']}  {fl['text']}")
    rd = card['readings'] or {}
    print(f"  readings: {rd.get('flagged', '?')}/{rd.get('words_checked', '?')} word cards flagged")
    for fl in (rd.get('flags') or [])[:6]:
        print(f"    [{fl['section']}] {fl['jp']}  authored {fl['authored']} vs dict {fl['dictionary']}  ({fl['line']})")
    print(f"  → headline: word PCO@0.3 {h['word_pco300_vs_independent_ref']}%  "
          f"acoustic {h['acoustic_pass_rate']}%  mora-flags {h['mora_flagged_lines']}  reading-flags {h['reading_flagged_words']}")


if __name__ == '__main__':
    main()
