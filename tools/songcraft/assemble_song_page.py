#!/usr/bin/env python3
"""Assemble the Silhouette song page from the current template build.

This IS the clone-template-and-swap pipeline step (the one build_song.py was
supposed to be): copy the newest template dir, inject data.json, splice the
LINE_TR / LINE_EXPLAIN literals, retarget every song-specific constant, write
the manifest, and add the per-song _redirects lines. Deterministic + rerunnable.
"""
import hashlib, json, re, secrets, shutil, string, sys
from pathlib import Path

REPO = Path(str(Path(__file__).resolve().parents[2]))
BASE = REPO / '.local-preview/REFINE-2026-06-11/silhouette'
TEMPLATE = REPO / 'songs/inochi-mijikashi-dcmzsn'

slug_file = BASE / 'page-slug.txt'
if slug_file.exists():
    slug = slug_file.read_text().strip()
else:
    slug = 'silhouette-' + ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    slug_file.write_text(slug)
DEST = REPO / 'songs' / slug
print('build dir:', DEST)

if DEST.exists():
    shutil.rmtree(DEST)
shutil.copytree(TEMPLATE, DEST)

# ── data.json ────────────────────────────────────────────────────────────
draft = json.load(open(BASE / 'data.draft.json'))
maps = json.load(open(BASE / 'line_maps.draft.json'))
apple = json.load(open(BASE / 'apple_lyrics.json'))
data = {
    'song_number': 2,
    'title_jp': 'シルエット',
    'title_en': 'Silhouette',
    'artist': 'KANA-BOON',
    'artist_en': 'KANA-BOON',
    'slug': 'silhouette',
    'youtube_id': 'dlFA0Zq1k2A',
    'level': 'Intermediate',
    'r2_folder': 'Song 2 シルエット',
    'podcast_file': '',
    'direction': 'ja',
    'sections': draft['sections'],
    'grammar': draft['grammar'],
    'trivia': draft['trivia'],
    'podcast_script': [],
    'apple_lyrics': apple if 'lines' in apple else apple.get('apple_lyrics', apple),
}
json.dump(data, open(DEST / 'data.json', 'w'), ensure_ascii=False, indent=2)

# ── tts_manifest.json (sections + line explainers; byte-identical keys) ──
manifest = []
for s in data['sections']:
    if s.get('speak_en'):
        manifest.append(['en-US', s['speak_en'], s['speak_en'], f"audio/en/section_{s['id']}_intro.mp3"])
for text in dict.fromkeys(maps['LINE_EXPLAIN'].values()):
    h = hashlib.sha1(text.encode()).hexdigest()[:8]
    manifest.append(['en-US', text, text, f'audio/en/line_{h}_explain.mp3'])
json.dump(manifest, open(DEST / 'tts_manifest.json', 'w'), ensure_ascii=False, indent=1)

# ── index.html surgery ───────────────────────────────────────────────────
html = (DEST / 'index.html').read_text()

def js_literal(d, value_fn):
    rows = []
    for k, v in d.items():
        key = json.dumps(re.sub(r'\s+', '', re.sub(r'\s*\(×\d+\)\s*$', '', k)), ensure_ascii=False)
        rows.append(f'  {key}: {value_fn(v)},')
    return '{\n' + '\n'.join(rows) + '\n}'

def tr_value(v):
    parts = [f"en:{json.dumps(v.get('en',''), ensure_ascii=False)}"]
    if v.get('full'): parts.append(f"full:{json.dumps(v['full'], ensure_ascii=False)}")
    if v.get('lead'): parts.append('lead:true')
    return '{' + ', '.join(parts) + '}'

def splice_literal(html, marker, new_literal):
    start = html.index(marker)
    open_brace = html.index('{', start)
    end = html.index('\n};', open_brace)   # literals end with newline + };
    return html[:open_brace] + new_literal + html[end+1:end+1] + html[end+1:], True

