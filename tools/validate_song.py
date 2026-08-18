#!/usr/bin/env python3
"""Manaoke song-build linter.

WHY THIS EXISTS
---------------
Two bug classes shipped to production because nothing lints song builds:

  (a) Coverage gaps. The page builds each lyric line's romaji AND its tappable
      study cards from study-word matches over data.json sections[].words[] (a
      greedy first-match walk). Any span of apple_lyrics.lines[].text that no
      word covers (i) renders with NO romaji if it is kanji, and (ii) is missing
      from the word-by-word study grid even when kana_timings gap-fills the
      romaji — so the learner can see and hear a syllable in the line yet cannot
      study or hear it in isolation. Real victims: "OL さん" in コピーにお茶汲み
      OL さん (kanji-side romaji hole), and the trailing question particle か in
      いつかは言えるか / あんたに言えるか (kana-side study hole — the か that turns
      the line into a question had no card). BOTH are E1 errors now; a span may
      only stay uncovered if listed in data.json "coverage_exceptions".
  (b) A CSS specificity bug killed the translation toggle. CSS is out of scope
      here (hard to lint statically); this tool covers everything else.

This validator replicates the page's own matching walk (read straight out of
index.html: tokenize / collectStudyWords / buildRomajiParts) so a coverage gap
fails the build instead of silently shipping. It also checks line-level
translation/explainer coverage, audio-file existence, manifest integrity, and
basic schema shape.

USAGE
-----
    python3 tools/validate_song.py songs/<slug>

Exit code 0 = clean OR warnings-only, 1 = at least one ERROR, 2 = bad usage.

RELATION TO validate_tts_safety.py
-----------------------------------
Separate concern. validate_tts_safety.py checks language safety (an en-US clip
must not speak Japanese, nothing the page speaks may fall through to the browser
voice). This tool does NOT duplicate those CJK-in-EN checks — run both. This one
is about build integrity: romaji coverage, line translations, asset presence,
schema.
"""

import hashlib
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Constants mirrored from the song index.html. Keep in sync if the page changes.
# ---------------------------------------------------------------------------

# Required top-level data.json keys (v057 shape).
REQUIRED_TOP_KEYS = [
    "song_number", "title_jp", "title_en", "artist", "slug",
    "youtube_id", "sections", "apple_lyrics",
]
# Required non-empty fields on every section word.
REQUIRED_WORD_FIELDS = ["jp", "rom", "en", "en_speak"]

# E9 provenance gate: a word whose SPOKEN form is one of these lone grammatical
# particles must NOT be voiced by Kokoro — Kokoro mangles isolated particles
# (a bare が came out "ガイド", に as "いろいろ"). Its served clip must come from a
# real voice (curated human dict library, else a Qwen Ono_Anna carrier-cut) and
# be recorded in builds/<folder>.clip_provenance.json. Mirror of gen_audio.py
# LONE_PARTICLES. は/へ/を author their spoken form (わ/え/お) via jp_speak (E8),
# but the raw particle also counts here so a pre-E8 song (silhouette) is caught.
LONE_PARTICLES = set("にとものがでやかねよなへをはぞさお")
_STRIP_KANA = re.compile(r"[。、，．！？!?,.\sーっッ]")
# Small kana that merge into the preceding mora (for the E12 <=2-mora test).
_SMALL_MORA = set("ゃゅょャュョぁぃぅぇぉァィゥェォ")


def mora_count(kana):
    """Morae in a reading: drop punctuation/長音/っ, count kana, merging small
    ゃゅょ / small vowels into the preceding mora. Mirror of gen_audio._mora_count."""
    k = _STRIP_KANA.sub("", kana or "")
    return sum(1 for ch in k if ch not in _SMALL_MORA)


# E15 pronunciation lexicon: keyed by FOLDED spoken kana (strip then widen small
# vowels) — mirror of gen_audio._fold_kana.
_FOLD_SMALL = str.maketrans("ぁぃぅぇぉ", "あいうえお")


def fold_kana(kana):
    return _STRIP_KANA.sub("", kana or "").translate(_FOLD_SMALL)


