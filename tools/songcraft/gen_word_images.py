#!/usr/bin/env python3
"""Silhouette scene images — same dual-coding design as inochi but a new
visual identity: the narrator is a teenage boy, the energy is motion and
sky (sunset silhouettes, wind, leaves) instead of interior melancholy.
Output: songs/_assets/silhouette/images/word_<secId>_<romUid>.webp (416x608).
"""
import gc, shutil, time
from pathlib import Path
import torch
from diffusers import StableDiffusionXLPipeline, EulerAncestralDiscreteScheduler
from PIL import Image

OUT = Path('<repo>/songs/_assets/silhouette/images')
OUT.mkdir(parents=True, exist_ok=True)
RAW = Path('<repo>/.local-preview/REFINE-2026-06-11/silhouette/images_raw')
RAW.mkdir(parents=True, exist_ok=True)

MODEL = 'cagliostrolab/animagine-xl-4.0'
QUALITY = 'masterpiece, best quality, very aesthetic'
STYLE = ('dusk and sunset tones, ember orange and deep blue palette, dynamic, '
         'nostalgic anime film still, wind, subtle film grain')
HIM = '1boy, japanese teenage boy, short messy dark hair, determined gentle eyes'
NEG = ('lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, '
      'fewer digits, cropped, worst quality, low quality, jpeg artifacts, signature, '
      'watermark, username, blurry, logo, speech bubble, multiple views, nsfw')

WORDS = [
    ('title', 'shiruetto',          'group of friends as dark silhouettes against a blazing orange sunset sky, standing on a hill, wind'),
    ('intro', 'issee-noo-se',       'group of japanese kids crouched side by side at a chalk start line ready to sprint, side view, dusk schoolyard, excited grins'),
    ('intro', 'goo-rain',           'a white chalk start line on asphalt, worn sneakers toeing the line, long evening shadows'),
    ('intro', 'fumikomu',           'sneaker mid-stride crossing a chalk line, low angle close up, dust kicking up, sunset light'),
    ('intro', 'furikaeru',          f'{HIM}, mid-run glancing back over his shoulder, hair blown by wind, long road behind, dusk'),
    ('pc1', 'ase',                  f'{HIM}, profile close up, a bead of sweat catching golden hour light on his jaw, out of breath, glowing'),
    ('chorus', 'daiji-ni-shiteta',  'still life, an open wooden keepsake box on a desk, glass marbles and a small medal and folded photographs inside, warm desk lamp light, cozy bedroom at night'),
    ('chorus', 'furi-wo-shita',     f'{HIM}, looking away with hands in pockets, an old photograph lying on the ground behind him, overcast dusk'),
    ('v2', 'shounen',               'a small boy in a summer field reaching both arms toward the bright sky, cicada summer light, nostalgic'),
    ('v2', 'hoshigatta',            'a child\'s hand reaching up toward a glowing toy on a high shop shelf, warm shop light, night outside'),
    ('v2', 'tokei',                 'a big round wall clock close up, the second hand motion-blurred, late afternoon light'),
    ('v2', 'hibi',                  f'{HIM}, standing still while a crowd rushes past in motion blur, city crossing, dusk'),
    ('pc2', 'ubatteku',             'wind tearing pages out of an open notebook held in two hands, pages scattering into a dusk sky'),
    ('pc2', 'kioku',                'faded polaroid photographs scattered on wooden floorboards, low warm light, dust motes'),
    ('bridge', 'ko-no-ha',          'green leaves swirling and dancing mid-air against a deep dusk sky, no people, wind'),
    ('bridge', 'shousou',           f'{HIM}, sitting hunched gripping his knees in a dim blue-shadowed room, city lights through window, restless'),
    ('outro', 'kienu',              'a row of human silhouettes fading into dusk, one silhouette still glowing warm ember orange, hilltop'),
    ('outro', 'mamori-tsuzukeyou',  '1boy, japanese teenage boy, cupping a tiny glowing firefly in his hands, warm light glowing on his gentle face, dark blue night background, close up'),
    ('outro', 'otona-ni-naru',      f'{HIM} walking a long road at dawn beside his own taller adult shadow stretched ahead, hopeful'),
    ('outro', 'tonde-yuku',         'leaves lifting off the ground and ascending into a bright morning sky, sun rays, ascending feeling'),
]
# フリをした also appears in ch2 — same image, copied to its section filename.
COPIES = [('chorus', 'furi-wo-shita', 'ch2', 'furi-wo-shita')]

def main():
    t0 = time.time()
    pipe = StableDiffusionXLPipeline.from_pretrained(MODEL, torch_dtype=torch.float16, use_safetensors=True)
    pipe.scheduler = EulerAncestralDiscreteScheduler.from_config(pipe.scheduler.config)
    pipe = pipe.to('mps')
    print(f'pipeline loaded {time.time()-t0:.0f}s', flush=True)
    g = torch.Generator('cpu').manual_seed(20260613)
    for i, (sec, uid, core) in enumerate(WORDS, 1):
        out = OUT / f'word_{sec}_{uid}.webp'
        if out.exists(): print(f'skip {out.name}', flush=True); continue
        t = time.time()
        img = pipe(prompt=f'{core}, {STYLE}, {QUALITY}', negative_prompt=NEG,
                   width=832, height=1216, num_inference_steps=26,
                   guidance_scale=5.5, generator=g).images[0]
        img.save(RAW / f'word_{sec}_{uid}.png')
        img.resize((416, 608), Image.LANCZOS).save(out, 'WEBP', quality=82)
        print(f'[{i}/{len(WORDS)}] {out.name} {time.time()-t:.0f}s', flush=True)
        gc.collect(); torch.mps.empty_cache()
    for s1, u1, s2, u2 in COPIES:
        src, dst = OUT / f'word_{s1}_{u1}.webp', OUT / f'word_{s2}_{u2}.webp'
        if src.exists() and not dst.exists(): shutil.copy(src, dst); print('copied', dst.name, flush=True)
    print(f'ALL DONE in {(time.time()-t0)/60:.1f} min', flush=True)

main()
