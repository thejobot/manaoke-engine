#!/usr/bin/env python3
"""
content_to_data.py — the deterministic assembler.

Takes the authored study CONTENT (builds/<key>.content.json) + the licensed
timed LYRICS (builds/<key>.lyrics.json, an Apple-Music TTML export via LyriCool)
and produces, to exact SONG-CONTRACT shape:
  songs/<slug>/data.json          (16 top-level keys incl. apple_lyrics + kana_timings)
  songs/<slug>/tts_manifest.json  (5 clip classes)
  builds/<key>.line_maps.json     (LINE_TR / LINE_EXPLAIN dicts to splice into index.html)
  builds/<key>.audio_jobs.json    ([lang, spoken_text, out_filename] for the audio step)

Per-mora kana_timings are derived from the licensed word-level timing: each timed
word is read with pyopenjtalk, split into morae, and its window distributed evenly.
English tokens pass through as single latin morae (no katakana transliteration).

Run with the parler python (needs pyopenjtalk, jaconv):
  /opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python content_to_data.py <key> <slug>
"""
import hashlib, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SONGS = ROOT / 'songs'
BUILDS = Path(__file__).resolve().parent / 'builds'

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import timing_overrides   # manual-edit sidecar (stdlib-only)

CJK = re.compile(r'[぀-ヿ㐀-鿿ｦ-ﾟ]')
# combine with preceding kana into one mora: yōon ゃゅょ AND small vowels
# ぁぃぅぇぉゎ (both scripts) — ウェイヴ is うぇ|い|ゔ, not う|ぇ|い|ゔ.
# bench_align/score.py _FOLD is a hardcoded twin of this set; update together.
SMALL = set('ゃゅょャュョぁぃぅぇぉゎァィゥェォヮ')
KANA = re.compile(r'[぀-ヿ]')
PURE_KANA = re.compile(r'^[぀-ゟ゠-ヿー]+$')

try:
    import pyopenjtalk, jaconv
    HAVE_JA = True
except Exception:
    HAVE_JA = False

# fugashi + unidic_lite: the context-fed reading source (tokenizes the FULL
# line; per-morpheme lexical reading preserves づ/ぢ orthography, pron says
# ワ for particle は). Optional: absent -> per-token g2p fallback (the old
# behavior), and the validate gate catches what that gets wrong.
try:
    import fugashi
    TAGGER = fugashi.Tagger()
except Exception:
    TAGGER = None

# Particle surface -> spoken romaji (display keeps the literal text kana;
# the romaji line must still say wa/e/o).
PARTICLE_ROM = {'は': 'wa', 'へ': 'e', 'を': 'o'}
# Hepburn for rendaku kana — jaconv romanizes づ/ぢ as du/di.
ROM_FIX = {'づ': 'zu', 'ぢ': 'ji', 'ヅ': 'zu', 'ヂ': 'ji'}


def kana_fold(s):
    """Comparison fold: hiragana-ize + collapse the zu/ji spelling split.
    づ vs ず (ぢ vs じ) are the same SOUND — reading agreement is judged on
    sounds; spelling is settled separately (text/orthography wins)."""
    h = jaconv.kata2hira(str(s or ''))
    return h.replace('づ', 'ず').replace('ぢ', 'じ')


def rom_uid(rom):
    return re.sub(r'^-+|-+$', '', str(rom or '').replace(' ', '-').replace('·', '').replace('/', '_'))


def line_tr_key(s):
    return re.sub(r'\s+', '', re.sub(r'\s*\(×\d+\)\s*$', '', str(s or ''))).strip()


def strip_echo(text):
    """Drop a trailing ' (backing vocal)' parenthetical for a clean displayed line."""
    return re.sub(r'\s*[\(（][^)）]*[\)）]\s*$', '', text).strip()


def moraize(kana_str):
    out = []
    for ch in kana_str:
        if ch in SMALL and out:
            out[-1] += ch
        elif ch == 'ー' and out:
            out[-1] += ch
        else:
            out.append(ch)
    return out


