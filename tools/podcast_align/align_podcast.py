#!/usr/bin/env python3
"""
Forced-alignment podcast word-timing for Manaoke song pages.

WHY THIS EXISTS (read before touching podcast_script timing):
The immerse "Liner Notes" transcript karaoke-highlights each word as it's spoken.
We tried deriving per-word times from whisper TRANSCRIPTION and it HALLUCINATED —
on bilingual audio, English whisper "hears" English over the Japanese reader's
lines (it stamped "Life is" on top of the Japanese title), so words lit on the
wrong line. Forced alignment is given the KNOWN script text and only finds WHEN
each word is spoken; constrained to the real words, it cannot hallucinate.

WHAT IT DOES
Reads a song's data.json `podcast_script` (each entry [speaker, text, ...]),
force-aligns the FULL podcast audio to the known text, and rewrites each entry to
    [speaker, text, lineStartSec, [[tokenText, startSec], ...]]
EN lines tokenize on whitespace; JP lines tokenize into words via fugashi (+ a
per-song KNOWN_SPLITS map for archaic spellings no tokenizer splits, e.g. the
all-katakana title). The song-page renderer draws one <span> per token and lights
each at its start time, so every word — English or Japanese — highlights in sync.

ENV / DEPS (conda `parler`): ctc-forced-aligner (deskpai ONNX fork), unidecode,
fugashi+unidic-lite, pykakasi, onnxruntime, ffmpeg. The MMS model auto-downloads
once to ~/.cache/ctc_forced_aligner/. Pre-romanize JP ourselves (pykakasi hepburn)
— do NOT let the aligner's unidecode romanize kanji (it returns Chinese pinyin).

USAGE
  python align_podcast.py --audio podcast.mp3 --data path/to/song/data.json
  (writes back in place; pass --out to write elsewhere; --dry to just print)
Then run tools/bump_asset_versions.py on the song HTML (data.json changed).
"""
import os, sys, json, re, argparse, subprocess, tempfile

# Per-song splits for spellings the tokenizer leaves as one block. Keyed by the
# exact surface fugashi yields. Add entries when a new song has archaic/forced
# orthography (the concat of the values MUST equal the key).
KNOWN_SPLITS = {
    "イノチミジカシコイセヨオトメ": ["イノチ", "ミジカシ", "コイセヨ", "オトメ"],  # 命短し恋せよ乙女
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, help="podcast audio (mp3/wav/etc)")
    ap.add_argument("--data", required=True, help="song data.json (with podcast_script)")
    ap.add_argument("--out", default=None, help="output data.json (default: in place)")
    ap.add_argument("--dry", action="store_true", help="print sanity, don't write")
    args = ap.parse_args()

    import onnxruntime as ort
    import fugashi
    from pykakasi import kakasi
    from ctc_forced_aligner import (generate_emissions, get_alignments, get_spans,
        load_audio, postprocess_results, preprocess_text, Tokenizer,
        ensure_onnx_model, MODEL_URL)

    tagger = fugashi.Tagger(); kks = kakasi()
    def romaji(s): return "".join(seg["hepburn"] for seg in kks.convert(s)).lower()
    def clean(s): return re.sub(r"[^a-z]", "", s)
    def jp_seg(text):
        out = []
        for chunk in re.split(r"(\s+)", text):
            if chunk == "": continue
            if chunk.isspace(): out.append(chunk); continue
            toks = [w.surface for w in tagger(chunk)]; exp = []
            for tk in toks: exp.extend(KNOWN_SPLITS.get(tk, [tk]))
            out.extend(exp if "".join(exp) == chunk else list(chunk))
        return out

    dj = json.load(open(args.data))
    ps = dj["podcast_script"]

    # 16kHz mono wav
    wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", args.audio, "-ar", "16000",
                    "-ac", "1", wav], check=True)

    # tokens (with per-language romanization; punctuation romanizes to empty)
    toks = []
    for i, e in enumerate(ps):
        spk, text = e[0], e[1]
        line_toks = jp_seg(text) if spk == "JP" else text.split()
        for tk in line_toks:
            rom = clean(romaji(tk)) if spk == "JP" else clean(tk.lower())
            toks.append({"line": i, "text": tk, "rom": (rom or None)})
    words = [t["rom"] for t in toks if t["rom"]]

    mp = os.path.expanduser("~/.cache/ctc_forced_aligner/model.onnx")
    ensure_onnx_model(mp, MODEL_URL)
    sess = ort.InferenceSession(mp, providers=["CPUExecutionProvider"])
    audio = load_audio(wav)
    emissions, stride = generate_emissions(sess, audio, batch_size=8)
    ts, txts = preprocess_text(" ".join(words), romanize=False, language="eng", split_size="word")
    segs, scores, blank = get_alignments(emissions, ts, Tokenizer())
    spans = get_spans(ts, segs, blank)
    wts = postprocess_results(txts, spans, stride, scores)
    if len(wts) != len(words):
        sys.exit(f"FATAL: {len(wts)} stamps != {len(words)} words — alignment drift, do not ship")

    wi = 0
    for t in toks:
        if t["rom"]:
            t["start"] = float(wts[wi]["start"]); wi += 1
        else:
            t["start"] = None
    prev = 0.0
    for t in toks:                              # punctuation inherits prior start
        if t["start"] is None: t["start"] = prev
        else: prev = t["start"]

    from collections import OrderedDict
    byline = OrderedDict()
    for t in toks: byline.setdefault(t["line"], []).append(t)
    def clip_marker(entry):                     # human-clip splice marker, if any
        for x in entry[2:]:
            if isinstance(x, dict) and x.get("clip"):
                return x
        return None

    new = [None] * len(ps)
    for i, e in enumerate(ps):
        lt = byline[i]; pairs = []; pv = lt[0]["start"]
        for t in lt:
            st = max(pv, t["start"]); pairs.append([t["text"], round(st, 3)]); pv = st
        new[i] = [e[0], e[1], round(lt[0]["start"], 3), pairs]
        cm = clip_marker(e)                      # preserve clip-splice marker (generate_podcast reads it)
        if cm is not None:
            new[i].append(cm)
    for i in range(1, len(new)):                # enforce monotonic line starts
        if new[i][2] < new[i-1][2]: new[i][2] = new[i-1][2]

    # integrity gate
    bad = []
    for i, e in enumerate(new):
        spk, text, ls, tk = e[0], e[1], e[2], e[3]
        cc = "".join(x[0] for x in tk)
        base = text if spk == "JP" else re.sub(r"\s+", "", text)
        ccx = cc if spk == "JP" else re.sub(r"\s+", "", cc)
        if ccx != base: bad.append(("concat", i))
        if [x[1] for x in tk] != sorted(x[1] for x in tk): bad.append(("mono", i))
    print(f"{len(new)} lines, {sum(len(e[3]) for e in new)} tokens, integrity issues: {bad[:10]} ({len(bad)})")
    print(f"line1 {new[1][3][:3]}... | line2 first {new[2][3][0]}")
    if bad: sys.exit("FATAL: integrity issues — do not ship")
    if args.dry: return
    dj["podcast_script"] = new
    json.dump(dj, open(args.out or args.data, "w"), ensure_ascii=False)
    print("wrote", args.out or args.data)

if __name__ == "__main__":
    main()