for marker, lit in [('const LINE_TR = ', js_literal(maps['LINE_TR'], tr_value)),
                    ('const LINE_EXPLAIN = ', js_literal(maps['LINE_EXPLAIN'], lambda v: json.dumps(v, ensure_ascii=False)))]:
    start = html.index(marker)
    open_brace = html.index('{', start)
    close = html.index('\n};', open_brace)
    html = html[:open_brace] + lit + html[close + len('\n};') - 1:]
    # note: lit already ends with '}', keep the trailing ';'

REPL = [
    # Visible topbar identity (body) + meta descriptions (head) — these were
    # MISSED on the first Silhouette build and shipped with the wrong song's
    # title in the header. Keep them first so a stale template can't survive.
    ('<div class="u-title">イノチミジカシコイセヨオトメ</div>',
     '<div class="u-title">シルエット</div>'),
    ('<div class="u-artist">クリープハイプ<span class="u-artist-en"> · CreepHyp</span></div>',
     '<div class="u-artist">KANA-BOON</div>'),
    ('<div class="u-pod-ep">イノチミジカシコイセヨオトメ - Deep Dive</div>',
     '<div class="u-pod-ep">シルエット - Deep Dive (coming soon)</div>'),
    ('Learn Japanese through イノチミジカシコイセヨオトメ by クリープハイプ. Sing along, study word-by-word, and drill the lyrics.',
     'Learn Japanese through シルエット by KANA-BOON. Sing along, study word-by-word, and drill the lyrics.'),
    ('Learn Japanese through イノチミジカシコイセヨオトメ by クリープハイプ. Sing along, study, and drill the lyrics.',
     'Learn Japanese through シルエット by KANA-BOON. Sing along, study, and drill the lyrics.'),
    ('inochi-mijikashi-dcmzsn', slug),
    ("const YT_ID = '7cCL0owFBqk';", "const YT_ID = 'dlFA0Zq1k2A';"),
    ("const PROGRESS_SONG = 'inochi-mijikashi';", "const PROGRESS_SONG = 'silhouette';"),
    ('<title>イノチミジカシコイセヨオトメ by クリープハイプ – Manaoke</title>',
     '<title>シルエット by KANA-BOON – Manaoke</title>'),
    ('content="イノチミジカシコイセヨオトメ by クリープハイプ – Manaoke"',
     'content="シルエット by KANA-BOON – Manaoke"'),
]
for old, new in REPL:
    if old not in html:
        print('WARN replacement source missing:', old[:60])
    html = html.replace(old, new)

# Podcast: no episode yet — point at nothing; the player ignores empty src.
html = re.sub(r"const PODCAST_URL = '[^']*';", "const PODCAST_URL = '';", html)
# Version chip = the random slug suffix (no v prefix), per CLAUDE.md.
chip = slug.split('-')[-1]
html = re.sub(r'<div class="u-version" aria-hidden="true">[^<]*</div>',
              f'<div class="u-version" aria-hidden="true">{chip}</div>', html)
(DEST / 'index.html').write_text(html)

# ── _redirects: per-song asset rules ABOVE the generic inochi fallthrough ──
rd = (REPO / '_redirects').read_text()
line_a = f'/songs/{slug}/audio/*       /songs/_assets/silhouette/audio/:splat       200'
line_p = f'/songs/{slug}/pitch_data/*  /songs/_assets/silhouette/pitch_data/:splat  200'
line_i = f'/songs/{slug}/images/*      /songs/_assets/silhouette/images/:splat      200'
if line_a not in rd:
    anchor = '/songs/:dir/audio/*'
    idx = rd.index(anchor)
    rd = rd[:idx] + line_a + '\n' + line_p + '\n' + line_i + '\n' + rd[idx:]
    (REPO / '_redirects').write_text(rd)

print('assembled:', slug)
print('manifest entries:', len(manifest))