def mora_rom(hira):
    # Hepburn: romanize づ/ぢ as zu/ji (jaconv says du/di). Display keeps the
    # literal kana; only the romanization input is folded.
    if hira and any(c in ROM_FIX for c in hira):
        hira = hira.translate(str.maketrans('づぢヅヂ', 'ずじズジ'))
    try:
        r = jaconv.kana2alphabet(jaconv.hira2kata(hira).translate(str.maketrans('', '')))
    except Exception:
        r = ''
    # jaconv wants hiragana for kana2alphabet
    try:
        r = jaconv.kana2alphabet(hira)
    except Exception:
        pass
    return r or hira


def reading_hira(text):
    if not HAVE_JA:
        return ''
    kata = pyopenjtalk.g2p(text, kana=True)
    return jaconv.kata2hira(kata)


def _morpheme_units(text):
    """fugashi over the FULL line -> [(start, end, reading_hira, rom_hint)]
    units in text order, one per morpheme. Reading priority per morpheme:
      literal kana surface (text-first — the rendaku/spelling arbiter)
      > UniDic lexical reading `kana` (orthographic: keeps ヅ, keeps ウ)
      > pyopenjtalk on the isolated surface (digits, unknowns).
    rom_hint is set only for particles は/へ/を: display keeps the literal
    kana, the romaji says wa/e/o (UniDic pron agrees). Punctuation/symbol
    morphemes carry an empty reading (never spoken). Returns None when
    fugashi is unavailable or the walk loses text alignment."""
    if TAGGER is None:
        return None
    units, pos = [], 0
    try:
        toks = list(TAGGER(text))
    except Exception:
        return None
    for t in toks:
        surf = t.surface
        if not surf:
            continue
        start = text.find(surf, pos)
        if start < 0:
            return None                     # lost alignment — refuse to guess
        end = start + len(surf)
        pos = end
        f = t.feature
        pos1 = getattr(f, 'pos1', '') or ''
        if pos1 in ('補助記号', '記号', '空白'):
            reading, hint = '', None
        elif PURE_KANA.fullmatch(surf):
            reading = jaconv.kata2hira(surf)
            hint = PARTICLE_ROM.get(surf) if pos1 == '助詞' else None
        else:
            kana = getattr(f, 'kana', None)
            if kana and PURE_KANA.fullmatch(kana):
                reading = jaconv.kata2hira(kana)
            else:
                reading = reading_hira(surf)   # digits / dictionary misses
            hint = None
        units.append([start, end, reading, hint])
    return units


def _card_kana(w):
    """A study card's kana reading, when the card actually carries one:
    jp pure kana -> jp; jp_speak differing from jp and pure kana -> jp_speak
    (the 2026-07-11 census: zero exceptions across 577 words). Cards where
    jp_speak == jp carry no kana — the default reading is declared correct."""
    jp = str(w.get('jp') or '')
    if PURE_KANA.fullmatch(jp):
        return jaconv.kata2hira(jp)
    speak = str(w.get('jp_speak') or '')
    if speak and speak != jp and PURE_KANA.fullmatch(speak):
        return jaconv.kata2hira(speak)
    return None


def _norm_line_key(s):
    return re.sub(r'\s+', '', re.sub(r'\s*\(×\d+\)\s*$', '', str(s or ''))).strip()


