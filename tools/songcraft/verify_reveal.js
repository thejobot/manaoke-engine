// Reveal regression harness — drives a song page with a FAKE clock and
// verifies the karaoke reveal frame by frame, so reveal correctness is
// LOCKED by a test instead of being re-judged by eye after every change.
//
// How: hooks window.YT at construction, wraps the player instance's
// getCurrentTime/getPlayerState/seekTo so window.__fakeT becomes the song
// clock. The page's own tick loop consumes it — every line of production
// reveal code runs unmodified.
//
// Checks per sampled frame (default 60ms steps over the given range):
//   1. wipe vars sane: 0 <= rowTop < rowBottom <= card-text CSS height + 2
//   2. fill monotonic within a row (never moves backwards > 2px)
//   3. ROW STABILITY: once a row's band has moved BELOW it, the pixels of
//      that row never dim again (catches "row 1 re-reveals when row 2
//      starts") — measured from real screenshots, mean luminance per row.
//
// Usage: node verify_reveal.js <url> [fromSec] [toSec] [lineIdx] [width] [remPx]\n//   NOTE lineIdx counts the INTRO CARD as 0 — the first sung line is 1.\n//   fromSec/toSec are VIDEO time (include the song's yt_offset_ms).
//   e.g. node verify_reveal.js https://manaoke.app/songs/inochi-mijikashi-qm92iw/ 16.8 23.4 1
// Modules resolve from the CWD (run it from a dir with puppeteer-core +
// pngjs installed, e.g. /tmp), not from this script's location.
const { createRequire } = require('module');
const creq = createRequire(process.cwd() + '/');
const puppeteer = creq('puppeteer-core');
const { PNG } = (() => { try { return creq('pngjs'); } catch(_) { return {}; } })();

const URL = process.argv[2];
const FROM = parseFloat(process.argv[3] || '16.8');
const TO = parseFloat(process.argv[4] || '23.4');
const LINE = parseInt(process.argv[5] || '1', 10);
const WIDTH = parseInt(process.argv[6] || '390', 10);
const REMPX = parseFloat(process.argv[7] || '0');   // override root font-size (emulates iOS text-size settings that make lines wrap)
if (!URL){ console.error('need url'); process.exit(2); }

