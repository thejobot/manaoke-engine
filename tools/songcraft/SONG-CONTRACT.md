# The Manaoke Song Contract & Pipeline

**Canonical reference: `<repo>/songs/inochi-mijikashi-v091/`.** "Identical" means a new song (or an update to an existing one) reproduces this build's markup strings, CSS values, data shape, audio naming/cadence, manifest contract, and deploy mechanics **byte-for-byte** — same selectors, same labels, same `data-*` attributes, same clip filenames, same drill timing, same validators passing at 0. The lean canonical dir is just `index.html` + `data.json` + `tts_manifest.json` (+ a `timestamp-recorder/` dev tool); all audio/pitch/images live once at `songs/_assets/inochi-mijikashi/` and are served to every build dir by the `_redirects` catch-all. **Two things are NOT in `data.json`:** `LINE_TR` and `LINE_EXPLAIN` are large hardcoded JS object literals **inside `index.html`** (§2.8) — cloning the page and only swapping `data.json` leaves inochi's per-line translations and line explainers wired to inochi's lyrics, and a per-song `const YT_ID` (§4.2) is what actually drives the embedded video. Two linters gate every deploy and must exit 0: `tools/validate_song.py` and `tools/validate_tts_safety.py`; a third gate, `tools/songcraft/line_explainers.py check`, enforces the every-line drill-tail explainer standard (§2.8.1). **Current PROD = `inochi-mijikashi-v096`** — clone this (it carries the v095 car-glitch + load fixes: per-word + full-line JP clips are now **mono 80kbps mp3** (not 44.1k stereo WAV), referenced everywhere as `audio/jp/...mp3` and compressed via `tools/human_audio/jp_to_mp3.py` (the `.wav` master is kept); plus the word-by-word **stall watchdog**, **unmuted iOS unlock** primer, prewarm throttle/timeouts, removed 665KB `PROD_URL` self-fetch; and **v0.96 self-hosts subset fonts** (no Google Fonts CDN — §4.9). It also carries the every-line `LINE_EXPLAIN` standard §2.8, the §1.9 how-to QUICK GUIDE standard, AND the v094 Sing-pill chrome standard: always-on ⟳ repeat / 🐢 slow toggles that fill with an iOS-style `#EDECE6` chip matching the study-book active keys when active and tap to toggle on/off, hairline frame-colour (`rgba(255,255,255,.12)`) dividers between every pill element, and the play glyph sized (13px) to the clock text + toggle icons; play/pause itself stays a plain glyph). Line numbers below are cited against **v091** for stability; v092 adds a 3-line `// v092:` comment + 10 `LINE_EXPLAIN` entries (13 lines) after v091 line 6372, so any cited line number **below that block is +13 in v092**. Refs at/above the `LINE_EXPLAIN` declaration (≈6362) are unchanged. v093 additionally adds the §1.9 how-to block (≈+30 lines, across menu/popup CSS ~2355/2405, book-glow ~4600, self-highlight ~7805, `SECT` data ~10208) — below those anchors line numbers shift further, so locate by the quoted string not the number. v094 then reworks the Sing-pill chrome (topbar CSS ~4900–4920, the pill markup ~5598, and the play-icon mirror JS ~9780) — again, locate by the quoted string, not the line number.

> ⚠️ **STALE — the Word-by-Word drill changed in Round 14 (PROD is now `inochi-mijikashi-v098`, not v096).** §1.3–1.5 below still describe the SUPERSEDED *chained* drill (a separate `<audio>.play()` per clip, retries/watchdog, and the `.wc-jp::after` active-word ring). As of v097 the drill plays **ONE concatenated mp3 per line** (`audio/drill/line_<sha1(lineKey)[:8]>.mp3`, order: w1 JP, w1 EN gloss, w2 JP, w2 EN gloss, …, full JP line, EN explanation) with a **single `.play()` off the gesture**, driven by an inline `const DRILL_MAP` timing map (per-word lit-windows + tail), exactly like the pitch card. The active-word ring is gone; the reveal is the dim→bright+lift the lyric/pitch reveals already use. The old chained drill remains only as `_playWordDrillChained` fallback for lines with no concat.
>
> **Building/updating a song MUST run `tools/songcraft/build_drill_concat.py songs/<dir>` after any word-gloss / line-JP / explanation audio or data change** (it re-extracts the real drill order via `extract_drill.js` + puppeteer, ffmpeg-concats the lean mono-80k line files into `_assets/inochi-mijikashi/audio/drill/`, and re-injects `DRILL_MAP`). Skip it and the new/updated line ships with **no concat → silent fallback to the old iOS-flaky chained drill**. Also: **drill glosses (the `gloss` field, not `en_speak`) must be short natural phrases, never a bare function-word / pronoun** (の → "of, or belonging to", not "of"; アタシ → "a rough girl's word for herself", not "I, me") — a bare word sounds clipped in TTS. **This is now GATED: `validate_song.py` E7 fails the build on any gloss that is only bare function-words/pronouns**, driven by a growable `BARE_GLOSS_OFFENDERS` denylist (add a word when a new offender surfaces). Regenerating a gloss clip means re-running `build_drill_concat.py` so the concat picks it up. Prewarm now yields while `isPlaying` so the background audio download can't stall the song. Full detail: memory `project_manaoke_song_card.md` → "Round 14". §1.3–1.5 are pending a proper rewrite to this architecture.
>
> 🔒 **The concat is a SHARED asset but every page has its OWN inline `DRILL_MAP` — so rebuilding the concat instantly changes what the LIVE page plays, against its old timings. Two rules keep that from shipping a desynced drill:** (1) **never run a concat rebuild in two conversations on the same song at once** — the writes are global and instant-to-live, and one thread's rebuild will desync the other thread's live map. Read/UI work in parallel is fine; concat/clip regeneration is one thread at a time. (2) `build_drill_concat.py` now has a **live-sync guard**: it REFUSES to rebuild unless `song_dir` is the dir the root landing points at. To make a non-live dir live in the same run, pass **`--promote`** (rebuilds + patches + repoints root → atomic, live map always matches the shared audio). To override knowingly (accepting the live page is desynced until you promote), pass `--force`. This is the exact failure that shipped a desynced drill once.

---

## 1. Design contract — what every song must look and behave like

### 1.1 The study book (card BACK face)

Every non-instrumental lyric card emits this back face verbatim (card render, lines 6591–6600):

```html
<div class="card-face back" data-explain="${escapeAttr(explainerSpeak)}">
  <div class="card-meta">
    ${sectionLabel ? `<span class="card-section">${escapeHtml(sectionLabel)} · study</span>` : ''}
    <span class="card-time" style="margin-left:auto">${timeStr}</span>
  </div>
  ${briefHtml}
  <div class="study-wrap">${studyHtml}</div>
  <div class="card-translation labeled-pill ${ln.translation ? 'has-translation' : ''}" data-speak="${escapeHtml(ln.translation || '')}" data-en-parts="${escapeAttr(JSON.stringify(ln.translationSpeakParts || []))}" data-drill="${escapeAttr(JSON.stringify(ln.drillParts || []))}"><span class="pill-label">Word by Word</span></div>
  <div class="card-actions">${renderActions()}</div>
</div>
```

Load-bearing rules:

- **`data-explain` = `escapeAttr(explainerSpeak)`.** `explainerSpeak` (lines 6540–6545) = `LINE_EXPLAIN[lineTrKey(ln.text)] || ''`, never a section-overview fallback. **STANDARD (v0.92+): every lyric line has an entry**, so the drill tail always explains (§2.8); empty `data-explain` only when authoring is incomplete (caught by `line_explainers.py check`, §2.8.1). **`LINE_EXPLAIN` is a hardcoded object literal in `index.html` (§2.8), NOT in `data.json`** — a new song must re-author it or every `explainerSpeak` is `''`.
- `briefHtml` (line 6546) = `(sec && sec.speak_en) ? <play-group> : ''` — the section play buttons (incl. the JP full-line button that the drill tail speaks) exist **only when the section has `speak_en`** (see §1.3 and §2.2).
- `studyHtml` (line 6532) = `(ln.studyWords||[]).length ? renderWordGrid(ln.studyWords) : '<div class="study-empty">no study words for this line</div>'`, wrapped in `<div class="study-wrap">`.
- **Exactly ONE labeled pill** below the grid: `.card-translation.labeled-pill`, caption literally `Word by Word` in `<span class="pill-label">`.
- **The Explainer pill (`.card-section-overview`) is REMOVED** — no such element is emitted on the back face. Its CSS (lines 2428–2433, 4604–4634) is vestigial. Do **not** re-introduce it.
- **Escaping is not interchangeable:** `data-explain`, `data-en-parts`, `data-drill` use `escapeAttr` (line 6413: escapes `& < > " '`, incl. `'`→`&#39;`). `data-speak` uses `escapeHtml` (line 6412: escapes `& < > "` only, **not** `'`). Preserve exactly.
- The front face (line 6588) carries a parallel `.card-en-row` with the SAME `data-en-parts` and `data-drill`, plus the inline English `ln.enInline` (sourced from `LINE_TR`, §2.8): `<div class="card-en-row ${ln.enInline ? 'has-en' : ''}" data-speak="${escapeAttr(ln.enInline || '')}" …>${escapeHtml(ln.enInline || '')}</div>`.

CSS values that must hold:
- `.study-wrap` (line 2860): `flex:1;min-height:0;overflow-y:auto;-webkit-overflow-scrolling:touch;padding-block:12px;scroll-padding-block:12px`.
- `.labeled-pill` (4640–4654): `white-space:normal !important; min-height:0 !important; line-height:1.4 !important; text-align:center !important; padding:9px 14px !important; overflow:visible !important`.
- `.labeled-pill .pill-label`: `display:block;text-align:center;font-family:var(--f-mono);font-size:.74rem;font-weight:700;letter-spacing:.04em`.
- Open sheet hero sizing (4931–4932): `#cardSheet .card-face.back .wc-jp .wc-main` → `1.7rem`, `.wc-sub` → `.92rem`.

### 1.2 `renderWordGrid()` — markup + every `data-*` (lines 6884–6905)

```js
function renderWordGrid(words){
  let html = '<div class="word-grid">';
  words.forEach(w => {
    const partCls = w.particle ? ' particle' : '';
    const pitchKey = w.jp_speak || w.jp;
    const pitchClass = pitchClassFor(pitchKey);
    const rowAttrs = `data-pitch-key="${escapeAttr(pitchKey)}" data-rom="${escapeAttr(w.rom||'')}" data-uid="${escapeAttr(w.uid||'')}" data-particle="${w.particle ? '1' : ''}" data-section-id="${escapeAttr(w.sectionId||'')}" data-en="${escapeAttr(w.en||'')}" data-en-hint="${escapeAttr(w.hint||w.section||'')}" data-en-speak="${escapeAttr(w.en_speak||w.en||'')}" data-en-context="${escapeAttr(w.context||'')}"`;
    html += `
      <div class="word-row word-row--jp-only ${pitchClass}" ${rowAttrs}>
        <div class="wc wc-jp${partCls}" data-speak="${escapeAttr(w.jp_speak || w.jp)}">
          <div class="wc-main">${escapeHtml(w.jp)}</div>
          <div class="wc-sub">${escapeHtml(w.rom || '')}</div>
        </div>
      </div>`;
  });
  html += '</div>';
  return html;
}
```

- Grid `<div class="word-grid">`; each word `<div class="word-row word-row--jp-only ${pitchClass}">`. `word-row--jp-only` = single centered column (`grid-template-columns:1fr`, CSS 2869–2870). **No separate blue EN tile** — the English rides as `data-*`.
- `pitchClass` from `pitchClassFor(pitchKey)` (6924–6930): one of `pitch-heiban`/`pitch-atamadaka`/`pitch-nakadaka`/`pitch-odaka` (optionally `+ pitch-kansai`), or `pitch-unknown`/`pitch-pending`. Drives `.word-row .wc-jp` underline color (CSS 4171–4178).
- Row `data-*`, in order: `data-pitch-key`(=`jp_speak||jp`), `data-rom`, `data-uid`, `data-particle`(`'1'`|`''`), `data-section-id`, `data-en`, `data-en-hint`(=`hint||section`), `data-en-speak`(=`en_speak||en`), `data-en-context`(=`context`).
- Inner tile `<div class="wc wc-jp${partCls}" data-speak="...">`; a particle adds class `particle` (orange, CSS 2875–2876, 2880). `data-speak` is what tap-to-hear speaks.
- Two lines: `.wc-main`=`jp` (escapeHtml), `.wc-sub`=`rom` (escapeHtml).

