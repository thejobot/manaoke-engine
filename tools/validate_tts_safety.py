#!/usr/bin/env python3
"""TTS language-safety validator for Manaoke song builds.

WHY THIS EXISTS
---------------
A song shipped with an English browser voice reading Japanese romaji ("pinsaro").
Root cause: short `en` glosses legitimately contain romaji, and any English speak
path that missed the audio cache fell through to the browser's English voice.
This checker makes that class of bug fail the build instead of shipping.

It cross-checks a song's tts_manifest.json (the 4-tuple list
[lang, key, spoken_text, filename]) against data.json and FAILS (nonzero exit)
when a clip's language and content disagree, or when something the page will try
to speak has no matching clip and would silently fall back to the browser voice.

USAGE
-----
    python3 tools/validate_tts_safety.py songs/<slug>/tts_manifest.json
    python3 tools/validate_tts_safety.py songs/<slug>/tts_manifest.json --data songs/<slug>/data.json

Exit code 0 = clean (warnings allowed), 1 = at least one FAIL, 2 = bad usage.
Run it before generate_tts.py and before any commit that touches a manifest.
"""

import argparse
import json
import os
import re
import sys

# Shared Japanese-token detector (vocabulary-anchored). Lives beside the audio
# engine so the gate flags EXACTLY what the splicer splices.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "songcraft"))
try:
    import jp_token_detect
except Exception:
    jp_token_detect = None

# Hiragana, katakana, half-width katakana, CJK ideographs. Mirrors the
# runtime HAS_CJK regex in the song index.html — keep the two in sync.
CJK = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")

# Voices that are NOT Japanese — must never carry CJK.
NON_JP_LANGS = {"en-US", "es-US"}

# Romanized-Japanese long vowels (macron / circumflex). These never appear in
# ordinary English definitions, so they're a high-precision romaji signal
# ("kopī", "ōkii", "Tōkyō").
ROMAJI_LONGVOWEL = re.compile(r"[āīūēōâîûêô]", re.I)


def has_cjk(s):
    return bool(CJK.search(s or ""))


def looks_like_bare_romaji(spoken):
    """Advisory only, HIGH-PRECISION. Distinguishing short English ("cute") from
    short romaji ("kuto") is unreliable, so we DON'T guess on letters alone — the
    real defense is structural (spoken strings are curated en_speak, never the
    display gloss). We only flag two unambiguous shapes:
      1. a parenthetical gloss like "pinsaro (slang)" / "x (lit. ...)"  — that's a
         *display* gloss form that should never be a *spoken* string;
      2. a romanized long vowel (macron/circumflex) — never in English prose.
    Heuristic, never fatal unless --strict-romaji."""
    s = (spoken or "").strip()
    if re.search(r"\((?:slang|lit\.?|name|particle|abbr\.?|sl\.?)[^)]*\)", s, re.I):
        return True
    if ROMAJI_LONGVOWEL.search(s):
        return True
    return False


# --- Podcast language-safety -------------------------------------------------
# The immerse podcast has an English HOST narrator + a native JP reader. the owner's
# rule is absolute: the English voice NEVER speaks Japanese, ANYWHERE — including
# the podcast. A HOST/EN line must carry (a) no CJK, and (b) no standalone
# romanized-Japanese particle/ritual token (the "wa"/"yubikiri genman" class the
# shinunoga podcast shipped). Those must move to a JP entry (voiced by the native
# reader or a curated human clip).
PODCAST_HOST_SPEAKERS = {"HOST", "GUEST", "EN", "HOST_A", "HOST_B"}

# Grammatical particles + set ritual phrases an English voice must not utter as
# Japanese. Kept to function words + named ritual phrases (NOT content/dialect
# words like "saigo"/"dasai"/"shika" — those are legitimate metalinguistic
# citations the podcast defines in English, and are the established style; the
# odoriko/inochi podcasts use them and stay clean).
PODCAST_ROMAJI_PARTICLES = {
    "wa", "ga", "wo", "ni", "de", "yo", "ne", "na", "mo", "ka", "sa", "ya",
    "ze", "zo", "wai", "yubikiri", "genman",
}
# Romaji that is also ordinary English — never flag (would false-positive on prose).
PODCAST_ROMAJI_STOP = {"no", "to", "so", "do", "on", "in"}


def _song_rom_vocab(data):
    """Lowercase romaji tokens (len>=2) the song actually SINGS (study words +
    title). Used to confirm a candidate particle token is genuinely this song's
    Japanese before flagging it — the disambiguation against English homographs."""
    vocab = set()

    def add(s):
        for tok in re.split(r"\s+", (s or "").lower()):
            tok = re.sub(r"[^a-z]", "", tok)
            if len(tok) >= 2:
                vocab.add(tok)

    for sec in data.get("sections", []):
        for w in sec.get("words", []):
            add(w.get("rom"))
    # title romaji if the data carries one (some songs store title_rom)
    add(data.get("title_rom"))
    return vocab


