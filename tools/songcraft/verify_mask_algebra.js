// Wipe-mask ALGEBRA lock — isolates the mask compositing from timing.
// Loads the song page, jumps to a WRAPPED line, writes the wipe vars BY HAND
// for two states, and measures row-1 luminance from real screenshots:
//   state A: band on row 1, fill at row-1 end        -> row 1 fully sung
//   state B: band on row 2, fill restarted (30px)    -> row 1 must STAY fully sung
// If row-1 luminance drops from A to B, the composite algebra is wrong.
//
// Why this exists: mask-composite evaluates BOTTOM-UP. With layer order
// [fill, row-band, rows-above] and `intersect,add` the algebra silently
// becomes fill ∩ (band ∪ above) — rows above get clipped by the fill, so
// row 1 visibly re-reveals in lockstep with row 2 (shipped bug, 2026-06-11,
// caught by the owner's eye, not by verify_reveal — its matrix never drove a
// wrapped line non-vacuously). Correct order is [rows-above, fill, band]
// with `add,intersect,add` = above ∪ (fill ∩ band). This harness fails on
// the bad order regardless of playback, timing, or tick behavior.
//
// Usage: node verify_mask_algebra.js <url> [lineIdx] [remPx]
//   lineIdx must be a line that WRAPS at remPx (default 2 @ 19px works for
//   inochi; the script exits 2 if the line doesn't wrap so a non-wrapping
//   pick can't produce a vacuous PASS). Run from a dir with puppeteer-core
//   + pngjs installed (e.g. /tmp). Exit 0 = PASS, 1 = algebra broken.
const { createRequire } = require('module');
const creq = createRequire(process.cwd() + '/');
const puppeteer = creq('puppeteer-core');
const { PNG } = creq('pngjs');

const URL = process.argv[2];
const LINE = parseInt(process.argv[3] || '2', 10);
const REMPX = parseFloat(process.argv[4] || '19');

