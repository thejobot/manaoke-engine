#!/usr/bin/env python3
"""Fetch timed lyrics for a song build — self-contained, multi-source.

Replaces the old "cd ~/lyricool && git worktree ..." ritual: everything this
needs is vendored under tools/songcraft/lyric_sources/ (NetEase, LRCLIB,
Apple TTML, all stdlib), so the tool works on any machine with no sibling
repos. This is the `lyrics` step's runner.

Sources, tried in order for --source auto:
  apple    syllable/word-level TTML (best quality; needs a signed-in token —
           see "Apple token" below; skipped silently if no token)
  netease  word-level YRC (keyless; excellent JP coverage)
  lrclib   line-level LRC (keyless; whisper_sync --words upgrades it)

Bring your own sheet (--source file --file <path>, never tried by auto):
  Any .lrc, TTML .xml/.ttml, or .json sheet you already have. The three
  network sources look a song up by identity, so a song none of them has
  heard of has no way in; this is that way in. The format is sniffed from
  the content, not the extension. Nothing downstream cares where the sheet
  came from — the aligner still sharpens it, the Timing Studio still edits
  it, the page still reads it. Other projects fetch and author these sheets
  (amll-dev/amll-ttml-tool is a syllable-level editor); this one takes the
  sheet you bring.

Apple token: read from tools/songcraft/.apple-lyrics.json, falling back to
~/.lyricool-config.json. Keys: authorization (Bearer developer token),
media_user_token, storefront. To refresh: sign into music.apple.com in a
browser, copy the `authorization` header + `media-user-token` header from any
amp-api.music.apple.com request in DevTools' Network tab into the JSON file.
Tokens last months; this stays an OPTION until the local aligner owns timing.

Usage:
  fetch_timed_lyrics.py <key> [--source auto|apple|netease|lrclib|file]
                              [--file <sheet>] [--force]

Writes builds/<key>.lyrics.json (refuses to overwrite without --force).
Exit 0 on success, 2 with a friendly explanation when no source has the song.
"""

import argparse
import json
import re
import socket
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILDS = HERE / 'builds'
sys.path.insert(0, str(HERE))

from lyric_sources import netease, lrclib, apple, ttml  # noqa: E402

APPLE_CONFIG_PATHS = [HERE / '.apple-lyrics.json',
                      Path.home() / '.lyricool-config.json']


class SourceBusy(Exception):
    """A source is rate-limiting us — it may well have the song. Distinct from
    "no match" so a throttle never reads as an answer."""


