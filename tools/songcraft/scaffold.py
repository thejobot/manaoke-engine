#!/usr/bin/env python3
"""scaffold — the deterministic content.json skeleton (four-verbs 1.3).

Everything MECHANICAL about a new song, generated locally and gate-clean by
construction; every CREATIVE field left empty for a human or an AI drafter
(ai_provider.py) to fill. This is what makes AI optional: after the scaffold,
"authoring" is only the teaching voice — translations, explainers, spoken
definitions, section intros, trivia, grammar.

Filled by the scaffold (deterministic, offline):
  lines[]           every timed lyric line + its section assignment
  sections[]        blocks from real silence + repetition (ids s1..sN;
                    names/subtitles empty) — a first guess to correct while
                    authoring, not a structure detector
  words[]           segmentation via validate_segmentation's OWN units_of/
                    is_word (the gate that judges cards also writes them —
                    every emitted card is re-checked with analyze_card and
                    the scaffold REFUSES to write if any card would fail),
                    readings (fugashi kana, orthographic), romaji, E8
                    jp_speak forms (bare は/へ/を -> わ/え/お), particle
                    flags, JMdict display glosses (data/jmdict_gloss.json.gz),
                    per-section dedupe + uid on rom collisions
  line_tr / line_explain   every key present, value "" — the creative
                    worklist, visible and countable
  identity          title/artist/yt/art/apple/level copied from build_state

Left empty on purpose (the residue a drafter fills): en_speak, context,
gloss, hint, section names/descriptions/speak_en, line_tr/line_explain
values, trivia, grammar, podcast_script.

Run under the parler env (fugashi + unidic_lite + jaconv):
  /opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python \
      tools/songcraft/scaffold.py <key> [--force] [--song-number N]

Refuses to overwrite an existing content.json without --force (an authored
file is a human artifact; the scaffold only ever writes skeletons).
"""
import argparse
import gzip
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILDS = HERE / 'builds'
GLOSS_GZ = HERE / 'data' / 'jmdict_gloss.json.gz'

sys.path.insert(0, str(HERE))
import validate_segmentation as vseg          # units_of / is_word / analyze_card
from content_to_data import (CJK, PURE_KANA, moraize, mora_rom, strip_echo)

PARTICLE_SPOKEN = {'は': 'わ', 'へ': 'え', 'を': 'お'}   # E8 rule table
# Dials swept against the six songs whose sections a person authored
# (scratch harness, 2026-07-29): boundary F1 0.56 at these values — it agrees
# with the author on about half the cuts and never invents a 1-line section.
# A first guess to correct while authoring, NOT a structure detector.
SECTION_GAP_MS = 1800          # real silence (not a breath) that starts a block
MIN_LINES = 3                  # a 1-2 line "section" is a breath, not a section
MAX_LINES = 14                 # longer than this is two blocks run together
LATIN = re.compile(r"^[A-Za-z0-9' .,!?-]+$")
KANJI = re.compile(r'[㐀-鿿]')      # kanji only, unlike content_to_data's CJK
# punctuation that can cling to a segmented unit — never part of the word.
# NOT the long-vowel mark ー: that IS part of マリーゴールド.
PUNCT = '「」『』（）()〈〉《》【】…‥・、。，．,.!?！？"\'“”‘’ 　'
WS = ' 　\t'          # only these mean the lyric put two words apart

# Particles are a closed class and JMdict lookup by kana surface hits
# homographs (に -> 荷 "load / baggage"). House-style glosses, from the
# shipped songs' own cards; review can still rephrase per line.
PARTICLE_GLOSS = {
    'は': 'as for ___', 'が': '(subject marker)', 'を': '(object marker)',
    'に': 'to / at / in', 'で': 'at / by / with', 'と': 'and / with',
    'も': 'also / even', 'の': 'of / ___\'s', 'へ': 'to / toward',
    'や': 'and (listing)', 'か': '(question marker)', 'ね': 'right? / isn\'t it',
    'よ': '(emphasis)', 'な': '(emphasis / huh)', 'ぞ': '(strong emphasis)',
    'さ': '(casual emphasis)', 'から': 'because / from', 'まで': 'until / up to',
    'だけ': 'only / just', 'って': 'quoting / speaking of',
    'けど': 'but / though', 'のに': 'even though', 'には': 'in / for / by',
}

