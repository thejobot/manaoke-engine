#!/usr/bin/env python3
"""run_bench.py — the aligner benchmark: how close is the local CTC forced
aligner to owning word/mora-level timing, measured against NetEase YRC
word-level references.

Stages (all idempotent; cached artifacts are reused unless --refresh):
  1. refs : fetch each song's lyrics through the RUNNING LyriCool server
            (127.0.0.1:8769, POST /api/song — NetEase YRC preferred there) and
            save the universal payload to refs/<key>.json. Songs whose best
            NetEase match has no YRC get a line-level-only ref and are
            EXCLUDED from word scoring (recorded in RESULTS.md).
  2. hyps : run the pure aligner (dump_align.py under the parler python) per
            song against the cached /tmp audio (wsync_/hq_/demucs stems —
            restore from tools/songcraft/corpus/ first). Songs with no cached
            audio need a yt-dlp download + demucs pass; those run under
            --download-timeout and are skipped (with a note) on overrun.
  3. score: score.py — RAW-aligner and SHIPPED (builds/<key>.lyrics.json)
            word onsets vs the ref, raw + offset-corrected. Writes
            results/RESULTS.md.

Run with plain python3 (stage 2 shells out to the parler env itself):
  python3 run_bench.py [--refresh] [--skip-hyps] [--download-timeout 600]
"""
import argparse, json, subprocess, sys, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SONGCRAFT = HERE.parent
ROOT = SONGCRAFT.parent.parent
BUILDS = SONGCRAFT / 'builds'
REFS, HYPS, RESULTS = HERE / 'refs', HERE / 'hyps', HERE / 'results'
LYRICOOL = 'http://127.0.0.1:8769'
PARLER = '/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python'
TMP = Path('/tmp')

sys.path.insert(0, str(HERE))
import score as scorer  # noqa: E402

# Song identities from builds/*.build_state.json meta (+ inochi from the
# promoted v098 data.json). duration_ms = the corpus hq wav duration (video
# length) for the 4 corpus songs; inochi's is the Apple-track ballpark.
SONGS = [
    {'key': 'headlong', 'yt': 'ADYzSz4FuGU', 'title': 'Headlong',
     'artist': 'ArtTheaterGuild', 'duration_ms': 180693,
     'lyrics': BUILDS / 'headlong.lyrics.json'},
    {'key': 'odoriko', 'yt': '7HgJIAUtICU', 'title': '踊り子',
     'artist': 'Vaundy', 'duration_ms': 245377,
     'lyrics': BUILDS / 'odoriko.lyrics.json'},
    {'key': 'shinunoga', 'yt': 'dawrQnvwMTY', 'title': '死ぬのがいいわ',
     'artist': '藤井 風', 'duration_ms': 191367,
     'lyrics': BUILDS / 'shinunoga.lyrics.json'},
    {'key': 'silhouette', 'yt': 'dlFA0Zq1k2A', 'title': 'シルエット',
     'artist': 'KANA-BOON', 'duration_ms': 265033,
     'lyrics': BUILDS / 'silhouette.lyrics.json'},
    {'key': 'inochi-mijikashi', 'yt': '7cCL0owFBqk',
     'title': 'イノチミジカシコイセヨオトメ', 'artist': 'クリープハイプ',
     'duration_ms': 165000,
     'lyrics': ROOT / 'songs' / 'inochi-mijikashi-v098' / 'data.json',
     'lyrics_key': 'apple_lyrics'},
]

# The quality bar the verdict paragraph measures against.
BAR_PCO300, BAR_MEDAE = 100.0, 50.0


def load_lyrics(song):
    d = json.loads(Path(song['lyrics']).read_text())
    return d[song['lyrics_key']] if song.get('lyrics_key') else d


def fetch_ref(song, refresh=False):
    outp = REFS / f"{song['key']}.json"
    if outp.exists() and not refresh:
        return json.loads(outp.read_text())
    body = json.dumps({'title': song['title'], 'artist': song['artist'],
                       'duration_ms': song['duration_ms']}).encode()
    req = urllib.request.Request(f'{LYRICOOL}/api/song', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read())
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    return payload


def audio_cached(yt):
    return ((TMP / f'wsync_{yt}.wav').exists()
            and (TMP / 'demucs' / 'htdemucs' / f'hq_{yt}' / 'vocals.wav').exists())


def run_hyp(song, timeout, refresh=False):
    """Returns (hyp_dict_or_None, note)."""
    outp = HYPS / f"{song['key']}.json"
    if outp.exists() and not refresh:
        return json.loads(outp.read_text()), 'cached'
    cached = audio_cached(song['yt'])
    stage = HERE / f".stage_{song['key']}.lyrics.json"
    stage.write_text(json.dumps(load_lyrics(song), ensure_ascii=False))
    try:
        cmd = [PARLER, str(HERE / 'dump_align.py'), song['key'],
               '--yt', song['yt'], '--lyrics', str(stage), '--out', str(outp)]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=(timeout if not cached else None))
    except subprocess.TimeoutExpired:
        return None, f'SKIPPED — download+demucs exceeded {timeout}s budget'
    finally:
        stage.unlink(missing_ok=True)
    if r.returncode != 0 or not outp.exists():
        return None, f'FAILED — {r.stderr.strip()[-300:]}'
    print(r.stdout.strip())
    return json.loads(outp.read_text()), ('aligned (cached audio)' if cached
                                          else 'aligned (fresh download+demucs)')


