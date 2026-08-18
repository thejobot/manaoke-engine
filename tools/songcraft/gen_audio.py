#!/usr/bin/env python3
"""
gen_audio.py — one turnkey local engine for every Manaoke clip.

Reads builds/<key>.audio_jobs.json ([lang, spoken_text, out_rel]) and renders:
  - EN with Kokoro am_michael @0.95   (definitions, context, gloss, intros, explainers)
  - JP with Kokoro jf_alpha  @1.0     (words + full lines)
Loudnorm to I=-16 (single pass for preview speed), mp3 out (JP mono 80k per the
lean-build contract; EN mp3). English jobs are hard-refused if they contain CJK.

This is the PREVIEW voice: fully local, no Apple/Google, validate-clean. The
production JP voice (the Irodori clone) can be swapped in on promote; naming is
identical so the swap is a re-render, not a code change.

Run with the parler python (needs kokoro):
  /opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python gen_audio.py <key> <asset_folder>
"""
import hashlib, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
# Model loads (faster-whisper read-back, Kokoro) must not phone home when the
# caches are warm — doctor verifies cache presence; unset/0 to allow downloads.
os.environ.setdefault('HF_HUB_OFFLINE', '1')

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BUILDS = HERE / 'builds'
HUMAN = ROOT / 'tools' / 'human_audio'
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import jp_token_detect  # shared JP-token detector (Task B EN-splice)


def _sha8(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:8]
CJK = re.compile(r'[぀-ヿ㐀-鿿ｦ-ﾟ]')

# Lone grammatical particles (surface form) that Kokoro jf_alpha renders badly in
# isolation — a bare に came out as "いろいろ". We NEVER let Kokoro voice one of
# these on its own: prefer a curated human dictionary clip, else fail loudly and
# tell the operator to cut it with the Qwen3 Ono_Anna carrier pipeline.
# Note: は/へ/を never reach here as surface chars — their jp_speak is already the
# spoken form わ/え/お (a full syllable Kokoro handles), which is not in this set.
LONE_PARTICLES = set('にとものがでやかねよなへをはぞさお')
# NOTE: お (the SPOKEN form of を) is in the set too — Kokoro mangles a lone お
# exactly like a lone に (whisper hears hallucination noise); curated clip at
# tools/human_audio/library/お.mp3 (human 尾 recording). わ/え (spoken は/へ)
# stay OUT: Kokoro's わ passed the whisper read-back audit across songs.
STRIP_KANA = re.compile(r'[。、，．！？!?,.\sーっッ]')

# Small kana that MERGE with the preceding mora (ゃゅょ + small vowels) — they are
# not counted as their own mora. STRIP_KANA already drops 長音ー and っ/ッ; what
# remains, minus these smalls, is the mora count. Used to decide "short word"
# (<=2 morae) routing + provenance.
SMALL_MORA = set('ゃゅょャュョぁぃぅぇぉァィゥェォ')
# surface particles whose DICTIONARY headword reading (ha/he/wo) != the spoken
# reading (wa/e/o). fetch.py/tofugu.py already refuse them; the library cache is
# keyed by the SPOKEN reading (は__わ.mp3) so a library-exact hit there is safe.
BAD_PARTICLES = {'は', 'へ', 'を'}

# Site-wide pronunciation lexicon: the MEMORY the whisper read-back feeds. A
# word listed here was caught mispronounced once and must NEVER ship from TTS
# again, on any song (tools/songcraft/PRONUNCIATION-POLICY.md). Keyed by the
# FOLDED spoken kana (_fold_kana below); validate_song.py E15 is the gate.
LEXICON_PATH = HERE / 'pronunciation_lexicon.json'
_FOLD_SMALL = str.maketrans('ぁぃぅぇぉ', 'あいうえお')


def _fold_kana(kana):
    """The lexicon key form: STRIP_KANA then widen small vowels (same fold the
    read-back gate uses)."""
    return STRIP_KANA.sub('', kana or '').translate(_FOLD_SMALL)


def load_lexicon():
    """words map of pronunciation_lexicon.json (folded spoken kana -> entry),
    {} when the file is absent/unreadable (absence = nothing listed, not an error)."""
    try:
        return json.loads(LEXICON_PATH.read_text()).get('words', {})
    except Exception:
        return {}


def _mora_count(kana):
    """Morae in a reading: drop punctuation/長音/っ (STRIP_KANA), then count kana,
    merging small ゃゅょ / small vowels into the preceding mora."""
    k = STRIP_KANA.sub('', kana or '')
    return sum(1 for ch in k if ch not in SMALL_MORA)


def _library_lookup(surface, kana, exts=('.mp3', '.wav')):
    """Dual-form library/ READ via tools/human_audio/fetch.library_lookup:
    tries '<surface>__<kana>' THEN the slug() form (bare '<surface>' when
    surface==kana). Both name forms exist historically — library/の__の.mp3 was
    unreachable under the old slug()-only read. Returns path str or None."""
    import importlib
    if str(HUMAN) not in sys.path:
        sys.path.insert(0, str(HUMAN))
    try:
        return importlib.import_module('fetch').library_lookup(surface, kana, exts=exts)
    except Exception:
        return None