_gloss = None


def _sense(head, reading='', require_reading=False):
    """One headword's best sense. The map holds every entry that spells itself
    this way, priority-first; the READING picks between them (風 is wind as
    かぜ and a swing as ふり), preferring the entry that reads that way FIRST —
    its primary reading — over one that merely lists it among alternates."""
    recs = _gloss.get(head)
    if not recs:
        return ''
    if isinstance(recs, list) and recs and isinstance(recs[0], str):
        return recs[0]                     # pre-2026-07-29 map: no readings
    # a common word this reading is the MAIN reading of, then a common word
    # that merely lists it, then the rare ones. Primary-reading alone promoted
    # 読過 ("reading through") over 何処か for どっか — the reading is exclusive
    # to the obscure word precisely BECAUSE it is obscure.
    if reading:
        for want_pri, want_primary in ((1, 1), (1, 0), (0, 1), (0, 0)):
            for r in recs:
                rs = r.get('r') or []
                if bool(r.get('p')) != bool(want_pri):
                    continue
                if reading in (rs[:1] if want_primary else rs):
                    return (r.get('g') or [''])[0]
    if require_reading:
        return ''
    return (recs[0].get('g') or [''])[0]


def gloss_of(surface, lemma='', reading=''):
    """Display gloss from the distilled JMdict map. The map drafts; review
    approves — but it used to draft nonsense on the commonest words, because a
    surface alone cannot pick a sense (こと was "koto, the 13-stringed zither",
    この was "nine", いる was "to enter"). Two disambiguators now:

      the reading    風 wind vs swing, 空 sky vs empty  (see _sense)
      the lemma      a KANA surface is where the homograph pile-ups live, and
                     UniDic has already read the sentence: this こと is 事, this
                     いる is 居る. Used ONLY when the lemma still reads the way
                     the surface does — otherwise an inflected form drags in a
                     stranger (ような -> 酔う "to get drunk", どっか ->
                     "finishing reading"), which is worse than the bare lookup.

    Everything here drafts. The author rewrites these in the house voice."""
    global _gloss
    if _gloss is None:
        with gzip.open(GLOSS_GZ, 'rt', encoding='utf-8') as f:
            _gloss = json.load(f)
    if (surface and PURE_KANA.fullmatch(surface) and lemma and lemma != surface
            and reading):
        g = _sense(lemma, reading, require_reading=True)
        if g:
            return g
    for k, r in ((surface, reading), (lemma, '')):
        if k:
            g = _sense(k, r)
            if g:
                return g
    return ''


_KANJI_DIGITS = '〇一二三四五六七八九'


def _int_to_kanji(n):
    """123 -> 百二十三. Positional, 1-9999 — enough for anything a lyric counts."""
    if n == 0:
        return '〇'
    if n > 9999:
        return ''.join(_KANJI_DIGITS[int(d)] for d in str(n))
    out = ''
    for val, unit in ((1000, '千'), (100, '百'), (10, '十')):
        d, n = divmod(n, val)
        if d:
            out += ('' if d == 1 else _KANJI_DIGITS[d]) + unit
    return out + (_KANJI_DIGITS[n] if n else '')


def _digits_to_kanji(s):
    return re.sub(r'\d+', lambda m: _int_to_kanji(int(m.group())), s)


def numeral_reading(tagger, unit):
    """Reading for a unit written with ARABIC digits, via the kanji numeral form.

    No tokenizer reads 2人 — fugashi hands back ('2', no reading) + ('人', ニン)
    — so this used to return '' and the card shipped with no reading and NO
    ROMAJI. That is not cosmetic: the JP clip filename is built from the romaji,
    so four 2人 cards all became jp/word_s<n>_.mp3 with an empty slot and every
    one of them failed to render (mariigoorudo, 2026-07-29, 4 of 894 clips).

    Rewriting the digits as a kanji numeral hands the counter to a dictionary
    that knows it, irregulars included: 二人 → ふたり (not ににん), 十日 → とおか,
    二十歳 → はたち. The whole-unit JMdict reading comes first because the
    tokenizer splits some compounds and then mis-reads them (二日 → フタ+カ
    instead of ふつか); per-token concatenation is the fallback."""
    import jaconv
    kanji = _digits_to_kanji(unit)
    if kanji == unit:
        return ''
    global _gloss
    if _gloss is None:
        with gzip.open(GLOSS_GZ, 'rt', encoding='utf-8') as f:
            _gloss = json.load(f)
    for rec in (_gloss.get(kanji) or []):
        for r in (rec.get('r') or []):
            if PURE_KANA.fullmatch(r):
                return jaconv.kata2hira(r)
    parts = []
    for t in tagger(kanji):
        kana = getattr(t.feature, 'kana', None) or (
            t.surface if PURE_KANA.fullmatch(t.surface) else '')
        if not (kana and PURE_KANA.fullmatch(kana)):
            return ''
        parts.append(jaconv.kata2hira(kana))
    return ''.join(parts)


