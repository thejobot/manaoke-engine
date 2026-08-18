#!/usr/bin/env python3
"""
timing_edit.py — the ONLY writer for line timings in builds/<key>.lyrics.json.

The denmoku timing tab (docs/denmoku-v2.1-addendum.md) shells this tool; the
server never edits state files directly. Verbs:

  timing_edit.py <key> set   <line_idx> --begin <ms> --end <ms>
  timing_edit.py <key> adopt <line_idx> --source lrclib [--delta <ms>]
  timing_edit.py <key> word  <line_idx> <word_idx> --begin <ms> [--end <ms>]
  timing_edit.py <key> hold  <line_idx> <word_idx> --at <ms> | --clear
  timing_edit.py <key> worddel  <line_idx> <word_idx>
  timing_edit.py <key> wordadd  <line_idx> <word_idx> --text T
                                [--where after|before] [--reading KANA]
  timing_edit.py <key> wordedit <line_idx> <word_idx> [--text T]
                                [--reading KANA] [--line-kana KANA]

hold marks the ms where a word's lexical morae end and a HELD sung vowel
begins (ほど…おおお): stored as words[wi].hold_ms + a scope='hold' sidecar
entry; content_to_data packs the morae into [begin, hold] and stretches the
final mora to the word end, so the page's karaoke fill sings the held vowel.

worddel / wordadd / wordedit are WORD-LIST edits (drop a stray 、 token, add
an ad-lib like "hey", fix a token's text/reading). They keep line text ↔
words walk-coherent (validate E10), mirror the new text into
builds/<key>.content.json, migrate existing sidecar entries to the new
key/indices, and record a scope='textop' replay entry so refetches/re-aligns
reproduce the edit.

set/adopt rewrite that line's begin/end in builds/<key>.lyrics.json and carry
the line's EXISTING word times along (delta-shift; begin-anchored proportional
squeeze/stretch only when the duration changes — see
timing_overrides.retime_line_words. The old behavior mora-redistributed the
words, destroying real CTC onsets on every nudge). `word` sets one word's
begin (and optionally end) absolutely. Monotonicity vs neighbours is enforced:
a small overlap is CLAMPED to the neighbour's edge (and reported); a line
clamp that would empty the line is REFUSED with a clear error; word clamps
never refuse.

EVERY edit is also RECORDED to builds/<key>.timing_overrides.json (the
sidecar timing_overrides.py owns), keyed by normalized line text — so
whisper_sync re-runs and lyric refetches re-apply the human decision instead
of clobbering it.

`adopt` re-times the line's begin to the LRCLIB synced-lyrics begin for the
matching text (+ delta; default delta = the median offset over all matched
lines, so a global sync shift is preserved). The LRCLIB fetch is cached under
builder/cache/ (gitignored). The server imports the lrclib_rows / match_lrclib
helpers for its /api/timing read model.

Stdlib only (runs under plain python3, like the server).
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent          # tools/songcraft
BUILDS = HERE / 'builds'
CACHE_DIR = HERE / 'builder' / 'cache'          # gitignored

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import state_io                                  # noqa: E402  (locked build_state)
import timing_overrides                          # noqa: E402  (sidecar owner)
from timing_overrides import norm_text          # noqa: E402  (shared normalizer)


# ── lyrics.json I/O ──────────────────────────────────────────────────────

def lyrics_path(key):
    return BUILDS / f'{key}.lyrics.json'


def load_lyrics(key):
    p = lyrics_path(key)
    if not p.exists():
        sys.exit(f'no lyrics for {key!r}: {p} does not exist')
    return json.loads(p.read_text())


# The steps that bake the lyric sheet's numbers into the shipped page. Audio
# is deliberately absent: a clip's bytes come from the WORDS, not from when
# they are sung, so moving a line never invalidates a recording.
TAIL = ('assemble', 'drill_concat', 'reassemble', 'validate',
        'landing_card', 'deploy')


def reopen_downstream(key):
    """Put the steps that consumed the old timings back to pending.

    Whatever changes an input reopens what consumed it — the lyric refetch has
    done this since it was written, and the study-text writer does it too, but
    the timing editor never did. So a nudge left the built page carrying the
    old numbers while the box still showed every step green, and the validator
    then judged a page that no longer matched its own sheet: it failed
    strawberry-anniversary on line durations that had already been fixed
    (2026-07-30). Returns the step keys it reopened."""
    path = BUILDS / f'{key}.build_state.json'
    try:
        st = json.loads(state_io.locked_read(path))
    except Exception:
        return []
    hit = []
    for s in st.get('steps', []):
        if s.get('key') in TAIL and s.get('status') == 'done':
            s['status'] = 'pending'
            s['note'] = ('[timing] open again — the timings changed after this '
                         'ran, and this step bakes them into the page.')
            hit.append(s['key'])
    if hit:
        state_io.locked_write(path, st)     # the server writes this too
    return hit


def save_lyrics(key, doc):
    # Same shape the writers use (lrclib_to_lyrics / LyriCool export): 2-space
    # indent, non-ASCII kept literal.
    lyrics_path(key).write_text(json.dumps(doc, ensure_ascii=False, indent=2))
    hit = reopen_downstream(key)
    if hit:
        print('[timing] these bake the timings into the page, so they are open '
              'again: ' + ', '.join(hit))


# ── LRCLIB (cached under builder/cache/) ─────────────────────────────────

def _lrclib_pick(items, duration_ms):
    """Best search hit: synced lyrics required; duration within ±3s of the
    build's duration preferred (closest wins), else the first synced hit."""
    synced = [it for it in items if it.get('syncedLyrics')]
    if not synced:
        return None
    if duration_ms:
        close = [it for it in synced
                 if abs((it.get('duration') or 0) * 1000 - duration_ms) <= 3000]
        if close:
            return min(close, key=lambda it: abs((it.get('duration') or 0) * 1000
                                                 - duration_ms))
    return synced[0]


