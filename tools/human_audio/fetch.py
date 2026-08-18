#!/usr/bin/env python3
"""Human-recorded JP word/particle audio — the answer to TTS mangling short
words and morae (頃→"goroo", 気→"kii"). Pulls clean human recordings from the
JapanesePod101 dictionary endpoint (the same source Jisho.org and Yomitan use),
caches them in library/, and logs hits/misses so we never re-fight TTS later.

Source: https://assets.languagepod101.com/dictionary/japanese/audiomp3.php?kanji=&kana=
  - The "word not found" reply is a FIXED file (md5 7e2c2f95…, 52288 bytes) — we
    fingerprint it to detect misses instead of trusting HTTP 200.
  - Tries (kanji+kana) first, then (kana only) as a fallback.

CAVEAT (do not ignore): this endpoint returns the word's HEADWORD/kana reading.
For the particles は / へ / を the headword reading is "ha" / "he" / "wo", but in
running speech they are "wa" / "e" / "o". DO NOT use this source for は/へ/を as a
grammatical particle — keep the TTS わ/え/お clip. Everything else (content words,
に, も, と, から, single morae) is correct.

Licensing: JapanesePod101 / Innovative Language asset. Universally used by the
Anki/Yomitan community for personal study; fine for the owner's private learning site.
If a clean-licensed source is ever needed, Wikimedia Commons "Japanese
pronunciation" + Forvo (CC-BY subset) are the fallbacks — not wired up here yet.

Usage:
  python3 fetch.py --words 気:き 頃:ころ 日:ひ          # explicit kanji:kana pairs
  python3 fetch.py --from-song ../../songs/inochi-mijikashi-v079/data.json
  python3 fetch.py --words 気:き --json out.json        # machine-readable report
"""
import sys, os, json, hashlib, argparse, urllib.request, urllib.parse, time

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, 'library')
os.makedirs(LIB, exist_ok=True)
EP = 'https://assets.languagepod101.com/dictionary/japanese/audiomp3.php'
MISS_MD5 = '7e2c2f954ef6051373ba916f000168dc'   # the fixed "not found" mp3
UA = {'User-Agent': 'Mozilla/5.0'}

# particles whose headword reading != spoken reading — never trust the dict clip
BAD_PARTICLES = {'は', 'へ', 'を'}

def slug(kanji, kana):
    # WRITER convention (stable — do not change): '<surface>__<kana>' normally,
    # degenerating to bare '<surface>' when surface == kana. Existing bare files
    # (に.mp3, お.mp3, …) are canon. READERS must NOT call this directly — use
    # library_lookup(), because TWO name forms coexist historically (see below).
    safe = lambda s: s.replace('/', '_').replace(' ', '')
    return f"{safe(kanji)}__{safe(kana)}" if kana and kana != kanji else safe(kanji)


def library_lookup(surface, kana, exts=('.mp3',)):
    """THE shared library/ READER. Returns an absolute path (str) to a cached
    clip, or None.

    Two clip-name forms coexist historically and both must stay reachable:
      1. '<surface>__<kana>'  — slug()'s normal form; ALSO what a 2026-07-06
         session wrote for surface == kana (library/の__の.mp3), a name slug()
         can never produce, which made that clip invisible to every reader.
      2. bare '<surface>'     — slug()'s degenerate form when surface == kana
         (に.mp3, お.mp3, … are canon under this form).
    Writers keep slug() unchanged; dual-form READ is the compatibility fix.
    Tries '<surface>__<kana>' first, then the slug() form, per extension."""
    safe = lambda s: (s or '').replace('/', '_').replace(' ', '')
    stems = []
    if kana:
        stems.append(f"{safe(surface)}__{safe(kana)}")
    canon = slug(surface, kana)
    if canon not in stems:
        stems.append(canon)
    for stem in stems:
        for ext in exts:
            p = os.path.join(LIB, stem + ext)
            if os.path.exists(p) and os.path.getsize(p) > 0:
                return p
    return None

def _get(params):
    url = EP + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=25).read()

def fetch_one(kanji, kana):
    """Return (status, path|None). status in HIT / MISS / BAD-PARTICLE / ERR."""
    if kanji in BAD_PARTICLES:
        return 'BAD-PARTICLE', None
    out = os.path.join(LIB, slug(kanji, kana) + '.mp3')
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return 'CACHED', out
    attempts = []
    if kana:
        attempts.append({'kanji': kanji, 'kana': kana})
    attempts.append({'kanji': kanji})          # kanji-only
    if kana and kana != kanji:
        attempts.append({'kana': kana})         # kana-only (for hiragana headwords)
    for p in attempts:
        try:
            data = _get(p)
        except Exception as e:
            return 'ERR', None
        if not data:
            continue
        if hashlib.md5(data).hexdigest() == MISS_MD5:
            continue
        open(out, 'wb').write(data)
        return 'HIT', out
    return 'MISS', None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--words', nargs='*', default=[], help='kanji:kana pairs')
    ap.add_argument('--from-song', help='data.json to harvest (jp + jp_speak/rom)')
    ap.add_argument('--json', help='write a machine-readable report here')
    a = ap.parse_args()

    targets = []  # (kanji, kana)
    for w in a.words:
        k, _, r = w.partition(':')
        targets.append((k, r))
    if a.from_song:
        d = json.load(open(a.from_song))
        for s in d['sections']:
            for w in s.get('words', []):
                kana = w.get('jp_speak') or w.get('jp')
                targets.append((w['jp'], kana))
    # dedupe, preserve order
    seen = set(); uniq = []
    for t in targets:
        if t in seen: continue
        seen.add(t); uniq.append(t)

    rows = []
    for kanji, kana in uniq:
        st, path = fetch_one(kanji, kana)
        sz = os.path.getsize(path) if path and os.path.exists(path) else 0
        rows.append(dict(kanji=kanji, kana=kana, status=st, size=sz,
                         file=os.path.basename(path) if path else None))
        print(f"  {kanji:6} {kana:8} {st:12} {sz or ''}", flush=True)
        time.sleep(0.15)  # be polite to the endpoint

    hits = [r for r in rows if r['status'] in ('HIT', 'CACHED')]
    print(f"\n{len(hits)}/{len(rows)} available in library/  "
          f"(miss: {[r['kanji'] for r in rows if r['status']=='MISS']}, "
          f"skip-particle: {[r['kanji'] for r in rows if r['status']=='BAD-PARTICLE']})")
    if a.json:
        json.dump(rows, open(a.json, 'w'), ensure_ascii=False, indent=1)

if __name__ == '__main__':
    main()