def unit_reading(tagger, unit):
    """Orthographic hiragana reading of one coalesced unit (fugashi kana
    field keeps ヅ/ウ; kana surfaces copy literally — same priorities as the
    kana-line composer in content_to_data)."""
    import jaconv
    if PURE_KANA.fullmatch(unit):
        return jaconv.kata2hira(unit)
    parts = []
    for t in tagger(unit):
        kana = getattr(t.feature, 'kana', None)
        if kana and PURE_KANA.fullmatch(kana):
            parts.append(jaconv.kata2hira(kana))
        elif PURE_KANA.fullmatch(t.surface):
            parts.append(jaconv.kata2hira(t.surface))
        else:
            return numeral_reading(tagger, unit)   # digits: read the kanji form
    return ''.join(parts)


def unit_lemma(tagger, unit):
    toks = list(tagger(unit))
    if len(toks) == 1:
        return getattr(toks[0].feature, 'lemma', '') or ''
    if toks:
        return getattr(toks[0].feature, 'orthBase', '') or ''
    return ''


def is_lone_particle(tagger, unit):
    toks = list(tagger(unit))
    return len(toks) == 1 and getattr(toks[0].feature, 'pos1', '') == '助詞'


def unit_rom(reading):
    """Card-style Hepburn from a hiragana reading: mora roms joined, then
    sokuon fixed (jaconv spells っ as 'xtsu' — gemination doubles the next
    consonant instead: しまった -> shimatta; word-final っ drops)."""
    rom = ''.join(mora_rom(m) for m in moraize(reading))
    rom = re.sub(r'xtsu(ch)', r'tch', rom)          # っち -> tchi
    rom = re.sub(r'xtsu([bcdfghjklmnpqrstvwz])', r'\1\1', rom)
    return rom.replace('xtsu', '')


def join_broken_words(units):
    """Glue a unit back onto the next one when the piece alone is not a word and
    the pair is. The gate's coalescer cut ぎゅっと into ぎゅっ + と (it reads the
    と as a particle), and ぎゅっ is not a word in any dictionary: no human
    recording exists for it, so the card fell through to TTS, which read it
    "nyu" — the one clip in マリーゴールド that could not be rendered at all.
    そっと survives only because it has no particle-shaped tail.

    Also glue a word to an identical twin next to it when the doubled form is
    its own dictionary word. Both halves being real words defeats the rule
    above, and the halves can mean something else entirely: バイバイ ("bye
    bye") came apart into バイ + バイ, and the card the singer's goodbye
    produced was glossed "Japanese ivory shell" (STRAWBERRY ANNIVERSARY,
    2026-07-30). Same shape as どきどき, いろいろ, わくわく. Two of the same
    word in a row that do NOT make a word — おしまい おしまい in this very
    song — fail the dictionary test and stay two cards.

    Units arrive with their spacing still attached (units_of hands back 'バイ'
    then 'バイ　'), so the twin test compares the stripped forms — but only
    when the first unit has nothing after it. A space between them means the
    lyric put the two words apart, and words the singer separated are not one
    word. Interior punctuation is left alone: the ・ in グランド・フィナーレ
    is part of the word, which is why the first rule still tests the units
    exactly as they came."""
    out = []
    i = 0
    while i < len(units):
        u = units[i]
        nxt = units[i + 1] if i + 1 < len(units) else None
        twin = (nxt is not None and u == u.rstrip(WS) and u.strip(PUNCT)
                and u.strip(PUNCT) == nxt.strip(PUNCT)
                and vseg.is_word(u.strip(PUNCT) * 2))
        if nxt is not None and (twin or (not vseg.is_word(u)
                                         and vseg.is_word(u + nxt))):
            out.append(u + nxt)
            i += 2
            continue
        out.append(u)
        i += 1
    return out


