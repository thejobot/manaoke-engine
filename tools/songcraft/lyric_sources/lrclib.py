"""LRCLIB client — free, public, no-auth lyrics database.

https://lrclib.net — broad coverage of Western music, offers plain lyrics and
line-level synced LRC. No word-level timing or translations.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request


LRCLIB_API = "https://lrclib.net/api"


def _request(path, params=None, timeout=10):
    url = LRCLIB_API + path
    if params:
        # Drop empty values so LRCLIB doesn't try to match against "".
        params = {k: v for k, v in params.items() if v not in (None, "", 0)}
        if params:
            url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "apple-lyrics/0.1 (https://github.com/local)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # A truthful miss — the record is not in the database. Distinct
            # from a timeout/network failure so probes can report "no" vs
            # "unknown" honestly (backlog b1a2b514).
            raise LookupError("LRCLIB: not found")
        raise RuntimeError(f"LRCLIB request failed: {e}")
    except Exception as e:
        raise RuntimeError(f"LRCLIB request failed: {e}")


def get_exact(track_name, artist_name, album_name="", duration_sec=None,
              timeout=10, strict=False):
    """Look up one entry by exact metadata. Returns the LRCLIB record or None
    (a genuine 404 miss). With strict=True, a timeout/network failure RAISES
    RuntimeError instead of masquerading as a miss — probe callers catch it
    and report 'unknown' rather than 'no' (backlog b1a2b514)."""
    params = {
        "track_name": track_name,
        "artist_name": artist_name,
        "album_name": album_name,
    }
    if duration_sec:
        params["duration"] = int(round(duration_sec))
    try:
        return _request("/get", params, timeout=timeout)
    except LookupError:
        return None
    except RuntimeError:
        if strict:
            raise
        return None


def search(track_name="", artist_name="", query="", timeout=10):
    """Search LRCLIB. Returns a list (possibly empty)."""
    params = {"track_name": track_name, "artist_name": artist_name, "q": query}
    try:
        result = _request("/search", params, timeout=timeout)
    except (RuntimeError, LookupError):
        return []
    return result if isinstance(result, list) else []


def parse_lrc(lrc_text):
    """Parse LRC synced lyrics into our internal line shape.

    Returns a list matching what `parser.parse_ttml` produces:
        [{begin_ms, end_ms, text, words: [], translation, ...}, ...]
    LRCLIB's synced lyrics are line-level only, so `words` is always empty.
    """
    if not lrc_text:
        return []

    # LRC line format: [mm:ss.xx] text  (multiple timestamps per line OK)
    ts_re = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")

    events = []  # (ms, text)
    for raw in lrc_text.splitlines():
        timestamps = list(ts_re.finditer(raw))
        if not timestamps:
            continue
        text = ts_re.sub("", raw).strip()
        for m in timestamps:
            mins = int(m.group(1))
            secs = float(m.group(2))
            ms = int(mins * 60_000 + secs * 1000)
            events.append((ms, text))
    events.sort(key=lambda e: e[0])

    lines = []
    for i, (start_ms, text) in enumerate(events):
        if not text:
            continue
        end_ms = events[i + 1][0] if i + 1 < len(events) else start_ms + 4000
        lines.append({
            "begin_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
            "lang": "",
            "translation": "",
            "translation_lang": "",
            "words": [],
            "is_background": False,
        })
    return lines


def lookup_as_lines(track_name, artist_name, album_name="", duration_sec=None):
    """Convenience: return (record, lines) where `lines` is our internal shape.

    `lines` will be an empty list if LRCLIB has only plain lyrics (no timing).
    Returns (None, []) if nothing found.
    """
    rec = get_exact(track_name, artist_name, album_name, duration_sec)
    if not rec:
        # Fall back to search; pick the closest-duration hit.
        hits = search(track_name=track_name, artist_name=artist_name)
        if not hits:
            return None, []
        if duration_sec:
            hits.sort(key=lambda h: abs((h.get("duration") or 0) - duration_sec))
        rec = hits[0]

    synced = rec.get("syncedLyrics") or ""
    lines = parse_lrc(synced) if synced else []
    return rec, lines


def summarize(record):
    """Return a compact description of what this LRCLIB entry offers."""
    if not record:
        return {"has": False}
    return {
        "has": True,
        "id": record.get("id"),
        "name": record.get("trackName") or record.get("name"),
        "artist": record.get("artistName"),
        "album": record.get("albumName"),
        "duration_sec": record.get("duration"),
        "instrumental": bool(record.get("instrumental")),
        "has_synced": bool(record.get("syncedLyrics")),
        "has_plain": bool(record.get("plainLyrics")),
    }