def check_podcast_language_safety(data):
    """Return (fails, warns) for podcast_script HOST/EN lines. Fails: CJK in a
    HOST line, or the EN voice speaking ANY romanized study word.

    HOLE THIS CLOSES: the original gate only knew a fixed PODCAST_ROMAJI_PARTICLES
    set (grammatical particles + a couple of ritual phrases), so a CONTENT word the
    host read aloud slipped through — odoriko's host said "Dokka" and the build
    shipped. The real rule is the owner's: the English voice must never say ANY Japanese.
    We now run the same vocabulary-anchored jp_token_detect the splicer uses over
    every HOST/EN line, so any romanized study word (dokka, mawaridashita, saigo,
    …) fails until it is moved to a JP entry / clip. The old particle scan is kept
    as a belt-and-braces fallback when the detector is unavailable."""
    fails, warns = [], []
    ps = data.get("podcast_script")
    if not isinstance(ps, list) or not ps:
        return fails, warns
    vocab = jp_token_detect.build_vocab_from_data(data) if jp_token_detect else None
    lint = (PODCAST_ROMAJI_PARTICLES & _song_rom_vocab(data)) - PODCAST_ROMAJI_STOP
    for i, entry in enumerate(ps):
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        spk = entry[0]
        text = entry[1] or ""
        if spk not in PODCAST_HOST_SPEAKERS:
            continue
        if has_cjk(text):
            fails.append(f"podcast entry #{i} ({spk}) contains Japanese characters "
                         f"(EN voice must not read Japanese): {text[:60]!r}")
        if vocab is not None:
            # phonotactic=True: podcasts also cite dictionary/derived forms with
            # no vocab-rom anchor ("chiru" beside vocab chitte) — the romaji-shape
            # net catches those. Study-clip texts (E11) stay exact+stem+suffix.
            det = sorted({s["text"] for s in
                          jp_token_detect.detect(text, vocab, phonotactic=True)})
            if det:
                fails.append(f"podcast entry #{i} ({spk}) speaks romanized Japanese "
                             f"study word(s) {det} — split the line so a JP entry/clip "
                             f"says it (EN voice never speaks Japanese): {text[:60]!r}")
        else:
            hits = sorted({t for t in (m.lower() for m in re.findall(r"[A-Za-z']+", text))
                           if len(t) >= 2 and t in lint})
            if hits:
                fails.append(f"podcast entry #{i} ({spk}) speaks romanized Japanese "
                             f"{hits} (move to a JP entry / human clip): {text[:60]!r}")
    return fails, warns


def check_en_splice_safety(manifest_path, data):
    """Gate E11 — run the JP-token detector over EVERY EN audio-job text and FAIL
    on any detected romanized study word NOT recorded as spliced in
    builds/<key>.en_splice.json. This is the study-clip analogue of the podcast
    gate: an EN word/context/gloss/explainer clip must never have the English
    voice read a romaji study word; gen_audio splices the real JP clip and records
    it, and this proves every such citation was spliced.

    Resolves builds/ from the repo root (…/tools/songcraft/builds/<key>.*).
    Returns (fails, degraded): degraded lists every reason the gate could
    not actually run — the caller decides whether that passes (it must not,
    by default: gate-completion 1.3; a gate that didn't run didn't pass)."""
    fails = []
    if jp_token_detect is None:
        return fails, ["jp_token_detect not importable — the E11 splice gate "
                       "cannot scan EN texts for romanized Japanese"]
    key = data.get("slug")
    if not key:
        return fails, ["data.json has no slug — cannot locate builds/<key>."
                       "audio_jobs.json for the E11 splice gate"]
    # repo root: walk up from the manifest until we find tools/songcraft/builds
    d = os.path.dirname(os.path.abspath(manifest_path))
    builds = None
    while d != os.path.dirname(d):
        cand = os.path.join(d, "tools", "songcraft", "builds")
        if os.path.isdir(cand):
            builds = cand
            break
        d = os.path.dirname(d)
    if not builds:
        return fails, ["no tools/songcraft/builds dir above the manifest — "
                       "E11 splice gate cannot run"]
    jobs_path = os.path.join(builds, f"{key}.audio_jobs.json")
    if not os.path.exists(jobs_path):
        return fails, [f"{key}.audio_jobs.json missing — E11 splice gate has "
                       f"no EN job texts to scan"]
    with open(jobs_path, encoding="utf-8") as f:
        jobs = json.load(f)
    splice_path = os.path.join(builds, f"{key}.en_splice.json")
    en_splice = {}
    if os.path.exists(splice_path):
        with open(splice_path, encoding="utf-8") as f:
            en_splice = json.load(f)
    vocab = jp_token_detect.build_vocab_from_data(data)
    for entry in jobs:
        if not isinstance(entry, list) or len(entry) < 3 or entry[0] != "en":
            continue
        text, out_rel = entry[1], entry[2]
        det = [s["text"] for s in jp_token_detect.detect(text, vocab)]
        if not det:
            continue
        recorded = set(en_splice.get(out_rel, []))
        missing = [t for t in det if t not in recorded]
        if missing:
            fails.append(f"EN clip {out_rel} speaks romanized Japanese {sorted(set(missing))} "
                         f"but it is NOT recorded as spliced in {key}.en_splice.json — the "
                         f"English voice would say Japanese. Re-run gen_audio to splice it. "
                         f"text: {text[:60]!r}")
    return fails, []