### 1.3 `playWordDrill(parts, el)` — cadence, retries, tail (lines 9377–9519)

Header contract (9368–9376): **per word: JP clip → 350ms → EN gloss → 700ms → next word; then full line; then explainer.**

- Re-tap same pill cancels (9378). Any other running seq cancelled; `currentAudio`/`speechSynthesis` stopped; `pauseSongForVocab()`. Empty `parts` → no-op.
- `grid = el.closest('.card-face.back')`; `rows = grid.querySelectorAll('.word-row')` (1:1 with parts).
- Per word `step()` (9481–9515): `rowOn(n)` adds `drill-hot` first (eye arrives) → `after(200)` plays JP `playUrl(jpUrl, ()=>after(350,gloss), gloss, {retries:2})` → on JP-done +350ms `gloss()` plays `glossUrl` `{retries:1}` (onSkip speaks `p.g` via `SpeechSynthesisUtterance` **only when `!HAS_CJK(p.g)`**, lines 9501–9507) → `glossEnd()` +380ms `rowOff(n)`; last → `playTail()`, else `after(500, step)` (so next JP at +500+200 = full **700ms** gap).
- Dim CSS: `.card-face.back.drill-live .word-row{opacity:.30}`; `.drill-hot{opacity:1;transform:translateY(-2px) scale(1.03)}` (5021–5028) + `.wc-jp::after` ring (5031–5036).
- TAIL (`playTail()`, 9471–9480): **550ms → full JP line (`_lineJpUrl`, {retries:1}) → 450ms → English explanation (`_ovUrl`, {retries:1}) → finish().** Each stage skips gracefully if its cached URL is absent.

```js
const playTail = () => {
  if (cancelled) return;
  const stageExplain = () => {
    if (cancelled) return;
    if (_ovUrl){ after(450, () => playUrl(_ovUrl, finish, finish, { retries: 1 })); }
    else finish();
  };
  if (_lineJpUrl){ after(550, () => playUrl(_lineJpUrl, stageExplain, stageExplain, { retries: 1 })); }
  else stageExplain();
};
```

- **Tail clip resolution (the load-bearing subtlety — corrected, 9399–9407):**

  ```js
  const _lineJpEl   = grid ? grid.querySelector('.brief-play--jp') : null;
  const _lineJpText = _lineJpEl ? (_lineJpEl.dataset.speak || '') : '';
  const _lineJpUrl  = _lineJpText ? audioCache['ja-JP:' + _lineJpText] : null;   // full JP line
  const _ovText     = grid ? (grid.dataset.explain || '') : '';
  const _ovUrl      = _ovText ? audioCache['en-US:' + _ovText] : null;            // explainer
  ```

  `_lineJpUrl` is **NOT** read straight from `audioCache['ja-JP:' + ln.text]`. It is resolved from the `.brief-play--jp` button's `data-speak` (which equals `escapeAttr(ln.text)`, line 6549) found inside this back face. **`.brief-play--jp` is only emitted when `sec.speak_en` is truthy** (`briefHtml`, line 6546). So **a section without `speak_en` has no JP play button → `_lineJpEl` is null → `_lineJpUrl` is null → the drill tail's full-line-JP stage silently skips** (it jumps straight to the explainer). Practical rule: every section whose cards should speak the full JP line at the drill tail **must** have a `sec.speak_en` (which also supplies the section-intro clip, §2.2). `_ovUrl` comes from `grid.dataset.explain` (= `escapeAttr(explainerSpeak)`, sourced from `LINE_EXPLAIN`, §2.8) → needs an `en-US` manifest entry whose key == the `LINE_EXPLAIN` value, or the explainer stage skips.
- Drill clip URLs (`step()`, 9487–9489): `jpUrl = audio/jp/word_${p.s}_${_romUid(p.u)}.mp3`; `glossUrl = audio/en/word_${p.s}_${_romUid(p.u)}_gloss.mp3`.
- Kickoff (9517–9518): `grid.classList.add('drill-live'); after(50, step)` — dims, JP 1 at t0+250.

`playUrl` RETRY (9429–9466) — the fix for "JP word wouldn't load but English would":

```js
const maxRetries = (opts && opts.retries) || 0;
let tries = 0;
const attempt = () => {
  if (cancelled) return;
  const a = _drillClip(url);
  activeAudio = a;
  let settled = false;
  const clear = () => { a.onended = null; a.onerror = null; };
  const retryOrSkip = () => {
    if (settled) return; settled = true; clear();
    if (activeAudio === a) activeAudio = null;
    if (tries <= maxRetries && !cancelled){
      CLIP_POOL.delete(url);          // drop poisoned pooled element
      after(180 * tries, attempt);    // backoff, re-fetch
    } else onSkip();
  };
  a.onended = () => { if (settled) return; settled = true; clear(); if (activeAudio === a) activeAudio = null; done(); };
  a.onerror = retryOrSkip;
  tries++;
  const pr = a.play();
  if (pr && pr.catch) pr.catch(retryOrSkip);
};
attempt();
```

**Retry counts: JP word = `{retries:2}` (9514); EN gloss = `{retries:1}` (9508); tail full-line = `{retries:1}` (9478); tail explanation = `{retries:1}` (9475).** Guarantees JP sounds first; one network blip never drops straight to English.

`finish()` removes `playing`, `trackStop()` (clears `drill-hot`+`drill-live`), nulls `currentSeq`. `cancel()` sets `cancelled`, clears timers, pauses `activeAudio`, cancels speech, runs `finish()`. Killed by `stopVocabAudio` on swipe/sheet-close/song-start.

### 1.4 `speakTranslationPill(el)` — drill wins (lines 9531–9553)

```js
function speakTranslationPill(el){
  let _drill = [];
  try { _drill = JSON.parse(el.dataset.drill || '[]'); } catch(_){}
  if (Array.isArray(_drill) && _drill.length) return playWordDrill(_drill, el);
  const fullText = el.dataset.speak || '';
  if (fullText && audioCache['en-US:' + fullText]){ return speakEN(fullText, el); }
  let parts = [];
  try { parts = JSON.parse(el.dataset.enParts || '[]'); } catch(_){}
  parts = (Array.isArray(parts) ? parts : []).filter(Boolean);
  if (parts.length) return playSequentialEN(parts, el);
  if (fullText) return speakEN(fullText, el);
}
```

Non-empty `data-drill` → `playWordDrill` and return; legacy paths run only for old drill-less data. Wired at 6657–6658 / 6722–6731: fires on click of `.card-translation.has-translation` with `SFX.tap()`.

### 1.5 Boot reliability scaffolding (lines 9582–9672)

Exists because the first JP word would skip to the English gloss — caused by (1) `Clear-Site-Data:"cache"` on clip responses evicting each other and (2) a fresh `<audio>` losing its cold-fetch race.

- **`CLIP_POOL`** (9582): `new Map()` url→fully-buffered ready `HTMLAudioElement` kept in memory.
- **`_drillClip(url)`** (9583–9587): returns pooled paused element (`currentTime=0`) if ready, else `new Audio(url)`. Tail/overview URLs aren't pooled → fall back to `new Audio`.
- **`_unlockAudioOnce()`** (9599–9610): on first `pointerdown`/`touchstart`/`mousedown`/`keydown` (capture, passive, once), plays a tiny muted base64 WAV inside the gesture + resumes SFX `AudioContext` — iOS unlock.
- **`prewarmWordClips()`** (9612–9672): scheduled `requestIdleCallback(prewarmWordClips,{timeout:4000})` else `setTimeout(...,1500)` (6076–6080). Skips on `saveData`/2g. Walks `lines[].studyWords`; for each word with `sectionId`+`rom`, `base = word_${sectionId}_${_romUid(uid||rom)}`, warms JP `audio/jp/${base}.mp3` (pooled via `warmJp`, `<audio preload="auto">`, resolves on `canplaythrough`/`loadeddata`, deletes self from pool on error) and fetch-warms EN `${base}_gloss.mp3`, `${base}_en.mp3`, `${base}_ctx.mp3` (`cache:'force-cache'`). `CONCURRENCY = 6`, JP queued first.

### 1.6 The word pitch card (flip card) — `expandPitchRow(row, lang)` (line 11490)

Full-screen overlay on tapping a study word. **FRONT = word + animated pitch contour only. BACK = definition then context.**

Data read (11504–11636): `jpKey=row.dataset.pitchKey`, `rom=row.dataset.rom`, `uidKey=row.dataset.uid||rom`, `secId=row.dataset.sectionId`, `enMain=data.en` (display-only, may contain romaji, never spoken), `enSpeak=data.enSpeak||enMain`, `enContext=data.enContext||enHint`, `frontDef=enSpeak||enMain`. `enAudioUrl=_audioUrlFor(secId,uidKey,'en')`, `ctxAudioUrl=enContext?_audioUrlFor(secId,uidKey,'ctx'):null`, `frontAudioUrl=_audioUrlFor(secId,uidKey,'jp')`.

`_audioUrlFor` (line 12069) — naming MUST match or audio 404s:

```js
function _audioUrlFor(secId, rom, lang) {
  if (!secId || !rom) return null;
  const uid = _romUid(rom);
  if (lang === 'en')  return `audio/en/word_${secId}_${uid}_en.mp3`;   // definition
  if (lang === 'ctx') return `audio/en/word_${secId}_${uid}_ctx.mp3`;  // context
  return `audio/jp/word_${secId}_${uid}.mp3`;                          // the word
}
```

**FRONT innerHTML (11638–11650)** — pitch + romaji + flip ONLY; no `.pd-def`, no `.pd-card-divider`:

```js
detail.innerHTML = `
  <div class="pd-stage">
    <div class="pd-stack">
      <svg class="pd-svg" preserveAspectRatio="none"></svg>
      <div class="pd-mora-row jp">${moraHtml}</div>
    </div>
  </div>
  ${rom ? `<div class="pd-rom-line">${escapeHtml(rom)}</div>` : ''}
  <div class="pd-flip" data-flip="back" role="button" tabindex="0" aria-label="Flip to definition and context">
    <span class="pd-flip-label">DEFINITION AND CONTEXT</span>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></svg>
  </div>
`;
```

- Flip label exactly `DEFINITION AND CONTEXT` (uppercase) in `<span class="pd-flip-label">`, **after** the icon; `data-flip="back"`, `aria-label="Flip to definition and context"`, circular-refresh 4-path SVG `stroke-width="2.1"`.
- `moraHtml` (11605): `morae.map((m,i)=>'<span class="mora" data-idx="${i}" data-hl="${pat[i]||'L'}">${m}</span>')` joined, plus trailing `<span class="mora drop-marker" data-idx="${morae.length}" data-hl="drop">↘</span>` **only when `isOdaka`**.

**BACK innerHTML (11657–11670)** — word → romaji → definition → conditional context → links → flip; **no `.pb-divider`**:

```js
back.innerHTML = `
  <div class="pb-body">
    <div class="pb-word">${escapeHtml(jpKey)}</div>
    ${rom ? `<div class="pb-rom">${escapeHtml(rom)}</div>` : ''}
    <div class="pb-def">${escapeHtml(frontDef) || '—'}</div>
    ${enContext ? `<div class="pb-context-label">in this line</div>
    <div class="pb-en">${escapeHtml(enContext)}</div>` : ''}
    <div class="pb-links"><a class="pb-jisho" href="https://jisho.org/search/${encodeURIComponent(jpKey)}" target="_blank" rel="noopener noreferrer" aria-label="Open in Jisho dictionary">jisho.org ↗</a><a class="pb-jisho" href="https://youglish.com/pronounce/${encodeURIComponent(jpKey)}/japanese" target="_blank" rel="noopener noreferrer" aria-label="Hear it in real speech on YouGlish">hear it in the wild ↗</a></div>
  </div>
  <div class="pd-flip" data-flip="front" role="button" tabindex="0" aria-label="Flip back to the word">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></svg>
    <span class="pd-flip-label">WORD</span>
  </div>
`;
```

