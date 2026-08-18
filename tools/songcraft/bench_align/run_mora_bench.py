#!/usr/bin/env python3
"""run_mora_bench.py — Prototype A benchmark: mora-token CTC alignment vs the
shipped even-division baseline (and a phoneme-weighted zero-model control),
scored against NetEase YRC per-character onsets.

Per song (shinunoga, inochi-mijikashi — the two with YRC word-level refs AND
cached demucs stems):
  1. stage builds lyrics (never touching builds/), run mora_align.py under the
     parler python in each of its three modes -> out/<key>.mora_<mode>.json
  2. reduce the YRC ref to mora-unit onsets (score.ref_mora_lines) and score
     each mode (score.hyp_mora_lines / match_morae / mora_metrics) —
     offset-corrected MedAE + PCO@0.1/0.15/0.2s, overall + >=3-mora-word
     subsets.
Writes results/MORA-RESULTS.md. Run with plain python3:
  python3 run_mora_bench.py [--refresh] [--songs shinunoga,inochi-mijikashi]
"""
import argparse, json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'out'
sys.path.insert(0, str(HERE))
import score as scorer            # noqa: E402
import run_bench                  # noqa: E402  (SONGS / load_lyrics / PARLER)

MODES = ('even', 'weighted', 'ctc', 'hybrid')   # hybrid = shipped default
STEM = lambda yt: Path(f'/tmp/demucs/htdemucs/hq_{yt}/vocals.wav')


def gen_hyps(song, refresh=False):
    key, yt = song['key'], song['yt']
    stage = HERE / f'.stage_mora_{key}.lyrics.json'
    stage.write_text(json.dumps(run_bench.load_lyrics(song), ensure_ascii=False))
    hyps = {}
    try:
        for mode in MODES:
            outp = OUT / f'{key}.mora_{mode}.json'
            if not outp.exists() or refresh:
                cmd = [run_bench.PARLER, str(HERE.parent / 'mora_align.py'), key,
                       '--lyrics', str(stage), '--out', str(outp), '--mode', mode]
                if mode == 'hybrid':
                    # derive from the raw-CTC hyp (generated earlier in MODES
                    # order) with the SAME transform content_to_data ships.
                    cmd += ['--from-ctc', str(OUT / f'{key}.mora_ctc.json')]
                elif mode == 'ctc':
                    stem = STEM(yt)
                    if not stem.exists():
                        raise SystemExit(f'{key}: no cached stem at {stem} — '
                                         f'regenerate via whisper_sync.vocal_stem')
                    cmd += ['--stem', str(stem)]
                print(f'[bench] {key}: generating {mode} hyp...', flush=True)
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode != 0:
                    raise SystemExit(f'{key} {mode} FAILED:\n{r.stderr[-2000:]}')
                print(r.stdout.strip().splitlines()[-1])
            hyps[mode] = json.loads(outp.read_text())
    finally:
        stage.unlink(missing_ok=True)
    return hyps