LRC_RE = re.compile(r'^\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$')   # lrclib_to_lyrics


def _parse_lrc(synced):
    rows = []
    for ln in (synced or '').split('\n'):
        m = LRC_RE.match(ln.strip())
        if not m:
            continue
        begin = int(int(m.group(1)) * 60000 + float(m.group(2)) * 1000)
        text = m.group(3).strip()
        if text:
            rows.append({'begin_ms': begin, 'text': text})
    rows.sort(key=lambda r: r['begin_ms'])
    return rows


def _build_meta(key):
    """title/artist for the LRCLIB query, from the build_state meta."""
    try:
        st = json.loads((BUILDS / f'{key}.build_state.json').read_text())
        m = st.get('meta') or {}
        return m.get('title_jp') or '', m.get('artist') or ''
    except Exception:
        return '', ''


def lrclib_rows(key, doc=None, timeout=6.0):
    """Parsed LRCLIB synced lines [{begin_ms, text}] for <key>, disk-cached at
    builder/cache/lrclib_<key>.json (a hit is cached; a miss is not, so a song
    that later appears on LRCLIB is retried). Returns [] on no match."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cpath = CACHE_DIR / f'lrclib_{key}.json'
    if cpath.exists():
        try:
            return json.loads(cpath.read_text()).get('rows') or []
        except Exception:
            pass
    if doc is None:
        doc = load_lyrics(key)
    title, artist = _build_meta(key)
    song = doc.get('song') or {}
    title = title or song.get('name') or ''
    artist = artist or song.get('artist') or ''
    if not title:
        return []
    duration_ms = int(song.get('duration_ms') or 0)
    qs = urllib.parse.urlencode({'track_name': title, 'artist_name': artist})
    try:
        with urllib.request.urlopen(f'https://lrclib.net/api/search?{qs}',
                                    timeout=timeout) as r:
            items = json.loads(r.read().decode('utf-8', errors='replace'))
    except Exception:
        return []
    best = _lrclib_pick(items if isinstance(items, list) else [], duration_ms)
    if best is None:
        return []
    rows = _parse_lrc(best.get('syncedLyrics'))
    if rows:
        cpath.write_text(json.dumps({
            'fetched': int(time.time()),
            'query': {'track_name': title, 'artist_name': artist,
                      'duration_ms': duration_ms},
            'match': {k: best.get(k) for k in
                      ('id', 'trackName', 'artistName', 'albumName', 'duration')},
            'rows': rows,
        }, ensure_ascii=False, indent=1))
    return rows


def match_lrclib(lines, rows):
    """Sequential text match of build lines against LRCLIB rows: each build
    line (in order) consumes the next unused LRCLIB row with the same
    normalized text, so repeated hook lines pair up 1:1 in order.
    Returns ([lrclib_begin_ms | None] per line, median_delta_ms | None) where
    median_delta = median(line begin − lrclib begin) over the matched
    ("trusted") lines — the song's global sync offset."""
    pool = {}
    for r in rows:
        pool.setdefault(norm_text(r['text']), []).append(r['begin_ms'])
    out, deltas = [], []
    for ln in lines:
        k = norm_text(ln.get('text') or '')
        bucket = pool.get(k)
        if k and bucket:
            b = bucket.pop(0)
            out.append(b)
            if isinstance(ln.get('begin_ms'), (int, float)):
                deltas.append(int(ln['begin_ms']) - b)
        else:
            out.append(None)
    median = None
    if deltas:
        s = sorted(deltas)
        mid = len(s) // 2
        median = int(s[mid]) if len(s) % 2 else int(round((s[mid - 1] + s[mid]) / 2))
    return out, median


