#!/usr/bin/env python3
"""Human-recorded JP word audio from the local NHK/yomichan corpus — the BEST
offline source (~250k native clips), sitting between the committed library/
cache and the online JPod101 fetch in the dictionary-priority chain:

    library (dual-form read) -> THIS (nhk16 > shinmeikai8 > forvo > jpod)
        -> fetch.py (JPod101 online) -> tofugu.py (WaniKani offline)

Corpus root (override with $YOMICHAN_AUDIO_DIR):
    ~/Desktop/JP TTS Research/yomichan-audio/user_files/
      nhk16_files/entries.json + audio/      NHK日本語発音アクセント辞典 (~103k,
                                             kana + kanji[] + pitch accents)
      shinmeikai8_files/index.json + media/  新明解第八版 (~67k;
                                             {headwords: hw->[files],
                                              files: file->{kana_reading,…}})
      jpod_files/index.json + media/         JapanesePod101 local dump (~134k;
                                             same index shape as shinmeikai)
      forvo_files/<speaker>/<term>.mp3       5 Forvo speakers (~79k; filename
                                             IS the term, reading unrecorded)
      jmdict_forms.json                      [{reading, expressions:[{kanji,
                                             override_reading?}]}] — alternate-
                                             spelling backfill (想い出 -> 思い出)

HOMOPHONE SAFETY (the whole point — a wrong dict clip is worse than TTS):
  - kanji surface: a tier may only return a clip whose entry pairs EXACTLY
    this surface with EXACTLY this reading (NHK: surface in entry.kanji AND
    entry.kana == kana; shinmeikai/jpod: headwords[surface] filtered on
    kana_reading == kana). resolve(気, き) can never return 木【き】.
  - pure-kana surface: the entry must be the kana headword (shinmeikai/jpod
    key kana headwords directly; NHK is kana-keyed — an entry with foreign
    kanji is accepted only when it is the SINGLE entry for that kana, e.g.
    の -> 野【の】; multiple kanji homophones = ambiguous = no NHK candidate).
  - forvo: the filename is the term with no recorded reading. Pure-kana terms
    are phonetically exact; a kanji term is accepted only when jmdict says
    that spelling has exactly ONE reading (== the requested kana). 空 (そら/
    から/くう/…) therefore never resolves via forvo.
  - jmdict alternate spellings are tried only for kanji surfaces (the kanji
    anchors word identity; bare kana can't).
  - は/へ/を are REFUSED like fetch.py/tofugu.py: their dict headword clips
    say ha/he/wo, not the spoken wa/e/o — callers use the spoken-reading
    library clips (は__わ.mp3 …).

Indexes are parsed lazily, once per process, and kept in module state
(measured on the M4 mini: nhk 0.5s + jpod 0.2s + shinmeikai 0.2s + jmdict
0.2s ≈ 1.1s for the full first build; every later resolve is dict lookups).
No pickle cache needed at that speed.

Clips are returned RAW — loudnorm is the installer's job (install_word.py /
gen_audio.loudnorm_mp3), same as fetch.py/tofugu.py.

Usage:
  python3 corpus.py resolve <surface> [kana]   # print every tier that resolves
  python3 corpus.py fetch   <surface> [kana]   # copy best hit into library/
                                               # under the slug() writer naming
Licensing: personal-study corpus (the standard yomichan local-audio pack);
fine for the owner's private learning site, same footing as fetch.py's JPod101 use.
"""
import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, 'library')
CORPUS = os.environ.get('YOMICHAN_AUDIO_DIR') or os.path.expanduser(
    '~/Desktop/JP TTS Research/yomichan-audio/user_files')

# particles whose headword reading != spoken reading — never trust a dict clip
# (mirrors fetch.py/tofugu.py)
BAD_PARTICLES = {'は', 'へ', 'を'}

# Forvo speaker priority: by corpus coverage (skent 28.9k, strawberrybrown
# 17.3k, poyotan 16.6k, kaoring 9.0k, akitomo 7.0k) so the most-likely-present
# voice is also the most consistently reused one.
FORVO_SPEAKERS = ('skent', 'strawberrybrown', 'poyotan', 'kaoring', 'akitomo')

KANA_ONLY = re.compile(r'^[ぁ-ゖァ-ヺー]+$')
_K2H = {i: i - 0x60 for i in range(0x30A1, 0x30F7)}   # ァ..ヶ -> ぁ..ゖ


