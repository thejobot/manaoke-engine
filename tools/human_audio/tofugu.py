#!/usr/bin/env python3
"""Human-recorded JP word audio from the WaniKani/Tofugu collection — the
clean-licensed offline source the README promised. Same job as fetch.py (fix TTS
mangling content words: 頃->"goroo", 気->"kii"), but instead of hitting the
JapanesePod101 endpoint it resolves from a local 6,355-word corpus of native
human recordings and copies hits into library/ using the SAME naming as fetch.py,
so the two sources are interchangeable downstream.

Use it as fetch.py's FALLBACK: run fetch.py first, then run this for the misses
(or with --overwrite to prefer Tofugu's single consistent pro speaker).

Corpus: ~/Desktop/JP TTS Research/tofugu-wanikani-audio/lib/mp3/
  Filenames are SURFACE【READING】.mp3 (e.g. 頃【ころ】.mp3). License: CC-BY-SA-4.0,
  attribute Tofugu + WaniKani. Override the path with $TOFUGU_DIR.

CAVEAT (read before baking a clip in): the corpus mixes TWO speakers —
a male Tokyo-accent professional and a female Kansai-accent amateur — and the
filename does NOT say which. Most are the clean male pro, but a few are Kansai /
amateur. ALWAYS spot-listen a clip before loudnorm'ing it into a song (that's
already a manual step — see README "Install a library clip into a song").

Same particle guard as fetch.py: は/へ/を are refused (headword reading ha/he/wo
!= spoken wa/e/o) — keep the TTS わ/え/お clip for those as particles.

Usage:
  python3 tofugu.py --words 頃:ころ 気:き 子供:こども     # explicit surface:reading
  python3 tofugu.py --from-song ../../songs/inochi-mijikashi-v088/data.json
  python3 tofugu.py --words 頃:ころ --overwrite          # replace an existing library clip
  python3 tofugu.py --words 頃:ころ --json out.json       # machine-readable report
"""
import sys, os, re, json, glob, shutil, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, 'library')
os.makedirs(LIB, exist_ok=True)
CORPUS = os.environ.get('TOFUGU_DIR') or os.path.expanduser(
    '~/Desktop/JP TTS Research/tofugu-wanikani-audio/lib/mp3')

# particles whose headword reading != spoken reading — never trust the dict clip
BAD_PARTICLES = {'は', 'へ', 'を'}
NAME_RE = re.compile(r'^(?P<surface>.+?)【(?P<reading>.+?)】\.mp3$')

def kata2hira(s):
    """Fold katakana to hiragana so reading comparisons ignore script."""
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in s or '')

def slug(kanji, kana):  # identical to fetch.py so the two sources share library/
    safe = lambda s: s.replace('/', '_').replace(' ', '')
    return f"{safe(kanji)}__{safe(kana)}" if kana and kana != kanji else safe(kanji)

_index = None
def index():
    """Build {surface: {reading: path}} once from the corpus filenames."""
    global _index
    if _index is not None:
        return _index
    _index = {}
    if not os.path.isdir(CORPUS):
        sys.exit(f"corpus not found: {CORPUS}\n"
                 f"clone it: git clone --depth 1 --filter=blob:none --sparse "
                 f"https://github.com/tofugu/japanese-vocabulary-pronunciation-audio.git "
                 f"&& (cd ... && git sparse-checkout set lib/mp3)  — or set $TOFUGU_DIR")
    for p in glob.glob(os.path.join(CORPUS, '*.mp3')):
        m = NAME_RE.match(os.path.basename(p))
        if m:
            _index.setdefault(m['surface'], {})[m['reading']] = p
    return _index