(async () => {
  const b = await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless:'new'});
  const p = await b.newPage();
  await p.setViewport({width:WIDTH, height:844, deviceScaleFactor:1});
  await p.goto(URL, {waitUntil:'networkidle2'});
  await new Promise(r => setTimeout(r, 3000));
  // The page's `player` variable is closure-scoped, but the iframe API keeps
  // a registry: YT.get('<element id>') returns the SAME instance the page
  // holds. Wrapping methods on that object in place is race-free — the
  // page's tick loop calls them on the same reference.
  await p.waitForFunction(() => window.YT && window.YT.get && window.YT.get('player') && window.YT.get('player').getCurrentTime, {timeout: 20000});
  await p.evaluate(() => {
    const inst = window.YT.get('player');
    window.__ytp = inst;
    const gct = inst.getCurrentTime.bind(inst);
    inst.getCurrentTime = () => (window.__fakeT != null ? window.__fakeT : gct());
    if (inst.getPlayerState){
      const gps = inst.getPlayerState.bind(inst);
      inst.getPlayerState = () => (window.__fakeT != null ? 1 : gps());
    }
    if (inst.seekTo){
      const st = inst.seekTo.bind(inst);
      inst.seekTo = (s, a) => { if (window.__fakeT != null) window.__fakeT = s; else st(s, a); };
    }
    inst.__wrapped = true;
  });

  if (REMPX) await p.evaluate(px => { document.documentElement.style.fontSize = px + 'px'; }, REMPX);
  // jump near the test window, then start the fake clock AND convince the
  // page it's PLAYING: the play-state machine is event-driven (onStateChange),
  // so a fake clock alone leaves the wipe frozen — the first version of this
  // harness passed VACUOUSLY because of that. onPlayerState is a top-level
  // function declaration, hence reachable on window.
  await p.evaluate((from, line) => {
    window.goToLine(line, {seek:false, play:false, manual:true});
    window.__fakeT = from;
    if (typeof window.onPlayerState === 'function') window.onPlayerState({ data: 1 });
  }, FROM, LINE);
  await new Promise(r => setTimeout(r, 700));
  // goToLine doesn't always land the card in the viewport (far lines, big
  // rem). Offscreen cards aren't painted — every clip screenshot comes back
  // BLACK and the pixel row-stability check compares black to black, i.e.
  // passes blind. Force the card into view; the luminance floor after the
  // run asserts we actually saw text.
  await p.evaluate(() => {
    const c = document.querySelector('.card.is-active');
    if (c) c.scrollIntoView({block:'center'});
  });
  await new Promise(r => setTimeout(r, 500));

  const card = await p.evaluate(() => {
    const c = document.querySelector('.card.is-active');
    const t = c.querySelector('.card-face.front .card-text');
    const r = t.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), cssH: t.offsetHeight };
  });

  const samples = [];
  const frames = [];
  for (let t = FROM; t <= TO; t += 0.06){
    await p.evaluate(ft => { window.__fakeT = ft; }, +t.toFixed(3));
    await new Promise(r => setTimeout(r, 70));   // > tick interval
    const s = await p.evaluate(() => {
      const c = document.querySelector('.card.is-active');
      if (!c) return null;
      const te = c.querySelector('.card-face.front .card-text');
      const st = te ? te.style : null;
      const raw = st ? st.getPropertyValue('--line-fill-px') : '';
      return st ? {
        idx: c.dataset.idx,
        written: raw !== '',          // vars not yet written this line = skip
        fill: parseFloat(raw) || 0,
        top: parseFloat(st.getPropertyValue('--wipe-row-top-px')) || 0,
        bot: parseFloat(st.getPropertyValue('--wipe-row-bottom-px')) || 0,
      } : null;
    });
    const shot = PNG ? await p.screenshot({clip: {x: card.x, y: card.y, width: card.w, height: card.h}}) : null;
    samples.push({t: +t.toFixed(2), ...s});
    if (shot) frames.push({t: +t.toFixed(2), shot});
  }
  await b.close();

  // ---- assertions -------------------------------------------------------
  let fails = [];
  let prev = null;
  for (const s of samples){
    if (!s || s.idx === undefined) continue;
    if (String(s.idx) !== String(LINE)) { prev = null; continue; }   // line advanced; reset
    if (!s.written) { prev = null; continue; }   // pre-first-write frames are vacuum, not data
    // ±7px tolerance: glyph boxes (JP ascenders/descenders) legitimately
    // overrun the line box; what must NEVER happen is the band crossing into
    // a DIFFERENT row — the pixel row-stability check below owns that.
    if (s.top < -7 || s.bot > card.cssH + 7 || s.bot < s.top)
      fails.push(`t=${s.t} band out of box: top=${s.top} bot=${s.bot} cssH=${card.cssH}`);
    // Before the first token sings, the page legitimately writes a
    // whole-element band [0, cssH] — that's "no row locked yet", not a row.
    const rowLocked = !(s.top === 0 && s.bot >= card.cssH - 1);
    const prevLocked = prev && !(prev.top === 0 && prev.bot >= card.cssH - 1);
    if (prev && rowLocked && prevLocked){
      const sameRow = Math.abs(s.top - prev.top) < 3;
      if (sameRow && s.fill < prev.fill - 2)
        fails.push(`t=${s.t} fill went BACKWARDS within a row: ${prev.fill} -> ${s.fill}`);
      if (s.top < prev.top - 3)
        fails.push(`t=${s.t} band moved back UP: top ${prev.top} -> ${s.top}`);
    }
    prev = s;
  }

  // row-stability via pixels: per frame, mean luminance of each row band
  if (PNG && frames.length){
    const rowOf = (png, y0, y1) => {
      let sum = 0, n = 0;
      for (let y = Math.max(0,y0); y < Math.min(png.height, y1); y++)
        for (let x = 0; x < png.width; x += 2){
          const i = (png.width * y + x) << 2;
          sum += 0.299*png.data[i] + 0.587*png.data[i+1] + 0.114*png.data[i+2]; n++;
        }
      return n ? sum / n : null;   // degenerate band (e.g. tops -4 and 0) = no data, not 0
    };
    // row boundaries from the samples' observed distinct tops
    const tops = [...new Set(samples.filter(s=>s&&String(s.idx)===String(LINE)).map(s => Math.round(s.top/4)*4))].sort((a,b)=>a-b);
    const lum = frames.map(f => {
      const png = PNG.sync.read(Buffer.from(f.shot));
      return { t: f.t, rows: tops.map((tp, i) => rowOf(png, tp, (tops[i+1] ?? card.cssH))) };
    });
    // BLINDNESS GUARD: somewhere in the run, some row must have been visibly
    // bright (sung text on the dark theme). All-near-black frames mean the
    // card never painted inside the clip — that's a blind run, not a pass.
    const brightest = Math.max(...lum.flatMap(f => f.rows).filter(v => Number.isFinite(v)), -1);
    if (!(brightest >= 25))
      fails.push(`BLIND RUN: max row luminance ${brightest.toFixed(1)} — card not visibly painted in the clip; pixel checks prove nothing`);
    // Once the band has moved past row i, row i must stay as bright as its
    // brightest moment in the WHOLE run. The peak must NOT be built only
    // from post-pass frames: the re-reveal bug collapses luminance exactly
    // AT the pass moment and then only ever brightens (the row re-reveals
    // with the fill), so a post-pass-only peak made the check vacuously
    // true — the mask-order regression sailed through it. 12% tolerance
    // absorbs the singing-glow halo that inflates the in-row peak slightly.
    for (let i = 0; i < tops.length - 1; i++){
      let peak = 0;
      for (let fi = 0; fi < lum.length; fi++){
        const s = samples[fi];
        if (!s || String(s.idx) !== String(LINE)) continue;
        const v = lum[fi].rows[i];
        if (Number.isFinite(v) && v > peak) peak = v;
      }
      let passed = false;
      for (let fi = 0; fi < lum.length; fi++){
        const s = samples[fi];
        if (!s || String(s.idx) !== String(LINE)) continue;
        if (s.top > tops[i] + 6) passed = true;          // band moved below row i
        if (passed){
          const v = lum[fi].rows[i];
          if (!Number.isFinite(v)) continue;   // degenerate band — no pixels
          if (peak > 0 && v < peak * 0.88)
            fails.push(`t=${lum[fi].t} ROW ${i} DIMMED after being sung: ${v.toFixed(1)} < peak ${peak.toFixed(1)} (re-reveal bug)`);
        }
      }
    }
  } else {
    console.log('NOTE: pngjs not installed — pixel row-stability check skipped (npm i pngjs)');
  }

  // NON-VACUITY: the wipe must actually have moved during the window —
  // otherwise the harness isn't driving playback and every check above is
  // meaningless (the trap the first version fell into).
  const lineFills = samples.filter(s => s && s.written && String(s.idx) === String(LINE)).map(s => s.fill);
  if (lineFills.length < 5 || Math.max(...lineFills) - Math.min(...lineFills) < 20)
    fails.push(`VACUOUS RUN: wipe fill barely moved (${Math.min(...lineFills)}..${Math.max(...lineFills)}) — playback not driven`);
  console.log(`samples: ${samples.length}, line ${LINE}, window ${FROM}-${TO}s`);
  if (fails.length){ console.log('FAIL'); fails.slice(0,12).forEach(f => console.log('  ✗', f)); process.exit(1); }
  console.log('PASS — band sane, fill monotonic, sung rows stay lit');
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