def mrow(name, m, sub):
    s = (m or {}).get(sub) if m else None
    if not s:
        return f'| {name} | — | — | — | — | — |'
    return (f"| {name} | {s['n']} | {s['medae']:.0f} | {s['pco100']:.1f}% "
            f"| {s['pco150']:.1f}% | {s['pco200']:.1f}% |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--songs', default='shinunoga,inochi-mijikashi')
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    keys = a.songs.split(',')
    songs = [s for s in run_bench.SONGS if s['key'] in keys]

    L = ['# Mora-timing benchmark — CTC-mora vs even-division vs '
         'phoneme-weighted division', '',
         '- REF = NetEase YRC per-character onsets reduced to mora units '
         '(small ゃゅょ/ー folded like moraize(); kanji = first mora only).',
         '- All hyps share the SAME mora segmentation (content_to_data '
         'moraize) and the SAME line/word windows from builds lyrics; only '
         'the intra-word (ctc: intra-line) mora placement differs.',
         '- Offset-corrected: per-song median delta over all matched morae '
         'subtracted (studio-vs-video clock).',
         '- w>=3 = morae inside words of >=3 morae; interior additionally '
         'drops each word\'s first mora (word onsets are shared by '
         'construction between even/weighted; the interior morae are where '
         'the schemes actually differ).', '']

    for song in songs:
        key = song['key']
        ref = json.loads((HERE / 'refs' / f'{key}.json').read_text())
        rl = scorer.ref_mora_lines(ref)
        n_units = sum(len(l['onsets']) for l in rl)
        hyps = gen_hyps(song, a.refresh)
        L += [f"## {key} — {song['title']} / {song['artist']}", '',
              f'- ref: {len(rl)} YRC lines, {n_units} mora-unit onsets.']
        n_fb = sum(1 for v in hyps['ctc'].values() for e in v
                   if e.get('src') == 'fallback-even')
        n_ctc = sum(1 for v in hyps['ctc'].values() for e in v
                    if e.get('src') == 'ctc')
        L.append(f'- ctc hyp: {n_ctc} CTC-timed morae, {n_fb} fallback-even.')
        L.append('')
        results = {}
        for mode in MODES:
            hl = scorer.hyp_mora_lines(hyps[mode])
            results[mode] = scorer.mora_metrics(scorer.match_morae(rl, hl))
        for sub, label in (('overall', 'ALL matched morae'),
                           ('w3', 'words with >=3 morae'),
                           ('w3_interior', 'interior morae of >=3-mora words')):
            L += [f'### {key}: {label} (offset-corrected)', '',
                  '| hypothesis | n | MedAE ms | PCO@0.1s | PCO@0.15s | PCO@0.2s |',
                  '|---|---|---|---|---|---|']
            for mode in MODES:
                L.append(mrow(mode, results[mode], sub))
            L.append('')
        L += [f'### {key}: by ref note-hold (onset-to-next-onset) — the '
              'vowel-smear probe (offset-corrected MedAE ms / n)', '',
              '| hypothesis | <250ms | 250-500ms | 500-1000ms | >=1s |',
              '|---|---|---|---|---|']
        for mode in MODES:
            hs = (results[mode] or {}).get('holds') or {}
            cells = []
            for b in ('<250ms', '250-500ms', '500-1000ms', '>=1s'):
                s = hs.get(b)
                cells.append(f"{s['medae']:.0f} /{s['n']}" if s else '—')
            L.append(f"| {mode} | " + ' | '.join(cells) + ' |')
        L.append('')
        m = results['ctc']
        if m:
            L += [f'### {key}: worst 10 CTC morae (corrected)', '',
                  '| kana | word_n | mora_idx | src | delta ms |', '|---|---|---|---|---|']
            for d, meta in m['worst10']:
                L.append(f"| {meta['kana']} | {meta['word_n']} | "
                         f"{meta['mora_idx']} | {meta['src']} | {d:+.0f} |")
            L.append('')

    L += ['## Verdict', '', VERDICT_PROSE.strip(), '']
    out = HERE / 'results' / 'MORA-RESULTS.md'
    out.write_text('\n'.join(L) + '\n')
    print('\n'.join(l for l in L if l.startswith('|') or l.startswith('#')
                    or l.startswith('- ')))
    print(f'\nwrote {out}')


# Interpretive paragraph — updated by hand after each benchmark run so the
# prose matches the numbers above (the tables are the machine truth).
VERDICT_PROSE = """
CTC-mora (per-line windowed, one romaji token per mora) beats the shipped
even-division baseline MATERIALLY on both scored songs (2026-07-07 run):
MedAE roughly halves-to-thirds everywhere (shinunoga >=3-mora interior 61ms
-> 15ms; inochi 93ms -> 44ms) and PCO@0.1 jumps ~11-35 points in the
subsets where schemes can differ. Phoneme-weighted division (the zero-model
control) is a wash vs even (-2 to -3ms MedAE) — mora placement needs the
audio, not a better prior. Two real CTC failure modes, both visible in the
tables: (1) vowel-run smear on held notes — the >=1s-hold bucket is the only
one where raw CTC can lose to even (inochi 244ms vs 148ms, n=7; shinunoga it
still wins 85ms vs 141ms); (2) line-final single-mora drag — line-ending
short words (inochi's やろか… か repeats) get parked on the line window's
reverb tail (+1.0-1.3s, 21 ctc-only outliers on inochi vs 7 even-only).

SHIPPED DEFAULT = the hybrid row (content_to_data MORA_MODE, 2026-07-07):
each word's first mora keeps the shipped word onset, CTC times the
interiors, clamped into the word window with contiguous spans. It kills
failure (2) — inochi ALL PCO@0.2 90.0% vs pure-ctc 85.1% vs even 84.2%;
shinunoga 85.2% (also best) — while matching pure CTC on interior morae
(MedAE 15/44ms, the subset where the win lives). Cost vs pure CTC: word-
initial morae keep the shipped word-onset error (ALL MedAE 30/47ms vs
25/43ms). MANAOKE_MORA_MODE=ctc|even selects the other rows for benching.

Caveats: the ref is crowd-timed YRC (treat single <=100ms disagreements as
ties); matching covers 357/515 (shinunoga; YRC merges hook repeats — handled
— but 私/わたし spelling variants keep 3 lines unmatched) and 241/261
(inochi) ref mora units; both baselines inherit the shipped line windows, so
the ~31 shared shinunoga outliers are window error (the known hook-repeat
drift), not division error.
"""

if __name__ == '__main__':
    main()
