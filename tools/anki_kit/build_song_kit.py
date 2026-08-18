#!/usr/bin/env python3
"""
Manaoke -> Anki + printable-flashcard KIT builder  (one song, one command).

Turns a promoted Manaoke song into two study artifacts:
  1. <slug>_vocab.apkg        - Anki deck, all words, embedded audio (JP + EN gloss)
  2. <slug>_flashcards.pdf    - double-sided printable cards (screen-free practice)

Reusable for ANY song: edit the SONGS table below (or pass --song <key>) and run.
Everything downstream is derived from the song's data.json + _assets audio, using
the canonical Manaoke clip-slug rule (tools/songcraft/gen_en_audio.py: rom_uid).

    python build_song_kit.py --song inochi --out ~/Downloads/Manaoke-Study-Kit

Deps:  pip install genanki   (plus ffmpeg + Google Chrome on PATH)

See HOW-IT-WORKS.pdf for the full rationale and the per-song checklist.
"""
import argparse, hashlib, html, json, os, re, shutil, subprocess, sys

# --------------------------------------------------------------------------- #
#  PER-SONG CONFIG.  Add a new entry to onboard a new song; nothing else changes.
# --------------------------------------------------------------------------- #
MANAOKE = str(Path(__file__).resolve().parents[2])
SONGS = {
    "shinunoga": {
        "song_dir":  f"{MANAOKE}/songs/shinunoga-b3qfut",
        "assets":    f"{MANAOKE}/songs/_assets/shinunoga/audio",
        "base_slug": "shinunoga",
        "live_slug": "shinunoga-b3qfut",
    },
    "odoriko": {
        "song_dir":  f"{MANAOKE}/songs/odoriko-q4f3rn",
        "assets":    f"{MANAOKE}/songs/_assets/odoriko/audio",
        "base_slug": "odoriko",
        "live_slug": "odoriko-q4f3rn",
    },
    "headlong": {
        "song_dir":  f"{MANAOKE}/songs/headlong-u0o2p4",
        "assets":    f"{MANAOKE}/songs/_assets/headlong/audio",
        "base_slug": "headlong",
        "live_slug": "headlong-u0o2p4",
    },
    "inochi": {
        "song_dir":  f"{MANAOKE}/songs/inochi-mijikashi-ry4rk0",   # wave rebuild dir
        "assets":    f"{MANAOKE}/songs/_assets/inochi-mijikashi/audio",
        "base_slug": "inochi-mijikashi",                          # version-independent
        "live_slug": "inochi-mijikashi-ry4rk0",                   # wave preview slug
    },
    "ema": {
        "song_dir":  f"{MANAOKE}/songs/ema-6rs1ij",
        "assets":    f"{MANAOKE}/songs/_assets/ema/audio",
        "base_slug": "ema",
        "live_slug": "ema-6rs1ij",
    },
    # key renamed silhouette -> silhouette2 (2026-07-07, backlog 624b6c45) to match
    # the builds/ key + asset folder; "silhouette" kept as an alias below.
    "silhouette2": {
        "song_dir":  f"{MANAOKE}/songs/silhouette2-o8mugf",
        "assets":    f"{MANAOKE}/songs/_assets/silhouette2/audio",  # NOT _assets/silhouette (legacy live page owns it)
        "base_slug": "silhouette2",   # asset FOLDER name — kit files are wired as _assets/silhouette2/kit/Manaoke_silhouette2_*
        "live_slug": "silhouette2-o8mugf",
    },
    "kaijuu-no-hana-uta": {
        "song_dir":  f"{MANAOKE}/songs/kaijuu-no-hana-uta-or9zd9",
        "assets":    f"{MANAOKE}/songs/_assets/kaijuu-no-hana-uta/audio",
        "base_slug": "kaijuu-no-hana-uta",
        "live_slug": "kaijuu-no-hana-uta-or9zd9",
    },
}

# The hardcoded song_dir/live_slug rot the moment a song rebuilds to a fresh
# slug (found 2026-07-12: every entry above pointed at a superseded dir, so a
# kit rebuild would have read PRE-kana-fix data). The build state is the
# truth: resolve each key's current slug from builds/<key>.build_state.json
# at import time; the literals above remain only as the no-state fallback.
_STATE_KEY = {"inochi": "inochi-mijikashi", "silhouette": "silhouette2"}
for _k, _e in SONGS.items():
    _sk = _STATE_KEY.get(_k, _k)
    _sp = f"{MANAOKE}/tools/songcraft/builds/{_sk}.build_state.json"
    try:
        with open(_sp) as _f:
            _slug = json.load(_f)["slug"]
        if os.path.exists(f"{MANAOKE}/songs/{_slug}/data.json"):
            _e["song_dir"] = f"{MANAOKE}/songs/{_slug}"
            _e["live_slug"] = _slug
    except Exception:
        pass    # no build state (legacy) — the hardcoded fallback stands

