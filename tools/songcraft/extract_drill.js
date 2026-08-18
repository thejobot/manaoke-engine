// Extract the REAL runtime per-line Word-by-Word drill order from a song page,
// so the concat build (build_drill_concat.py) uses the exact same sequence the
// browser renders — no risk of a build/runtime desync misaligning the timing map.
//
// Loads index.html over file:// with request interception: data.json /
// tts_manifest.json are fulfilled from disk (file:// fetch is CORS-blocked
// otherwise), every external request (YouTube, fonts, R2 audio) is aborted so
// the page renders its cards fast and never hangs. The production
// collectStudyWords / drillParts path then runs unmodified.
//
// Usage:  node extract_drill.js <song_dir>            # JSON array -> stdout
// puppeteer-core resolves from CWD, /private/tmp, or /tmp (same convention as
// verify_reveal.js — run from a dir that has it, or rely on the fallbacks).
const { createRequire } = require('module');
const fs = require('fs'), path = require('path');
function loadPuppeteer(){
  // .node_tools/ is the durable install (macOS reaps /tmp files after ~3 days,
  // which half-deletes a /tmp npm install and breaks resolution mid-week).
  for (const base of [__dirname + '/.node_tools/', process.cwd() + '/',
                      '/private/tmp/node_modules/', '/tmp/node_modules/']){
    try { return createRequire(base)('puppeteer-core'); } catch(_){}
  }
  throw new Error('puppeteer-core not found (tools/songcraft/.node_tools, cwd, /private/tmp, /tmp)');
}
const puppeteer = loadPuppeteer();

const SONG_DIR = process.argv[2];
if (!SONG_DIR){ console.error('usage: node extract_drill.js <song_dir>'); process.exit(2); }
const idx = path.resolve(SONG_DIR, 'index.html');
if (!fs.existsSync(idx)){ console.error('no index.html in', SONG_DIR); process.exit(2); }
const CHROME = process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const localBytes = (name) => { const p = path.join(SONG_DIR, name); return fs.existsSync(p) ? fs.readFileSync(p) : null; };

(async () => {
  const b = await puppeteer.launch({ executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox','--disable-gpu','--allow-file-access-from-files'] });
  const p = await b.newPage();
  await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
  await p.setRequestInterception(true);
  p.on('request', (req) => {
    const u = req.url();
    if (u.startsWith('file://')){
      if (/data\.json(\?|$)/.test(u)){ const d = localBytes('data.json'); return d ? req.respond({status:200, contentType:'application/json', body:d}) : req.continue(); }
      if (/tts_manifest\.json(\?|$)/.test(u)){ const d = localBytes('tts_manifest.json'); return d ? req.respond({status:200, contentType:'application/json', body:d}) : req.continue(); }
      return req.continue();          // main doc + relative images
    }
    return req.abort();               // external: youtube, fonts, R2 audio
  });
  const errors = [];
  p.on('pageerror', e => errors.push(String(e)));
  await p.goto('file://' + idx, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await p.waitForFunction(() => document.querySelectorAll('.card-stack .card').length > 3, { timeout: 20000 }).catch(() => {});
  await new Promise(r => setTimeout(r, 1200));

  const out = await p.evaluate(() => {
    const lineTrKey = (s) => String(s||'').replace(/\s*\(×\d+\)\s*$/,'').replace(/\s+/g,'').trim();
    const rows = [];
    document.querySelectorAll('.card-stack .card').forEach((c) => {
      const back = c.querySelector('.card-face.back');
      if (!back) return;
      const dEl = back.querySelector('.card-translation[data-drill]');
      let drill = [];
      try { drill = JSON.parse(dEl ? dEl.dataset.drill : '[]'); } catch(_){}
      if (!Array.isArray(drill)) drill = [];
      const jpEl = c.querySelector('.brief-play--jp');
      const lineJp = jpEl ? (jpEl.dataset.speak || '') : '';
      const explain = back.dataset.explain || '';
      // Zero-word lines (pure-EN lyrics) still own a concat when they carry a
      // LINE_EXPLAIN entry: [line clip + explainer tail], words:[] lights
      // nothing. Skip only true no-content cards (intro/instrumental/no-study
      // with no explainer or no line).
      if (!drill.length && !(lineJp && explain)) return;
      rows.push({ idx: c.dataset.idx, lineKey: lineTrKey(lineJp), lineJp, explain, drill,
                  wordRows: back.querySelectorAll('.word-row').length });
    });
    return rows;
  });
  if (errors.length) console.error('page errors:', errors.length, '-', errors[0]);
  console.error(`extracted ${out.length} drill lines from ${path.basename(SONG_DIR)}`);
  await b.close();
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