def fmt_ms(v):
    return f'{v:+.0f}' if v is not None else '—'


def mrow(name, m):
    if m is None:
        return f'| {name} | — | — | — | — | — | — |'
    return (f"| {name} | {m['n_matched']} | {m['mae']:.0f} | {m['medae']:.0f} "
            f"| {m['pco100']:.0f}% | {m['pco200']:.0f}% | {m['pco300']:.0f}% |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true',
                    help='refetch refs + rerun alignments even if cached')
    ap.add_argument('--skip-hyps', action='store_true',
                    help='score existing hyps only (no aligner runs)')
    ap.add_argument('--download-timeout', type=int, default=600,
                    help='budget (s) for songs needing yt-dlp + demucs')
    a = ap.parse_args()
    for d in (REFS, HYPS, RESULTS):
        d.mkdir(parents=True, exist_ok=True)

    report = {}
    for song in SONGS:
        k = song['key']
        entry = report.setdefault(k, {'song': song})
        try:
            ref = fetch_ref(song, a.refresh)
        except Exception as e:
            entry['ref_note'] = f'ref fetch FAILED: {e}'
            continue
        entry['ref'] = ref
        rl = scorer.ref_lines(ref)
        n_rw = sum(len(l['words']) for l in rl)
        ne = ref.get('sources', {}).get('netease', {})
        entry['has_word_ref'] = bool(rl)
        entry['ref_note'] = (
            f"NetEase id {ne.get('id')} ({ne.get('duration_sec')}s), "
            f"YRC word-level: {'YES — ' + str(n_rw) + ' ref word onsets on ' + str(len(rl)) + ' lines' if rl else 'NO (line-level only) — word scoring skipped'}")

        if a.skip_hyps and not (HYPS / f'{k}.json').exists():
            entry['hyp_note'] = 'skipped (--skip-hyps)'
        else:
            hyp, note = run_hyp(song, a.download_timeout, a.refresh)
            entry['hyp'], entry['hyp_note'] = hyp, note

        if entry['has_word_ref']:
            if entry.get('hyp'):
                entry['m_raw'] = scorer.metrics(
                    scorer.match(rl, scorer.hyp_lines_raw(entry['hyp'])))
            entry['m_shipped'] = scorer.metrics(
                scorer.match(rl, scorer.hyp_lines_shipped(load_lyrics(song))))

    write_results(report)
    print(f"\nwrote {RESULTS / 'RESULTS.md'}")


def write_results(report):
    L = []
    L.append('# Aligner benchmark — local word-onset quality vs NetEase YRC')
    L.append('')
    L.append('Question: how close is the local pipeline (demucs vocal stem + '
             'MMS CTC forced alignment) to owning word/mora-level timing, '
             'without any external word-timing source?')
    L.append('')
    L.append('- REF = NetEase YRC word-level timings (per-character onsets, '
             'studio-track clock) fetched via LyriCool.')
    L.append('- RAW ALIGNER = pure CTC token onsets from whisper_sync\'s '
             'align_lines, dry, no per-line anchoring (video clock).')
    L.append('- SHIPPED = word onsets in builds/<key>.lyrics.json (post '
             'plausibility-gate + per-line affine anchoring + hand fixes).')
    L.append('- Matching is line-anchored then character-anchored (kana '
             'folded, punctuation stripped, difflib on line sequences — '
             'occurrence-safe for repeated hooks); onset error per matched '
             'word.')
    L.append('- OFFSET-CORRECTED subtracts the per-song median delta — the '
             'YRC studio clock vs YouTube video clock differ by a constant; '
             'corrected numbers are the honest aligner-quality measure.')
    L.append(f'- Bar: every word within 0.3s (PCO@0.3 = 100%) AND MedAE <= '
             f'{BAR_MEDAE:.0f}ms, offset-corrected.')
    L.append('')

    for k, e in report.items():
        song = e['song']
        L.append(f"## {k} — {song['title']} / {song['artist']}")
        L.append('')
        L.append(f"- ref: {e.get('ref_note', 'n/a')}")
        L.append(f"- aligner run: {e.get('hyp_note', 'n/a')}")
        if not e.get('has_word_ref'):
            L.append('- NOT SCORED (no word-level reference).')
            L.append('')
            continue
        L.append('')
        for label, mk in (('RAW (uncorrected)', 'raw'),
                          ('OFFSET-CORRECTED', 'corrected')):
            L.append(f'### {label}')
            L.append('')
            L.append('| hypothesis | n_matched | MAE ms | MedAE ms | '
                     'PCO@0.1s | PCO@0.2s | PCO@0.3s |')
            L.append('|---|---|---|---|---|---|---|')
            for name, key in (('raw aligner', 'm_raw'), ('shipped', 'm_shipped')):
                m = e.get(key)
                L.append(mrow(name, m[mk] | {'n_matched': m['n_matched']}
                              if m else None))
            L.append('')
        for name, key in (('raw aligner', 'm_raw'), ('shipped', 'm_shipped')):
            m = e.get(key)
            if not m:
                continue
            L.append(f'### Worst 10 matched words — {name} '
                     f'(median offset {fmt_ms(m["median_offset_ms"])}ms '
                     'already removed)')
            L.append('')
            L.append('| word | ref onset ms | hyp onset ms | corrected delta ms |')
            L.append('|---|---|---|---|')
            for w in m['worst10']:
                L.append(f"| {w['text']} | {w['ref_ms']:.0f} | "
                         f"{w['hyp_ms']:.0f} | {fmt_ms(w['delta_corr_ms'])} |")
            L.append('')

    L.append('## Verdict')
    L.append('')
    scored = [(k, e) for k, e in report.items()
              if e.get('has_word_ref') and e.get('m_raw')]
    for k, e in scored:
        c = e['m_raw']['corrected']
        hit = c['pco300'] >= BAR_PCO300 and c['medae'] <= BAR_MEDAE
        L.append(f"- {k}: raw aligner MedAE {c['medae']:.0f}ms, "
                 f"PCO@0.3 {c['pco300']:.0f}% — "
                 f"{'MEETS' if hit else 'MISSES'} the bar "
                 f"(need PCO@0.3=100% and MedAE<=50ms).")
    L.append('')
    L.append(VERDICT_PROSE.strip())
    (RESULTS / 'RESULTS.md').write_text('\n'.join(L) + '\n')