# ── the two verbs ────────────────────────────────────────────────────────

def _stamp_edit(key):
    """builds/<key>.last_edit — epoch of the last HUMAN edit. The ladder's
    "last saved" clock reads this instead of file mtimes, which machine
    writers (refetch --force, whisper_sync --apply) also bump."""
    try:
        (BUILDS / f'{key}.last_edit').write_text(str(int(time.time())) + '\n')
    except OSError:
        pass


def _journal(key, summary, detail=''):
    """Best-effort append to the lessons journal (lessons.py) — every manual
    timing fix is a place the aligner was wrong. A journal failure never
    breaks the edit itself."""
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import lessons
        lessons.journal('timing', key, summary, detail=detail,
                        source='timing_edit')
    except Exception:
        pass


def _clamp_neighbours(lines, idx, begin, end):
    """Clamp [begin, end) against the previous line's end and the next line's
    begin. Returns (begin, end, notes) or exits with a clear error when the
    clamp would leave no window (a real overlap, not a nudge)."""
    notes = []
    prev = lines[idx - 1] if idx > 0 else None
    nxt = lines[idx + 1] if idx + 1 < len(lines) else None
    if prev is not None and begin < int(prev['end_ms']):
        clamped = int(prev['end_ms'])
        if clamped >= end:
            sys.exit(f'refused: line {idx} begin {begin}ms overlaps line {idx-1} '
                     f'(ends {prev["end_ms"]}ms) past this line\'s end {end}ms — '
                     f'move line {idx-1} first.')
        notes.append(f'begin clamped {begin}→{clamped}ms (line {idx-1} ends there)')
        begin = clamped
    if nxt is not None and end > int(nxt['begin_ms']):
        clamped = int(nxt['begin_ms'])
        if clamped <= begin:
            sys.exit(f'refused: line {idx} end {end}ms overlaps line {idx+1} '
                     f'(begins {nxt["begin_ms"]}ms) before this line\'s begin '
                     f'{begin}ms — move line {idx+1} first.')
        notes.append(f'end clamped {end}→{clamped}ms (line {idx+1} begins there)')
        end = clamped
    return begin, end, notes


def _occurrence(lines, idx):
    """Occurrence index of line <idx> among lines with the same normalized
    text — which entry of the sidecar's (line_key, ...) stream this edit is."""
    k = norm_text(lines[idx].get('text') or '')
    return sum(1 for j in range(idx)
               if norm_text(lines[j].get('text') or '') == k)


def _apply(key, doc, idx, begin, end, notes, how='manual nudge'):
    ln = doc['lines'][idx]
    old_b, old_e = int(ln['begin_ms']), int(ln['end_ms'])
    # Words that carry their own sidecar override keep their ABSOLUTE times;
    # everything else delta-shifts (or begin-anchored-scales) with the line.
    pins = timing_overrides.word_pins_for_line(key, doc['lines'], idx)
    rnotes = timing_overrides.retime_line_words(ln, begin, end, pinned=pins)
    save_lyrics(key, doc)
    timing_overrides.record(key, ln.get('text') or '', None, begin, end, 'line',
                            note=how, occ=_occurrence(doc['lines'], idx))
    for n in notes + rnotes:
        print(f'note: {n}')
    print(f'line {idx} {ln["text"][:32]!r}: {old_b}→{begin}ms .. {old_e}→{end}ms '
          f'({len(ln.get("words") or [])} words carried along'
          + (f', {len(pins)} pinned' if pins else '')
          + '; recorded to sidecar)')
    _journal(key, f'{key} line {idx} begin {old_b}→{begin} end {old_e}→{end} ({how})',
             detail='; '.join([f'text {ln["text"][:32]!r}'] + notes + rnotes))


