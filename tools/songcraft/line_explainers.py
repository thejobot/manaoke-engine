#!/usr/bin/env python3
"""Per-line drill-tail explainers — author/check/build for ANY Manaoke song.

The study card's Word-by-Word drill ends each line with a TAIL: it speaks the
whole Japanese sentence, then an English voice that EXPLAINS the sentence and
gives context. That tail only fires on a line when BOTH exist:

  1. a LINE_EXPLAIN entry in the song's index.html, keyed by the
     whitespace-stripped JP line text (== runtime `lineTrKey`), and
  2. an `en-US:<that explanation>` clip in tts_manifest.json (the runtime has
     NO Siri fallback for the tail — a missing clip = silent skip).

Standard (since inochi v0.92): EVERY lyric line gets this treatment. This tool
finds the gaps and fills them.

PURE-EN LINES (code-switching songs like shinunoga): a lyric line with no CJK
has no JP-line clip BY DESIGN (the JP voice never reads English). Its tail is
the DRILL_MAP concat = [en-US spoken-line clip + explainer], zero word pairs
(Round-11 page support: the pill gets has-translation off LINE_EXPLAIN alone
and dispatches playWordDrill([], el)). Such a line passes check when it has
(1) a LINE_EXPLAIN entry, (2) an en-US spoken-line clip in the manifest
(content_to_data emits audio/en/line_en_uNN.mp3), (3) the en-US explainer
clip, and (4) a DRILL_MAP entry with audio (build_drill_concat wired it).
JP-line requirements are untouched.

USAGE
  # 1) See which lines are missing the tail (read-only; exit 1 if any gap):
  python3 tools/songcraft/line_explainers.py check songs/<dir>
  #    write a fill-in template of just the missing lines (+ LINE_TR hints):
  python3 tools/songcraft/line_explainers.py check songs/<dir> --template gaps.json

  # 2) Author the explanations into gaps.json ({ "<jp_line>": "She's saying: ..." }),
  #    then render + wire them in (RUN WITH THE KOKORO ENV PYTHON):
  /opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python \
      tools/songcraft/line_explainers.py build songs/<dir> gaps.json
  #    then:  python3 tools/bump_asset_versions.py
  #           python3 tools/validate_tts_safety.py songs/<dir>/tts_manifest.json

AUTHORING the explanations (the only non-mechanical part):
  - Voice/format match the existing entries: open with "She's saying:", give the
    plain-English meaning of the WHOLE line, then one beat of song context.
  - Spoken-only (never displayed) -> write for the EAR. TTS-safe: no CJK, no
    macrons (ā/ē/...), no em/en dashes (Kokoro mispronounces/over-pauses them).
  - Clips render once into the SHARED assets dir (_assets/<slug>/audio/en/), so
    they work across every build dir via the _redirects rewrite. Lean builds add
    no audio of their own.

Recipe (manaoke-audio-recipe): Kokoro am_michael @0.95 -> two-pass loudnorm
I=-16 TP=-1.5 LRA=11 -> mp3. Filename = line_<sha1(text)[:8]>_explain.mp3.
"""
import argparse, hashlib, json, os, re, sys

CJK = re.compile(r'[　-ヿ㐀-鿿＀-￯]')
MAC = re.compile(r'[āēīōūĀĒĪŌŪ]')


def line_tr_key(s):
    """Mirror of the runtime lineTrKey(): drop a trailing (×N), strip all space."""
    s = re.sub(r'\s*\(×\d+\)\s*$', '', s or '')
    return re.sub(r'\s+', '', s).strip()


def _unesc(s):
    """Undo JS string escaping for \\' \\" \\\\ only — leave unicode intact
    (encode/unicode_escape would mangle the Japanese keys)."""
    return s.replace("\\'", "'").replace('\\"', '"').replace('\\\\', '\\')


def _js_esc(s):
    """Escape a string for a single-quoted JS literal: backslash then apostrophe."""
    return s.replace('\\', '\\\\').replace("'", "\\'")


def parse_js_obj(html, name):
    """Parse `const <name> = { 'k': '...', ... };` -> dict (string values only)."""
    m = re.search(r'const ' + re.escape(name) + r' = \{(.*?)\n\};', html, re.S)
    if not m:
        return {}, None
    # Drop pure `//` comment lines so a `'x': 'y'`-shaped substring inside a
    # comment can't be parsed as a spurious entry (the block carries a `// vNN:`
    # note). Only whole-line comments — never split a value that contains `//`.
    block = '\n'.join(ln for ln in m.group(1).split('\n') if not ln.lstrip().startswith('//'))
    out = {}
    for km in re.finditer(r"'((?:[^'\\]|\\.)*)'\s*:\s*'((?:[^'\\]|\\.)*)'", block):
        out[_unesc(km.group(1))] = _unesc(km.group(2))
    return out, m