# Fixed forever: one shared Anki note type across every Manaoke song.
MODEL_ID = 1607392319
ACCENT   = "#c43a72"     # print accent (per-song could be overridden)
COLS, ROWS = 2, 4        # flashcard grid; 8 per US-Letter sheet
PER = COLS * ROWS
PRODUCTION_CARDS = True  # also emit EN->JP recall cards for content words
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# --------------------------------------------------------------------------- #
def rom_uid(rom):
    """Canonical Manaoke clip slug (matches tools/songcraft/gen_en_audio.py)."""
    return re.sub(r'^-+|-+$', '', str(rom or '').replace(' ', '-')
                  .replace('·', '').replace('/', '_'))

def stable_id(s):
    return int(hashlib.sha256(s.encode()).hexdigest(), 16) % (1 << 31)

def load_cards(cfg):
    """Read data.json -> flat list of word records with resolved audio + URLs."""
    from urllib.parse import quote
    d = json.load(open(os.path.join(cfg["song_dir"], "data.json")))
    jp_dir = os.path.join(cfg["assets"], "jp")
    en_dir = os.path.join(cfg["assets"], "en")
    live = f"https://manaoke.app/songs/{cfg['live_slug']}/audio"
    cards = []
    for s in d["sections"]:
        sid = s["id"]
        for w in s["words"]:
            # clip slug = _romUid(uid || rom) — a word carries an explicit `uid`
            # when the same rom collides twice in one section (e.g. two を's).
            uid = rom_uid(w.get("uid") or w["rom"]); base = f"word_{sid}_{uid}"
            # Prefer the .wav master (best transcode source for the .apkg); fall
            # back to the served mono-mp3 if a song ships mp3-only. v095+ serves
            # JP word clips as .mp3 but keeps the .wav master alongside.
            jp = os.path.join(jp_dir, base + ".wav")
            if not os.path.exists(jp):
                jp = os.path.join(jp_dir, base + ".mp3")
            gl = os.path.join(en_dir, base + "_gloss.mp3")
            if not os.path.exists(jp):
                raise SystemExit(f"missing JP audio: {jp}")
            cards.append({
                "order": len(cards) + 1, "section_id": sid,
                "section_name": s["name"], "section_short": s.get("short_name", s["name"]),
                "uid": uid, "basename": base, "jp": w["jp"], "rom": w["rom"],
                "en": w["en"], "gloss": w.get("gloss", w["en"]),
                "hint": w.get("hint", ""), "context": w.get("context", ""),
                "particle": bool(w.get("particle", False)),
                "audio_jp_wav": jp,
                "audio_gloss_mp3": gl if os.path.exists(gl) else None,
                "url_jp": f"{live}/jp/{quote(base + '.mp3')}",
            })
    meta = {k: d[k] for k in ("song_number", "title_jp", "title_en", "artist",
                              "artist_en", "youtube_id", "level")}
    meta.update(base_slug=cfg["base_slug"], live_slug=cfg["live_slug"],
                song_url=f"https://manaoke.app/songs/{cfg['live_slug']}/",
                n_cards=len(cards),
                n_particles=sum(1 for c in cards if c["particle"]))
    return meta, cards

# --------------------------------------------------------------------------- #
def stage_media(cards, media_dir):
    """Transcode JP wav->mp3 (10x smaller) and copy EN gloss mp3. Dedupe by basename."""
    os.makedirs(media_dir, exist_ok=True)
    jp = sorted({c["audio_jp_wav"] for c in cards})
    en = sorted({c["audio_gloss_mp3"] for c in cards if c["audio_gloss_mp3"]})
    for w in jp:
        out = os.path.join(media_dir, os.path.splitext(os.path.basename(w))[0] + ".mp3")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", w,
                        "-codec:a", "libmp3lame", "-q:a", "5", "-ar", "44100", out],
                       check=True)
    for m in en:
        shutil.copy2(m, os.path.join(media_dir, os.path.basename(m)))
    return media_dir