- `.pb-def` falls back to literal em-dash `—` when empty.
- Context block rendered **only when `enContext` non-empty**; label exactly `in this line` (lowercase in markup; CSS uppercases it).
- Exactly **two** `.pb-jisho` anchors: Jisho (`jisho.org ↗`) and YouGlish (`hear it in the wild ↗`), both `target="_blank" rel="noopener noreferrer"`.
- Back flip: `data-flip="front"`, `aria-label="Flip back to the word"`, SVG **first**, label `WORD` after (mirror of front).

**Audio behavior:**
- **FRONT speaks ONLY the word.** `detail._enAudioUrl = null` (line 11870). Tap-to-replay (`replayAudio`, ~12021) plays only the `jp` clip.
- **BACK speaks definition → context, chained** (`playBackExplain()`, 11890): queue `[enAudioUrl, ctxAudioUrl].filter(Boolean)`, each `new Audio(u)`, chained on `onended`, skips missing on `onerror`/`catch`.
- **Auto-play on flip-to-back:** `setFlipped(true)` (11944) schedules `playBackExplain()` **300ms** after flip (11969). Flipping away calls `stopWordCardAudio()` first (11948).
- **Tap back** (11914): ignores `.pd-flip`/`.pb-ctx-listen`/`.pb-jisho`; if sounding → stop, else → replay def→ctx.

**CSS (exact):**
- `.pitch-detail .pd-flip` (4376–4392): `margin-top:auto; margin-left:-22px; margin-right:-22px; margin-bottom:-18px; padding:13px 22px;` **no border-top**; `border-radius:0 0 18px 18px; display:flex; align-items:center; justify-content:center; gap:9px; font-family:var(--f-mono); font-size:11.5px; font-weight:700; letter-spacing:0.05em; color:var(--pd-mora-low,#b4a7c8);`. `:hover` color `var(--pd-mora-high,#f3e8ff)` bg `var(--pd-note-bg,rgba(197,163,255,0.08))`; `:active` bg `var(--pd-accent-dim,rgba(197,163,255,0.18))`; svg `15px`, opacity `0.85`.
- `.pitch-back .pb-body` (4395+): `display:flex;flex-direction:column;justify-content:center;flex:1 1 auto;padding:14px 0`.
- `.pb-word`: `font-family:var(--f-voice);font-weight:700;font-size:27px;line-height:1.1;color:var(--pd-accent,#B794F6)`. `.pb-rom`: `var(--f-mono);11px;letter-spacing:0.07em;color:var(--pd-mora-dim,#7e7095);margin-top:4px`.
- `.pb-divider{display:none}`.
- **`.pb-def` and `.pb-en` visually identical:** both `font-family:var(--f-voice);font-weight:400;font-size:19px;line-height:1.3;color:var(--pd-mora-high,#f3e8ff)`; `.pb-def` adds `text-wrap:balance;margin-top:18px`.
- `.pb-context-label`: `font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--pd-mora-dim,#7e7095);margin-top:18px;margin-bottom:6px` (so `in this line` renders `IN THIS LINE`; the matching 18px top margins give even rhythm).
- `.pb-jisho`: `display:inline-block;margin:14px auto 0;var(--f-mono);10.5px;letter-spacing:.04em;color:var(--pd-mora-dim,#7e7095);text-decoration:none;border-bottom:1px dotted currentColor;padding-bottom:1px`. `.pb-links`: `display:flex;justify-content:center;gap:18px;margin-bottom:0`; `.pb-links .pb-jisho{margin:14px 0 0}`.
- `.pd-rom-line` (4276): `var(--f-mono);11px;letter-spacing:.06em;text-align:center;margin-top:6px`.
- Vestigial-but-unused: `.pitch-detail .pd-card-divider`, `.pitch-detail .pd-def` (front never emits them).

### 1.7 Why the Explainer pill was removed

The drill already ends by speaking the whole sentence then its explanation, so a standalone `.card-section-overview` pill was duplicate UI — it is removed and stays removed. The explanation now rides the **drill tail** (not a visible element), so there is no always-present pill to force padding. **As of v0.92 every line carries `data-explain`** (sourced from `LINE_EXPLAIN`, §2.8); the tail speaks it (`audioCache['en-US:'+explain]`). The ≤v0.91 "earned"/some-lines model is superseded — it left most lines silent after the JP sentence (the bug the owner flagged). Empty/uncached → tail finishes after the full-line JP (now only an authoring gap, caught by `line_explainers.py check`).

### 1.8 The "identical" design contract (per studyable line / word)

1. Front = `.pd-stage` (`.pd-stack`>`.pd-svg`+`.pd-mora-row.jp`) + optional `.pd-rom-line` + `.pd-flip`; **no** definition text on front.
2. Front flip label `DEFINITION AND CONTEXT`; back flip label `WORD`.
3. Back order = word → romaji → `.pb-def`(=`enSpeak`) → `in this line` + `.pb-en`(=`enContext`, conditional) → two `.pb-links` → flip.
4. **No divider lines anywhere.**
5. `.pb-def`/`.pb-en` identical typography (19px/400/1.3/`#f3e8ff`).
6. Front speaks only the word; back speaks def→ctx chained, auto on flip (300ms), tap-to-replay/stop.
7. Back face has exactly one labeled pill `Word by Word`; drill cadence JP→350ms→gloss→700ms→next; tail 550ms→line→450ms→explainer; retries JP=2 / gloss=1 / tail stages=1. The tail JP stage needs the section's `speak_en` (it reads the `.brief-play--jp` button); the tail explainer needs a `LINE_EXPLAIN` entry (§2.8).
8. Clip filenames follow `_audioUrlFor`/`_romUid`.

### 1.9 The intro card QUICK GUIDE ("How to use this") — v0.93 standard

The intro card's flip-back is the QUICK GUIDE: a SHORT menu of tappable `.howto-sec` cards (one per `SECT` entry in the `initHowtoCards` IIFE) that open `.howto-pop` overlays. Inherited byte-identical from a v094 clone; do NOT rebuild it from an older template. Six invariants:

1. **Book beckon = WHITE, never amber.** `#openCardsBtn.book-beckon` glyph `color:#fff !important`; the breathing halo is a `::after` `radial-gradient(rgba(255,255,255,.30) 0%, transparent 70%)`, `@keyframes bookGlow` opacity .16↔.42 / scale .9↔1.06 / 2.8s. (The frosted-glass `!important` rules beat keyframes, so the glow MUST be the pseudo-element and the glyph needs its own `!important`.)
2. **The guide does NOT self-highlight on open.** The sheet-arrive arrival pulse skips the how-to back: `pulseTarget = back.querySelector('.section-brief') || (back.querySelector('.howto-menu') ? null : back.firstElementChild)` (the how-to back has `.howto-menu`, no `.section-brief`).
3. **Section list = study-card pill look.** `.howto-sec` = `background:var(--paper-shade)` + `box-shadow:inset 0 0 0 1px var(--paper-rule)` + `border-radius:var(--r-key)`; NO 1px box border, NO coloured left-bars (the `:nth-child(2)/(3)` role-ink rules are deleted). Reads as part of the study sheet, not a settings menu.
4. **Section names (4, exact):** `Karaoke`, `Sing Mode and Study Mode`, `Inside the study card`, `Immerse` — set in BOTH the `.howto-sec` menu labels AND the matching `SECT` popup `title`s.
5. **Each popup shows the control it describes** at the top: `initHowtoCards` has `PILL(txt)` and `KEY(href)` helpers + a `btns` field per `SECT` entry, rendered by `openPop` after the title inside `.howto-pop-btns`. lyrics=`KEY('#i-xlate-corner')+KEY('#i-cards-corner')`, modes=`PILL('Sing Mode')+PILL('Study Mode')`, study=`KEY('#i-turtle')+KEY('#i-loop')+KEY('#i-share')`, more=`PILL('Immerse')`. `.howto-pop-pill` mirrors a topbar pill (white text, `rgba(255,255,255,.08)` fill, `rgba(255,255,255,.24)` 1px border, radius 5); `.howto-pop-key` mirrors a frosted toolbar circle (40px, `rgba(255,255,255,.06)` fill, inset `rgba(255,255,255,.22)` ring, 21px glyph). If you rename/reorder `SECT`, update its `btns` too or the replicas go stale.
6. The Karaoke popup does NOT say "It glows softly while you wait."

A new song inherits all of §1.9 from the v094 clone — only re-author it if the page structure changes.

---

## 2. `data.json` authoring contract

Canonical: `songs/inochi-mijikashi-v091/data.json` (138 KB). Gated by `validate_song.py` (coverage/integrity) and `validate_tts_safety.py` (language safety); both must exit 0. **Note:** two per-line dictionaries that the page depends on — `LINE_TR` and `LINE_EXPLAIN` — do **NOT** live in `data.json`; they are hardcoded in `index.html` (§2.8). Authoring a song is not done until those are written too.

### 2.1 Top-level keys (16, in order)

| Key | v091 value / type | Meaning |
|---|---|---|
| `song_number` | `1` (int) | Library position; appears in `r2_folder`. |
| `title_jp` | `"イノチミジカシコイセヨオトメ"` | Display title (katakana here). |
| `title_en` | `"Life is Short, Fall in Love, Maiden"` | English title. |
| `artist` | `"クリープハイプ"` | Artist, JP. |
| `artist_en` | `"CreepHyp"` | Artist romanized. |
| `slug` | `"inochi-mijikashi"` | **Stable conceptual id — never suffixed with build slug.** |
| `youtube_id` | `"7cCL0owFBqk"` | YouTube id. **Used only by the share-link builder (line 6966) and a fallback (line 11308). The actual embedded IFrame player uses the hardcoded `const YT_ID` (line 5804), NOT this field (line 8094: `videoId: YT_ID`)** — so `youtube_id` here **and** `YT_ID` in index.html must agree (§4.2). |
| `level` | `"Intermediate"` | Difficulty. |
| `r2_folder` | `"Song 1 イノチミジカシコイセヨオトメ"` | R2 bucket folder. |
| `podcast_file` | `"inochi-mijikashi_podcast.mp3"` | Podcast filename. |
| `direction` | `"ja"` | Source language. |
| `sections` | list[7] | Lesson body; drives study cards. |
| `grammar` | list[9] `{pattern,explanation,example}` | Grammar panel. |
| `trivia` | list[11] `{title,text,links}` | Trivia cards. |
| `podcast_script` | list[65], 4-tuple arrays `["HOST"/"GUEST", text, float, …]` | Transcript (arrays, NOT objects). |
| `apple_lyrics` | object | Timed lyrics → line render + per-syllable highlight. |

`REQUIRED_TOP_KEYS` (hard): `song_number, title_jp, title_en, artist, slug, youtube_id, sections, apple_lyrics`.

**`apple_lyrics`** (7 keys, **in this exact order**): `song`, `lines`, `line_count`, `has_translations`, `has_word_timing`, `languages`, `has_kana_timings`.
- `song` = `{id,name,artist,album,duration_ms,artwork_url}`.
- `lines` (27 in v091) each `{begin_ms,end_ms,text,lang,translation,translation_lang,words[],is_background,kana_timings[]}` where `words[]`={text,begin_ms,end_ms} (coarse) and `kana_timings[]`={kana,rom,begin_ms,end_ms} (per-mora, drives highlight + romaji gap-fill).
- `line_count` = `27` (== len(lines)).
- `has_translations` = `false`.
- `has_word_timing` = `true`.
- `languages` = `[]`.
- `has_kana_timings` = `true`.

### 2.2 `sections[]` and the word object

Section: `{id, name, short_name, subtitle, description, speak_en, context_lines, note, words}`. v091 ids: `title, v1, v2, ch1, v3, ch2, outro`. `speak_en` = section-intro narration (en-US clip `audio/en/section_<id>_intro.mp3`, required by safety linter). **`speak_en` is also what conditionally emits `briefHtml` / the `.brief-play--jp` button (line 6546), which the drill tail reads to speak the full JP line (§1.3) — a section with no `speak_en` loses both the intro buttons AND the tail's full-line-JP stage.** `context_lines` = the section's raw lyric lines (line→section match). `note` = display prose.

`sections[].words[]` fields (v091: 76 words):