def load_lexicon(repo_root):
    """words map of tools/songcraft/pronunciation_lexicon.json (folded spoken
    kana -> entry), {} when the file is absent (a legitimate pass), None when
    the file EXISTS but cannot be read/parsed — the caller must turn None
    into an E15 error (fail-closed: a corrupt memory is not an empty one).
    Local twin of gen_audio.load_lexicon so this validator stays
    dependency-light (no songcraft import)."""
    p = os.path.join(repo_root, "tools", "songcraft", "pronunciation_lexicon.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("words", {})
    except Exception:
        return None

# Instrumental / section-label lines that the page never tokenizes. The page
# marks instrumental lines structurally (empty text or a synthesized gap line);
# in data.json the real lyric lines all carry text, so we treat any line with
# non-empty text as a lyric line. Pure-label text is filtered by the same
# whitespace-strip + presence checks the page applies.
WS_RE = re.compile(r"\s")


# ---------------------------------------------------------------------------
# Helpers replicated from index.html
# ---------------------------------------------------------------------------

def line_tr_key(s):
    """index.html lineTrKey(): strip a trailing (×N) repeat marker, then strip
    ALL whitespace. JS: String(s).replace(/\\s*\\(×\\d+\\)\\s*$/,'').replace(/\\s+/g,'').trim()"""
    s = s or ""
    s = re.sub(r"\s*\(×\d+\)\s*$", "", s)
    s = re.sub(r"\s+", "", s)
    return s.strip()


def rom_uid(rom):
    """index.html _romUid(): rom with ' '->'-', '·'->'', '/'->'_', strip edge '-'."""
    s = str(rom or "")
    s = s.replace(" ", "-").replace("·", "").replace("/", "_")
    s = re.sub(r"^-+|-+$", "", s)
    return s


# ---------------------------------------------------------------------------
# E7 bare-gloss gate — the word-by-word drill SPEAKS each word's `gloss` field
# in isolation. A gloss that is nothing but bare English function words or
# pronouns ("of", "to", "I, me", "you", "but", "at, with") sounds clipped and
# robotic from TTS — no sentence prosody — and teaches almost nothing. Content
# words are fine ("kid", "mom", "love"); the offender is the *floating function
# word / bare pronoun*, regardless of grammatical category (this is why a
# particle-only pass kept missing pronouns like アタシ/あんた and conjunctions
# like けど). Fix = a short natural phrase (の → "of, or belonging to").
# GROWABLE: when a new bare offender surfaces, add the one word here and it's
# caught forever (mirrors tools/human_audio BAD_PARTICLES).
BARE_GLOSS_OFFENDERS = {
    # prepositions / particle-glosses
    "of", "to", "into", "onto", "in", "on", "at", "by", "with", "for", "from",
    "as", "off", "up", "out", "over", "under",
    # conjunctions / connectives
    "and", "or", "but", "so", "than", "then", "yet", "nor", "if", "because",
    # articles / degree fillers / negation
    "the", "a", "an", "even", "also", "too", "just", "only", "not", "no",
    # bare pronouns (any person/number)
    "i", "me", "my", "mine", "myself", "you", "your", "yours", "yourself",
    "he", "him", "his", "she", "her", "hers", "herself", "himself",
    "it", "its", "we", "us", "our", "ours", "they", "them", "their", "theirs",
    "this", "that", "these", "those", "one",
}


def is_bare_gloss(gloss):
    """True iff the drill gloss is composed ONLY of bare function words / pronouns
    — a single one, or a comma/slash list of them. Split ONLY on comma/slash/
    semicolon (the "alternatives" separators), NEVER on ' and '/' or ' — those
    appear inside legit phrases/idioms, and splitting on them false-flagged
    'over and over' as [over, over]. So multi-word tokens pass: 'over and over',
    'not even', 'of, or belonging to' (→ ['of', 'or belonging to']) all pass;
    'I, me', 'at, with', 'because, so', bare 'but'/'you'/'of' all fail."""
    toks = [t.strip().lower().strip(".'\"") for t in re.split(r"[,/;]", gloss or "")]
    toks = [t for t in toks if t]
    return bool(toks) and all(t in BARE_GLOSS_OFFENDERS for t in toks)


def sorted_vocab(words):
    """Words with a jp field, sorted by jp length DESC (longest-first greedy
    match), mirroring sec._vocabSorted / globalVocabSorted in index.html."""
    return sorted((w for w in words if w.get("jp")), key=lambda w: -len(w["jp"]))


def ws_tolerant_match(text, i, word_list):
    """Replicate the page's wsTolerantMatch (build nhxbyb, 2026-06-11): a word
    may match across display whitespace inside the line (Apple lyric lines
    space-separate what is one study word in data.json, e.g. line "OL さん"
    vs word jp "OLさん"). Returns chars consumed in text (interior whitespace
    included) or 0. List order = first match wins, same as the JS."""
    for w in word_list:
        jp = w.get("jp") or ""
        if not jp:
            continue
        ti, wi = i, 0
        while wi < len(jp) and ti < len(text):
            if text[ti] == jp[wi]:
                ti += 1
                wi += 1
            elif wi > 0 and WS_RE.match(text[ti]):
                ti += 1
            else:
                break
        if wi == len(jp):
            return ti - i
    return 0


def coverage_walk(text, primary, fallback):
    """Replicate the page's greedy first-match walk (collectStudyWords /
    buildRomajiParts; whitespace-tolerant as of build nhxbyb):
      - skip whitespace,
      - at each non-ws position try PRIMARY then FALLBACK via
        ws_tolerant_match (first match wins),
      - on a match, advance by the chars consumed and mark them covered,
      - on no match, advance ONE char (that char is uncovered).
    Returns a covered[] boolean list parallel to text."""
    n = len(text)
    covered = [False] * n
    i = 0
    while i < n:
        if WS_RE.match(text[i]):
            i += 1
            continue
        consumed = ws_tolerant_match(text, i, primary) or \
            ws_tolerant_match(text, i, fallback)
        if consumed:
            for k in range(i, i + consumed):
                covered[k] = True
            i += consumed
        else:
            i += 1
    return covered


# Kana scripts + Latin/digits can be phonetically aligned to kana_timings by
# the page's runtime gap-filler (buildRomajiParts gapFillParts, build nhxbyb),
# so such gaps self-heal into a complete romaji line and downgrade to a
# WARNING (the word is still untappable — worth fixing in data eventually).
# Kanji cannot be aligned and stay hard ERRORS.
KANA_OR_LATIN_RE = re.compile(r"^[぀-ゟ゠-ヿA-Za-z0-9ー]+$")


def match_line_to_section(line_text, sections):
    """Replicate matchLineToSection(): context_lines exact -> substring -> best
    by word-overlap. Determines which section's words are PRIMARY for a line.
    Returns the section dict or None."""
    def norm(s):
        s = re.sub(r"\s*\(×\d+\)\s*$", "", s or "")
        return s.strip()

    target = norm(line_text)
    if not target:
        return None
    # 1. exact match against any context_lines fragment
    for sec in sections:
        for cl in sec.get("context_lines", []):
            for p in re.split(r"\s*/\s*", cl):
                if norm(p) and norm(p) == target:
                    return sec
    # 2. substring either direction
    for sec in sections:
        for cl in sec.get("context_lines", []):
            for p in re.split(r"\s*/\s*", cl):
                c = norm(p)
                if not c:
                    continue
                if c in target or target in c:
                    return sec
    # 3. best by word overlap (longest single word, then total chars)
    best, best_longest, best_total = None, 0, 0
    for sec in sections:
        longest = total = 0
        for w in sec.get("words", []):
            jp = w.get("jp")
            if jp and jp in target:
                longest = max(longest, len(jp))
                total += len(jp)
        if total == 0:
            continue
        if longest > best_longest or (longest == best_longest and total > best_total):
            best_longest, best_total, best = longest, total, sec
    return best


def line_kana_rom(line):
    """The full romaji READING of a line from kana_timings[].rom, concatenated.

    NOTE on why this is line-level, not per-span: kana_timings is the phonetic
    reading sequence (1 entry per kana of the *reading*), NOT positionally tied
    to text chars. A single kanji (嬢) is one text char but two kana (じょう);
    katakana ピンサロ reads as ぴんさろ. There is no clean char->kana map, so
    mapping an arbitrary text span to its kana is unsound. We therefore use the
    whole-line reading for the W1 comparison and for the E1 supplementary hint."""
    kts = line.get("kana_timings") or []
    return "".join(k.get("rom", "") for k in kts)


# ---------------------------------------------------------------------------
# Macron normalization for the W1 rom-vs-kana lenient comparison.
# ---------------------------------------------------------------------------

def normalize_rom(s):
    """Lenient romaji normalization for the W1 WARNING comparison only. Folds:
      - macron/circumflex long vowels (ō≈ou≈oo, ū≈uu, etc.),
      - separators the page strips (space, middle-dot, hyphen, slash, apostrophe),
      - the small-tsu gemination marker (っ/ッ and its romaji double consonant),
      - the は/へ/を particle orthography split: word roms spell them by sound
        (wa/e/o) while kana_timings spells them literally (ha/he/wo). We map both
        sides through a sentinel so that split never trips the check.
      - z-row reading drift (zu≈zo, e.g. 数え kazoete vs kazuete).
    This is intentionally generous — W1 is advisory, and we only want to surface
    a genuinely different *reading*, not orthographic noise."""
    s = (s or "").lower()
    repl = {
        "ō": "ou", "ū": "uu", "ā": "aa", "ī": "ii", "ē": "ee",
        "ô": "ou", "û": "uu", "â": "aa", "î": "ii", "ê": "ee",
        "’": "", "'": "",
    }
    for a, b in repl.items():
        s = s.replace(a, b)
    s = re.sub(r"[ ·\-/]", "", s)
    s = s.replace("っ", "").replace("ッ", "")
    # collapse long-vowel spellings to a single vowel
    for lv in ("oo", "ou", "uu", "aa", "ii", "ee"):
        s = s.replace(lv, lv[0])
    # remove doubled consonants left by gemination
    s = re.sub(r"([bcdfghjklmnpqrstvwxyz])\1", r"\1", s)
    # particle orthography: fold は/wa, へ/e, を/o ambiguity via sentinels
    s = s.replace("wa", "\x01").replace("ha", "\x01")
    s = s.replace("zu", "\x02").replace("zo", "\x02")
    return s


# ---------------------------------------------------------------------------
# Report accumulator
# ---------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.errors = {}   # check_id -> list[str]
        self.warns = {}    # check_id -> list[str]

    def error(self, check, msg):
        self.errors.setdefault(check, []).append(msg)

    def warn(self, check, msg):
        self.warns.setdefault(check, []).append(msg)

    def n_errors(self):
        return sum(len(v) for v in self.errors.values())

    def n_warns(self):
        return sum(len(v) for v in self.warns.values())


# ---------------------------------------------------------------------------
# Parse LINE_TR / LINE_EXPLAIN object literals out of index.html
# ---------------------------------------------------------------------------

def extract_js_block(html, var_name):
    """Return the raw text between `const <var_name> = {` and its matching `};`."""
    m = re.search(r"const\s+" + re.escape(var_name) + r"\s*=\s*\{", html)
    if not m:
        return None
    i = m.end()  # just after the opening brace
    depth = 1
    start = i
    in_str = None
    esc = False
    while i < len(html):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_str:
                in_str = None
        else:
            if ch in "'\"`":
                in_str = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return html[start:i]
        i += 1
    return None


def parse_line_map(html, var_name, value_key=None):
    """Parse a JS object literal whose keys are JP strings and whose values are
    either a string (LINE_EXPLAIN) or an object with an `en` field (LINE_TR).

    Returns dict[jp_key] -> en_string (possibly empty). Keys are kept verbatim
    (they are already whitespace-stripped in the source, matching lineTrKey).

    JS string-literal escapes for the limited set we use (\\' \\" \\n \\\\) are
    unescaped; smart quotes are left as-is (they are real chars, not escapes)."""
    block = extract_js_block(html, var_name)
    if block is None:
        return None
    result = {}

    def unescape(s, quote):
        out = []
        i = 0
        while i < len(s):
            c = s[i]
            if c == "\\" and i + 1 < len(s):
                nxt = s[i + 1]
                mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\",
                           "'": "'", '"': '"', "`": "`", "/": "/"}
                if nxt in mapping:
                    out.append(mapping[nxt])
                    i += 2
                    continue
                if nxt == "x" and i + 3 < len(s):
                    try:
                        out.append(chr(int(s[i + 2:i + 4], 16)))
                        i += 4
                        continue
                    except ValueError:
                        pass
                out.append(nxt)
                i += 2
                continue
            out.append(c)
            i += 1
        return "".join(out)

    # Tokenize key/value pairs. Keys are quoted JS strings followed by ':'.
    # Walk char by char to respect nested braces and strings.
    i = 0
    n = len(block)
    while i < n:
        c = block[i]
        if c in "'\"`":
            # read a key string
            quote = c
            j = i + 1
            buf = []
            while j < n:
                if block[j] == "\\":
                    buf.append(block[j:j + 2])
                    j += 2
                    continue
                if block[j] == quote:
                    break
                buf.append(block[j])
                j += 1
            key = unescape("".join(buf), quote)
            j += 1
            # skip whitespace to ':'
            while j < n and block[j] in " \t\n\r":
                j += 1
            if j >= n or block[j] != ":":
                i = j
                continue
            j += 1
            while j < n and block[j] in " \t\n\r":
                j += 1
            # value: either a string or an object {...}
            en_val = ""
            if j < n and block[j] in "'\"`":
                vq = block[j]
                k = j + 1
                vbuf = []
                while k < n:
                    if block[k] == "\\":
                        vbuf.append(block[k:k + 2])
                        k += 2
                        continue
                    if block[k] == vq:
                        break
                    vbuf.append(block[k])
                    k += 1
                en_val = unescape("".join(vbuf), vq)
                j = k + 1
            elif j < n and block[j] == "{":
                # object literal: find matching brace, then pull `en` field
                depth = 0
                k = j
                instr = None
                esc = False
                while k < n:
                    ch = block[k]
                    if instr:
                        if esc:
                            esc = False
                        elif ch == "\\":
                            esc = True
                        elif ch == instr:
                            instr = None
                    else:
                        if ch in "'\"`":
                            instr = ch
                        elif ch == "{":
                            depth += 1
                        elif ch == "}":
                            depth -= 1
                            if depth == 0:
                                break
                    k += 1
                obj_src = block[j:k + 1]
                fld = value_key or "en"
                vm = re.search(re.escape(fld) + r"\s*:\s*(['\"`])", obj_src)
                if vm:
                    vq = vm.group(1)
                    p = vm.end()
                    vbuf = []
                    while p < len(obj_src):
                        if obj_src[p] == "\\":
                            vbuf.append(obj_src[p:p + 2])
                            p += 2
                            continue
                        if obj_src[p] == vq:
                            break
                        vbuf.append(obj_src[p])
                        p += 1
                    en_val = unescape("".join(vbuf), vq)
                j = k + 1
            result[key] = en_val
            i = j
            continue
        i += 1
    return result


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def resolve_song_folder(song_dir, data):
    """Determine the shared-assets folder name under songs/_assets/<song>/.
    The page references audio relative ('audio/...'); _redirects rewrites
    /songs/:dir/audio/* to /songs/_assets/<song>/audio/*. We resolve <song> by:
      1. reading the repo-root _redirects for the rewrite target, else
      2. data.json slug.
    Returns (folder_name, assets_path)."""
    repo_root = song_dir
    while repo_root != os.path.dirname(repo_root):
        if os.path.isdir(os.path.join(repo_root, "songs", "_assets")):
            break
        repo_root = os.path.dirname(repo_root)
    # _redirects can hold MULTIPLE songs' rules (specific per-build lines above
    # a generic :dir fallthrough). Resolve like Cloudflare does — first rule
    # whose SOURCE pattern matches THIS song dir — not just the first rule in
    # the file (that bug surfaced the day a second song was added).
    dir_name = os.path.basename(os.path.normpath(song_dir))
    folder = None
    generic = None
    red = os.path.join(repo_root, "_redirects")
    if os.path.exists(red):
        with open(red, encoding="utf-8") as f:
            for ln in f:
                m = re.search(r"^/songs/([^/\s]+)/audio/\*\s+/songs/_assets/([^/]+)/audio/", ln)
                if not m:
                    continue
                src, target = m.group(1), m.group(2)
                if src == dir_name:
                    folder = target
                    break
                if src.startswith(":") and generic is None:
                    generic = target
    if not folder:
        folder = generic
    if not folder:
        folder = data.get("slug") or data.get("r2_folder")
    assets = os.path.join(repo_root, "songs", "_assets", folder) if folder else None
    return folder, assets


