#!/usr/bin/env python3
"""
verify_jp_pronunciation.py — whisper read-back of the JP study clips, with a
human-vocal-dictionary fallback when the TTS mispronounces a word.

the owner's rule: "we are doing a whisper reading of the words in the pronunciations;
if text-to-speech is not cutting it on the note cards or in the study book, we
pull from the vocal dictionary." This transcribes each JP word clip and compares
what Whisper heard to the word's expected kana reading. A low match flags a
mispronunciation. With --fix, flagged words are replaced by a real human
recording from the vocal dictionary (tools/human_audio: JapanesePod101 online,
then the offline WaniKani/Tofugu corpus), loudnormed to the served mono-80k mp3.

Run with the parler python:
  python verify_jp_pronunciation.py <slug> <asset_folder> [--fix] [--model base] [--thresh 0.5]
"""
import argparse, difflib, hashlib, json, os, re, subprocess, sys, time
from pathlib import Path
# Warm-cache model loads stay offline (doctor checks the caches; HF_HUB_OFFLINE=0 to override).
os.environ.setdefault('HF_HUB_OFFLINE', '1')

ROOT = Path(__file__).resolve().parents[2]
HUMAN = ROOT / 'tools' / 'human_audio'
sys.path.insert(0, str(HUMAN))
try:
    import pyopenjtalk, jaconv
except Exception:
    pyopenjtalk = None

PARTICLES = set('はへを')
STRIP = re.compile(r'[。、，．！？!?,.\sーっッ]')
# Mirror of gen_audio.py: lone particles are gated by E9/curated clips, never
# the lexicon; small-vowel fold makes the lexicon key form.
LONE_PARTICLES = set('にとものがでやかねよなへをはぞさお')
FOLD_SMALL = str.maketrans('ぁぃぅぇぉ', 'あいうえお')
LEXICON_PATH = ROOT / 'tools' / 'songcraft' / 'pronunciation_lexicon.json'


def rom_uid(rom):
    return re.sub(r'^-+|-+$', '', str(rom or '').replace(' ', '-').replace('·', '').replace('/', '_'))


def hira(text):
    try:
        return jaconv.kata2hira(pyopenjtalk.g2p(text or '', kana=True))
    except Exception:
        return jaconv.kata2hira(text or '')