| Field | Required | Drives |
|---|---|---|
| `jp` | yes | On-card JP (`.wc-main`); matched against lyric line in greedy coverage walk. |
| `rom` | yes | Romaji (`.wc-sub`) **and** default audio slug via `_romUid(rom)`. |
| `jp_speak` | yes (76/76) | **TTS key + pitch mora source.** `data-speak`/`data-pitch-key`=`jp_speak||jp`. JP voice speaks this. |
| `en` | yes | **Short DISPLAY gloss — NEVER spoken.** May contain romaji/parentheticals. Keep terse. |
| `en_speak` | yes | **SPOKEN definition.** `data-en-speak`; bound to `_en.mp3`. Safety linter requires a clip. |
| `context` | yes (76/76) | **Spoken "in this line" explanation.** `data-en-context`; bound to `_ctx.mp3`. |
| `gloss` | yes (76/76) | **Drill's short spoken EN.** `drillParts[].g`; bound to `_gloss.mp3`. 1–4 words. |
| `hint` | yes (76/76) | Display-only background. `data-en-hint=hint||section`. Never spoken. |
| `particle` | yes (76/76) | Bool → orange particle tile. |
| `uid` | optional (6/76) | Overrides `rom` as audio slug + study key (`uid||rom`). |
| `only_lines` | optional (6/76) | Lyric lines where this card is tappable (one `jp` → two cards). Pair with `uid`. |

`sectionId` is NOT stored per word in `data.json` — it is injected at runtime (line 6005: `(sec.words||[]).forEach(w => { w.sectionId = sec.id; })`, so **each section needs an `id`**); `uid` is optional (audio uid falls back to `rom`).

How drill parts build (lines 6021–6031): `ln.drillParts = (ln.studyWords||[]).filter(w => w.sectionId && w.rom).map(w => ({ s: w.sectionId, u: (w.uid||w.rom), g: (w.gloss||'') }))`. A drill part = `{s,u,g}`. `collectStudyWords` (6186–6216) is occurrence-aware (repeated word emits per occurrence).

### 2.3 `en_speak` / `context` / `gloss` / `speak_en` STYLE GUIDE (the spoken English)

These four are read aloud by an **en-US voice**; `en`/`hint` are not. (The `LINE_EXPLAIN` values in §2.8 are read aloud too, by the same voice — apply the same rules.) Rules:

1. **Natural, complete English sentences.** End with a period (read as prosody).
2. **NEVER the "gloss — nuance" em-dash fragment** (reads as a broken half-sentence). Use two clauses/sentences. (Known drift: silhouette's shipped `en_speak` still carries some em-dash fragments — the CONTRACT wins for all new/updated authoring; don't copy that style from silhouette as "newest-vintage" precedent.)
3. **Clean English ONLY — zero kana/kanji/CJK.** `validate_tts_safety.py` HARD-FAILS any en-US text matching `[぀-ヿ㐀-鿿ｦ-ﾟ]`; advisory-flags `(slang|lit.|name|particle|abbr.|sl.)` parentheticals and macron/circumflex romaji (`āīūēōâ…`) — those belong in `en`.
4. **Preserve register/dialect/nuance in words, not symbols** ("casual Osaka slang", "old-fashioned command", "girlish word for I").
5. **A particle gets a natural grammatical-function sentence**, not a bare label.
6. **`en_speak` (meaning) and `context` (in-line role) stay DISTINCT.**
7. **Keep `en` and `gloss` short**; prose lives in `en_speak`/`context`.

BEFORE → AFTER (real v091 data):

- **なんぼ (nanbo):** `en`=`"no matter how much"`; `en_speak`=`"No matter how much. It's casual Osaka slang."`; `context`=`"Osaka slang for no matter how much. It makes her sound like a real person talking plainly, not a polished lyric."`; `gloss`=`"no matter how"`.
- **かってね (katte ne):** `en`=`"they said / apparently"`; `en_speak`=`"They used to say. It's casual Osaka speech for hearing something secondhand."`; `context`=`"She heard she was cute; she doesn't claim it herself. Other people's words, remembered at a wistful distance."`; `gloss`=`"they used to say"`.
- **は (topic):** `en`=`"as for ___"`; `jp_speak`=`"わ"`; `en_speak`=`"As for. It's the topic marker that flags what the sentence is about."`; `context`=`"Sets 'those child-days' as the thing the line is talking about."`.
- **ピンサロ (pinsaro):** `en`=`"pinsaro (slang)"` (display, unsafe-to-speak allowed); `en_speak`=`"Pink salon is the slang name for a cheap, low-end sex parlor."` (clean, no romaji); `context`=`"A cheap sex-work parlor, said with the blunt street word. She refuses to dress up what she does."`; `gloss`=`"pink salon"`. This is the exact bug class the safety linter exists to catch.

### 2.4 The `jp_speak` rule

`jp_speak` is the literal string handed to the JP voice and split into pitch morae. When written `jp` misleads, carry the **pronounced** form:

- **A lone topic/contrast particle `は` MUST have `jp_speak:"わ"`** (read "wa"): `{"jp":"は","rom":"wa","jp_speak":"わ","en":"as for ___","particle":true}`. Clip keyed by `jp_speak` → `(ja-JP,"わ")` → `audio/jp/word_v1_wa.mp3`. Compound particles containing は keep written form (`には → jp_speak "には"`).
- **Latin/abbreviations spelled in katakana:** `OLさん → jp_speak "オーエルさん"` while `en` stays `"office lady"`.

### 2.5 Coverage rule (validate_song E1)

Every sung kana span of `apple_lyrics.lines[].text` must be covered by a tappable study word (greedy first-match over `sections[].words[].jp`). A kana span that romaji-gap-fills from `kana_timings` but has **no** study word is an **E1 ERROR** (the bug that hid question-particle か in 言えるか). Only escape: add the span to top-level `coverage_exceptions` (downgrades to warning), reserved for genuinely meaningless syllables — never a real particle. A new particle needs its own card. Kanji spans with no card and no romaji are also E1 errors (the OLさん romaji-hole case).

