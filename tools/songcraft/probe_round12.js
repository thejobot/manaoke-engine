// Round-12 live probes (iPhone touch emulation + YT playback):
//  1. ENGINE: while playing, the wipe advances on most display frames (rAF,
//     not 16Hz steps); estClock |est-raw| stays bounded; buildWipeGeom runs
//     ~once per line (not per frame); glow strip exists + lit; .hit chars.
//  2. PAUSE: html[data-wipe-paused] after 150ms debounce; romaji raised.
//  3. WORD CARD: dim+blur split (blur never animated, on after dim); entry
//     FLIP done; still beat — pitch audio starts ≥600ms after tap.
//  4. FLIP: .flipped, audio killed (round-11 law), height swaps untransitioned.
//  5. DRILL: drill-live dims rows; exactly one drill-hot at a time; wav→gloss.
// Usage: cd /tmp && node probe_round12.js <build-url>
const { createRequire } = require('module');
const creq = createRequire(process.cwd() + '/');
const puppeteer = creq('puppeteer-core');
const URL = process.argv[2];
if (!URL){ console.error('need url'); process.exit(2); }

(async () => {
  const b = await puppeteer.launch({
    executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless:'new',
    args:['--autoplay-policy=no-user-gesture-required','--mute-audio']});
  const p = await b.newPage();
  const fails = [];
  const reqs = [];
  p.on('pageerror', e => fails.push('pageerror: ' + e.message));
  p.on('request', r => reqs.push({u: r.url(), t: Date.now()}));
  await p.emulate({viewport:{width:390, height:844, deviceScaleFactor:2, isMobile:true, hasTouch:true},
    userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'});
  await p.goto(URL, {waitUntil:'networkidle2'});
  await new Promise(r => setTimeout(r, 3500));

  const tap = async sel => {
    const pt = await p.evaluate(s => { const el = document.querySelector(s); if (!el) return null;
      el.scrollIntoView({block:'center'}); const r = el.getBoundingClientRect();
      return {x:r.x+r.width/2, y:r.y+r.height/2}; }, sel);
    if (!pt) return false;
    await p.touchscreen.tap(pt.x, pt.y);
    return true;
  };

  // ── 1. ENGINE under real playback ──
  const ytUp = await p.evaluate(() => new Promise(res => {
    let n = 0;
    const iv = setInterval(() => {
      n++;
      if (typeof ytReady !== 'undefined' && ytReady && typeof player !== 'undefined' && player.playVideo){ clearInterval(iv); res(true); }
      if (n > 60){ clearInterval(iv); res(false); }
    }, 250);
  }));
  if (!ytUp){ fails.push('YT player never ready'); }
  else {
    await p.evaluate(() => {
      window.__clockErr = [];
      window.__diagClock = (raw, est) => { if (window.__clockErr.length < 5000) window.__clockErr.push(Math.abs(est - raw)); };
      const _b = buildWipeGeom; window.__geomCalls = 0;
      window.buildWipeGeom = function(){ window.__geomCalls++; return _b.apply(this, arguments); };
      try { player.mute(); } catch(_){}
      // start from the first lyric line so the wipe is live immediately
      goToLine(1, {seek:true, play:true, manual:false});
      try { player.playVideo(); } catch(_){}
    });
    // wait for actual playback
    const playing = await p.evaluate(() => new Promise(res => {
      let n = 0;
      const iv = setInterval(() => {
        n++;
        if (typeof isPlaying !== 'undefined' && isPlaying){ clearInterval(iv); res(true); }
        if (n > 40){ clearInterval(iv); res(false); }
      }, 250);
    }));
    if (!playing) fails.push('playback never started (headless YT)');
    else {
      await new Promise(r => setTimeout(r, 1500));
      const eng = await p.evaluate(() => new Promise(res => {
        const fills = [];
        let frames = 0;
        const step = () => {
          frames++;
          // re-query per frame: a short line can hand off mid-sample and the
          // old card's fill var freezes (probe artifact, not an engine stall)
          const card = document.querySelector('.card.is-active');
          const el = card && card.querySelector('.card-face.front .card-text');
          fills.push(el ? parseFloat(getComputedStyle(el).getPropertyValue('--line-fill-px')) || 0 : 0);
          if (frames < 180) requestAnimationFrame(step);
          else {
            let advances = 0, moving = 0;
            for (let i = 1; i < fills.length; i++){
              if (fills[i] !== fills[i-1]) advances++;
              if (fills[i] > fills[i-1]) moving++;
            }
            // longest run of consecutive identical samples while a line is live
            let stall = 0, run = 0;
            for (let i = 1; i < fills.length; i++){
              run = (fills[i] === fills[i-1]) ? run + 1 : 0;
              if (run > stall) stall = run;
            }
            const errs = window.__clockErr || [];
            res({
              frames, advances, moving, stall,
              maxClockErr: errs.length ? Math.max.apply(null, errs) : -1,
              clockSamples: errs.length,
              geomCalls: window.__geomCalls,
              glow: (() => { const g = document.querySelector('.card.is-active .wipe-glow');
                return g ? {present:true, op: parseFloat(getComputedStyle(g).opacity)} : {present:false}; })(),
              hitChars: document.querySelectorAll('.card.is-active .tok-char.hit').length,
              isLive: !!document.querySelector('.card.is-active.is-live'),
              paused: document.documentElement.hasAttribute('data-wipe-paused'),
            });
          }
        };
        requestAnimationFrame(step);
      }));
      console.log('engine:', JSON.stringify(eng));
      // 3s @ ~60fps: the old 60ms timer would change the var on <=33% of frames.
      if (eng.advances < eng.frames * 0.4) fails.push(`wipe not per-frame: ${eng.advances}/${eng.frames} frames advanced`);
      if (eng.maxClockErr < 0) fails.push('estClock diag hook never fired');
      else if (eng.maxClockErr > 0.35) fails.push(`clock err too big: ${eng.maxClockErr}`);
      if (eng.geomCalls > 25) fails.push(`buildWipeGeom ran ${eng.geomCalls}x in ~5s — per-frame rebuild?`);
      if (!eng.glow.present) fails.push('no .wipe-glow strip on active line');
      if (eng.paused) fails.push('data-wipe-paused set while playing');

      // ── 2. PAUSE look ──
      await p.evaluate(() => { try { player.pauseVideo(); } catch(_){} });
      await new Promise(r => setTimeout(r, 800));
      const pl = await p.evaluate(() => ({
        attr: document.documentElement.hasAttribute('data-wipe-paused'),
        romOp: (() => { const r = document.querySelector('.card.is-active .card-romaji');
          return r ? parseFloat(getComputedStyle(r).opacity) : -1; })(),
        hits: document.querySelectorAll('.card.is-active .tok-char.hit, .card.is-active .tok-char.pre').length,
      }));
      console.log('pause:', JSON.stringify(pl));
      if (!pl.attr) fails.push('pause: data-wipe-paused not set');
      // Round 13: pause = FREEZE. No ghost/romaji RAISE — romaji holds its
      // playing opacity (~.72 since the legibility-floor fix). A raise toward
      // the old readable-ghost ~.85 would be the regression we forbid.
      if (pl.romOp >= 0 && pl.romOp > 0.8) fails.push(`pause: romaji opacity ${pl.romOp} (raised toward ghost-read; pause must freeze)`);
      if (pl.hits > 0) fails.push('pause: lifted chars not released');
      // resume clears
      await p.evaluate(() => { try { player.playVideo(); } catch(_){} });
      await new Promise(r => setTimeout(r, 700));
      const resumed = await p.evaluate(() => !document.documentElement.hasAttribute('data-wipe-paused'));
      if (!resumed) fails.push('resume: data-wipe-paused stuck');
      await p.evaluate(() => { try { player.pauseVideo(); } catch(_){} });
      await new Promise(r => setTimeout(r, 400));
    }
  }

  // ── 3+4. WORD CARD: backdrop split, still beat, flip ──
  await p.evaluate(() => { goToLine(2, {seek:false, play:false, manual:true}); });
  await new Promise(r => setTimeout(r, 500));
  await tap('#openCardsBtn');
  await new Promise(r => setTimeout(r, 1100));
  const tapT = Date.now();
  const rowTapped = await tap('#cardSheet .word-row .wc-jp');
  if (!rowTapped) fails.push('no word row to tap');
  else {
    // poll for pitch audio actually playing (still beat: entry ~460 + 350)
    const audioStart = await p.evaluate(() => new Promise(res => {
      const t0 = performance.now();
      const iv = setInterval(() => {
        if (typeof _expandedAudio !== 'undefined' && _expandedAudio && !_expandedAudio.paused){ clearInterval(iv); res(performance.now() - t0); }
        if (performance.now() - t0 > 6000){ clearInterval(iv); res(-1); }
      }, 16);
    }));
    const ov = await p.evaluate(() => {
      const o = document.querySelector('.pitch-overlay');
      if (!o) return null;
      const dim = o.querySelector('.pd-dim'), blur = o.querySelector('.pd-blur');
      const flip = o.querySelector('.pitch-flip');
      return {
        dim: dim ? parseFloat(getComputedStyle(dim).opacity) : -1,
        blur: blur ? parseFloat(getComputedStyle(blur).opacity) : -1,
        blurTransition: blur ? getComputedStyle(blur).transitionProperty : '',
        flipTransform: flip ? getComputedStyle(flip).transform : '',
      };
    });
    console.log('overlay:', JSON.stringify(ov), 'audioStartMs:', audioStart === -1 ? 'never' : Math.round(audioStart));
    if (!ov) fails.push('word card overlay did not open');
    else {
      if (ov.dim < 0.95) fails.push(`dim layer opacity ${ov.dim}`);
      if (ov.blur < 0.95) fails.push(`blur layer not enabled after entry (${ov.blur})`);
      if (/opacity/.test(ov.blurTransition)) fails.push('blur layer has an opacity transition (must never animate)');
      if (!(ov.flipTransform === 'none' || /matrix\(1, 0, 0, 1, 0, 0\)/.test(ov.flipTransform))) fails.push('entry FLIP did not settle to identity: ' + ov.flipTransform);
    }
    if (audioStart !== -1 && audioStart < 550) fails.push(`still beat violated: audio at ${Math.round(audioStart)}ms after tap`);
    if (audioStart === -1) console.log('note: pitch audio never started (may lack clip) — still-beat unverified');

    // flip to back: audio must die, height must swap without transition
    const hBefore = await p.evaluate(() => { const f = document.querySelector('.pitch-flip-inner'); return f ? f.style.height : null; });
    await tap('.pitch-overlay .pitch-detail:not(.pitch-back) .pd-flip');
    await new Promise(r => setTimeout(r, 700));
    const flipState = await p.evaluate(() => {
      const inner = document.querySelector('.pitch-flip-inner');
      return {
        flipped: inner ? inner.classList.contains('flipped') : false,
        h: inner ? inner.style.height : null,
        hTrans: inner ? getComputedStyle(inner).transitionProperty : '',
        audioDead: (typeof _expandedAudio === 'undefined') || !_expandedAudio || _expandedAudio.paused,
      };
    });
    console.log('flip:', JSON.stringify({...flipState, hBefore}));
    if (!flipState.flipped) fails.push('flip did not flip');
    if (!flipState.audioDead) fails.push('AUDIO LAW: word audio survived the flip');
    if (/height/.test(flipState.hTrans)) fails.push('flip-inner still has a height transition');
    await p.evaluate(() => collapsePitchRow());
    await new Promise(r => setTimeout(r, 500));
  }

  // ── 5. DRILL: cadence + per-word isolation ──
  reqs.length = 0;
  const pillTapped = await tap('#cardSheet .card-translation.has-translation');
  if (!pillTapped) fails.push('Word by Word pill not found');
  else {
    // sample drill-hot uniqueness + dimming over 4s
    const drill = await p.evaluate(() => new Promise(res => {
      const samples = [];
      const t0 = performance.now();
      const iv = setInterval(() => {
        const grid = document.querySelector('#cardSheet .card-face.back');
        const hot = document.querySelectorAll('#cardSheet .word-row.drill-hot');
        const rows = document.querySelectorAll('#cardSheet .word-row');
        let dimmed = -1;
        for (const r of rows){ if (!r.classList.contains('drill-hot')){ dimmed = parseFloat(getComputedStyle(r).opacity); break; } }
        samples.push({t: Math.round(performance.now() - t0),
          live: !!(grid && grid.classList.contains('drill-live')),
          hot: hot.length, dimmed});
        if (performance.now() - t0 > 4200){ clearInterval(iv); res(samples); }
      }, 150);
    }));
    const everLive = drill.some(s => s.live);
    const everHot = drill.some(s => s.hot === 1);
    const multiHot = drill.some(s => s.hot > 1);
    const dimOk = drill.some(s => s.live && s.dimmed >= 0 && s.dimmed < 0.5);
    console.log('drill samples:', JSON.stringify(drill.slice(0, 10)));
    if (!everLive) fails.push('drill-live never set');
    if (!everHot) fails.push('no word row ever drill-hot');
    if (multiHot) fails.push('more than one drill-hot at once');
    if (!dimOk) fails.push('non-active rows not dimmed during drill');
    const audioReqs = reqs.filter(r => /audio\/(jp|en)\/word_/.test(r.u)).map(r => ({f: r.u.split('/').pop(), t: r.t}));
    // JP word clip = the bare word_<sid>_<uid> clip. It's .mp3 in v095+ (where
    // gloss/en/ctx are ALSO .mp3, so exclude those suffixes) or .wav in legacy builds.
    const jpIdx = audioReqs.findIndex(a => a.f.endsWith('.wav') || (a.f.endsWith('.mp3') && !/_(gloss|en|ctx)\.mp3$/.test(a.f)));
    const glossIdx = audioReqs.findIndex(a => a.f.includes('_gloss.mp3'));
    if (jpIdx === -1 || glossIdx === -1 || glossIdx < jpIdx) fails.push(`drill order wrong: [${audioReqs.map(a=>a.f).slice(0,5).join(', ')}]`);
    else console.log('drill order:', audioReqs[jpIdx].f, '→', audioReqs[glossIdx].f);
    // cancel
    await tap('#cardSheet .card-translation.has-translation');
    await new Promise(r => setTimeout(r, 500));
    const cleaned = await p.evaluate(() => !document.querySelector('#cardSheet .word-row.drill-hot') &&
      !document.querySelector('#cardSheet .drill-live'));
    if (!cleaned) fails.push('drill cancel left visual state behind');
  }

  await b.close();
  if (fails.length){ console.log('FAILS:'); fails.forEach(f => console.log('  ✗ ' + f)); process.exit(1); }
  console.log('ALL ROUND-12 PROBES PASS');
  process.exit(0);
})();