def norm(s):
    # convert through the READING so a kanji transcription (Whisper often writes
    # 鏡 not かがみ) compares phonetically, not by surface form — else every
    # kanji-transcribed clip false-flags as a mispronunciation.
    return STRIP.sub('', hira(s or ''))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('slug'); ap.add_argument('folder')
    ap.add_argument('--fix', action='store_true'); ap.add_argument('--model', default='base')
    ap.add_argument('--thresh', type=float, default=0.5)
    ap.add_argument('--add-to-lexicon', action='store_true',
                    help='record each flagged word in the site-wide pronunciation '
                         'lexicon so it can never regress to TTS')
    a = ap.parse_args()
    data = json.loads((ROOT / 'songs' / a.slug / 'data.json').read_text())
    jp_dir = ROOT / 'songs' / '_assets' / a.folder / 'audio' / 'jp'

    from faster_whisper import WhisperModel
    model = WhisperModel(a.model, device='cpu', compute_type='int8')

    seen = set()
    rows = []
    for sec in data['sections']:
        sid = sec['id']
        for w in sec.get('words', []):
            uid = rom_uid(w.get('uid') or w['rom'])
            clip = jp_dir / f'word_{sid}_{uid}.mp3'
            if not clip.exists() or str(clip) in seen:
                continue
            seen.add(str(clip))
            jp = w['jp']
            jp_speak = w.get('jp_speak') or jp
            # PARTICLES ARE NOW AUDITED. For は/へ/を the spoken form is わ/え/お and
            # that IS already the authored jp_speak, so norm(jp_speak) compares
            # against the correct sound — no special-casing needed here.
            expected = norm(jp_speak)
            # Two transcriptions (greedy + beam) so a single noisy read on a
            # sub-second clip can't false-flag: we keep the BEST match and only
            # fail when BOTH attempts agree the sound is wrong (corroboration).
            heards = []
            for bs in (1, 5):
                segs, _ = model.transcribe(str(clip), language='ja', vad_filter=False, beam_size=bs)
                heards.append(norm(''.join(s.text for s in segs)))
            ratios = [difflib.SequenceMatcher(None, expected, h).ratio() if expected else 1.0
                      for h in heards]
            ratio = max(ratios)
            all_empty = all(not h for h in heards)   # whisper heard nothing = too short, not "wrong"
            rows.append((sid, uid, jp, jp_speak, expected, '/'.join(heards), ratio,
                         bool(w.get('particle')), all_empty))

    def eff_thresh(exp):
        # 1-mora words (most particles) are the noisiest for whisper on sub-second
        # clips; require a clearly-wrong read before flagging one.
        return a.thresh if len(exp) > 1 else min(a.thresh, 0.34)

    flagged = [r for r in rows
               if r[4] and not r[8]                 # has an expected reading, whisper heard SOMETHING
               and r[6] < eff_thresh(r[4])]         # both attempts corroborate a wrong sound
    print(f'read-back: {len(rows)} clips, {len(flagged)} flagged (both attempts < threshold)')
    for sid, uid, jp, sp, exp, heard, ratio, part, _ in flagged:
        tag = ' [particle]' if part else ''
        print(f'  ⚠ {jp} ({sp}){tag} [{sid}/{uid}]  expected {exp!r} heard {heard!r}  ({ratio:.2f})')

    # --add-to-lexicon: the detector FEEDS the memory. Each flagged word goes in
    # the site-wide lexicon (keyed by folded spoken kana) so gen_audio can never
    # TTS it again and validate_song E15 gates every future build. Lone
    # particles stay out — E9 + the curated-clip system owns them.
    if a.add_to_lexicon and flagged:
        try:
            lex = json.loads(LEXICON_PATH.read_text())
        except Exception:
            lex = {'version': 1, 'words': {}}
        words = lex.setdefault('words', {})
        added = 0
        for sid, uid, jp, sp, exp, heard, ratio, _part, _empty in flagged:
            key = exp.translate(FOLD_SMALL)
            if not key or key in words or (len(key) == 1 and key in LONE_PARTICLES):
                continue
            words[key] = {'surface': jp, 'kana': hira(sp), 'carrier': None,
                          'clip': None,
                          'reason': f'whisper heard {heard} expected {exp}',
                          'added': time.strftime('%Y-%m-%d'), 'fed_by': 'readback'}
            added += 1
            print(f'  + lexicon: {key} ({jp}) — never TTS again')
        if added:
            LEXICON_PATH.write_text(json.dumps(lex, ensure_ascii=False, indent=1) + '\n')
            print(f'lexicon: {added} word(s) added -> {LEXICON_PATH.name} '
                  f'(fill in each carrier before the next phrase_cut remediation)')

    if not a.fix or not flagged:
        if flagged and not a.fix:
            print('\n(pass --fix to replace flagged clips from the vocal dictionary)')
        return

    # --fix: pull human recordings for the flagged words
    import fetch as jpod, importlib
    try:
        tofugu = importlib.import_module('tofugu')
    except Exception:
        tofugu = None
    # NHK/yomichan local corpus — loaded by explicit path (a bare `import
    # corpus` can bind tools/songcraft/corpus/, the gitignored wav dir).
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location('manaoke_ha_corpus',
                                             str(HUMAN / 'corpus.py'))
        corpus = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(corpus)
    except Exception:
        corpus = None
    # provenance is owned by install_word.py now (b3ac8de1) — no side-channel
    # bookkeeping here; the installer stamps source/sha8/kana itself.
    fixed = 0
    for sid, uid, jp, sp, exp, heard, ratio, _part, _empty in flagged:
        # は/へ/を as particles are spoken わ/え/お; the vocal dictionary only has
        # their HEADWORD reading (ha/he/wo), so NEVER pull a dict clip for them —
        # keep the TTS わ/え/お clip. (fetch/tofugu also refuse these, belt+braces.)
        if jp in ('は', 'へ', 'を'):
            print(f'    keep TTS for particle {jp} (spoken わ/え/お; no valid dict clip)')
            continue
        kana = hira(jp)
        # LOCAL-FIRST source order: cached library → yomichan corpus (NHK >
        # shinmeikai > forvo > local-jpod) → tofugu offline → JPod101 ONLINE
        # last (the endpoint duplicates the local jpod tier; network is the
        # fallback, not the default).
        lib = jpod.library_lookup(jp, kana)
        if not lib and corpus is not None:
            try:
                cpath, cnote = corpus.resolve(jp, kana)
            except (Exception, SystemExit):
                cpath = None
            if cpath:
                dest = HUMAN / 'library' / (jpod.slug(jp, kana) + '.mp3')
                if not dest.exists():
                    import shutil as _sh
                    _sh.copyfile(cpath, dest)
                lib = str(dest)
                print(f'    corpus hit {jp}: {cnote}')
        if not lib and tofugu:
            try:
                r = tofugu.fetch_one(jp, kana)
                lib = r if isinstance(r, str) else (r.get('path') if isinstance(r, dict) else None)
            except Exception:
                pass
        if not lib:
            try:
                r = jpod.fetch_one(jp, kana)
                lib = r if isinstance(r, str) else (r.get('path') if isinstance(r, dict) else None)
            except Exception as e:
                print(f'    jpod miss {jp}: {e}')
        # accept ONLY an exact (kanji, kana) match — never a loose glob, which
        # matched 子(こ) to 子供(こども) and made the clip say the wrong word.
        # Dual-form read (fetch.library_lookup): '<jp>__<kana>.mp3' AND the bare
        # '<jp>.mp3' slug() form both count as exact — two historical name forms
        # coexist in library/. A fresh fetch_one download above lands under the
        # slug() name, so the lookup covers it too.
        src = jpod.library_lookup(jp, kana)
        if not src:
            print(f'    no exact vocal-dict entry for {jp} ({kana})')
            continue
        # ONE sanctioned installer (b3ac8de1): the old bespoke ffmpeg swap here
        # did single-pass loudnorm straight onto the served mp3 — no wav-master
        # update (a stale master kept feeding downstream consumers the OLD
        # take), side-channel provenance, no read-back verify, no rollback.
        # install_word.py owns all of that: two-pass loudnorm → wav MASTER →
        # served mp3 → provenance → read-back verify with .bak restore on
        # mismatch. This --fix runs as an AUTO step inside the build DAG, so a
        # failed install must never strand the song: install_word restores the
        # previous clip and we just report the miss.
        r = subprocess.run(['python3', str(HUMAN / 'install_word.py'),
                            '--song', a.folder, '--sec', sid, '--rom', uid,
                            '--src', str(src), '--kana', kana,
                            '--source', 'curated'],
                           capture_output=True, text=True)
        if r.returncode == 0:
            fixed += 1
            print(f'    ✓ {jp} <- human recording ({Path(src).name}) via install_word')
        else:
            tail = (r.stdout + r.stderr).strip().splitlines()
            print(f'    install_word REFUSED {jp} ({Path(src).name}): '
                  f'{tail[-1] if tail else "no output"} — previous clip kept, not swapped')
    if fixed:
        print(f'  provenance updated by install_word ({fixed} entr'
              f'{"y" if fixed == 1 else "ies"})')
    print(f'\nfixed {fixed}/{len(flagged)} from the vocal dictionary. '
          f'Re-run build_drill_concat + deploy if any changed.')


if __name__ == '__main__':
    main()
