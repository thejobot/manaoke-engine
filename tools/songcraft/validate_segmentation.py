#!/usr/bin/env python3
"""validate_segmentation.py — flag study cards that are NOT single dictionary words.

THE RULE (the owner's, the hard way): one card == one dictionary lookup. A beginner
can't be assumed to know that two glued morphemes combine into a meaning, so a
card must be a thing they could actually look up. The arbiter is a DICTIONARY,
not a hand-maintained "these clusters are fine" allowlist — that allowlist is
exactly what shipped 付けてほしい AND のかな (I had listed のかな as an ok "sentence
cluster"; the dictionary has no such word). のかな is の + か + な; かな alone IS a word.

The dictionary is LOCAL and OFFLINE — the set of every JMdict headword (kanji +
reading form), bundled at data/jmdict_headwords.txt.gz. JMdict is the same data
jisho.org is built on, so the verdict equals jisho's (validated 60/60 against the
old API cache) but with no network call — a hash-set membership test, deterministic,
never fuzzy, never AI. See data/NOTICE-jmdict.txt for provenance/licence.

HOW IT DECIDES, per card:
  1. Tokenize with fugashi/unidic and coalesce into "units":
       - a content stem keeps its own inflection (た/たら/ます/ない/て) — 気付いた,
         生まれ変わったら are ONE unit (a single inflected verb);
       - a verb directly after a verb with no て-hinge is a COMPOUND VERB, one
         unit (弾け出す, 守り続ける — these are NOT dictionary headwords, so the
         dictionary alone would wrongly split them; the morphology keeps them whole);
       - honorific 接頭辞 (お/ご) attach forward, number+counter coalesce (お買い物,
         1番 stay whole);
       - every particle, and any auxiliary lexeme after a て-hinge (しまう/いる/
         ほしい/くれる), starts a NEW unit.
  2. <= 1 unit  → single (possibly inflected) word. OK.
  3. >= 2 units → OK **only if the whole surface is a JMdict headword** (かな, にも,
       ように, だろう, から, 思考回路, 木の葉, 誰も彼も all ARE — stay whole; のかな,
       タッチした, 付けてほしい, シガレットアメリカン, なろうかな are NOT — they split).

Run in the parler env (fugashi/unidic live there):
  /opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python \
      tools/songcraft/validate_segmentation.py songs/headlong-fzc9fg
  ... --all      # every live song (backlog survey)
  ... --json     # machine-readable findings for backlog.py
"""
import argparse, gzip, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SONGS = ROOT / 'songs'
HEADWORDS_GZ = Path(__file__).resolve().parent / 'data' / 'jmdict_headwords.txt.gz'

# Tiny manual override for a genuine word the dictionary somehow lacks; grow only
# when a REAL word is false-flagged (JMdict is the authority, not this).
KEEP_WHOLE_LEXICAL = {
    # silhouette chant (KANA-BOON いっせーのーせ): JMdict has いっせーの/せーの but
    # not these elongated stage forms; the suggested splits (いっせー│のーせ) are
    # ALL non-headwords — splitting swaps one recognizable chant for two
    # unlookupable fragments. Adjudicated 2026-07-07 (backlog 797386b8, 7be9e15f).
    'いっせーのーせ', 'いっせーのー',
    # shinunoga (Fujii Kaze, Okayama dialect): したない = したくない as ONE
    # inflected dialect word. fugashi misparses した+ない, which as cards would
    # teach false grammar ("did"+"not"). Adjudicated 2026-07-07 (backlog 23724cb9).
    'したない',
}

# 代名詞 (pronouns: いつ/どこ/君/何) and 接続詞 (conjunctions) are content
# words too — omitting them sent いつ down the fallback-glue branch and
# fused 君がいつも into a がいつ "card" (found by the scaffold's self-check
# 2026-07-11). They start their own unit like any content word.
CONTENT = ('名詞', '代名詞', '動詞', '形容詞', '副詞', '形状詞', '連体詞',
           '感動詞', '接続詞')

_headwords = None


def is_word(text):
    """True iff `text` is an exact JMdict headword. Local, offline, deterministic —
    a hash-set membership test (the dictionary is jisho.org's own source data)."""
    global _headwords
    if _headwords is None:
        if not HEADWORDS_GZ.exists():
            sys.exit(f'missing dictionary {HEADWORDS_GZ} — see data/NOTICE-jmdict.txt to regenerate.')
        with gzip.open(HEADWORDS_GZ, 'rt', encoding='utf-8') as f:
            _headwords = set(line.rstrip('\n') for line in f if line.strip())
    return text in _headwords


def feat(node, name, default=''):
    try:
        v = getattr(node.feature, name)
        return v if v not in (None, '*') else default
    except Exception:
        return default


