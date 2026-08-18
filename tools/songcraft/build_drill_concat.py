#!/usr/bin/env python3
"""Build ONE concatenated drill audio file + timing map per lyric line.

WHY: the study card's Word-by-Word drill used to CHAIN a separate <audio>.play()
per segment (word1 JP, word1 gloss, word2 JP, ..., whole line, explanation),
stitched with onended + setTimeout. On iOS Safari the later plays fall outside
the originating tap gesture and race/stall on a flaky link — the drill "doesn't
always play." This tool concatenates each line's whole drill into ONE lean mono
mp3 so the runtime plays it with a SINGLE .play() off the gesture and drives the
word advancement + highlight off audio.currentTime via the emitted timing map —
exactly how the pitch-accent card already works (one Audio, mora_starts/ends).

Order per line:  w1 JP, w1 EN gloss, w2 JP, w2 EN gloss, ... , whole JP line,
EN explanation. Small silences between segments preserve the drill's cadence.

Pure-EN lyric lines (no CJK — code-switching songs like shinunoga) own ZERO
word pairs but still get a concat when they have a LINE_EXPLAIN entry:
[EN spoken-line clip + explainer tail]. Their line clip is manifest-keyed
en-US (content_to_data emits audio/en/line_en_uNN.mp3); words:[] lights
nothing and tail.jp marks the line-clip start (0.0), consistent with the
page's DRILL_MAP consumer (which reads only .audio/.words — tail is a
bookkeeping record).

Segments are decoded to sample-exact PCM and concatenated before a SINGLE mp3
encode, so the timing map never accumulates the per-clip encoder-padding drift
that summing independent ffprobe durations would introduce.

Output:
  songs/_assets/<slug>/audio/drill/line_<sha1(lineKey)[:8]>.mp3   (shared asset)
  DRILL_MAP injected into <song_dir>/index.html  (keyed by lineKey ->
     { audio, dur, words:[{s,e}], tail:{jp,ex} })

The audio/* _redirects rewrite already resolves audio/drill/* from the lean
build dirs; /songs/*.mp3 gives it immutable caching. No _redirects/_headers
change needed. The runtime resolves the concat by lineKey (no manifest entry
needed — like the per-word clips).

Usage:
  # (drill order is read from the RENDERED page via extract_drill.js/puppeteer)
  python3 tools/songcraft/build_drill_concat.py songs/inochi-mijikashi-<slug>
  # then:  python3 tools/bump_asset_versions.py   (if data/manifest changed — not here)
"""
import argparse, hashlib, json, os, re, subprocess, sys, tempfile

# CJK detector (mirrors validate_tts_safety / the runtime HAS_CJK): a lyric
# line with NO CJK is a pure-EN line — its spoken-line tail clip is en-US.
CJK = re.compile(r'[぀-ヿ㐀-鿿ｦ-ﾟ]')

# cadence (seconds) — approximates the legacy chained drill's breathing room
GAP_JP_GLOSS = 0.28   # within a word: JP -> its EN gloss
GAP_WORD     = 0.50   # between words: gloss -> next word's JP
GAP_TAIL_JP  = 0.60   # last gloss -> whole JP line
GAP_TAIL_EX  = 0.45   # whole JP line -> EN explanation
SR = 48000
BITRATE = '80k'       # final mono mp3 encode

# recipe fingerprint for the deps manifest: any tweak to these params (or to
# this tool itself) must invalidate every recorded concat, so the incremental
# skip can never reuse audio rendered under a different recipe.
RECIPE_PARAMS = {'GAP_JP_GLOSS': GAP_JP_GLOSS, 'GAP_WORD': GAP_WORD,
                 'GAP_TAIL_JP': GAP_TAIL_JP, 'GAP_TAIL_EX': GAP_TAIL_EX,
                 'SR': SR, 'BITRATE': BITRATE}
BUILDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'builds')


def sha8_file(path):
    """First 8 hex of sha256 of a file's bytes (site-wide convention)."""
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:8]


def tool_recipe():
    return {'tool_sha8': sha8_file(os.path.abspath(__file__)), 'params': RECIPE_PARAMS}