def collect_required_speech(data):
    """Every string the page hands to speakEN/speakJP, with its expected lang.
    Returns list of (lang, text, where). Mirrors what index.html actually speaks:
      - word en_speak  -> en-US ; word jp_speak -> ja-JP
      - section speak_en -> en-US
      - line_explain values -> en-US (Change 3; map keyed by normalized JP line)
    """
    req = []
    for sec in data.get("sections", []):
        sid = sec.get("id", "?")
        if sec.get("speak_en"):
            req.append(("en-US", sec["speak_en"], f"section {sid} speak_en"))
        for w in sec.get("words", []):
            rom = w.get("rom", "?")
            if w.get("jp_speak"):
                req.append(("ja-JP", w["jp_speak"], f"word {sid}/{rom} jp_speak"))
            if w.get("en_speak"):
                req.append(("en-US", w["en_speak"], f"word {sid}/{rom} en_speak"))
    # line_explain: { "<normalized jp line>": "<english explanation>" }  (Change 3)
    le = data.get("line_explain") or {}
    if isinstance(le, dict):
        for k, v in le.items():
            if v:
                req.append(("en-US", v, f"line_explain[{k[:18]}...]"))
    return req


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--data", default=None,
                    help="data.json (defaults to sibling of the manifest)")
    ap.add_argument("--strict-romaji", action="store_true",
                    help="treat the advisory romaji heuristic as a failure")
    ap.add_argument("--allow-degraded", action="store_true",
                    help="let checks that CANNOT run (missing data.json/"
                         "audio_jobs.json, detector import failure) pass as "
                         "warnings instead of failing — never in gate runs")
    args = ap.parse_args()

    if not os.path.exists(args.manifest):
        print(f"ERROR: manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    data_path = args.data or os.path.join(os.path.dirname(args.manifest), "data.json")

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    data, degraded = {}, []
    if os.path.exists(data_path):
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        degraded.append(f"data.json not found at {data_path} — page-speech "
                        f"coverage, podcast safety and E11 splice checks "
                        f"cannot run")

    fails, warns = [], []

    # --- Per-manifest-entry checks --------------------------------------------
    have = set()  # (lang, key) the runtime can resolve from audioCache
    for idx, entry in enumerate(manifest):
        if not isinstance(entry, list) or len(entry) < 4:
            fails.append(f"entry #{idx} malformed (need [lang, key, spoken, file]): {entry!r}")
            continue
        lang, key, spoken, filename = entry[0], entry[1], entry[2], entry[3]
        have.add((lang, key))

        if lang in NON_JP_LANGS and has_cjk(spoken):
            fails.append(f"{lang} clip speaks Japanese -> {filename}: {spoken[:50]!r}")
        if lang == "ja-JP" and not has_cjk(spoken):
            fails.append(f"ja-JP clip has no Japanese (likely a mixup) -> {filename}: {spoken[:50]!r}")
        if lang in NON_JP_LANGS and has_cjk(key):
            warns.append(f"{lang} entry KEY contains Japanese -> only a JP-carrying EN call could match it: {filename}")
        if lang in NON_JP_LANGS and looks_like_bare_romaji(spoken):
            msg = f"{lang} clip looks like a romaji gloss, not English prose -> {filename}: {spoken[:40]!r}"
            (fails if args.strict_romaji else warns).append(msg)

    # --- Missing-clip check (nothing the page speaks falls through to Siri) ----
    if data:
        for lang, text, where in collect_required_speech(data):
            if (lang, text) not in have:
                fails.append(f"no {lang} clip for {where}: {text[:50]!r} (would fall back to browser voice)")

    # --- Podcast language safety (EN voice must never read Japanese) -----------
    if data:
        p_fails, p_warns = check_podcast_language_safety(data)
        fails.extend(p_fails)
        warns.extend(p_warns)

    # --- E11 EN-study-clip splice safety (EN voice must never read Japanese) ----
    if data:
        s_fails, s_degraded = check_en_splice_safety(args.manifest, data)
        fails.extend(s_fails)
        degraded.extend(s_degraded)

    # --- Degraded checks: fail-closed unless explicitly allowed ----------------
    # (gate-completion 1.3) A check that could not run used to no-op — this
    # script printed its clean banner with the splice gate silently dark.
    if degraded:
        if args.allow_degraded:
            warns.extend(f"DEGRADED (allowed by flag): {d}" for d in degraded)
        else:
            fails.extend(f"DEGRADED: {d} — cannot verify == not verified "
                         f"(--allow-degraded to override outside gate runs)"
                         for d in degraded)

    # --- Report ---------------------------------------------------------------
    for w in warns:
        print(f"  WARN: {w}")
    for fa in fails:
        print(f"  FAIL: {fa}")
    n = len(manifest)
    if fails:
        print(f"\n✗ TTS safety: {len(fails)} failure(s), {len(warns)} warning(s) over {n} clips.")
        return 1
    print(f"\n✓ TTS safety: clean ({len(warns)} warning(s)) over {n} clips.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