def load_song(song_dir):
    data = json.load(open(os.path.join(song_dir, 'data.json'), encoding='utf-8'))
    manifest = json.load(open(os.path.join(song_dir, 'tts_manifest.json'), encoding='utf-8'))
    html = open(os.path.join(song_dir, 'index.html'), encoding='utf-8').read()
    cache = {}
    for e in manifest:
        if isinstance(e, list) and len(e) >= 4 and e[0] and e[1] and e[3]:
            cache.setdefault(f"{e[0]}:{e[1]}", e[3])
    explain, _ = parse_js_obj(html, 'LINE_EXPLAIN')
    line_tr, _ = {}, None
    m = re.search(r'const LINE_TR = \{(.*?)\n\};', html, re.S)
    if m:  # values are objects {en:'..', full:'..'} — pull en/full per key
        for km in re.finditer(r"'((?:[^'\\]|\\.)*)'\s*:\s*\{([^}]*)\}", m.group(1)):
            obj = {}
            for em in re.finditer(r"(\w+)\s*:\s*'((?:[^'\\]|\\.)*)'", km.group(2)):
                obj[em.group(1)] = _unesc(em.group(2))
            line_tr[_unesc(km.group(1))] = obj
    return data, manifest, html, cache, explain, line_tr


def lyric_lines(data):
    return [l.get('text', '') for l in data.get('apple_lyrics', {}).get('lines', [])
            if l.get('text', '').strip()]