def _line_index(doc, raw):
    try:
        idx = int(raw)
    except ValueError:
        sys.exit(f'line index must be an integer, got {raw!r}')
    if not 0 <= idx < len(doc['lines']):
        sys.exit(f'line index {idx} out of range (0..{len(doc["lines"]) - 1})')
    return idx


def cmd_set(key, args):
    doc = load_lyrics(key)
    idx = _line_index(doc, args.line_idx)
    begin, end = args.begin, args.end
    if begin is None or end is None:
        sys.exit('set needs both --begin and --end (ms)')
    if begin < 0:
        sys.exit(f'--begin {begin} is negative')
    if begin >= end:
        sys.exit(f'--begin {begin} must be < --end {end}')
    begin, end, notes = _clamp_neighbours(doc['lines'], idx, begin, end)
    _apply(key, doc, idx, begin, end, notes)


def cmd_adopt(key, args):
    if args.source != 'lrclib':
        sys.exit(f'unknown --source {args.source!r} (only lrclib is wired up)')
    doc = load_lyrics(key)
    idx = _line_index(doc, args.line_idx)
    rows = lrclib_rows(key, doc)
    if not rows:
        sys.exit(f'no LRCLIB synced lyrics found for {key!r} — nothing to adopt.')
    begins, median = match_lrclib(doc['lines'], rows)
    lb = begins[idx]
    if lb is None:
        sys.exit(f'line {idx} {doc["lines"][idx]["text"][:32]!r} has no LRCLIB '
                 f'text match — adopt needs a matching source line.')
    delta = args.delta if args.delta is not None else (median or 0)
    ln = doc['lines'][idx]
    begin = lb + delta
    end = int(ln['end_ms'])                    # LRC carries begins only
    if begin >= end:
        sys.exit(f'refused: adopted begin {begin}ms (lrclib {lb} + delta {delta}) '
                 f'is not before this line\'s end {end}ms.')
    begin, end, notes = _clamp_neighbours(doc['lines'], idx, begin, end)
    notes.insert(0, f'adopted lrclib begin {lb}ms + delta {delta}ms'
                    + ('' if args.delta is not None else f' (median over matches)'))
    _apply(key, doc, idx, begin, end, notes, how='adopt lrclib')


def cmd_word(key, args):
    """Set ONE word's begin (and optionally end) absolutely — the drag-a-word
    edit. Clamped inside the line window and monotonic vs neighbour words
    (clamp + report, never refuse); the previous word's end follows the new
    begin so spans stay continuous. Writes lyrics.json AND records a
    scope='word' sidecar entry so re-alignments re-apply it."""
    doc = load_lyrics(key)
    idx = _line_index(doc, args.line_idx)
    ln = doc['lines'][idx]
    words = ln.get('words') or []
    try:
        wi = int(args.word_idx)
    except ValueError:
        sys.exit(f'word index must be an integer, got {args.word_idx!r}')
    if not words:
        sys.exit(f'line {idx} {ln.get("text","")[:32]!r} has no words[] — '
                 f'run whisper_sync --words first')
    if not 0 <= wi < len(words):
        sys.exit(f'word index {wi} out of range (0..{len(words) - 1})')
    if args.begin < 0:
        sys.exit(f'--begin {args.begin} is negative')
    if args.end is not None and args.end < args.begin:
        sys.exit(f'--end {args.end} must be >= --begin {args.begin}')
    old_b, old_e = int(words[wi]['begin_ms']), int(words[wi]['end_ms'])
    notes = timing_overrides.apply_word_override(ln, wi, args.begin, args.end)
    save_lyrics(key, doc)
    # record the CLAMPED values so re-application is idempotent; end stays
    # None when the human never set one (it keeps floating with alignments).
    timing_overrides.record(
        key, ln.get('text') or '', wi, words[wi]['begin_ms'],
        None if args.end is None else words[wi]['end_ms'],
        'word', note='manual word nudge', occ=_occurrence(doc['lines'], idx))
    for n in notes:
        print(f'note: {n}')
    print(f'line {idx} word {wi} {words[wi].get("text","")!r}: '
          f'begin {old_b}→{words[wi]["begin_ms"]}ms, '
          f'end {old_e}→{words[wi]["end_ms"]}ms (recorded to sidecar)')
    _journal(key, f'{key} line {idx} word {wi} begin {old_b}→'
                  f'{words[wi]["begin_ms"]} end {old_e}→{words[wi]["end_ms"]} '
                  f'(manual word nudge)',
             detail='; '.join([f'word {words[wi].get("text","")!r} in '
                               f'{ln.get("text","")[:32]!r}'] + notes))