(async () => {
  const b = await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless:'new'});
  const p = await b.newPage();
  await p.setViewport({width:390, height:844, deviceScaleFactor:2});
  await p.goto(URL, {waitUntil:'networkidle2'});
  await new Promise(r => setTimeout(r, 2500));
  if (REMPX) await p.evaluate(px => { document.documentElement.style.fontSize = px + 'px'; }, REMPX);
  await p.evaluate(line => { window.goToLine(line, {seek:false, play:false, manual:true}); }, LINE);
  await new Promise(r => setTimeout(r, 900));
  // goToLine doesn't always land the card in the viewport (far lines, big
  // rem). Offscreen cards aren't painted — screenshots come back BLACK and
  // a black-vs-black comparison "passes". Force it into view, then verify
  // below (luminance floor) that we can actually see text.
  await p.evaluate(() => {
    const c = document.querySelector('.card.is-active');
    if (c) c.scrollIntoView({block:'center'});
  });
  await new Promise(r => setTimeout(r, 500));

  const geo = await p.evaluate(() => {
    const c = document.querySelector('.card.is-active');
    const t = c.querySelector('.card-face.front .card-text');
    const base = t.getBoundingClientRect();
    const k = base.width ? (t.offsetWidth / base.width) : 1;
    const toks = [...t.querySelectorAll('.lyric-layer--upcoming .tok')];
    const tops = [...new Set(toks.map(tk => Math.round((tk.getBoundingClientRect().top - base.top) * k)))].sort((a,b)=>a-b);
    const rows = tops.map((tp,i) => {
      const rowToks = toks.filter(tk => Math.abs((tk.getBoundingClientRect().top - base.top)*k - tp) < 4);
      const last = rowToks[rowToks.length-1].getBoundingClientRect();
      const bot = (last.bottom - base.top) * k;
      return { top: tp, bottom: Math.round(bot), fillEnd: Math.round((last.right - base.left) * k) };
    });
    // force every token sung-class so per-token styling can't confound the mask test
    document.querySelectorAll('.card.is-active .card-face.front .lyric-layer .tok').forEach(el => el.classList.add('sung'));
    return { rect: {x: Math.round(base.x), y: Math.round(base.y), w: Math.round(base.width), h: Math.round(base.height)}, rows, cssH: t.offsetHeight, k };
  });
  if (geo.rows.length < 2){ console.log('LINE DOES NOT WRAP at rem ' + REMPX + ' — rows: ' + geo.rows.length); await b.close(); process.exit(2); }
  console.log('rows:', JSON.stringify(geo.rows), 'cssH:', geo.cssH);

  const setVars = (fill, top, bot) => p.evaluate(v => {
    const t = document.querySelector('.card.is-active .card-face.front .card-text');
    // the page's tick keeps writing these vars even when paused — block its
    // writes (instance-level monkeypatch) and write ours via the original
    if (!t.style.__origSet){
      t.style.__origSet = t.style.setProperty.bind(t.style);
      t.style.setProperty = (n, val, pr) => {
        if (n === '--line-fill-px' || n.startsWith('--wipe-row')) return;
        t.style.__origSet(n, val, pr);
      };
    }
    t.style.__origSet('--line-fill-px', v.fill + 'px');
    t.style.__origSet('--wipe-row-top-px', v.top + 'px');
    t.style.__origSet('--wipe-row-bottom-px', v.bot + 'px');
  }, {fill, top, bot});

  const shoot = async name => {
    const dbg = await p.evaluate(() => {
      const t = document.querySelector('.card.is-active .card-face.front .card-text');
      const sg = t.querySelector('.lyric-layer--sung');
      const cs = sg ? getComputedStyle(sg) : null;
      const mi = cs ? (cs.maskImage && cs.maskImage !== 'none' ? cs.maskImage : cs.webkitMaskImage) : '';
      return {
        fill: t.style.getPropertyValue('--line-fill-px'),
        top: t.style.getPropertyValue('--wipe-row-top-px'),
        bot: t.style.getPropertyValue('--wipe-row-bottom-px'),
        sung: !!sg, op: cs && cs.opacity, vis: cs && cs.visibility,
        maskLayers: (mi || '').split('linear-gradient').length - 1,
        color: sg ? getComputedStyle(sg.querySelector('.tok') || sg).color : null,
      };
    });
    console.log(name, JSON.stringify(dbg));
    // VACUITY GUARD: a syntactically invalid mask-image computes to 'none',
    // which leaves the sung layer fully visible everywhere — the luminance
    // check then "passes" while the reveal is completely broken. Exactly 3
    // gradient layers or the run is meaningless. (Caught a real shipped-
    // almost bug: a stray paren dropped the whole declaration.)
    if (dbg.maskLayers !== 3){
      console.log(`FAIL: sung-layer mask has ${dbg.maskLayers} gradient layers (expected 3) — mask-image invalid or missing, reveal is broken`);
      process.exit(1);
    }
    const buf = await p.screenshot({clip:{x:geo.rect.x, y:geo.rect.y, width:geo.rect.w, height:geo.rect.h}});
    require('fs').writeFileSync('/tmp/mask_' + name + '.png', buf);
    return PNG.sync.read(Buffer.from(buf));
  };
  const rowLum = (png, y0, y1) => {  // viewport clip is 2x DPR; rows are CSS px
    let s=0, n=0;
    for (let y = Math.max(0, y0*2); y < Math.min(png.height, y1*2); y++)
      for (let x = 0; x < png.width; x += 2){
        const i = (png.width*y+x)<<2;
        s += 0.299*png.data[i]+0.587*png.data[i+1]+0.114*png.data[i+2]; n++;
      }
    return s/n;
  };

  const r1 = geo.rows[0], r2 = geo.rows[1];
  // state A: singing end of row 1
  await setVars(r1.fillEnd, r1.top, r1.bottom);
  await new Promise(r => setTimeout(r, 250));
  const A = await shoot('A_row1done');
  // state B: just moved to row 2, fill restarted
  await setVars(30, r2.top, r2.bottom);
  await new Promise(r => setTimeout(r, 250));
  const B = await shoot('B_row2start');
  await b.close();

  // sample only row 1's exclusive core: below its top, above row 2's top
  const y0 = Math.max(0, r1.top + 2), y1 = Math.max(y0 + 4, r2.top - 2);
  const lumA = rowLum(A, y0, y1);
  const lumB = rowLum(B, y0, y1);
  console.log(`row-1 luminance  A(row1 sung)=${lumA.toFixed(1)}  B(band on row2)=${lumB.toFixed(1)}  drop=${(100*(1-lumB/lumA)).toFixed(1)}%`);
  // BLINDNESS GUARD: state A is "row 1 fully sung" — bright text on the dark
  // theme. If its luminance is near-black we photographed background, not
  // text (offscreen/unpainted card), and the comparison proves nothing.
  if (lumA < 25){
    console.log(`FAIL: state-A row-1 luminance ${lumA.toFixed(1)} is near-black — the card was not visibly painted (offscreen?); run is blind, not a PASS`);
    process.exit(1);
  }
  if (lumB < lumA * 0.97){
    console.log('FAIL: row 1 loses its reveal when the band moves to row 2 (mask layer order/composite broken)');
    process.exit(1);
  }
  console.log('PASS — row 1 stays fully lit while row 2 reveals');
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
