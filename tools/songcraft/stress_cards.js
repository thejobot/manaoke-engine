// Card stress harness — drives EVERY word card on a live song build like a
// human (iPhone touch emulation) and reports defects instead of vibes:
//   per card: overlay opens; front-face void ratio (empty purple — the nanbo
//   bug class); scene image frame resolves to .ready or removes itself (no
//   broken half-state); jisho link present with the right href; front JP
//   audio URL serves 200; card collapses cleanly.
//   per song: lightbox click-through probe — open an image, tap where the
//   share button sits underneath, assert the button did NOT fire and the
//   lightbox closed (the trailing-click bug class).
// Usage: cd /tmp && node stress_cards.js <build-url> [maxLines]
// Exit 0 = no defects, 1 = defects listed.
const { createRequire } = require('module');
const creq = createRequire(process.cwd() + '/');
const puppeteer = creq('puppeteer-core');
const URL = process.argv[2];
const MAXLINES = parseInt(process.argv[3] || '999', 10);
if (!URL){ console.error('need url'); process.exit(2); }

(async () => {
  const b = await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless:'new'});
  const p = await b.newPage();
  const pageErrors = [];
  p.on('pageerror', e => pageErrors.push(e.message));
  await p.emulate({viewport:{width:390, height:844, deviceScaleFactor:2, isMobile:true, hasTouch:true},
    userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'});
  await p.goto(URL, {waitUntil:'networkidle2'});
  await new Promise(r => setTimeout(r, 3000));

  const nLines = await p.evaluate(() => lines.length);
  const defects = [];
  const seen = new Set();
  let cards = 0;
  let lightboxTested = false;

  for (let li = 1; li < Math.min(nLines, MAXLINES); li++){
    const hasWords = await p.evaluate(i => !!(lines[i] && lines[i].studyWords && lines[i].studyWords.length), li);
    if (!hasWords) continue;
    await p.evaluate(i => { window.goToLine(i, {seek:false, play:false, manual:true}); }, li);
    await new Promise(r => setTimeout(r, 500));
    // open study sheet via the book button
    const btn = await p.evaluate(() => {
      const b = document.getElementById('openCardsBtn').getBoundingClientRect();
      return {x: b.x + b.width/2, y: b.y + b.height/2};
    });
    await p.touchscreen.tap(btn.x, btn.y);
    await new Promise(r => setTimeout(r, 900));
    const rowKeys = await p.evaluate(() => [...document.querySelectorAll('#cardSheet .word-row')]
      .map(r => r.dataset.pitchKey + '|' + (r.dataset.uid || r.dataset.rom)));
    for (const key of rowKeys){
      if (seen.has(key)) continue;
      seen.add(key);
      // tap the row
      const loc = await p.evaluate(k => {
        const row = [...document.querySelectorAll('#cardSheet .word-row')]
          .find(r => (r.dataset.pitchKey + '|' + (r.dataset.uid || r.dataset.rom)) === k);
        if (!row) return null;
        row.scrollIntoView({block:'center'});
        const b = row.getBoundingClientRect();
        return {x: b.x + b.width/2, y: b.y + b.height/2};
      }, key);
      if (!loc){ defects.push(`${key}: row vanished`); continue; }
      await new Promise(r => setTimeout(r, 250));
      const loc2 = await p.evaluate(k => {
        const row = [...document.querySelectorAll('#cardSheet .word-row')]
          .find(r => (r.dataset.pitchKey + '|' + (r.dataset.uid || r.dataset.rom)) === k);
        const b = row.getBoundingClientRect();
        return {x: b.x + b.width/2, y: b.y + b.height/2};
      }, key);
      await p.touchscreen.tap(loc2.x, loc2.y);
      await new Promise(r => setTimeout(r, 1300));
      const res = await p.evaluate(async k => {
        const ov = document.querySelector('.pitch-overlay');
        if (!ov) return {fail: 'no overlay'};
        const front = ov.querySelector('.pitch-detail:not(.pitch-back)');
        const back = ov.querySelector('.pitch-back');
        const out = {};
        if (front){
          const fr = front.getBoundingClientRect();
          const kids = [...front.children].filter(c => !c.classList.contains('pd-flip'));
          let top = Infinity, bot = -Infinity;
          kids.forEach(c => { const r = c.getBoundingClientRect(); if (r.height){ top = Math.min(top, r.top); bot = Math.max(bot, r.bottom); } });
          out.voidRatio = fr.height ? +(1 - (bot - top) / fr.height).toFixed(2) : 0;
          out.faceH = Math.round(fr.height);
        }
        if (back){
          const frame = back.querySelector('.pb-img-frame');
          out.img = frame ? (frame.classList.contains('ready') ? 'ready' : 'PENDING') : 'none';
          const j = back.querySelector('.pb-jisho');
          out.jisho = j ? (j.href.includes('jisho.org/search/') ? 'ok' : 'BAD:' + j.href) : 'MISSING';
        }
        // front JP audio reachable?
        const row = [...document.querySelectorAll('#cardSheet .word-row')]
          .find(r => (r.dataset.pitchKey + '|' + (r.dataset.uid || r.dataset.rom)) === k);
        if (row){
          const uid = row.dataset.uid || row.dataset.rom;
          const base = `audio/jp/word_${row.dataset.sectionId}_${uid.replace(/ /g,'-').replace(/·/g,'').replace(/\//g,'_').replace(/^-+|-+$/g,'')}`;
          // v095+ serves the JP word clip as mono mp3; legacy builds (silhouette)
          // still serve .wav. Probe mp3 first, fall back to wav.
          try {
            let r2 = await fetch(base + '.mp3', {method:'HEAD'});
            if (r2.status !== 200) r2 = await fetch(base + '.wav', {method:'HEAD'});
            out.audio = r2.status;
          } catch(_){ out.audio = 'ERR'; }
        }
        return out;
      }, key);
      cards++;
      if (res.fail) defects.push(`${key}: ${res.fail}`);
      else {
        if (res.voidRatio > 0.45) defects.push(`${key}: front face ${Math.round(res.voidRatio*100)}% empty (h=${res.faceH})`);
        if (res.img === 'PENDING'){
          // slow 404-through-redirect can leave the frame pending briefly —
          // only a defect if it persists
          await new Promise(r => setTimeout(r, 1800));
          const again = await p.evaluate(() => {
            const f = document.querySelector('.pitch-overlay .pb-img-frame');
            return f ? (f.classList.contains('ready') ? 'ready' : 'PENDING') : 'none';
          });
          if (again === 'PENDING') defects.push(`${key}: image frame stuck half-loaded`);
        }
        if (res.jisho && res.jisho !== 'ok') defects.push(`${key}: jisho link ${res.jisho}`);
        if (res.audio !== 200) defects.push(`${key}: front audio HTTP ${res.audio}`);
      }
      // lightbox click-through probe, once, on the first card with an image
      if (!lightboxTested && res.img === 'ready'){
        lightboxTested = true;
        // the image frame lives on the BACK face — flip first, else the tap
        // lands on whatever FRONT element shares those coordinates (this
        // harness once "found" a click-through by tapping the share button
        // itself through a rotated-away frame rect)
        const flipLoc = await p.evaluate(() => {
          const f = document.querySelector('.pitch-overlay .pitch-detail:not(.pitch-back) .pd-flip');
          if (!f) return null;
          const b = f.getBoundingClientRect();
          return {x: b.x + b.width/2, y: b.y + b.height/2};
        });
        if (flipLoc){ await p.touchscreen.tap(flipLoc.x, flipLoc.y); await new Promise(r => setTimeout(r, 900)); }
        const probe = await p.evaluate(() => {
          const frame = document.querySelector('.pitch-overlay .pb-img-frame.ready');
          if (frame){
            const fr = frame.getBoundingClientRect();
            const hit = document.elementFromPoint(fr.x + fr.width/2, fr.y + fr.height/2);
            if (!hit || !frame.contains(hit)) return null;   // frame not actually tappable
          }
          const share = document.querySelector('.pitch-overlay .card-actions [data-action="share"], .pitch-overlay .card-btn[data-action="share"]');
          if (!frame) return null;
          window.__shareFired = 0;
          if (share) share.addEventListener('click', () => { window.__shareFired++; }, true);
          const f = frame.getBoundingClientRect();
          const s = share ? share.getBoundingClientRect() : null;
          return {fx: f.x + f.width/2, fy: f.y + f.height/2, sx: s && s.x + s.width/2, sy: s && s.y + s.height/2};
        });
        if (probe){
          await p.touchscreen.tap(probe.fx, probe.fy);          // open lightbox
          await new Promise(r => setTimeout(r, 700));
          const tapAt = probe.sx ? probe : {sx: 195, sy: 780};  // share spot or toolbar lane
          await p.touchscreen.tap(tapAt.sx, tapAt.sy);          // tap where button hides
          await new Promise(r => setTimeout(r, 900));
          const lbRes = await p.evaluate(() => ({
            lbGone: !document.querySelector('.scene-lightbox'),
            shareFired: window.__shareFired || 0,
          }));
          if (!lbRes.lbGone) defects.push('lightbox: did not close on tap');
          if (lbRes.shareFired) defects.push(`lightbox: CLICK-THROUGH — share fired ${lbRes.shareFired}x`);
        }
      }
      await p.evaluate(() => { try { collapsePitchRow(); } catch(_){} });
      await new Promise(r => setTimeout(r, 450));
    }
    await p.evaluate(() => { try { closeCards(); } catch(_){} });
    await new Promise(r => setTimeout(r, 500));
  }

  // lyric typography: any wrapped line with a 1-char orphan row?
  const orphans = await p.evaluate(() => {
    const bad = [];
    document.querySelectorAll('.card .card-text').forEach(t => {
      const toks = t.querySelectorAll('.lyric-layer--upcoming .tok');
      if (!toks.length) return;
      const rows = new Map();
      toks.forEach(tk => {
        const top = Math.round(tk.getBoundingClientRect().top / 6);
        rows.set(top, (rows.get(top) || 0) + tk.textContent.length);
      });
      if (rows.size > 1){
        const counts = [...rows.values()];
        if (counts[counts.length - 1] === 1) bad.push(t.textContent.slice(0, 18));
      }
    });
    return bad;
  });
  orphans.forEach(o => defects.push(`lyric orphan row (single char): ${o}`));

  await b.close();
  console.log(`stress: ${cards} cards driven, ${defects.length} defects, ${pageErrors.length} page errors`);
  defects.slice(0, 30).forEach(d => console.log('  ✗', d));
  pageErrors.slice(0, 5).forEach(e => console.log('  ⚠ pageerror:', e));
  process.exit(defects.length || pageErrors.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