_HA_CORPUS = None
def _ha_corpus():
    """tools/human_audio/corpus.py (the offline NHK/yomichan resolver), loaded
    by explicit file path — a bare `import corpus` could bind the local-only
    tools/songcraft/corpus/ wav dir as a namespace package instead, depending
    on sys.path order. Memoized so the ~1s index build happens once."""
    global _HA_CORPUS
    if _HA_CORPUS is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'manaoke_ha_corpus', str(HUMAN / 'corpus.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _HA_CORPUS = mod
    return _HA_CORPUS


def _dict_word_clip(surface, kana):
    """Dictionary-priority lookup for a study WORD (not a lone particle):
    library/ dual-form read -> corpus.py resolve (offline NHK/yomichan chain:
    nhk16 > shinmeikai8 > forvo > jpod, kanji+kana exact / homophone-safe) ->
    fetch.py fetch_one(surface,kana) [JPod101 online] -> tofugu.py
    fetch_one(surface,kana) [offline WaniKani corpus]. Returns (path, src_desc)
    on a hit, else (None, None). Every source refuses は/へ/を internally, so
    only their library-exact (spoken-reading は__わ) clip can serve those."""
    import importlib
    p = _library_lookup(surface, kana)
    if p:
        return p, f'library/{Path(p).name} (dict cache)'
    try:
        cpath, cnote = _ha_corpus().resolve(surface, kana)
        if cpath:
            return cpath, cnote            # e.g. 'nhk16:20170726161104.mp3 野【の】'
    except (Exception, SystemExit):
        pass                               # corpus absent/unreadable -> next source
    if str(HUMAN) not in sys.path:
        sys.path.insert(0, str(HUMAN))
    for modname in ('fetch', 'tofugu'):
        try:
            mod = importlib.import_module(modname)
            r = mod.fetch_one(surface, kana)      # fetch:(st,path) tofugu:(st,path,note)
            path = r[1] if isinstance(r, tuple) and len(r) >= 2 else (r if isinstance(r, str) else None)
            if path and Path(path).exists():
                return path, f'{modname}:{Path(path).name}'
        except Exception:
            continue
    return None, None


def _curated_particle_clip(mora):
    """Return (path, src_desc) for a curated human recording of a lone particle
    `mora`, or (None, None). Order: library dual-form read -> corpus.py (the
    offline NHK/yomichan chain — NHK has bare の as 野【の】) -> JPod101 online
    -> Tofugu offline. Every source refuses は/へ/を (headword reading ha/he/wo
    != spoken wa/e/o), and their spoken-reading library clips (は__わ.mp3) are
    keyed by kana != mora so library_lookup(mora, mora) can't hit them either —
    the fail-loudly-never-TTS rule downstream stands."""
    import sys as _sys, importlib
    if str(HUMAN) not in _sys.path:
        _sys.path.insert(0, str(HUMAN))
    p = _library_lookup(mora, mora)
    if p:
        return p, f'library/{Path(p).name}'
    try:
        cpath, cnote = _ha_corpus().resolve(mora, mora)
        if cpath:
            return cpath, cnote
    except (Exception, SystemExit):
        pass
    for modname in ('fetch', 'tofugu'):
        try:
            mod = importlib.import_module(modname)
            r = mod.fetch_one(mora, mora)      # kanji==kana==the lone mora
            path = r[1] if isinstance(r, tuple) else (r if isinstance(r, str) else None)
            if path and Path(path).exists():
                return path, f'library/{Path(path).name}'
        except Exception:
            continue
    return None, None



# Strict read-back gate for SHORT dict-miss words: Kokoro renders of 1-2 mora
# words are exactly where TTS quietly ships garbage (いい->"いえ", どっか->"どうか",
# こと->"こど" all passed the small-model spot check). Any kokoro_dictmiss render
# must survive a large-v3 read-back whose folded-hira transcription matches the
# word's kana; otherwise the build FAILS with the Qwen carrier-cut remediation.
# For <=2-mora words pass strict=True: the match must be EXACT equality, not
# containment — containment is the gate hole that shipped a clip actually
# saying これでいい on both shinunoga いい cards (2026-07-07, backlog 0bd85bd1).
_WM_LARGE = None
def _readback_ok(path, kana, strict=False):
    global _WM_LARGE
    from faster_whisper import WhisperModel
    import jaconv, re as _re
    from pykakasi import kakasi as _kk
    if _WM_LARGE is None:
        _WM_LARGE = WhisperModel('large-v3', device='cpu', compute_type='int8')
    segs, _ = _WM_LARGE.transcribe(str(path), language='ja', vad_filter=False,
                                   beam_size=5, condition_on_previous_text=False)
    heard = ''.join(s.text for s in segs).strip()

    # Normalize BOTH sides to bare hiragana. The expected `kana` is word_meta's
    # spoken form, which is the KANJI surface whenever jp_speak == jp — comparing
    # 祭り against whisper's hira could never match, so correct renders of plain
    # kanji words (祭り/電脳/理想, ハレ heard as 晴れ) were rejected as garbage
    # (found on the ema build, 2026-07-07).
    # Whisper sometimes renders a kana particle as an Arabic numeral (the NHK
    # human に transcribes as '2' every pass) — kakasi passes digits through
    # and the kana filter strips them, so a CORRECT clip failed the gate.
    # Fold digits to PER-DIGIT kana readings first ('25'→にご, never the
    # place-value にじゅうご, so digit strings can't counterfeit real words).
    # Mirrors install_word.py's READBACK_SRC fix (2026-07-07).
    _READINGS = ['ぜろ', 'いち', 'に', 'さん', 'よん',
                 'ご', 'ろく', 'なな', 'はち', 'きゅう']
    _DIGITS = {ord(a): b for digits in ('0123456789', '０１２３４５６７８９')
               for a, b in zip(digits, _READINGS)}
    # Short Latin letter runs (<=3) fold to Japanese letter-name readings the
    # same way (whisper hears オーエルさん as 'OLさん'); longer runs are real
    # English words and stay un-folded so letters can't counterfeit kana.
    _LNAMES = {'a': 'えー', 'b': 'びー', 'c': 'しー', 'd': 'でぃー', 'e': 'いー',
               'f': 'えふ', 'g': 'じー', 'h': 'えいち', 'i': 'あい', 'j': 'じぇー',
               'k': 'けー', 'l': 'える', 'm': 'えむ', 'n': 'えぬ', 'o': 'おー',
               'p': 'ぴー', 'q': 'きゅー', 'r': 'あーる', 's': 'えす', 't': 'てぃー',
               'u': 'ゆー', 'v': 'ぶい', 'w': 'だぶりゅー', 'x': 'えっくす',
               'y': 'わい', 'z': 'ぜっと'}

    def _hira(s):
        s = _re.sub(r'(?<![A-Za-z])[A-Za-z]{1,3}(?![A-Za-z])',
                    lambda m: ''.join(_LNAMES[c] for c in m.group(0).lower()),
                    s.translate(_DIGITS))
        conv = _kk().convert(s)
        return _re.sub(r'[^ぁ-ゖー]', '', jaconv.kata2hira(''.join(d['hira'] for d in conv)))
    h, k = _hira(heard), _hira(kana)
    fold = lambda x: x.translate(str.maketrans('ぁぃぅぇぉ', 'あいうえお'))
    eq = fold(h) == fold(k)
    return bool(k) and (eq if strict else (fold(k) in fold(h) or eq)), heard


# Acoustic clip physics (tools/human_audio/clip_physics.py): duration + envelope
# vs the reading. Catches what transcription can't hear — truncated long vowels
# that read back fine, cut windows that caught the next word's onset, silent or
# over-long takes. 'fail' never ships; 'suspect' ships but is surfaced for the
# ear (Denmoku words-tab strip + validate_song E19 warning).
_CLIP_PHYSICS = None
def _physics(path, kana):
    """(verdict, reasons) for a rendered/copied JP clip — 'pass'/'suspect'/'fail'."""
    global _CLIP_PHYSICS
    if _CLIP_PHYSICS is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'manaoke_clip_physics', str(HUMAN / 'clip_physics.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CLIP_PHYSICS = mod
    verdict, reasons, _prof = _CLIP_PHYSICS.judge(_CLIP_PHYSICS.measure(path), kana)
    return verdict, '; '.join(reasons)


# --- Citation clips (Task B round 2) -----------------------------------------
# A podcast/explainer may QUOTE a citation/derived form that is not any study
# word's exact rom (dictionary form "hajikeru" for vocab hajikete; the bare
# conditional "tara" out of yarinaosetara). Those still must be spoken by a JP
# voice. Each such token gets its own clip at jp/podcast_<token>.mp3, rendered
# under the same short-word rules as study words: dictionary chain first, then
# Kokoro jf_alpha only if >2 morae or passing the large-v3 read-back gate.
_CIT_PIPE = None
KANA_ONLY = re.compile(r'^[ぁ-ゖァ-ヺー]+$')


def render_citation_clip(key, folder, token, jp_voice='jf_alpha', prov_dict=None):
    """Render (or reuse) the JP clip for a romaji citation token. Returns the
    Path on success, None on failure (caller must FAIL LOUDLY). Records
    provenance under jp/podcast_<slug>.mp3 — into `prov_dict` when the caller
    owns the manifest lifecycle (gen_audio main saves at the end), else straight
    into builds/<key>.clip_provenance.json (load-then-update)."""
    global _CIT_PIPE
    import jaconv
    slug = token.lower().replace(' ', '-')
    out_rel = f'jp/podcast_{slug}.mp3'
    out = ROOT / 'songs' / '_assets' / folder / 'audio' / out_rel
    prov_path = BUILDS / f'{key}.clip_provenance.json'
    if prov_dict is not None:
        prov = prov_dict
    else:
        try:
            prov = json.loads(prov_path.read_text())
        except Exception:
            prov = {}
    if out.exists() and out_rel in prov:
        return out
    kana = jaconv.alphabet2kana(token.lower().replace(' ', ''))
    if not KANA_ONLY.match(kana):
        print(f'  ✗ citation token {token!r} -> {kana!r} is not clean kana; '
              f'fix the token or render by hand.', file=sys.stderr)
        return None

    def _save(source, src):
        prov[out_rel] = {'source': source, 'src': src, 'sha8': _sha8(out),
                         'kana': kana, 'token': token}
        if prov_dict is None:
            json.dump(prov, open(prov_path, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)

    # 1. dictionary chain (surface = kana)
    clip, srcdesc = _dict_word_clip(kana, kana)
    if clip:
        ok, err = loudnorm_mp3(Path(clip), out, mono=True, bitrate='80k')
        if ok:
            verdict, why = _physics(out, kana)
            if verdict == 'fail':
                out.unlink(missing_ok=True)
                print(f'  ✗ citation {token!r} ({kana}) dictionary clip {srcdesc} '
                      f'fails clip physics: {why}. Audition another candidate '
                      f'(Denmoku Words tab) or fix the library clip.', file=sys.stderr)
                return None
            if verdict == 'suspect':
                print(f'  ⚠ citation {token} ({kana}) physics: {why} — worth a listen')
            _save('curated', srcdesc)
            print(f'  citation {token} ({kana}) <- dict {srcdesc}')
            return out
        print(f'  ffmpeg fail {out_rel}: {err}', file=sys.stderr)
        return None
    # 1.5 lexicon-listed token: read-back caught this word once — never Kokoro.
    lex = load_lexicon().get(_fold_kana(kana))
    if lex is not None:
        pin = (HUMAN / lex['clip']) if lex.get('clip') else None
        if pin is not None and pin.exists():
            ok, err = loudnorm_mp3(pin, out, mono=True, bitrate='80k')
            if ok:
                verdict, why = _physics(out, kana)
                if verdict == 'fail':
                    out.unlink(missing_ok=True)
                    print(f'  ✗ citation {token!r} ({kana}) lexicon-pinned clip '
                          f'{lex["clip"]} fails clip physics: {why}. Re-pin a clean '
                          f'take (Denmoku Words tab / install_word.py).', file=sys.stderr)
                    return None
                if verdict == 'suspect':
                    print(f'  ⚠ citation {token} ({kana}) physics: {why} — worth a listen')
                _save('curated', f'lexicon pinned {lex["clip"]}')
                print(f'  citation {token} ({kana}) <- lexicon pinned clip {lex["clip"]}')
                return out
            print(f'  ffmpeg fail {out_rel}: {err}', file=sys.stderr)
            return None
        carrier = lex.get('carrier') or f'<carrier phrase containing {kana}>'
        print(f'  ✗ citation token {token!r} ({kana}) dict-missed and is in the '
              f'pronunciation lexicon ({lex.get("reason", "listed")}) — never TTS it. '
              f'Cut a real voice with phrase_cut.py using carrier {carrier!r} '
              f'(PHRASE_CUT_OUT=songs/_assets/{folder}/audio/jp).', file=sys.stderr)
        return None
    # 2. Kokoro jf_alpha — gated: >2 morae, or the large-v3 read-back must
    #    corroborate the kana (short Kokoro renders are where garbage ships).
    try:
        import soundfile as sf
        import numpy as np
        from kokoro import KPipeline
        if _CIT_PIPE is None:
            _CIT_PIPE = KPipeline(lang_code='j', repo_id='hexgrad/Kokoro-82M')
        chunks = [a for _, _, a in _CIT_PIPE(kana, voice=jp_voice, speed=1.0)]
        if not chunks:
            return None
        wav = Path(tempfile.gettempdir()) / f'_cit_{key}_{slug}.wav'
        sf.write(str(wav), np.concatenate(chunks) if len(chunks) > 1 else chunks[0], 24000)
        ok, err = loudnorm_mp3(wav, out, mono=True, bitrate='80k')
        wav.unlink(missing_ok=True)
        if not ok:
            print(f'  ffmpeg fail {out_rel}: {err}', file=sys.stderr)
            return None
        verdict, why = _physics(out, kana)
        if verdict != 'pass':
            # Synthetic renders get no 'suspect' leniency — Kokoro can't be
            # worth-a-listen, it either produced a clean take or it didn't.
            out.unlink(missing_ok=True)
            print(f'  ✗ citation {token!r} ({kana}) Kokoro take fails clip physics: '
                  f'{why}. Cut a real voice with phrase_cut.py '
                  f'(PHRASE_CUT_OUT=songs/_assets/{folder}/audio/jp).', file=sys.stderr)
            return None
        rb_ok, heard = _readback_ok(out, kana, strict=_mora_count(kana) <= 2)
        if _mora_count(kana) <= 2 and not rb_ok:
            out.unlink(missing_ok=True)
            print(f'  ✗ citation {token!r} ({kana}) dict-missed AND Kokoro read back as '
                  f'{heard!r} — cut a real voice with phrase_cut.py '
                  f'(PHRASE_CUT_OUT=songs/_assets/{folder}/audio/jp).', file=sys.stderr)
            return None
        _save('kokoro_dictmiss', f'kokoro {jp_voice} (citation, dict miss; '
                                 f'read-back {"ok" if rb_ok else heard!r})')
        print(f'  citation {token} ({kana}) <- kokoro {jp_voice} '
              f'(dict miss, read-back heard {heard!r})')
        return out
    except Exception as e:
        print(f'  ✗ citation synth fail {out_rel}: {type(e).__name__} {str(e)[:120]}',
              file=sys.stderr)
        return None


def loudnorm_mp3(wav_path, out_path, mono, bitrate):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    af = 'loudnorm=I=-16:TP=-1.5:LRA=11'
    cmd = ['ffmpeg', '-y', '-i', str(wav_path), '-af', af]
    if mono:
        cmd += ['-ac', '1', '-ar', '48000', '-c:a', 'libmp3lame', '-b:a', bitrate]
    else:
        cmd += ['-ar', '44100', '-c:a', 'libmp3lame', '-b:a', bitrate]
    cmd.append(str(out_path))
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stderr[-400:]


def main(key, folder, jp_voice='jf_alpha'):
    import soundfile as sf
    import numpy as np
    from kokoro import KPipeline
    jobs = json.loads((BUILDS / f'{key}.audio_jobs.json').read_text())
    out_base = ROOT / 'songs' / '_assets' / folder / 'audio'
    pipes = {}

    # word_meta (out_rel -> {surface,kana,rom}) drives dictionary-priority routing
    # for JP study-word clips (Task A). Absent for legacy songs -> no dict routing.
    try:
        word_meta = json.loads((BUILDS / f'{key}.word_meta.json').read_text())
    except Exception:
        word_meta = {}

    # Pronunciation lexicon (site-wide): a listed word never falls through to
    # Kokoro, however it reaches us — study word, legacy job, or lone particle.
    lexicon = load_lexicon()

    # Song vocab for the EN-splice detector (Task B): an EN clip must never have
    # the English voice READ a romaji study word — we splice the real JP clip in.
    try:
        content = json.loads((BUILDS / f'{key}.content.json').read_text())
        vocab = jp_token_detect.build_vocab_from_content(content)
    except Exception:
        vocab = []

    # en_splice manifest (out_rel -> [tokens spliced]) — load-then-update so a
    # partial re-render doesn't drop prior records. validate_tts_safety E11 reads it.
    splice_path = BUILDS / f'{key}.en_splice.json'
    try:
        en_splice = json.loads(splice_path.read_text())
    except Exception:
        en_splice = {}

    # Render all JP jobs BEFORE any EN job so every word clip an EN splice needs
    # already exists on disk (a deleted-and-not-yet-rendered clip would otherwise
    # break the splice). Stable within each language group.
    jobs = sorted(jobs, key=lambda j: 0 if j[0] == 'ja' else 1)

    # Provenance manifest (builds/<key>.clip_provenance.json): records the SOURCE
    # of every rendered JP clip so tools/validate_song.py E9 can prove no
    # lone-particle clip is Kokoro-voiced. We load-then-update (never clobber),
    # so qwen/curated entries baked by tools/human_audio/phrase_cut.py or a
    # provenance regeneration survive a re-render of the OTHER clips.
    prov_path = BUILDS / f'{key}.clip_provenance.json'
    try:
        prov = json.loads(prov_path.read_text())
    except Exception:
        prov = {}

    def record(out_rel, out_path, source, src, kana=None):
        # ja word clips carry their spoken kana so the provenance manifest is
        # self-describing (validate_song E15 sweeps it against the lexicon).
        old_ent = prov.get(out_rel) or {}
        prov[out_rel] = {'source': source, 'src': src, 'sha8': _sha8(out_path)}
        if kana:
            prov[out_rel]['kana'] = kana
        # an ear-approved physics waiver survives a re-render ONLY when the
        # bytes are identical — a waiver blesses a specific take, not a slot
        if old_ent.get('physics_waiver') and old_ent.get('sha8') == prov[out_rel]['sha8']:
            prov[out_rel]['physics_waiver'] = old_ent['physics_waiver']

    def lexicon_route(kana_spoken, surface, out, out_rel):
        """HOOK for lexicon-listed words after a dict miss: use the pinned clip
        if the entry has one, else fail loudly with the carrier-bearing
        phrase_cut remediation. Returns 'done'/'failed' when the lexicon owned
        the job, None when the word is not listed (caller falls through)."""
        lex = lexicon.get(_fold_kana(kana_spoken))
        if lex is None:
            return None
        pin = (HUMAN / lex['clip']) if lex.get('clip') else None
        if pin is not None and pin.exists():
            ok, err = loudnorm_mp3(pin, out, mono=True, bitrate='80k')
            if ok:
                verdict, why = _physics(out, kana_spoken)
                old_ent = prov.get(out_rel) or {}
                waived = (old_ent.get('physics_waiver')
                          and old_ent.get('sha8') == _sha8(out))
                if verdict == 'fail' and not waived:
                    out.unlink(missing_ok=True)
                    print(f'  ✗ {surface!r} ({kana_spoken}) lexicon-pinned clip '
                          f'{lex["clip"]} fails clip physics: {why}. Re-pin a clean '
                          f'take (Denmoku Words tab / install_word.py).', file=sys.stderr)
                    return 'failed'
                if verdict == 'suspect':
                    print(f'  ⚠ word {surface} ({kana_spoken}) physics: {why} — worth a listen')
                record(out_rel, out, 'curated', f'lexicon pinned {lex["clip"]}', kana=kana_spoken)
                print(f'  word {surface} ({kana_spoken}) <- lexicon pinned clip {lex["clip"]}')
                return 'done'
            print(f'  ffmpeg fail {out_rel}: {err}', file=sys.stderr)
            return 'failed'
        stem = Path(out_rel).stem
        carrier = lex.get('carrier') or f'<carrier phrase containing {surface}>'
        print(f'  ✗ {surface!r} ({kana_spoken}) dict-missed and is in the pronunciation '
              f'lexicon ({lex.get("reason", "listed")}) — never TTS it. Cut a real voice:\n'
              f'      conda run -n parler python tools/human_audio/phrase_cut.py '
              f"'{surface}:{carrier}:{stem}'\n"
              f'    then install at {out_rel} with provenance qwen/aivis.', file=sys.stderr)
        return 'failed'

    def pipe(lang):
        code = 'a' if lang == 'en' else 'j'
        if code not in pipes:
            pipes[code] = KPipeline(lang_code=code, repo_id='hexgrad/Kokoro-82M')
        return pipes[code]

    def _decode_pcm(src, dst, sr=24000):
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(src), '-ar', str(sr),
                        '-ac', '1', '-c:a', 'pcm_s16le', str(dst)], check=True)

    def _silence_pcm(dst, sr=24000, secs=0.08):
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        f'anullsrc=r={sr}:cl=mono', '-t', f'{secs:.3f}',
                        '-c:a', 'pcm_s16le', str(dst)], check=True)

    def render_en_spliced(text, spans, out, i):
        """Render an EN clip whose text cites romaji study words: EN prose through
        Kokoro am_michael, each JP token through its real JP clip, joined with ~80ms
        gaps, then one loudnorm to the standard EN mp3. Returns (ok, err, tokens)."""
        segs = []          # ('en', text) | ('clip', path)
        tokens = []
        cur = 0
        for s in spans:
            pre = text[cur:s['start']]
            if pre.strip():
                segs.append(('en', pre))
            if s.get('match', 'exact') == 'exact':
                rel = s['entry']['out_rel']
                clip = out_base / rel
                if not clip.exists():             # fallback: a library clip by kana
                    kana = s['entry'].get('kana')
                    surf = s['entry'].get('surface') or kana
                    lib = _library_lookup(surf, kana)   # dual-form read
                    clip = Path(lib) if lib else None
                if clip is None:
                    # The word's OWN clip is made by jp_audio, which runs after
                    # this step, so on a first build every cited study word
                    # depends on a file that does not exist yet. Most survive
                    # because the dictionary has them; an inflected form does
                    # not (舞って/matte, STRAWBERRY ANNIVERSARY 2026-07-30) and
                    # the whole explainer clip failed over one word. Cut it the
                    # same way a derived form is cut — dictionary first, TTS
                    # with read-back after. Never let the English voice fall
                    # back to reading the romaji itself: mangling it out loud
                    # is what this splice exists to prevent.
                    clip = render_citation_clip(key, folder, s['text'],
                                                jp_voice=jp_voice, prov_dict=prov)
            else:
                # citation/derived form (stem/suffix/shape) — its own clip,
                # rendered under the dictionary-first + read-back rules.
                rel = f'jp/podcast_{s["token"].replace(" ", "-")}.mp3'
                clip = out_base / rel
                if not (clip.exists() and rel in prov):
                    clip = render_citation_clip(key, folder, s['token'],
                                                jp_voice=jp_voice, prov_dict=prov)
            if clip is None:
                return False, (f'EN-splice FAIL {out_rel}: no JP clip for token '
                               f'{s["text"]!r} (wanted {rel})'), None
            segs.append(('clip', str(clip)))
            tokens.append(s['text'])
            cur = s['end']
        tail = text[cur:]
        if tail.strip():
            segs.append(('en', tail))
        if not segs:
            return False, f'EN-splice FAIL {out_rel}: no segments', None
        # decode every segment (and interleaved silence) to canonical 24k mono PCM,
        # concat losslessly, then a single loudnorm + mp3 encode (EN 128k format).
        parts = []
        for j, (kind, val) in enumerate(segs):
            pcm = tmp / f'_ensp_{key}_{i}_{j}.wav'
            if kind == 'en':
                chunks = [a for _, _, a in pipe('en')(val, voice='am_michael', speed=0.95)]
                if not chunks:
                    continue
                raw = tmp / f'_ensr_{key}_{i}_{j}.wav'
                sf.write(str(raw), np.concatenate(chunks) if len(chunks) > 1 else chunks[0], 24000)
                _decode_pcm(raw, pcm); raw.unlink(missing_ok=True)
            else:
                _decode_pcm(val, pcm)
            parts.append(pcm)
            if j < len(segs) - 1:
                gap = tmp / f'_ensg_{key}_{i}_{j}.wav'
                _silence_pcm(gap); parts.append(gap)
        listtxt = tmp / f'_enl_{key}_{i}.txt'
        listtxt.write_text(''.join(f"file '{p}'\n" for p in parts))
        combined = tmp / f'_enc_{key}_{i}.wav'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0',
                        '-i', str(listtxt), '-c:a', 'pcm_s16le', str(combined)], check=True)
        ok, err = loudnorm_mp3(combined, out, mono=False, bitrate='128k')
        for p in parts + [combined, listtxt]:
            Path(p).unlink(missing_ok=True)
        return ok, err, tokens

    done = skipped = failed = 0
    bad_cjk = []
    needs_qwen = []
    t0 = time.time()
    tmp = Path(tempfile.gettempdir())
    for i, (lang, text, out_rel) in enumerate(jobs):
        out = out_base / out_rel
        if out.exists():
            # A lexicon-listed word whose existing clip is Kokoro-voiced must be
            # deleted and re-routed here — otherwise adding a word to the
            # lexicon silently does nothing (skip-existing would keep the bad clip).
            _k = ((word_meta.get(out_rel) or {}).get('kana') or text) if lang == 'ja' else None
            if _k and _fold_kana(_k) in lexicon and \
                    prov.get(out_rel, {}).get('source') in ('kokoro', 'kokoro_dictmiss'):
                out.unlink()
                print(f'  re-route {out_rel}: lexicon-listed word had a '
                      f'{prov[out_rel]["source"]} clip — deleted, re-rendering')
            else:
                skipped += 1
                continue
        if lang == 'en' and CJK.search(text):
            bad_cjk.append((out_rel, text[:40]))
            failed += 1
            continue
        # EN clip that CITES a romaji study word -> splice the real JP clip in so
        # the English voice never says the Japanese (Task B, the owner's absolute rule).
        if lang == 'en':
            spans = jp_token_detect.detect(text, vocab) if vocab else []
            if spans:
                ok, err, tokens = render_en_spliced(text, spans, out, i)
                if ok:
                    done += 1
                    en_splice[out_rel] = tokens
                    print(f'  spliced EN {out_rel}: {tokens}')
                else:
                    failed += 1
                    print(f'  {err}', file=sys.stderr)
                continue
        # Lone particle -> route AROUND Kokoro (it mangles isolated particles).
        mora = STRIP_KANA.sub('', text)
        if lang == 'ja' and len(mora) == 1 and mora in LONE_PARTICLES:
            # A lexicon entry may PIN a specific clip for a particle; everything
            # else about particles stays with the curated/Qwen rules (E9 owns them).
            _lex = lexicon.get(_fold_kana(mora))
            pin = (HUMAN / _lex['clip']) if (_lex and _lex.get('clip')) else None
            if pin is not None and pin.exists():
                src, src_desc = str(pin), f'lexicon pinned {_lex["clip"]}'
            else:
                src, src_desc = _curated_particle_clip(mora)
            if src:
                ok, err = loudnorm_mp3(Path(src), out, mono=True, bitrate='80k')
                if ok:
                    done += 1
                    record(out_rel, out, 'curated', src_desc, kana=mora)
                    print(f'  particle {mora} <- curated dict clip ({src_desc})')
                else:
                    failed += 1
                    print(f'  ffmpeg fail {out_rel}: {err}', file=sys.stderr)
                continue
            # No curated clip: DO NOT hand a lone particle to Kokoro. Fail loudly
            # with the Qwen3 Ono_Anna carrier-cut remediation.
            needs_qwen.append((out_rel, mora))
            failed += 1
            stem = Path(out_rel).stem
            print(f'  ✗ lone particle {mora!r} has no curated dict clip and Kokoro '
                  f'mangles it. Cut it with Qwen3 Ono_Anna:\n'
                  f'      conda run -n parler python tools/human_audio/phrase_cut.py '
                  f"'{mora}:<carrier phrase containing {mora}>:{stem}'\n"
                  f'    then jp_to_mp3 + place at {out_rel}', file=sys.stderr)
            continue
        # JP study WORD -> dictionary-priority routing (Task A): try the human
        # voice dictionaries before Kokoro. On a miss, a SHORT word (<=2 morae)
        # falls back to Kokoro but is tagged 'kokoro_dictmiss' so the validator can
        # prove the lookup ran; a long word is plain 'kokoro' as before.
        ja_kokoro_source = 'kokoro'
        wm = word_meta.get(out_rel) if lang == 'ja' else None
        if wm is not None:
            surface = wm.get('surface') or text
            kana = wm.get('kana') or text
            clip, srcdesc = _dict_word_clip(surface, kana)
            if clip:
                ok, err = loudnorm_mp3(Path(clip), out, mono=True, bitrate='80k')
                if ok:
                    verdict, why = _physics(out, kana)
                    old_ent = prov.get(out_rel) or {}
                    waived = (old_ent.get('physics_waiver')
                              and old_ent.get('sha8') == _sha8(out))
                    if verdict == 'fail' and not waived:
                        out.unlink(missing_ok=True)
                        failed += 1
                        print(f'  ✗ word {surface!r} ({kana}) dictionary clip {srcdesc} '
                              f'fails clip physics: {why}. Audition another candidate '
                              f'(Denmoku Words tab / install_word.py).', file=sys.stderr)
                        continue
                    if verdict == 'suspect':
                        print(f'  ⚠ word {surface} ({kana}) physics: {why} — worth a listen')
                    done += 1
                    record(out_rel, out, 'curated', srcdesc, kana=kana)
                    print(f'  word {surface} ({kana}) <- dict {srcdesc}')
                else:
                    failed += 1
                    print(f'  ffmpeg fail {out_rel}: {err}', file=sys.stderr)
                continue
            # HOOK A: dict missed — a lexicon-listed word must never reach Kokoro.
            routed = lexicon_route(kana, surface, out, out_rel)
            if routed:
                done += (routed == 'done'); failed += (routed == 'failed')
                continue
            if _mora_count(kana) <= 2:
                ja_kokoro_source = 'kokoro_dictmiss'
        elif lang == 'ja':
            # Legacy song (no word_meta -> no dict routing): still honor the
            # lexicon, matched on the raw job text (already the spoken form).
            routed = lexicon_route(text, text, out, out_rel)
            if routed:
                done += (routed == 'done'); failed += (routed == 'failed')
                continue
        voice = 'am_michael' if lang == 'en' else jp_voice
        speed = 0.95 if lang == 'en' else 1.0
        try:
            chunks = [audio for _, _, audio in pipe(lang)(text, voice=voice, speed=speed)]
            if not chunks:
                failed += 1
                continue
            audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
            wav = tmp / f'_ka_{key}_{i}.wav'
            sf.write(str(wav), audio, 24000)
            mono = (lang == 'ja')
            ok, err = loudnorm_mp3(wav, out, mono=mono, bitrate=('80k' if mono else '128k'))
            wav.unlink(missing_ok=True)
            if ok:
                done += 1
                if lang == 'ja':
                    # A JP clip voiced by Kokoro. 'kokoro_dictmiss' when a short
                    # word missed the dictionary (proves the lookup ran, for E12);
                    # plain 'kokoro' for long words / full lines. E9 only rejects a
                    # LONE PARTICLE whose source is kokoro.
                    note = (f'kokoro {jp_voice} (dict miss, <=2 morae)'
                            if ja_kokoro_source == 'kokoro_dictmiss' else f'kokoro {jp_voice}')
                    rb_kana = (wm or {}).get('kana') or text
                    verdict, why = _physics(out, rb_kana)
                    # WORD clips get no 'suspect' leniency — a Kokoro word take
                    # either comes out clean or gets re-routed. Full LINES are
                    # judged against word-calibrated thresholds, where a brisk
                    # natural line is routinely 'suspect' (silhouette2 line_u00
                    # etc.) — matching E19, a suspect line ships with a loud
                    # note and only a hard 'fail' blocks.
                    is_word = out_rel.startswith('jp/word_')
                    if verdict == 'fail' or (is_word and verdict == 'suspect'):
                        out.unlink(missing_ok=True)
                        done -= 1
                        failed += 1
                        if is_word:
                            stem = Path(out_rel).stem
                            print(f'  ✗ {text!r} Kokoro take fails clip physics: {why}.\n'
                                  f'    Cut a real voice:\n'
                                  f'      conda run -n parler python tools/human_audio/phrase_cut.py '
                                  f"'{text}:<carrier phrase containing {text}>:{stem}'\n"
                                  f'    then install at {out_rel} with provenance qwen/aivis.',
                                  file=sys.stderr)
                        else:
                            print(f'  ✗ line clip {out_rel} fails clip physics: {why}. '
                                  f'Re-run the step (Kokoro takes vary); if it persists, '
                                  f'check the line text/reading.', file=sys.stderr)
                        continue
                    if verdict == 'suspect':
                        print(f'  ⚠ line clip {out_rel} physics: {why} — ships, '
                              f'worth a listen')
                    if ja_kokoro_source == 'kokoro_dictmiss':
                        rb_ok, heard = _readback_ok(out, rb_kana, strict=True)
                        if not rb_ok:
                            out.unlink(missing_ok=True)
                            done -= 1
                            failed += 1
                            stem = Path(out_rel).stem
                            print(f'  ✗ short word {text!r} dict-missed AND Kokoro read back as '
                                  f'{heard!r} — never ship it. Cut a real voice:\n'
                                  f'      conda run -n parler python tools/human_audio/phrase_cut.py '
                                  f"'{text}:<carrier phrase containing {text}>:{stem}'\n"
                                  f'    then install at {out_rel} with provenance qwen/aivis, and add\n'
                                  f'    it to the site-wide lexicon so it can never regress to TTS:\n'
                                  f'      python3 tools/songcraft/manaoke_build.py lexicon add {text} '
                                  f'--kana {rb_kana} --reason "read-back heard {heard}"', file=sys.stderr)
                            continue
                    record(out_rel, out, ja_kokoro_source, note,
                           kana=(wm or {}).get('kana') or text)
            else:
                failed += 1
                print(f'  ffmpeg fail {out_rel}: {err}', file=sys.stderr)
        except Exception as e:
            failed += 1
            print(f'  synth fail {out_rel}: {type(e).__name__} {str(e)[:120]}', file=sys.stderr)
        if (done + failed) % 25 == 0 and (done + failed):
            print(f'  ... {done} done, {skipped} skip, {failed} fail, {time.time()-t0:.0f}s', flush=True)
    if prov:
        prov_path.parent.mkdir(parents=True, exist_ok=True)
        json.dump(prov, open(prov_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'  provenance -> {prov_path.name} ({len(prov)} entries)')
    if en_splice:
        json.dump(en_splice, open(splice_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'  en_splice  -> {splice_path.name} ({len(en_splice)} spliced EN clips)')
    print(f'\nDONE: {done} rendered, {skipped} skipped(existing), {failed} failed in {time.time()-t0:.0f}s')
    print(f'  -> {out_base}')
    if bad_cjk:
        print(f'  ⚠ {len(bad_cjk)} EN jobs had CJK (skipped, would fail safety): {bad_cjk[:3]}')
    if needs_qwen:
        print(f'  ✗ {len(needs_qwen)} lone particle(s) need a curated/Qwen clip (never Kokoro): '
              f'{[m for _, m in needs_qwen]}')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv)>3 else 'jf_alpha'))