def split_stray_particles(tagger, unit):
    """The gate's coalescer sometimes glues a particle onto a following word
    it has no rule for (が + いつ -> がいつ via the fallback branch). Such a
    unit PASSES the gate but is a wrong card. When a multi-token unit is not
    itself a JMdict headword, peel leading/trailing lone-particle tokens off
    into their own cards and keep the content core."""
    toks = list(tagger(unit))
    if len(toks) < 2 or vseg.is_word(unit):
        return [unit]
    surfs = [t.surface for t in toks]
    # A true standalone particle is 助詞 but NOT 接続助詞 — the て in 行って
    # is inflection glue and must stay on its stem (peeling it minted bare
    # 行っ/見 stem cards on kaijuu, 2026-07-12). Same distinction the gate's
    # own is_particle rule draws.
    stray = [getattr(t.feature, 'pos1', '') == '助詞'
             and getattr(t.feature, 'pos2', '') != '接続助詞' for t in toks]
    lo, hi = 0, len(toks)
    out = []
    while lo < hi and stray[lo]:
        out.append(surfs[lo])
        lo += 1
    tail = []
    while hi > lo and stray[hi - 1]:
        tail.insert(0, surfs[hi - 1])
        hi -= 1
    core = ''.join(surfs[lo:hi])
    return out + ([core] if core else []) + tail


def sung_end(ln):
    """Where the VOICE stops on this line. A line-level sheet (LRC, NetEase)
    chains end_ms to the next line's begin, so end_ms says nothing at all about
    silence — every gap computed from it is 0 and a whole song reads as one
    unbroken block. whisper_sync measures the real thing and writes
    sung_end_ms; fall back to end_ms only when nothing has aligned this song."""
    return int(ln.get('sung_end_ms') or ln.get('end_ms') or 0)


def repeat_bounds(texts, min_run=4):
    """Line indices where a REPEATED block starts or ends — the chorus, mostly.

    Silence alone cannot find a chorus: singers breathe mid-verse and run
    straight from a verse into the hook. Repetition can. Only maximal runs
    count (a run whose left neighbours also match is the tail of a longer one,
    not a boundary), so a repeated 8-line chorus contributes exactly two
    boundaries instead of one at every offset inside it."""
    n = len(texts)
    bounds = set()
    for i in range(n):
        for j in range(i + min_run, n):
            if texts[i] != texts[j]:
                continue
            if i and j and texts[i - 1] == texts[j - 1]:
                continue                      # not the start of the repeat
            run = 0
            while j + run < n and i + run < j and texts[i + run] == texts[j + run]:
                run += 1
            if run >= min_run:
                bounds.update({i, j, i + run, j + run})
    return {b for b in bounds if 0 < b < n}