def units_of(tagger, surface):
    """Coalesce fugashi tokens into dictionary-lookup units (see module docstring)."""
    nodes = list(tagger(surface))
    units, prev_pos1, prev_pos2, prefix = [], None, None, ''
    for n in nodes:
        pos1, pos2 = feat(n, 'pos1'), feat(n, 'pos2')
        if pos1 == '接頭辞':
            prefix += n.surface                   # お/ご/御 — glue forward to the next word
            continue
        s = prefix + n.surface
        prefix = ''
        is_infl = pos1 in ('助動詞', '補助記号', '記号') or (pos1 == '助詞' and pos2 == '接続助詞')
        is_particle = pos1 == '助詞' and pos2 != '接続助詞'
        is_content = pos1 in CONTENT
        if not units:
            units.append(s)
        elif is_infl:
            units[-1] += s                        # た/ます/ない/て ride the stem
        elif is_content and pos1 == '動詞' and prev_pos1 == '動詞':
            units[-1] += s                        # 弾け+出す = one compound verb
        elif is_content and pos1 == '名詞' and prev_pos2 == '数詞':
            units[-1] += s                        # 1+番, 三+度 = number + counter, one unit
        elif is_content or is_particle:
            units.append(s)                       # new content word OR standalone particle
        else:
            units[-1] += s
        prev_pos1, prev_pos2 = pos1, pos2
    if prefix:
        units.append(prefix) if not units else units.__setitem__(-1, units[-1] + prefix)
    return [u for u in units if u.strip()]


def analyze_card(tagger, jp):
    """(is_over_merged, reason, suggested_units) for one card surface."""
    surface = (jp or '').strip()
    if not surface or surface in KEEP_WHOLE_LEXICAL:
        return False, '', []
    if re.fullmatch(r"[A-Za-z' ]+", surface):     # pure English chorus word
        return False, '', []
    units = units_of(tagger, surface)
    if len(units) <= 1:
        return False, '', []                      # single (possibly inflected) word
    if is_word(surface):
        return False, '', []                      # a real dictionary headword — keep whole
    reason = (f'not a dictionary word (JMdict has no "{surface}") — '
              f'it is {len(units)} lookups: ' + ' │ '.join(units))
    return True, reason, units


def load_tagger():
    try:
        import fugashi
        return fugashi.Tagger()
    except Exception as e:
        sys.exit(f'fugashi/unidic not importable ({e}); run under the parler env:\n'
                 '  /opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python '
                 + ' '.join(sys.argv))


def song_words(slug):
    d = json.loads((SONGS / slug / 'data.json').read_text())
    return [(s.get('id', s.get('name', '?')), w.get('jp', ''), w.get('rom', ''))
            for s in d.get('sections', []) for w in s.get('words', [])]


def audit(slug, tagger):
    out = []
    for sec, jp, rom in song_words(slug):
        merged, reason, units = analyze_card(tagger, jp)
        if merged:
            out.append(dict(song=slug, section=sec, jp=jp, rom=rom, reason=reason, suggest=units))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('song', nargs='?')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    tagger = load_tagger()
    if a.all:
        slugs = re.findall(r"url:\s*'/songs/([a-z0-9-]+)/'", (ROOT / 'index.html').read_text())
    elif a.song:
        slugs = [a.song.replace('songs/', '').strip('/')]
    else:
        ap.error('pass a song slug or --all')

    findings, n_checked = [], 0
    for slug in slugs:
        if (SONGS / slug / 'data.json').exists():
            findings.extend(audit(slug, tagger))
            n_checked += 1
        else:
            print(f'skip {slug}: no data.json', file=sys.stderr)

    # Fail-closed exits (2026-07-10 audit): findings in --all mode used to
    # exit 0, and a run where every named song was skipped printed the clean
    # banner — both read as green in scripts. A sweep that checked nothing,
    # or found anything, is not clean.
    if n_checked == 0:
        print('✗ SEGMENTATION: checked 0 song(s) — every target was skipped '
              '(no data.json). Nothing was verified.', file=sys.stderr)
        if a.json:
            print(json.dumps(findings, ensure_ascii=False, indent=2))
        return 1
    if a.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
        return 1 if findings else 0
    if not findings:
        print(f'✓ SEGMENTATION: every card is one dictionary word '
              f'({n_checked} song(s) checked).')
        return 0
    by_song = {}
    for f in findings:
        by_song.setdefault(f['song'], []).append(f)
    print(f'✗ SEGMENTATION: {len(findings)} non-word card(s) across {len(by_song)} song(s):\n')
    for slug, fs in by_song.items():
        print(f'  {slug}  ({len(fs)}):')
        for f in fs:
            print(f"    [{f['section']}] {f['jp']}  [{f['rom']}]  → {' │ '.join(f['suggest'])}")
        print()
    return 1


if __name__ == '__main__':
    sys.exit(main())