**Granularity is the other half of coverage (validate_segmentation).** Coverage says every kana must be under *some* card; granularity says each card must be **a real dictionary word** — something a beginner could actually look up. The ARBITER is a **local JMdict headword set** (jisho.org's own source data, bundled offline at `tools/songcraft/data/jmdict_headwords.txt.gz` — no API call), not anyone's judgment about "clusters" (that judgment shipped both 付けてほしい AND のかな). A card is fine iff it's a single (possibly inflected) word, a compound verb (弾け出す), or an exact JMdict headword. Split anything the dictionary can't find: 付けてほしい, タッチした, のかな (の+か+な — but かな alone IS a word), なろうかな, シガレットアメリカン. Keep whole what it HAS: 思考回路, にも, ように, だろう, から, 三度, 木の葉. `validate_segmentation.py` (fugashi units + local-dictionary membership, parler env) gates this loudly in the `validate` step. See BUILDER.md "Segmentation canon". This closed the gap that shipped Headlong's 付けてほしい (under-split) and のかな (a non-word left whole).

### 2.6 Audio-file naming this produces

`uid = _romUid(w.uid || w.rom)`; `_romUid` = space→`-`, `·`→removed, `/`→`_`, trim leading/trailing `-` (`"katte ne"→"katte-ne"`, `"ōeru san"→"ōeru-san"`).

| File | Speaks | In manifest? | Validated? |
|---|---|---|---|
| `audio/jp/word_<secId>_<uid>.mp3` | `jp_speak` | yes (key=`jp_speak`) | yes |
| `audio/en/word_<secId>_<uid>_en.mp3` | `en_speak` | yes (key=`en_speak`) | **yes** |
| `audio/en/word_<secId>_<uid>_gloss.mp3` | `gloss` | no (URL-resolved) | no (404→clean-EN speechSynthesis) |
| `audio/en/word_<secId>_<uid>_ctx.mp3` | `context` | no (URL-resolved) | no |
| `audio/en/section_<secId>_intro.mp3` | `speak_en` | yes | yes |
| `audio/en/line_<sha8>_explain.mp3` | `LINE_EXPLAIN` value (§2.8) | yes | yes (validate_song E5) |
| `audio/jp/line_uNN.mp3` | full line text | yes | yes |

All clips live once at `songs/_assets/inochi-mijikashi/audio/{en,jp}/`; `_redirects` rewrites `/songs/:dir/audio/*`. `tts_manifest.json` = list of 4-tuples `[lang, key, spoken, filename]` (v091: 205 entries — 107 `ja-JP`, 98 `en-US`).

### 2.7 New-word authoring checklist

1. `jp` = exact lyric substring.
2. `rom` = romaji → audio slug `_romUid(rom)`; if it collides in-section, set unique `uid` + `only_lines:["<full line>"]`.
3. `jp_speak` = pronounced form (bare `は`→`"わ"`; Latin/abbrev → katakana; else usually == `jp`).
4. `en` = short display gloss (romaji/parentheticals allowed; never spoken).
5. `en_speak` = complete English sentence; no CJK, no em-dash splice; register in words.
6. `context` = complete sentence on the word's role **in this line**, distinct from `en_speak`.
7. `gloss` = 1–4 plain English words.
8. `hint` = display-only background.
9. `particle` = bool.
10. Generate the 4 clips (`_en.mp3`,`_ctx.mp3`,`_gloss.mp3`,`.mp3`) per §2.6; add `(ja-JP,jp_speak)` + `(en-US,en_speak)` (+ any `speak_en`/`line_explain`) rows to `tts_manifest.json`.
11. **If you added/changed a line:** update its `LINE_TR` and `LINE_EXPLAIN` entries in `index.html` (§2.8 — every line gets a `LINE_EXPLAIN`), and generate the line-explainer clip + its manifest entry (use `line_explainers.py`, §2.8.1).
12. Run both validators (E1 clean; 0 fails), then `python3 tools/bump_asset_versions.py`.

### 2.8 `LINE_TR` & `LINE_EXPLAIN` live in `index.html` (NOT `data.json`)

Two per-line dictionaries are **large hardcoded JS object literals inside `index.html`**, keyed by the JP lyric line. They are part of authoring a song just as much as `data.json` is. Cloning `index.html` and only swapping `data.json` leaves these keyed to inochi's lyrics, so **no key matches a new song's lines** — every line's inline English silently falls back (or blanks) and **every** line explainer disappears. A new song MUST re-author both from scratch.

**The key function — `lineTrKey(s)` (line 6319):**

```js
function lineTrKey(s){ return String(s||'').replace(/\s*\(×\d+\)\s*$/,'').replace(/\s+/g,'').trim(); }
```

Strips a trailing `(×N)` repeat marker, then strips **all** whitespace. Both dictionaries are keyed by `lineTrKey(<JP line>)` (the source literals are already whitespace-stripped). Repeated lyric lines reuse one entry.

**`const LINE_TR = { … }` (line 6324; v091: 20 entries).** Each value is an object `{en, full?}` (optionally a `lead:true` form for a deferred half of a paired thought — see the source comment at 6315–6323):
- `en` = the inline English shown **under THIS line** (card front) and spoken — drives `ln.enInline` at **line 6037**: `ln.enInline = _tr ? _tr.en : ln.translation;` (so absent → falls back to `ln.translation` from `apple_lyrics`, usually empty for inochi). `ln.enInline` is rendered into `.card-en-row` (line 6588).
- `full` = the complete paired-thought sentence (the full sentence for a two-line thought).
- Example entries (verbatim):
  ```js
  'なんぼ汚れたアタシでも':       {en:'No matter how filthy I’ve become,', full:'No matter how filthy I’ve become, I was a cute kid, they used to say.'},
  '長生きする気も無いから':       {en:'It’s not like I even plan on living long, so —'},
  'イノチミジカシコイセヨオトメ': {en:'Life is short — fall in love, maiden.'},
  ```

**`const LINE_EXPLAIN = { … }` (line 6362; v092: 20 entries — one per unique lyric line).** Each value is a **plain string** of clean spoken English (no romaji, no CJK — it becomes an en-US clip and must pass TTS safety). This is the **sole source** of the drill-tail explainer:
- `_lineExplain = LINE_EXPLAIN[lineTrKey(ln.text)] || ''` (line 6540) → `explainerSpeak` (line 6545) → `data-explain="${escapeAttr(explainerSpeak)}"` on the back face (line 6591) → the drill tail speaks `audioCache['en-US:' + grid.dataset.explain]` (§1.3).
- **STANDARD (v0.92+): EVERY lyric line carries an entry.** Earlier builds (≤v0.91) used an "earned"/some-lines model, which left most lines speaking the full JP sentence and then going **silent** — owner: "every line should have this treatment." So as of v0.92 every unique line has a `LINE_EXPLAIN` value **and** its `en-US` clip, and the drill tail always ends: full JP sentence → English explanation + context. (This does NOT reintroduce the removed pill, §1.7 — the *drill tail* is the delivery, not a visible element.) The every-line gate is **`tools/songcraft/line_explainers.py check`** (§2.8.1), not `validate_song` (which only flags orphan keys, not missing coverage).
- Each value → en-US clip `audio/en/line_<sha8>_explain.mp3`, where `sha8 = hashlib.sha1(value.encode()).hexdigest()[:8]` (§3.2), plus a manifest entry `["en-US", value, value, "audio/en/line_<sha8>_explain.mp3"]` (key === spoken === the value).
- Example entries (verbatim):
  ```js
  'イノチミジカシコイセヨオトメ': 'She\'s saying: life is short, so love now. The line is a 1915 song quote — here it\'s her own bleak reason.',
  'ピンサロ嬢になりました': 'She\'s saying: I became a sex worker. She announces it twice, politely, like a formal self-introduction.',
  '明日には': 'She\'s saying: by tomorrow... and nothing more. The hope trails off before she can finish it.',
  ```

**Validation (both parsed straight out of `index.html` by `validate_song.py`, `parse_line_map`, lines ~268–308):**
- **E2 — `LINE_TR` coverage:** every non-instrumental lyric line must have a `LINE_TR` entry with a non-empty `en` (validate_song lines ~626–637), or `could not locate const LINE_TR` if the literal is missing (line 535).
- **E3 — `LINE_EXPLAIN` orphans:** every `LINE_EXPLAIN` **key** must match a real lyric line (whitespace-stripped); a key matching no line is an orphan ERROR (lines ~638–645).
- **E5 — explainer manifest integrity:** every `LINE_EXPLAIN` **value** must be present as a manifest key **byte-identical** (lines ~691–694), so its en-US clip resolves. (`validate_tts_safety.py` only checks a `data.json` `line_explain` map, which inochi does not have — so for inochi the line-explainer gate is **validate_song E5**, not the safety linter.)

**Rule when editing a lyric line (Flow B):** if any lyric LINE TEXT changes, the matching `LINE_TR` and `LINE_EXPLAIN` **keys** (whitespace-stripped JP) in `index.html` must be updated to the new text too. Otherwise validate_song flags E2/E3 orphans, the inline translation and the explainer silently blank, and (because the explainer value's `sha8` changes when its text changes) the line-explainer clip + manifest entry must be regenerated.

**Pure-English lyric lines (Round 11 standard — shinunoga/headlong):** an EN
line is NOT exempt from the every-line standard. Its `LINE_TR` = the line
itself (natural punctuation; the page's `.is-pure-en` suppresses the redundant
inline row); it gets an authored `LINE_EXPLAIN` like any JP line; culturally
interesting EN words may carry 1–2 anchor study cards with **katakana
`jp_speak`** (ヘッドロング — teaches the katakana ear, §2.4 applies). The
tooling side: `content_to_data` emits an `en-US`-keyed spoken-line clip
(`audio/en/line_en_uNN.mp3`, Kokoro — the JP voice never reads Latin text);
`build_drill_concat` builds the word-less concat `[EN line clip + explainer]`
with `words:[]`; the Round-11 page dispatches it from the Word-by-Word pill.
`line_explainers.py check` passes an EN line on explainer + en-US line clip +
en-US explain clip + DRILL_MAP audio. Requires a Round-11+ template
(`inochi-mijikashi-e03jz0` or its promoted successor).

### 2.8.1 Turnkey tool — `tools/songcraft/line_explainers.py`

Manages the every-line `LINE_EXPLAIN` standard for **any** song (new or existing). Two subcommands:

```bash
# CHECK — read-only audit; exit 1 if any line lacks the full tail. Works on any dir.
python3 tools/songcraft/line_explainers.py check songs/<dir>
#   For each unique lyric line it verifies: ja-JP:<line> clip + LINE_EXPLAIN entry +
#   en-US:<explanation> clip. Prints each gap with its reason.
python3 tools/songcraft/line_explainers.py check songs/<dir> --template gaps.json
#   ...also writes a fill-in JSON of just the missing lines (+ LINE_TR hints).

# BUILD — author the explanations into gaps.json, then render + wire.
#   RUN WITH THE KOKORO ENV PYTHON (am_michael render):
/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python \
    tools/songcraft/line_explainers.py build songs/<dir> gaps.json
#   For each NEW key (skips ones already present): TTS-safety gate → render
#   line_<sha8>_explain.mp3 into songs/_assets/<slug>/audio/en/ (recipe §3) →
#   append the manifest entry → insert the LINE_EXPLAIN entry into index.html.
#   --no-render = text/manifest only;  --overwrite = re-render existing clips.
# THEN:  python3 tools/bump_asset_versions.py
#        python3 tools/validate_tts_safety.py songs/<dir>/tts_manifest.json
#        re-run `check` (expect full coverage), deploy a fresh slug.
```

**Interpreter:** `check` runs with plain `python3`; `build` MUST run with the parler kokoro python (`/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python`) — the wrong interpreter on `build` = `ModuleNotFoundError: kokoro`.

**Precondition:** `check`/`build` assume the **v090+ drill-tail runtime** (`playTail` + `data-explain`). `check` warns when the target page predates it (e.g. silhouette-v023) — authoring `LINE_EXPLAIN` alone will NOT make such a page speak the tail; it needs the apply-to-both architecture port first (§1.3).

**Boundary:** `build` only fills the **EN explainer** side. It assumes the **full-line JP clip** (`ja-JP:<line>`, e.g. `audio/jp/line_uNN.mp3`) already exists (true for inochi). `check` surfaces a missing JP-line clip separately ("no JP-line clip") — that clip is part of the base JP-voice render (§3, qwentts/Aivis), not this tool. So a song that lacks JP line clips (e.g. silhouette) needs those rendered first before its tail can speak the JP sentence.

**Deploy-verify caveat:** after deploying, do NOT request the new dir's bare audio rewrite URL during propagation — it can cache the root HTML as immutable at that CF edge (one clip "no sound"). Verify with a `?cb=` cache-buster query; recover a poisoned bare URL with a fresh slug (the Keychain CF token can't purge).

---

## 3. Audio pipeline

**Pronunciation lexicon (Round 10, 2026-07-04):** before rendering ANY JP clip,
gen_audio consults `tools/songcraft/pronunciation_lexicon.json` — words caught
mispronounced once may never regress to TTS (allow-sets per word; E15 gates it;
`manaoke_build.py lexicon add` records a new catch and deletes the offending
clips so re-renders re-route). Policy + failure catalog:
`tools/songcraft/PRONUNCIATION-POLICY.md`.

### 3.1 Two voice engines (the only correct ones)

**Do NOT use `tools/generate_tts.py` (Google Neural2 → wrong voices).** Production audio is local.

**EN clips** (`_en`, `_ctx`, `_gloss`, section intros, line explainers) = **Kokoro-82M**, voice `am_michael`, **speed `0.95`**, **24000 Hz**, then **two-pass ffmpeg `loudnorm` `I=-16:TP=-1.5:LRA=11:linear=true`**, mp3 `libmp3lame -q:a 2`.
- Pass 1: `ffmpeg -y -i <wav> -af loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json -f null -`; parse trailing JSON for `input_i/input_tp/input_lra/input_thresh/target_offset`.
- Pass 2: `ffmpeg -y -i <wav> -af loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:measured_I=…:measured_TP=…:measured_LRA=…:measured_thresh=…:offset=… -codec:a libmp3lame -q:a 2 <mp3>`.
- **`tools/songcraft/gen_en_audio.py` is the SILHOUETTE-specific build script, NOT a runnable inochi command.** It is hardcoded to `BASE = <repo>/.local-preview/REFINE-2026-06-11/silhouette`, reads `data.draft.json` + `line_maps.draft.json` from that path, and writes to `_assets/silhouette/audio/en` (its `LINE_EXPLAIN` comes from `line_maps.draft.json`, not from any `index.html`). Cite it **only** as the recipe reference for `loudnorm()` (lines ~55–72), `rom_uid()` (lines 18–19), and the line-explainer `sha1[:8]` (line ~39). **The actual reusable production path is the inline parler-env Kokoro loop in §3.7**, which reads the **current** `data.json`.
- **WORKING ENV (critical):** the conda **`parler`** python `/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python` (kokoro 0.9.4 + misaki). The default `KOKORO_PY` in `verify_en_audio.py` (line 36–37) is **STALE/missing** — always set `KOKORO_PY` to the parler python. Fallback: `<your desktop>/macOS Voices/.higgs-venv/bin/python` (kokoro 0.7.16).
- Init: `from kokoro import KPipeline; pipe = KPipeline(lang_code='a')`; render `[a for _,_,a in pipe(text, voice='am_michael', speed=0.95)]`, `np.concatenate` if >1, `sf.write(wav, audio, 24000)`.
- **Spoken-text hygiene:** bare single word gets terminal `.` for the render only (Kokoro adds spurious L-onset; contract text unchanged). Em/en dashes (`—`/`–`) → `, ` in spoken string only; on-screen + filenames/manifest keep the em-dash. CJK or macron vowels (`āēīōūĀĒĪŌŪ`) in EN-spoken = hard TTS-SAFETY violation.

**JP clips** (served as `word_<sec>_<uid>.mp3`, `line_uNN.mp3`) — render a **wav master** then **compress to the served mp3**. Master = **HYBRID**, **44100 Hz**, `pcm_s16le`, same two-pass loudnorm; then `python3 tools/human_audio/jp_to_mp3.py --song <song>` → mono **48000 Hz / 80kbps mp3** (`ffmpeg -ac 1 -ar 48000 -c:a libmp3lame -b:a 80k`). Runtime + manifest reference `.mp3` (lowercase — `.MP3` 404s on CF); keep the `.wav` master alongside (legacy dirs + Anki kit read it). Large WAVs lost the cellular cold-fetch race → the word-by-word drill dropped to the English gloss (v095 car-glitch fix). Master render recipe:
- **Primary: Qwen3-TTS `Ono_Anna`** — model `mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit`, `voice='Ono_Anna'`, `lang_code='ja'`, `instruct='穏やかな大人の朗読'`, via `mlx_audio`, conda env `qwentts` (ref `engines/CosyVoice/qwen_tts.py`).
- **Fallback: AivisSpeech** (deterministic OpenJTalk) for words the neural engine garbles isolated (~30%: short particles <0.35s, wrong isolated kanji). Aida (female) for inochi-voiced, TANAKA (male) for silhouette. Whisper read-back + acoustic clip physics gate each clip (`tools/human_audio/clip_physics.py`, E19; the old check_jp_gates.py prototype is retired); 12 re-rolls then fall back.
- **`human_audio` swap** for short mangled words (頃→"goroo", 気→"kii"). *(Counts like "~10/65 on inochi" in older notes are illustrative/historical — they predate v091, whose `data.json` has **76** words; do not treat 65 as the inochi word count.)* `tools/human_audio/fetch.py` (JapanesePod101 first) then `tools/human_audio/tofugu.py` (offline WaniKani/Tofugu CC-BY-SA). Cache to `library/<kanji>__<kana>.mp3`; install = two-pass loudnorm to same target, `-ar 44100 pcm_s16le`, overwrite in place (temp→replace, never delete-then-recreate). Miss reply is a fixed file (md5 `7e2c2f95…`, 52288 B) — fingerprint it, never trust HTTP 200. **は/へ/を guard:** dict returns "ha/he/wo"; as particles must sound "wa/e/o" — `fetch.py` refuses (`BAD-PARTICLE`); keep the TTS わ/え/お clip.

### 3.2 Clip naming — `_romUid` + `_audioUrlFor`

`_romUid(rom)` (page 11352; mirrored `gen_en_audio.py` 18–19, `verify_en_audio.py` 59–60): space→`-`, `·`→`''`, `/`→`_`, trim leading/trailing `-`.

`_audioUrlFor(secId, rom, lang)` (page 12069, `uid=_romUid(rom)`): `en`→`audio/en/word_<secId>_<uid>_en.mp3` (`en_speak`); `ctx`→`audio/en/word_<secId>_<uid>_ctx.mp3` (`context_speak||context`); else→`audio/jp/word_<secId>_<uid>.mp3` (native JP).

Other classes: drill gloss `audio/en/word_<secId>_<uid>_gloss.mp3` (`gloss`); section intro `audio/en/section_<secId>_intro.mp3` (`speak_en`); line explainer `audio/en/line_<sha8>_explain.mp3` where **`sha8 = hashlib.sha1(text.encode()).hexdigest()[:8]` (SHA1, not SHA256)** (`gen_en_audio.py` 38–39; `text` = the `LINE_EXPLAIN` value, §2.8); full line JP `audio/jp/line_uNN.mp3`. Shared store: `songs/_assets/<song>/audio/{en,jp}/` (inochi: ~414 EN + 114 JP; the EN dir accumulates regenerated/old `line_*_explain.mp3`, so the exact count drifts — only the 20 live explainer values matter). Build dirs ship NO `audio/`; page references relative, `_redirects` rewrites.

### 3.3 Two play paths

- **uid-direct** (`new Audio(url)`, bypasses `audioCache`): pitch-card front JP (11834), back EN def (11625) + ctx (11627), drill JP+gloss (9488–9489). Resolve by **filename only**.
- **manifest/audioCache** (`audioCache['<lang>:<text>']`): `speakEN(text)` keys `'en-US:'+text` (9310); `speakJP(text)` keys `'ja-JP:'+text` (9309). **Section intros** (`brief-play`, `data-speak=sec.speak_en`, 6553/6661), **full JP lines via the drill tail** (`.brief-play--jp` data-speak=`ln.text`, §1.3), and **line explainers** (9348) play here. Miss → browser (Siri/SpeechSynthesis) voice — the bug this system prevents.

Manifest fetched at boot (`fetch('./tts_manifest.json?v=<hash>')`, 5959), folded into cache (5990–5996): `const [lang, speakText, , filename] = entry; audioCache[`${lang}:${speakText}`] = filename`. **Cache key uses `entry[1]` (key); `entry[2]` (spoken) is skipped — so `key === spoken` must hold in every entry.**

### 3.4 THE MANIFEST CONTRACT

`tts_manifest.json` = flat array of `[lang, key, spoken, filename]`; `lang ∈ {"en-US","ja-JP"}`; `filename` song-relative (`audio/en/word_v1_nanbo_en.mp3`). Five entry classes:

1. **en-US word definitions** — `key=spoken=en_speak`, `…/word_<sec>_<uid>_en.mp3`.
2. **ja-JP word readings** — `key=spoken=jp_speak` (or `jp`), `…/word_<sec>_<uid>.mp3`. **Kana aliases:** when `jp_speak ≠ jp`, BOTH strings get a `ja-JP` entry → same `.mp3` (manifest 416–492: `よごれた`, `やすみのひ`, `かあさん`…).
3. **en-US section intros** — `key=spoken=speak_en`, `audio/en/section_<sec>_intro.mp3`.
4. **en-US line explainers** — `key=spoken=LINE_EXPLAIN value` (§2.8, the dictionary lives in `index.html`), `audio/en/line_<sha8>_explain.mp3`.
5. **ja-JP full lines** — `key=spoken=full line text` (the RAW lyric line incl. internal spaces, NOT the whitespace-stripped `lineTrKey` form used by `LINE_EXPLAIN`), `audio/jp/line_uNN.mp3`. **NN = positional index into `apple_lyrics.lines` (00,01,02…).** A line that IS a single study word reuses that word's clip instead (the ja-JP key points at `word_<sec>_<uid>.mp3`) and its `line_uNN` number is skipped — e.g. inochi skips u12 (スキキライスキ→`word_ch1_suki-kirai-suki.mp3`) and u18 (明日には→`word_outro_ashita-ni-wa.mp3`). These full-line JP clips are rendered with the §3.1 JP engine (same as word clips); there is no turnkey script for them yet (unlike §3.7 for EN), so `line_explainers.py build` does NOT make them — `check` reports "no JP-line clip" when they are missing.

**The rule:** when you change a word's `en_speak`/`jp_speak`/`context`, a section `speak_en`, or a `LINE_EXPLAIN` value, update the matching entry's `key` **and** `spoken` (match by filename; path unchanged — except a changed `LINE_EXPLAIN` value also changes its `sha8`, so its filename **does** change). Otherwise `validate_tts_safety.py` FAILS for the per-word/section assertions (`collect_required_speech`, 65–88, asserts a clip exists for every `speak_en`→en-US, per-word `en_speak`→en-US, per-word `jp_speak`→ja-JP, and any `data.json` `line_explain` map → en-US), `validate_song.py` E5 FAILS for line explainers (each `LINE_EXPLAIN` value must be a byte-identical manifest key), and section intros / line explainers silently fall back to the browser voice. Filling missing per-word entries is what dropped inochi's 37 phantom failures to 0.

### 3.5 The `verify_en_audio.py` GAP (regenerate `_en` directly)

`verify_en_audio.py` whispers each clip (`small.en`, re-judged `large-v3`; `--fix` regenerates) but only sweeps: every `en-US` **manifest** entry (85–87), every `_ctx.mp3` (91–94), every `_gloss.mp3` (98–100). It does **NOT** sweep per-word `_en.mp3` directly — those are caught only if their manifest text was updated. **Regenerate `_en.mp3` straight from `data.json` `en_speak`; do not rely on `--fix` to catch reworded definitions.**

### 3.6 The lone-は fix WITHOUT TTS

A bare topic particle は must SOUND わ. Two parts, no TTS:
1. In `data.json` set `jp_speak:'わ'` AND update the matching `ja-JP` manifest entry (`key=spoken='わ'`).
2. Make the word's uid clip **byte-identical** to the canonical わ clip — SERVED file is now `word_v1_wa.mp3` (**md5 `0ea3e40165987ab22e470c18654a6ea5`**, mono/48k/80k); the legacy `word_v1_wa.wav` (**md5 `750ca1c12e8cade384c0c2ec2a9043d3`**) stays for old `.wav` dirs. `cp` BOTH, don't regenerate. The pitch card/drill resolves は by uid-direct path (now `.mp3`), so fixing the FILE changes the sound.

```bash
ASSET=<repo>/songs/_assets/inochi-mijikashi/audio/jp
# served mp3 (what the page fetches) + legacy wav master (old dirs)
cp "$ASSET/word_v1_wa.mp3" "$ASSET/word_v2_wa-mainichi.mp3"
cp "$ASSET/word_v1_wa.mp3" "$ASSET/word_v2_wa-toki.mp3"
cp "$ASSET/word_v1_wa.wav" "$ASSET/word_v2_wa-mainichi.wav"
cp "$ASSET/word_v1_wa.wav" "$ASSET/word_v2_wa-toki.wav"
```

### 3.7 Regenerate reworded `_en` / `_ctx` clips (parler Kokoro) — THE reusable EN script

This inline loop is the runnable production path (gen_en_audio.py is silhouette-only, §3.1). It reads the **current** `data.json`:

```bash
SONG=inochi-mijikashi
DIR=<repo>/songs/${SONG}-v091
OUT=<repo>/songs/_assets/${SONG}/audio/en
PY=/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python

"$PY" - "$DIR/data.json" "$OUT" <<'PYEOF'
import sys, json, re, subprocess
from pathlib import Path
data, out = json.load(open(sys.argv[1])), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
def rom_uid(r): return re.sub(r'^-+|-+$','',str(r or '').replace(' ','-').replace('·','').replace('/','_'))
ONLY = set()   # e.g. {'v1_katte-ne','v1_nanbo'}; empty = all
jobs = []
for s in data['sections']:
    for w in s['words']:
        uid = rom_uid(w.get('uid') or w['rom']); base = f"word_{s['id']}_{uid}"
        if ONLY and uid not in ONLY: continue
        if w.get('en_speak'): jobs.append((w['en_speak'], f"{base}_en.mp3"))
        ctx = w.get('context_speak') or w.get('context')
        if ctx: jobs.append((ctx, f"{base}_ctx.mp3"))
CJK = re.compile(r'[぀-ヿ㐀-鿿ｦ-ﾟ]'); MAC = re.compile(r'[āēīōūâîûêôĀĒĪŌŪ]')
bad = [(t[:40],f) for t,f in jobs if CJK.search(t) or MAC.search(t)]
assert not bad, ("TTS-SAFETY: clean the text", bad)
from kokoro import KPipeline; import soundfile as sf, numpy as np
pipe = KPipeline(lang_code='a')
def loudnorm(wav, mp3):
    p1 = subprocess.run(['ffmpeg','-y','-i',wav,'-af','loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json','-f','null','-'],capture_output=True,text=True)
    j = json.loads(p1.stderr[p1.stderr.rindex('{'):p1.stderr.rindex('}')+1])
    af = (f"loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
          f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}")
    subprocess.run(['ffmpeg','-y','-i',wav,'-af',af,'-codec:a','libmp3lame','-q:a','2',mp3],capture_output=True,check=True)
for text, name in jobs:
    tts = text.replace('—',', ').replace('–',', ')
    tts = tts if tts.rstrip().endswith(('.','!','?')) else tts + '.'
    chunks = [a for _,_,a in pipe(tts, voice='am_michael', speed=0.95)]
    audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    wav = str(out / (name.replace('.mp3','.wav'))); sf.write(wav, audio, 24000)
    loudnorm(wav, str(out / name)); Path(wav).unlink()
    print('regen', name)
print(f'DONE {len(jobs)} clips')
PYEOF
```

**To regenerate a line explainer** (`LINE_EXPLAIN` value, §2.8): same loop, but the job is `(value, f"line_{hashlib.sha1(value.encode()).hexdigest()[:8]}_explain.mp3")`. A section intro is `(sec['speak_en'], f"section_{sec['id']}_intro.mp3")`.

Then fix the manifest + gate:

```bash
cd <repo>/
# 1. Hand-edit songs/<slug>/tts_manifest.json: for each changed clip, set entry[1] (key)
#    AND entry[2] (spoken) to the new en_speak/context (filename entry[3] unchanged). key === spoken.
#    For a changed LINE_EXPLAIN value, entry[3] (filename) ALSO changes (sha8 of the new text).
python3 tools/bump_asset_versions.py
# validate_tts_safety: --data defaults to the manifest's sibling data.json (lines 95–104),
# so the manifest path alone is sufficient (per-word en_speak/jp_speak assertions still run):
python3 tools/validate_tts_safety.py songs/inochi-mijikashi-v091/tts_manifest.json
KOKORO_PY=/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python \
  python3 tools/songcraft/verify_en_audio.py songs/inochi-mijikashi-v091   # add --fix to regen flagged ctx/gloss
```

Write clips to `songs/_assets/<song>/audio/...`, never a per-build copy. After any JP clip change, re-run forced alignment (`timing.json` via MMS_FA) or kana-lighting desyncs.

---

## 4. Build / deploy / verify / promote

### 4.0 What a canonical (LEAN) dir contains

```
songs/inochi-mijikashi-v091/
├── index.html            (660 KB)
├── data.json             (138 KB)
├── tts_manifest.json     (27 KB)
└── timestamp-recorder/   (dev tool; index.html only)
```

Ships **no `audio/`, `pitch_data/`, `images/`.** Those live once at `songs/_assets/inochi-mijikashi/{audio,pitch_data,images,kit}/`. CF Pages caps a deployment at **20,000 files**; ~94 accumulated fat copies once blew it and broke the v0.31 build. Lean builds grow ~4 files/build. When you `cp -R`, you inherit lean — **do NOT re-add audio.** If audio itself changes, edit `songs/_assets/`.

### 4.1 `_redirects` — a new inochi build needs ZERO new rules

The file ends (lines 92–94) with the generic `:dir` catch-all (the LAST rules):

```
/songs/:dir/audio/*       /songs/_assets/inochi-mijikashi/audio/:splat       200
/songs/:dir/pitch_data/*  /songs/_assets/inochi-mijikashi/pitch_data/:splat  200
/songs/:dir/images/*      /songs/_assets/inochi-mijikashi/images/:splat      200
```

`:dir` matches any `songs/<anything>/` not caught by a more-specific rule above. Silhouette has explicit per-dir rules (lines 8–91) above it; every inochi build (random or `v0N`) falls through. **A new inochi build needs NO `_redirects` edit.** Do NOT add per-dir inochi rules — CF honors only ~100 splat rules; piling them risks pushing the catch-all past the cap → "no sound." `validate_song.py::resolve_song_folder()` (446–483) mirrors CF: first rule whose source equals THIS dir name wins.

**A SECOND (non-inochi) song is different:** it needs its own `songs/_assets/<song>/{audio,pitch_data,images}/` plus **3 explicit rules placed ABOVE the inochi `:dir` catch-all** (like silhouette's blocks), or the catch-all rewrites its audio to inochi's.

### 4.2 Per-song refs to update inside a new dir's `index.html` (EXACT — 3 slug refs + chip + `YT_ID`)

| What | Line | v091 value | New song? | inochi update? |
|---|---|---|---|---|
| `og:url` meta | 51 | `<meta property="og:url" content="https://manaoke.app/songs/inochi-mijikashi-v091/">` | change to new dir | change to new dir |
| canonical link | 54 | `<link rel="canonical" href="https://manaoke.app/songs/inochi-mijikashi-v091/">` | change to new dir | change to new dir |
| `const SONG` | 5803 | `const SONG = 'inochi-mijikashi-v091';` | change to new dir | change to new dir |
| version chip | 5640 | `<div class="u-version" aria-hidden="true">v0.91</div>` | bare slug / `v0.N` | bare slug / `v0.N` |
| **`const YT_ID`** | **5804** | **`const YT_ID = '7cCL0owFBqk';`** | **CHANGE to the new song's YouTube id** | **UNCHANGED (same video)** |

**`const YT_ID` is load-bearing for a NEW song.** The embedded IFrame player is created with `videoId: YT_ID` (line 8094) — the hardcoded const, **NOT** `data.youtube_id`. (Only `shareLyricLine` at 6966 and the fallback at 11308 read `data.youtube_id`.) So a new song that edits only `data.json` `youtube_id` **silently embeds inochi's video `7cCL0owFBqk`.** Set `YT_ID` (line 5804) and `data.json` `youtube_id` to the same id. For an inochi **update** (Flow B) `YT_ID` is unchanged.

`const SONG` builds `SONG_DATA_URL = `/songs/${SONG}/data.json?v=bd4fff29`` (line 5805; `?v=` managed by bump). **CLAUDE.md also names `CH_LIBRARY` and `currentSlug()` — those are NOT in the current v091 page (stale doc); the page derives its slug at runtime.** Confirm after copy: `grep -n "inochi-mijikashi-v096" index.html` must return exactly **3** (og:url, canonical, `const SONG`) — the chip (`v0.91`, a dot form) and `YT_ID` (a YouTube id, not the slug) are **separate** edits this slug-grep does NOT catch, so verify them by line. `og:image` carries no slug. `data.json` `slug` stays unsuffixed `inochi-mijikashi` across all versions.

### 4.3 Random-slug PREVIEW (Stage 1)

Every change ships first to a random-suffix dir (pure RNG entropy, unguessable, undeducible):

```
python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(6)))"
```

Chip on a random build = bare string, **no `v` prefix** (`<div class="u-version" aria-hidden="true">k9p3qr</div>`). **Do NOT touch root `index.html` `url:` for a random build** — root only ever points at the last promoted `v0.N`.

### 4.4 `tools/bump_asset_versions.py`

Run from repo root, **no args**. Globs `songs/*/index.html`; for `data.json` + `tts_manifest.json` computes `sha256(sibling)[:8]` and rewrites every reference immediately followed by a JS string delimiter (`'`,`"`,`` ` ``) to `?v=<sha8>`. Quote-anchored, idempotent. (Any path argument is ignored — `main()` only globs.) Lets the JSON be `Cache-Control: public, max-age=31536000, immutable` while HTML is `no-cache`. **Run before every commit that changed `data.json` / `tts_manifest.json`.**

### 4.5 Validate before deploy

```
python3 tools/validate_song.py songs/inochi-mijikashi-<slug>
python3 tools/validate_tts_safety.py songs/inochi-mijikashi-<slug>/tts_manifest.json
python3 tools/songcraft/line_explainers.py check songs/inochi-mijikashi-<slug>   # every-line tail gate (exit 0)
```

**`validate_tts_safety.py` takes the manifest path positionally; `--data` defaults to the manifest's sibling `data.json` (lines 95–104), so the manifest path alone is sufficient** — the per-word `en_speak`/`jp_speak` and section-`speak_en` assertions still run. Passing `--data songs/inochi-mijikashi-<slug>/data.json` is equivalent (used in §3.7); the bare form is the canonical invocation, use it consistently.

- `validate_song.py`: exit 0 = clean/warnings, 1 = ≥1 ERROR, 2 = bad usage. Checks **E1–E6** (E1 line/romaji coverage incl. uncovered-kana-no-study-word; E2 `LINE_TR` coverage — parsed from `index.html`, §2.8; E3 `LINE_EXPLAIN` orphans — parsed from `index.html`; E4 word-audio existence `audio/jp/word_<sid>_<uid>.mp3` + `_en.mp3` [+ `_ctx.mp3` when `context`]; E5 manifest integrity incl. every `speak_en`/`LINE_EXPLAIN` value being a byte-identical manifest key; E6 schema — `REQUIRED_TOP_KEYS`, `REQUIRED_WORD_FIELDS = jp,rom,en,en_speak`). **All E\* must be 0.**
- `validate_tts_safety.py`: exit 0 = clean, 1 = ≥1 FAIL, 2 = bad usage. Must end `✓ TTS safety: clean …`.
- `line_explainers.py check` (§2.8.1): exit 0 = every unique lyric line has the full drill tail (JP-line clip + `LINE_EXPLAIN` entry + en-US clip), 1 = gaps. This is the **every-line standard gate** — neither validator enforces coverage (validate_song E3/E5 only check orphans + key↔manifest integrity). It also warns on orphan `LINE_EXPLAIN` keys and on a pre-v090 page that lacks the drill-tail runtime.

### 4.6 Headless verify at 390px (against the LIVE url)

```
cd /tmp && npm install puppeteer-core pngjs --no-save --silent
node <repo>/tools/songcraft/stress_cards.js  https://manaoke.app/songs/inochi-mijikashi-<slug>/
node <repo>/tools/songcraft/verify_reveal.js https://manaoke.app/songs/inochi-mijikashi-<slug>/ 16.8 23.4 1
```

- `stress_cards.js <url> [maxLines]`: iPhone 390×844 DPR2 hasTouch, opens every word card via `#openCardsBtn`; flags front-face void ratio > 0.45, half-loaded scene image, `.pb-jisho` href must contain `jisho.org/search/`, **front JP audio `HEAD` must be 200** (URL `audio/jp/word_<sectionId>_<uid>.mp3`, same `rom_uid`), lightbox probe, single-char orphan rows. Exit 0 = no defects; this is how you confirm the lean-audio rewrite serves audio (404 = missing rule/broken catch-all).
- `verify_reveal.js <url> [fromSec=16.8] [toSec=23.4] [lineIdx=1] [width=390] [remPx]`: fake-clock karaoke wipe; reveal band sane, fill monotonic, sung rows never re-dim. `lineIdx` counts intro card as 0; `fromSec/toSec` are VIDEO time (include `yt_offset_ms`).

**Word-by-word play-order + は check:** no committed harness; instrument ad-hoc in /tmp puppeteer — wrap `HTMLMediaElement.prototype.play` to log `this.currentSrc`, drive the drill, assert per word **JP → gloss, then full line, then explainer**. For は assert the card plays `word_<sid>_wa.mp3` (the わ recording), confirming "wa". **For a new song, also confirm the embedded video matches** (the player loads `YT_ID`, §4.2, not inochi's `7cCL0owFBqk`).

### 4.7 Deploy

From local CLI (`cd ~/manaoke-site && claude`): commit locally as needed, **push once** to `main` (your own remote). CF Pages auto-builds in ~30–60s. No PR. Hand over the **bare URL** on its own line (no backticks/markdown):

```
https://manaoke.app/songs/inochi-mijikashi-<slug>/
```

CF token in Keychain: `security find-generic-password -s cloudflare-api-token -w`. A `build failure` stage = nothing deployed; previous deploy stays live.

### 4.8 Promote (Stage 2) + landing accent

Triggered by "keep it"/"make it main"/"promote it". Monotonic, no semver: after v0.91 → **v0.92** (slug `v092` no dot; chip `v0.92` dot). Copy the most-recent random dir → `v0N`, fix the per-song refs (§4.2: og:url, canonical, `const SONG`, chip; `YT_ID` unchanged for inochi), **repoint root**, bump, push. Old dirs stay as rollback (roll back by URL).

**Landing card-accent rule:** root `index.html` `SONGS[].cardAccent` MUST equal the song page's living-gradient dominant `--field-c1` (or its vivid variant if `--field-c1` is muted for white-text contrast). Inochi = **`cardAccent:'#d14e86'`** (root line 1186; the song page's literal `--field-c1` is muted dusty-rose `172,114,132` at v091 line 1018/345 — `#d14e86` is its vivid variant). Silhouette = `--field-c1:198,72,22` → `cardAccent:'#c64816'`. Promotion does NOT change `cardAccent` unless `--field-c1` changed.

### 4.9 Self-hosted subset fonts (per-song — a NEW song MUST regenerate or it tofus)

As of **v0.96** the song page **self-hosts** its two web fonts instead of Google Fonts. A/B (7 cold headless runs, 390px): for a CJK page Google served **568 KB across 32 requests** (M PLUS Rounded 1c is sliced into many unicode-range chunks; the song's scattered kanji pull most of them); the targeted subset is **261 KB in 5 same-origin files, 0 cross-origin** — lighter, fewer requests, no third-party dependency, ~1 s faster to real-font on a slow link (first paint unchanged). Winning config = lazy `@font-face` + `font-display:swap`, **no preload** (preloading cost ~160 ms of first paint on a slow link for no real gain).

**Where the files live:** 5 woff2 at **`/fonts/<song>/`** — NOT in the lean song dir (shared across every build of the song; `_headers` caches `/fonts/*.woff2` immutable, one wildcard spans the subdir): `MPLUSRounded1c-{400,500,700,800}.subset.woff2` + `DotGothic16.song.subset.woff2`. The page `<head>` has an inline `<style>` of **5 `@font-face`** rules (M PLUS 400/500/700/800 + DotGothic16 400), each `src:url('/fonts/<song>/…woff2?v=<sha8>')`; **no** Google `<link>`/`<noscript>`, **no** `fonts.googleapis`/`gstatic` preconnects. (The landing `/index.html` self-hosts its own pair — `DotGothic16.subset` + `VT323.subset` — at `/fonts/` the same way.)

**The subset is GLYPH-SPECIFIC to the song's text** (built from that song's `index.html` + `data.json`, + full-kana insurance). **Cloning the canonical page inherits inochi's `@font-face` pointing at `/fonts/inochi-mijikashi/` — a new song's different kanji render as silent fallback/tofu unless you regenerate.** Recipe (also in `~/manaoke-site/fonts/README.md`; source TTFs from `github.com/google/fonts` `ofl/mplusrounded1c` + `ofl/dotgothic16`; `pyftsubset` ships with fonttools+brotli):

```bash
# 1) the song's exact charset
python3 - songs/<dir>/index.html songs/<dir>/data.json <<'EOF'
import sys; c=set()
for p in sys.argv[1:]: c|=set(open(p,encoding='utf-8').read())
open('charset.txt','w').write(''.join(sorted(x for x in c if ord(x)>=0x20)))
EOF
# 2) subset each weight -> woff2 (Regular/Medium/Bold/ExtraBold = 400/500/700/800)
UNI="U+0020-00FF,U+3000-30FF,U+2026,U+FF01-FF60,U+2018-201F,U+2022,U+2190-2193"
pyftsubset MPLUSRounded1c-Regular.ttf --text-file=charset.txt --unicodes="$UNI" \
  --layout-features='*' --flavor=woff2 --output-file=fonts/<song>/MPLUSRounded1c-400.subset.woff2
#   …repeat 500/700/800, and DotGothic16-Regular.ttf -> fonts/<song>/DotGothic16.song.subset.woff2
# 3) point the 5 @font-face src urls at /fonts/<song>/…?v=<sha8>
shasum -a 256 fonts/<song>/MPLUSRounded1c-400.subset.woff2 | cut -c1-8   # -> the ?v= value
```

**Verify by SCREENSHOT at 390px** that lyrics + LCD wells + title render in the real faces — a missing glyph is silent (falls back to Hiragino / monospace, not an error). For an inochi **update** (Flow B), fonts only need touching if a lyric edit **introduces a new kanji** — then re-subset `/fonts/inochi-mijikashi/` and bump the `?v=` hashes; non-lyric edits need nothing.

---

## Flow A — ADD a brand-new song

A new song = its own `_assets`, its own `_redirects` rules, its own data + clips, **and its own `LINE_TR`/`LINE_EXPLAIN` + `YT_ID` re-authored inside the cloned `index.html`.**

1. **Decide the conceptual `slug`** (unsuffixed, stable, e.g. `new-song`). Pick the first build dir name `songs/new-song-<rand>` (RNG, §4.3).
2. **Author `data.json`** per §2: 16 top-level keys (incl. `youtube_id` = the new video), `sections[]` with `id`s + `words[]` (every word: `jp,rom,jp_speak,en,en_speak,context,gloss,hint,particle`; `uid`/`only_lines` on collisions; give each section a `speak_en` so the drill tail can speak the full JP line, §1.3/§2.2), `apple_lyrics` with `lines[]`+`kana_timings[]` (7 keys in order, §2.1). Apply the spoken-English STYLE GUIDE (§2.3), the `jp_speak`/は rule (§2.4), and ensure coverage (§2.5) — every sung kana span has a tappable study word.
3. **Generate every clip** (§3.1–§3.2, §3.7) into `songs/_assets/new-song/audio/{en,jp}/`: per word `.wav` master then COMPRESS to served `.mp3` (`jp_to_mp3.py`; JP via Qwen3 `Ono_Anna`/AivisSpeech/human_audio), `_en.mp3`,`_ctx.mp3`,`_gloss.mp3` (Kokoro `am_michael` speed 0.95, 24k, two-pass loudnorm `I=-16:TP=-1.5:LRA=11`, mp3 `-q:a 2`, parler python — use the §3.7 inline loop, NOT gen_en_audio.py); section intros `section_<id>_intro.mp3`; line explainers `line_<sha8>_explain.mp3` (SHA1[:8] of the `LINE_EXPLAIN` value); full lines `line_uNN.mp3`. Lone は → `cp` the canonical わ clip (§3.6) — but that 750ca1… clip is **inochi's female voice**; a NEW song must render its OWN canonical わ in that song's JP voice (only inochi updates reuse 750ca1…).
4. **Build `tts_manifest.json`** (§3.4): 4-tuples `[lang,key,spoken,filename]`, `key===spoken`, all five entry classes incl. kana aliases (and the line-explainer entries whose key/spoken == the `LINE_EXPLAIN` value).
5. **Clone the canonical page:** `cp songs/inochi-mijikashi-v096/index.html songs/new-song-<rand>/index.html` (latest promoted — carries the every-line explainer standard AND the §1.9 how-to QUICK GUIDE; and bring `timestamp-recorder/`). Edit the 3 slug refs + chip **and `const YT_ID` (line 5804)** to the new song (§4.2). Keep all markup/CSS/JS from §1 byte-identical. **Then regenerate the font subset for THIS song's glyphs and repoint the 5 `@font-face` src urls to `/fonts/<song>/…?v=<sha8>` (§4.9)** — the clone inherits inochi's subset, so a new song's kanji tofu otherwise.
6. **Re-author `LINE_TR` and `LINE_EXPLAIN` in the cloned `index.html`** (§2.8) — delete inochi's entries; write one `LINE_TR` entry per lyric line (`{en, full?}`, non-empty `en` so E2 passes) and one `LINE_EXPLAIN` entry **per lyric line** (v0.92 standard, §2.8 — clean spoken English, no romaji/CJK). Their keys = `lineTrKey(<JP line>)`. Without this, every line's inline English blanks and no explainer audio plays. Fastest path: `line_explainers.py check songs/new-song-<rand> --template gaps.json` lists every line needing an explainer (after step 3's JP line clips exist), author the values, then `build` renders+wires them (§2.8.1).
7. **Add `_redirects` rules** (§4.1): 3 explicit rules for `songs/new-song-<rand>/...` (audio/pitch_data/images) → `songs/_assets/new-song/...`, **placed ABOVE the inochi `:dir` catch-all**. (Each future build of this song needs its own rules, or convert to its own `:dir`-style block above inochi's.)
8. **Validate:** `python3 tools/validate_song.py songs/new-song-<rand>` (E1–E6 all 0 — E2/E3/E5 now exercise the re-authored dictionaries) and `python3 tools/validate_tts_safety.py songs/new-song-<rand>/tts_manifest.json` (✓ line; `--data` defaults to the sibling) and `python3 tools/songcraft/line_explainers.py check songs/new-song-<rand>` (must exit 0 — every line has the tail). Fix until clean.
9. **Bump:** `python3 tools/bump_asset_versions.py`.
10. **Landing:** add a `SONGS[]` entry in root `index.html` with `cardAccent` = the page's `--field-c1` dominant/vivid variant (§4.8). For a private preview do not yet repoint anything public-facing beyond adding the card if desired.
11. **Deploy:** `git add -A && git commit -m "new-song: initial (preview <rand>)" && git push origin main` (push once).
12. **Headless verify at 390px** against the live url (§4.6); also instrument the drill play-order + は clip, and **confirm the embedded video is the new song's `YT_ID`, not inochi's**. Confirm audio HEAD 200 (proves the new `_redirects` rules resolve).
13. **Hand over the bare URL:** `https://manaoke.app/songs/new-song-<rand>/`. On "promote", run the promote step (§4.8) to `new-song-v0.1` (or the agreed number), fix the per-song refs to the `v0N` form, add the `v0N` `_redirects` rules above the catch-all, repoint root, bump, push.

## Flow B — UPDATE an existing (inochi) song

```
cd <repo>/
```

1. **RNG slug:** `RAND=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(6)))")`
2. **Lean copy:** `cp -R songs/inochi-mijikashi-v096 songs/inochi-mijikashi-$RAND` (latest promoted — clone this, NOT an older vN, or you regress the every-line explainers, the §1.9 how-to, and other recent work; inherits no audio — do NOT re-add it).
3. **Make changes inside `songs/inochi-mijikashi-$RAND/` only.** If editing words/spoken text, follow §2 (style guide, `jp_speak`/は, coverage) and §3 (regenerate the touched clips via the §3.7 inline loop into `songs/_assets/inochi-mijikashi/audio/...`, never a per-build copy; update the matching manifest `key`+`spoken`; for lone は `cp` the canonical わ clip md5 `750ca1c12e8cade384c0c2ec2a9043d3`). Remember `verify_en_audio.py` does NOT sweep `_en.mp3` directly (§3.5) — regenerate those from `data.json` `en_speak`. **If you change any lyric LINE TEXT, update the matching `LINE_TR` and `LINE_EXPLAIN` keys (whitespace-stripped JP, `lineTrKey`) in `index.html` (§2.8) too** — else validate_song E2/E3 flag orphans and the inline translation/explainer silently blank; a changed `LINE_EXPLAIN` value also needs its line-explainer clip + manifest entry regenerated (its `sha8` changes). **Fonts (§4.9):** only if a lyric edit adds a NEW kanji — re-subset `/fonts/inochi-mijikashi/` + bump the `?v=` hashes; otherwise untouched.
4. **Update the 3 slug refs + chip** (§4.2) to `inochi-mijikashi-$RAND`: og:url (~51), canonical (~54), `const SONG` (~5803), chip (~5640, bare `$RAND`, no `v`). **`const YT_ID` (5804) stays `'7cCL0owFBqk'` — same song, same video.** **Do NOT touch root `index.html` `url:`.** Verify: `grep -n "inochi-mijikashi-v096" songs/inochi-mijikashi-$RAND/index.html` returns NOTHING.
5. **Validate:** `python3 tools/validate_song.py songs/inochi-mijikashi-$RAND` (E* all 0), `python3 tools/validate_tts_safety.py songs/inochi-mijikashi-$RAND/tts_manifest.json` (✓ line), and `python3 tools/songcraft/line_explainers.py check songs/inochi-mijikashi-$RAND` (exit 0 — every-line tail intact).
6. **Bump:** `python3 tools/bump_asset_versions.py`.
7. **Deploy:** `git add -A && git commit -m "inochi: <what changed> (preview $RAND)" && git push origin main` (push once; CF deploys ~30–60s). No `_redirects` edit needed (inochi catch-all already covers `$RAND`).
8. **Headless verify at 390px** (§4.6) against `https://manaoke.app/songs/inochi-mijikashi-$RAND/`: `stress_cards.js` (audio HEAD 200), `verify_reveal.js 16.8 23.4 1`, and ad-hoc drill play-order / は-clip instrumentation.
9. **Hand over the bare URL:** `https://manaoke.app/songs/inochi-mijikashi-$RAND/`
10. **On "promote"** (§4.8): `cp -R songs/inochi-mijikashi-$RAND songs/inochi-mijikashi-v092`; set its 3 slug refs + chip to the version form (og:url/canonical `…-v092/`, `const SONG='inochi-mijikashi-v092'`, chip `v0.92` dot; `YT_ID` unchanged); **repoint root** `index.html` line ~1181 `url: '/songs/inochi-mijikashi-v092/',` (leave `cardAccent '#d14e86'` unless `--field-c1` changed); `python3 tools/bump_asset_versions.py`; re-validate the `v092` dir; `git add -A && git commit -m "promote inochi v0.92" && git push origin main`. Old `$RAND` and prior `v0N` dirs stay as rollback (roll back by URL).

---

**Key paths:** `<repo>/CLAUDE.md`, `<repo>/_redirects`, `<repo>/tools/bump_asset_versions.py`, `<repo>/tools/validate_song.py`, `<repo>/tools/validate_tts_safety.py`, `<repo>/tools/songcraft/{gen_en_audio.py (silhouette-only recipe ref),verify_en_audio.py,sweep_clip_physics.py,stress_cards.js,verify_reveal.js}`, `<repo>/tools/human_audio/{fetch.py,tofugu.py}`, `<repo>/songs/inochi-mijikashi-v091/{index.html (carries LINE_TR ~6324, LINE_EXPLAIN ~6362, const YT_ID 5804),data.json,tts_manifest.json}`, `<repo>/songs/_assets/inochi-mijikashi/{audio,pitch_data,images,kit}/`, `<repo>/index.html` (root landing: inochi `url:` ~1181, `cardAccent:'#d14e86'` ~1186). Parler Kokoro python: `/opt/homebrew/Caskroom/miniforge/base/envs/parler/bin/python`.
<usage>subagent_tokens: 121939
tool_uses: 18
duration_ms: 553996</usage>

## Audio provenance + language-safety gates (Round 7, 2026-07-03)

Non-negotiable for every song build (all enforced, not advisory):
1. **Every playback site is AUDIO_V-versioned** — `lint_template.py` (in the
   assemble step) fails the build on any `new Audio(...)`/`.src=` audio site
   not routed through `_withAudioV`. In-place clip replacement without a URL
   change NEVER reaches devices (immutable 1-yr cache).
2. **Short JP words (≤2 morae) prioritize the human voice dictionaries**
   (library → JPod101 → Tofugu). Kokoro fallback only with a passing
   large-v3 read-back; provenance `kokoro_dictmiss`. `validate_song` E12.
3. **Lone particles never Kokoro** (E9, unchanged) — curated clip or
   Qwen/AivisSpeech carrier-cut, fail-loud otherwise.
4. **An English voice never says a Japanese word** — anywhere: card
   definitions/context/gloss, line explainers, podcast. `jp_token_detect`
   + JP-clip splicing in `gen_audio` (en_splice manifest) and `{"clip":}`
   entries in podcast_script. `validate_tts_safety` E11 + podcast gate.
