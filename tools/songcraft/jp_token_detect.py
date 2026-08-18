#!/usr/bin/env python3
"""jp_token_detect.py — shared Japanese-token detector for Manaoke EN audio.

the owner's rule: "English speakers in this app should never say Japanese words. We have
an entire dictionary and text-to-speech engine dedicated toward Japanese." This
finds romanized-Japanese tokens inside an ENGLISH string by matching them against
the song's OWN sung vocabulary (study-word roms), so the audio engine can splice
the real JP clip in place of the English voice reading the romaji.

Detection is vocabulary-anchored (precision over recall): a run of English word
tokens is only flagged when it EXACTLY equals a study word's rom (single token
like `dokka`, `yubikiri`, `hora`, `genman`, `anata`, or a consecutive rom
bigram/trigram like `ano ko`), AND it is not an ordinary English word (the
COMMON_EN guard — when a token IS common English we treat it as English, since a
false JP-splice is worse than an occasional missed rom).

MATCHING STRATEGIES (in order; later ones only see tokens the earlier left):
  1. exact  — token run == a vocab rom (unigram or consecutive bigram/trigram).
  2. stem   — token shares a common prefix of >=4 chars with a vocab rom token
              (token len>=4, not COMMON_EN). Catches CITATION forms the podcast
              quotes: "hajikeru" (dictionary form) vs vocab "hajikete".
  3. suffix — token (len>=3, not COMMON_EN) is a strict suffix of a vocab rom
              token of len>=6. Catches quoted MORPHEMES: "tara" out of vocab
              "yarinaosetara". NOTE: /usr/share/dict/words contains "tara" and
              "hora", so the system dict must NOT veto stem/suffix — COMMON_EN
              is the only English veto for those two strategies.
  4. shape  — OPTIONAL (phonotactic=True; podcast texts): a token whose letters
              scan as pure romaji syllables (len>=4, not COMMON_EN, not in
              /usr/share/dict/words). Catches citation forms unrelated to any
              vocab rom ("chiru" quoted next to vocab 散って/chitte).

Public API:
    build_vocab_from_content(content) -> [entry...]
    build_vocab_from_data(data)       -> [entry...]
    detect(text, vocab, phonotactic=False) -> [span...]
each entry: {rom, rom_tokens:[...], surface, kana, out_rel}
each span:  {start, end, text, entry, match, token}
  - start/end: char offsets into the input text
  - match: 'exact' | 'stem' | 'suffix' | 'shape'
  - token: cleaned lowercase romaji token(s) matched
  - entry: the matched vocab entry ('shape' spans carry entry=None)
Splicers: an 'exact' span speaks with entry['out_rel']'s clip; any other match is
a CITATION form (different inflection from the vocab clip) — its clip is
jp/podcast_<token>.mp3, rendered by the citation-clip path in gen_audio.

Importable by gen_audio.py (splicing) and validate_tts_safety.py (gate E11).
"""
import os
import re