def parse_drill_map(html):
    """The page's inline `const DRILL_MAP = {...};` as a dict (it is emitted as
    one-line compact JSON by build_drill_concat.patch_html). None when the page
    has no DRILL_MAP block at all."""
    m = re.search(r'const DRILL_MAP = (\{.*?\});', html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def audit(data, cache, explain, drill_map=None):
    """Per unique line: (key, sample_text, line_ok, has_explain, en_ok, is_en,
    concat_ok). line_ok = the ja-JP line clip for a JP line, the en-US
    spoken-line clip for a pure-EN line. concat_ok only gates pure-EN lines
    (their tail plays ONLY via the DRILL_MAP concat — no chained fallback);
    JP lines keep their original triple-AND untouched."""
    seen, rows = set(), []
    for text in lyric_lines(data):
        key = line_tr_key(text)
        if key in seen:
            continue
        seen.add(key)
        exp = explain.get(key, '')
        is_en = not CJK.search(text)
        if is_en:
            line_ok = ('en-US:' + text) in cache
            concat_ok = bool(((drill_map or {}).get(key) or {}).get('audio'))
        else:
            line_ok = ('ja-JP:' + text) in cache
            concat_ok = True
        rows.append((key, text, line_ok,
                     bool(exp),
                     (('en-US:' + exp) in cache) if exp else False,
                     is_en, concat_ok))
    return rows


# ---------------------------------------------------------------- check --------
def cmd_check(args):
    data, _, html, cache, explain, line_tr = load_song(args.song_dir)
    # Precondition guard: the drill-tail explainer needs the v090+ runtime
    # (playTail + grid.dataset.explain). An older page (e.g. silhouette-v023)
    # won't speak the tail even with full LINE_EXPLAIN — it needs the
    # apply-to-both port first; line_explainers can't supply that.
    if 'playTail' not in html or 'dataset.explain' not in html:
        print("⚠ WARNING: this page predates the v090+ drill-tail runtime "
              "(no playTail / data-explain). Authoring LINE_EXPLAIN alone will NOT "
              "make the tail speak — port the page to the current architecture first "
              "(SONG-CONTRACT §1.3/§2.8.1).")
    valid_keys = {line_tr_key(t) for t in lyric_lines(data)}
    orphans = sorted(k for k in explain if k not in valid_keys)
    rows = audit(data, cache, explain, drill_map=parse_drill_map(html))
    ok_row = lambda r: r[2] and r[3] and r[4] and r[6]
    full = [r for r in rows if ok_row(r)]
    gaps = [r for r in rows if not ok_row(r)]
    print(f"{args.song_dir}: {len(full)}/{len(rows)} unique lines have the full tail "
          f"(sentence clip + explain entry + EN clip; pure-EN lines also need "
          f"their DRILL_MAP concat)")
    if gaps:
        print("\nGAPS:")
        for key, text, line_ok, hasexp, en, is_en, concat_ok in gaps:
            why = []
            if not line_ok:
                why.append("no EN spoken-line clip (pure-EN line)" if is_en
                           else "no JP-line clip")
            if not hasexp: why.append("no LINE_EXPLAIN entry")
            elif not en:   why.append("explain present but no EN clip")
            if is_en and not concat_ok:
                why.append("no DRILL_MAP concat (run build_drill_concat)")
            print(f"  - {text}\n      {', '.join(why)}")
    if orphans:
        print(f"\nORPHAN LINE_EXPLAIN keys (match no lyric line — e.g. inherited from a "
              f"clone; validate_song flags these E3): {len(orphans)}")
        for k in orphans:
            print(f"  - {k}")
    if args.template:
        missing = {text: "" for key, text, line_ok, hasexp, en, is_en, concat_ok in gaps
                   if not hasexp}
        hints = {text: line_tr.get(line_tr_key(text), {})
                 for text in missing}
        json.dump({"_README": "Fill each value with a spoken English explanation "
                   "(She's saying: <meaning>. <context>). No CJK or macron vowels; "
                   "em dashes are OK (comma-normalized for the voice). "
                   "Delete _README and _hints before running build.",
                   "_hints": hints, **missing},
                  open(args.template, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f"\nWrote fill-in template for {len(missing)} line(s) -> {args.template}")
    return 0 if not gaps else 1


# ---------------------------------------------------------------- build --------
def render_clips(jobs, overwrite):
    """jobs = [(spoken_text, mp3_path)]. Kokoro am_michael @0.95 + two-pass loudnorm.
    Em/en dashes are normalized to ', ' for the VOICE ONLY (Kokoro over-pauses on a
    bare dash); the filename hash / manifest key / LINE_EXPLAIN value keep the
    original text — exactly how the legacy explainer clips were made."""
    import subprocess, time
    from kokoro import KPipeline
    import soundfile as sf
    import numpy as np
    pipe = KPipeline(lang_code='a')

    def loudnorm(wav, mp3):
        p1 = subprocess.run(['ffmpeg', '-y', '-i', wav, '-af',
            'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'],
            capture_output=True, text=True)
        j = json.loads(p1.stderr[p1.stderr.rindex('{'):p1.stderr.rindex('}') + 1])
        af = ("loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:"
              f"measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
              f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:"
              f"offset={j['target_offset']}")
        subprocess.run(['ffmpeg', '-y', '-i', wav, '-af', af,
            '-codec:a', 'libmp3lame', '-q:a', '2', mp3], capture_output=True, check=True)

    t0 = time.time()
    for i, (text, mp3) in enumerate(jobs, 1):
        if os.path.exists(mp3) and not overwrite:
            print(f"[{i}/{len(jobs)}] exists, skip {os.path.basename(mp3)}"); continue
        os.makedirs(os.path.dirname(mp3), exist_ok=True)
        spoken = text.replace('—', ', ').replace('–', ', ')
        chunks = [a for _, _, a in pipe(spoken, voice='am_michael', speed=0.95)]
        audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
        wav = mp3.replace('.mp3', '.tmp.wav')
        sf.write(wav, audio, 24000)
        loudnorm(wav, mp3)
        os.remove(wav)
        print(f"[{i}/{len(jobs)}] {os.path.basename(mp3)} ({time.time()-t0:.0f}s)", flush=True)


def cmd_build(args):
    data, manifest, html, cache, explain, _ = load_song(args.song_dir)
    slug = data.get('slug')
    if not slug:
        print("ERROR: data.json has no 'slug'", file=sys.stderr); return 2
    # Shared assets dir: <repo>/songs/_assets/<slug>/audio/en. song_dir is
    # normally <repo>/songs/<dir> (so ../.. is the repo); if that doesn't look
    # like the repo, walk up for a dir containing songs/_assets.
    repo = os.path.dirname(os.path.dirname(os.path.abspath(args.song_dir)))
    if not os.path.isdir(os.path.join(repo, 'songs', '_assets')):
        cur = os.path.abspath(args.song_dir)
        while cur != os.path.dirname(cur):
            if os.path.isdir(os.path.join(cur, 'songs', '_assets')):
                repo = cur; break
            cur = os.path.dirname(cur)
    en_dir = os.path.join(repo, 'songs', '_assets', slug, 'audio', 'en')

    raw = json.load(open(args.explains, encoding='utf-8'))
    raw = {k: v for k, v in raw.items() if not k.startswith('_')}  # drop _README/_hints
    explains = {k: v.strip() for k, v in raw.items() if v and v.strip()}
    if not explains:
        print("ERROR: no non-empty explanations in", args.explains, file=sys.stderr); return 2

    # TTS-safety gate (fail-closed, report ALL): CJK and macron vowels are hard
    # errors (Kokoro speaks/garbles them). Em/en dashes are NOT rejected — they
    # are comma-normalized for the voice in render_clips (§ recipe) while the
    # value keeps its dashes.
    bad = [k for k, v in explains.items() if CJK.search(v) or MAC.search(v)]
    if bad:
        print("TTS-SAFETY VIOLATIONS (CJK or macron — fix these, then re-run):",
              *bad, sep='\n  ', file=sys.stderr)
        return 2

    valid_keys = {line_tr_key(t) for t in lyric_lines(data)}
    manifest_keys = {(e[0], e[1]) for e in manifest if isinstance(e, list) and len(e) >= 2}
    jobs, new_manifest, inserts, rewrites = [], [], [], {}
    unknown, fully_present = [], []
    for jp_key, text in explains.items():
        k = line_tr_key(jp_key)
        if k not in valid_keys:
            unknown.append(jp_key); continue
        h = hashlib.sha1(text.encode()).hexdigest()[:8]
        rel = f'audio/en/line_{h}_explain.mp3'
        mp3 = os.path.join(en_dir, f'line_{h}_explain.mp3')
        html_has = k in explain
        html_changed = html_has and explain[k] != text
        manifest_has = ('en-US', text) in manifest_keys
        clip_present = os.path.exists(mp3)
        # Decouple HTML / manifest / clip: only fully skip when ALL three agree
        # (so `build` can also fix an explain-present-but-no-clip gap or re-author
        # a changed value — the cases the old key-in-LINE_EXPLAIN short-circuit
        # silently dropped).
        if html_has and not html_changed and manifest_has and clip_present:
            fully_present.append(jp_key); continue
        if not clip_present or args.overwrite:
            jobs.append((text, mp3))
        if not manifest_has:
            new_manifest.append(['en-US', text, text, rel]); manifest_keys.add(('en-US', text))
        if not html_has:
            inserts.append((k, text))
        elif html_changed:
            rewrites[k] = text

    if unknown:
        print("WARNING: these keys match no lyric line (typo? wrong (×N)?):",
              *unknown, sep='\n  ')
    if fully_present:
        print(f"Already complete (HTML+manifest+clip), skipping {len(fully_present)}.")
    if not (jobs or new_manifest or inserts or rewrites):
        print("Nothing to do."); return 0

    if not args.no_render:
        if jobs: render_clips(jobs, args.overwrite)
    else:
        print(f"--no-render: skipped rendering {len(jobs)} clip(s) (manifest/HTML still written).")

    # Append manifest entries.
    if new_manifest:
        manifest.extend(new_manifest)
        json.dump(manifest, open(os.path.join(args.song_dir, 'tts_manifest.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        print(f"Manifest: +{len(new_manifest)} entries.")

    # Patch index.html LINE_EXPLAIN: rewrite changed values in place, append new keys.
    if inserts or rewrites:
        _, m = parse_js_obj(html, 'LINE_EXPLAIN')
        if not m:
            print("ERROR: no LINE_EXPLAIN block in index.html to patch", file=sys.stderr); return 2
        block_inner = m.group(1)
        for k, text in rewrites.items():
            pat = re.compile(r"('" + re.escape(k) + r"'\s*:\s*')(?:[^'\\]|\\.)*(')")
            block_inner, n = pat.subn(lambda mm: mm.group(1) + _js_esc(text) + mm.group(2),
                                      block_inner, count=1)
            if n == 0:
                print(f"WARNING: could not rewrite LINE_EXPLAIN value for {k} (left as-is)")
        ins_lines = [f"  '{k}': '{_js_esc(text)}'," for k, text in inserts]
        tail = ("\n" + "\n".join(ins_lines)) if ins_lines else ""
        new_block = "const LINE_EXPLAIN = {" + block_inner + tail + "\n};"
        html2 = html[:m.start()] + new_block + html[m.end():]
        open(os.path.join(args.song_dir, 'index.html'), 'w', encoding='utf-8').write(html2)
        print(f"index.html: +{len(inserts)} new, {len(rewrites)} rewritten LINE_EXPLAIN entries.")

    print("\nNEXT:\n  python3 tools/bump_asset_versions.py"
          f"\n  python3 tools/validate_tts_safety.py {args.song_dir}/tts_manifest.json"
          f"\n  python3 tools/songcraft/line_explainers.py check {args.song_dir}   # expect full coverage"
          "\n  # then deploy a fresh slug (verify new audio with ?cb=, never the bare URL mid-deploy).")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check', help='report lines missing the drill-tail explanation')
    c.add_argument('song_dir')
    c.add_argument('--template', help='write a fill-in JSON of the missing lines')
    c.set_defaults(func=cmd_check)
    b = sub.add_parser('build', help='render + wire explanations (run with kokoro env python)')
    b.add_argument('song_dir')
    b.add_argument('explains', help='JSON {"<jp line>": "<spoken explanation>"}')
    b.add_argument('--no-render', action='store_true', help='skip audio render (text/manifest only)')
    b.add_argument('--overwrite', action='store_true', help='re-render clips that already exist')
    b.set_defaults(func=cmd_build)
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == '__main__':
    main()