def romuid(rom):
    """Mirror of runtime _romUid()."""
    r = str(rom or '').replace(' ', '-').replace('·', '').replace('/', '_')
    return re.sub(r'^-+|-+$', '', r)


def line_key_hash(line_key):
    return hashlib.sha1(line_key.encode('utf-8')).hexdigest()[:8]


def live_dir_for_slug(repo, slug):
    """Basename of the dir the ROOT landing currently serves for this song
    (from its `url: '/songs/<slug>-.../'` entry), or None if not found."""
    root = os.path.join(repo, 'index.html')
    if not os.path.isfile(root):
        return None
    html = open(root, encoding='utf-8').read()
    m = re.search(r"url:\s*'/songs/(" + re.escape(slug) + r"-[^/']+)/'", html)
    return m.group(1) if m else None


def repoint_root(repo, slug, target_basename):
    """Point the root landing's `url:` for this song at target_basename. Returns
    number of substitutions (0 = song not on the landing)."""
    root = os.path.join(repo, 'index.html')
    html = open(root, encoding='utf-8').read()
    pat = r"(url:\s*'/songs/)" + re.escape(slug) + r"-[^/']+(/')"
    new, n = re.subn(pat, r"\g<1>" + target_basename + r"\g<2>", html, count=1)
    if n:
        open(root, 'w', encoding='utf-8').write(new)
    return n


def load_manifest_cache(song_dir):
    man = json.load(open(os.path.join(song_dir, 'tts_manifest.json'), encoding='utf-8'))
    cache = {}
    for e in man:
        if isinstance(e, list) and len(e) >= 4 and e[0] and e[1] and e[3]:
            cache.setdefault(f"{e[0]}:{e[1]}", e[3])
    return cache


def extract_spec(song_dir):
    """Run the puppeteer extractor to get the real runtime drill order."""
    here = os.path.dirname(os.path.abspath(__file__))
    js = os.path.join(here, 'extract_drill.js')
    r = subprocess.run(['node', js, song_dir], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.stderr.write(r.stderr)
        raise SystemExit(f"extract_drill.js failed ({r.returncode})")
    sys.stderr.write(r.stderr)
    return json.loads(r.stdout)


def probe_dur(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'csv=p=0', path], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def decode_wav(src, dst):
    """Decode any clip to sample-exact 48k mono s16 PCM."""
    subprocess.run(['ffmpeg', '-y', '-i', src, '-ar', str(SR), '-ac', '1',
                    '-c:a', 'pcm_s16le', dst], capture_output=True, check=True)


def make_silence_wav(seconds, dst):
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i',
                    f'anullsrc=r={SR}:cl=mono', '-t', f'{seconds:.4f}',
                    '-c:a', 'pcm_s16le', dst], capture_output=True, check=True)


def wav_dur(path):
    """Exact wav duration = samples / rate (no codec padding ambiguity)."""
    return probe_dur(path)


def concat_line(segments, gaps, out_mp3, tmp):
    """segments = ordered list of source clip paths. gaps = list len == len(segments)
    of the silence (s) to insert AFTER each segment (0 = none). Returns
    (total_dur, seg_windows) where seg_windows[i] = (start, end) of segment i in
    the OUTPUT timeline (silence not included in a segment's window)."""
    parts = []      # (wav_path, is_segment, seg_index)
    for i, src in enumerate(segments):
        w = os.path.join(tmp, f'seg_{i}.wav')
        decode_wav(src, w)
        parts.append((w, True, i))
        g = gaps[i]
        if g > 0:
            s = os.path.join(tmp, f'gap_{i}.wav')
            make_silence_wav(g, s)
            parts.append((s, False, -1))
    # concat demuxer on PCM = lossless, sample-exact
    listtxt = os.path.join(tmp, 'list.txt')
    with open(listtxt, 'w') as f:
        for w, _, _ in parts:
            f.write(f"file '{w}'\n")
    combined = os.path.join(tmp, 'combined.wav')
    subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', listtxt,
                    '-c:a', 'pcm_s16le', combined], capture_output=True, check=True)
    # segment windows from cumulative wav durations (exact against the PCM)
    seg_windows, t = {}, 0.0
    for w, is_seg, si in parts:
        d = wav_dur(w)
        if is_seg:
            seg_windows[si] = (round(t, 3), round(t + d, 3))
        t += d
    total = round(t, 3)
    # single mp3 encode of the whole line (mono 80k) — one encode, no per-seg gaps
    os.makedirs(os.path.dirname(out_mp3), exist_ok=True)
    subprocess.run(['ffmpeg', '-y', '-i', combined, '-ar', str(SR), '-ac', '1',
                    '-c:a', 'libmp3lame', '-b:a', BITRATE, out_mp3],
                   capture_output=True, check=True)
    return total, seg_windows