# Interpretive paragraph — updated by hand after each benchmark run so the
# prose matches the numbers above (the tables are the machine truth).
VERDICT_PROSE = """
How far is the local aligner from "every word within 0.3s + MedAE <= 50ms"?
Close on the median, killed by section-level catastrophes. On all three
scored songs the offset-corrected MedAE is 30-40ms — the typical word the
CTC aligner locks onto is already timed BETTER than the 50ms bar, and better
than our shipped timings on inochi (raw 30ms vs shipped 66ms, because the
shipped path re-quantizes word onsets through per-line affine anchoring).
The bar-killer is not per-word jitter; it is a handful of section-level
parking failures that throw whole line runs 3-22 SECONDS off and account for
essentially all the PCO@0.3 shortfall (inochi 97%, headlong 91%, shinunoga
74%):

1. Instrumental-break parking of repeated hook lines (worst, shinunoga):
   the 7-line final-chorus block after the bridge (あなたとこのまま… /
   死ぬのがいいわ repeats) is parked 20-22s EARLY, spread over the
   instrumental break — identical repeated text gives CTC nothing to
   disambiguate, exactly the failure the plausibility gate was built for.
   26% of shinunoga's matched words (55/215) fall outside 0.3s: this block
   is the catastrophic half (25 words parked >3s off); the other 30 are the
   mid-band drift of pattern 4. (The shipped timings hand-rescued the block:
   shipped max error there is ~3s vs raw 22s.)
2. Line-initial grab of intro vocal energy (headlong): the first sung word
   月 latched onto pre-first-line vocal noise 21s early.
3. Non-lexical / English scat runs at song ends (headlong): the outro run
   ("dizzy … headlong") smeared 7-17s late toward the fade.
4. Mid-band drift (0.3-3s) on repeated hooks and English interjections
   (shinunoga): the 死ぬのがいいわ repeats sit +1-3s (raw AND shipped — the
   hand fixes didn't fully rescue them either) and EN ad-lib words
   (oh / say / byebye / no) drift 0.4-2.5s. Particles and ordinary line-end
   words are NOT a failure pattern: every worst-10 row traces to a repeat
   run, a section break, or an EN interjection, not word class.

Verdict: the aligner already owns word-level timing WITHIN a correctly
anchored line: 30-40ms median, and with the >3s parking removed PCO@0.3 is
headlong 93% / shinunoga 84% / inochi 97% (PCO@0.2: 88/82/97%). To own
timing outright it needs section anchoring and repeat handling, not a
better per-word model — constrain repeated-line runs with vocal-activity
spans (the tail-clamp generalized to interior breaks), window the alignment
per verse/chorus, and treat EN interjections as low-confidence anchors.
That fixes patterns 1-3 outright (headlong/inochi -> ~97-99% PCO@0.3) but
shinunoga's hook repeats (pattern 4) keep it in the mid-80s until repeats
get disambiguated (e.g. melody/energy features or per-occurrence windows).
Caveats: the reference is NetEase YRC (studio clock, itself crowd-timed —
treat single ±100ms disagreements as ties); odoriko and silhouette have no
YRC word-level entry on NetEase, so 2 of 5 songs are unscored; shinunoga's
shipped n_matched (139 vs 215 raw) is lower because shipped line texts carry
"(backing vocal)" parentheticals that break exact line matching, so its
shipped numbers cover fewer lines.
"""

if __name__ == '__main__':
    main()
