"""Vendored open_jtalk dictionary bootstrap — keeps the pitch pipeline fully
LOCAL / OFFLINE.

pyopenjtalk-plus does NOT ship its ~102MB dictionary inside the wheel; on first
use it DOWNLOADS it from the network. To match how the JMdict segmentation gate
is self-contained, we instead bundle the dictionary (xz-compressed, ~15MB, at
data/openjtalk_dict.tar.xz) and point OPEN_JTALK_DICT_DIR at a locally-extracted
copy — so pyopenjtalk resolves its dict from the repo and never reaches out.

`ensure_dict()` MUST run BEFORE `import pyopenjtalk` (pyopenjtalk reads
OPEN_JTALK_DICT_DIR at import time). __init__.py calls it before importing core.
"""
import os
import tarfile
from pathlib import Path

_DATA = Path(__file__).resolve().parent / 'data'
_TARBALL = _DATA / 'openjtalk_dict.tar.xz'
_CACHE = _DATA / 'openjtalk_dict'           # extracted here (gitignored)
_DICT = _CACHE / 'dictionary'
_SENTINEL = _DICT / 'sys.dic'               # the big file — proof of a full extract


def ensure_dict():
    """Point OPEN_JTALK_DICT_DIR at the vendored dictionary, extracting it once.
    Respects an OPEN_JTALK_DICT_DIR the caller already set. No-ops (falls back to
    pyopenjtalk's own resolution) if the vendored tarball isn't present."""
    if os.environ.get('OPEN_JTALK_DICT_DIR'):
        return
    if not _SENTINEL.exists():
        if not _TARBALL.exists():
            return
        _CACHE.mkdir(parents=True, exist_ok=True)
        with tarfile.open(_TARBALL, 'r:xz') as t:
            try:
                t.extractall(_CACHE, filter='data')   # py3.12+ safe-extract
            except TypeError:
                t.extractall(_CACHE)                   # older Python
    if _SENTINEL.exists():
        os.environ['OPEN_JTALK_DICT_DIR'] = str(_DICT)