def line_reading_plan(text, src_words, cards):
    """Context-fed reading plan for one line: {word_index: (reading, rom_hints)}.

    1. fugashi units over the FULL line (never an isolated fragment — the
       きゅーみのびにわははさんと class was Apple's sub-word splits each fed
       to g2p alone).
    2. Card reconciliation: a study card whose surface sits in the line and
       whose span exactly covers a run of units replaces that run's reading —
       the card is the arbiter of SOUNDS (かあさん over ははさん, あした over
       あす). Spelling stays orthographic: when card and derivation agree
       modulo the zu/ji fold, the derived spelling (きづいた) is kept.
    3. Slice units into the Apple word windows by start-char (a unit whose
       surface straddles a window boundary rides the window it starts in —
       readings-only; word timing windows are untouched).

    rom_hints: {mora_index: rom} per word (particle wa/e/o). Returns None
    when fugashi is unavailable — caller falls back to the old per-token path.
    """
    units = _morpheme_units(text)
    if units is None:
        return None
    # --- card reconciliation (longest surface first, ws-tolerant not needed:
    # card jp is matched against the same text the units cover) -------------
    line_key = _norm_line_key(text)
    for w in cards:
        jp, kana = w['_jp'], w['_kana']
        if w.get('only_lines') and line_key not in w['_only_keys']:
            continue
        start = 0
        while True:
            s = text.find(jp, start)
            if s < 0:
                break
            e = s + len(jp)
            run = [u for u in units if u[0] >= s and u[1] <= e and u[2] != '']
            covered = ''.join(text[u[0]:u[1]] for u in run)
            if run and covered == jp:
                derived = ''.join(u[2] for u in run)
                if kana_fold(derived) != kana_fold(kana):
                    # sounds differ -> the card wins as ONE unit spanning the
                    # whole surface; the tail units disappear (a window whose
                    # text a wider unit consumed merges into that unit's
                    # segment below — it must never re-read itself).
                    run[0][1], run[0][2], run[0][3] = run[-1][1], kana, None
                    for u in run[1:]:
                        units.remove(u)
            start = e
    # --- slice into Apple word windows by start-char ------------------------
    # A window where NO unit starts had its text consumed by a unit that
    # started earlier (a card spanning 母|さん, a morpheme straddling 汚|れた,
    # ピンサロ split ピン|サロ): that window MERGES into the earlier segment —
    # it must never fall back and re-read its own fragment (the かあさんさん
    # doubling found in the first regeneration pass).
    spans, cur = [], 0
    for w in src_words:
        wt = strip_echo(w['text'])
        while cur < len(text) and text[cur].isspace():
            cur += 1
        if not wt or text[cur:cur + len(wt)] != wt:
            return None                     # tokens must compose the text
        spans.append((cur, cur + len(wt)))
        cur += len(wt)
    plan, merged = {}, set()
    head = None
    for wi, (s, e) in enumerate(spans):
        parts = [u for u in units if s <= u[0] < e]
        if not parts and head is not None:
            merged.add(wi)
            plan[head]['end_wi'] = wi       # earlier segment absorbs this window
            continue
        reading = ''.join(u[2] for u in parts)
        hints, mi = {}, 0
        for u in parts:
            morae = moraize(u[2])
            if u[3] and len(morae) == 1:
                hints[mi] = u[3]
            mi += len(morae)
        plan[wi] = {'reading': reading, 'hints': hints, 'end_wi': wi}
        head = wi
    return plan, merged


# How builds/<key>.mora_timings.json (whisper_sync --morae, CTC per line) is
# consumed. Benchmarked 2026-07-07 (bench_align/results/MORA-RESULTS.md):
#   hybrid  (DEFAULT) each word's FIRST mora keeps the shipped word onset,
#           interior morae take the CTC times clamped inside the word window,
#           spans contiguous. Kills CTC's line-final single-mora drag while
#           keeping the interior-mora win (inochi PCO@0.2 89.6% vs even 84.2%).
#   ctc     raw CTC mora times (video-clock, may cross word boundaries) —
#           best MedAE, fatter tail; for benching.
#   even    ignore the mora file entirely (the legacy division; also what any
#           word falls back to on a kana mismatch).
MORA_MODE = os.environ.get('MANAOKE_MORA_MODE', 'hybrid')


def hybrid_mora_times(entries, begin, end):
    """The shipped hybrid: [(begin_ms, end_ms)] per mora for one word window.
    First mora pinned to the shipped word onset `begin`; interior begins are
    the CTC entries clamped into [begin, end] and forced monotonic; ends are
    contiguous (end_i = begin_{i+1}), last end = the shipped word end. The
    result occupies exactly [begin, end] like even division — safe for the
    template's fill contract — with CTC-informed interior boundaries."""
    begins = [float(begin)]
    for e in entries[1:]:
        begins.append(min(max(float(e['begin_ms']), begins[-1]), float(end)))
    ends = begins[1:] + [max(float(end), begins[-1])]
    return [(int(round(b)), int(round(en))) for b, en in zip(begins, ends)]