# ---------------------------------------------------------------------------
# COMMON_EN — ordinary English words that must NEVER be treated as Japanese even
# when they collide with a romaji token (to/so/no/on/in/one/name/made/date/...).
# Precision-first: it is far better to leave a rare rom unspliced than to splice a
# JP clip over a real English word. Grows freely — add a colliding word and it's
# excluded forever. (~600 hand-picked high-frequency words + every romaji-shaped
# English word we could think of.)
# ---------------------------------------------------------------------------
COMMON_EN = set("""
a an the this that these those it its it's here there where when who whom whose
which what why how i me my mine myself we us our ours you your yours he him his
she her hers they them their theirs one ones none no not nor so do does did done
doing to too into onto in on at by of off up out over under above below from with
without within about across after against along among around before behind
beneath beside besides between beyond during except for inside near since through
throughout till until unto upon via and or but yet if then else than as because
while whereas though although even also just only still ever never always often
sometimes soon now today tonight yesterday tomorrow again once twice
be been being am is are was were will would shall should can could may might must
have has had let make made makes making take took taken taking get got gotten
give gave given go goes going gone come came coming say said says see saw seen
know knew known think thought want wanted feel felt find found tell told ask asked
work works word words name names use used using try tried call called put keep
kept mean means meant leave left turn turned start started show shown showed
hear heard play played move moved live lived hold held bring brought begin began
run ran walk walked talk talked stand stood lose lost pay paid meet met set sit
sat speak spoke read write wrote grow grew open close first second third last next
new old good bad big small long short high low late early right wrong same
different other another each every all both few many much more most less least
some any little lot lots kind sort type way ways time times year years day days
week month hour minute moment life world people man woman men women child kid
kids friend love loved lover song songs line lines verse chorus music sound
sounds voice heart hearts mind soul body hand hands eye eyes face head home place
places thing things something anything everything nothing someone anyone everyone
side idea story stories part parts point end ends real true false whole half
gender neutral western eastern japan japanese tokyo okayama english slang casual
formal literal meaning sense vibe tone lilt final soft gentle grim vague purpose
memory memories feeling feelings surface bubble bubbles float floating misplaced
he's she's we're you're they're i'm i've you've we've isn't aren't wasn't don't
doesn't didn't won't can't couldn't wouldn't shouldn't that's there's here's
what's let's o.k. ok okay yes yeah nope yep hey oh ah um well like so-called
change changes changed changing chance chances attach attached attaches
attachment goodbye goodbyes byebye bye outro intro
""".split())

# Proper nouns the podcast legitimately says in English (artist/place/work/brand
# names). These are Japanese-origin words, but citing a NAME is established
# podcast style (the host says "Fujii Kaze", "Ozaki Sekaikan", "Kurosawa's film
# Ikiru", "That's Manaoke" as English prose). Consulted by the CITATION
# strategies only (stem/suffix/shape) — an exact study-word rom still matches,
# so a song that actually TEACHES 生きる/ikiru still gets its splice.
NAME_ALLOW = set("""
kaze fujii vaundy kyushu okayama tokyo osaka kansai kanto shikoku hokkaido
creephyp kanaboon manaoke akira kurosawa ozaki sekaikan ikiru naruto
""".split())

# tokens (letters + a few in-word marks); apostrophes/hyphens kept so "don't",
# "so-called" stay single tokens
_TOK = re.compile(r"[A-Za-z][A-Za-z'’.-]*")


def _rom_tokens(rom):
    """Lowercase rom tokens, split on whitespace AND hyphens (e.g. 'ano ne' ->
    ['ano','ne']; 'higaisha-zura' -> ['higaisha','zura'] so prose "higaisha zura"
    exact-matches the vocab clip)."""
    return [t for t in re.split(r"[\s\-]+", (rom or "").strip().lower()) if t]


def rom_uid(rom):
    s = str(rom or "").replace(" ", "-").replace("·", "").replace("/", "_")
    return re.sub(r"^-+|-+$", "", s)


def _entry(surface, kana, rom, out_rel):
    return {"rom": (rom or "").lower(), "rom_tokens": _rom_tokens(rom),
            "surface": surface, "kana": kana, "out_rel": out_rel}


def build_vocab_from_content(content):
    """From builds/<key>.content.json — flat top-level words[] (with 'section')
    OR nested sections[].words[]. Returns detector entries with the JP clip
    out_rel each rom maps to."""
    entries = []
    words = list(content.get("words") or [])
    for s in content.get("sections", []):
        for w in (s.get("words") or []):
            w = dict(w)
            w.setdefault("section", s.get("id"))
            words.append(w)
    for w in words:
        rom = w.get("rom")
        if not rom:
            continue
        sid = w.get("section", "")
        uid = rom_uid(w.get("uid") or rom)
        out_rel = f"jp/word_{sid}_{uid}.mp3"
        kana = w.get("jp_speak") or w.get("jp")
        entries.append(_entry(w.get("jp"), kana, rom, out_rel))
    return _dedupe(entries)


