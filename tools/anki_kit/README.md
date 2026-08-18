# Manaoke Study Kit toolkit

Turns a promoted Manaoke song into two study artifacts in one command:

- `Manaoke_<slug>_vocab.apkg` — Anki deck, every word, embedded audio (JP + EN gloss)
- `Manaoke_<slug>_flashcards.pdf` — double-sided printable cards for screen-free practice

## Run

```
pip install genanki        # plus: ffmpeg + Google Chrome on the machine
python build_song_kit.py --song inochi --out ~/Downloads/Manaoke-Study-Kit
```

## Add a new song

Edit the `SONGS` table at the top of `build_song_kit.py`:

```python
"silhouette": {
    "song_dir":  f"{MANAOKE}/songs/silhouette-vNN",      # promoted version dir
    "assets":    f"{MANAOKE}/songs/_assets/silhouette/audio",
    "base_slug": "silhouette",        # version-independent → stable Anki deck
    "live_slug": "silhouette-vNN",    # the slug that is actually live (for QR URLs)
},
```

Then `python build_song_kit.py --song silhouette --out ...`.

Precondition: the song's per-word audio clips must already exist under
`_assets/<song>/audio/{jp,en}` (the same clips the site's study cards play). The
builder stops and names any word missing its JP clip.

## Files

- `build_song_kit.py` — the whole pipeline (read → resolve audio → transcode → Anki → PDF)
- `anki_card.css` — Anki note-type styling (light + dark mode)
- `print_card.css` — flashcard print layout (8/sheet, long-edge duplex mirror)

Full rationale, print settings, and the duplex mirror explanation are in
**How to Make Manaoke Study Kits.pdf**.

## Print the flashcards correctly

Double-sided, **Flip on long edge**, **100% / Actual size**. Cut on the grey grid.
One-sheet test first; hold it to the light to confirm the backs line up.