def _word_index(ln, raw, idx):
    words = ln.get('words') or []
    if not words:
        sys.exit(f'line {idx} {ln.get("text", "")[:32]!r} has no words[] — '
                 f'run whisper_sync --words first')
    try:
        wi = int(raw)
    except ValueError:
        sys.exit(f'word index must be an integer, got {raw!r}')
    if not 0 <= wi < len(words):
        sys.exit(f'word index {wi} out of range (0..{len(words) - 1})')
    return wi


def cmd_hold(key, args):
    """Mark (or clear) the held-vowel point inside one word — the ms where
    the lexical morae stop and the sung vowel extension begins."""
    doc = load_lyrics(key)
    idx = _line_index(doc, args.line_idx)
    ln = doc['lines'][idx]
    wi = _word_index(ln, args.word_idx, idx)
    w = ln['words'][wi]
    occ = _occurrence(doc['lines'], idx)
    if args.clear:
        had = w.pop('hold_ms', None)
        save_lyrics(key, doc)
        removed = timing_overrides.remove_entries(key, ln.get('text') or '',
                                                  wi, 'hold', occ=occ)
        if not removed and occ > 0:
            # record() appends past-the-stream entries at the tail — a
            # repeated-line hold may sit at an earlier slot than occ says
            removed = timing_overrides.remove_entries(
                key, ln.get('text') or '', wi, 'hold', occ=0)
        if had is not None and not removed:
            print(f'⚠ the hold came off this line but NO sidecar entry was '
                  f'found to remove — check {timing_overrides.path(key).name} '
                  f'by hand (repeated-line holds can bind to the other copy)')
        print(f'line {idx} word {wi} {w.get("text","")!r}: held part cleared'
              + ('' if had is not None else ' (none was set)'))
        return
    if args.at is None:
        sys.exit('hold needs --at <ms> (or --clear)')
    b, e = int(w['begin_ms']), int(w['end_ms'])
    w['hold_ms'] = int(args.at)
    timing_overrides.clamp_hold(w)
    if w.get('hold_ms') is None:
        sys.exit(f'word {wi} window {b}–{e}ms is too small to hold a held part')
    save_lyrics(key, doc)
    timing_overrides.record(key, ln.get('text') or '', wi, w['hold_ms'], None,
                            'hold', note='held vowel (sung extension)', occ=occ)
    print(f'line {idx} word {wi} {w.get("text","")!r}: sings through '
          f'{tmfmt(w["hold_ms"])} — words end there, the vowel holds to '
          f'{tmfmt(e)} (recorded to sidecar)')
    _journal(key, f'{key} line {idx} word {wi} hold at {w["hold_ms"]}ms',
             detail=f'word {w.get("text","")!r} window {b}-{e}ms')


def tmfmt(ms):
    s = (int(ms) % 60000) / 1000
    return f'{int(ms) // 60000}:{s:04.1f}'