def _fold(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub(r'[\s\W_]+', '', s)


# A lyric sheet belongs to ONE recording. LRCLIB's exact match is (title,
# artist, duration) — and a live take can land within a second of the studio
# take, which is exactly how マリーゴールド (2026-07-29) came back as the 甲子園
# live sheet: different intro, different tail, an extra ありがとう, every line
# drifting against the studio video. Nothing said a word, and the alignment
# was left to fail three steps later. These are the words that mark a sheet as
# a DIFFERENT recording of the same song.
_VARIANT_WORDS = ['live', 'ライブ', 'ライヴ', '弾き語り', 'acoustic', 'アコースティック',
                  'instrumental', 'インストゥルメンタル', 'インスト', 'off vocal',
                  'カラオケ', 'karaoke', 'remix', 'リミックス', 'cover', 'カバー',
                  'remaster', 'リマスター', 'version', 'ver.', 'ヴァージョン',
                  'バージョン', 'mix', 'edit', 'demo', 'デモ']


def _variant_markers(text):
    """Variant words present in a title/album, ignoring the ones the person
    already asked for (a search for 'Live in Tokyo' SHOULD match a live sheet).
    Latin markers need a word boundary — 'edit' must not fire inside
    'meditation', 'live' must not fire inside 'delivery'."""
    t = unicodedata.normalize('NFKC', text or '').lower()
    out = set()
    for w in _VARIANT_WORDS:
        if w.isascii():
            if re.search(r'(?<![a-z])' + re.escape(w) + r'(?![a-z])', t):
                out.add(w)
        elif w in t:
            out.add(w)
    return out


def variant_mismatch(want_title, sheet_name, album=''):
    """'' when the sheet is the same recording as the track being built;
    otherwise a plain sentence naming what it actually is."""
    want = _variant_markers(want_title)
    got = _variant_markers(sheet_name) | _variant_markers(album)
    extra = got - want
    if not extra:
        return ''
    what = sheet_name or album
    return (f'this sheet is for "{what}" — a different recording of the song, '
            f'not the track you picked')


def load_apple_config():
    for p in APPLE_CONFIG_PATHS:
        try:
            cfg = json.loads(p.read_text())
        except Exception:
            continue
        if cfg.get('media_user_token') and cfg.get('authorization'):
            return cfg
    return None


def itunes_duration_ms(apple_url):
    """Keyless duration lookup via the iTunes lookup API (?i=<trackId>)."""
    try:
        q = urllib.parse.urlparse(apple_url)
        track_id = urllib.parse.parse_qs(q.query).get('i', [None])[0]
        if not track_id:
            return None, None
        with urllib.request.urlopen(
                f'https://itunes.apple.com/lookup?id={track_id}&country=jp',
                timeout=8) as r:
            data = json.loads(r.read())
        for res in data.get('results', []):
            if res.get('kind') == 'song':
                return int(res.get('trackTimeMillis') or 0) or None, res
    except Exception:
        pass
    return None, None


def _score_netease(cand, title, artist, duration_ms):
    score = 0
    if _fold(cand['name']) == _fold(title):
        score += 4
    elif _fold(title) in _fold(cand['name']):
        score += 2
    if artist and (_fold(artist) in _fold(cand['artist'])
                   or _fold(cand['artist']) in _fold(artist)):
        score += 3
    if duration_ms and cand.get('duration_ms'):
        d = abs(cand['duration_ms'] - duration_ms)
        score += 3 if d <= 3000 else (1 if d <= 10000 else -2)
    return score


def try_netease(title, artist, duration_ms):
    # NetEase throttles bursts (adding a song probes it, then fetches it) and
    # answers 405 "操作频繁" for a minute or so. That is a wait, not a miss —
    # give it a couple of chances before letting a worse source have the song.
    cands = None
    for wait in (0, 3, 8):
        if wait:
            time.sleep(wait)
        try:
            cands = netease.search(f'{title} {artist}'.strip(), limit=10)
            break
        except netease.Throttled as e:
            busy = str(e)
    if cands is None:
        raise SourceBusy(f'netease is rate-limiting right now ({busy})')
    if not cands:
        return None
    cands.sort(key=lambda c: -_score_netease(c, title, artist, duration_ms))
    best = cands[0]
    if _score_netease(best, title, artist, duration_ms) < 4:
        return None
    variants = netease.fetch_lyrics(best['id'])
    lines = netease.parse_yrc(variants.get('yrc'))
    source = 'netease_yrc'
    if not lines:
        lines = netease.parse_lrc(variants.get('lrc'))
        source = 'netease_lrc'
    if not lines:
        return None
    # merge line-level translations by nearest begin time (±2s).
    # NetEase tlyric is almost always CHINESE for JP songs — label it honestly
    # so nothing downstream treats it as English authoring reference.
    tr = {e['begin_ms']: e['text'] for e in netease.parse_lrc(variants.get('tlyric'))}
    for ln in lines:
        near = [t for b, t in tr.items() if abs(b - ln['begin_ms']) <= 2000]
        if near:
            lang = 'zh' if re.search(r'[一-鿿]', near[0]) else 'en'
            ln['translation'], ln['translation_lang'] = near[0], lang
    return {'song': best, 'lines': lines, 'source': source}


def _lrclib_search_strict(title, artist):
    """lrclib.search, but a network failure RAISES instead of returning [].
    An empty list from a source that never answered is the same lie
    SourceBusy exists to stop — see try_lrclib."""
    try:
        return lrclib._request('/search', {'track_name': title,
                                           'artist_name': artist}) or []
    except LookupError:
        return []


def try_lrclib(title, artist, duration_ms):
    """Raises SourceBusy when LRCLIB can't be reached. It used to swallow the
    failure and return None, which the caller printed as 'lrclib (no match)' —
    a source that was DOWN reported as a source that had looked and found
    nothing (LRCLIB's API hung for every request on 2026-07-30 while its site
    still served). NetEase already had this distinction; LRCLIB did not."""
    rec = None
    try:
        if duration_ms:
            rec = lrclib.get_exact(title, artist,
                                   duration_sec=round(duration_ms / 1000),
                                   strict=True)
        # get_exact matches on duration, which a live take can share with the
        # studio take to the second. If what came back is a different
        # recording, look through the search hits for the real one before
        # settling for it.
        if rec and variant_mismatch(title, rec.get('trackName', ''), rec.get('albumName', '')):
            for c in _lrclib_search_strict(title, artist):
                if (c.get('syncedLyrics') and _fold(title) in _fold(c.get('trackName', ''))
                        and not variant_mismatch(title, c.get('trackName', ''), c.get('albumName', ''))):
                    rec = c
                    break
        if not (rec and rec.get('syncedLyrics')):
            for c in _lrclib_search_strict(title, artist):
                if c.get('syncedLyrics') and _fold(title) in _fold(c.get('trackName', '')):
                    rec = c
                    break
    except RuntimeError as e:
        raise SourceBusy(f'lrclib did not answer ({e})')
    if not (rec and rec.get('syncedLyrics')):
        return None
    lines = lrclib.parse_lrc(rec['syncedLyrics'])
    if not lines:
        return None
    song = {'id': f"lrclib-{rec.get('id', '')}", 'name': rec.get('trackName', title),
            'artist': rec.get('artistName', artist), 'album': rec.get('albumName', ''),
            'duration_ms': int((rec.get('duration') or 0) * 1000) or duration_ms or 0,
            'artwork_url': ''}
    return {'song': song, 'lines': lines, 'source': 'lrclib'}


def try_apple(apple_url, title, artist, duration_ms):
    cfg = load_apple_config()
    if not cfg:
        return None
    try:
        # Lyrics endpoints only answer for ids in the ACCOUNT's own storefront
        # catalog (a us token + a jp catalog id = 404, verified 2026-07-06), so
        # ignore the URL's storefront and search the account catalog by name;
        # the URL id is only worth trying when the storefronts already agree.
        song_id = apple.parse_song_url(apple_url) if apple_url else None
        url_sf = None
        if isinstance(song_id, tuple):
            song_id, url_sf = song_id
        if url_sf and url_sf != cfg.get('storefront', 'us'):
            song_id = None
        # Apple serves the syllable endpoint even for releases that only have
        # line-timed TTML (itunes:timing="Line"), so "got TTML" isn't enough —
        # prefer the release whose TTML actually parses to per-word timing
        # (kaijuu: the album release is line-timed, the replica release has
        # the real 300+ word spans).
        def parsed(sid):
            raw = apple.fetch_syllable_lyrics(sid, cfg) if sid else None
            ls = ttml.parse_ttml(raw) if raw else []
            return ls or None
        lines = parsed(song_id)
        best_line_level = lines if lines and not any(l.get('words') for l in lines) else None
        if not lines or best_line_level is not None:
            hits = apple.search_catalog(f'{title} {artist}', cfg, limit=8)
            hits = [h for h in hits if h.get('has_syllable_lyrics')]
            if duration_ms:
                hits.sort(key=lambda h: abs((h.get('duration_ms') or 0) - duration_ms))
            for h in hits:
                ls = parsed(h['id'])
                if not ls:
                    continue
                if any(l.get('words') for l in ls):
                    lines, song_id = ls, h['id']
                    break
                if best_line_level is None:
                    best_line_level, song_id = ls, h['id']
            else:
                lines = best_line_level
        if not lines:
            return None
        info = {}
        try:
            info = apple.fetch_song_info(song_id, cfg) or {}
        except Exception:
            pass
        song = {'id': str(song_id), 'name': info.get('name', title),
                'artist': info.get('artist', artist), 'album': info.get('album', ''),
                'duration_ms': info.get('duration_ms') or duration_ms or 0,
                'artwork_url': info.get('artwork_url', '')}
        return {'song': song, 'lines': lines, 'source': 'apple_syllable'}
    except Exception as e:
        print(f'[lyrics] apple source failed ({e}) — trying the next source')
        return None


# NetEase (and some LRC files) embed songwriter credits as timestamped
# "lines" at the top (作词 : Tatsuya Maki). They are metadata, not lyrics —
# a human strikes them on sight; so do we. Only the leading run is dropped:
# a lyric that legitimately contains 作曲 mid-song would be untouched.
CREDIT_RE = re.compile(
    r'^\s*(?:(?:作词|作詞|作曲|编曲|編曲|词|曲|歌词|製作|监制|監製|'
    r'Lyricist|Composer|Arranger|Producer)\s*[:：]|(?:Written|Music|Lyrics)\s+by\b)',
    re.I)


def strip_credits(lines):
    out, seen_lyric = [], False
    for l in lines:
        if not seen_lyric and CREDIT_RE.match(l.get('text') or ''):
            continue
        seen_lyric = True
        out.append(l)
    return out


# The same habit at the other end of the sheet: transcribers close a NetEase
# lyric with a bare おわり ("the end"). Nobody sings it. Left in, it ships as a
# real lyric line — a card, a timing, a line on the page that the song never
# says (caught rebuilding イノチミジカシコイセヨオトメ, 2026-07-30, where the
# hand-tuned reference has no such line). Only an EXACT final marker goes: a
# song that ends on a sung 終わり as part of a sentence keeps it.
END_MARKER_RE = re.compile(r'^\s*[（(\[]?\s*(?:おわり|終わり|終り|終|完|'
                           r'fin|end|the\s+end)\s*[)）\].。!！~〜]*\s*$', re.I)


def strip_end_marker(lines):
    if len(lines) > 1 and END_MARKER_RE.match(lines[-1].get('text') or ''):
        return lines[:-1]
    return lines


# ── bring-your-own sheet ─────────────────────────────────────────────────
# The three network sources look a song up by identity, so a song they have
# never heard of has no way in at all. This is that way in: hand it the sheet
# you already have. Everything downstream is source-agnostic from the moment
# builds/<key>.lyrics.json lands, so a file walks the same road as a fetch —
# the aligner sharpens it, the Timing Studio edits it, the page reads it.
BYO_FORMATS = 'an .lrc, a TTML .xml/.ttml, or a .json sheet'


def sniff_sheet(text, path=''):
    """Which of the three shapes this is, by content first and name second.
    Content wins: people rename files, and a TTML saved as .txt is still
    TTML."""
    head = text.lstrip()[:400]
    if head.startswith('<') and re.search(r'<tt\b|<tt:tt\b|\bttml\b', head, re.I):
        return 'ttml'
    # LRC before JSON: an LRC opens on its metadata tags ([ar:…], [ti:…]),
    # so "starts with a bracket" reads as a JSON array and the file gets
    # rejected as malformed JSON. A timestamped line is the honest tell.
    if re.search(r'^\s*\[\d+:\d', text, re.M):
        return 'lrc'
    if head.startswith('{') or head.startswith('['):
        return 'json'
    ext = Path(path).suffix.lower()
    return {'.lrc': 'lrc', '.ttml': 'ttml', '.xml': 'ttml',
            '.json': 'json'}.get(ext, '')


def _lines_from_json(text):
    """Accept either a whole lyrics.json doc or a bare list of lines — both
    are things a person plausibly has on disk."""
    doc = json.loads(text)
    lines = doc.get('lines') if isinstance(doc, dict) else doc
    if not isinstance(lines, list):
        raise ValueError('no "lines" array in the JSON')
    out = []
    for i, l in enumerate(lines):
        if not isinstance(l, dict):
            raise ValueError(f'line {i + 1} is not an object')
        if 'begin_ms' not in l or 'text' not in l:
            raise ValueError(f'line {i + 1} is missing begin_ms or text')
        out.append({
            'begin_ms': int(l['begin_ms']),
            'end_ms': int(l.get('end_ms') or int(l['begin_ms']) + 4000),
            'text': str(l.get('text') or ''),
            'lang': l.get('lang', ''),
            'translation': l.get('translation', ''),
            'translation_lang': l.get('translation_lang', ''),
            'words': l.get('words') or [],
            'is_background': bool(l.get('is_background')),
        })
    return out


def try_file(src_path, title, artist, duration_ms):
    """Parse a sheet the user brought. Raises ValueError with a sentence a
    person can act on — a bad file is a mistake to fix, not a source that
    missed."""
    p = Path(src_path).expanduser()
    try:
        text = p.read_text(encoding='utf-8-sig')
    except OSError as e:
        raise ValueError(f'could not read {p} ({e.strerror or e})')
    if not text.strip():
        raise ValueError(f'{p.name} is empty')
    kind = sniff_sheet(text, str(p))
    if not kind:
        raise ValueError(f'could not tell what {p.name} is — expected {BYO_FORMATS}')
    if kind == 'ttml':
        lines = ttml.parse_ttml(text)
    elif kind == 'lrc':
        lines = lrclib.parse_lrc(text)
    else:
        try:
            lines = _lines_from_json(text)
        except (ValueError, json.JSONDecodeError) as e:
            raise ValueError(f'{p.name} is not a lyric sheet we can read — {e}')
    if not lines:
        raise ValueError(f'{p.name} parsed as {kind} but held no timed lines')
    # The identity comes from the build, not the file: a hand-made sheet has
    # no trustworthy metadata, and letting it name the song would let a typo
    # in someone's LRC header rename the build.
    song = {'id': f'file-{p.name}', 'name': title, 'artist': artist,
            'album': '', 'duration_ms': duration_ms or 0, 'artwork_url': '',
            'file': str(p)}
    return {'song': song, 'lines': lines, 'source': f'file_{kind}'}


def sanity(lines, duration_ms):
    lines = [l for l in lines if (l.get('text') or '').strip()]
    lines = strip_end_marker(strip_credits(lines))
    if len(lines) < 5:
        return None, 'fewer than 5 lyric lines'
    begins = [l['begin_ms'] for l in lines]
    if begins != sorted(begins):
        lines.sort(key=lambda l: l['begin_ms'])
    if duration_ms and lines[-1]['end_ms'] > duration_ms + 20000:
        return None, (f'timing runs {lines[-1]["end_ms"] - duration_ms}ms past '
                      f'the track — wrong song match?')
    return lines, None


def stanzas_by_gaps(lines, gap_ms=2500):
    out, cur = [], [0]
    for i in range(1, len(lines)):
        if lines[i]['begin_ms'] - lines[i - 1]['end_ms'] > gap_ms:
            out.append(cur)
            cur = []
        cur.append(i)
    out.append(cur)
    return [{'id': f's{n + 1}', 'lines': ix} for n, ix in enumerate(out)]


def availability(title, artist, duration_ms=None, timeout=2.5):
    """Quick per-source has-it checks for the denmoku add-song pills.
    Returns {'netease': bool|None, 'lrclib': bool|None} — None = unknown.
    LRCLIB runs strict inside the caller's budget: a timeout/network failure
    leaves None ('?'), never False ('no') — backlog b1a2b514."""
    out = {'netease': None, 'lrclib': None}
    try:
        cands = netease.search(f'{title} {artist}'.strip(), limit=5,
                               timeout=timeout, strict=True)
        out['netease'] = any(
            _score_netease(c, title, artist, duration_ms) >= 4 for c in cands)
    except Exception:
        pass
    try:
        rec = lrclib.get_exact(title, artist,
                               duration_sec=round(duration_ms / 1000) if duration_ms else None,
                               timeout=timeout, strict=True)
        out['lrclib'] = bool(rec and rec.get('syncedLyrics'))
    except Exception:
        pass
    return out


# ── deep probe (denmoku add-song confirm pane) ───────────────────────────
# One PICKED candidate, generous budget, sources in parallel. Unlike the fast
# boolean pills this fetches the actual lyrics, so the granularity is the
# TRUTH: Apple's hasTimeSyncedLyrics flag lies about word-level (kaijuu: the
# syllable endpoint serves line-timed TTML too) — only parsing the TTML and
# checking any(l['words']) is honest. A source that misses its budget reports
# has=None ('unknown'), never False ('no') — backlog b1a2b514.

def _probe_shape(has, granularity=None, line_count=0, preview=None, note='', variant=''):
    return {'has': has, 'granularity': granularity, 'line_count': line_count,
            'preview': preview or [], 'note': note, 'variant': variant}


def _probe_preview(lines, n=6):
    """(lyric_line_count, first-n display texts) with leading credit rows
    (作词/作曲…) struck, same as the pipeline does before shipping."""
    lines = strip_end_marker(
        strip_credits([l for l in lines if (l.get('text') or '').strip()]))
    return len(lines), [l['text'] for l in lines[:n]]


def _probe_apple(title, artist, duration_ms, apple_url):
    if not load_apple_config():
        return _probe_shape(None, note='no Apple token — the pipeline skips Apple')
    got = try_apple(apple_url, title, artist, duration_ms)
    if not got:
        return _probe_shape(False, note='no timed lyrics in the account catalog')
    n, prev = _probe_preview(got['lines'])
    word = any(l.get('words') for l in got['lines'])
    song = got.get('song') or {}
    return _probe_shape(True, 'word' if word else 'line', n, prev,
                        f"{song.get('name', '')} — {song.get('artist', '')}".strip(' —'))


def _probe_netease(title, artist, duration_ms, timeout):
    # strict: a timeout/network failure RAISES (→ 'unknown' upstream) instead
    # of reading as an empty result set ('no').
    cands = netease.search(f'{title} {artist}'.strip(), limit=10,
                           timeout=timeout, strict=True)
    if not cands:
        return _probe_shape(False, note='no search hits')
    cands.sort(key=lambda c: -_score_netease(c, title, artist, duration_ms))
    best = cands[0]
    if _score_netease(best, title, artist, duration_ms) < 4:
        return _probe_shape(False, note='nothing score-matches this track')
    variants = netease.fetch_lyrics(best['id'], timeout=timeout)
    which = f"{best.get('name', '')} — {best.get('artist', '')}".strip(' —')
    var = variant_mismatch(title, best.get('name', ''), best.get('album', ''))
    if var:
        which += ' · a different recording'
    lines = netease.parse_yrc(variants.get('yrc'))
    if lines:
        n, prev = _probe_preview(lines)
        return _probe_shape(True, 'word', n, prev, which, var)
    lines = netease.parse_lrc(variants.get('lrc'))
    if lines:
        n, prev = _probe_preview(lines)
        return _probe_shape(True, 'line', n, prev, which, var)
    if (variants.get('lrc') or '').strip():
        return _probe_shape(True, 'text', 0, [], which + ' · untimed text only')
    return _probe_shape(False, note='match has no lyrics')


def _probe_lrclib(title, artist, duration_ms, timeout):
    rec = None
    if duration_ms:
        # strict: a timeout RAISES (→ 'unknown' upstream) instead of reading
        # as a miss.
        rec = lrclib.get_exact(title, artist,
                               duration_sec=round(duration_ms / 1000),
                               timeout=timeout, strict=True)
    if not (rec and rec.get('syncedLyrics')):
        for c in lrclib.search(track_name=title, artist_name=artist,
                               timeout=timeout) or []:
            if c.get('syncedLyrics') and _fold(title) in _fold(c.get('trackName', '')):
                rec = c
                break
    if rec and rec.get('syncedLyrics'):
        lines = lrclib.parse_lrc(rec['syncedLyrics'])
        n, prev = _probe_preview(lines)
        var = variant_mismatch(title, rec.get('trackName', ''), rec.get('albumName', ''))
        which = f"{rec.get('trackName', '')} — {rec.get('artistName', '')}".strip(' —')
        return _probe_shape(True, 'line', n, prev,
                            which + (' · a different recording' if var else ''), var)
    if rec and rec.get('plainLyrics'):
        return _probe_shape(True, 'text', 0, [], 'plain lyrics only — no timing')
    return _probe_shape(False, note='not in the database')


PROBE_ORDER = ('apple', 'netease', 'lrclib')   # fetch_for_key's auto order


def probe_sources(title, artist, duration_ms=None, apple_url='', timeout=9.0):
    """Deep per-source probe for ONE picked add-song candidate. Returns
    {apple|netease|lrclib: {has, granularity: 'word'|'line'|'text'|None,
    line_count, preview: [first ~6 lyric lines], note}, auto_pick, order,
    elapsed_ms}. Sources run in parallel daemon threads against a shared
    deadline; one that overruns reports has=None ('unknown')."""
    t0 = time.time()
    out = {s: _probe_shape(None, note='probe timed out') for s in PROBE_ORDER}

    def run(name, fn, args):
        try:
            res = fn(*args)
        except netease.Throttled:
            # rate-limited, not empty: it very likely HAS this song
            res = _probe_shape(None, note='busy right now (too many requests) — '
                                          'it may have this song; try again in a minute')
        except Exception as e:
            # The reader of this line is picking a song, not reading a stack
            # trace. "unreachable (RuntimeError)" told them the name of a
            # Python class; what they need to know is that this is a "not
            # right now", not a "no", and that trying again may work.
            slow = isinstance(e, (TimeoutError, socket.timeout)) or 'timed out' in str(e)
            res = _probe_shape(None, note=('too slow to answer — try again in a minute'
                                           if slow else
                                           "couldn't reach it — try again in a minute"))
        out[name] = res

    per = max(1.0, timeout - 0.5)      # per-source network budget
    threads = [threading.Thread(
        target=run, args=(name, fn, args), daemon=True,
        name=f'probe-{name}') for name, fn, args in (
            ('apple', _probe_apple, (title, artist, duration_ms, apple_url)),
            ('netease', _probe_netease, (title, artist, duration_ms, per)),
            ('lrclib', _probe_lrclib, (title, artist, duration_ms, per)))]
    deadline = t0 + timeout
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0.1, deadline - time.time()))
    snapshot = {s: dict(out[s]) for s in PROBE_ORDER}
    # what fetch_for_key --source auto would land on: first source in order
    # with timed lyrics that clear the ≥5-line sanity gate.
    pick = None
    for want_clean in (True, False):
        for s in PROBE_ORDER:
            r = snapshot[s]
            if not (r['has'] and r['granularity'] in ('word', 'line')
                    and r['line_count'] >= 5):
                continue
            # first pass: only sheets for the recording that was picked. A live
            # take's sheet is a last resort, never the automatic answer.
            if want_clean and r.get('variant'):
                continue
            pick = s
            break
        if pick:
            break
    snapshot.update({'auto_pick': pick, 'order': list(PROBE_ORDER),
                     'elapsed_ms': int((time.time() - t0) * 1000)})
    return snapshot


