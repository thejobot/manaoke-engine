#!/usr/bin/env python3
"""timing_overrides.py — the sidecar that makes MANUAL timing edits durable.

the owner's requirement: "physically move a word and see the waveform — and manual
edits are remembered. A pipeline that clobbers a human decision is broken."
Today every writer clobbers: whisper_sync --apply rewrites line begins/ends,
whisper_sync --words --apply regenerates every words[] wholesale, and
fetch_timed_lyrics --force replaces the whole lyrics.json. The only record of
a hand fix was prose in builds/lessons.jsonl — not machine-reapplicable.

This module owns builds/<key>.timing_overrides.json — one entry per human
decision, keyed by NORMALIZED LINE TEXT (not line index), so entries survive
line-list changes and full refetches. Every writer re-applies the sidecar at
its output edge (whisper_sync before each --apply write; content_to_data as
belt-and-suspenders before kana_timings derivation); timing_edit.py is the
writer OF the sidecar (set/adopt/word all record here).

Schema (version 1):
  {"version": 1, "entries": [
      {"line_key":  "<normalized line text — norm_text() below>",
       "word_idx":  int | null,          # null = line-level entry
       "begin_ms":  int,
       "end_ms":    int | null,          # null = leave the existing end alone
       "scope":     "word" | "line" | "hold",
       "ts":        int,                 # epoch seconds of the human decision
       "note":      "free text"}]}

Two later additions (still version 1 — additive, older readers skip them):
  scope "hold" — a held/sung vowel point INSIDE a word (the "hodo…ooo" case):
      begin_ms = the ms where the lexical morae end and the held vowel starts;
      end_ms = null. Re-apply sets words[wi]["hold_ms"] (clamped into the word).
  scope "textop" — a WORD-LIST edit (delete a stray 、token / add an ad-lib
      like "hey" / rename a token). Not a timing entry at all; carries an
      "op" dict instead of begin/end:
      {"line_key": <normalized PRE-edit text>, "scope": "textop", "ts", "note",
       "op": {"kind": "del"|"add"|"edit", "word_idx": int,
              "word_text": <token text expected at word_idx>,
              "new_text": str|null,      # add/edit
              "reading": str|null,       # add/edit — per-word kana override
              "where": "before"|"after"} # add only
      }
      Replayed FIRST (in file order, each resolving against the then-current
      text) so a lyric refetch/re-align gets the same word list back; the
      timing entries recorded after the edit are keyed by the POST-edit text
      (record-time migration rewrites older entries' keys/indices).

Duplicate lines (repeated hooks): the n-th entry of a given (line_key,
word_idx, scope) stream binds to the n-th line whose normalized text matches
— first unconsumed match, in order. Limitation of the text-keyed schema:
overriding ONLY the 2nd occurrence of a repeated line (never the 1st) will
re-apply to the 1st; record() warns loudly when that can happen.

Entries whose line text no longer exists (or whose word_idx is out of range)
are ORPHANS: reported loudly, never applied, and NEVER dropped from the file
— a refetched lyric set may bring the text back.

Stdlib only (imported by timing_edit/server python3 AND the parler-env
aligner scripts).
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

BUILDS = Path(__file__).resolve().parent / 'builds'
VERSION = 1

_XN = re.compile(r'\s*\(×\d+\)\s*$')
_WS = re.compile(r'\s+')


def norm_text(s):
    """THE shared line normalizer: strip a trailing (×N) repeat marker + ALL
    whitespace. Byte-identical policy to whisper_sync._gate_norm,
    content_to_data.line_tr_key and validate_song.line_tr_key — every
    consumer of line_key must agree on this."""
    return _WS.sub('', _XN.sub('', s or '')).strip()


def path(key):
    return BUILDS / f'{key}.timing_overrides.json'


def load(key):
    """Sidecar dict for <key> ({version, entries}); empty when absent. An
    unreadable file is reported loudly and treated as empty for APPLY, but
    save()/record() refuse to overwrite it (never destroy human decisions)."""
    p = path(key)
    if not p.exists():
        return {'version': VERSION, 'entries': []}
    try:
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            raise ValueError('not a JSON object')
    except Exception as ex:
        print(f'[overrides] ⚠ UNREADABLE {p} ({ex}) — treating as empty; '
              f'writes to it are REFUSED until fixed', file=sys.stderr)
        return {'version': VERSION, 'entries': [], '_unreadable': True}
    data.setdefault('version', VERSION)
    data.setdefault('entries', [])
    return data


def save(key, data):
    """Atomic write (temp file + os.replace) so a crash mid-save can never
    leave a torn sidecar. Refuses to clobber a file load() found unreadable."""
    if data.get('_unreadable'):
        raise SystemExit(f'[overrides] refusing to overwrite unreadable '
                         f'{path(key)} — fix or remove it first')
    out = {k: v for k, v in data.items() if not str(k).startswith('_')}
    out['version'] = VERSION
    p = path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.write('\n')
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record(key, line_text, word_idx, begin_ms, end_ms, scope, note='', occ=0):
    """Upsert one human decision. line_text may be raw (normalized here).
    occ = occurrence index of the edited line among same-normalized-text
    lines: it replaces the occ-th existing entry of this (line_key, word_idx,
    scope) stream, or appends when the stream is shorter. Returns the entry."""
    if scope not in ('word', 'line', 'hold'):
        raise SystemExit(f'[overrides] bad scope {scope!r}')
    lk = norm_text(line_text)
    if not lk:
        raise SystemExit('[overrides] refusing to record an empty line_key')
    data = load(key)
    if data.get('_unreadable'):
        raise SystemExit(f'[overrides] refusing to write over unreadable '
                         f'{path(key)} — fix or remove it first')
    wi = None if word_idx is None else int(word_idx)
    entry = {'line_key': lk, 'word_idx': wi, 'begin_ms': int(begin_ms),
             'end_ms': None if end_ms is None else int(end_ms),
             'scope': scope, 'ts': int(time.time()), 'note': note or ''}
    entries = data['entries']
    matches = [i for i, e in enumerate(entries)
               if norm_text(e.get('line_key') or '') == lk
               and e.get('word_idx') == wi and (e.get('scope') or '') == scope]
    if occ < len(matches):
        entries[matches[occ]] = entry
    else:
        if occ > len(matches):
            print(f'[overrides] ⚠ {key}: this line text repeats and only '
                  f'{len(matches)} earlier entr{"y" if len(matches)==1 else "ies"} '
                  f'exist for it — the new entry will re-apply to occurrence '
                  f'{len(matches)}, not {occ} (text-keyed schema binds '
                  f'duplicates in order)', file=sys.stderr)
        entries.append(entry)
    save(key, data)
    return entry


def record_text_op(key, line_text, op, note='', occ=0, after_text=None):
    """Record one word-list edit (scope 'textop'). line_text = the PRE-edit
    line text (the key a replay resolves against); after_text = the POST-edit
    text, so a replay over an already-edited doc recognises its own work and
    skips silently instead of orphaning. Appends — text ops are a replay LOG,
    not an upsert (two dels on one line are two entries)."""
    lk = norm_text(line_text)
    if not lk:
        raise SystemExit('[overrides] refusing to record an empty line_key')
    if (op or {}).get('kind') not in ('del', 'add', 'edit'):
        raise SystemExit(f'[overrides] bad textop kind {(op or {}).get("kind")!r}')
    data = load(key)
    if data.get('_unreadable'):
        raise SystemExit(f'[overrides] refusing to write over unreadable '
                         f'{path(key)} — fix or remove it first')
    entry = {'line_key': lk, 'word_idx': None, 'begin_ms': None, 'end_ms': None,
             'scope': 'textop', 'ts': int(time.time()), 'note': note or '',
             'occ': int(occ), 'op': dict(op)}
    if after_text is not None:
        entry['line_key_after'] = norm_text(after_text)
    data['entries'].append(entry)
    save(key, data)
    return entry


def remove_entries(key, line_text, word_idx, scope, occ=0):
    """Drop the occ-th entry of the (line_key, word_idx, scope) stream — e.g.
    clearing a hold. Returns how many entries were removed (0 or 1)."""
    lk = norm_text(line_text)
    data = load(key)
    if data.get('_unreadable'):
        raise SystemExit(f'[overrides] refusing to write over unreadable '
                         f'{path(key)} — fix or remove it first')
    entries = data.get('entries') or []
    wi = None if word_idx is None else int(word_idx)
    matches = [i for i, e in enumerate(entries)
               if norm_text(e.get('line_key') or '') == lk
               and e.get('word_idx') == wi and (e.get('scope') or '') == scope]
    if occ >= len(matches):
        return 0
    del entries[matches[occ]]
    save(key, data)
    return 1


def migrate_entries(key, old_text, new_text, shift_from=None, shift=0,
                    drop_word_idx=None):
    """After a text op changes a line's text (and/or word indices), rewrite
    the EXISTING timing entries so they keep binding: 'line'/'word'/'hold'
    entries keyed by the old text get the new key; word-indexed entries at or
    past shift_from move by shift; entries pinned to a deleted word are left
    in place (they orphan loudly rather than silently retiming a neighbour).
    Textop entries are NOT touched — they replay against the text they were
    recorded on. Returns (migrated_count, warn|None); warns instead of
    migrating when the old text appears on several lines (text-keyed schema
    can't tell which occurrence the edit hit)."""
    old_k, new_k = norm_text(old_text), norm_text(new_text)
    if old_k == new_k and not shift:
        return 0, None
    data = load(key)
    if data.get('_unreadable'):
        return 0, f'sidecar {path(key).name} unreadable — entries not migrated'
    lp = BUILDS / f'{key}.lyrics.json'
    try:
        doc_lines = json.loads(lp.read_text()).get('lines') or []
        n_old = sum(1 for L in doc_lines
                    if norm_text(L.get('text') or '') in (old_k, new_k))
    except Exception:
        n_old = 1
    if n_old > 1 and old_k != new_k:
        return 0, (f'line text repeats ({n_old}×) — existing overrides for it '
                   f'were NOT re-keyed; re-check them by ear')
    moved = 0
    for e in data.get('entries') or []:
        if (e.get('scope') or '') == 'textop':
            continue
        if norm_text(e.get('line_key') or '') != old_k:
            continue
        wi = e.get('word_idx')
        if wi is not None and drop_word_idx is not None and int(wi) == drop_word_idx:
            continue                      # its word is gone — let it orphan loudly
        if wi is not None and shift and shift_from is not None \
                and int(wi) >= shift_from:
            e['word_idx'] = int(wi) + shift
        if old_k != new_k:
            e['line_key'] = new_k
        moved += 1
    if moved:
        save(key, data)
    return moved, None


def token_char_range(text, words, wi):
    """The [start, end) char range of token wi in line text — the SAME walk
    validate_song E10 replicates (skip untokenized whitespace, then each
    token's text must sit at the cursor). Returns None when the walk desyncs
    (never guess — a wrong splice corrupts the lyric)."""
    cursor = 0
    for i, w in enumerate(words):
        wt = (w.get('text') or '')
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if text[cursor:cursor + len(wt)] != wt:
            return None
        if i == wi:
            return cursor, cursor + len(wt)
        cursor += len(wt)
    return None


_LATIN = re.compile(r'[A-Za-z0-9]')


def apply_text_op(line, op):
    """Apply one recorded word-list edit to a lyrics line (text + words stay
    walk-coherent — validate E10). Mutates the line; returns note strings.
    Raises ValueError when the op can't bind (token text moved/gone)."""
    notes = []
    words = line.get('words') or []
    text = line.get('text') or ''
    kind = op.get('kind')
    wi = int(op.get('word_idx', -1))
    expect = op.get('word_text') or ''
    if not 0 <= wi < len(words) or (words[wi].get('text') or '') != expect:
        hits = [i for i, w in enumerate(words) if (w.get('text') or '') == expect]
        if len(hits) == 1:
            wi = hits[0]
            notes.append(f'token {expect!r} found at index {wi} '
                         f'(was recorded at {op.get("word_idx")})')
        else:
            raise ValueError(f'token {expect!r} not found once '
                             f'({len(hits)} matches) — text op skipped')
    rng = token_char_range(text, words, wi)
    if rng is None and kind in ('del', 'edit'):
        raise ValueError('token/text walk desynced — text op skipped')

    if kind == 'del':
        s, e = rng
        t = text[:s] + text[e:]
        # tidy whitespace the removal stranded — a space only earns its keep
        # on a Latin join (JP↔JP never needs one)
        i0 = s
        while i0 > 0 and t[i0 - 1].isspace():
            i0 -= 1
        i1 = s
        while i1 < len(t) and t[i1].isspace():
            i1 += 1
        if i1 > i0:
            left = t[i0 - 1] if i0 > 0 else ''
            right = t[i1] if i1 < len(t) else ''
            keep = ' ' if (left and right and
                           (_LATIN.search(left) or _LATIN.search(right))) else ''
            t = t[:i0] + keep + t[i1:]
        line['text'] = t
        w = words.pop(wi)
        if wi > 0:
            words[wi - 1]['end_ms'] = max(int(words[wi - 1]['end_ms']),
                                          int(w.get('end_ms') or 0))
        elif words:
            words[0]['begin_ms'] = min(int(words[0]['begin_ms']),
                                       int(w.get('begin_ms') or 0))
        notes.append(f'deleted token {expect!r} (window folded into a neighbour)')
        return notes

    if kind == 'edit':
        s, e = rng
        new = op.get('new_text') or ''
        if not new:
            raise ValueError('edit op with empty new_text')
        line['text'] = text[:s] + new + text[e:]
        words[wi]['text'] = new
        if op.get('reading'):
            words[wi]['kana'] = op['reading']
            notes.append(f'reading pinned to {op["reading"]!r}')
        elif 'reading' in op and op.get('reading') is None \
                and 'kana' in words[wi]:
            del words[wi]['kana']
        notes.append(f'token {expect!r} → {new!r}')
        return notes

    # add — carve the new word's window out of the anchor token's
    new = op.get('new_text') or ''
    if not new:
        raise ValueError('add op with empty new_text')
    where = op.get('where') or 'after'
    anchor = words[wi]
    ab, ae = int(anchor['begin_ms']), int(anchor['end_ms'])
    dur = max(ae - ab, 2)
    pos = token_char_range(text, words, wi)
    if pos is None:
        raise ValueError('token/text walk desynced — text op skipped')
    if where == 'before':
        cut = ab + int(dur * 0.4)
        nw = {'text': new, 'begin_ms': ab, 'end_ms': cut}
        anchor['begin_ms'] = cut
        ins, cpos = wi, pos[0]
    else:
        cut = ab + int(dur * 0.6)
        nw = {'text': new, 'begin_ms': cut, 'end_ms': ae}
        anchor['end_ms'] = cut
        ins, cpos = wi + 1, pos[1]
    if op.get('reading'):
        nw['kana'] = op['reading']
    # splice into the display text. A space on a side whose join touches
    # Latin keeps "hey" readable amid JP; the E10 walk skips whitespace, so
    # text-only spaces are safe.
    latin_new = bool(_LATIN.search(new))
    lead = ' ' if cpos > 0 and (latin_new or _LATIN.search(text[cpos - 1])) else ''
    tail = ' ' if cpos < len(text) and (latin_new or _LATIN.search(text[cpos])) \
        else ''
    line['text'] = text[:cpos] + lead + new + tail + text[cpos:]
    words.insert(ins, nw)
    notes.append(f'added token {new!r} {where} {expect!r} '
                 f'({nw["begin_ms"]}–{nw["end_ms"]}ms — drag to fit)')
    return notes


def clamp_hold(word):
    """Keep a word's hold_ms strictly inside its window (drop it when the
    window is too small to hold one)."""
    if word.get('hold_ms') is None:
        return
    b, e = int(word['begin_ms']), int(word['end_ms'])
    if e - b < 3:
        del word['hold_ms']
        return
    word['hold_ms'] = min(max(int(word['hold_ms']), b + 1), e - 1)


def resolve(lines, entries):
    """Match entries to line indices by normalized text. The n-th entry of a
    (line_key, word_idx, scope) stream binds to the n-th line whose
    normalized text == line_key. Returns ([(line_idx, entry)] in entry
    order, [(entry, reason)] orphans)."""
    by_norm = {}
    for i, L in enumerate(lines):
        by_norm.setdefault(norm_text(L.get('text') or ''), []).append(i)
    seen = {}
    resolved, orphans = [], []
    for e in entries:
        lk = norm_text(e.get('line_key') or '')
        cands = by_norm.get(lk) if lk else None
        stream = (lk, e.get('word_idx'), e.get('scope') or '')
        n = seen.get(stream, 0)
        if not lk or not cands:
            orphans.append((e, 'no line with this text'))
            continue
        if n >= len(cands):
            orphans.append((e, f'only {len(cands)} line(s) with this text '
                               f'(entry #{n} of its stream)'))
            continue
        seen[stream] = n + 1
        resolved.append((cands[n], e))
    return resolved, orphans


def apply_word_override(line, wi, begin_ms, end_ms=None):
    """Set one word's begin (and optionally end) ABSOLUTELY, clamped into the
    line window and monotonic against its neighbours' begins (clamp + note,
    never refuse). The previous word's end follows the new begin so the
    continuous-span contract (each end == next begin) survives. Mutates the
    line; returns clamp-note strings."""
    notes = []
    words = line.get('words') or []
    w = words[wi]
    lb, le = int(line['begin_ms']), int(line['end_ms'])
    lo = max(int(words[wi - 1]['begin_ms']) if wi > 0 else lb, lb)
    hi = min(int(words[wi + 1]['begin_ms']) if wi + 1 < len(words) else le, le)
    if hi < lo:
        hi = lo
    nb = min(max(int(begin_ms), lo), hi)
    if nb != int(begin_ms):
        notes.append(f'begin {begin_ms} clamped to {nb} '
                     f'(window/neighbour bounds {lo}..{hi})')
    w['begin_ms'] = nb
    if wi > 0:
        pw = words[wi - 1]
        pw['end_ms'] = max(int(pw['begin_ms']), nb)   # keep spans continuous
    if end_ms is not None:
        ne_hi = int(words[wi + 1]['begin_ms']) if wi + 1 < len(words) else le
        ne = min(max(int(end_ms), nb), max(ne_hi, nb))
        if ne != int(end_ms):
            notes.append(f'end {end_ms} clamped to {ne}')
        w['end_ms'] = ne
    else:
        w['end_ms'] = max(int(w['end_ms']), nb)
    clamp_hold(w)                                 # a hold rides its word
    return notes


def retime_line_words(line, new_b, new_e, pinned=None):
    """Move a line to [new_b, new_e] and carry its EXISTING word times along
    without rewriting their acoustic structure: a pure delta-shift when the
    duration is unchanged, a begin-anchored proportional squeeze/stretch when
    it changed. (Replaces timing_edit's old redistribute_words, which
    mora-splatted every word on a line nudge — destroying real CTC onsets.)
    pinned = {word_idx: (begin_ms, end_ms|None)} sidecar word overrides that
    keep their ABSOLUTE times, clamped into the new window. Mutates the line
    (begin/end + words); returns note strings."""
    notes = []
    old_b, old_e = int(line['begin_ms']), int(line['end_ms'])
    new_b, new_e = int(new_b), int(new_e)
    line['begin_ms'], line['end_ms'] = new_b, new_e
    words = line.get('words') or []
    if not words:
        return notes
    old_dur, new_dur = old_e - old_b, new_e - new_b
    scale = (new_dur / old_dur) if (old_dur > 0 and new_dur > 0) else 1.0
    if abs(scale - 1.0) > 1e-9:
        notes.append(f'duration {old_dur}→{new_dur}ms: word times scaled '
                     f'×{scale:.3f} anchored at begin (relative structure kept)')
    for w in words:
        wb = int(w.get('begin_ms', old_b))
        we = int(w.get('end_ms', wb))
        w['begin_ms'] = int(round(new_b + (wb - old_b) * scale))
        w['end_ms'] = int(round(new_b + (we - old_b) * scale))
        if w.get('hold_ms') is not None:             # the held point rides along
            w['hold_ms'] = int(round(new_b + (int(w['hold_ms']) - old_b) * scale))
    prev = new_b                                     # clamp + monotonic sweep
    for w in words:
        b = min(max(int(w['begin_ms']), prev), new_e)
        e = min(max(int(w['end_ms']), b), new_e)
        w['begin_ms'], w['end_ms'] = b, e
        clamp_hold(w)
        prev = b
    for wi in sorted(pinned or {}):
        if 0 <= wi < len(words):
            b, e = (pinned or {})[wi]
            kept = apply_word_override(line, wi, b, e)
            notes.append(f'word {wi} {words[wi].get("text","")!r} kept its '
                         f'absolute override ({words[wi]["begin_ms"]}ms)')
            notes.extend(f'word {wi}: {n}' for n in kept)
    return notes


def apply(lyr, key, quiet=False):
    """Re-apply every sidecar entry for <key> onto lyr (a lyrics.json dict).
    Line-scope entries re-impose the human line window (existing word times
    delta-shifted/scaled along, sidecar-pinned words kept absolute); word-
    scope entries re-impose absolute word times (clamped, minimal
    re-monotonic). Orphans (text gone / word_idx out of range) are reported
    LOUDLY, returned, and never removed from the file.
    Returns (lyr, applied_count, [(entry, reason)] orphans)."""
    data = load(key)
    entries = data.get('entries') or []
    if not entries:
        return lyr, 0, []
    lines = lyr.get('lines') or []
    applied = 0
    notes = []
    orphans = []

    # -- text ops FIRST, in file order, each resolving against the text as it
    #    stands after the previous op (a chain of edits replays in sequence).
    #    They rebuild the word list a fresh alignment/refetch clobbered, so
    #    the timing entries below land on the human-edited token stream.
    textops = [e for e in entries if (e.get('scope') or '') == 'textop']
    if textops:
        for ti, e in enumerate(textops):
            lk = norm_text(e.get('line_key') or '')
            ak = norm_text(e.get('line_key_after') or '')
            occ = int(e.get('occ') or 0)
            # Bind the recorded occurrence over the COMBINED stream of lines
            # matching the PRE-edit or POST-edit key, in line order. In a
            # pristine doc (fresh refetch) the target still bears the pre-key
            # → apply; in an already-edited doc it bears the post-key →
            # already applied, skip. Repeated hook lines keep their untouched
            # siblings safe: they bear the pre-key at OTHER occurrence slots.
            cands = [(i, norm_text(L.get('text') or '') == lk)
                     for i, L in enumerate(lines)
                     if norm_text(L.get('text') or '') == lk
                     or (ak and norm_text(L.get('text') or '') == ak)]
            if occ >= len(cands):
                # a LATER text op continued from this one's result (chained
                # edits: only the final text survives on the line) → done.
                if ak and any(norm_text(e2.get('line_key') or '') == ak
                              for e2 in textops[ti + 1:]):
                    continue
                orphans.append((e, 'no line with this (pre-edit) text'))
                continue
            li, is_pre = cands[occ]
            if not is_pre:
                continue                         # bears the post-edit text — applied
            op = e.get('op') or {}
            try:
                for n2 in apply_text_op(lines[li], op):
                    notes.append(f'line {li}: {n2}')
                applied += 1
            except ValueError as ex:
                orphans.append((e, str(ex)))

    timing_entries = [e for e in entries if (e.get('scope') or '') != 'textop']
    resolved, more_orphans = resolve(lines, timing_entries)
    orphans.extend(more_orphans)
    per_line = {}
    for li, e in resolved:
        per_line.setdefault(li, []).append(e)
    line_scope_touched = False
    for li in sorted(per_line):
        L = lines[li]
        line_es = [e for e in per_line[li] if e.get('word_idx') is None]
        word_es = [e for e in per_line[li] if e.get('word_idx') is not None
                   and (e.get('scope') or '') == 'word']
        hold_es = [e for e in per_line[li] if (e.get('scope') or '') == 'hold']
        pins = {int(e['word_idx']):
                (int(e['begin_ms']),
                 None if e.get('end_ms') is None else int(e['end_ms']))
                for e in word_es
                if 0 <= int(e['word_idx']) < len(L.get('words') or [])}
        for e in line_es:                        # line window first, so word
            nb = int(e['begin_ms'])              # pins land inside it
            ne = int(e['end_ms']) if e.get('end_ms') is not None else int(L['end_ms'])
            if ne <= nb:
                ne = nb + 200
                notes.append(f'line {li}: override end <= begin, forced end {ne}')
            notes.extend(f'line {li}: {n}'
                         for n in retime_line_words(L, nb, ne, pinned=pins))
            applied += 1
            line_scope_touched = True
        for e in sorted(word_es, key=lambda e: int(e['word_idx'])):
            wi = int(e['word_idx'])
            words = L.get('words') or []
            if not 0 <= wi < len(words):
                orphans.append((e, f'word_idx {wi} out of range '
                                   f'({len(words)} words on the line)'))
                continue
            ne = None if e.get('end_ms') is None else int(e['end_ms'])
            notes.extend(f'line {li} word {wi}: {n}'
                         for n in apply_word_override(L, wi, int(e['begin_ms']), ne))
            applied += 1
        for e in hold_es:                       # held-vowel points, after the
            wi = int(e['word_idx'])             # words have their final windows
            words = L.get('words') or []
            if not 0 <= wi < len(words):
                orphans.append((e, f'hold word_idx {wi} out of range '
                                   f'({len(words)} words on the line)'))
                continue
            words[wi]['hold_ms'] = int(e['begin_ms'])
            clamp_hold(words[wi])
            applied += 1
    if line_scope_touched:                       # minimal re-monotonic on lines
        prev = None
        for L in lines:
            if not (L.get('text') or '').strip():
                continue
            if prev is not None and L['begin_ms'] < prev:
                notes.append(f'monotonic: line begin {L["begin_ms"]} raised to '
                             f'{prev} ({(L.get("text") or "")[:16]!r})')
                L['begin_ms'] = prev
                if L['end_ms'] < L['begin_ms']:
                    L['end_ms'] = L['begin_ms']
            prev = L['begin_ms']
    if not quiet:
        for n in notes:
            print(f'[overrides] note: {n}')
        if orphans:
            print(f'[overrides] ⚠⚠ {len(orphans)} ORPHANED override(s) for '
                  f'{key!r} — NOT applied, kept in {path(key).name}:')
            for e, why in orphans:
                print(f'[overrides]   ⚠ scope={e.get("scope")} '
                      f'line_key={e.get("line_key","")[:28]!r} '
                      f'word_idx={e.get("word_idx")} '
                      f'begin={e.get("begin_ms")} — {why}')
    return lyr, applied, orphans


def word_pins_for_line(key, lines, idx):
    """{word_idx: (begin_ms, end_ms|None)} of sidecar word-scope entries that
    resolve to line <idx> — the words a line retime must NOT move."""
    resolved, _ = resolve(lines, [e for e in (load(key).get('entries') or [])
                                  if (e.get('scope') or '') in ('line', 'word')])
    out = {}
    for li, e in resolved:
        if li == idx and e.get('word_idx') is not None \
                and (e.get('scope') or '') == 'word':
            out[int(e['word_idx'])] = (int(e['begin_ms']),
                                       None if e.get('end_ms') is None
                                       else int(e['end_ms']))
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description='inspect a timing-overrides sidecar (read-only)')
    ap.add_argument('key')
    a = ap.parse_args()
    data = load(a.key)
    entries = data.get('entries') or []
    print(f'{path(a.key)}: {len(entries)} entr{"y" if len(entries)==1 else "ies"}')
    lp = BUILDS / f'{a.key}.lyrics.json'
    resolved, orphans = ([], [(e, 'no lyrics.json') for e in entries])
    if lp.exists():
        lyr = json.loads(lp.read_text())
        resolved, orphans = resolve(lyr.get('lines') or [], entries)
    res_by_id = {id(e): li for li, e in resolved}
    for e in entries:
        li = res_by_id.get(id(e))
        where = f'line {li}' if li is not None else 'ORPHAN'
        print(f'  [{where:>8}] {e.get("scope"):4} word_idx={e.get("word_idx")} '
              f'begin={e.get("begin_ms")} end={e.get("end_ms")} '
              f'{e.get("line_key","")[:28]!r} note={e.get("note","")!r}')
    if orphans:
        print(f'  ⚠ {len(orphans)} orphaned')


if __name__ == '__main__':
    main()