def derive_line_segs(L, assets, asset_root, cache):
    """Resolve the ordered source clips + gaps one line's concat renders — the
    exact sequence main() feeds concat_line — WITHOUT touching ffmpeg (also used
    by emit_deps.py to reconstruct inputs for already-built songs). Returns
    (segs, gaps, word_windows_idx, tail_jp_i, tail_ex_i, roles, warn):
    segs is None when a required JP clip is missing (warn names it); roles[i] =
    {'role': 'jp'|'gloss'|'tail_jp'|'tail_line_en'|'tail_ex'[, 'word': word_idx]}
    per seg ('tail_line_en' = a pure-EN line's en-US spoken-line clip)."""
    def clip(rel):
        # per-word clips, addressed relative to the audio/ dir (e.g. "jp/word_..")
        return os.path.join(assets, rel) if rel else None

    def clip_manifest(rel):
        # tail clips come from the manifest as page-relative urls ("audio/jp/..")
        return os.path.join(asset_root, rel) if rel else None

    segs, gaps, word_windows_idx, roles = [], [], [], []
    for wi, p in enumerate(L['drill']):
        uid = romuid(p['u']); s = p['s']
        jp = clip(f"jp/word_{s}_{uid}.mp3")
        gl = clip(f"en/word_{s}_{uid}_gloss.mp3")
        if not (jp and os.path.exists(jp)):
            return None, None, None, None, None, None, f"missing JP clip word_{s}_{uid}.mp3"
        jp_i = len(segs); segs.append(jp); gaps.append(GAP_JP_GLOSS)
        roles.append({'role': 'jp', 'word': wi})
        if gl and os.path.exists(gl):
            gl_i = len(segs); segs.append(gl); gaps.append(GAP_WORD)
            roles.append({'role': 'gloss', 'word': wi})
        else:
            gl_i = None; gaps[-1] = GAP_WORD   # no gloss: JP -> next word gap
        word_windows_idx.append((jp_i, gl_i))
    # tail: the whole sung line, then EN explanation (each guaranteed reachable —
    # it's one file). Skip a tail piece if its clip is absent. A JP line's clip
    # is manifest-keyed ja-JP; a pure-EN line's spoken-line clip is en-US
    # (never ja-JP for Latin text) — for those the concat is just
    # [EN line + explainer], zero word pairs.
    if CJK.search(L['lineJp']):
        ljp = clip_manifest(cache.get('ja-JP:' + L['lineJp']))
        line_role = 'tail_jp'
    else:
        ljp = clip_manifest(cache.get('en-US:' + L['lineJp']))
        line_role = 'tail_line_en'
    exp = clip_manifest(cache.get('en-US:' + L['explain']) if L['explain'] else None)
    # last word's trailing gap becomes GAP_TAIL_JP if a tail follows
    if segs and (ljp or exp):
        gaps[-1] = GAP_TAIL_JP
    tail_jp_i = tail_ex_i = None
    if ljp and os.path.exists(ljp):
        tail_jp_i = len(segs); segs.append(ljp)
        gaps.append(GAP_TAIL_EX if (exp and os.path.exists(exp)) else 0.0)
        roles.append({'role': line_role})
    if exp and os.path.exists(exp):
        tail_ex_i = len(segs); segs.append(exp); gaps.append(0.0)
        roles.append({'role': 'tail_ex'})
    return segs, gaps, word_windows_idx, tail_jp_i, tail_ex_i, roles, None


