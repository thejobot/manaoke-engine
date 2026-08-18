// Round-11 live probes (iPhone touch emulation):
//  1. lifecycle: open word card → play "hear it in context" → flip → audio MUST pause
//  2. drill: tap Word by Word pill → network shows word_*.wav then word_*_gloss.mp3
//  3. earned explainer: a nulled line shows NO Explainer pill; a kept line shows it
//  4. links row: card back has BOTH jisho.org and youglish hrefs
// Usage: cd /tmp && node probe_round11.js <build-url> <keptLineIdx> <nulledLineIdx>
const { createRequire } = require('module');
const creq = createRequire(process.cwd() + '/');
const puppeteer = creq('puppeteer-core');
const URL = process.argv[2];
const KEPT_TXT = process.argv[3];
const NULLED_TXT = process.argv[4];
if (!URL || !KEPT_TXT || !NULLED_TXT){ console.error('need url keptText nulledText'); process.exit(2); }

(async () => {
  const b = await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless:'new'});
  const p = await b.newPage();
  const fails = [];
  const reqs = [];
  p.on('pageerror', e => fails.push('pageerror: ' + e.message));
  p.on('request', r => reqs.push(r.url()));
  await p.emulate({viewport:{width:390, height:844, deviceScaleFactor:2, isMobile:true, hasTouch:true},
    userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'});
  await p.goto(URL, {waitUntil:'networkidle2'});
  await new Promise(r => setTimeout(r, 3000));

  const tap = async sel_or_pt => {
    const pt = typeof sel_or_pt === 'string'
      ? await p.evaluate(s => { const el = document.querySelector(s); if (!el) return null;
          el.scrollIntoView({block:'center'}); const r = el.getBoundingClientRect();
          return {x:r.x+r.width/2, y:r.y+r.height/2}; }, sel_or_pt)
      : sel_or_pt;
    if (!pt) return false;
    await p.touchscreen.tap(pt.x, pt.y);
    return true;
  };

  // resolve line texts → page indices
  const [KEPT, NULLED] = await p.evaluate((a, b) =>
    [a, b].map(t => lines.findIndex(l => l.text && lineTrKey(l.text) === lineTrKey(t))), KEPT_TXT, NULLED_TXT);
  if (KEPT < 0 || NULLED < 0){ console.log('FAILS:\n  ✗ line text not found', KEPT, NULLED); process.exit(1); }
  // ── go to KEPT line, open study sheet ──
  await p.evaluate(i => { window.goToLine(i, {seek:false, play:false, manual:true}); }, KEPT);
  await new Promise(r => setTimeout(r, 500));
  await tap('#openCardsBtn');
  await new Promise(r => setTimeout(r, 900));

  // probe 3a: kept line HAS the Explainer pill
  const keptPill = await p.evaluate(() => {
    const el = document.querySelector('#cardSheet .card-section-overview');
    if (!el) return {exists:false};
    const cs = getComputedStyle(el);
    return {exists:true, has: el.classList.contains('has-overview'), visible: cs.display !== 'none'};
  });
  if (!keptPill.has) fails.push(`kept line ${KEPT_TXT}: Explainer pill missing (has-overview=false)`);

  // probe 2: Word by Word drill — tap the pill, watch network order
  reqs.length = 0;
  const pillTapped = await tap('#cardSheet .card-translation.has-translation');
  if (!pillTapped) fails.push('Word by Word pill not found on kept line');
  await new Promise(r => setTimeout(r, 4500));
  const audioReqs = reqs.filter(u => /audio\/(jp|en)\/word_/.test(u)).map(u => u.split('/').pop());
  // JP word clip is .mp3 in v095+ (gloss/en/ctx are also .mp3 — exclude them) or .wav legacy.
  const jpIdx = audioReqs.findIndex(u => u.endsWith('.wav') || (u.endsWith('.mp3') && !/_(gloss|en|ctx)\.mp3$/.test(u)));
  const glossIdx = audioReqs.findIndex(u => u.includes('_gloss.mp3'));
  if (jpIdx === -1 || glossIdx === -1 || glossIdx < jpIdx)
    fails.push(`drill order wrong: [${audioReqs.slice(0,6).join(', ')}]`);
  else console.log('drill sequence:', audioReqs.slice(0,6).join(' → '));
  // cancel drill (tap pill again)
  await tap('#cardSheet .card-translation.has-translation');
  await new Promise(r => setTimeout(r, 400));

  // ── probe 1 + 4: word card lifecycle + links ──
  const rowPt = await p.evaluate(() => {
    const row = document.querySelector('#cardSheet .word-row');
    if (!row) return null;
    row.scrollIntoView({block:'center'});
    const r = row.querySelector('.wc-jp').getBoundingClientRect();
    return {x:r.x+r.width/2, y:r.y+r.height/2};
  });
  if (!rowPt){ fails.push('no word row on kept line'); }
  else {
    await p.touchscreen.tap(rowPt.x, rowPt.y);
    await new Promise(r => setTimeout(r, 1200));
    const overlayUp = await p.evaluate(() => !!document.querySelector('.pitch-overlay'));
    if (!overlayUp) fails.push('word card overlay did not open');
    else {
      // probe 4: links on the back
      const links = await p.evaluate(() => [...document.querySelectorAll('.pitch-overlay .pb-links a')].map(a => a.href));
      if (!(links.some(h => h.includes('jisho.org')) && links.some(h => h.includes('youglish.com'))))
        fails.push('links row wrong: ' + JSON.stringify(links));
      else console.log('links row:', links.map(l => l.split('/')[2]).join(' + '));
      // flip to back
      await tap('.pitch-overlay .pitch-detail:not(.pitch-back) .pd-flip');
      await new Promise(r => setTimeout(r, 800));
      // play hear-it-in-context (if present on this card; else hear-definition on front)
      const ctxBtn = await p.evaluate(() => !!document.querySelector('.pitch-overlay .pb-ctx-listen'));
      if (ctxBtn){
        await tap('.pitch-overlay .pb-ctx-listen');
        await new Promise(r => setTimeout(r, 700));
        const playing = await p.evaluate(() => !!(window.__probeAudio = null) || !!(_expandedAudio && !_expandedAudio.paused));
        if (!playing) fails.push('ctx audio did not start');
        // FLIP back to front — audio must stop
        await tap('.pitch-overlay .pitch-back .pd-flip');
        await new Promise(r => setTimeout(r, 600));
        const stopped = await p.evaluate(() => !_expandedAudio || _expandedAudio.paused);
        if (!stopped) fails.push('LIFECYCLE: ctx audio kept playing after flip');
        else console.log('lifecycle: flip stopped ctx audio ✓');
      } else fails.push('no ctx listen control on first word card (cannot probe lifecycle)');
      // close overlay
      await p.evaluate(() => collapsePitchRow());
      await new Promise(r => setTimeout(r, 500));
    }
  }

  // ── probe 3b: nulled line has NO Explainer pill ──
  await p.evaluate(i => { window.goToLine(i, {seek:false, play:false, manual:true}); }, NULLED);
  await new Promise(r => setTimeout(r, 700));
  const nulledPill = await p.evaluate(() => {
    const el = document.querySelector('#cardSheet .card-section-overview');
    if (!el) return {exists:false, has:false};
    return {exists:true, has: el.classList.contains('has-overview')};
  });
  if (nulledPill.has) fails.push(`nulled line ${NULLED_TXT}: Explainer pill PRESENT (should be hidden)`);
  else console.log('earned explainer: nulled line shows no pill ✓');

  await b.close();
  if (fails.length){ console.log('FAILS:'); fails.forEach(f => console.log('  ✗ ' + f)); process.exit(1); }
  console.log('ALL ROUND-11 PROBES PASS');
  process.exit(0);
})();
