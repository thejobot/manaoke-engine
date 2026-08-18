"""Apple Music API client for fetching lyrics."""

import re
import urllib.request
import urllib.error
import urllib.parse
import json


AMP_API_BASE = "https://amp-api.music.apple.com"


def parse_song_url(url_or_id):
    """Extract song ID from an Apple Music URL or raw ID.

    Handles URLs like:
        https://music.apple.com/us/album/song-name/1234567890?i=1234567891
        https://music.apple.com/us/song/song-name/1234567891
        1234567891
    """
    if url_or_id.isdigit():
        return url_or_id, None

    # Album URL with ?i= track param
    match = re.search(r'music\.apple\.com/(\w+)/album/.+\?i=(\d+)', url_or_id)
    if match:
        return match.group(2), match.group(1)

    # Direct song URL
    match = re.search(r'music\.apple\.com/(\w+)/song/.+/(\d+)', url_or_id)
    if match:
        return match.group(2), match.group(1)

    # Album URL without ?i= (whole album, not a specific track)
    match = re.search(r'music\.apple\.com/(\w+)/album/.+/(\d+)$', url_or_id)
    if match:
        return match.group(2), match.group(1)

    raise ValueError(f"Could not parse song ID from: {url_or_id}")


def _make_request(url, config):
    """Make authenticated request to Apple Music API."""
    auth = config["authorization"]
    if not auth.startswith("Bearer "):
        auth = f"Bearer {auth}"

    headers = {
        "Authorization": auth,
        "Media-User-Token": config["media_user_token"],
        "Origin": "https://music.apple.com",
        "Referer": "https://music.apple.com/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ValueError("Authentication failed — tokens may be expired. Grab fresh ones from music.apple.com.")
        if e.code == 403:
            raise ValueError("Access denied — your Media-User-Token may be invalid or expired.")
        if e.code == 404:
            raise ValueError(f"Song not found at: {url}")
        body = e.read().decode() if e.fp else ""
        raise ValueError(f"API error {e.code}: {body}")


def fetch_song_info(song_id, config):
    """Fetch basic song metadata."""
    storefront = config.get("storefront", "us")
    url = f"{AMP_API_BASE}/v1/catalog/{storefront}/songs/{song_id}"
    data = _make_request(url, config)
    if data.get("data"):
        attrs = data["data"][0].get("attributes", {})
        artwork_url = ""
        artwork = attrs.get("artwork", {})
        if artwork.get("url"):
            artwork_url = artwork["url"].replace("{w}", "600").replace("{h}", "600")
        return {
            "id": song_id,
            "name": attrs.get("name", "Unknown"),
            "artist": attrs.get("artistName", "Unknown"),
            "album": attrs.get("albumName", "Unknown"),
            "duration_ms": attrs.get("durationInMillis", 0),
            "artwork_url": artwork_url,
        }
    return {"id": song_id, "name": "Unknown", "artist": "Unknown", "album": "Unknown", "duration_ms": 0, "artwork_url": ""}


def fetch_lyrics(song_id, config):
    """Fetch line-level timed lyrics (TTML)."""
    storefront = config.get("storefront", "us")
    lang = config.get("language", "en-US")
    url = (
        f"{AMP_API_BASE}/v1/catalog/{storefront}/songs/{song_id}/lyrics"
        f"?l={lang}"
    )
    data = _make_request(url, config)
    if data.get("data"):
        return data["data"][0].get("attributes", {}).get("ttml", "")
    return ""


def search_catalog(query, config, limit=10):
    """Search Apple Music's catalog for songs matching `query`.

    Returns a list of compact song dicts (same shape as fetch_song_info).
    """
    storefront = config.get("storefront", "us")
    q = urllib.parse.quote(query)
    url = (
        f"{AMP_API_BASE}/v1/catalog/{storefront}/search"
        f"?term={q}&types=songs&limit={int(limit)}"
    )
    try:
        data = _make_request(url, config)
    except ValueError:
        return []
    results = []
    songs = data.get("results", {}).get("songs", {}).get("data", []) or []
    for s in songs:
        attrs = s.get("attributes", {}) or {}
        artwork_url = ""
        art = attrs.get("artwork", {}) or {}
        if art.get("url"):
            artwork_url = art["url"].replace("{w}", "300").replace("{h}", "300")
        results.append({
            "id": s.get("id") or attrs.get("playParams", {}).get("id", ""),
            "name": attrs.get("name", ""),
            "artist": attrs.get("artistName", ""),
            "album": attrs.get("albumName", ""),
            "duration_ms": attrs.get("durationInMillis", 0),
            "artwork_url": artwork_url,
            "url": attrs.get("url", ""),
            "has_lyrics": bool(attrs.get("hasLyrics")),
            "has_syllable_lyrics": bool(attrs.get("hasTimeSyncedLyrics")),
        })
    return results


def fetch_syllable_lyrics(song_id, config):
    """Fetch syllable/word-level timed lyrics (TTML)."""
    storefront = config.get("storefront", "us")
    lang = config.get("language", "en-US")
    url = (
        f"{AMP_API_BASE}/v1/catalog/{storefront}/songs/{song_id}/syllable-lyrics"
        f"?l={lang}"
    )
    try:
        data = _make_request(url, config)
        if data.get("data"):
            return data["data"][0].get("attributes", {}).get("ttml", "")
    except ValueError:
        # Syllable lyrics not available for all songs
        pass
    return ""
