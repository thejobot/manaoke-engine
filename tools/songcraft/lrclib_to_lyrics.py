#!/usr/bin/env python3
"""
lrclib_to_lyrics.py — second lyric source for the pipeline (no Apple, no auth).

LRCLIB (lrclib.net) is an open synced-lyrics database that LyriCool already
speaks. This fetches a song's synced (LRC) lyrics and writes the same
builds/<key>.lyrics.json shape the Apple path produces, so the rest of the
builder is source-agnostic. LRC is LINE-level timing; content_to_data
distributes kana per mora within each line (no word timing needed).

Usage:
  python3 lrclib_to_lyrics.py <key> --artist "Vaundy" --track "踊り子" \
      --album replica --art <400x400 url> --title-jp 踊り子 --artist-jp Vaundy
"""
import argparse, json, re, urllib.parse, urllib.request
from pathlib import Path

BUILDS = Path(__file__).resolve().parent / 'builds'
LRC_RE = re.compile(r'^\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$')


def fetch(artist, track, album):
    q = urllib.parse.urlencode({'artist_name': artist, 'track_name': track, 'album_name': album or ''})
    url = f'https://lrclib.net/api/get?{q}'
    try:
        return json.loads(urllib.request.urlopen(url, timeout=25).read())
    except Exception:
        # fall back to search
        q2 = urllib.parse.urlencode({'q': f'{artist} {track}'})
        res = json.loads(urllib.request.urlopen(f'https://lrclib.net/api/search?{q2}', timeout=25).read())
        for r in res:
            if r.get('syncedLyrics'):
                return r
        raise SystemExit('no synced lyrics found on LRCLIB')


def parse_lrc(synced):
    rows = []
    for ln in synced.split('\n'):
        m = LRC_RE.match(ln.strip())
        if not m:
            continue
        mm, ss, text = int(m.group(1)), float(m.group(2)), m.group(3).strip()
        rows.append((int(mm * 60000 + ss * 1000), text))
    rows.sort(key=lambda r: r[0])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('key')
    ap.add_argument('--artist', required=True); ap.add_argument('--track', required=True)
    ap.add_argument('--album', default=''); ap.add_argument('--art', default='')
    ap.add_argument('--title-jp', default=''); ap.add_argument('--artist-jp', default='')
    a = ap.parse_args()

    d = fetch(a.artist, a.track, a.album)
    dur_ms = int((d.get('duration') or 0) * 1000)
    rows = parse_lrc(d.get('syncedLyrics') or '')
    if not rows:
        raise SystemExit('LRCLIB returned no synced lyrics')
    lines = []
    for i, (begin, text) in enumerate(rows):
        if not text:
            continue
        end = rows[i + 1][0] if i + 1 < len(rows) else (dur_ms or begin + 3000)
        lines.append({'begin_ms': begin, 'end_ms': end, 'text': text, 'lang': '',
                      'translation': '', 'translation_lang': '', 'words': [], 'is_background': False})
    out = {
        'song': {'id': str(d.get('id', '')), 'name': a.title_jp or a.track,
                 'artist': a.artist_jp or a.artist, 'album': a.album,
                 'duration_ms': dur_ms or lines[-1]['end_ms'], 'artwork_url': a.art},
        'lines': lines, 'line_count': len(lines), 'has_translations': False,
        'has_word_timing': False, 'languages': [], 'source': 'lrclib',
    }
    (BUILDS / f'{a.key}.lyrics.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f'wrote builds/{a.key}.lyrics.json: {len(lines)} lines from LRCLIB (id {d.get("id")}), '
          f'{dur_ms/1000:.0f}s, source=lrclib')


if __name__ == '__main__':
    main()
