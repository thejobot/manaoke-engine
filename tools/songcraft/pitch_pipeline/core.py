"""Main pitch lookup API."""
import json, os
from dataclasses import dataclass, asdict
from .morae import kata_to_hira, hira_to_kata, split_morae
from .pyojt_backend import get_pyojt
from . import kanjium_db

@dataclass
class PitchEntry:
    word: str             # the JP form we looked up
    morae: list[str]      # mora list (kana, including ー / small ya/yu/yo)
    accent: int           # 0 = heiban, N>0 = downstep after mora N
    accent_type: str      # 'heiban' | 'atamadaka' | 'nakadaka' | 'odaka'
    pattern: str          # H/L per mora, e.g. 'LHHL'
    particle_pitch: str   # 'H' or 'L' — what a following particle would be
    confidence: str       # 'high' | 'medium' | 'low' | 'kansai' | 'unknown'
    sources: dict         # {'pyopenjtalk': N, 'pyopenjtalk_marine': N, 'kanjium': [N,...], 'kansai_override': N}
    notes: list[str]      # human-readable caveats (disagreement flags, dialect, etc.)

# ── helpers ──────────────────────────────────────────────────────────────

def _classify(accent: int, mora_count: int) -> str:
    if accent == 0: return 'heiban'
    if accent == 1: return 'atamadaka'
    if accent >= mora_count: return 'odaka'
    return 'nakadaka'

def _hl_pattern(accent: int, mora_count: int) -> tuple[str, str]:
    """→ (within_word_pattern, particle_pitch).

    NHK rules:
      heiban (0): L H H ... H, particle stays H
      atamadaka (1): H L L ... L, particle L
      nakadaka (1<N<count): L H H...H L L...L (drop after mora N), particle L
      odaka (N==count): L H H ... H, particle L (drop lands on particle)
    """
    n = mora_count
    if n == 0: return '', 'H'
    if accent == 0:
        return 'L' + 'H' * (n - 1), 'H'
    if accent == 1:
        return 'H' + 'L' * (n - 1), 'L'
    if accent >= n:
        # odaka: full word high, drop on particle
        return 'L' + 'H' * (n - 1), 'L'
    return 'L' + 'H' * (accent - 1) + 'L' * (n - accent), 'L'

# ── public API ───────────────────────────────────────────────────────────

_KANSAI = None
def _kansai():
    global _KANSAI
    if _KANSAI is None:
        path = os.path.join(os.path.dirname(__file__), 'kansai_overrides.json')
        _KANSAI = json.load(open(path))
    return _KANSAI

def get_pitch(word: str, reading_kana: str | None = None) -> PitchEntry:
    """Return PitchEntry for word, consulting all sources."""
    sources = {}
    notes = []

    # 1. Kansai override?
    kansai = _kansai().get(word)
    if kansai:
        accent = kansai['accent']
        morae = kansai['morae']
        n = len(morae)
        pattern, particle = _hl_pattern(accent, n)
        return PitchEntry(
            word=word, morae=morae, accent=accent,
            accent_type=_classify(accent, n), pattern=pattern,
            particle_pitch=particle, confidence='kansai',
            sources={'kansai_override': accent},
            notes=[f"Kansai dialect: {kansai.get('note','manual override')}"],
        )

    # 2. pyopenjtalk (canonical for conjugations)
    try:
        a_pyojt, morae = get_pyojt(word, run_marine=False)
        sources['pyopenjtalk'] = a_pyojt
    except Exception as e:
        notes.append(f"pyopenjtalk error: {e}")
        a_pyojt, morae = None, []

    # 3. pyopenjtalk-marine cross-check (skip on failures — marine is shakier)
    a_marine = None
    try:
        a_marine, _ = get_pyojt(word, run_marine=True)
        sources['pyopenjtalk_marine'] = a_marine
    except Exception:
        pass

    # 4. kanjium citation-form lookup
    kana_reading = ''.join(morae) if morae else (reading_kana or word)
    kj_hits = kanjium_db.lookup(word, kana_reading)
    if kj_hits:
        sources['kanjium'] = kj_hits

    # ── pick + confidence ────────────────────────────────────────
    candidates = []
    if a_pyojt is not None: candidates.append(a_pyojt)
    if kj_hits and kj_hits[0] == a_pyojt: confidence_boost = True
    else: confidence_boost = False

    final_accent = a_pyojt if a_pyojt is not None else (kj_hits[0] if kj_hits else 0)
    if not morae and kj_hits:
        # kanjium-only path — derive mora from reading
        morae = split_morae(hira_to_kata(kana_reading))
    n = len(morae) if morae else 0

    # Confidence — 'high' when multiple sources agree, 'medium' single-source
    # or surfaced-disagreement (we trust pyopenjtalk on disagreement since its
    # chain rules know conjugations kanjium doesn't store, but we flag).
    src_vals = []
    if a_pyojt is not None: src_vals.append(a_pyojt)
    if a_marine is not None: src_vals.append(a_marine)
    if kj_hits: src_vals.append(kj_hits[0])
    if len(src_vals) >= 2 and len(set(src_vals)) == 1:
        confidence = 'high'
    elif len(src_vals) >= 2:
        confidence = 'medium'
        notes.append(f"sources disagree: pyopenjtalk={a_pyojt} marine={a_marine} kanjium={kj_hits}")
    elif src_vals:
        confidence = 'medium'
    else:
        confidence = 'unknown'

    pattern, particle = _hl_pattern(final_accent, n)
    return PitchEntry(
        word=word, morae=morae, accent=final_accent,
        accent_type=_classify(final_accent, n) if n else 'unknown',
        pattern=pattern, particle_pitch=particle,
        confidence=confidence, sources=sources, notes=notes,
    )

def to_dict(entry: PitchEntry) -> dict:
    return asdict(entry)
