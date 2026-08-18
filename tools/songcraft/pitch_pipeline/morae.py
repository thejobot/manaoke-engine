"""Kana → mora splitting. Each mora is one beat; small ya/yu/yo combine with
the preceding kana to form a single mora (e.g., きょ is one mora, not two).
Long-vowel mark ー is its own mora. Sokuon っ/ッ is its own mora.
"""
SMALL_KANA = set('ャュョゃゅょァィゥェォぁぃぅぇぉヮゎ')

def split_morae(kana_str: str) -> list[str]:
    out = []
    i = 0
    while i < len(kana_str):
        c = kana_str[i]
        if i + 1 < len(kana_str) and kana_str[i+1] in SMALL_KANA:
            out.append(c + kana_str[i+1]); i += 2
        else:
            out.append(c); i += 1
    return out

def kata_to_hira(s: str) -> str:
    """Convert full-width katakana to hiragana (preserves ー and small kana)."""
    return ''.join(
        chr(ord(c) - 0x60) if 'ァ' <= c <= 'ン' else c
        for c in s
    )

def hira_to_kata(s: str) -> str:
    return ''.join(
        chr(ord(c) + 0x60) if 'ぁ' <= c <= 'ん' else c
        for c in s
    )