def timed_morae(word_text, begin, end, mora_entries=None, reading=None,
                hold_ms=None, rom_hints=None):
    """Return [{kana,rom,begin_ms,end_ms}] for one licensed word window.

    mora_entries (optional) = this word's group from builds/<key>.mora_timings
    .json (whisper_sync --morae: per-line windowed CTC mora alignment). It is
    trusted ONLY when its kana sequence equals this word's moraized reading
    exactly — same moraize() on both sides, so a match means same lyrics, same
    segmentation. On ANY mismatch (stale file, changed reading, different word
    split) the shipped even division below runs unchanged — assembly never
    breaks on a bad mora file. How trusted entries are used = MORA_MODE
    (hybrid default / ctc / even; see above).

    reading (optional) = a human kana override for this token (timing_edit
    wordedit --reading) — trusted over pykakasi's guess.
    hold_ms (optional) = the human-marked held-vowel point (timing_edit hold):
    the lexical morae pack into [begin, hold_ms] and the FINAL mora stretches
    to the word end — the sung vowel extension (ほど…おおお) the page's
    karaoke fill then holds. A human hold beats CTC entries."""
    if MORA_MODE == 'even':
        mora_entries = None
    if reading or CJK.search(word_text):
        hira = reading if reading else reading_hira(word_text)
        morae = moraize(hira) or [word_text]
        # rom per mora: the particle hint (は spoken wa) beats the romanizer
        roms = [(rom_hints or {}).get(i) or mora_rom(m)
                for i, m in enumerate(morae)]
        if hold_ms is not None and begin < int(hold_ms) < end:
            hold = int(hold_ms)
            n = len(morae)
            step = (hold - begin) / max(n, 1)
            out = [{'kana': m, 'rom': roms[i],
                    'begin_ms': int(round(begin + i * step)),
                    'end_ms': int(round(begin + (i + 1) * step))}
                   for i, m in enumerate(morae)]
            out[-1]['end_ms'] = int(end)         # the held vowel sings on
            return out
        if mora_entries and [e.get('kana') for e in mora_entries] == morae:
            if MORA_MODE == 'ctc':
                return [{'kana': m, 'rom': roms[i],
                         'begin_ms': int(round(e['begin_ms'])),
                         'end_ms': int(round(max(e['end_ms'], e['begin_ms'])))}
                        for i, (m, e) in enumerate(zip(morae, mora_entries))]
            times = hybrid_mora_times(mora_entries, begin, end)
            return [{'kana': m, 'rom': roms[i], 'begin_ms': b, 'end_ms': en}
                    for i, (m, (b, en)) in enumerate(zip(morae, times))]
    else:
        # english / latin / digits — one token, shown as-is (no katakana-izing)
        # hybrid keeps the shipped word window for single-token latin words
        # (CTC's worst outliers were EN interjections — byebye/Oh/No).
        if MORA_MODE == 'ctc' and mora_entries and len(mora_entries) == 1 \
                and mora_entries[0].get('kana') == word_text:
            e = mora_entries[0]
            return [{'kana': word_text, 'rom': word_text,
                     'begin_ms': int(round(e['begin_ms'])),
                     'end_ms': int(round(max(e['end_ms'], e['begin_ms'])))}]
        return [{'kana': word_text, 'rom': word_text, 'begin_ms': int(begin), 'end_ms': int(end)}]
    n = len(morae)
    step = (end - begin) / max(n, 1)
    out = []
    for i, m in enumerate(morae):
        out.append({'kana': m, 'rom': roms[i],
                    'begin_ms': int(round(begin + i * step)),
                    'end_ms': int(round(begin + (i + 1) * step))})
    return out


def load_mora_timings(key, lines):
    """builds/<key>.mora_timings.json -> {line_idx: {word_i: [entries]}} with
    each entry clamped inside its line's [begin_ms, end_ms] and forced
    monotonic. Missing file (the normal case until whisper_sync --morae has
    run) -> {}; any unreadable shape -> {} (assembly falls back to even)."""
    p = BUILDS / f'{key}.mora_timings.json'
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
        out = {}
        for k, entries in raw.items():
            i = int(k)
            if not (0 <= i < len(lines)):
                continue
            lo, hi = int(lines[i]['begin_ms']), int(lines[i]['end_ms'])
            prev = lo
            by_word = {}
            for e in entries:
                b = min(max(float(e['begin_ms']), prev), hi)
                en = min(max(float(e['end_ms']), b), hi)
                prev = b
                by_word.setdefault(int(e['word_i']), []).append(
                    {'kana': e.get('kana'), 'begin_ms': b, 'end_ms': en})
            out[i] = by_word
        return out
    except Exception as ex:
        print(f'[mora] ignoring unreadable {p.name}: {ex}', file=sys.stderr)
        return {}