def is_lyric_line(ln):
    """A line the page tokenizes: non-empty text and not flagged instrumental/bg."""
    if ln.get("is_background"):
        return False
    return bool((ln.get("text") or "").strip())


def run_checks(song_dir):
    rep = Report()
    data_path = os.path.join(song_dir, "data.json")
    html_path = os.path.join(song_dir, "index.html")
    manifest_path = os.path.join(song_dir, "tts_manifest.json")

    if not os.path.exists(data_path):
        rep.error("E6", f"data.json not found at {data_path}")
        return rep, None
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    # Spans explicitly allowed to stay uncovered (no study card). Use ONLY for
    # genuinely meaningless syllables — never to wave through a real particle.
    # An uncovered kana span that carries meaning (the question particle か was
    # the bug that motivated this) must get its own tappable word, not a pass.
    coverage_exceptions = set(data.get("coverage_exceptions") or [])

    html = ""
    if os.path.exists(html_path):
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
    else:
        rep.error("E2", f"index.html not found at {html_path} (cannot parse LINE_TR/LINE_EXPLAIN)")

    manifest = []
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        rep.error("E5", f"tts_manifest.json not found at {manifest_path}")

    sections = data.get("sections", [])
    lines = (data.get("apple_lyrics") or {}).get("lines", [])

    # Pre-build the matching vocab.
    global_vocab = sorted_vocab([w for s in sections for w in s.get("words", [])])
    sec_vocab = {id(s): sorted_vocab(s.get("words", [])) for s in sections}

    line_tr = parse_line_map(html, "LINE_TR", value_key="en") if html else {}
    line_explain = parse_line_map(html, "LINE_EXPLAIN") if html else {}
    if html and line_tr is None:
        rep.error("E2", "could not locate const LINE_TR = {...} in index.html")
        line_tr = {}
    if html and line_explain is None:
        rep.error("E3", "could not locate const LINE_EXPLAIN = {...} in index.html")
        line_explain = {}

    folder, assets = resolve_song_folder(song_dir, data)
    if not assets or not os.path.isdir(assets):
        rep.error("E4", f"shared assets dir not found (resolved folder={folder!r}, path={assets!r})")
        assets = None

    # -- E1 line coverage + W1 rom-vs-kana ----------------------------------
    for idx, ln in enumerate(lines):
        if not is_lyric_line(ln):
            continue
        text = ln["text"]
        sec = match_line_to_section(text, sections)
        primary = sec_vocab.get(id(sec), []) if sec else []
        covered = coverage_walk(text, primary, global_vocab)
        # Group uncovered into contiguous non-ws spans.
        spans = []
        i = 0
        n = len(text)
        while i < n:
            if not covered[i] and not WS_RE.match(text[i]):
                j = i
                while j < n and not covered[j] and not WS_RE.match(text[j]):
                    j += 1
                spans.append((i, j))
                i = j
            else:
                i += 1
        line_kr = line_kana_rom(ln)
        for (a, b) in spans:
            span_txt = text[a:b]
            extra = f" (line kana_timings rom: {line_kr})" if line_kr else \
                    " (no kana_timings rom available)"
            if span_txt in coverage_exceptions:
                # Explicitly whitelisted in data.json — a syllable with no
                # meaning worth a card. Stays a warning so it's still visible.
                rep.warn("E1", f"line {idx} {text!r}: span {span_txt!r} "
                         f"uncovered but whitelisted (coverage_exceptions)")
            elif KANA_OR_LATIN_RE.match(span_txt) and ln.get("kana_timings"):
                # Romaji gap-fills from kana_timings so the LINE looks complete,
                # but this span is not a tappable study word — the learner sees
                # it (and hears its romaji) yet can't study or hear it in
                # isolation. That silent gap hid the question particle か in
                # 言えるか. It is an ERROR now: give it a study word, or, only if
                # it is genuinely meaningless, add it to data.json
                # "coverage_exceptions".
                rep.error("E1", f"line {idx} {text!r}: kana span {span_txt!r} "
                          f"has no study word (romaji gap-fills but it is "
                          f"untappable/unstudyable){extra}")
            else:
                rep.error("E1", f"line {idx} {text!r}: uncovered span "
                          f"{span_txt!r}{extra}")

        # W1: concatenated matched-word rom for the WHOLE line vs the line's
        # kana_timings reading. Line-level (not per-span) because kana_timings
        # cannot be soundly aligned to individual text chars (see line_kana_rom).
        if line_kr:
            word_rom_parts = []
            i = 0
            while i < n:
                if WS_RE.match(text[i]):
                    i += 1
                    continue
                matched = None
                consumed = 0
                for wl in (primary, global_vocab):
                    for w in wl:
                        c = ws_tolerant_match(text, i, [w])
                        if c:
                            matched, consumed = w, c
                            break
                    if matched:
                        break
                if matched is not None:
                    word_rom_parts.append(matched.get("rom", ""))
                    i += consumed
                else:
                    i += 1
            word_rom = "".join(word_rom_parts)
            nw_ = normalize_rom(word_rom)
            nk_ = normalize_rom(line_kr)
            # Lenient: a mismatch only if neither is a substring of the other
            # (one being shorter happens on an E1 gap, already reported there).
            if nw_ and nk_ and nw_ != nk_ and nw_ not in nk_ and nk_ not in nw_:
                rep.warn("W1", f"line {idx} {text!r}: word roms {word_rom!r} "
                                f"vs kana reading {line_kr!r}")

    # -- E2 LINE_TR coverage / E3 LINE_EXPLAIN coverage ----------------------
    seen_keys = set()
    for idx, ln in enumerate(lines):
        if not is_lyric_line(ln):
            continue
        key = line_tr_key(ln["text"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        tr = (line_tr or {}).get(key)
        if not tr or not tr.strip():
            rep.error("E2", f"line {idx} {ln['text']!r} (key {key!r}): no LINE_TR entry with non-empty en")
        # E3 (round 11, earned Explainer): absence is BY DESIGN — lines whose
        # displayed translation already says everything ship no entry and the
        # pill doesn't render. The error is an ORPHAN key (entry matching no
        # line), checked after this loop — not a missing entry.

    for k in (line_explain or {}):
        if k not in seen_keys:
            rep.error("E3", f"LINE_EXPLAIN key {k!r} matches no lyric line (orphan)")

    # -- E4 audio existence --------------------------------------------------
    if assets:
        jp_dir = os.path.join(assets, "audio", "jp")
        en_dir = os.path.join(assets, "audio", "en")
        for sec in sections:
            sid = sec.get("id", "?")
            for w in sec.get("words", []):
                rom = w.get("rom")
                if not rom:
                    continue
                # audio slug = uid||rom — the runtime (_audioUrlFor / prewarm) and
                # content_to_data both honor the optional `uid` override
                # (SONG-CONTRACT: "Overrides rom as audio slug"); a rom-only
                # derivation false-flags uid-split words (silhouette outro を).
                uid = rom_uid(w.get("uid") or rom)
                # JP clip: v095+ ships+serves mono-80k mp3 (was 44.1k stereo WAV).
                # Accept either so this also validates legacy .wav-only builds
                # (e.g. silhouette), but the served/expected format is .mp3.
                jp_mp3 = os.path.join(jp_dir, f"word_{sid}_{uid}.mp3")
                jp_wav = os.path.join(jp_dir, f"word_{sid}_{uid}.wav")
                en_f = os.path.join(en_dir, f"word_{sid}_{uid}_en.mp3")
                if not (os.path.exists(jp_mp3) or os.path.exists(jp_wav)):
                    rep.error("E4", f"missing JP audio for {sid}/{w.get('jp')}: "
                                    f"audio/jp/word_{sid}_{uid}.mp3")
                if not os.path.exists(en_f):
                    rep.error("E4", f"missing EN audio for {sid}/{w.get('jp')}: "
                                    f"audio/en/word_{sid}_{uid}_en.mp3")
                if w.get("context"):
                    ctx_f = os.path.join(en_dir, f"word_{sid}_{uid}_ctx.mp3")
                    if not os.path.exists(ctx_f):
                        rep.error("E4", f"missing CTX audio for {sid}/{w.get('jp')}: "
                                        f"audio/en/word_{sid}_{uid}_ctx.mp3")

    # -- E5 manifest integrity ----------------------------------------------
    manifest_keys = set()  # entry[1] (the spoken-text key)
    if manifest:
        for mi, entry in enumerate(manifest):
            if not isinstance(entry, list) or len(entry) < 4:
                rep.error("E5", f"manifest entry #{mi} malformed: {entry!r}")
                continue
            fname = entry[3]
            manifest_keys.add(entry[1])
            if assets:
                fpath = os.path.join(assets, fname)
                if not os.path.exists(fpath):
                    rep.error("E5", f"manifest entry #{mi} references missing file: {fname}")
        # every section speak_en must be a manifest key (entry[1])
        for sec in sections:
            sp = sec.get("speak_en")
            if sp and sp not in manifest_keys:
                rep.error("E5", f"section {sec.get('id')} speak_en not present as a manifest key: "
                                f"{sp[:50]!r}")
        # every LINE_EXPLAIN value must be a manifest key, byte-identical
        for k, v in (line_explain or {}).items():
            if v and v not in manifest_keys:
                rep.error("E5", f"LINE_EXPLAIN[{k!r}] value not present as a manifest key (byte-identical): "
                                f"{v[:50]!r}")

    # -- E6 schema -----------------------------------------------------------
    for k in REQUIRED_TOP_KEYS:
        if k not in data or data.get(k) in (None, "", [], {}):
            rep.error("E6", f"required top-level data.json key missing/empty: {k!r}")
    for sec in sections:
        sid = sec.get("id", "?")
        for wi, w in enumerate(sec.get("words", [])):
            for fld in REQUIRED_WORD_FIELDS:
                if not (w.get(fld) or "").strip() if isinstance(w.get(fld), str) else not w.get(fld):
                    rep.error("E6", f"section {sid} word #{wi} ({w.get('jp','?')}): "
                                    f"required field {fld!r} missing/empty")

    # -- E7 bare drill gloss (spoken in isolation in word-by-word) -----------
    for sec in sections:
        sid = sec.get("id", "?")
        for w in sec.get("words", []):
            g = (w.get("gloss") or "").strip()
            if g and is_bare_gloss(g):
                rep.error("E7", f"section {sid} word {w.get('jp','?')!r}: drill gloss {g!r} "
                                f"is bare function-word(s)/pronoun(s) — the word-by-word voice "
                                f"speaks it in isolation and it sounds clipped. Use a short natural "
                                f"phrase (e.g. の → 'of, or belonging to').")

    # -- E8 particle spoken-form (は/へ/を must author jp_speak わ/え/お) ------
    # Kokoro (and dictionary sources) render the KANJI reading of these three
    # particles (ha/he/wo), not the spoken form. The established fix is authored:
    # jp_speak carries the spoken syllable. A raw jp_speak here shipped odoriko's
    # を as unintelligible noise — gate it so it can't recur.
    PARTICLE_SPOKEN = {"は": "わ", "へ": "え", "を": "お"}
    for sec in sections:
        sid = sec.get("id", "?")
        for w in sec.get("words", []):
            jp = (w.get("jp") or "").strip()
            if w.get("particle") and jp in PARTICLE_SPOKEN:
                want = PARTICLE_SPOKEN[jp]
                if (w.get("jp_speak") or "").strip() != want:
                    rep.error("E8", f"section {sid} particle {jp!r}: jp_speak is "
                                    f"{w.get('jp_speak')!r} — must be the spoken form {want!r} "
                                    f"(TTS/dict clips mangle the raw particle; は→わ, へ→え, を→お).")

    # -- E9 lone-particle audio provenance (never Kokoro) --------------------
    # Every word whose spoken form is a lone particle must have its served JP
    # clip recorded in builds/<folder>.clip_provenance.json with source != kokoro.
    # This is the rule QA could not enforce: a Kokoro particle can still be
    # transcribable ("ガイド" for が passed whisper) yet sounds wrong — provenance,
    # not read-back, is the gate.
    repo_root = song_dir
    while repo_root != os.path.dirname(repo_root):
        if os.path.isdir(os.path.join(repo_root, "songs", "_assets")):
            break
        repo_root = os.path.dirname(repo_root)
    prov = None
    prov_path = None
    if folder:
        prov_path = os.path.join(repo_root, "tools", "songcraft", "builds",
                                 f"{folder}.clip_provenance.json")
        if os.path.exists(prov_path):
            try:
                with open(prov_path, encoding="utf-8") as f:
                    prov = json.load(f)
            except Exception as e:
                rep.error("E9", f"could not parse provenance manifest {prov_path}: {e}")
    # Collect the lone-particle words and their expected served clip relpaths.
    particle_words = []
    for sec in sections:
        sid = sec.get("id", "?")
        for w in sec.get("words", []):
            spoken = (w.get("jp_speak") or w.get("jp") or "")
            mora = _STRIP_KANA.sub("", spoken)
            if len(mora) == 1 and mora in LONE_PARTICLES:
                uid = rom_uid(w.get("uid") or w.get("rom"))
                particle_words.append((sid, w.get("jp", "?"), mora, uid))
    if particle_words:
        if prov is None:
            where = prov_path or f"tools/songcraft/builds/{folder}.clip_provenance.json"
            rep.error("E9", f"{len(particle_words)} lone-particle word(s) but NO provenance "
                            f"manifest at {where}. Every lone particle must be voiced by a "
                            f"real voice (curated dict library or Qwen carrier-cut), never "
                            f"Kokoro. Regenerate the particle clips and write the manifest "
                            f"(gen_audio.py records it; or run the provenance regeneration).")
        else:
            for sid, jp, mora, uid in particle_words:
                # match the served ext (mp3 preferred, else the legacy wav) — the
                # manifest is keyed by the audio-relative path used at render time.
                rels = [f"jp/word_{sid}_{uid}.mp3", f"jp/word_{sid}_{uid}.wav"]
                entry = next((prov[r] for r in rels if r in prov), None)
                if entry is None:
                    rep.error("E9", f"section {sid} particle {jp!r} ({mora}): served clip "
                                    f"jp/word_{sid}_{uid}.(mp3|wav) has NO provenance entry — "
                                    f"regenerate it from a real voice and record its source.")
                elif entry.get("source") == "kokoro":
                    rep.error("E9", f"section {sid} particle {jp!r} ({mora}): provenance source "
                                    f"is 'kokoro' — Kokoro mangles isolated particles. Replace "
                                    f"with a curated dict clip or a Qwen carrier-cut.")

        # -- E17 lone-particle audio is HUMAN, never a TTS cut ----------------
        # E9 bans kokoro; E17 goes further after the 2026-07-07 audit: both bad
        # particles shipped that day were Qwen carrier-cuts that PASSED QA (ema
        # の carried the preceding word — transcribed もの; silhouette2 よ carried
        # a な residue). A carrier-cut can keep neighbor-phoneme residue that
        # read-back can't reliably catch, so lone particles must come from a
        # human recording (source 'curated…' or 'nhk…'). A deliberate exception
        # requires an explicit waiver on the provenance entry:
        # {"waiver": "<why this take was approved by ear>"}.
        if prov is not None:
            for sid, jp, mora, uid in particle_words:
                rels = [f"jp/word_{sid}_{uid}.mp3", f"jp/word_{sid}_{uid}.wav"]
                entry = next((prov[r] for r in rels if r in prov), None)
                if entry is None:
                    continue  # E9 already errors on the missing entry
                src = entry.get("source") or ""
                if not (src.startswith("curated") or src.startswith("nhk")
                        or entry.get("waiver")):
                    rep.error("E17", f"section {sid} particle {jp!r} ({mora}): provenance "
                                     f"source {src!r} is not a human recording. Install a "
                                     f"curated/NHK clip (picker or install_word.py), or add "
                                     f"a provenance \"waiver\" recording who approved this "
                                     f"take by ear and why.")

    # -- E10 token/text alignment (romaji reveal timing) ---------------------
    # The page times each romaji part by overlapping its line.text char range
    # against a cursor walked over the word tokens. The token stream DROPS
    # display whitespace between JP-adjacent words, so the walk must skip
    # untokenized spaces in line.text — a desync here made every romaji word
    # after a space wear the NEXT word's time (the odoriko "pronunciation is
    # not timing correctly" bug). Replicate the corrected walk: every timed
    # word must be findable at its walked position in line.text.
    for ln in (data.get("apple_lyrics", {}).get("lines") or []):
        text = ln.get("text") or ""
        words = ln.get("words") or []
        if not text.strip() or not words:
            continue
        cursor = 0
        for w in words:
            wt = (w.get("text") or "")
            while cursor < len(text) and WS_RE.match(text[cursor]):
                cursor += 1
            if text[cursor:cursor + len(wt)] != wt:
                rep.error("E10", f"line {text[:24]!r}: timed word {wt!r} not at walked "
                                 f"text position {cursor} — token/text desync would shift "
                                 f"the romaji (pronunciation) reveal onto the wrong word.")
                break
            cursor += len(wt)

    # -- E12 short-word dictionary priority ----------------------------------
    # Task A rule: a short word (まえ, いい, どっか — any <=2-mora study word) and
    # particles must PRIORITIZE the human-voice dictionaries, never silently
    # default to Kokoro. Every ja study-word clip whose spoken kana is <=2 morae
    # must therefore carry provenance source in {curated, qwen, kokoro_dictmiss}:
    # 'curated'/'qwen' = a real human/carrier voice; 'kokoro_dictmiss' = the
    # dictionary WAS tried, missed, and Kokoro is the recorded fallback. Plain
    # 'kokoro' (or no provenance entry) means the dictionary was never attempted
    # for a short word — an ERROR. (Reuses prov/prov_path/folder loaded for E9.)
    E12_OK = {"curated", "qwen", "aivis", "google", "kokoro_dictmiss"}
    short_words = []
    for sec in sections:
        sid = sec.get("id", "?")
        for w in sec.get("words", []):
            spoken = (w.get("jp_speak") or w.get("jp") or "")
            if spoken and mora_count(spoken) <= 2:
                uid = rom_uid(w.get("uid") or w.get("rom"))
                short_words.append((sid, w.get("jp", "?"), spoken, uid))
    if short_words:
        if prov is None:
            where = prov_path or f"tools/songcraft/builds/{folder}.clip_provenance.json"
            rep.error("E12", f"{len(short_words)} short (<=2 mora) word(s) but NO provenance "
                             f"manifest at {where} — cannot prove dictionary-priority routing. "
                             f"Regenerate with gen_audio.py (records source per clip).")
        else:
            for sid, jp, spoken, uid in short_words:
                rels = [f"jp/word_{sid}_{uid}.mp3", f"jp/word_{sid}_{uid}.wav"]
                entry = next((prov[r] for r in rels if r in prov), None)
                if entry is None:
                    rep.error("E12", f"section {sid} short word {jp!r} ({spoken}): served clip "
                                     f"jp/word_{sid}_{uid}.(mp3|wav) has NO provenance entry — a "
                                     f"<=2-mora word must try the human dictionary first "
                                     f"(source curated/qwen/kokoro_dictmiss).")
                elif entry.get("source") not in E12_OK:
                    rep.error("E12", f"section {sid} short word {jp!r} ({spoken}): provenance source "
                                     f"is {entry.get('source')!r} — a <=2-mora word must PRIORITIZE "
                                     f"the human dictionary; source must be curated/qwen/"
                                     f"kokoro_dictmiss, never plain 'kokoro'.")

    # -- E15 pronunciation lexicon (listed words never TTS) -------------------
    # tools/songcraft/pronunciation_lexicon.json is the site-wide MEMORY the
    # whisper read-back feeds: a word caught mispronounced once is listed and
    # must never regress to TTS on any song. Each entry's `allow` names the
    # permitted provenance sources; when omitted the default is anything EXCEPT
    # kokoro/kokoro_dictmiss — deliberately stricter than E12_OK (the lexicon
    # overrides the dict-miss allowance). Lone particles are excluded from
    # Pass A: E9 already gates them (no double-fire). Absent lexicon = pass.
    # (Reuses prov/prov_path/folder/repo_root loaded for E9.)
    lexicon = load_lexicon(repo_root)
    if lexicon is None:
        rep.error("E15", "tools/songcraft/pronunciation_lexicon.json exists but "
                         "cannot be read/parsed — the never-TTS memory is "
                         "unenforceable. Fix the file; an unreadable lexicon "
                         "must never pass as an empty one.")
        lexicon = {}
    if lexicon:
        def lex_allowed(src, lex):
            allow = lex.get("allow")
            return src not in ("kokoro", "kokoro_dictmiss") if allow is None else src in allow

        # Pass A: section words whose folded spoken form is lexicon-listed.
        listed_rels = {}   # prov rel -> lexicon key (for the sha8 drift pass)
        for sec in sections:
            sid = sec.get("id", "?")
            for w in sec.get("words", []):
                spoken = (w.get("jp_speak") or w.get("jp") or "")
                mora = _STRIP_KANA.sub("", spoken)
                if len(mora) == 1 and mora in LONE_PARTICLES:
                    continue
                lex = lexicon.get(fold_kana(spoken))
                if lex is None:
                    continue
                uid = rom_uid(w.get("uid") or w.get("rom"))
                rels = [f"jp/word_{sid}_{uid}.mp3", f"jp/word_{sid}_{uid}.wav"]
                rel = next((r for r in rels if r in (prov or {})), None)
                if rel is None:
                    rep.error("E15", f"section {sid} word {w.get('jp','?')!r} ({spoken}) is in the "
                                     f"pronunciation lexicon but its served clip "
                                     f"jp/word_{sid}_{uid}.(mp3|wav) has NO provenance entry — "
                                     f"a listed word must be provably non-TTS.")
                    continue
                listed_rels[rel] = fold_kana(spoken)
                src = prov[rel].get("source")
                if not lex_allowed(src, lex):
                    allow = lex.get("allow") or ["anything except kokoro/kokoro_dictmiss"]
                    rep.error("E15", f"section {sid} word {w.get('jp','?')!r} ({spoken}): provenance "
                                     f"source {src!r} not in the lexicon allow set {allow} — the "
                                     f"lexicon overrides the dict-miss allowance; a listed word "
                                     f"never ships from TTS. ({lex.get('reason', '')})")
        # Pass B: sweep the whole manifest by its kana/spoken fields — catches
        # clips no section word points at (e.g. citation clips jp/podcast_*).
        for rel, entry in (prov or {}).items():
            if rel in listed_rels:
                continue
            spoken = entry.get("kana") or entry.get("spoken") or ""
            mora = _STRIP_KANA.sub("", spoken)
            if len(mora) == 1 and mora in LONE_PARTICLES:
                continue
            lex = lexicon.get(fold_kana(spoken))
            src = entry.get("source")
            if lex is not None and src in ("kokoro", "kokoro_dictmiss") \
                    and not lex_allowed(src, lex):
                rep.error("E15", f"provenance entry {rel} (kana {spoken!r}) is lexicon-listed but "
                                 f"its source is {src!r} — a listed word never ships from TTS.")
        # sha8 drift: the manifest must describe the bytes actually served
        # (a clip swap that skips provenance would otherwise pass unverified).
        if assets:
            for rel in listed_rels:
                want = prov[rel].get("sha8")
                fpath = os.path.join(assets, "audio", rel)
                if want and os.path.exists(fpath):
                    with open(fpath, "rb") as f:
                        got = hashlib.sha256(f.read()).hexdigest()[:8]
                    if got != want:
                        rep.warn("E15", f"lexicon-listed clip {rel}: file sha8 {got} != provenance "
                                        f"sha8 {want} — bytes drifted from the manifest (clip "
                                        f"swapped without updating provenance?)")

    # -- E13 timing sanity ---------------------------------------------------
    # apple_lyrics.song.music_start_ms is the countdown/onset anchor (whisper_sync
    # measures it). Checks: (1) it is present for a pipeline song — the TEMPLATE
    # (inochi) may predate it, so an ENTIRELY absent field is a WARNING, not an
    # error; (2) the first sung line begins at or after it (a count-in must not
    # outlast the first lyric); (3) word begin_ms is monotonic non-decreasing
    # within each line (out-of-order onsets shift the karaoke reveal).
    apple = data.get("apple_lyrics") or {}
    song_meta = apple.get("song") or {}
    ms = song_meta.get("music_start_ms")
    if ms is None:
        rep.warn("E13", "apple_lyrics.song.music_start_ms absent — no countdown/onset "
                        "anchor (pre-pipeline template; count-in starts at t=0).")
    else:
        try:
            ms_i = int(ms)
        except (TypeError, ValueError):
            ms_i = None
            rep.error("E13", f"apple_lyrics.song.music_start_ms is not an integer: {ms!r}")
        if ms_i is not None:
            first = next((ln for ln in lines if is_lyric_line(ln)), None)
            fb = first.get("begin_ms") if first else None
            if isinstance(fb, int) and fb < ms_i:
                rep.error("E13", f"first sung line begin_ms={fb} is before "
                                 f"music_start_ms={ms_i} — the count-in would outlast the "
                                 f"first lyric.")
    for idx, ln in enumerate(lines):
        prev = None
        for w in (ln.get("words") or []):
            b = w.get("begin_ms")
            if not isinstance(b, int):
                continue
            if prev is not None and b < prev:
                rep.error("E13", f"line {idx} {(ln.get('text') or '')[:24]!r}: word "
                                 f"begin_ms={b} < previous {prev} — word onsets must be "
                                 f"monotonic non-decreasing within a line.")
                break
            prev = b

    # -- E16 timing plausibility ----------------------------------------------
    # The CTC-smear corruption (whisper_sync forced alignment parking repeated
    # hook/scat lines over an instrumental break) shipped 9s word tokens, a 24s
    # lyric line, and wildly inconsistent durations for identical repeated text
    # to 3 of 5 live songs. Gate the shipped data.json timings directly:
    #   HARD FAIL: any word/kana token > 6000ms; identical-text line-duration
    #   max/min ratio > 3; line mora-rate outside [0.45, 16] morae/sec
    #   (approx morae: kana=1, kanji=2; lines with no JP chars skipped);
    #   any single line duration > 20s.
    #   WARNING (non-failing): token > 4000ms; duration ratio > 2.5.
    # Calibrated so inochi's final held note (スキキライスキ 6.65s line with a
    # 4.01s キ token; repeat ratio 2.54) passes with warnings only.
    E16_KANA = re.compile(r"[぀-ゟ゠-ヿ]")
    E16_JP = re.compile(r"[぀-ヿ㐀-鿿]")

    def e16_morae(text):
        m = 0
        for ch in text:
            if E16_KANA.match(ch):
                m += 1
            elif E16_JP.match(ch):
                m += 2
        return m

    e16_durs = {}  # normalized text -> list[(line idx, duration_ms)]
    for idx, ln in enumerate(lines):
        if not is_lyric_line(ln):
            continue
        text = ln["text"]
        b, e = ln.get("begin_ms"), ln.get("end_ms")
        if not isinstance(b, (int, float)) or not isinstance(e, (int, float)):
            continue
        dur = e - b
        if dur > 20000:
            rep.error("E16", f"line {idx} {text[:30]!r}: duration {dur:.0f}ms > 20s "
                             f"(begin {b:.0f} → end {e:.0f}) — CTC-smear-scale "
                             f"stretch, no sung line lasts this long.")
        for kind, seq in (("word", ln.get("words") or []),
                          ("kana", ln.get("kana_timings") or [])):
            for t in seq:
                tb, te = t.get("begin_ms"), t.get("end_ms")
                if not isinstance(tb, (int, float)) or not isinstance(te, (int, float)):
                    continue
                td = te - tb
                label = t.get("text") or t.get("rom") or "?"
                if td > 6000:
                    rep.error("E16", f"line {idx} {text[:24]!r}: {kind} token "
                                     f"{label!r} spans {td:.0f}ms > 6000ms.")
                elif td > 4000:
                    rep.warn("E16", f"line {idx} {text[:24]!r}: {kind} token "
                                    f"{label!r} spans {td:.0f}ms > 4000ms "
                                    f"(legit only for a long held note).")
        if E16_JP.search(text) and dur > 0:
            rate = e16_morae(text) / (dur / 1000.0)
            if not (0.45 <= rate <= 16):
                rep.error("E16", f"line {idx} {text[:30]!r}: mora-rate {rate:.2f}/s "
                                 f"over {dur:.0f}ms — outside plausible singing "
                                 f"range [0.45, 16] morae/sec.")
        key = line_tr_key(text)
        if key:
            e16_durs.setdefault(key, []).append((idx, dur))
    for key, entries in e16_durs.items():
        if len(entries) < 2:
            continue
        durs = [max(d, 1) for _, d in entries]
        ratio = max(durs) / min(durs)
        if ratio > 2.5:
            detail = ", ".join(f"line {i}={d:.0f}ms" for i, d in entries)
            if ratio > 3:
                rep.error("E16", f"identical-text lines {key[:20]!r}: duration "
                                 f"ratio {ratio:.2f} > 3 ({detail}) — the same "
                                 f"sung text cannot vary this much.")
            else:
                rep.warn("E16", f"identical-text lines {key[:20]!r}: duration "
                                f"ratio {ratio:.2f} > 2.5 ({detail}).")

    # -- E18 audio cache-key freshness (AUDIO_V + DRILL_MAP ?v=) --------------
    # /songs/*.mp3 is edge-cached immutable for a year, so the page's cache keys
    # ARE the deploy mechanism. Two real failures motivated this gate
    # (2026-07-07 audit): ema shipped an AUDIO_V computed BEFORE its last clip
    # renders (page key didn't cover the bytes it served), and live headlong's
    # DRILL_MAP carried year-old ?v= keys for concats whose bytes had changed
    # (cache-cold visitors got new bytes against an old baked timing map).
    # Gate: the page's AUDIO_V must equal a fresh walk of _assets audio, and
    # every DRILL_MAP audio ?v= must equal sha256[:8] of the current file bytes.
    if folder and html:
        m_av = re.search(r"const AUDIO_V\s*=\s*'([0-9a-f]*)'", html)
        if m_av is None:
            rep.error("E18", "index.html has no `const AUDIO_V = '...'` — the page "
                             "cannot mint fresh clip URLs after audio changes.")
        else:
            page_av = m_av.group(1)
            try:
                sc_dir = os.path.join(repo_root, "tools", "songcraft")
                if sc_dir not in sys.path:
                    sys.path.insert(0, sc_dir)
                import assemble_page as _ap
                fresh_av = _ap.audio_version(folder)
                fresh_av = fresh_av[0] if isinstance(fresh_av, tuple) else fresh_av
            except Exception as e:
                fresh_av = None
                # Fail-closed (gate-completion 1.2): a freshness gate that
                # cannot compute freshness has not passed — warn-level here
                # let "cannot verify" read as green in scripted runs.
                rep.error("E18", f"could not compute a fresh AUDIO_V "
                                 f"(assemble_page.audio_version): {e} — "
                                 f"cannot verify == not verified.")
            if fresh_av is not None:
                if not page_av:
                    rep.error("E18", f"page AUDIO_V is EMPTY but songs/_assets/{folder}/audio "
                                     f"has clips (fresh walk = {fresh_av}) — no cache busting "
                                     f"reaches devices. Re-assemble.")
                elif page_av != fresh_av:
                    rep.error("E18", f"page AUDIO_V {page_av} != fresh asset walk {fresh_av} — "
                                     f"clip bytes changed after the page was assembled. "
                                     f"Re-assemble (rebuild) so devices fetch the new bytes.")
        drill_dir = os.path.join(repo_root, "songs", "_assets", folder, "audio", "drill")
        for dm in re.finditer(r"audio/drill/(line_[0-9a-f]{8}\.mp3)(?:\?v=([0-9a-f]{8}))?",
                              html):
            fname, ver = dm.group(1), dm.group(2)
            fpath = os.path.join(drill_dir, fname)
            if not os.path.exists(fpath):
                rep.error("E18", f"DRILL_MAP references {fname} but no such file under "
                                 f"songs/_assets/{folder}/audio/drill/.")
                continue
            with open(fpath, "rb") as f:
                got = hashlib.sha256(f.read()).hexdigest()[:8]
            if ver is None:
                rep.error("E18", f"DRILL_MAP entry {fname} has NO ?v= key — the concat is "
                                 f"served immutable and can never update. Re-assemble.")
            elif ver != got:
                rep.error("E18", f"DRILL_MAP {fname} carries ?v={ver} but current bytes are "
                                 f"{got} — stale key; drill audio and its baked timing map "
                                 f"can desync. Rebuild the drill concat + re-assemble.")

    # -- E20 podcast URL resolves to a real asset -----------------------------
    # The live inochi podcast died silently (2026-07-10 audit): the page baked
    # a relative PODCAST_URL whose mp3 was never in _assets (the v098 lineage
    # streamed it from the retired audio.manaoke.app), so the player fetched
    # the SPA fallback HTML and played nothing. No gate looked. This one does:
    # a relative PODCAST_URL must resolve to an existing non-empty file under
    # songs/_assets/<folder>/. Absolute URLs can't be checked offline (and a
    # bare fetch of live audio is forbidden — cache poison), so they only warn.
    if folder and html:
        m_pu = re.search(r"const PODCAST_URL\s*=\s*'([^']*)'", html)
        if m_pu is None:
            pass    # no podcast player on the page (product simplify,
                    # 2026-07-12) — nothing to gate; E20 applies only when a
                    # page still carries one (legacy dirs).
        else:
            pod_url = m_pu.group(1)
            if not pod_url:
                rep.warn("E20", "PODCAST_URL is empty — podcast player disabled.")
            elif re.match(r"https?://", pod_url):
                rep.warn("E20", f"PODCAST_URL is absolute ({pod_url}) — existence "
                                f"not verifiable offline; pipeline songs should "
                                f"ship it from _assets.")
            else:
                ppath = os.path.join(repo_root, "songs", "_assets", folder,
                                     pod_url.lstrip("/"))
                if not os.path.exists(ppath):
                    rep.error("E20", f"PODCAST_URL '{pod_url}' does not resolve to a "
                                     f"file under songs/_assets/{folder}/ — the player "
                                     f"will fetch the SPA fallback HTML and play "
                                     f"nothing (the dead-inochi-podcast class).")
                elif os.path.getsize(ppath) == 0:
                    rep.error("E20", f"PODCAST_URL '{pod_url}' resolves to an EMPTY "
                                     f"file ({ppath}).")

    # -- E21 kana-line integrity (readings reconcile with cards + text) -------
    # The 2026-07-10 audit class: the kana/romaji line shipped speech-engine
    # guesses (きゅーみのびにわははさんと for 休みの日には母さんと) while the
    # study cards on the SAME page carried the correct readings — and W1, the
    # advisory that saw it, was a warning, so it shipped. Two binding checks:
    #   (a) RENDAKU/spelling: every づ/ぢ the lyric TEXT contains must appear
    #       in that line's kana_timings (g2p output is phonemic and can never
    #       emit づ — its absence proves a guessed reading).
    #   (b) CARD AGREEMENT: a study card that carries a kana reading (jp pure
    #       kana, or jp_speak differing from jp in pure kana) is the arbiter
    #       of sounds; where its surface sits in a line, the line's kana must
    #       contain that reading (compared sound-folded: kata->hira, づ/ぢ->
    #       ず/じ, ー expanded to the preceding vowel).
    # Waivers: builds/<key>.reading_waivers.json  [{"jp": ..., "note": ...}]
    # for genuinely ambiguous sung readings — route those to the ear queue
    # (backlog 54a0233b) instead of silencing the gate.
    _KATA2HIRA = {chr(k): chr(k - 0x60) for k in range(ord("ァ"), ord("ヶ") + 1)}
    _VOWEL = {}
    for _v, _ks in (("あ", "あかがさざただなはばぱまやらわゃぁ"),
                    ("い", "いきぎしじちぢにひびぴみりゐぃ"),
                    ("う", "うくぐすずつづぬふぶぷむゆるゔゅぅ"),
                    ("え", "えけげせぜてでねへべぺめれゑぇ"),
                    ("お", "おこごそぞとどのほぼぽもよろをょぉ")):
        for _k in _ks:
            _VOWEL[_k] = _v

    def _sound_fold(s):
        h = "".join(_KATA2HIRA.get(c, c) for c in str(s or ""))
        out = []
        for c in h:
            if c == "ー" and out:
                # ー expands to the previous vowel (かー == かあ). No おう/えい
                # equivalence folding: it is position-blind and mangles across
                # word boundaries (で+いちばん -> でえちばん, a false positive
                # caught 2026-07-11). Regenerated lines are orthographic, so
                # exact agreement is the expectation; legacy phonemic lines
                # SHOULD fire — they are the defect set.
                out.append(_VOWEL.get(out[-1][-1], ""))
            else:
                out.append(c)
        return "".join(out).replace("づ", "ず").replace("ぢ", "じ")

    _PURE_KANA_RE = re.compile(r"^[぀-ゟ゠-ヿー]+$")
    _CJK_RE = re.compile(r"[㐀-鿿]")
    _wv_path = os.path.join(repo_root, "tools", "songcraft", "builds",
                            f"{data.get('slug', '')}.reading_waivers.json")
    _waived = set()
    if os.path.exists(_wv_path):
        try:
            _waived = {e.get("jp", "") for e in json.load(open(_wv_path))}
        except Exception:
            rep.error("E21", f"{_wv_path} exists but cannot be parsed — an "
                             f"unreadable waiver list waives nothing.")
    _norm_lk = lambda s: re.sub(r"\s+", "", re.sub(r"\s*\(×\d+\)\s*$", "",
                                                   str(s or ""))).strip()

    def _card_kana_of(w):
        _jp = str(w.get("jp") or "")
        if not _jp or not _CJK_RE.search(_jp) or _PURE_KANA_RE.fullmatch(_jp):
            return None
        _speak = str(w.get("jp_speak") or "")
        if _speak and _speak != _jp and _PURE_KANA_RE.fullmatch(_speak):
            return _speak
        return None

    for idx, ln in enumerate(lines):
        if not is_lyric_line(ln):
            continue
        text = ln.get("text") or ""
        joined = "".join(k.get("kana", "") for k in (ln.get("kana_timings") or []))
        if not joined:
            continue
        for ch in ("づ", "ぢ"):
            if text.count(ch) > joined.count(ch):
                rep.error("E21", f"line {idx} {text!r}: text contains {ch} but the "
                                 f"kana line says {joined!r} — literal kana must "
                                 f"never be re-derived (phonemic G2P can't spell "
                                 f"{ch}).")
        fold_line = _sound_fold(joined)
        lk = _norm_lk(text)
        # The page's greedy longest-first walk (only_lines-aware, like the
        # page's collectStudyWords): the card checked at each position is the
        # card a TAP there actually surfaces — a 日 card never fires inside
        # 毎日 because the longer card consumes the span first.
        sec = match_line_to_section(text, sections)
        primary = sec_vocab.get(id(sec), []) if sec else []
        _ok_line = (lambda w: not w.get("only_lines")
                    or any(_norm_lk(x) == lk for x in w["only_lines"]))
        i = 0
        while i < len(text):
            if text[i].isspace():
                i += 1
                continue
            matched, consumed = None, 0
            for wl in (primary, global_vocab):
                for w in wl:
                    if not _ok_line(w):
                        continue
                    c = ws_tolerant_match(text, i, [w])
                    if c:
                        matched, consumed = w, c
                        break
                if matched:
                    break
            if not matched:
                i += 1
                continue
            _kana = _card_kana_of(matched)
            _jp = str(matched.get("jp") or "")
            if _kana and _jp not in _waived \
                    and _sound_fold(_kana) not in fold_line:
                rep.error("E21", f"line {idx} {text!r}: card {_jp!r} reads "
                                 f"{_kana!r} but the kana line says {joined!r} — "
                                 f"the card is the arbiter; fix the line reading, "
                                 f"or waive in {os.path.basename(_wv_path)} and "
                                 f"queue the ear check (54a0233b).")
            i += consumed

    # -- E19 acoustic clip physics (duration + envelope vs reading) ----------
    # The いい/の defect class (backlog 0bd85bd1): short takes that are
    # truncated or carry the next word's onset PASS transcription read-back —
    # a hard-cut いい reads back fine, a これでいい cut passed containment —
    # but they are physically wrong: not enough voiced time for the morae, or
    # energy still high at the very end. sweep_clip_physics.py (chained into
    # the validate runner, parler env) measures every served JP clip against
    # its reading and writes verdicts to builds/<folder>.clip_suspects.json;
    # this gate holds the pipeline to it:
    #   - a study-word clip with no FRESH sidecar entry (missing, or sha8 no
    #     longer matching the served bytes) shipped unchecked -> error
    #   - verdict 'fail' without a physics_waiver on the clip's provenance
    #     entry -> error; with a waiver -> visible warning
    #   - verdict 'suspect' -> warning (the Denmoku ear strip carries it)
    # Line/podcast clips ride the sidecar as warnings only.
    if folder:
        side_path = os.path.join(repo_root, "tools", "songcraft", "builds",
                                 f"{folder}.clip_suspects.json")
        side = None
        try:
            with open(side_path, encoding="utf-8") as f:
                side = json.load(f)
        except FileNotFoundError:
            rep.error("E19", f"no acoustic sweep sidecar at tools/songcraft/builds/"
                             f"{folder}.clip_suspects.json — run sweep_clip_physics.py "
                             f"{folder} (parler env) so every JP clip is physics-checked "
                             f"before it ships.")
        except Exception as e:
            rep.error("E19", f"could not parse {side_path}: {e}")
        if side is not None:
            sclips = side.get("clips", {})
            audio_root = os.path.join(repo_root, "songs", "_assets", folder, "audio")
            for sec in sections:
                sid = sec.get("id", "?")
                for w in sec.get("words", []):
                    uid = rom_uid(w.get("uid") or w.get("rom"))
                    # served ext: mp3 preferred, legacy wav still covered —
                    # same dual-rel idiom as E9/E12/E15
                    rel, fpath = None, None
                    for cand in (f"jp/word_{sid}_{uid}.mp3", f"jp/word_{sid}_{uid}.wav"):
                        p = os.path.join(audio_root, cand)
                        if os.path.exists(p):
                            rel, fpath = cand, p
                            break
                    if rel is None:
                        continue          # E4 owns missing audio
                    ent = sclips.get(rel)
                    if ent is None:
                        rep.error("E19", f"section {sid} word {w.get('jp', '?')!r}: served "
                                         f"clip {rel} has never been physics-swept — run "
                                         f"sweep_clip_physics.py {folder}.")
                        continue
                    with open(fpath, "rb") as f:
                        got = hashlib.sha256(f.read()).hexdigest()[:8]
                    if ent.get("sha8") != got:
                        rep.error("E19", f"section {sid} word {w.get('jp', '?')!r}: {rel} "
                                         f"bytes changed since the last sweep (sidecar "
                                         f"{ent.get('sha8')}, current {got}) — re-run "
                                         f"sweep_clip_physics.py {folder}.")
                        continue
                    why = "; ".join(ent.get("reasons") or [])
                    if ent.get("verdict") == "fail":
                        pent = (prov or {}).get(rel) or {}
                        if pent.get("physics_waiver"):
                            rep.warn("E19", f"section {sid} word {w.get('jp', '?')!r}: "
                                            f"physics fail waived "
                                            f"({pent['physics_waiver']}) — {why}")
                        else:
                            rep.error("E19", f"section {sid} word {w.get('jp', '?')!r} "
                                             f"({rel}): {why}. Fix the clip (Denmoku Words "
                                             f"tab / install_word.py / phrase_cut.py) and "
                                             f"rebuild --why {rel}, or add a physics_waiver "
                                             f"to its provenance entry recording who "
                                             f"approved the take by ear and why.")
                    elif ent.get("verdict") == "suspect":
                        rep.warn("E19", f"section {sid} word {w.get('jp', '?')!r} "
                                        f"({rel}): {why}")
            for srel, ent in sorted(sclips.items()):
                if not srel.startswith("jp/word_") and ent.get("verdict") == "fail":
                    rep.warn("E19", f"{srel} ({ent.get('kana', '?')}): "
                                    f"{'; '.join(ent.get('reasons') or [])}")

    # -- W2 words missing hint/context --------------------------------------
    for sec in sections:
        sid = sec.get("id", "?")
        for w in sec.get("words", []):
            missing = [f for f in ("hint", "context") if not (w.get(f) or "").strip()]
            if missing:
                rep.warn("W2", f"section {sid} word {w.get('jp','?')!r}: missing {', '.join(missing)}")

    # -- W3 duplicate jp across sections with different en -------------------
    jp_to_en = {}  # jp -> list[(sid, en)]
    for sec in sections:
        sid = sec.get("id", "?")
        for w in sec.get("words", []):
            jp = w.get("jp")
            if not jp:
                continue
            jp_to_en.setdefault(jp, []).append((sid, w.get("en", "")))
    for jp, lst in jp_to_en.items():
        ens = {en for _, en in lst}
        if len(lst) > 1 and len(ens) > 1:
            detail = "; ".join(f"{sid}={en!r}" for sid, en in lst)
            rep.warn("W3", f"jp {jp!r} appears in multiple sections with differing en: {detail}")

    return rep, data


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

CHECK_TITLES = {
    "E1": "E1 line-coverage (romaji gaps)",
    "E2": "E2 LINE_TR coverage",
    "E3": "E3 LINE_EXPLAIN coverage",
    "E4": "E4 word audio existence",
    "E5": "E5 manifest integrity",
    "E6": "E6 schema",
    "E7": "E7 bare drill gloss (isolated function-word/pronoun)",
    "E8": "E8 particle spoken-form (は/へ/を jp_speak must be わ/え/お)",
    "E9": "E9 lone-particle audio provenance (never Kokoro)",
    "E10": "E10 token/text alignment (romaji reveal timing)",
    "E12": "E12 short-word dictionary priority (<=2 mora never plain kokoro)",
    "E13": "E13 timing sanity (music_start_ms / monotonic word onsets)",
    "E15": "E15 pronunciation lexicon (listed words never TTS)",
    "E16": "E16 timing plausibility (token/line durations, repeat consistency, mora-rate)",
    "E17": "E17 lone-particle audio is human (curated/nhk, waiver required otherwise)",
    "E18": "E18 audio cache-key freshness (AUDIO_V + DRILL_MAP ?v= match current bytes)",
    "E19": "E19 acoustic clip physics (duration + envelope vs reading)",
    "E20": "E20 podcast URL resolves to a real asset",
    "E21": "E21 kana-line integrity (readings reconcile with cards + text)",
    "W1": "W1 rom-vs-kana mismatch",
    "W2": "W2 words missing hint/context",
    "W3": "W3 duplicate jp / differing en",
}


def print_report(rep, song_dir):
    print(f"Manaoke song validation — {song_dir}")
    print("=" * 64)

    print("\nERRORS")
    print("-" * 64)
    any_err = False
    for cid in ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10", "E12", "E13", "E15", "E16", "E17", "E18", "E19", "E20", "E21"]:
        msgs = rep.errors.get(cid, [])
        title = CHECK_TITLES[cid]
        if msgs:
            any_err = True
            print(f"\n  [{title}] — {len(msgs)} error(s)")
            for m in msgs:
                print(f"    ✗ {m}")
        else:
            print(f"  [{title}] — ok")
    if not any_err:
        print("\n  (no errors)")

    print("\nWARNINGS")
    print("-" * 64)
    any_warn = False
    # E1 can also emit warnings (kana-alignable spans that the runtime
    # gap-filler self-heals) — show them here alongside the W checks. E15's
    # sha8-drift finding is also a warning, as are E16's borderline timings.
    for cid in ["E1", "E13", "E15", "E16", "E19", "E20", "W1", "W2", "W3"]:
        msgs = rep.warns.get(cid, [])
        title = CHECK_TITLES[cid]
        if cid in ("E1", "E13", "E15", "E16", "E19", "E20") and not msgs:
            continue
        if msgs:
            any_warn = True
            print(f"\n  [{title}] — {len(msgs)} warning(s)")
            for m in msgs:
                print(f"    ! {m}")
        else:
            print(f"  [{title}] — ok")
    if not any_warn:
        print("\n  (no warnings)")

    print("\n" + "=" * 64)
    ne, nw = rep.n_errors(), rep.n_warns()
    counts = ", ".join(
        f"{cid}={len(rep.errors.get(cid, [])) if cid.startswith('E') else len(rep.warns.get(cid, []))}"
        for cid in ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10", "E12", "E13", "E15", "E16", "E17", "E18", "E19", "E20", "E21", "W1", "W2", "W3"]
    )
    print(f"Counts: {counts}")
    if ne:
        print(f"RESULT: FAIL — {ne} error(s), {nw} warning(s).")
    else:
        print(f"RESULT: PASS — 0 errors, {nw} warning(s).")


def main(argv):
    if len(argv) != 2:
        print("usage: python3 tools/validate_song.py songs/<slug>", file=sys.stderr)
        return 2
    song_dir = argv[1].rstrip("/")
    if not os.path.isdir(song_dir):
        print(f"ERROR: not a directory: {song_dir}", file=sys.stderr)
        return 2
    rep, _ = run_checks(song_dir)
    print_report(rep, song_dir)
    return 1 if rep.n_errors() else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
