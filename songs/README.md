# songs/

One directory per built song page, plus `_assets/<song>/` holding that song's
audio, images and pitch data.

Both are empty here on purpose: this repository ships the engine, not a library.
`manaoke_build.py init <key>` creates a build, and the assemble step writes the
page into `songs/<key>-<slug>/`.

The directory name prefix before the random slug **must** match the `_assets/`
folder name — the asset-routing Function derives one from the other.
