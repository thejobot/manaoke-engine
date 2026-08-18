#!/usr/bin/env python3
"""gen_pitch.py — bake pitch-accent data for a song into
songs/_assets/<folder>/pitch_data/pitch.json (the file the page fetches and keys
by `jp_speak || jp`). Turnkey pipeline step; the pitch engine (pyopenjtalk-plus +
kanjium + marine cross-check) is vendored at tools/songcraft/pitch_pipeline/.

INCREMENTAL like gen_audio: only words missing from the existing pitch.json are
computed, and the heavy engine is imported ONLY when there is work — so a rebuild
that didn't change the word set stays fast (loads nothing, exits in ms).

Each study word is keyed by BOTH its display surface (jp) and its spoken kana
(jp_speak), because the page looks up `jp_speak || jp` and older data keyed both.
Pitch is COMPUTED from the surface when it is Japanese (so kanjium's kanji+reading
lookup fires), but from the KANA when the surface is latin — pyopenjtalk otherwise
spells an English loanword out letter by letter (headlong → "H-E-A-D-L-O-N-G").

Run in the qwentts env (pyopenjtalk-plus lives there):
  /opt/homebrew/Caskroom/miniforge/base/envs/qwentts/bin/python \
      tools/songcraft/gen_pitch.py <slug> <folder>
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
JP_RE = re.compile(r'[぀-ゟ゠-ヿ一-鿿々]')


def is_japanese(s):
    return bool(JP_RE.search(s or ''))


def collect(data):
    """Return {(surface, kana): [keys...]} — the unique lookups and every
    pitch.json key that should point at each one."""
    pairs = {}
    def add(surface, kana):
        surface = (surface or '').strip()
        kana = (kana or surface).strip()
        if not (surface or kana):
            return
        keyset = pairs.setdefault((surface, kana), set())
        if surface: keyset.add(surface)
        if kana: keyset.add(kana)
    for sec in data.get('sections', []):
        for w in sec.get('words', []):
            add(w.get('jp', ''), w.get('jp_speak') or w.get('jp', ''))
    # short particles that ride in lyric lines but aren't card vocab
    for line in (data.get('apple_lyrics') or {}).get('lines', []):
        for tok in line.get('words', []):
            t = (tok.get('text') or '').strip()
            if 1 <= len(t) <= 4:
                add(t, t)
    return pairs


def main():
    if len(sys.argv) < 3:
        sys.exit('usage: gen_pitch.py <slug> <folder>')
    slug, folder = sys.argv[1], sys.argv[2]
    data = json.loads((ROOT / 'songs' / slug / 'data.json').read_text())
    out_dir = ROOT / 'songs' / '_assets' / folder / 'pitch_data'
    out_dir.mkdir(parents=True, exist_ok=True)
    pj = out_dir / 'pitch.json'
    existing = json.loads(pj.read_text()) if pj.exists() else {}

    pairs = collect(data)
    todo = {(s, k): keys for (s, k), keys in pairs.items()
            if any(key not in existing for key in keys)}
    if not todo:
        print(f'pitch fresh: {len(existing)} entries, 0 new (word set unchanged)')
        return 0

    sys.path.insert(0, str(HERE))
    from pitch_pipeline import get_pitch          # noqa: E402  (heavy — only when needed)
    from pitch_pipeline.core import to_dict        # noqa: E402

    added = 0
    for (surface, kana), keys in todo.items():
        word = surface if is_japanese(surface) else kana   # latin → look up by kana
        try:
            entry = to_dict(get_pitch(word or kana, kana))
        except Exception as e:
            print(f'  ! {surface or kana}: {e}', file=sys.stderr)
            continue
        for key in keys:
            existing[key] = entry
        added += 1
    pj.write_text(json.dumps(existing, ensure_ascii=False, indent=1))
    print(f'pitch: {added} new lookup(s), {len(existing)} keys total '
          f'-> songs/_assets/{folder}/pitch_data/pitch.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
