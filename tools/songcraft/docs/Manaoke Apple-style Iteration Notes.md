# Manaoke Apple-style Renderer — Iteration Notes

Live URLs observed via the same CDP rig that dismantled Apple Music.

| Slug | URL | What changed | Status |
|---|---|---|---|
| e3mkjf | https://manaoke.app/songs/inochi-mijikashi-e3mkjf/ | First cut. Apple per-letter mechanism, bottom-right icon, Sing/Study modes. | BUG: rendered kana readings ('お') instead of original kanji ('汚') for non-1:1 lines. |
| htdg9s | https://manaoke.app/songs/inochi-mijikashi-htdg9s/ | Fix. Main row uses words[].text (preserves kanji), romaji grouped per word. | Animation verified end-to-end via fake-time CDP probe. Distance opacity binary, entry subtle, sokuon raw. |
| 4iuaps | https://manaoke.app/songs/inochi-mijikashi-4iuaps/ | Entry animation stretched + arcs. Distance-staged opacity (1 / .55 / .32 / .18). Icon flash replaces pulse ring. Skip empty-rom kana. | Entry blur visible. Distance gradient working. Sokuon FIX MISSED (rom field has the kana itself, not empty). |
| 3pyqd0 | https://manaoke.app/songs/inochi-mijikashi-3pyqd0/ | Sokuon dot placeholder. Trail-glow rises from icon to entering line. Icon flash warmer + bigger (scale 1.18, warm tint). Stricter isValidRom rejects Japanese-script chars. | Current. Sokuon dot rendering correctly between "ika" and "te" for L2. Entry animation visibly blurred mid-flight. Trail glow visible as warm tint rising mid-screen at transition. |


## What I verified works on htdg9s

Driving the page via CDP with `window.getCurrentMs` overridden to fake-time, then sampling computed styles per letter and rendering screenshots:

- Per-letter wipe (Apple mechanism): rAF loop writes inline `style` on each `.letter` every frame. Each letter carries `transform: matrix(scale,...,dy)` plus `--gp` (gradient progress), `--tso` (text-shadow opacity), `--tsb` (text-shadow blur). The CSS rule paints a two-stop linear-gradient via `background-clip: text` so the wipe travels left → right per character. Verified live: at t=12500ms in line 1, char "な" is at progress 100% (fully sung), char "ん" is at progress 57.39% with scale 1.040 and shadow opacity 0.835 (mid-bounce, glowing), char "ぼ" is at progress -20% (off-canvas, not started). Exactly Apple's curve.
- The romaji row twins the main row: when "ん" is at 57.62%, the romaji "n" is at 57.62%. Same JS loop, parallel CHARS array.
- Line state machine: `.is-current` (active) → `.is-past` (faded out) → `.is-current` again on the new line. Single transition managed inside the rAF tick.
- Auto-scroll to current line in Sing mode with a 600ms SEEK_GUARD so user scroll doesn't fight the auto-center.
- Mode toggle (SING ↔ STUDY) flips classes + colors the dot (green → yellow) + halts playback when entering Study.
- Note card popup from the bottom-right icon. Tapping the icon opens a card scaled from `scale(.05) translate(60px, 60px)` to identity over 350ms. Card shows the active char + its romaji + the line context + timing (e.g. "た / ta / なんぼ汚れたアタシでも / 14.00s → 14.33s (334ms)").
- Mobile viewport (390×844 iPhone-class). 26px main + 13px sup at narrow widths, 30px main + 14px sup default.
- The bottom-right icon is fixed-positioned with `position:fixed; right:14px; bottom:calc(14px + safe-area-inset-bottom)`. Stays visible in both modes.


## What's not great yet — fix in next slug

1. The "popFromIcon" entry animation is too fast to feel intentional. The line ENTERS from the icon position via a `translate(icon-x - 50vw, icon-y - 50vh) scale(.18)` keyframe, but it completes in 550ms and the scale starts at 18% — too big a starting scale for the eye to track. the owner wanted lyrics to feel like they emerge from the icon, but right now the animation reads as "fade in" rather than "fly out of icon". Fix: start at scale(0.04), slow to ~900ms, add a more pronounced curve so the line arcs from icon location.