def _hira(s):
    """Katakana -> hiragana fold for reading comparison (ー kept)."""
    return (s or '').translate(_K2H)


def _root():
    if not os.path.isdir(CORPUS):
        sys.exit(f"corpus not found: {CORPUS}\n"
                 f"expected the yomichan local-audio pack (nhk16_files/, "
                 f"shinmeikai8_files/, jpod_files/, forvo_files/, "
                 f"jmdict_forms.json) — set $YOMICHAN_AUDIO_DIR to its "
                 f"user_files/ dir")
    return CORPUS


# ── lazy per-source indexes (module state; ~1.1s total first build) ─────────

_NHK = None            # hira(kana) -> [(kanji_tuple, soundFile), ...]
_SMK = None            # headword -> [(media_file, hira(kana_reading)), ...]
_JPOD = None           # same shape as _SMK
_JMDICT = None         # hira(reading) -> [[(kanji_form, form_reading), ...]]
_FORM_READINGS = None  # kanji_form -> set(hira readings)


def _nhk():
    global _NHK
    if _NHK is None:
        idx = {}
        entries = json.load(open(os.path.join(_root(), 'nhk16_files',
                                              'entries.json')))
        for e in entries:
            sf = next((a.get('soundFile') for a in e.get('accents', [])
                       if a.get('soundFile')), None)
            if not sf:
                continue
            idx.setdefault(_hira(e.get('kana') or ''), []).append(
                (tuple(e.get('kanji') or ()), sf))
        _NHK = idx
    return _NHK


def _headword_index(subdir):
    d = json.load(open(os.path.join(_root(), subdir, 'index.json')))
    files = d.get('files', {})
    return {hw: [(f, _hira((files.get(f) or {}).get('kana_reading') or ''))
                 for f in fl]
            for hw, fl in d.get('headwords', {}).items()}


def _smk():
    global _SMK
    if _SMK is None:
        _SMK = _headword_index('shinmeikai8_files')
    return _SMK


def _jpod():
    global _JPOD
    if _JPOD is None:
        _JPOD = _headword_index('jpod_files')
    return _JPOD


def _jmdict_maps():
    global _JMDICT, _FORM_READINGS
    if _JMDICT is None:
        by_reading, form_readings = {}, {}
        for e in json.load(open(os.path.join(_root(), 'jmdict_forms.json'))):
            r = _hira(e.get('reading') or '')
            forms = []
            for x in e.get('expressions', []):
                kj = x.get('kanji')
                if not kj:
                    continue
                fr = _hira(x.get('override_reading') or '') or r
                forms.append((kj, fr))
                form_readings.setdefault(kj, set()).add(fr)
            if r and forms:
                by_reading.setdefault(r, []).append(forms)
        _JMDICT, _FORM_READINGS = by_reading, form_readings
    return _JMDICT, _FORM_READINGS


def _alt_surfaces(surface, k):
    """jmdict-backed alternate spellings of the SAME word (identical reading):
    想い出/おもいで -> [思い出, …]. Kanji surfaces only — bare kana can't anchor
    a homophone-safe word identity, so pure-kana requests get no alternates."""
    by_reading, _ = _jmdict_maps()
    alts = []
    for forms in by_reading.get(k, []):
        names = [kj for kj, fr in forms if fr == k]
        if surface in names:
            alts += [n for n in names if n != surface and n not in alts]
    return alts


# ── per-tier matchers — each returns (path, display) or None ────────────────

def _match_nhk(surfs, k, pure):
    cands = _nhk().get(k) or []
    pick = None
    if pure:
        exact = [c for c in cands if not c[0] or surfs[0] in c[0]
                 or _hira(surfs[0]) in c[0]]
        if exact:
            pick = exact[0]
        elif len(cands) == 1:
            pick = cands[0]      # single homophone for this kana (の -> 野【の】)
        # multiple kanji homophones for a bare-kana request: ambiguous, refuse
    else:
        for s in surfs:
            hit = [c for c in cands if s in c[0]]
            if hit:
                pick = hit[0]
                break
    if pick is None:
        return None
    kanji, sf = pick
    p = os.path.join(_root(), 'nhk16_files', 'audio', sf)
    if not os.path.isfile(p):
        return None
    return p, ('・'.join(kanji) if kanji else k) + f'【{k}】'


