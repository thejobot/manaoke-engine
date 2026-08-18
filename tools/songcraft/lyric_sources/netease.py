"""NetEase Cloud Music client — unofficial, lyrics-only.

NetEase has the richest free word-level lyric database for CJK music.
The API isn't documented or officially supported; we use the public GET
endpoints that return plain JSON. These have been stable for years but
can change without notice — if something breaks, check what AMLL /
Binaryify's NeteaseCloudMusicApi do.

Endpoints used:
  GET /api/search/get/web?s=<query>&type=1&limit=<n>
  GET /api/song/lyric?id=<id>&lv=-1&kv=-1&tv=-1&yv=-1&rv=-1

No auth, no cookies, just a browser User-Agent. We only pull lyric data —
no personal info is sent.
"""

import json
import random
import re
import urllib.parse
import urllib.request


class Throttled(Exception):
    """NetEase said "操作频繁" (code 405) — too many requests, come back later.

    This is NOT "the song isn't here", and the difference matters: the fetch
    treated a throttle as a miss, fell through to another source, and quietly
    took a lyric sheet for a LIVE recording of the same song (mariigoorudo,
    2026-07-29). A busy source has to say busy."""


# Outside China NetEase returns an AES-encrypted `result` blob with
# `abroad: true`. Declaring a Chinese origin address defeats that without
# touching auth. Standard workaround used across the ecosystem.
#
# It used to be ONE hardcoded address, 118.88.88.88 — the value every copy of
# this trick on the internet uses. NetEase counts requests per declared
# address, so the quota is shared with every other tool that copied it, and
# this Mac inherited a throttle it had not earned: on 2026-07-30 that address
# answered 操作频繁 for hours while a different one answered on the first try,
# with the song right there. A quota you share with strangers is not a quota.
# These are well-known public resolvers, and one 405 rotates to the next.
#
# Not every address defeats the abroad check — 119.29.29.29 answers 200 with
# `abroad: true` and an encrypted STRING where `result` should be an object,
# which the callers then call .get() on and crash. So a rotation address has
# to be verified, not assumed, and _request treats an abroad answer as a
# no-answer and moves on (see _is_abroad).
CN_ADDRS = ("223.5.5.5", "180.76.76.76", "114.114.114.114",
            "39.156.66.10", "118.88.88.88")

NETEASE_BASE = "https://music.163.com/api"


