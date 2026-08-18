# CD jewel-case assets (pipeline rule)

The song page can show the album as a tap-to-flip **CD in a photographic jewel
case**: a small CD pinned top-right that grows to center, flips front↔back, and
shrinks back on tap-outside. Front = the cover in the case; back = the live HTML
tracklist laid over an empty cream case.

## Make the assets for a song (one command)

```bash
python3 tools/cd_case/make_cd_case.py path/to/cover.jpg songs/<song-dir>/
# -> songs/<song-dir>/cd-front.jpg  (cover in a glossy case)
# -> songs/<song-dir>/cd-back.jpg   (empty cream case for the tracklist overlay)
```

Re-running on an already-live song? Bump `--name` (`--name cd2`) so the new image
gets a fresh URL — phones cache by filename and will otherwise serve the stale
old image (this bit us once: reused `front.jpg`, the owner kept seeing the pre-crop
version while fresh browsers saw the new one).

## The rule (why it looks right, baked into the script — don't re-derive by eye)

- Case photo comes from **csmith/jewelcase** (a fixed 884×777 frame, art window at
  offset (98,13) size 750). Straight out of the tool it has a pale plastic lip
  around the cover ("white ridges") and the rounded outer corners poke white into
  a rectangle.
- So we crop to the case **content** and pull in a few px more on every side to
  kill the rounded-corner white, keeping the black **spine** on the left:
  **`CROP = (9, 23, 840, 753)` → 831×730**. One box works for every song (fixed
  frame geometry).
- **Front** is glossy (reflection + colour-correct + edges on). **Back** is flat
  (reflection/colour off) so the overlaid tracklist text stays neutral & legible.

## Song-page CSS contract (must match the assets)

- `.mini` and `.flip`: **`aspect-ratio:831/730`**
- the case faces: **`border-radius:0`** (real jewel cases have square corners) —
  rounding stays only on the small controls (platform logos, flip button)
- back text window `.bk-win`: `left:11.5%` (clears the spine), `right/top/bottom:2.5%`

## Back insert palette (PIPELINE RULE — make it blend into the song)

The back's tracklist insert is tinted to **that song's living-gradient world** so
the flipped case doesn't stand out — it reads as part of the same colour world as
the page background. On `.cdj-ov` set four vars to the song page's own field
palette (copy them verbatim from the song's `--field-c1/c2/c3/hi`):

```css
.cdj-ov{
  --cdj-c1:172,114,132; --cdj-c2:106,60,80; --cdj-c3:58,32,46; --cdj-hi:182,126,144;
  --cdj-acc:color-mix(in srgb, rgb(var(--cdj-hi)) 62%, #fff); /* light accent, auto-derived */
}
```

The insert gradient + the accent (artist label, track numbers, NOW) build from
those — body text is white-alpha so it stays legible on any song's palette.
Adding a song = swap these 4 numbers, nothing else.

## Interaction (baked into the controller)

- Tap the mini (or the topbar art) → it **pops up and auto-flips to the back**.
- Tap the popped-up case (anywhere but a streaming link) → it **flips to the front
  first, then shrinks** back into the slot. Tap-outside / Esc do the same.

Reference implementation: `songs/cdcase-a-fe1c7/index.html` (the approved test
page) — the front/back markup, the grow/flip/shrink JS, and the readable
tracklist overlay. Clone it when wiring the CD onto a real song page.