def build(key, slug):
    content = json.loads((BUILDS / f'{key}.content.json').read_text())
    lyr = json.loads((BUILDS / f'{key}.lyrics.json').read_text())
    # Belt-and-suspenders: re-apply the manual-edit sidecar over the lyric
    # grid BEFORE kana_timings are derived. lyrics.json is normally already
    # post-merge (every writer re-applies at its output edge), so this is an
    # idempotent no-op — it only matters when some writer skipped the sidecar.
    # Composes with MORA_MODE hybrid: load_mora_timings clamps into the
    # (possibly overridden) line windows and hybrid first-mora pinning reads
    # the (possibly overridden) word onsets below.
    _, _n_over, _over_orph = timing_overrides.apply(lyr, key)
    if _n_over or _over_orph:
        print(f'overrides: {_n_over} re-applied, {len(_over_orph)} orphaned')
    meta = content
    tlines = lyr['lines']
    clines = content['lines']
    # CTC mora timing (whisper_sync --morae), consumed per word when its kana
    # sequence matches this word's moraized reading; {} when absent.
    mora_map = load_mora_timings(key, tlines)
    # Reconciliation cards for the line-reading plan: every study word that
    # actually CARRIES a kana reading (the cards are the arbiter of sounds —
    # かあさん over ははさん). Longest surface first so 母さん beats 母.
    recon_cards = []
    for w in (content.get('words') or []) + [w for s in content.get('sections', [])
                                             for w in (s.get('words') or [])]:
        jp = str(w.get('jp') or '')
        if not jp or not CJK.search(jp) or PURE_KANA.fullmatch(jp):
            continue
        kana = _card_kana(w)
        if not kana:
            continue
        recon_cards.append({'_jp': jp, '_kana': kana,
                            'only_lines': w.get('only_lines'),
                            '_only_keys': {_norm_line_key(x)
                                           for x in (w.get('only_lines') or [])}})
    recon_cards.sort(key=lambda w: -len(w['_jp']))
    # pair licensed timed lines with authored lines by index (authored used exact order)
    n = min(len(tlines), len(clines))

    apple_lines = []
    for i in range(len(tlines)):
        tl = tlines[i]
        cl = clines[i] if i < len(clines) else {}
        text = strip_echo(tl['text'])
        words_coarse = [dict({'text': w['text'], 'begin_ms': int(w['begin_ms']),
                              'end_ms': int(w['end_ms'])},
                             **{k: w[k] for k in ('hold_ms', 'kana') if w.get(k)})
                        for w in (tl.get('words') or []) if strip_echo(w['text']).strip()]
        # Echo tail: strip_echo removed a trailing "(backing vocal)" repeat from
        # the DISPLAY text, but Apple's words[] still carries those echo words.
        # Keeping them poisons everything downstream that assumes words == text:
        # the page grows phantom tokens, the romaji timeForRange borrows echo
        # timing, and kana_timings gains echo morae so the romaji fill can never
        # complete the visible line (validate_song E10 now gates this). Walk the
        # stripped text and keep only the words that actually compose it.
        _cur = 0
        _kept = []
        for w in words_coarse:
            wt = w['text']
            while _cur < len(text) and text[_cur].isspace():
                _cur += 1
            if text[_cur:_cur + len(wt)] == wt:
                _kept.append(w); _cur += len(wt)
            else:
                break  # first mismatch = start of the echo tail; discard the rest
        words_coarse = _kept
        kts = []
        src_words = words_coarse or [{'text': text, 'begin_ms': tl['begin_ms'], 'end_ms': tl['end_ms']}]
        line_morae = mora_map.get(i, {})
        # Context-fed readings for the whole line at once — Apple's sub-word
        # splits must never be read in isolation (休|みの日には|母|さん read
        # as きゅー|みのびにわ|はは|さん — the 2026-07-10 audit class). Human
        # kana overrides (words[].kana via timing_edit) still win per word.
        planned = (line_reading_plan(text, src_words, recon_cards)
                   if CJK.search(text) else None)
        plan, merged_wins = planned if planned else ({}, set())
        # A human kana override anywhere inside a merged segment disables the
        # plan for that whole segment — the override channel is per-window and
        # must never be double-emitted or absorbed.
        seg_head = {}
        for h, p in plan.items():
            for j in range(h, p['end_wi'] + 1):
                seg_head[j] = h
        disabled = {seg_head[j] for j, w in enumerate(src_words)
                    if w.get('kana') and j in seg_head}
        for wi, w in enumerate(src_words):
            if not strip_echo(w['text']).strip():
                continue
            if wi in merged_wins and seg_head.get(wi) not in disabled \
                    and not w.get('kana'):
                continue                    # this window rides an earlier segment
            p = plan.get(wi) if not w.get('kana') and wi not in disabled else None
            if p and p['reading']:
                end_wi = p['end_wi']
                kts.extend(timed_morae(
                    strip_echo(w['text']), w['begin_ms'],
                    src_words[end_wi]['end_ms'],
                    mora_entries=line_morae.get(wi) if end_wi == wi else None,
                    reading=p['reading'], hold_ms=w.get('hold_ms'),
                    rom_hints=p['hints']))
            else:
                kts.extend(timed_morae(strip_echo(w['text']), w['begin_ms'], w['end_ms'],
                                       mora_entries=line_morae.get(wi),
                                       reading=w.get('kana'), hold_ms=w.get('hold_ms')))
        apple_lines.append({
            'begin_ms': int(tl['begin_ms']), 'end_ms': int(tl['end_ms']),
            'text': text, 'lang': '', 'translation': '', 'translation_lang': '',
            'words': [{'text': strip_echo(w['text']), 'begin_ms': w['begin_ms'], 'end_ms': w['end_ms']}
                      for w in words_coarse],
            'is_background': False, 'kana_timings': kts,
        })

    song = lyr.get('song', {})
    art = meta.get('art') or song.get('artwork_url', '')
    apple = {
        'song': {
            'id': str(song.get('id', '')), 'name': meta['title_jp'],
            'artist': meta['artist'], 'album': song.get('album', ''),
            'duration_ms': int(song.get('duration_ms', apple_lines[-1]['end_ms'] if apple_lines else 0)),
            'artwork_url': art,
            # music onset (ms) for the intro-card countdown; additive, absent
            # when the aligner didn't measure it (music-at-0 songs behave as before).
            **({'music_start_ms': int(song['music_start_ms'])} if 'music_start_ms' in song else {}),
        },
        'lines': apple_lines, 'line_count': len(apple_lines),
        'has_translations': False, 'has_word_timing': True,
        'languages': [], 'has_kana_timings': True,
    }

    # sections: build context_lines from authored line→section, keep the 9 word fields
    sec_lines = {}
    for cl in clines:
        sec_lines.setdefault(cl.get('section', ''), []).append(strip_echo(cl['jp']))
    # words may be a flat top-level array (with a `section` ref) OR nested in sections[]
    words_by_sec = {}
    for w in content.get('words', []):
        words_by_sec.setdefault(w.get('section', ''), []).append(w)
    WORD_FIELDS = ['jp', 'rom', 'jp_speak', 'en', 'en_speak', 'particle', 'hint', 'context', 'gloss']
    sections = []
    for s in content['sections']:
        words = []
        src_words = s.get('words') or words_by_sec.get(s['id'], [])
        for w in src_words:
            ww = {k: w.get(k, '') for k in WORD_FIELDS}
            ww['particle'] = bool(w.get('particle'))
            if w.get('jp_speak') in (None, ''):
                ww['jp_speak'] = w.get('jp', '')
            if w.get('uid'):
                ww['uid'] = w['uid']
            if w.get('only_lines'):
                ww['only_lines'] = w['only_lines']
            words.append(ww)
        sections.append({
            'id': s['id'], 'name': s.get('name', ''),
            'short_name': s.get('short_name', s['id'][:2].upper()),
            'subtitle': s.get('subtitle', ''), 'description': s.get('description', ''),
            'speak_en': s.get('speak_en', ''),
            'context_lines': sec_lines.get(s['id'], []),
            'note': s.get('note', ''), 'words': words,
        })

    # coverage_exceptions: pure-Latin/ASCII tokens (English words in a JP/EN
    # code-switching song) are already in the target language — a JP learner
    # doesn't study them, so they are intentionally untappable, not a coverage bug.
    exc = set()
    for ln in apple_lines:
        for tok in re.split(r'\s+', ln['text']):          # whole-word English tokens
            tok = tok.strip()
            if tok and not CJK.search(tok) and not re.fullmatch(r'[0-9]+', tok):
                exc.add(tok)
        for run in re.findall(r"[A-Za-z][A-Za-z'’.]*", ln['text']):  # Latin runs glued to CJK (浮つくMy)
            exc.add(run)
    # content may declare non-lexical spans (a scat/vocalise hook like とぅるるる)
    for span in content.get('coverage_exceptions', []):
        exc.add(span)
    # punctuation is never a study word (backing-vocal parens like (ねぇ));
    # full-width ？！ and digit runs glued to CJK (深夜1時) are equally
    # unstudyable — found by the ema build's E1 (2026-07-07).
    exc.update(['(', ')', '（', '）', '[', ']', '「', '」', '、', '。', '·', '…', '　',
                '？', '！', '?', '!'])
    for ln in apple_lines:
        for run in re.findall(r'[0-9０-９]+', ln['text']):
            exc.add(run)

    data = {
        'song_number': meta.get('song_number', 3),
        'coverage_exceptions': sorted(exc),
        'title_jp': meta['title_jp'], 'title_en': meta['title_en'],
        'artist': meta['artist'], 'artist_en': meta['artist_en'],
        'slug': key, 'youtube_id': meta['youtube_id'],
        'level': meta.get('level', 'Intermediate'),
        'r2_folder': f"Song {meta.get('song_number', 3)} {meta['title_jp']}",
        # Podcast is EXPERIMENTAL (mission 2026-07-12): a song without a
        # podcast_script ships with NO podcast file — an empty URL fails soft
        # at the player (and E20 only warns), instead of baking a URL that
        # 404s into HTML (the dead-inochi class). Graceful Immerse UI for
        # podcast-less songs = backlog 2601104f.
        'podcast_file': (f'{key}_podcast.mp3' if content.get('podcast_script')
                         else ''),
        'direction': 'ja',
        'sections': sections, 'grammar': content.get('grammar', []),
        # trivia retired from the product (2026-07-12): not shipped to pages
        'podcast_script': content.get('podcast_script', []),
        'apple_lyrics': apple,
    }

    # ---- tts_manifest (5 classes) + audio jobs -----------------------------
    manifest = []
    jobs = []            # [lang, spoken_text, out_filename]
    # word_meta: for every ja WORD clip (out_rel -> {surface, kana, rom}) so the
    # audio engine can drive dictionary-priority routing for short words. surface
    # = display/kanji form (w['jp']); kana = the reading actually spoken
    # (jp_speak); rom = the romaji. Additive; only word_* ja jobs, not full lines.
    word_meta = {}
    seen_m, seen_j = set(), set()

    def add_manifest(lang, key_text, filename):
        k = (lang, key_text, filename)
        if k in seen_m:
            return
        seen_m.add(k)
        manifest.append([lang, key_text, key_text, filename])

    def add_job(lang, spoken, filename):
        if (filename in seen_j) or not spoken:
            return
        seen_j.add(filename)
        jobs.append([lang, spoken, filename])

    for s in sections:
        sid = s['id']
        if s['speak_en']:
            fn = f'section_{sid}_intro.mp3'
            add_manifest('en-US', s['speak_en'], f'audio/en/{fn}')
            add_job('en', s['speak_en'], f'en/{fn}')
        for w in s['words']:
            uid = rom_uid(w.get('uid') or w['rom'])
            # A card's clip filename IS its romaji. Empty romaji silently built
            # jp/word_<sid>_.mp3 — one nameless slot that every romaji-less card
            # in the section collides on, and all of them fail at render, ten
            # minutes into the TTS pass (mariigoorudo 2人, 2026-07-29). Refuse
            # here, where it costs a second and names the card.
            if not uid:
                sys.exit(f'[content_to_data] card {w["jp"]!r} in section {sid} has no '
                         f'romaji, so its clip has no filename (jp/word_{sid}_.mp3).\n'
                         f'  Give it a reading + rom in builds/<key>.content.json — '
                         f'scaffold.py reads digits via the kanji numeral form '
                         f'(numeral_reading), so re-scaffolding usually fixes it.')
            jp_speak = w.get('jp_speak') or w['jp']
            word_out = f'jp/word_{sid}_{uid}.mp3'
            add_manifest('ja-JP', jp_speak, f'audio/jp/word_{sid}_{uid}.mp3')
            add_job('ja', jp_speak, word_out)
            # record surface/kana/rom for the audio engine's dictionary routing
            word_meta.setdefault(word_out, {
                'surface': w['jp'], 'kana': jp_speak, 'rom': w.get('rom', '')})
            if w.get('en_speak'):
                add_manifest('en-US', w['en_speak'], f'audio/en/word_{sid}_{uid}_en.mp3')
                add_job('en', w['en_speak'], f'en/word_{sid}_{uid}_en.mp3')
            if w.get('context'):
                add_job('en', w['context'], f'en/word_{sid}_{uid}_ctx.mp3')
            if w.get('gloss'):
                add_job('en', w['gloss'], f'en/word_{sid}_{uid}_gloss.mp3')

    # full-line JP clips (drill tail) + line explainers
    line_tr = content.get('line_tr', {})
    line_explain = content.get('line_explain', {})
    uniq_lines = []
    seen_l = set()
    for ln in apple_lines:
        t = ln['text']
        if not CJK.search(t):
            continue
        k = line_tr_key(t)
        if k in seen_l:
            continue
        seen_l.add(k)
        uniq_lines.append(t)
    for idx, t in enumerate(uniq_lines):
        fn = f'line_u{idx:02d}.mp3'
        add_manifest('ja-JP', t, f'audio/jp/{fn}')
        add_job('ja', t, f'jp/{fn}')
    # Pure-EN lyric lines (no CJK — code-switching songs) that HAVE a
    # line_explain entry get a spoken-line clip too, so their drill concat can
    # open with the sung line before the explainer ([EN line + explainer tail],
    # zero word pairs). The clip is ENGLISH (Kokoro am_michael via gen_audio)
    # and manifest-keyed en-US — the JP voice must never read Latin text
    # (validate_tts_safety fails a ja-JP entry with no Japanese). EN lines
    # WITHOUT an explainer stay clip-less, exactly as before.
    explain_keys = {line_tr_key(k) for k, v in line_explain.items() if v}
    uniq_en_lines = []
    seen_le = set()
    for ln in apple_lines:
        t = ln['text']
        if not t.strip() or CJK.search(t):
            continue
        k = line_tr_key(t)
        if k in seen_le or k not in explain_keys:
            continue
        seen_le.add(k)
        uniq_en_lines.append(t)
    for idx, t in enumerate(uniq_en_lines):
        fn = f'line_en_u{idx:02d}.mp3'
        add_manifest('en-US', t, f'audio/en/{fn}')
        add_job('en', t, f'en/{fn}')
    for text in dict.fromkeys(line_explain.values()):
        if not text:
            continue
        h = hashlib.sha1(text.encode()).hexdigest()[:8]
        fn = f'line_{h}_explain.mp3'
        add_manifest('en-US', text, f'audio/en/{fn}')
        add_job('en', text, f'en/{fn}')

    # ---- write outputs -----------------------------------------------------
    dst = SONGS / slug
    dst.mkdir(parents=True, exist_ok=True)
    (dst / 'data.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
    (dst / 'tts_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (BUILDS / f'{key}.line_maps.json').write_text(json.dumps(
        {'LINE_TR': {line_tr_key(k): v for k, v in line_tr.items()},
         'LINE_EXPLAIN': {line_tr_key(k): v for k, v in line_explain.items()}},
        ensure_ascii=False, indent=2))
    (BUILDS / f'{key}.audio_jobs.json').write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
    (BUILDS / f'{key}.word_meta.json').write_text(json.dumps(word_meta, ensure_ascii=False, indent=2))

    print(f'data.json: {len(apple_lines)} lines, {sum(len(s["words"]) for s in sections)} words, '
          f'{len(sections)} sections')
    print(f'manifest: {len(manifest)} entries | audio jobs: {len(jobs)} '
          f'({sum(1 for j in jobs if j[0]=="ja")} ja, {sum(1 for j in jobs if j[0]=="en")} en)')
    print(f'line maps: {len(line_tr)} LINE_TR, {len(line_explain)} LINE_EXPLAIN')
    print(f'word meta: {len(word_meta)} ja word clips')


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2])