# How good a sheet is, in the order the pipeline already prefers its sources.
# Word timing outranks everything: it is what the karaoke line actually needs.
_SRC_RANK = {'apple': 3, 'netease_yrc': 2, 'netease_lrc': 1, 'lrclib': 0}


def _sheet_rank(doc, src_name):
    return (1 if doc.get('has_word_timing') else 0, _SRC_RANK.get(src_name or '', 0))


def _existing_sheet(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# Everything downstream of the lyric sheet, in build order. author_data is NOT
# here on purpose: reopening it would throw away hand-written study content
# over a timing refetch, and the scaffold carries that content forward itself.
_LYRIC_CONSUMERS = ('whisper_sync', 'scaffold')


def _reopen_downstream(key):
    path = BUILDS / f'{key}.build_state.json'
    try:
        st = json.loads(path.read_text())
    except Exception:
        return []
    hit = []
    for s in st.get('steps', []):
        if s.get('key') in _LYRIC_CONSUMERS and s.get('status') == 'done':
            s['status'] = 'pending'
            s['note'] = ('[lyrics] open again — the timed lyric sheet was '
                         'refetched after this ran, and this step reads it.')
            hit.append(s['key'])
    if hit:
        path.write_text(json.dumps(st, ensure_ascii=False, indent=2) + '\n')
    return hit


def fetch_for_key(key, source='auto', force=False, file_path=None):
    if source == 'file' and not file_path:
        return False, f'[lyrics] --source file needs --file <path> ({BYO_FORMATS})'
    out_path = BUILDS / f'{key}.lyrics.json'
    if out_path.exists() and not force:
        return True, f'[lyrics] {out_path.name} already exists (use --force to refetch)'
    st = json.loads((BUILDS / f'{key}.build_state.json').read_text())
    meta = st.get('meta', {})
    title = meta.get('title_jp') or meta.get('title_en') or key
    artist = meta.get('artist') or meta.get('artist_en') or ''
    duration_ms, itunes_info = itunes_duration_ms(meta.get('apple', ''))
    if not duration_ms:
        # init persists the picked catalog candidate's duration_ms in meta —
        # the fallback when the apple URL is blank (better NetEase scoring +
        # LRCLIB exact-match instead of duration_ms=None).
        try:
            duration_ms = int(meta.get('duration_ms') or 0) or None
        except (TypeError, ValueError):
            duration_ms = None

    order = {'auto': ['apple', 'netease', 'lrclib']}.get(source, [source])
    tried = []
    # A sheet for a DIFFERENT recording (a live take, an instrumental) is worse
    # than a later source's sheet for the right one, so it never wins outright —
    # it is held here and only used if nothing clean turns up.
    spare = None
    busy = []            # sources that were rate-limiting, not missing

    def _write(doc, src_name, note=''):
        # A refetch must never quietly hand back a WORSE sheet than the one it
        # replaced. NetEase rate-limits bursts, so pressing "refetch lyrics" a
        # minute after adding a song routinely knocks a 25-line word-level YRC
        # down to a 17-line line-level LRC — same button, same wording, silently
        # coarser timing and junk "la la la" lines (caught rebuilding
        # イノチミジカシコイセヨオトメ, 2026-07-30). Busy is not an answer: keep
        # what we have and say when to try again.
        old = _existing_sheet(out_path)
        if old and busy and _sheet_rank(doc, src_name) < _sheet_rank(old, old.get('source')):
            return False, (
                f'[lyrics] kept the sheet you already have — {old.get("line_count")} lines '
                f'from {old.get("source")}. {"; ".join(busy)}, and the only sheet available '
                f'right now is {src_name} with {doc["line_count"]} lines, which is worse. '
                f'Nothing was changed. Try the refetch again in a minute.')
        # And keep the sheet being replaced. The guard above only catches the
        # downgrade it can see coming; a refetch that lands a legitimately
        # different sheet is still a one-way door without this.
        if old:
            (out_path.parent / f'{key}.lyrics.prev.json').write_text(
                json.dumps(old, ensure_ascii=False, indent=2) + '\n')
        out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
        # The steps that READ this sheet finished against the old one. Left
        # alone they keep their green dots and the box reports a song further
        # along than it is — the page would still be carrying the previous
        # sheet's words. Whatever changes an input reopens what consumed it.
        reopened = _reopen_downstream(key) if old else []
        if reopened:
            note = ((note + '\n') if note else '') + (
                '[lyrics] the sheet these ran against is gone, so they are open '
                'again: ' + ', '.join(reopened))
        lvl = ('word-level' if doc['has_word_timing']
               else 'line-level (whisper_sync --words upgrades it)')
        skipped = f'  (skipped: {", ".join(tried)})' if tried else ''
        return True, (f'[lyrics] {src_name}: {doc["line_count"]} lines, {lvl} → '
                      f'{out_path.name}{skipped}' + (f'\n{note}' if note else ''))

    for src in order:
        if src == 'file':
            try:
                got = try_file(file_path, title, artist, duration_ms)
            except ValueError as e:
                # A file the person chose is not a source that missed — say
                # what is wrong with it and stop, rather than falling through.
                return False, f'[lyrics] {e}'
        elif src == 'apple':
            got = try_apple(meta.get('apple', ''), title, artist, duration_ms)
            if got is None and not load_apple_config():
                tried.append('apple (no token — optional, see fetch_timed_lyrics.py docstring)')
                continue
        elif src == 'netease':
            try:
                got = try_netease(title, artist, duration_ms)
            except SourceBusy as e:
                busy.append(str(e))
                tried.append('netease (busy — rate-limited)')
                continue
        else:
            try:
                got = try_lrclib(title, artist, duration_ms)
            except SourceBusy as e:
                busy.append(str(e))
                tried.append('lrclib (no answer — down or too slow)')
                continue
        if not got:
            tried.append(f'{src} (no match)')
            continue
        lines, err = sanity(got['lines'], duration_ms)
        if err:
            tried.append(f'{src} ({err})')
            continue
        word_level = any(l.get('words') for l in lines)
        doc = {
            'song': got['song'],
            'lines': lines,
            'line_count': len(lines),
            'has_translations': any(l.get('translation') for l in lines),
            'has_word_timing': word_level,
            'languages': [],
            'source': got['source'],
            'stanzas': stanzas_by_gaps(lines),
        }
        wrong = variant_mismatch(title, got['song'].get('name', ''),
                                 got['song'].get('album', ''))
        if wrong:
            # Hold it and keep looking. Only the first one is worth keeping —
            # they are all the same kind of wrong.
            if spare is None:
                spare = (doc, got['source'], wrong)
            tried.append(f'{src} (different recording)')
            continue
        return _write(doc, got['source'])
    later = (f' {"; ".join(busy)} — the good sheet may be there in a minute, '
             f'so refetch then.') if busy else ''
    if spare is not None:
        doc, src_name, wrong = spare
        doc['song']['variant_warning'] = wrong + ('. A better source was busy'
                                                  if busy else '')
        return _write(doc, src_name,
                      f'[lyrics] HEADS UP — {wrong}. Its lines will not sit on this '
                      f'video, and the line-up step will say so.{later or " Refetch later if the other sources come back."}')
    return False, (f'[lyrics] no timed lyrics found for "{title}" / "{artist}" — tried: '
                   f'{"; ".join(tried)}.{later}\nOptions: check the title/artist spelling in the build, '
                   f'add an Apple token (best coverage), or bring your own sheet — '
                   f'fetch_timed_lyrics.py {key} --source file --file <{BYO_FORMATS}>.')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('key')
    ap.add_argument('--source', default='auto',
                    choices=['auto', 'apple', 'netease', 'lrclib', 'file'])
    ap.add_argument('--file', help=f'sheet to import with --source file ({BYO_FORMATS})')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    ok, msg = fetch_for_key(a.key, a.source, a.force, a.file)
    print(msg)
    sys.exit(0 if ok else 2)


if __name__ == '__main__':
    main()