def _get(url, addr, timeout):
    req = urllib.request.Request(url, headers={
        # NetEase blocks empty / non-browser UAs. Desktop Safari works.
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
        ),
        "Referer": "https://music.163.com/",
        "Accept": "application/json, text/plain, */*",
        "X-Real-IP": addr,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_abroad(data):
    """True when NetEase served the outside-China response: `abroad: true`, or
    a `result` that is an AES blob (a string) instead of the object every
    caller reads. Both mean this address did not work — not that the song is
    missing."""
    if not isinstance(data, dict):
        return True
    if data.get("abroad"):
        return True
    res = data.get("result")
    return res is not None and not isinstance(res, (dict, list))


def _request(path, params=None, timeout=10):
    url = NETEASE_BASE + path
    if params:
        params = {k: v for k, v in params.items() if v not in (None, "")}
        if params:
            url += "?" + urllib.parse.urlencode(params)
    # Start somewhere different each run so a busy address is not always the
    # one tried first, and walk the ring from there.
    start = random.randrange(len(CN_ADDRS))
    data = None
    for i in range(len(CN_ADDRS)):
        data = _get(url, CN_ADDRS[(start + i) % len(CN_ADDRS)], timeout)
        # The HTTP status is 200 either way; the refusal is in the body.
        if isinstance(data, dict) and data.get("code") == 405:
            continue
        if _is_abroad(data):
            continue          # answered, but with a blob nothing can read
        return data
    raise Throttled(str((data or {}).get("msg") or (data or {}).get("message")
                        or "too many requests"))


def search(query, limit=10, timeout=10, strict=False):
    """Search the catalog. Returns a list of compact song dicts.
    strict=True re-raises timeouts/network failures instead of returning []
    so probes can report 'unknown' rather than a false 'no'."""
    try:
        data = _request("/search/get/web", {
            "s": query, "type": 1, "limit": int(limit), "offset": 0,
        }, timeout=timeout)
    except Throttled:
        raise            # always — "busy" must never be returned as "no hits"
    except Exception:
        if strict:
            raise
        return []
    songs = ((data or {}).get("result") or {}).get("songs") or []
    results = []
    for s in songs:
        artists = s.get("artists") or []
        album = s.get("album") or {}
        results.append({
            "id": s.get("id"),
            "name": s.get("name", ""),
            "artist": ", ".join(a.get("name", "") for a in artists),
            "album": album.get("name", ""),
            "duration_ms": s.get("duration") or 0,  # NetEase returns ms already
            "artwork_url": (album.get("picUrl") or "").replace("http://", "https://"),
        })
    return results


def fetch_lyrics(song_id, timeout=10):
    """Grab every lyric variant NetEase has for a song.

    Returns a dict with string fields (possibly empty):
        { lrc, tlyric, romalrc, yrc, ytlrc, yromalrc }

    `yrc` is the word-level format. `tlyric` is a line-level translation.
    `romalrc` is romanised (kana-less Japanese, pinyin for Mandarin, etc.).
    `timeout` lets budget-capped probes stay inside their window instead of
    inheriting the 10s default.
    """
    data = _request("/song/lyric", {
        "id": int(song_id),
        "lv": -1, "kv": -1, "tv": -1, "yv": -1, "rv": -1,
    }, timeout=timeout)
    def _text(key):
        blk = data.get(key) or {}
        return blk.get("lyric") or ""
    return {
        "lrc":      _text("lrc"),
        "tlyric":   _text("tlyric"),
        "romalrc":  _text("romalrc"),
        "yrc":      _text("yrc"),
        "ytlrc":    _text("ytlrc"),
        "yromalrc": _text("yromalrc"),
    }


# ── YRC parser ────────────────────────────────────────────────────────────

# Line header: `[LINE_START_MS,LINE_DURATION_MS]`
_YRC_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
# Word token: `(WORD_START_MS,WORD_DURATION_MS,FLAG)TEXT`
_YRC_WORD_RE = re.compile(r"\((\d+),(\d+),\d+\)([^(]*)")


def parse_yrc(yrc_text):
    """Parse NetEase YRC into our internal line shape with word timing.

    Returns [] if the text isn't YRC (e.g. plain LRC fallback).
    """
    if not yrc_text:
        return []
    lines = []
    for raw in yrc_text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = _YRC_LINE_RE.match(raw)
        if not m:
            continue
        line_start = int(m.group(1))
        line_dur = int(m.group(2))
        rest = m.group(3)

        # Some songs ship a metadata JSON blob in the first line (author,
        # upload time, etc.). Skip those.
        if rest.startswith("{"):
            continue

        words = []
        for wm in _YRC_WORD_RE.finditer(rest):
            start_ms = int(wm.group(1))
            dur_ms = int(wm.group(2))
            text = wm.group(3)
            if not text:
                continue
            words.append({
                "text": text,
                "begin_ms": start_ms,
                "end_ms": start_ms + dur_ms,
            })

        if not words:
            continue

        lines.append({
            "begin_ms": line_start,
            "end_ms": line_start + line_dur,
            "text": "".join(w["text"] for w in words),
            "lang": "",
            "translation": "",
            "translation_lang": "",
            "words": words,
            "is_background": False,
        })
    return lines


# ── LRC parser (shared shape with lrclib) ─────────────────────────────────

_LRC_TS = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def parse_lrc(lrc_text):
    """Line-level LRC → our line shape. Same structure the TTML parser produces."""
    if not lrc_text:
        return []
    events = []
    for raw in lrc_text.splitlines():
        timestamps = list(_LRC_TS.finditer(raw))
        if not timestamps:
            continue
        text = _LRC_TS.sub("", raw).strip()
        for m in timestamps:
            ms = int(int(m.group(1)) * 60_000 + float(m.group(2)) * 1000)
            events.append((ms, text))
    events.sort(key=lambda e: e[0])

    lines = []
    for i, (start_ms, text) in enumerate(events):
        if not text:
            continue
        end_ms = events[i + 1][0] if i + 1 < len(events) else start_ms + 4000
        lines.append({
            "begin_ms": start_ms, "end_ms": end_ms, "text": text,
            "lang": "", "translation": "", "translation_lang": "",
            "words": [], "is_background": False,
        })
    return lines


def _lrc_by_start(lrc_text):
    """Build {begin_ms: text} from an LRC string, for translation/romaji merging."""
    out = {}
    for line in parse_lrc(lrc_text):
        out[line["begin_ms"]] = line["text"]
    return out


def _merge_side_text(lines, lrc_text, field):
    """Merge an auxiliary LRC (tlyric / romalrc) into primary lines by nearest
    start-time match. NetEase YRC and the side LRCs usually share timestamps
    exactly, but allow ±2s slack for edge cases."""
    if not lrc_text or not lines:
        return
    aux = sorted(_lrc_by_start(lrc_text).items())
    if not aux:
        return
    for line in lines:
        # Binary-ish scan: find closest
        target = line["begin_ms"]
        best_text = ""
        best_delta = 2000
        for ms, text in aux:
            d = abs(ms - target)
            if d < best_delta:
                best_delta = d
                best_text = text
            elif ms > target + best_delta:
                break
        if best_text:
            line[field] = best_text


# ── convenience ───────────────────────────────────────────────────────────

def _normalise(s):
    return (s or "").strip().lower()


def _score_match(result, track_name, artist_name, target_ms):
    """Lower = better. Prefer exact artist match, then duration match, then
    title match. Penalises covers/remixes with the same duration as originals."""
    name = _normalise(result.get("name"))
    artist = _normalise(result.get("artist"))
    want_title = _normalise(track_name)
    want_artist = _normalise(artist_name)

    score = 0.0
    # Artist match is the biggest signal — covers get heavy penalty.
    if want_artist and want_artist in artist:
        pass
    elif want_artist and any(a in artist for a in want_artist.split()):
        score += 30
    else:
        score += 100  # unrelated artist

    # Title proximity.
    if want_title and want_title == name:
        pass
    elif want_title and (want_title in name or name in want_title):
        score += 5
    else:
        score += 15

    # Duration — convert ms diff to scaled points.
    if target_ms:
        score += abs((result.get("duration_ms") or 0) - target_ms) / 1000.0
    return score


def lookup_as_lines(track_name, artist_name, album_name="", duration_sec=None):
    """Search NetEase + return (record, lines). Prefers YRC word-level lyrics,
    falls back to LRC. Merges translation (tlyric) and romanisation (romalrc)
    into the line objects when present."""
    q = f"{track_name} {artist_name}".strip()
    results = search(q)
    if not results:
        return None, []

    target_ms = int((duration_sec or 0) * 1000)
    results.sort(key=lambda r: _score_match(r, track_name, artist_name, target_ms))
    best = results[0]

    # Quality gate: if the best match has neither an artist match nor a
    # strong title match, treat it as "no match". Prevents surfacing random
    # same-duration covers. 50 = artist mismatch + exact title; 115+ = both
    # wrong. Threshold leaves room for romanised-vs-native-script mismatches
    # where duration is the tiebreaker.
    score = _score_match(best, track_name, artist_name, target_ms)
    if score > 90:
        return None, []

    try:
        lyrics = fetch_lyrics(best["id"])
    except Exception:
        return best, []

    lines = parse_yrc(lyrics.get("yrc", ""))
    used_yrc = bool(lines)
    if not lines:
        lines = parse_lrc(lyrics.get("lrc", ""))

    _merge_side_text(lines, lyrics.get("tlyric", ""), "translation")
    # Romaji (romanisation) has its own slot in our shape.
    _merge_side_text(lines, lyrics.get("romalrc", ""), "romaji")

    # Stash raw variants on the record so callers can inspect / export.
    best["_raw_lyrics"] = lyrics
    best["_used_yrc"] = used_yrc
    return best, lines


def summarize(record, lyrics):
    """Compact summary for the Sources panel. `lyrics` is the raw dict from
    fetch_lyrics (or None)."""
    if not record:
        return {"has": False}
    if lyrics is None:
        lyrics = {}
    return {
        "has": True,
        "id": record.get("id"),
        "name": record.get("name"),
        "artist": record.get("artist"),
        "album": record.get("album"),
        "duration_sec": (record.get("duration_ms") or 0) / 1000,
        "has_yrc":         bool(lyrics.get("yrc")),
        "has_lrc":         bool(lyrics.get("lrc")),
        "has_translation": bool(lyrics.get("tlyric")),
        "has_romaji":      bool(lyrics.get("romalrc")),
    }