2. Sokuon (small "っ") renders as raw kana in the romaji row because the kana_timings entry for っ has empty `rom`. Looks ugly mixed in with Latin letters. Fix: either omit the .letter span entirely for empty rom, or treat っ as a doubled-consonant marker on the next syllable.

3. The icon pulse on line change is too subtle to read as feedback. The `iconPulse` keyframe is a 1.0s ring expanding to 1.5× scale with opacity fading 0.55 → 0. Hard to see at small icon size. Fix: make the icon BORDER pulse instead of the outer ring, OR briefly scale the icon itself 1.0 → 1.15 → 1.0 with a color flash.

4. Upcoming lines (below the current) are at opacity 0.32 — readable but very dim. The contrast between current (opacity 1) and upcoming (0.32) is a 3× jump, which feels too binary. Apple's contrast is more gradual: current=1.0, n±1=0.5, n±2=0.3. Fix: stage the dim by distance from current.

5. The translation row is empty for this song (data has no translations). Apple shows a third row of English when present, so the UI hierarchy supports it. Either add LLM-generated translations to data.json or hide the row container entirely (currently a no-op since the conditional `if (line.translation && line.translation.trim())` correctly skips empty ones, but worth flagging).

6. The icon position drives an entry animation but isn't itself a UI surface that shows the active text. the owner's design idea was that the current lyric "pops out of the icon" — could be interpreted as: the icon ITSELF expands into the active lyric on activation, or a tiny version of the current line lives in the icon and grows to full size. Current implementation is more like "lyric flies from icon location" — the icon stays static. Worth a design conversation.

7. The "の" / "は" particles are split into their own .word spans because they're separate words[] entries in the TTML. This makes the romaji read as "kodomo no goroha kawai" — "go ro ha" is one word because TTML chunked "頃は" together. Faithful to data, but reads a bit oddly. Could split particles into their own words[].


## What I learned from comparing to Apple

| Aspect | Apple's web renderer | htdg9s |
|---|---|---|
| Animation driver | rAF + per-letter style.cssText write | Same |
| Wipe primitive | `linear-gradient(90deg, A var(--gp), B calc(var(--gp)+20%))` over `background-clip: text` | Same |
| Bounce | scale 1.0 → 1.047 (peak ~85% prog) → 1.0; dy 0 → -2px (stays) | scale 1.0 → 1.045 (peak ~50% prog) → 1.0; dy 0 → -2px (stays) |
| Pre-glow | text-shadow ramp before progress > 0 | Same, 180ms window |
| Furigana positioning | HTML5 `<ruby>` + `<rt>` (browser auto-positions) | Flat: separate `.main` + `.sup` divs |
| Active line scale | 1.1× | 1.06× |
| Inter-line gap | 30px (margin-top) | 30px (margin-top) ✓ |
| Font sizes | 22 / 15 / 13 px (main/sup/trans) | 30 / 14 / 13 px (main/sup/trans) — main slightly larger because htdg9s is full-screen, not 300px drawer |
| Voice/duet | `is-secondary-vocalist` flips align right | Not implemented (single voice for this song) |
| Mixed JP/EN | English kept verbatim, JP transliterated | Single-language data, not exercised |
| Layout | 300px drawer or full-screen | Always full-screen |
| Bottom-right icon | Speech-bubble for lyric mode toggle | Speech-bubble — but Manaoke uses it for note popup + entry origin |


## Captured files

- `/tmp/amll-probe/htdg9s-anim-12500.png` — mid-L1 wipe captured (the gem screenshot)
- `/tmp/amll-probe/htdg9s-anim-14500.png` — wipe progressed further
- `/tmp/amll-probe/htdg9s-anim-20000.png` — L2 active, L1 faded
- `/tmp/amll-probe/htdg9s-card.png` — note card open over L1
- `/tmp/amll-probe/htdg9s-study.png` — Study mode (yellow dot)
- `/tmp/amll-probe/htdg9s-375.png` — narrow viewport (iPhone SE class)


## Next slug priorities

1. Slow + exaggerate the popFromIcon entry animation. Make it visibly arc from icon to position.
2. Hide or replace empty-rom sokuon (っ) in romaji row.
3. Distance-staged opacity for non-current lines (n±1 = .5, n±2 = .3, n±3+ = .15).
4. More visible icon feedback on line change (icon pulse + brief scale).
5. Optional: render the current line preview INSIDE the icon (the "lyric lives in the icon" interpretation).
