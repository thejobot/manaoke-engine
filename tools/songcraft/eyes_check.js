// eyes_check.js — the EYE SHEET (founder, tenth input 2026-07-12: "This team
// is working blind. Find skills to see and ensure it's always seen. That's
// minimum.")
//
// Captures the canonical states of a song page as screenshots so an agent
// can actually LOOK before anything is called done. This tool only captures;
// the looking is the reviewer's job: Read every image it prints, judge it
// against the product bar, and answer the standing question — "what
// dimension is nobody measuring?" A ship of visible behavior without this
// sheet being captured AND read is an unfinished ship.
//
// Canonical set (the states users live in, at the widths that matter):
//   load    @ 390, 566, 1100
//   playing @ 390            (taps the intro stage; on live the song really plays)
//   book    @ 390, 566       (study mode -> select lyric line -> open book)
//
// Usage:
//   node eyes_check.js <url-or-slug> [--local] [--out <dir>]
//     slug form builds https://manaoke.app/songs/<slug>/
//     --local intercepts the document from songs/<dir>/index.html (same as the gates)
//     default out dir: tools/songcraft/builds/eyes/<slug-or-host>/  (gitignored)
// Prints one absolute path per line; exit 0 when every capture landed.
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
// Repo root from this file's own location, so a clone works anywhere.
const ROOT = path.resolve(__dirname, '..', '..');

