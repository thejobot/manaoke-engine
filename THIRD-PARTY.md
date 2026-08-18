# Third-party material

Vendored here, each under its own terms. This file records what they are; the
authoritative notices live next to the files.

| What | Where | Terms |
|---|---|---|
| M PLUS Rounded 1c, DotGothic16 (fonts) | `tools/songcraft/data/fonts_src/` | SIL Open Font License — see `NOTICE.txt` there |
| JMdict / EDICT (Japanese dictionary) | `tools/songcraft/data/jmdict_*` | Electronic Dictionary Research and Development Group, CC BY-SA — see `NOTICE-jmdict.txt` |
| Open JTalk dictionary | `tools/songcraft/pitch_pipeline/data/openjtalk_dict.tar.xz` | Modified BSD — see `NOTICE-openjtalk.txt` |
| Kanjium pitch-accent data | `tools/songcraft/pitch_pipeline/data/kanjium_accents.txt` | From the Kanjium project; attribution required |

## Not vendored, and deliberately so

Lyrics, translations, album art, and song audio are not in this repository and never
will be. The engine reads them from a `data.json` you supply.

## Open question

`tools/human_audio/physics_fixtures/` holds eight short single-word audio clips used
as pass/fail fixtures for the clip-physics check. They are test data, but they are
recordings the project did not make. Decide whether to keep, replace, or drop them
before this repository is made public.
