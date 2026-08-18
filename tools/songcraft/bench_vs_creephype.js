// bench_vs_creephype.js — measure a song page against the BENCHMARK page.
//
// the owner, 2026-07-31: "Our benchmark is creep hype, creep hype gets everything
// correct. You're not benchmarking against creep hype."
//
// parity_audit.py already proves a built page is a faithful clone of the
// TEMPLATE. That is not the same thing. The template is edited by hand and has
// drifted from the page that actually works — the promoted クリープハイプ page —
// without anyone ever measuring the two side by side. That is how the un-sung
// lyric colour ended up 42 brightness points from the sung colour (reveal
// invisible) while every gate stayed green: the drift was IN the reference.
//
// So this measures RENDERED state, both pages, same viewport, same forced
// playback position, and prints every difference. Static text diffing cannot
// see a wipe that does not read; computed colour and geometry can.
//
// Usage:
//   node bench_vs_creephype.js <slug|url> [--benchmark <slug|url>] [--width 390]
// Exit 0 = no drift outside the allowed per-song set, 1 = drift to look at.
const puppeteer = require('puppeteer-core');
const sleep = ms => new Promise(r => setTimeout(r, ms));

const argv = process.argv.slice(2);
const flag = (name, dflt) => { const i = argv.indexOf('--' + name); return i > -1 ? argv[i + 1] : dflt; };
const asUrl = s => s.startsWith('http') ? s : `https://manaoke.app/songs/${s}/`;
const CAND = asUrl(argv[0] || '');
// The benchmark is whatever the landing currently promotes for inochi — read it
// from the root SONGS[] rather than hardcoding a slug that will rot.
const BENCH = asUrl(flag('benchmark', require('fs')
  .readFileSync(path.join(__dirname, '..', '..', 'index.html'), 'utf8')
  .match(/url:\s*'\/songs\/(inochi-mijikashi-[^/']+)\//)[1]));
const WIDTH = +flag('width', 390);
if (!argv[0]) { console.error('usage: node bench_vs_creephype.js <slug|url> [--benchmark <slug|url>] [--width N]'); process.exit(2); }

// Per-song things that MUST differ. Everything else is drift until proven
// otherwise — the default is suspicion, which is the point of a benchmark.
const PER_SONG = /^(--field|--body-g|--cover|--card-accent|--art|--yt|--song)/;
// Two measurements legitimately differ per song and must not read as drift:
// how many rows a line takes (a function of how long the lyric is) and how many
// break opportunities it carries (a function of how many words are in it).
const BY_CONTENT = new Set(['layout.rows', 'reveal.breakOpportunities']);

const PROBE = () => {
  const pick = (el, props) => {
    if (!el) return null;
    const cs = getComputedStyle(el);
    const o = {};
    props.forEach(p => { o[p] = cs.getPropertyValue(p).trim(); });
    return o;
  };
  const TYPE = ['font-family', 'font-size', 'font-weight', 'line-height', 'letter-spacing',
                'color', 'white-space', 'word-break', 'overflow-wrap', 'text-align', 'opacity'];

  // force a line live so the reveal machinery is in its working state
  const cards = [...document.querySelectorAll('#cardStack .card')].filter(c => !c.classList.contains('intro'));
  const card = cards[0];
  document.querySelectorAll('.card.is-active').forEach(x => x.classList.remove('is-active', 'is-live'));
  card.classList.add('is-active', 'is-live');
  const text = card.querySelector('.card-text');
  const rom = card.querySelector('.card-romaji');
  const tr = card.querySelector('.card-translation');
  const wrap = text.closest('.card-text-wrap');

  const lum = c => {
    const m = (c || '').match(/(\d+),\s*(\d+),\s*(\d+)/);
    if (!m) return null;
    return +(0.2126 * m[1] + 0.7152 * m[2] + 0.0722 * m[3]).toFixed(1);
  };
  const tokColor = (layer, cls) => {
    const t = card.querySelector(`.lyric-layer--${layer} .tok${cls || ''}`);
    const g = t && (t.querySelector('.tok-char') || t);
    return g ? getComputedStyle(g).color : null;
  };

  const cs = getComputedStyle(text);
  const lh = parseFloat(cs.lineHeight);
  const groups = [...text.querySelectorAll('.lyric-layer--upcoming .wgrp, .lyric-layer--upcoming > .tok')];
  const wr = wrap.getBoundingClientRect();
  let overflow = 0;
  groups.forEach(g => { const r = g.getBoundingClientRect();
    overflow = Math.max(overflow, r.right - wr.right, wr.left - r.left); });

  const vars = {};
  const rootStyle = getComputedStyle(document.documentElement);
  for (const sheet of document.styleSheets) {
    let rules; try { rules = sheet.cssRules; } catch (e) { continue; }
    for (const rule of rules || []) {
      if (!rule.style || !/:root/.test(rule.selectorText || '')) continue;
      for (const p of rule.style) if (p.startsWith('--')) vars[p] = rootStyle.getPropertyValue(p).trim();
    }
  }

  const upcoming = tokColor('upcoming'), sung = tokColor('sung');
  return {
    vars,
    jp: pick(text, TYPE), romaji: pick(rom, TYPE), translation: pick(tr, TYPE),
    reveal: {
      upcoming, sung,
      // the number that decides whether a wipe is visible at all
      contrast: (lum(sung) != null && lum(upcoming) != null) ? +(lum(sung) - lum(upcoming)).toFixed(1) : null,
      romajiUpcoming: rom ? getComputedStyle(rom).color : null,
      breakOpportunities: text.querySelectorAll('.lyric-layer--upcoming wbr').length,
    },
    layout: {
      rows: Math.round(text.getBoundingClientRect().height / lh),
      overflowPx: +overflow.toFixed(1),
      wrapWidth: +wr.width.toFixed(1),
      neighbourOpacity: getComputedStyle(cards[1] || card).opacity,
    },
  };
};

// The study BOOK is a rendered state too — the EN-tile/header drift of
// 2026-07-31 shipped invisibly because only the sing view was measured.
// Open it the way eyes_check does (study mode -> tap a line -> open book)
// and measure the word-row structure, visible tiles, and header chrome.
const BOOK_PROBE = () => {
  const vis = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden' && +cs.opacity > 0.05; };
  const sheet = document.querySelector('#cardSheet');
  if (!sheet || !sheet.classList.contains('open')) return { open: false };
  const row = sheet.querySelector('.word-row');
  const rowCs = row ? getComputedStyle(row) : null;
  const jp = row ? row.querySelector('.wc-jp') : null;
  const main = jp ? jp.querySelector('.wc-main') : null;
  const sub = jp ? jp.querySelector('.wc-sub') : null;
  const pick = el => { if (!el) return null; const cs = getComputedStyle(el);
    return { font: cs.fontFamily, size: cs.fontSize, weight: cs.fontWeight, color: cs.color, align: cs.textAlign }; };
  const pill = sheet.querySelector('.card-translation');
  return {
    open: true,
    rowColumns: rowCs ? rowCs.gridTemplateColumns.split(' ').length : null,
    rowJpOnly: row ? row.classList.contains('word-row--jp-only') : null,
    visibleEnTiles: [...sheet.querySelectorAll('.wc-en')].filter(vis).length,
    visibleHeaderChrome: [...sheet.querySelectorAll('.card-meta, .card-section, .section-brief')].filter(vis).length,
    pillExtraChildren: pill ? [...pill.children].filter(c => vis(c) && !c.classList.contains('pill-label')).length : null,
    jpMain: pick(main), jpSub: pick(sub),
  };
};

const load = async (browser, url) => {
  const page = await browser.newPage();
  await page.setViewport({ width: WIDTH, height: 874, deviceScaleFactor: 2,
                           isMobile: WIDTH < 700, hasTouch: WIDTH < 700 });
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(4500);
  const out = await page.evaluate(PROBE);
  // open the book (same sequence as eyes_check) and measure it
  await page.tap('#uModeStudy').catch(() => {});
  await sleep(400);
  await page.evaluate(() => {
    const cards = [...document.querySelectorAll('.card')].filter(c => !c.classList.contains('intro'));
    if (cards[1]) cards[1].dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await sleep(800);
  await page.tap('#openCardsBtn').catch(() => {});
  await sleep(1200);
  out.book = await page.evaluate(BOOK_PROBE);
  await page.close();
  return out;
};

const walk = (a, b, path, hits) => {
  const keys = [...new Set([...Object.keys(a || {}), ...Object.keys(b || {})])].sort();
  for (const k of keys) {
    const va = a ? a[k] : undefined, vb = b ? b[k] : undefined;
    if (va && typeof va === 'object' || vb && typeof vb === 'object') { walk(va, vb, path + k + '.', hits); continue; }
    if (String(va) !== String(vb)) hits.push({ key: path + k, bench: va, cand: vb });
  }
  return hits;
};

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: 'new', args: ['--no-first-run', '--mute-audio'],
  });
  console.log(`benchmark : ${BENCH}`);
  console.log(`candidate : ${CAND}`);
  console.log(`width     : ${WIDTH}\n`);
  const [bench, cand] = [await load(browser, BENCH), await load(browser, CAND)];
  await browser.close();
  // vacuity guard: a book that failed to open on either side is a harness
  // failure, not a match — two closed books diff as identical.
  if (!bench.book || !bench.book.open || !cand.book || !cand.book.open) {
    console.error(`FATAL book state failed to open (benchmark ${bench.book && bench.book.open}, `
      + `candidate ${cand.book && cand.book.open}) — nothing was measured there.`);
    process.exit(2);
  }

  const all = walk(bench, cand, '', []).filter(d => !PER_SONG.test(d.key.replace(/^vars\./, '')));
  const drift = all.filter(d => !BY_CONTENT.has(d.key));
  for (const d of all.filter(d => BY_CONTENT.has(d.key)))
    console.log(`by content (not drift): ${d.key}  benchmark ${d.bench} / candidate ${d.cand}`);
  // the reveal contrast is the headline number: report it whether or not it drifted
  console.log(`reveal contrast (sung - un-sung luminance):  benchmark ${bench.reveal.contrast}   candidate ${cand.reveal.contrast}`);
  console.log(`line overflow past the column:               benchmark ${bench.layout.overflowPx}px   candidate ${cand.layout.overflowPx}px\n`);
  if (!drift.length) { console.log('NO DRIFT — the candidate measures like the benchmark.'); process.exit(0); }
  console.log(`DRIFT vs benchmark (${drift.length}):`);
  for (const d of drift) console.log(`  ${d.key}\n      benchmark: ${d.bench}\n      candidate: ${d.cand}`);
  process.exit(1);
})().catch(e => { console.error('FATAL', e.message); process.exit(2); });