let arg = process.argv[2];
if (!arg) { console.error('usage: node eyes_check.js <url-or-slug> [--local] [--out <dir>]'); process.exit(2); }
let URL = arg.startsWith('http') ? arg : (process.env.MANAOKE_HOST || 'http://127.0.0.1:8773/preview') + `/${arg}/`;
// /songs/<slug>/ on the live site, /preview/<slug>/ on the Denmoku server —
// the preview form is how you look at a build that is not deployed yet, and
// its assets resolve the way production's do (unlike --local, which serves the
// document and lets data.json 404).
const slug = (URL.match(/\/(?:songs|preview)\/([^/]+)\//)
  || [null, URL.replace(/[^a-z0-9]+/gi, '_').slice(0, 40)])[1];
const outIdx = process.argv.indexOf('--out');
const OUT = outIdx > -1 ? process.argv[outIdx + 1] : `${ROOT}/tools/songcraft/builds/eyes/${slug}`;
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: 'new',
    args: ['--no-first-run', '--mute-audio', '--autoplay-policy=no-user-gesture-required'],
  });
  const page = await browser.newPage();
  if (process.argv.includes('--local')) {
    await page.setRequestInterception(true);
    page.on('request', req => {
      if (req.resourceType() === 'document') {
        const m = req.url().match(/\/songs\/([^/]+)\//);
        if (m) {
          let body = fs.readFileSync(path.join(ROOT, 'songs', m[1], 'index.html'), 'utf8');
          body = body.replace("<script>if(location.protocol==='http:'){location.replace('https:'+location.href.slice(5));}</script>", '');
          return req.respond({ contentType: 'text/html; charset=utf-8', body });
        }
      }
      req.continue();
    });
  }
  const shots = [];
  let failures = 0;
  const snap = async name => {
    const p = path.join(OUT, name + '.png');
    try { await page.screenshot({ path: p }); shots.push(p); console.log(p); }
    catch (e) { failures++; console.error('CAPTURE FAILED ' + name + ': ' + e.message); }
  };
  const load = async w => {
    // keep every image under 2000px on both sides (Read tool cap): 2x under
    // 700 CSS px, 1x above
    const dsf = w < 700 ? 2 : 1;
    // Changing isMobile makes puppeteer RELOAD the current page to re-emulate
    // it, and it waits on that reload with its own default 30s — which this
    // page never satisfies, so the call threw and killed the whole sheet
    // before the 1100 shot (and the playing/book shots after it) every time.
    // Park on about:blank first: there is nothing to reload, the emulation
    // change is instant, and the real load is the goto below.
    await page.goto('about:blank').catch(() => {});
    await page.setViewport({ width: w, height: 874, deviceScaleFactor: dsf, isMobile: w < 700, hasTouch: w < 700 })
      .catch(() => {});
    await page.goto(URL, { waitUntil: 'networkidle2', timeout: 60000 }).catch(() => {});
    await sleep(1500);
  };

  // Is this actually the song page? An undeployed slug on manaoke.app serves
  // the LANDING page instead of a 404, and the sheet happily photographed it
  // six times and reported "6/6 captured" (mariigoorudo, 2026-07-29). A sheet
  // of the wrong page is worse than no sheet: it looks like proof.
  const identify = () => page.evaluate(() => ({
    song: (document.documentElement.innerHTML.match(/const SONG\s*=\s*'([^']+)'/) || [])[1] || '',
    title: document.title,
    // the page's own "the data never arrived" state, which looks like a page
    // and photographs like proof
    broken: /Couldn.t load this song/.test((document.body && document.body.innerText) || ''),
  }));
  await load(390);
  let got = await identify();
  // Not deployed yet? The Denmoku box serves the built dir at /preview/<slug>/
  // with production's asset routing, so switch to it instead of failing and
  // sending the operator to --local (which can't fetch data.json — see below).
  if (got.song !== slug && !arg.startsWith('http') && !process.argv.includes('--local')) {
    let appUrl = '';
    try {
      appUrl = fs.readFileSync(path.join(ROOT, 'tools/songcraft/builder/.app-url'),
                               'utf8').trim();
    } catch (e) { /* Denmoku has never run here */ }
    if (appUrl) {
      URL = appUrl.replace(/\/+$/, '') + `/preview/${slug}/`;
      console.error(`not on manaoke.app yet — looking at the box instead: ${URL}`);
      await load(390);
      got = await identify();
    }
  }
  if (got.song !== slug) {
    console.error(`WRONG PAGE: ${URL} served ${got.song ? `the song page for '${got.song}'`
      : `something that is not a song page (title: ${JSON.stringify(got.title)})`}, `
      + `not '${slug}'.\n  If this build is not deployed yet, start Denmoku and use its `
      + `preview: node eyes_check.js http://127.0.0.1:8773/preview/${slug}/`);
    await browser.close();
    process.exit(2);
  }
  // A page whose data.json never arrived still renders chrome, so the sheet
  // used to capture six photos of "Couldn't load this song — please refresh."
  // and report 6/6 (mariigoorudo, 2026-07-30, --local). That is the same class
  // of lie as the wrong-page sheet: fail instead.
  if (got.broken) {
    console.error(`BROKEN PAGE: ${URL} loaded the chrome but not the song — data.json `
      + `did not arrive, so every shot would say "Couldn't load this song".`
      + (process.argv.includes('--local')
        ? `\n  --local serves the document off disk and file:// cannot fetch data.json. `
          + `Use the box instead: node eyes_check.js http://127.0.0.1:8773/preview/${slug}/`
        : `\n  Check that the build has a data.json and that its assets resolve.`));
    await browser.close();
    process.exit(2);
  }

  for (const w of [390, 566, 1100]) { await load(w); await snap(`load_${w}`); }

  // playing @390: tap the intro stage; on the live site the song really plays
  await load(390);
  await page.evaluate(() => {
    const intro = Array.from(document.querySelectorAll('.card')).find(c => c.classList.contains('intro'));
    if (intro) intro.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await sleep(14000);
  const playing = await page.evaluate(() => document.querySelector('.u-app')?.hasAttribute('data-playing'));
  await snap('playing_390' + (playing ? '' : '_NOTPLAYING'));

  // book @390 and @566: the word-lock opening sequence (study -> line -> book)
  for (const w of [390, 566]) {
    await load(w);
    await page.tap('#uModeStudy').catch(() => {}); await sleep(400);
    await page.evaluate(() => {
      const cards = Array.from(document.querySelectorAll('.card')).filter(c => !c.classList.contains('intro'));
      if (cards[1]) cards[1].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await sleep(800);
    await page.tap('#openCardsBtn').catch(() => {}); await sleep(1000);
    const open = await page.evaluate(() => document.querySelector('#cardSheet')?.classList.contains('open'));
    await snap(`book_${w}` + (open ? '' : '_NOTOPEN'));
  }

  await browser.close();
  console.log(`EYE SHEET: ${shots.length}/6 captured -> ${OUT}`);
  console.log('Now READ every image above and write down what you saw. Unread screenshots are still blindness.');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error('FATAL', e.message); process.exit(2); });
