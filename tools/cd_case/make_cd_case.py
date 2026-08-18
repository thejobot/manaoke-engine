#!/usr/bin/env python3
"""
make_cd_case.py — turn an album cover into the Manaoke "CD jewel case" assets.

This is THE pipeline for the tap-to-flip CD on a song page. Run it once per song
on that song's cover; it emits the two images the song page references:

    <out>/cd-front.jpg   the cover composited into a photographic jewel case,
                         cropped tight to the case (no white plastic ridges,
                         sharp square corners). Used for the mini + the front face.
    <out>/cd-back.jpg    the EMPTY cream jewel case (no art), same crop. The song
                         page lays the live HTML tracklist over this (.bk-win).
                         This file is identical for every song, but we emit a copy
                         per song dir so the build stays lean/self-contained.

WHY a fixed crop (the rule):
  The case photo comes from csmith/jewelcase (github.com/csmith/jewelcase), whose
  frame is a FIXED 884x777 with the art window at offset (98,13) size 750x750.
  Straight out of the tool there's a pale plastic lip around the cover ("white
  ridges") and the rounded outer plastic corners poke white into a rectangular
  crop. So we crop to the case CONTENT and pull in a few px more on every side to
  kill the rounded-corner white, keeping the black spine on the left. Because the
  frame geometry is constant, ONE crop box works for every song:

      CROP = (9, 23, 840, 753)  ->  831 x 730  (aspect 831/730)

  The song page must use  aspect-ratio:831/730  on .mini and .flip, and
  border-radius:0 (real cases have square corners).

FRONT gets the glossy treatment (reflection + colour-correct + edges); BACK is
flat (reflection/colour OFF) so the overlaid tracklist text stays neutral/legible.

Usage:
    python3 tools/cd_case/make_cd_case.py COVER.jpg OUTDIR [--name PREFIX]

    COVER.jpg   any album cover (square-ish; it's scaled+centre-cropped to fit).
    OUTDIR      song build dir (e.g. songs/inochi-mijikashi-xxxxxx/).
    --name      filename prefix (default "cd" -> cd-front.jpg / cd-back.jpg).
                Bump it (cd2, cd3...) when you re-run on an EXISTING live song so
                the new image gets a fresh URL and phones don't serve the cached
                old one. (Same lesson as the per-build dir: new URL = guaranteed
                fresh; reusing a filename = stale cache on phones.)

Requires: Go (for `go run github.com/csmith/jewelcase/...`) and Pillow.
"""
import subprocess, sys, tempfile, argparse, os
from PIL import Image

# --- the rule: fixed crop box derived from the csmith 884x777 frame geometry ---
CROP = (9, 23, 840, 753)          # left, top, right, bottom  -> 831 x 730
ASPECT = "%d/%d" % (CROP[2]-CROP[0], CROP[3]-CROP[1])
JEWELCASE = "github.com/csmith/jewelcase/cmd/jewelcase@latest"


def jewelcase(src, dst, *, glossy):
    """Run csmith/jewelcase. glossy=True for the front cover, False for the empty back."""
    flags = ["-rotation=false", "-offset=false"]          # never randomise placement
    if not glossy:
        flags += ["-reflection=false", "-colour=false"]   # flat empty case for the back
    subprocess.run(["go", "run", JEWELCASE, *flags, src, dst], check=True)


def crop_case(path):
    Image.open(path).convert("RGB").crop(CROP).save(path, "JPEG", quality=92)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cover")
    ap.add_argument("outdir")
    ap.add_argument("--name", default="cd")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    front = os.path.join(a.outdir, f"{a.name}-front.jpg")
    back = os.path.join(a.outdir, f"{a.name}-back.jpg")

    with tempfile.TemporaryDirectory() as tmp:
        # FRONT: glossy case over the cover
        jewelcase(a.cover, front, glossy=True)
        crop_case(front)
        # BACK: empty cream case (shared look; flat for legible overlaid text)
        cream = os.path.join(tmp, "cream.png")
        Image.new("RGB", (750, 750), (246, 239, 222)).save(cream)
        jewelcase(cream, back, glossy=False)
        crop_case(back)

    print(f"wrote {front}")
    print(f"wrote {back}")
    print(f"-> song page: aspect-ratio:{ASPECT}; border-radius:0; "
          f'img src="{a.name}-front.jpg" / bk-frame "{a.name}-back.jpg"')


if __name__ == "__main__":
    main()
