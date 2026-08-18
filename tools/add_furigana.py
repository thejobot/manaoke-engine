#!/usr/bin/env python3
"""Add per-syllable kana timing to a song's data.json.

Apple's word-level timing (apple_lyrics.lines[].words[]) is too coarse
for the romaji wipe to match the JP wipe — within a multi-character
word like '可愛' (kawai), the romaji slides linearly across all 5
letters at constant speed while the JP wipe is per-word.

This script splits each Apple word into per-syllable groups using
pykakasi (kanji → hiragana) plus a built-in Hepburn syllable table,
then proportionally distributes the word's begin_ms/end_ms across
its syllables. The result is appended to each line as a flat
`kana_timings` array:

    {kana: 'こ', rom: 'ko', begin_ms: 17442, end_ms: 17831}

The page reads kana_timings to drive the romaji wipe at syllable
granularity instead of vocab-match granularity. Idempotent — safe
to re-run.

Usage:
    pip3 install --break-system-packages pykakasi
    python3 tools/add_furigana.py songs/inochi-mijikashi/data.json
"""

import sys
import json
from pathlib import Path

try:
    from pykakasi import kakasi
except ImportError:
    print("error: pykakasi not installed. run: pip3 install --break-system-packages pykakasi", file=sys.stderr)
    sys.exit(1)


# ============================================================================
# Hiragana → Hepburn romaji table
# ============================================================================
# Single-kana chars. Yōon (ki+ya etc.) and sokuon (っ) / chōonpu (ー) handled
# as special cases in split_hira().
HIRA = {
    'あ':'a','い':'i','う':'u','え':'e','お':'o',
    'か':'ka','き':'ki','く':'ku','け':'ke','こ':'ko',
    'さ':'sa','し':'shi','す':'su','せ':'se','そ':'so',
    'た':'ta','ち':'chi','つ':'tsu','て':'te','と':'to',
    'な':'na','に':'ni','ぬ':'nu','ね':'ne','の':'no',
    'は':'ha','ひ':'hi','ふ':'fu','へ':'he','ほ':'ho',
    'ま':'ma','み':'mi','む':'mu','め':'me','も':'mo',
    'や':'ya','ゆ':'yu','よ':'yo',
    'ら':'ra','り':'ri','る':'ru','れ':'re','ろ':'ro',
    'わ':'wa','ゐ':'wi','ゑ':'we','を':'wo','ん':'n',
    'が':'ga','ぎ':'gi','ぐ':'gu','げ':'ge','ご':'go',
    'ざ':'za','じ':'ji','ず':'zu','ぜ':'ze','ぞ':'zo',
    'だ':'da','ぢ':'ji','づ':'zu','で':'de','ど':'do',
    'ば':'ba','び':'bi','ぶ':'bu','べ':'be','ぼ':'bo',
    'ぱ':'pa','ぴ':'pi','ぷ':'pu','ぺ':'pe','ぽ':'po',
    # Small variants — mostly handled in yōon, but some appear standalone:
    'ぁ':'a','ぃ':'i','ぅ':'u','ぇ':'e','ぉ':'o',
    'ゔ':'vu',
}

# Yōon: consonant + small ya/yu/yo → single syllable
YOON = {
    'きゃ':'kya','きゅ':'kyu','きょ':'kyo',
    'しゃ':'sha','しゅ':'shu','しょ':'sho',
    'ちゃ':'cha','ちゅ':'chu','ちょ':'cho',
    'にゃ':'nya','にゅ':'nyu','にょ':'nyo',
    'ひゃ':'hya','ひゅ':'hyu','ひょ':'hyo',
    'みゃ':'mya','みゅ':'myu','みょ':'myo',
    'りゃ':'rya','りゅ':'ryu','りょ':'ryo',
    'ぎゃ':'gya','ぎゅ':'gyu','ぎょ':'gyo',
    'じゃ':'ja','じゅ':'ju','じょ':'jo',
    'びゃ':'bya','びゅ':'byu','びょ':'byo',
    'ぴゃ':'pya','ぴゅ':'pyu','ぴょ':'pyo',
}


