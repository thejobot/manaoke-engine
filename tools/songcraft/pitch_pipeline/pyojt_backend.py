"""pyopenjtalk-plus backend. Gives conjugation-aware accent via chain rules
and falls back to neural prediction via run_marine=True for cross-check.
"""
import pyopenjtalk
from .morae import split_morae

def _morpheme_morae(morpheme):
    """Extract mora list from a morpheme record, stripping OJT's accent
    diacritic marker (the smart-quote ’ that OJT inserts mid-pron)."""
    clean = morpheme['pron'].replace('’', '')
    return split_morae(clean)

def accent_from_chain(morphemes: list[dict]) -> tuple[int, list[str]]:
    """Compose accent + mora list from a chain of morphemes (run_frontend output).

    OpenJTalk encodes the COMBINED-form accent on the chain-head morpheme's
    `acc` field. e.g., 汚れる(汚れ, acc=4 when chained with た) — the 4
    accounts for the appended た, so the full word 汚れた has accent 4.
    """
    if not morphemes:
        return 0, []
    accent = morphemes[0]['acc']
    morae = []
    for m in morphemes:
        morae.extend(_morpheme_morae(m))
    return accent, morae

def get_pyojt(word: str, run_marine: bool = False) -> tuple[int, list[str]]:
    """→ (accent_num, mora_list). run_marine=True uses the DNN predictor."""
    if run_marine:
        # marine returns a separate API; pyopenjtalk integrates it via flag
        morphemes = pyopenjtalk.run_frontend(word, run_marine=True)
    else:
        morphemes = pyopenjtalk.run_frontend(word)
    return accent_from_chain(morphemes)