def sectionize(lines):
    """Blocks a person would recognize as verse / chorus / bridge.

    Three signals, in order: real silence (from sung_end, never end_ms),
    repetition (where the chorus starts and stops), then shape — nothing
    longer than MAX_LINES (split at its biggest internal gap) and nothing
    shorter than MIN_LINES (folded into whichever neighbour it is closer to).
    The old gap-only version cut a section wherever a singer took a breath and
    handed マリーゴールド nine sections of 1,1,4,12,17,1,12,1,1 lines.
    Returns [(section_id, [line_idx, ...]), ...]."""
    n = len(lines)
    if n <= MIN_LINES:
        return [('s1', list(range(n)))]
    gaps = [0] + [int(lines[i]['begin_ms']) - sung_end(lines[i - 1])
                  for i in range(1, n)]
    cuts = {i for i in range(1, n) if gaps[i] >= SECTION_GAP_MS}
    cuts |= repeat_bounds([(ln.get('text') or '').strip() for ln in lines])

    def blocks_of(cutset):
        out, cur = [], []
        for i in range(n):
            if i in cutset and cur:
                out.append(cur)
                cur = []
            cur.append(i)
        if cur:
            out.append(cur)
        return out

    # too long: split at the biggest silence inside, until every block fits
    blocks = blocks_of(cuts)
    while True:
        for b in blocks:
            # only cuts that leave BOTH halves a real section — splitting at the
            # biggest gap regardless just makes an orphan line the merge pass
            # folds straight back, and the block stays over length forever
            inner = b[MIN_LINES:len(b) - MIN_LINES + 1]
            if len(b) > MAX_LINES and inner:
                cuts.add(max(inner, key=lambda i: gaps[i]))
                break
        else:
            break
        blocks = blocks_of(cuts)

    # too short: fold into the neighbour it is closer to (smaller silence)
    while len(blocks) > 1:
        short = next((k for k, b in enumerate(blocks) if len(b) < MIN_LINES), None)
        if short is None:
            break
        before = gaps[blocks[short][0]] if short > 0 else None
        after = (gaps[blocks[short + 1][0]] if short + 1 < len(blocks) else None)
        back = after is None or (before is not None and before <= after)
        tgt = short - 1 if back else short + 1
        blocks[tgt] = sorted(blocks[tgt] + blocks[short])
        blocks.pop(short)
    return [(f's{k + 1}', idxs) for k, idxs in enumerate(blocks)]


