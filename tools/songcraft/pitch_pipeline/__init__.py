"""Manaoke pitch accent pipeline.

Layered lookup:
  1. Kansai dialect override JSON (hand-curated; pyopenjtalk doesn't cover Kansai)
  2. kanjium accents.txt (CC-BY-SA, 124k NHK-derived entries) — citation-form lookup
  3. pyopenjtalk-plus run_frontend (conjugation-aware via OpenJTalk chain rules)
  4. cross-check with run_marine=True (DNN predictor; flags disagreements)

Output: PitchEntry with mora list, accent number, accent type, H/L pattern,
source attribution, and confidence (high / medium / low / kansai).

Fully local/offline: the open_jtalk dictionary is vendored (see _dict_bootstrap),
so pyopenjtalk never downloads it. The bootstrap must run BEFORE core imports
pyopenjtalk (which reads OPEN_JTALK_DICT_DIR at import time).
"""
from ._dict_bootstrap import ensure_dict as _ensure_dict
_ensure_dict()
from .core import get_pitch, PitchEntry  # noqa: E402  (must follow _ensure_dict)