# --------------------------------------------------------------------------- #
def build_anki(meta, cards, media_dir, out_path):
    import genanki
    DECK_ID = stable_id("manaoke-deck::" + meta["base_slug"])
    CSS = open(os.path.join(os.path.dirname(__file__), "anki_card.css")).read()
    FIELDS = [{"name": n} for n in ("SortOrder", "Japanese", "Reading", "Meaning",
              "Hint", "Context", "Section", "Song", "Producible", "AudioJP",
              "AudioEN", "ParticleTag")]
    REC_F = ('<div class="sec">{{Section}}</div><div class="jp">{{Japanese}}</div>'
             '<div class="audio">{{AudioJP}}</div>')
    REC_B = ('<div class="sec">{{Section}}</div><div class="jp">{{Japanese}}</div>'
             '<div class="reading">{{Reading}}</div><div class="audio">{{AudioJP}}</div><hr>'
             '<div class="meaning">{{Meaning}}</div>'
             '{{#Hint}}<div class="hint">{{Hint}}</div>{{/Hint}}'
             '{{#Context}}<div class="context">&ldquo;{{Context}}&rdquo;</div>{{/Context}}'
             '<div class="audio">{{AudioEN}}</div>{{ParticleTag}}'
             '<div class="song">{{Song}}</div>')
    PRO_F = ('<div class="sec">{{Section}} &middot; produce</div>'
             '<div class="prompt-en">{{Meaning}}</div>'
             '{{#Hint}}<div class="hint">{{Hint}}</div>{{/Hint}}')
    PRO_B = ('<div class="sec">{{Section}} &middot; produce</div>'
             '<div class="prompt-en">{{Meaning}}</div><hr>'
             '<div class="jp-small">{{Japanese}}</div><div class="reading">{{Reading}}</div>'
             '<div class="audio">{{AudioJP}}</div>'
             '{{#Context}}<div class="context">&ldquo;{{Context}}&rdquo;</div>{{/Context}}'
             '<div class="song">{{Song}}</div>')
    templates = [{"name": "1 Recognize (JP to EN)", "qfmt": REC_F, "afmt": REC_B}]
    if PRODUCTION_CARDS:
        templates.append({"name": "2 Produce (EN to JP)",
                          "qfmt": "{{#Producible}}" + PRO_F + "{{/Producible}}",
                          "afmt": "{{#Producible}}" + PRO_B + "{{/Producible}}"})
    model = genanki.Model(MODEL_ID, "Manaoke - Japanese Song Vocab",
                          fields=FIELDS, templates=templates, css=CSS, sort_field_index=0)
    deck = genanki.Deck(DECK_ID, f"Manaoke::{meta['title_jp']} ({meta['artist_en']})")
    song_label = f"{meta['title_jp']} — {meta['artist_en']}"
    media, seen = [], set()
    def add(p):
        if p and p not in seen: seen.add(p); media.append(p)
    for c in cards:
        jp_mp3 = os.path.join(media_dir, c["basename"] + ".mp3")
        en_mp3 = os.path.join(media_dir, os.path.basename(c["audio_gloss_mp3"]))
        add(jp_mp3); add(en_mp3)
        ptag = '<div class="tag-particle">grammar particle</div>' if c["particle"] else ""
        deck.add_note(genanki.Note(
            model=model,
            guid=genanki.guid_for(meta["base_slug"], c["order"]),  # STABLE per word
            sort_field=f"{c['order']:03d}",
            tags=["Manaoke", meta["base_slug"], c["section_id"]],
            fields=[f"{c['order']:03d}", html.escape(c["jp"]), html.escape(c["rom"]),
                    html.escape(c["en"]), html.escape(c["hint"]), html.escape(c["context"]),
                    html.escape(c["section_name"]), html.escape(song_label),
                    "" if c["particle"] else "1",
                    f"[sound:{os.path.basename(jp_mp3)}]",
                    f"[sound:{os.path.basename(en_mp3)}]", ptag]))
    pkg = genanki.Package(deck); pkg.media_files = media
    pkg.write_to_file(out_path)
    n_prod = sum(1 for c in cards if not c["particle"]) if PRODUCTION_CARDS else 0
    return {"notes": len(cards), "cards": len(cards) + n_prod, "media": len(media)}