def _mirror_content(key, idx, old_text, new_text, line_kana=None,
                    coverage_add=None, jp_changed=False):
    """Mirror a lyric-text edit into builds/<key>.content.json (the authored
    side: page kanji line, LINE_TR keys, kana reading, coverage). Index join
    first (assemble pairs lines in order), normalized-text fallback. A missing
    content line is a WARN, not a failure — mid-pipeline songs have none."""
    p = BUILDS / f'{key}.content.json'
    try:
        c = json.loads(p.read_text())
    except Exception:
        print(f'note: no content.json for {key!r} — page text not mirrored')
        return
    lines = c.get('lines') or []
    ok = norm_text
    ci = None
    if idx < len(lines) and ok(lines[idx].get('jp') or '') == ok(old_text):
        ci = idx
    else:
        hits = [j for j, l in enumerate(lines)
                if ok(l.get('jp') or '') == ok(old_text)]
        if len(hits) == 1:
            ci = hits[0]
    if ci is None:
        print(f'⚠ content.json line for {old_text[:24]!r} not found — page '
              f'text NOT mirrored (fix content.json by hand before assemble)')
        return
    if new_text is not None:
        if jp_changed and line_kana is None \
                and (lines[ci].get('kana') or '').strip():
            print(f'⚠ the line\'s full reading (kana) is now STALE — it '
                  f'doesn\'t know about this word change. Fix it with '
                  f'wordedit --line-kana (the editor\'s "full line reading" '
                  f'box).')
        lines[ci]['jp'] = new_text
    if line_kana is not None:
        lines[ci]['kana'] = line_kana
    if coverage_add:
        exc = c.setdefault('coverage_exceptions', [])
        if coverage_add not in exc:
            exc.append(coverage_add)
            print(f'note: {coverage_add!r} added to coverage_exceptions — it '
                  f'ships without a study card; make it a study word later if '
                  f'it deserves one')
    p.write_text(json.dumps(c, ensure_ascii=False, indent=2))
    print(f'content.json line {ci} mirrored'
          + (f' (kana updated)' if line_kana is not None else ''))


_JP_CHARS = re.compile(r'[぀-ヿ㐀-鿿]')


def _finish_text_op(key, doc, idx, old_text, op, shift_from=None, shift=0,
                    drop_word_idx=None, line_kana=None, coverage_add=None):
    """Shared tail of the word-list verbs: apply, save, migrate sidecar
    entries, record the replay op, mirror content.json, journal."""
    ln = doc['lines'][idx]
    occ = _occurrence(doc['lines'], idx)
    try:
        notes = timing_overrides.apply_text_op(ln, op)
    except ValueError as ex:
        sys.exit(f'refused: {ex}')
    save_lyrics(key, doc)
    moved, warn = timing_overrides.migrate_entries(
        key, old_text, ln.get('text') or '', shift_from=shift_from,
        shift=shift, drop_word_idx=drop_word_idx)
    if warn:
        print(f'⚠ {warn}')
    timing_overrides.record_text_op(key, old_text, op,
                                    note=op.get('kind', ''), occ=occ,
                                    after_text=ln.get('text') or '')
    jp_changed = bool(_JP_CHARS.search((op.get('new_text') or '')
                                       + (op.get('word_text') or '')))
    _mirror_content(key, idx, old_text, ln.get('text') or '',
                    line_kana=line_kana, coverage_add=coverage_add,
                    jp_changed=jp_changed)
    for n in notes:
        print(f'note: {n}')
    print(f'line {idx}: {ln.get("text","")!r} '
          f'({len(ln.get("words") or [])} words; {moved} override(s) '
          f'migrated; recorded to sidecar)')
    _journal(key, f'{key} line {idx} {op.get("kind")} word '
                  f'{op.get("word_text","")!r}'
                  + (f' → {op.get("new_text")!r}' if op.get('new_text') else ''),
             detail='; '.join([f'text now {ln.get("text","")[:40]!r}'] + notes))


def cmd_worddel(key, args):
    doc = load_lyrics(key)
    idx = _line_index(doc, args.line_idx)
    ln = doc['lines'][idx]
    wi = _word_index(ln, args.word_idx, idx)
    old_text = ln.get('text') or ''
    op = {'kind': 'del', 'word_idx': wi,
          'word_text': ln['words'][wi].get('text') or ''}
    _finish_text_op(key, doc, idx, old_text, op,
                    shift_from=wi + 1, shift=-1, drop_word_idx=wi)