def split_hira(hira: str):
    """Walk a hiragana string, return [(kana_group, romaji), ...].

    Each entry is one syllable. Yōon (e.g. 'きゃ' → 'kya') stay as one
    syllable. Sokuon ('っ') merges with the following syllable, doubling
    its leading consonant. Long-vowel mark ('ー') extends the previous
    syllable's vowel.
    """
    out = []
    i = 0
    n = len(hira)
    while i < n:
        c = hira[i]

        # Yōon: 2-char cluster
        if i + 1 < n and hira[i:i+2] in YOON:
            out.append((hira[i:i+2], YOON[hira[i:i+2]]))
            i += 2
            continue

        # Sokuon: っ + next syllable's leading consonant doubled
        if c == 'っ' and i + 1 < n:
            # Try yōon at i+1 first
            if i + 2 < n and hira[i+1:i+3] in YOON:
                rom = YOON[hira[i+1:i+3]]
                out.append((c + hira[i+1:i+3], (rom[0] if rom else '') + rom))
                i += 3
                continue
            nxt = hira[i+1]
            rom = HIRA.get(nxt, nxt)
            out.append((c + nxt, (rom[0] if rom else '') + rom))
            i += 2
            continue

        # Chōonpu: extend previous vowel
        if c == 'ー':
            if out:
                k, r = out[-1]
                vowel = r[-1] if r and r[-1] in 'aiueo' else r
                out[-1] = (k + c, r + (vowel if vowel else ''))
            i += 1
            continue

        # Single
        out.append((c, HIRA.get(c, c)))
        i += 1

    return out


# Single shared converter — pykakasi is fairly heavy to instantiate.
_KAKASI = kakasi()


def syllables_for_text(text: str):
    """Run text through pykakasi, walk the resulting hiragana per chunk
    and split into syllables. Returns [(kana, romaji), ...].

    Empty / non-Japanese text returns [].
    """
    if not text:
        return []
    chunks = _KAKASI.convert(text)
    out = []
    for ch in chunks:
        hira = ch.get('hira', '') or ''
        if not hira:
            continue
        out.extend(split_hira(hira))
    return out


def line_kana_timings(words):
    """Given a list of Apple words [{begin_ms, end_ms, text}], return a
    flat list of per-syllable timings spanning the whole line.

    Each Apple word's time range is split equally across its syllables.
    Words with no kana (punctuation etc.) emit no entries.
    """
    out = []
    for w in words:
        text = w.get('text', '') or ''
        b, e = int(w.get('begin_ms', 0)), int(w.get('end_ms', 0))
        if e <= b or not text.strip():
            continue
        sylls = syllables_for_text(text)
        if not sylls:
            continue
        n = len(sylls)
        dur = e - b
        for j, (kana, rom) in enumerate(sylls):
            sb = b + (dur * j) // n
            se = b + (dur * (j + 1)) // n
            out.append({
                'kana': kana,
                'rom': rom,
                'begin_ms': sb,
                'end_ms': se,
            })
    return out


def process(path: Path) -> int:
    data = json.loads(path.read_text())
    apple = data.get('apple_lyrics')
    if not apple:
        print(f'no apple_lyrics in {path}', file=sys.stderr)
        return 1
    lines = apple.get('lines') or []
    touched = 0
    skipped = 0
    for ln in lines:
        words = ln.get('words') or []
        if not words:
            ln['kana_timings'] = []
            skipped += 1
            continue
        ln['kana_timings'] = line_kana_timings(words)
        touched += 1
    apple['has_kana_timings'] = True
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(f'updated {path}: {touched} lines with kana_timings, {skipped} skipped (instrumental / no words)')
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    rc = 0
    for arg in sys.argv[1:]:
        rc |= process(Path(arg))
    sys.exit(rc)


if __name__ == '__main__':
    main()