def build_vocab_from_data(data):
    """From songs/<slug>/data.json — sections[].words[]."""
    entries = []
    for s in data.get("sections", []):
        sid = s.get("id", "")
        for w in (s.get("words") or []):
            rom = w.get("rom")
            if not rom:
                continue
            uid = rom_uid(w.get("uid") or rom)
            out_rel = f"jp/word_{sid}_{uid}.mp3"
            kana = w.get("jp_speak") or w.get("jp")
            entries.append(_entry(w.get("jp"), kana, rom, out_rel))
    return _dedupe(entries)


def _dedupe(entries):
    """Keep one entry per (rom, out_rel); longest rom_tokens first so multi-word
    phrases (ano ko) win over their unigram parts during matching."""
    seen = set()
    uniq = []
    for e in entries:
        k = (e["rom"], e["out_rel"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    uniq.sort(key=lambda e: -len(e["rom_tokens"]))
    return uniq


def _all_common(toks):
    return all(t.strip("'’.-").lower() in COMMON_EN for t in toks)


def _common_prefix_len(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


# Romaji phonotactic shape: a string that scans entirely as Hepburn syllables
# (optional consonant/digraph + vowel, or a moraic n not followed by a vowel),
# with an optional geminate double consonant before a syllable (tte, kka).
# Used ONLY by the optional 'shape' strategy.
_ROMAJI_SHAPE = re.compile(
    r"^(?:(?:[kgsztdnhbpmr]?(?:sh|ch|ts|[kgsztdnhbpmyrwfj]y?|[fjvw]))?[aiueo]|"
    r"n(?![aiueo]))+$")

_EN_DICT = None
def _en_dict():
    """Lowercased /usr/share/dict/words (veto for the 'shape' strategy ONLY —
    stem/suffix must not use it: it contains 'tara' and 'hora')."""
    global _EN_DICT
    if _EN_DICT is None:
        _EN_DICT = set()
        try:
            with open("/usr/share/dict/words", encoding="utf-8") as f:
                _EN_DICT = {ln.strip().lower() for ln in f if ln.strip()}
        except Exception:
            pass
    return _EN_DICT


def detect(text, vocab, phonotactic=False):
    """Find romanized-JP spans in an English `text`. Returns a list of
    {start, end, text, entry, match, token} char-range spans, left-to-right,
    non-overlapping. Strategies run in precision order: exact vocab-rom runs
    first, then (over the still-unmatched tokens only) stem and suffix citation
    matching, then optionally the romaji-shape net (podcast texts)."""
    if not text:
        return []
    toks = [(m.group(0), m.start(), m.end()) for m in _TOK.finditer(text)]
    low = [t[0].strip("'’.-").lower() for t in toks]
    # index vocab by first token -> list of entries (longest rom first)
    by_first = {}
    for e in vocab:
        if e["rom_tokens"]:
            by_first.setdefault(e["rom_tokens"][0], []).append(e)
    for lst in by_first.values():
        lst.sort(key=lambda e: -len(e["rom_tokens"]))

    spans = []
    consumed = [False] * len(toks)

    # -- 1. exact vocab-rom runs ---------------------------------------------
    i = 0
    n = len(toks)
    while i < n:
        cands = by_first.get(low[i], [])
        matched = None
        for e in cands:
            rt = e["rom_tokens"]
            if i + len(rt) <= n and low[i:i + len(rt)] == rt:
                if not _all_common(low[i:i + len(rt)]):
                    matched = e
                    break
        if matched:
            rt = matched["rom_tokens"]
            start = toks[i][1]
            end = toks[i + len(rt) - 1][2]
            spans.append({"start": start, "end": end, "text": text[start:end],
                          "entry": matched, "match": "exact",
                          "token": " ".join(low[i:i + len(rt)])})
            for k in range(i, i + len(rt)):
                consumed[k] = True
            i += len(rt)
        else:
            i += 1

    # unigram vocab rom tokens for the citation strategies
    vocab_unigrams = []          # (rom_token, entry)
    for e in vocab:
        for vt in e["rom_tokens"]:
            vocab_unigrams.append((vt, e))

    # -- 2./3. stem + suffix citation matching (unmatched tokens only) --------
    # Precision guards: COMMON_EN + NAME_ALLOW veto, AND the token must itself
    # scan as romaji syllables (kills English lookalikes: "attached" vs vocab
    # atta, "changing"/"chance" vs chanto — none of which are romaji-shaped).
    # The system dict is deliberately NOT consulted here (it contains tara/hora).
    def _citable(t):
        return (t and t.isalpha() and t not in COMMON_EN
                and t not in NAME_ALLOW and _ROMAJI_SHAPE.match(t))

    idx = 0
    while idx < n:
        if consumed[idx]:
            idx += 1
            continue
        tok = low[idx]
        if not _citable(tok):
            idx += 1
            continue
        best = None              # (strategy, score, entry, n_toks, token_str)
        # stem: >=4-char common prefix with a vocab rom token (citation form).
        # Also try JOINING the next token (citation of a compound written as two
        # words: "neri aruku" vs vocab rom neriaruketara) — the longer prefix wins.
        if len(tok) >= 4:
            for vt, e in vocab_unigrams:
                cp = _common_prefix_len(tok, vt)
                if cp >= 4 and (best is None or cp > best[1]):
                    best = ("stem", cp, e, 1, tok)
            if idx + 1 < n and not consumed[idx + 1] and _citable(low[idx + 1]):
                joined = tok + low[idx + 1]
                for vt, e in vocab_unigrams:
                    cp = _common_prefix_len(joined, vt)
                    if cp >= 4 and cp > len(tok) and (best is None or cp > best[1]):
                        best = ("stem", cp, e, 2, tok + " " + low[idx + 1])
        # suffix: token is a strict suffix of a long vocab rom token (morpheme)
        if best is None and len(tok) >= 3:
            for vt, e in vocab_unigrams:
                if len(vt) >= 6 and vt != tok and vt.endswith(tok):
                    best = ("suffix", len(tok), e, 1, tok)
                    break
        if best:
            strategy, _score, e, ntok, token_str = best
            start = toks[idx][1]
            end = toks[idx + ntok - 1][2]
            spans.append({"start": start, "end": end, "text": text[start:end],
                          "entry": e, "match": strategy, "token": token_str})
            for k in range(idx, idx + ntok):
                consumed[k] = True
            idx += ntok
        else:
            idx += 1

    # -- 4. optional romaji-shape net (podcast texts) --------------------------
    if phonotactic:
        for idx in range(n):
            if consumed[idx]:
                continue
            tok = low[idx]
            if (len(tok) >= 4 and tok.isalpha() and tok not in COMMON_EN
                    and tok not in NAME_ALLOW and tok not in _en_dict()
                    and _ROMAJI_SHAPE.match(tok)):
                start, end = toks[idx][1], toks[idx][2]
                spans.append({"start": start, "end": end, "text": text[start:end],
                              "entry": None, "match": "shape", "token": tok})
                consumed[idx] = True

    spans.sort(key=lambda s: s["start"])
    return spans


if __name__ == "__main__":
    import json, sys
    # quick manual check: python jp_token_detect.py builds/odoriko.content.json "some text"
    content = json.load(open(sys.argv[1], encoding="utf-8"))
    vocab = build_vocab_from_content(content)
    txt = sys.argv[2] if len(sys.argv) > 2 else "Dokka is a slurred, casual somewhere."
    for s in detect(txt, vocab):
        print(s["text"], "->", s["entry"]["surface"], s["entry"]["out_rel"])