def build(key, force=False, song_number=0):
    st = json.loads((BUILDS / f'{key}.build_state.json').read_text())
    meta = st['meta']
    lyr = json.loads((BUILDS / f'{key}.lyrics.json').read_text())
    dst = BUILDS / f'{key}.content.json'
    if dst.exists() and not force:
        sys.exit(f'{dst} already exists — an authored content.json is a human '
                 f'artifact. Re-run with --force only if it is a stale scaffold.')

    tagger = vseg.load_tagger()
    tlines = [ln for ln in lyr['lines'] if strip_echo(ln.get('text') or '').strip()]

    # ---- sections + lines ---------------------------------------------------
    sec_blocks = sectionize(tlines)
    sec_of_line = {}
    for sid, idxs in sec_blocks:
        for i in idxs:
            sec_of_line[i] = sid
    lines = [{'jp': strip_echo(tl['text']), 'section': sec_of_line[i]}
             for i, tl in enumerate(tlines)]

    # ---- words: segment every line with the GATE'S OWN splitter -------------
    words, failures = [], []
    seen_by_sec = {}
    rom_seen = {}
    for i, tl in enumerate(tlines):
        text = strip_echo(tl['text'])
        sid = sec_of_line[i]
        if not CJK.search(text):
            continue                        # pure-Latin line: no JP cards
        for raw_unit in join_broken_words(vseg.units_of(tagger, text)):
            for unit in split_stray_particles(tagger, raw_unit.strip(PUNCT)):
                # 「もう離れないで」と minted a card spelled 離れないで」 — a
                # closing quote is not part of the word, and it poisons the
                # reading, the romaji and the clip filename downstream.
                unit = unit.strip(PUNCT)
                if not unit or LATIN.fullmatch(unit) or not CJK.search(unit):
                    continue
                if re.fullmatch(r'[0-9０-９]+', unit):
                    continue                # digit runs are coverage exceptions
                if unit in seen_by_sec.setdefault(sid, set()):
                    continue
                seen_by_sec[sid].add(unit)
                merged, reason, _units = vseg.analyze_card(tagger, unit)
                if merged:
                    failures.append(f'{sid} {unit!r}: {reason}')
                    continue
                reading = unit_reading(tagger, unit)
                lemma = unit_lemma(tagger, unit)
                # closed-class table beats an isolated re-tag (a lone が
                # re-tags as 接続詞 out of context and loses its flag)
                particle = (unit in PARTICLE_GLOSS
                            or is_lone_particle(tagger, unit))
                # jp_speak is the string the VOICE gets, and the key the human
                # clip dictionary is looked up by. Leaving kanji in it made
                # every kanji card a dictionary miss: gen_audio searched NHK for
                # 今日 spelled 今日, missed, fell back to Kokoro, and Kokoro read
                # it "iyo" — nine common words in マリーゴールド (君 夏 日 光 奥
                # 今日 日々 遠い) blocked the audio step that way on 2026-07-29,
                # every one of them sitting in the dictionary under its reading.
                # Contract §2.4: when the written form misleads, carry the
                # pronounced one.
                # (kanji only — a katakana loanword already spells its sound,
                # and Kokoro reads マリーゴールド better than まりーごーるど)
                jp_speak = reading if (reading and KANJI.search(unit)) else unit
                if particle and unit in PARTICLE_SPOKEN:
                    jp_speak = PARTICLE_SPOKEN[unit]      # E8
                # rom follows the SPOKEN form (contract §2.3: jp は carries
                # rom "wa"), else the orthographic reading.
                rom_src = jp_speak if jp_speak != unit else (reading or unit)
                if PURE_KANA.fullmatch(rom_src):
                    import jaconv
                    rom = unit_rom(jaconv.kata2hira(rom_src))
                elif reading:
                    rom = unit_rom(reading)
                else:
                    rom = ''
                if particle or unit in PARTICLE_GLOSS:
                    en = PARTICLE_GLOSS.get(unit, '(particle)')
                else:
                    en = gloss_of(unit, lemma, reading)
                w = {'jp': unit, 'rom': rom, 'jp_speak': jp_speak,
                     'en': en, 'en_speak': '', 'context': '',
                     'gloss': '', 'hint': '', 'particle': bool(particle),
                     'section': sid}
                rk = (sid, rom or unit)
                if rk in rom_seen:
                    rom_seen[rk] += 1
                    w['uid'] = f'{rom or "w"}{rom_seen[rk]}'
                else:
                    rom_seen[rk] = 1
                words.append(w)

    if failures:
        sys.exit('scaffold REFUSED — the gate would fail these cards '
                 '(gate-clean by construction means zero):\n  ' +
                 '\n  '.join(failures))

    # ---- the creative worklist ----------------------------------------------
    uniq = list(dict.fromkeys(ln['jp'] for ln in lines))
    line_tr = {t: '' for t in uniq}
    line_explain = {t: '' for t in uniq}

    content = {
        'song_number': song_number,
        'title_jp': meta.get('title_jp', ''), 'title_en': meta.get('title_en', ''),
        'artist': meta.get('artist', ''), 'artist_en': meta.get('artist_en', ''),
        'youtube_id': meta.get('yt', ''), 'level': meta.get('level', 'Intermediate'),
        'art': meta.get('art', ''), 'apple': meta.get('apple', ''),
        'lines': lines,
        'sections': [{'id': sid, 'name': '', 'short_name': sid.upper(),
                      'subtitle': '', 'description': '', 'speak_en': '',
                      'note': ''} for sid, _ in sec_blocks],
        'words': words,
        'line_tr': line_tr, 'line_explain': line_explain,
        'trivia': [], 'grammar': [], 'coverage_exceptions': [],
        'podcast_script': [],
        '_scaffold': {
            'version': 1, 'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
            'generator': 'scaffold.py (four-verbs 1.3)',
            'stats': {'lines': len(lines), 'sections': len(sec_blocks),
                      'words': len(words),
                      'glossed': sum(1 for w in words if w['en']),
                      'creative_todo': {'line_tr': len(uniq),
                                        'line_explain': len(uniq),
                                        'en_speak': len(words)}},
            'note': 'machine skeleton — every empty field is the creative '
                    'residue a human or AI drafter fills; review flips '
                    'author_data, never this file existing.'},
    }
    dst.write_text(json.dumps(content, ensure_ascii=False, indent=2))
    s = content['_scaffold']['stats']
    print(f"scaffold -> {dst.relative_to(ROOT)}")
    print(f"  {s['lines']} lines · {s['sections']} sections · {s['words']} cards "
          f"({s['glossed']} glossed, {s['words'] - s['glossed']} need a gloss)")
    print(f"  every card re-checked with the segmentation gate: 0 failures")
    print(f"  creative residue: {s['creative_todo']['line_tr']} translations, "
          f"{s['creative_todo']['line_explain']} explainers, "
          f"{s['creative_todo']['en_speak']} spoken definitions, "
          f"section names/intros, trivia, grammar, podcast")
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('key')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--song-number', type=int, default=0)
    a = ap.parse_args()
    build(a.key, force=a.force, song_number=a.song_number)


if __name__ == '__main__':
    main()