def _match_headwords(idx, media_dir, surfs, k):
    for s in surfs:
        for f, r in idx.get(s) or []:
            if r == k:
                p = os.path.join(media_dir, f)
                if os.path.isfile(p):
                    return p, f'{s}【{k}】'
    return None


def _match_forvo(surfs, k, pure):
    _, form_readings = _jmdict_maps()
    base = os.path.join(_root(), 'forvo_files')
    for s in surfs:
        if not pure and form_readings.get(s) != {k}:
            continue     # spelling has 0 or 2+ jmdict readings — the file name
                         # can't prove what the speaker said; homophone-unsafe
        for sp in FORVO_SPEAKERS:
            p = os.path.join(base, sp, s + '.mp3')
            if os.path.isfile(p):
                return p, f'{sp}/{s}.mp3'
    return None


# ── public API ───────────────────────────────────────────────────────────────

def resolve_all(surface, kana=None):
    """Every corpus tier that resolves (surface, kana), priority order:
    [(source, path, display), ...] with source in
    'nhk16' | 'shinmeikai8' | 'forvo' | 'jpod'. Empty list = miss (or a
    refused は/へ/を particle)."""
    if surface in BAD_PARTICLES:
        return []
    kana = kana or surface
    k = _hira(kana)
    pure = bool(KANA_ONLY.match(surface))
    surfs = [surface] if pure else [surface] + _alt_surfaces(surface, k)
    out = []
    m = _match_nhk(surfs, k, pure)
    if m:
        out.append(('nhk16',) + m)
    m = _match_headwords(_smk(), os.path.join(_root(), 'shinmeikai8_files',
                                              'media'), surfs, k)
    if m:
        out.append(('shinmeikai8',) + m)
    m = _match_forvo(surfs, k, pure)
    if m:
        out.append(('forvo',) + m)
    m = _match_headwords(_jpod(), os.path.join(_root(), 'jpod_files',
                                               'media'), surfs, k)
    if m:
        out.append(('jpod',) + m)
    return out


def resolve(surface, kana=None):
    """Best corpus hit: (path, source_note) or (None, note). source_note is
    provenance-ready, e.g. 'nhk16:20170726161104.mp3 野【の】'."""
    if surface in BAD_PARTICLES:
        return None, (f'BAD-PARTICLE {surface}: dict headword clips say '
                      f'ha/he/wo, not the spoken particle — use the '
                      f'spoken-reading library clip (は__わ.mp3 form)')
    hits = resolve_all(surface, kana)
    if not hits:
        return None, 'MISS'
    source, path, disp = hits[0]
    return path, f'{source}:{os.path.basename(path)} {disp}'


def main():
    ap = argparse.ArgumentParser(
        description='NHK/yomichan local-corpus resolver (offline, stdlib-only)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for c, h in (('resolve', 'print every tier that resolves the word'),
                 ('fetch', 'copy the best hit into library/ (slug naming)')):
        s = sub.add_parser(c, help=h)
        s.add_argument('surface')
        s.add_argument('kana', nargs='?', default='')
    a = ap.parse_args()
    surface, kana = a.surface, a.kana or a.surface
    if surface in BAD_PARTICLES:
        sys.exit(f'BAD-PARTICLE {surface}: headword reading is ha/he/wo, not '
                 f'the spoken particle — keep the spoken-reading library clip')
    hits = resolve_all(surface, kana)
    if not hits:
        sys.exit(f'MISS {surface} ({kana}) — no homophone-safe corpus entry')
    for source, path, disp in hits:
        print(f'{source:12} {disp:14} {path}')
    if a.cmd == 'fetch':
        # writer naming stays slug() (fetch.py's convention) — bare '<surface>'
        # when surface == kana; readers reach both forms via library_lookup().
        safe = lambda s: s.replace('/', '_').replace(' ', '')
        stem = (f'{safe(surface)}__{safe(kana)}'
                if kana and kana != surface else safe(surface))
        os.makedirs(LIB, exist_ok=True)
        out = os.path.join(LIB, stem + '.mp3')
        shutil.copyfile(hits[0][1], out)
        print(f'-> {out}')
        print('   raw corpus copy — loudnorm happens at install time '
              '(install_word.py / gen_audio), not here.')


if __name__ == '__main__':
    main()