# --------------------------------------------------------------------------- #
def build_pdf(meta, cards, out_path):
    def jp_cls(s):
        n = len(s)
        return "jp jp-xl" if n <= 3 else "jp jp-lg" if n <= 5 else \
               "jp jp-md" if n <= 7 else "jp jp-sm"
    def front(c):
        if c is None: return '<div class="card blank"></div>'
        # front is a bare recall prompt: the Japanese word. No audio prompt by this
        # stage of study; you already hear the song in your head.
        return (f'<div class="card front"><div class="corner tl">{c["order"]:02d}</div>'
                f'<div class="corner tr sec">{html.escape(c["section_short"])}</div>'
                f'<div class="{jp_cls(c["jp"])}">{html.escape(c["jp"])}</div></div>')
    def back(c):
        if c is None: return '<div class="card blank"></div>'
        hint = f'<div class="hint">{html.escape(c["hint"])}</div>' if c["hint"] else ""
        ctx = f'<div class="ctx">&ldquo;{html.escape(c["context"])}&rdquo;</div>' if c["context"] else ""
        ptag = '<span class="ptag">particle</span>' if c["particle"] else ""
        return (f'<div class="card back"><div class="corner tl sec">'
                f'{html.escape(c["section_short"])}{ptag}</div>'
                f'<div class="corner tr">{c["order"]:02d}</div>'
                f'<div class="reading">{html.escape(c["rom"])}</div>'
                f'<div class="meaning">{html.escape(c["en"])}</div>{hint}{ctx}</div>')

    def rev_cols(g):
        out = [None] * PER
        for r in range(ROWS):
            for col in range(COLS):
                out[r*COLS + (COLS-1-col)] = g[r*COLS + col]
        return out

    pages = []
    for i in range(0, len(cards), PER):
        g = cards[i:i+PER] + [None] * (PER - len(cards[i:i+PER]))
        pages.append('<div class="sheet">' + "".join(front(c) for c in g) + '</div>')
        pages.append('<div class="sheet back-sheet">' + "".join(back(c) for c in rev_cols(g)) + '</div>')

    css = open(os.path.join(os.path.dirname(__file__), "print_card.css")).read() \
        .replace("__ACCENT__", ACCENT).replace("__COLS__", str(COLS)).replace("__ROWS__", str(ROWS))
    doc = (f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>'
           f'<body>{"".join(pages)}</body></html>')
    html_path = out_path[:-4] + ".html"
    with open(html_path, "w") as f:   # context-manager guarantees flush+close before Chrome reads it
        f.write(doc)
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={os.path.abspath(out_path)}",
                    "file://" + os.path.abspath(html_path)],  # absolute: file://./rel loads nothing
                   check=True, capture_output=True)
    os.remove(html_path)
    n_sheets = (len(cards) + PER - 1) // PER
    return {"cards": len(cards), "sheets": n_sheets, "pdf_pages": n_sheets * 2}

# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--song", default="inochi",
                    choices=list(SONGS) + ["silhouette", "inochi-mijikashi"])
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads/Manaoke-Study-Kit"))
    args = ap.parse_args()
    if args.song == "silhouette":      # legacy alias for the renamed key
        args.song = "silhouette2"
    if args.song == "inochi-mijikashi":  # builds/ key alias (manaoke_build run
        args.song = "inochi"             # <key> kits passes the builds key)
    cfg = SONGS[args.song]
    os.makedirs(args.out, exist_ok=True)
    work = os.path.join(args.out, ".work_" + args.song)
    os.makedirs(work, exist_ok=True)

    meta, cards = load_cards(cfg)
    print(f"[{args.song}] {meta['title_jp']} - {meta['artist_en']}: "
          f"{meta['n_cards']} words ({meta['n_particles']} particles)")
    stage_media(cards, os.path.join(work, "media"))
    apkg = os.path.join(args.out, f"Manaoke_{cfg['base_slug']}_vocab.apkg")
    pdf  = os.path.join(args.out, f"Manaoke_{cfg['base_slug']}_flashcards.pdf")
    a = build_anki(meta, cards, os.path.join(work, "media"), apkg)
    print(f"  anki: {a['notes']} notes / {a['cards']} cards / {a['media']} media -> {apkg}")
    p = build_pdf(meta, cards, pdf)
    print(f"  pdf:  {p['cards']} cards / {p['sheets']} sheets / {p['pdf_pages']} pages -> {pdf}")
    json.dump({"meta": meta, "cards": [{k: v for k, v in c.items() if not k.startswith('_')}
              for c in cards]}, open(os.path.join(work, "cards.json"), "w"),
              ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