def cmd_wordadd(key, args):
    doc = load_lyrics(key)
    idx = _line_index(doc, args.line_idx)
    ln = doc['lines'][idx]
    wi = _word_index(ln, args.word_idx, idx)
    new = (args.text or '').strip()
    if not new:
        sys.exit('wordadd needs --text')
    old_text = ln.get('text') or ''
    op = {'kind': 'add', 'word_idx': wi,
          'word_text': ln['words'][wi].get('text') or '',
          'new_text': new, 'where': args.where}
    if args.reading:
        op['reading'] = args.reading.strip()
    ins = wi if args.where == 'before' else wi + 1
    # a Japanese ad-lib with no study card would fail coverage (E1) at the
    # next validate — declare it a vocalise like とぅるるる unless/until the owner
    # gives it a study word.
    coverage = new if _JP_CHARS.search(new) else None
    _finish_text_op(key, doc, idx, old_text, op,
                    shift_from=ins, shift=1, coverage_add=coverage)


def cmd_wordedit(key, args):
    doc = load_lyrics(key)
    idx = _line_index(doc, args.line_idx)
    ln = doc['lines'][idx]
    wi = _word_index(ln, args.word_idx, idx)
    old_text = ln.get('text') or ''
    cur = ln['words'][wi].get('text') or ''
    new = (args.text or '').strip() or cur
    if new == cur and args.reading is None and args.line_kana is None:
        sys.exit('wordedit changed nothing (give --text, --reading or --line-kana)')
    op = {'kind': 'edit', 'word_idx': wi, 'word_text': cur, 'new_text': new}
    if args.reading is not None:
        op['reading'] = args.reading.strip() or None
    _finish_text_op(key, doc, idx, old_text, op,
                    line_kana=args.line_kana)


def main():
    ap = argparse.ArgumentParser(description='Edit line timings in builds/<key>.lyrics.json')
    ap.add_argument('key')
    sub = ap.add_subparsers(dest='verb', required=True)
    s = sub.add_parser('set', help='set a line window explicitly')
    s.add_argument('line_idx')
    s.add_argument('--begin', type=int, required=True, help='new begin (ms)')
    s.add_argument('--end', type=int, required=True, help='new end (ms)')
    a = sub.add_parser('adopt', help='adopt a lyric-source begin for a line')
    a.add_argument('line_idx')
    a.add_argument('--source', default='lrclib')
    a.add_argument('--delta', type=int, default=None,
                   help='offset added to the source begin (default: the median '
                        'offset over all matched lines)')
    w = sub.add_parser('word', help="set one word's begin/end absolutely "
                                    '(records a durable sidecar override)')
    w.add_argument('line_idx')
    w.add_argument('word_idx')
    w.add_argument('--begin', type=int, required=True, help='new word begin (ms)')
    w.add_argument('--end', type=int, default=None, help='new word end (ms, optional)')
    h = sub.add_parser('hold', help="mark where a word's held sung vowel starts")
    h.add_argument('line_idx')
    h.add_argument('word_idx')
    h.add_argument('--at', type=int, default=None, help='hold point (ms, absolute)')
    h.add_argument('--clear', action='store_true', help='remove the held part')
    d = sub.add_parser('worddel', help='delete one token (text + words stay coherent)')
    d.add_argument('line_idx')
    d.add_argument('word_idx')
    aw = sub.add_parser('wordadd', help='insert a token next to an existing one')
    aw.add_argument('line_idx')
    aw.add_argument('word_idx')
    aw.add_argument('--text', required=True, help='the new token text (e.g. hey)')
    aw.add_argument('--where', choices=('after', 'before'), default='after')
    aw.add_argument('--reading', default=None, help='kana reading override')
    we = sub.add_parser('wordedit', help="change a token's text / reading")
    we.add_argument('line_idx')
    we.add_argument('word_idx')
    we.add_argument('--text', default=None, help='new token text')
    we.add_argument('--reading', default=None,
                    help='kana reading override for this token ("" clears it)')
    we.add_argument('--line-kana', dest='line_kana', default=None,
                    help='replace the full line reading (content.json kana)')
    args = ap.parse_args()
    if args.verb == 'set':
        cmd_set(args.key, args)
    elif args.verb == 'word':
        cmd_word(args.key, args)
    elif args.verb == 'hold':
        cmd_hold(args.key, args)
    elif args.verb == 'worddel':
        cmd_worddel(args.key, args)
    elif args.verb == 'wordadd':
        cmd_wordadd(args.key, args)
    elif args.verb == 'wordedit':
        cmd_wordedit(args.key, args)
    else:
        cmd_adopt(args.key, args)
    _stamp_edit(args.key)          # every verb that returns is a human edit


if __name__ == '__main__':
    main()