def resolve(kanji, kana):
    """Return (corpus_path|None, note). Matching, best-first:
       exact surface+reading -> surface w/ matching reading -> surface w/ a single
       reading THAT MATCHES the requested kana (kata2hira-folded; a lone
       mismatching reading REFUSES with READING-MISMATCH rather than serving a
       wrong-reading clip) -> the kana itself as a headword."""
    idx = index()
    by_reading = idx.get(kanji)
    if by_reading:
        if kana and kana in by_reading:
            return by_reading[kana], f"{kanji}【{kana}】"
        if len(by_reading) == 1:
            r, p = next(iter(by_reading.items()))
            if kana and kata2hira(r) != kata2hira(kana):
                # STRICT reading guard: the corpus's only reading for this
                # surface is NOT the requested one (良い【よい】 asked いい).
                # Returning it would bake a wrong-reading clip — refuse.
                return None, f"READING-MISMATCH corpus has {r}, asked {kana}"
            return p, f"{kanji}【{r}】"
        if kana:  # multiple readings, none equal kana -> ambiguous, don't guess
            return None, f"AMBIG {kanji} has {sorted(by_reading)}"
    # surface might itself be the kana (pure-kana headword)
    by_reading = idx.get(kana) if kana else None
    if by_reading:
        r, p = (kana, by_reading[kana]) if kana in by_reading else next(iter(by_reading.items()))
        return p, f"{kana}【{r}】"
    return None, ""

def fetch_one(kanji, kana, overwrite=False):
    """Return (status, path|None, note). status in
       HIT / CACHED / MISS / BAD-PARTICLE / AMBIG."""
    if kanji in BAD_PARTICLES:
        return 'BAD-PARTICLE', None, ''
    out = os.path.join(LIB, slug(kanji, kana) + '.mp3')
    if os.path.exists(out) and os.path.getsize(out) > 0 and not overwrite:
        return 'CACHED', out, ''
    src, note = resolve(kanji, kana)
    if not src:
        return ('AMBIG' if note.startswith('AMBIG') else 'MISS'), None, note
    shutil.copyfile(src, out)
    return 'HIT', out, note

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--words', nargs='*', default=[], help='surface:reading pairs')
    ap.add_argument('--from-song', help='data.json to harvest (jp + jp_speak/rom)')
    ap.add_argument('--overwrite', action='store_true',
                    help='replace an existing library clip (prefer Tofugu over JPod101)')
    ap.add_argument('--json', help='write a machine-readable report here')
    a = ap.parse_args()

    targets = []  # (surface, reading)
    for w in a.words:
        k, _, r = w.partition(':')
        targets.append((k, r))
    if a.from_song:
        d = json.load(open(a.from_song))
        for s in d['sections']:
            for w in s.get('words', []):
                targets.append((w['jp'], w.get('jp_speak') or w.get('jp')))
    seen = set(); uniq = []
    for t in targets:
        if t in seen: continue
        seen.add(t); uniq.append(t)

    rows = []
    for kanji, kana in uniq:
        st, path, note = fetch_one(kanji, kana, a.overwrite)
        sz = os.path.getsize(path) if path and os.path.exists(path) else 0
        rows.append(dict(kanji=kanji, kana=kana, status=st, size=sz,
                         file=os.path.basename(path) if path else None, source=note or None))
        print(f"  {kanji:6} {kana:8} {st:12} {sz or '':>6}  {note}", flush=True)

    hits = [r for r in rows if r['status'] in ('HIT', 'CACHED')]
    print(f"\n{len(hits)}/{len(rows)} available in library/  "
          f"(new: {sum(1 for r in rows if r['status']=='HIT')}, "
          f"miss: {[r['kanji'] for r in rows if r['status']=='MISS']}, "
          f"ambiguous: {[r['kanji'] for r in rows if r['status']=='AMBIG']}, "
          f"skip-particle: {[r['kanji'] for r in rows if r['status']=='BAD-PARTICLE']})")
    print("Reminder: spot-listen each new clip before baking — speaker/accent varies per word.")
    if a.json:
        json.dump(rows, open(a.json, 'w'), ensure_ascii=False, indent=1)

if __name__ == '__main__':
    main()