def seg_inputs(segs, roles, repo):
    """deps-manifest `inputs` rows for one line: repo-relative path + sha8 +
    role (+ word index for per-word clips), in concat order."""
    rows = []
    for path, meta in zip(segs, roles):
        row = {'path': os.path.relpath(path, repo), 'sha8': sha8_file(path),
               'role': meta['role']}
        if 'word' in meta:
            row['word'] = meta['word']
        rows.append(row)
    return rows


def load_deps_manifest(folder):
    p = os.path.join(BUILDS, f'{folder}.deps.json')
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding='utf-8'))
        except Exception as e:
            sys.stderr.write(f'  note: unreadable {os.path.basename(p)} ({e}); starting fresh.\n')
    return {}


def write_deps_manifest(folder, man):
    p = os.path.join(BUILDS, f'{folder}.deps.json')
    os.makedirs(BUILDS, exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(json.dumps(man, ensure_ascii=False, indent=1) + '\n')
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('song_dir')
    ap.add_argument('--overwrite', action='store_true', help='re-encode existing concat mp3s')
    ap.add_argument('--no-patch', action='store_true', help='build audio + map only; skip HTML injection')
    ap.add_argument('--promote', action='store_true',
                    help='this build becomes live: after patching, repoint the root landing '
                         'url: at song_dir so the shared audio and the live drill map move together')
    ap.add_argument('--force', action='store_true',
                    help='override the live-sync guard — proceed even though this rebuilds the '
                         'SHARED concat while root points elsewhere (accepts that the live page '
                         'is desynced until you promote)')
    args = ap.parse_args()

    song_dir = args.song_dir.rstrip('/')
    data = json.load(open(os.path.join(song_dir, 'data.json'), encoding='utf-8'))
    slug = data.get('slug')
    if not slug:
        raise SystemExit("data.json has no 'slug'")
    # repo root = two levels up from songs/<dir>; fall back to walking up
    repo = os.path.dirname(os.path.dirname(os.path.abspath(song_dir)))
    if not os.path.isdir(os.path.join(repo, 'songs', '_assets')):
        cur = os.path.abspath(song_dir)
        while cur != os.path.dirname(cur):
            if os.path.isdir(os.path.join(cur, 'songs', '_assets')):
                repo = cur; break
            cur = os.path.dirname(cur)
    # shared-assets FOLDER: resolve exactly like validate_song.resolve_song_folder
    # — the _redirects rewrite target for THIS song dir first, then the generic
    # :dir catch-all, then data.json slug. The conceptual slug and the _assets
    # folder can DIFFER (silhouette's assets live at _assets/silhouette2 because
    # _assets/silhouette belongs to the legacy live page); slug alone picked the
    # wrong asset root the first time such a song was built.
    folder, generic = None, None
    red_path = os.path.join(repo, '_redirects')
    if os.path.exists(red_path):
        for ln in open(red_path, encoding='utf-8'):
            m = re.search(r"^/songs/([^/\s]+)/audio/\*\s+/songs/_assets/([^/]+)/audio/", ln)
            if not m:
                continue
            src, tgt = m.group(1), m.group(2)
            if src == os.path.basename(song_dir):
                folder = tgt
                break
            if src.startswith(':') and generic is None:
                generic = tgt
    folder = folder or generic or slug
    asset_root = os.path.join(repo, 'songs', '_assets', folder)
    assets = os.path.join(asset_root, 'audio')
    drill_dir = os.path.join(assets, 'drill')

    # --- LIVE-SYNC GUARD -----------------------------------------------------
    # The concat lives in SHARED _assets, but each page carries its OWN inline
    # DRILL_MAP. Rebuilding the concat instantly changes what the LIVE page (the
    # dir root points at) plays, while that page keeps its old map => desync in
    # production. So a rebuild may only target the live dir, unless you promote
    # this dir to live in the same run (--promote, atomic) or knowingly override
    # (--force). This is the exact failure that shipped a desynced drill once.
    target = os.path.basename(song_dir)
    live = live_dir_for_slug(repo, slug)
    if live is not None and live != target and not (args.promote or args.force):
        sys.stderr.write(
            "\n  REFUSING: this rebuilds the SHARED drill concat, but root points at a\n"
            f"  DIFFERENT dir than the one you're patching:\n"
            f"      live (root url:) : {live}\n"
            f"      you're patching  : {target}\n"
            "  The live page keeps its own inline DRILL_MAP, so after this rebuild it\n"
            "  would play the new audio against its OLD timings => desynced drill in\n"
            "  production (this is the bug that shipped once).\n\n"
            "  Do ONE of:\n"
            f"    * rebuild in place on the live dir:  build_drill_concat.py songs/{live}\n"
            f"    * make THIS dir live in the same run: add --promote  (repoints root -> {target})\n"
            "    * override knowingly (live stays desynced until you promote): add --force\n\n")
        raise SystemExit(2)
    if live is None:
        sys.stderr.write(f"  note: no root url: found for slug '{slug}'; skipping live-sync check.\n")

    cache = load_manifest_cache(song_dir)
    spec = extract_spec(song_dir)

    # deps manifest (builds/<folder>.deps.json): this tool owns `drill` +
    # `recipe.build_drill_concat`; assemble_page.py owns the rest. Prior line
    # entries feed the incremental skip below; the run replaces drill.lines
    # wholesale with what it just verified or rendered. FOLDER-keyed (matches
    # assemble_page/emit_deps/plan_rebuild), not slug-keyed.
    manifest = load_deps_manifest(folder)
    recipe = tool_recipe()
    recipe_ok = (manifest.get('recipe') or {}).get('build_drill_concat') == recipe
    prev_lines = (manifest.get('drill') or {}).get('lines') or {}

    drill_map, new_lines, built, skipped, warnings = {}, {}, 0, 0, []
    for L in spec:
        key = L['lineKey']
        if key in drill_map:
            continue   # repeated line reuses one concat
        segs, gaps, word_windows_idx, tail_jp_i, tail_ex_i, roles, warn = \
            derive_line_segs(L, assets, asset_root, cache)
        if segs is None:
            warnings.append(f"{key}: {warn}")
            continue
        if not segs:
            continue

        h = line_key_hash(key)
        out_mp3 = os.path.join(drill_dir, f'line_{h}.mp3')
        rel_audio = f'audio/drill/line_{h}.mp3'
        inputs = seg_inputs(segs, roles, repo)

        # INCREMENTAL SKIP: reuse the recorded concat when nothing that feeds it
        # moved — same recipe (tool bytes + gap/encode params), same seg files
        # (paths + sha8s + roles, in order), and the out mp3 on disk still
        # matches the recorded out_sha8. The recorded `audio` value (with its
        # baked ?v=) and windows are emitted VERBATIM, so a full-skip run leaves
        # drill_map.json and the page's DRILL_MAP byte-identical. Adopted
        # (emit_deps.py) entries qualify too: their inputs were reconstructed by
        # this same derivation and their windows/audio read from the shipped
        # map, so matching out bytes + unchanged inputs make reuse exactly as
        # safe as for a rendered entry. Entries with EMPTY inputs are
        # unverifiable — never skip on them.
        prev = prev_lines.get(key)
        if (not args.overwrite and recipe_ok and prev
                and prev.get('inputs') and prev['inputs'] == inputs
                and prev.get('out') == rel_audio
                and prev.get('audio') and prev.get('out_sha8')
                and prev.get('windows')
                and os.path.exists(out_mp3)
                and sha8_file(out_mp3) == prev['out_sha8']):
            w = prev['windows']
            drill_map[key] = {'audio': prev['audio'], 'dur': w['dur'],
                              'words': w['words'], 'tail': w['tail']}
            new_lines[key] = prev
            skipped += 1
            continue

        with tempfile.TemporaryDirectory() as tmp:
            total, seg_win = concat_line(segs, gaps, out_mp3, tmp)
        # Cache-bust: the page uses drill_map[key].audio VERBATIM as the <audio>
        # src (new Audio(map.audio)) and CF serves /songs/*.mp3 immutable for a
        # year. Reusing the same line_<hash>.mp3 filename means an edited concat
        # never reaches a device that cached the old one. Append ?v=<sha8 of the
        # actual bytes> so a real change mints a fresh URL; identical bytes keep
        # the same URL (still cached). No template change — the URL is data.
        vh = sha8_file(out_mp3)
        audio_url = f'{rel_audio}?v={vh}'
        # word lit-windows: [jp.start, gloss.end] (row stays lit across the
        # mid-word gap); if no gloss, [jp.start, jp.end].
        words = []
        for (jp_i, gl_i) in word_windows_idx:
            s0 = seg_win[jp_i][0]
            e0 = seg_win[gl_i][1] if gl_i is not None else seg_win[jp_i][1]
            words.append({'s': s0, 'e': e0})
        tail = {}
        if tail_jp_i is not None: tail['jp'] = seg_win[tail_jp_i][0]
        if tail_ex_i is not None: tail['ex'] = seg_win[tail_ex_i][0]
        drill_map[key] = {'audio': audio_url, 'dur': total, 'words': words, 'tail': tail}
        new_lines[key] = {'out': rel_audio, 'out_sha8': vh, 'audio': audio_url,
                          'inputs': inputs,
                          'windows': {'dur': total, 'words': words, 'tail': tail}}
        built += 1

    if warnings:
        sys.stderr.write("WARNINGS:\n  " + "\n  ".join(warnings) + "\n")
    print(f"built {built} concat drill line(s), skipped {skipped} up-to-date -> {drill_dir}")

    # write a record of the map next to the audio (precedent: pitch_data/timing.json)
    map_path = os.path.join(drill_dir, 'drill_map.json')
    json.dump(drill_map, open(map_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"wrote timing map -> {map_path}")

    # record what this run rendered/verified into the deps manifest
    from datetime import datetime, timezone
    manifest.setdefault('schema', 1)
    manifest.setdefault('folder', folder)
    manifest.setdefault('key', slug)
    manifest['deploy_slug'] = target
    manifest['built_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    manifest.setdefault('recipe', {})['build_drill_concat'] = recipe
    manifest.setdefault('drill', {})['lines'] = new_lines
    print(f"wrote deps manifest -> {write_deps_manifest(folder, manifest)}")

    if args.no_patch:
        if args.promote:
            sys.stderr.write("  note: --promote ignored with --no-patch (nothing patched to make live).\n")
        return
    patch_html(os.path.join(song_dir, 'index.html'), drill_map)

    if args.promote and live != target:
        n = repoint_root(repo, slug, target)
        if n:
            print(f"PROMOTED: root landing url: -> /songs/{target}/ "
                  f"(live drill map now matches the shared concat)")
        else:
            sys.stderr.write(
                f"  WARNING: --promote could not find a root url: for '{slug}' to repoint; "
                f"root NOT changed. Set it by hand or the live drill will desync.\n")


def patch_html(html_path, drill_map):
    """Inject `const DRILL_MAP = {...};` — replace an existing block or insert
    one just before the drill runtime uses it."""
    html = open(html_path, encoding='utf-8').read()
    payload = 'const DRILL_MAP = ' + json.dumps(drill_map, ensure_ascii=False, separators=(',', ':')) + ';'
    if 'const DRILL_MAP' in html:
        html2 = re.sub(r'const DRILL_MAP = .*?;\n', payload + '\n', html, count=1, flags=re.S)
    else:
        # insert right before playWordDrill so it's defined first
        anchor = 'function playWordDrill('
        i = html.index(anchor)
        # back up to the start of that line
        ls = html.rfind('\n', 0, i) + 1
        html2 = html[:ls] + payload + '\n' + html[ls:]
    open(html_path, 'w', encoding='utf-8').write(html2)
    print(f"patched {html_path}: DRILL_MAP ({len(drill_map)} lines)")


if __name__ == '__main__':
    main()
