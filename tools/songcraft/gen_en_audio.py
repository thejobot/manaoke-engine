#!/usr/bin/env python3
"""Silhouette EN audio: Kokoro am_michael @0.95 (production recipe), two-pass
loudnorm to I=-16 TP=-1.5 LRA=11, mp3 out. Builds the manifest entries for
sections + line explainers (key == spoken text, byte-identical).
Run with the kokoro venv python.
"""
import hashlib, json, re, subprocess, sys, time
from pathlib import Path

BASE = Path('<repo>/.local-preview/REFINE-2026-06-11/silhouette')
OUT = Path('<repo>/songs/_assets/silhouette/audio/en')
OUT.mkdir(parents=True, exist_ok=True)
TMP = BASE / 'wav_tmp'; TMP.mkdir(exist_ok=True)

CJK = re.compile(r'[　-ヿ㐀-鿿＀-￯]')
MAC = re.compile(r'[āēīōūĀĒĪŌŪ]')

def rom_uid(rom):
    return re.sub(r'^-+|-+$', '', str(rom or '').replace(' ', '-').replace('·', '').replace('/', '_'))

d = json.load(open(BASE / 'data.draft.json'))
maps = json.load(open(BASE / 'line_maps.draft.json'))

jobs = []   # (spoken_text, out_name)
manifest = []  # [lang, key, spoken, filename]
for s in d['sections']:
    if s.get('speak_en'):
        fn = f"section_{s['id']}_intro.mp3"
        jobs.append((s['speak_en'], fn))
        manifest.append(['en-US', s['speak_en'], s['speak_en'], f'audio/en/{fn}'])
    for w in s['words']:
        uid = rom_uid(w['rom'])
        if w.get('en_speak'):
            jobs.append((w['en_speak'], f"word_{s['id']}_{uid}_en.mp3"))
        ctx_spoken = w.get('context_speak') or w.get('context')
        if ctx_spoken:
            jobs.append((ctx_spoken, f"word_{s['id']}_{uid}_ctx.mp3"))
for text in dict.fromkeys(maps['LINE_EXPLAIN'].values()):
    h = hashlib.sha1(text.encode()).hexdigest()[:8]
    fn = f'line_{h}_explain.mp3'
    jobs.append((text, fn))
    manifest.append(['en-US', text, text, f'audio/en/{fn}'])

bad = [(t[:50], f) for t, f in jobs if CJK.search(t) or MAC.search(t)]
if bad:
    print('TTS-SAFETY VIOLATIONS:', *bad, sep='\n')
    sys.exit(1)
print(f'{len(jobs)} clips to generate, {len(manifest)} manifest entries', flush=True)

from kokoro import KPipeline
import soundfile as sf
import numpy as np
pipe = KPipeline(lang_code='a')

def loudnorm(wav, mp3):
    p1 = subprocess.run(['ffmpeg', '-y', '-i', str(wav), '-af',
        'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'],
        capture_output=True, text=True)
    j = json.loads(p1.stderr[p1.stderr.rindex('{'):p1.stderr.rindex('}')+1])
    af = (f"loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:"
          f"measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
          f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:"
          f"offset={j['target_offset']}")
    subprocess.run(['ffmpeg', '-y', '-i', str(wav), '-af', af,
        '-codec:a', 'libmp3lame', '-q:a', '2', str(mp3)],
        capture_output=True, check=True)

t0 = time.time()
done = 0
for text, name in jobs:
    mp3 = OUT / name
    if mp3.exists():
        done += 1; continue
    chunks = [a for _, _, a in pipe(text, voice='am_michael', speed=0.95)]
    audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    wav = TMP / (name.replace('.mp3', '.wav'))
    sf.write(str(wav), audio, 24000)
    loudnorm(wav, mp3)
    wav.unlink()
    done += 1
    if done % 20 == 0:
        print(f'[{done}/{len(jobs)}] {name} ({time.time()-t0:.0f}s)', flush=True)

json.dump(manifest, open(BASE / 'tts_manifest.en.json', 'w'), ensure_ascii=False, indent=1)
print(f'DONE {done}/{len(jobs)} clips in {(time.time()-t0)/60:.1f} min; manifest -> tts_manifest.en.json', flush=True)
