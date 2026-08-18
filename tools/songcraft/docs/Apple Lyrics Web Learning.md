# Apple Music Lyrics on the Web — Reverse-Engineering Notes

Last updated 2026-06-01. Two live observations dismantled via Chrome DevTools Protocol on a clean isolated profile:

| Song | Artist | Duration | Lines | Method | Genre coverage |
|---|---|---|---|---|---|
| Inochi Mijikashi Koiseyo Otome | CreepHyp | 3:11 | 30 | port 9222, 79 screenshots, 498 ticks | JP ballad — small syllable chunks, slow timing |
| AKARUIHEYA | LEX & LANA | 2:54 | 50 | port 9223, 117 screenshots, 555 ticks | JP/EN hip-hop duet — large chunks, fast timing, two voices |

Together these cover ballad and rap timing, single-voice and duet, pure-JP and mixed JP/EN, with and without translations. Every claim in this document is from observing the actual rendered page state, not from documentation or third-party reimplementations.

This is what Apple's own production renderer does, not what YouLy+ or AMLL or Cider do. Where their code differs, I've called it out at the bottom.


## TL;DR

Apple Music's web lyric renderer is JavaScript-driven on every frame. There are no CSS keyframes for the wipe (one exception: `@keyframes gap-loop` for the instrumental-break loading dots). JS sets inline `style` on every `.letter` element each `requestAnimationFrame` tick with four coupled properties (`transform`, `--gradient-progress`, `--text-shadow-blur-radius`, `--text-shadow-opacity`). A single CSS rule converts those four values into the visible reveal via `background-clip: text` plus a two-stop `linear-gradient`. The text itself is rendered transparent and painted by the gradient. The per-letter punch animation (scale 1.0 → 1.047 → 1.0, dy 0 → -2px → stays at -2px) is what makes it feel "alive."

For Japanese, Apple uses native HTML5 `<ruby>` + `<rt>` to position romaji below kanji. Each kanji syllable and its romaji twin share the same `data-duration` and `data-delay`, so they animate in lock-step without any JS synchronization code.

For duets, the second vocalist's lines flip to `is-secondary-vocalist` (right-aligned, transform-origin right). Both voices cap to 60% container width when `is-duet`.

For mixed-script lyrics (e.g. "I'll say goodbye to 暗い闇"), Apple keeps English verbatim in the romaji row and only transliterates the Japanese portions. Pure-English lines suppress both romaji and translation rows entirely.

The "syllable" in Apple's TTML is NOT always a single character. It's a phrase-boundary chunk. A line like "落ちて俺はなんもなくなった いまはこれだけ" (24 chars) can be just 2 spans. The per-letter animation makes long chunks readable as the wipe travels across.


## Data layer

### Endpoint

```
GET https://amp-api.music.apple.com/v1/catalog/{storefront}/songs/{songId}/syllable-lyrics
  ?l[lyrics]={locale}
  &l[script]={baseLang}-{scriptCode}
  &extend=ttmlLocalizations
```

The endpoint is literally named `syllable-lyrics`. Apple internally treats word-by-word as "syllable" granularity, because for Japanese and Chinese the meaningful unit is the mora/syllable, not the space-separated word.

### Authentication

Two headers, both pulled directly off `window.MusicKit.getInstance()`:

```
Authorization: Bearer <developerToken>   // mk.developerToken or mk.configuration.app.developerToken
Music-User-Token: <userToken>            // mk.musicUserToken (signed-in subscriber's token)
```

Without `Music-User-Token` the endpoint returns 401 even with a valid developer token.

### Payload shape

The response wraps a TTML XML document. For our test song:

```xml
<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    itunes:timing="Word"
    xml:lang="ja-JP">
  <head><metadata>
    <ttm:agent type="person" xml:id="voice1">
      <ttm:name type="full">クリープハイプ</ttm:name>
    </ttm:agent>
    <iTunesMetadata leadingSilence="0.320">
      <translations/>
      <songwriters><songwriter>尾崎世界観</songwriter></songwriters>
    </iTunesMetadata>
  </metadata></head>
  <body dur="3:11.000">
    <div begin="11.990" end="33.657">
      <p begin="11.990" end="16.478" itunes:key="L1" ttm:agent="voice1">
        <span begin="11.990" end="13.123">なんぼ</span>
        <span begin="13.123" end="13.665">汚</span>
        <span begin="13.665" end="15.666">れたアタシで</span>
        <span begin="15.666" end="16.478">も</span>
      </p>
      ...
    </div>
  </body>
</tt>
```

Key conventions:

- `itunes:timing="Word"` declares this as a per-syllable timed track. The default is "Line."
- `<div>` groups lines into verses/choruses/sections.
- `<p>` is one line. `itunes:key="L1"` is a stable line ID. `ttm:agent` ties it to a vocalist (used for duet rendering).
- `<span begin="" end="">` is one syllable. Time values are in seconds with millisecond precision, or `M:SS.fff` format. Apple mixes the two formats in a single document.
- `leadingSilence="0.320"` declares the intro silence so the renderer can anchor the timeline.
- `<translations/>` exists in the schema but is empty for this song. Apple's translation feature only works on tracks where Apple has commissioned translations.


## DOM structure

Apple uses a stack of custom elements with nested shadow DOM. None of this is reachable by a normal `querySelector` from the page — you have to recurse through shadow roots.

```
amp-lyrics                           [shadow root]
  └─ amp-lyrics-display-time-synced  [shadow root, hydrated="" ]
     └─ amp-lyrics-display-synced-line  (one per line, hydrated="")
        └─ ruby.display-synced-line.is-current.is-animating[.is-secondary-vocalist][.is-duet]
           └─ button.line              (entire line is clickable — seeks audio to line start)
              ├─ div.primary-vocals
              │  └─ span.group  (one per word/phrase)
              │     ├─ div.main
              │     │  └─ span.syllable[.emphasis]
              │     │       data-content="..."       (the displayed text)
              │     │       data-duration="1133"      (ms)
              │     │       data-delay="0"            (ms from line start)
              │     │       data-script="Jpan"        (Unicode script: Jpan, Hans, Hant, Latn, etc.)
              │     │     └─ span.letter × M          (one per displayed character)
              │     └─ rt.supplementary
              │        └─ span.syllable               (twin of main, same data-duration/data-delay)
              │           └─ span.letter
              └─ div.secondary                        (translation slot, empty when no translation)
```

Side-by-side custom elements that appear in the line list:

- `amp-lyrics-display-instrumental-line` — used for instrumental breaks; renders the three-dot loading animation.
- `amp-lyrics-display-synced-line` with `.collapsible` — used for non-current lines during long instrumental sections.

### Classes that drive state

| Class | On element | Meaning |
|---|---|---|
| `.is-current` | the `<ruby>` | This is the active line right now |
| `.is-animating` | the `<ruby>` | Currently in transition (active or fading out) |
| `.is-first` | the `<ruby>` | First lyric line in the song |
| `.is-duet` | the `<ruby>` | Song has two vocalists |
| `.is-secondary-vocalist` | the `<ruby>` | This line is the second voice (right-aligned) |
| `.collapsible` | the `<ruby>` | Can collapse to height:0 during silence |
| `.emphasis` | the `<span class="syllable">` | Long-duration syllable that gets the bouncy scale |
| `.show-supplementary` | the `<span class="group">` | Show furigana/romaji for this group |

### Per-syllable data attributes

These are written once at hydration time and never change. They are the timing data, not the animation state.

```
data-content       the actual text (also as textContent)
data-duration      milliseconds the syllable takes
data-delay         milliseconds from line start when it begins
data-script        Unicode script: Jpan, Hans, Hant, Latn, Hang, etc.
dir                "auto", "ltr", or "rtl" for bidi
```


## The animation mechanism

### What happens every frame

Apple maintains a `requestAnimationFrame` loop driven off the live `<audio>` element's `currentTime`. On each tick:

1. Compute `lineProgress = (currentTime - lineStart) / lineDuration` for each currently-visible line.
2. Compute per-syllable progress with the syllable's `data-delay` and `data-duration`.
3. For each syllable's letters, compute four interpolated values from the eased syllable progress.
4. Write all four values plus `transform` as a single inline `style="..."` on every letter.

The browser then re-renders. There are no CSS animations, no `@property` declarations, no transitions on custom properties. All easing happens in JS.

### What gets written per letter

```css
transform: matrix(scale, 0, 0, scale, 0, dy);
--gradient-progress: <signed-percent>;
--text-shadow-blur-radius: <px>;
--text-shadow-opacity: <0..1>;
```

Captured frame-by-frame for letter `な` (parent syllable `なんぼ`, total duration 1.133s):

| t (s) | progress  | scale   | dy      | shadow-opacity | shadow-blur |
|-------|-----------|---------|---------|----------------|-------------|
| 12.85 | 59.53%    | 1.03615 | -1.480  | (rising)       | (rising)    |
| 12.94 | 77.90%    | 1.0445  | -1.825  | 0.209          | 5.22px      |
| 13.02 | 90.57%    | 1.0472  | -2.040  | (peak)         | (peak)      |
| 13.10 | 92.23%    | 1.0388  | -2.030  | (falling)      | (falling)   |
| 13.19 | 93.90%    | 1.0305  | -2.030  |                |             |
| 13.28 | 95.90%    | 1.0205  | -2.020  |                |             |
| 13.36 | 97.57%    | 1.0122  | -2.010  |                |             |
| 13.44 | 99.23%    | 1.0039  | -2.000  |                |             |
| 13.53 | 100.00%   | 1.0000  | -2.000  | 0              | 0px         |

### What this means visually

- Progress runs from `-20%` (fully unsung, off-canvas) to `100%` (fully sung). The -20% to 0% region is the pre-sung state. The 0% to 100% region is the visible wipe.
- Scale peaks at ~1.047× around progress=90%, then eases back to 1.0. This is the bounce. It happens just before the letter completes its wipe.
- `dy` goes from 0 to -2px and stays at -2px after the wipe. Each sung letter is permanently lifted 2 pixels. This is why active lines feel slightly "floaty" — they sit higher than inactive lines.
- Text-shadow opacity and blur are the pre-glow. Both ramp up before the wipe edge arrives (so the letter softly glows ahead of itself) and ramp down after.
- The easing curve is ease-out. Fast at start, slow at end. Sampled deltas: progress jumped +18% in 90ms early, then only +1.7% in 80ms near the end.

### The CSS rule that turns those values into pixels

```css
.display-synced-line.is-current .letter[style*="--text-shadow-opacity"] {
  text-shadow: 0 0 var(--text-shadow-blur-radius)
               rgba(255, 255, 255, var(--text-shadow-opacity));

  background-image: linear-gradient(90deg,
    rgba(var(--gradient-color), var(--gradient-color), var(--gradient-color),
         var(--gradient-color-alpha-active))  var(--gradient-progress),
    rgba(var(--gradient-color), var(--gradient-color), var(--gradient-color),
         var(--gradient-color-alpha))         calc(var(--gradient-progress) + 20%));

  background-clip: text;
  -webkit-text-fill-color: transparent;
  -webkit-background-clip: text;
}
```

- `--gradient-color` is `0` in light mode, `255` in dark mode. It's the single channel value that gets repeated for R, G, B.
- `--gradient-color-alpha-active` is `0.85` (the bright "sung" alpha).
- `--gradient-color-alpha` is `0.5` (the dim "unsung" alpha).
- The two-stop gradient creates a 20% transition band that IS the soft wipe edge. By placing the bright stop at `var(--gradient-progress)` and the dim stop at `calc(var(--gradient-progress) + 20%)`, you get a soft edge that travels left-to-right as progress increments.
- At `progress = -20%`, the bright stop is at -20% (off canvas left) and dim stop at 0%, so the entire visible letter shows the dim end-color. Unsung.
- At `progress = 100%`, the bright stop is at 100% and dim stop at 120%, so the entire visible letter shows bright. Fully sung.
- In between, the soft 20%-wide band sweeps across.

The selector `.letter[style*="--text-shadow-opacity"]` keys off the LITERAL inline-style attribute containing the custom property name. This is how Apple makes the rule only apply when the JS has written something. Letters that JS hasn't touched yet stay in their default state.

`background-clip: text` plus `-webkit-text-fill-color: transparent` is the gem. The text itself becomes a mask; the visible color comes entirely from the background gradient. This is the trick that lets a single `background-image` paint the wipe.

### Selectors that route the rule

The full set of CSS rules is layered:

```css
.display-synced-line.is-current
  .syllable:not(.emphasis)[style*="--gradient-progress"]   { ... }   /* whole-syllable wipe for non-emphasized */
.display-synced-line.is-current
  .letter[style*="--text-shadow-opacity"]                  { ... }   /* per-letter wipe for emphasis path */
```

For non-emphasized syllables, the gradient is painted once on the whole syllable span. For emphasized syllables, the gradient is painted per-letter. The DOM contains the letter spans in both cases; only the CSS routing differs.

### Why this approach instead of CSS animations

Three reasons, all important for karaoke:

1. Scrubbing. If you seek back 5s, the renderer just samples the new `currentTime` next frame. No animation state to roll back.
2. Pause. Stopping the audio stops the JS loop. CSS keyframes would keep running.
3. Variable line duration. Each line has its own length. With keyframes you'd need one set per line; with JS you parameterize by `data-delay` and `data-duration`.

The cost is JS work every frame. Apple writes inline style on every visible letter (in our song, ~30 letters visible at any moment). That's well within budget at 60fps.


## Layout

Measurements from the live page at the standard web-player size:

### Container

| Property | Value |
|---|---|
| Drawer width | 300px (web). On iPhone the lyrics view goes full-screen. |
| Drawer position | `right: 0; top: 0` |
| Drawer background | transparent (sits over the now-playing artwork blur) |
| Container `overflow` | `auto hidden` (vertical scroll only) |
| `scroll-snap-type` | none |

### Line layout

| Property | Value |
|---|---|
| Line slot height (single-row lyric) | 110px |
| Line slot height (wrapped two-line lyric) | 170px |
| Gap between lines | 30px (constant — from `margin-top: 30px` on `.line`) |
| Main text font-size | 22px |
| Main text line-height | 26px (1.18em) |
| Main text font-weight | 700 |
| Main text color | `rgba(255, 255, 255, 0.92)` at rest |
| Active line scale | 1.1× (CSS `transform: scale(var(--lyrics-current-line-scale, 1.1))`) |
| Active line padding-block | 12px |
| Active line color | `var(--lyrics-line-color-current)` (brighter) |
| Inactive line transform | `matrix(1, 0, 0, 1, 0, 0)` (identity) |
| Pre-intro line transform | `matrix(0.1, 0, 0, 0.1, 0, 0)` (collapsed to 10%) |
| Duet line width | 60% of container |
| Secondary vocalist alignment | `text-align: right; transform-origin: right center` |
| Margins | top: 30px, right: 45px, left: 20px, bottom: 0px |

### Furigana (the `<rt class="supplementary">`)

Apple's choice on the web: romaji renders BELOW the kanji, not above. Native HTML5 ruby positions `<rt>` above by default; Apple overrides this for "pronunciation help" hierarchy.

| Property | Value |
|---|---|
| Default state | `width: 0; max-height: 0; opacity: 0` (collapsed) |
| Active state | `.show-supplementary .supplementary { width: auto; max-height: 24px; margin-top: 0.2em; opacity: 1 }` |
| Transition | `width 0.4s linear, height 0.4s linear, margin-top 0.4s linear` |
| Font-size | 15px (68% of main) |
| Line-height | normal (~18px) |
| White-space | nowrap |
| Vertical position relative to main | `margin-top: 0.2em` |
| Main bottom → sup top gap | ~3px overlap (small overlap is intentional) |

### Translation (the `.secondary`)

| Property | Value |
|---|---|
| Default state | `max-height: 0; overflow: hidden` (collapsed) |
| Active state | `.is-visible { margin-top: 0.2em; overflow: visible }` |
| Font-size | 13px |
| Line-height | 15.6px (1.2em) |
| Visibility | Empty for our test song. Apple's catalog has no translation for it. |

### Font fallback per language

Apple uses `:lang()` selectors to swap fonts based on the language attribute. For Japanese:

```
font-family: -apple-system, BlinkMacSystemFont, "Apple Color Emoji", "SF Pro",
             "Hiragino Sans", "SF Pro Icons", "Hiragino Kaku Gothic Pro",
             "ヒラギノ角ゴ Pro W3", "メイリオ", "Meiryo",
             "ＭＳ Ｐゴシック", "Helvetica Neue", "Helvetica", "Arial", sans-serif;
```

For Chinese: `PingFang SC` / `PingFang HK` / `PingFang TC` depending on region.
For Korean: `Apple SD Gothic Neo`.
For Arabic: `Arabic UI Display`.
For Bengali/Gujarati/Hindi/Kannada/Malayalam/Marathi/Odia/Punjabi/Tamil/Telugu: matching Kohinoor family.
For Thai: `Thonburi Pro`.


## Time precision

Apple's `mkInstance.currentPlaybackTime` (the public MusicKit JS API) only ticks ~4 Hz. That is too slow for 60 fps lyric reveal.

Apple reaches into the private MusicKit player to grab the live `<audio>` element's `currentTime`, which updates per frame:

```js
const player = mkInstance.services.mediaItemPlayback._currentPlayer;
const mediaElement = player._targetElement;
const offset = player._buffer?.currentTimestampOffset || 0;
const preciseTime = mediaElement.currentTime - offset;
```

Side note relevant to instrumenting this with CDP: Apple Music's page can have 7+ `<audio>` elements on it. Most are empty 90-second placeholder/preview slots. The live one is the one whose `src` starts with `blob:` and `paused === false`. Selecting `document.querySelector('audio')` returns the first one and gives you a `currentTime` of 0 forever.


## Animation curve

The mapping from raw audio time to the four animation values is non-linear. From sampled trajectory:

- `progress` (gradient position): ease-out. Fast through the first 70%, slow through the last 30%.
- `scale`: bell curve. Rises from 1.0 at progress=0% to ~1.047 around progress=85-90%, then eases back to 1.0 by progress=100%.
- `dy`: bell-shaped rise from 0 to ~-2.04 (peak) then settles at -2.0. Does NOT return to 0.
- `text-shadow-opacity`: rises before progress turns positive (anticipatory glow), peaks around progress=50-70%, falls to 0 by progress=100%.

Approximate curves (eyeballed from the captured frames, hand-modeled for Manaoke):

```
let p = (currentTime - syllableStart) / syllableDuration;  // 0..1
let eased = 1 - Math.pow(1 - p, 2.5);                       // ease-out cubic-ish
let gradientProgress = -20 + 120 * eased;                   // -20% to 100%

// Bell-shaped scale punch
let bell = Math.sin(Math.PI * eased);                       // 0 at ends, 1 at middle
let scale = 1.0 + 0.047 * bell;

// Permanent lift
let dy = -2.0 * eased;

// Anticipatory glow
let glowPhase = Math.max(0, eased - 0.1);                   // starts after 10% in
let textShadowOpacity = 0.8 * Math.sin(Math.PI * glowPhase);
let textShadowBlur = 8 * Math.sin(Math.PI * glowPhase);
```

The exact curve is hidden inside Apple's minified JS. Hand-modeled curves above produce a very close visual match.


## DOM creation, not destruction

When a line activates, Apple does NOT recreate the DOM. The same `amp-lyrics-display-synced-line` elements are present in the tree from page load (all 30 of them for our song). The only changes during playback are:

- `.is-current` / `.is-animating` classes flipping on `<ruby>` elements
- Inline `style` writes on `.letter` elements (continuous)
- `.show-supplementary` toggling per group (rare — usually static for the song)

This matters for performance. Apple builds the tree once and just mutates attributes. Layout doesn't reflow on line change; the active line's `transform: scale(1.1)` is a compositor-only operation.


## Custom properties Apple exposes for theming

The renderer reads these from `getComputedStyle` so they can be overridden at any level:

```
--lyrics-line-font-size           : default 22px
--lyrics-line-line-height         : default 1.1818181818
--lyrics-line-margin-top          : default 30px
--lyrics-line-margin-right        : default 45px
--lyrics-line-margin-left         : default 20px
--lyrics-line-margin-bottom       : default 0
--lyrics-current-line-padding-block : default 12px
--lyrics-current-line-scale       : default 1.1
--lyrics-line-supplementary-font-size : default 15px
--lyrics-line-supplementary-line-height : default 1.2em
--lyrics-line-secondary-font-size : default 13px
--lyrics-line-secondary-line-height : default 1.2em
--lyrics-line-color               : default var(--systemTertiary)
--lyrics-line-color-current       : default var(--systemPrimary)
--lyrics-line-color-hover         : default var(--systemPrimary)
--lyrics-line-overbleed           : default 0
--lyrics-line-duet-width          : default 60%
--gradient-color                  : 0 in light mode, 255 in dark mode
--gradient-color-alpha            : 0.5
--gradient-color-alpha-active     : 0.85
--gradient-direction              : "to right" (or "to left" for RTL)
```


## Side-by-side with third-party reimplementations

YouLy+ extension and Steve-xmh's AMLL library both attempt to mimic Apple's behavior. Both diverge from Apple's actual approach in important ways:

| Aspect | Apple (actual) | YouLy+ | AMLL (Steve-xmh) |
|---|---|---|---|
| Animation driver | Per-frame JS writes | CSS `@keyframes` with `animation-delay` | Mix of CSS keyframes + JS scrubbing |
| Pre-glow | Anticipatory `text-shadow` ramp on each letter | "pre-highlight" class on next syllable | CSS class state machine |
| Bounce | `transform: matrix()` scale+dy on each letter | `transform: translateY(-3.5%)` keyframe | Spring-based JS interpolation |
| Furigana | Native HTML `<ruby>` + `<rt>` | Plain divs stacked | Plain divs stacked |
| Scrubbing | Free — values resample next frame | Animation has to be re-fired | Spring re-targets |
| CPU | Continuous (writes every letter every frame) | Idle once started | Continuous |

If you copy ONE thing from Apple, copy the per-frame inline-style write loop. It's simpler than people assume and handles all the edge cases (scrub, pause, variable line length, RTL).

If you can't afford per-frame JS, the YouLy+ keyframe approach is a reasonable second-best, but you lose smooth scrubbing.


## Translations and pronunciations: how the menu works

The lyrics drawer has a "..." menu button at the top right. Inside:

- "Show pronunciations" / "Hide pronunciations" — toggles the `<rt class="supplementary">` visibility by adding/removing `.show-supplementary` on `.group` elements.
- "Show translations" / "Hide translations" — controls `.is-visible` on `.secondary`. Only meaningful when Apple's catalog has translations for the song. In our test song, Apple's catalog has empty `<translations/>`, so this toggle does nothing.

Pronunciations are sourced from the same `syllable-lyrics` payload — the `<rt>` data comes back in the TTML via `data-content` on the supplementary syllables (e.g. `nanbo` paired with `なんぼ`).

The YouLy+ extension, if installed, adds its OWN translation overlay using `lyrics-translation-container` elements injected into a parallel `lyrics-plus-container`. Apple is unaware of these. They are NOT part of Apple's pipeline.


## Edge cases observed

- Instrumental breaks: a separate custom element `amp-lyrics-display-instrumental-line` renders the three-dot loading animation. It lives between `amp-lyrics-display-synced-line` elements when there's a gap in lyrics longer than ~4 seconds.
- Leading silence: the first line has `.is-first.collapsible` and uses `animation: none` (skip the collapse) so it's ready to appear at the song's first lyric moment.
- Duet handling: lines tagged `ttm:agent="voice2"` in the TTML get rendered as `.is-secondary-vocalist`. The CSS flips alignment to the right and origin to right-center. Width caps at `--lyrics-line-duet-width` (60% default) so the two voices can sit side-by-side visually.
- Bidi: `dir="auto"` on `<span class="syllable">` lets browser pick LTR vs RTL per syllable. For mixed-script lines (Latin + Japanese) this matters.
- `data-script="Hans"` vs `data-script="Jpan"` vs `data-script="Hant"`: Apple distinguishes simplified Chinese (Hans), Japanese (Jpan), and traditional Chinese (Hant) per syllable for font selection.


## Captured files (kept for future reference)

- `/tmp/amll-probe/apple-styles.css` — 70KB of Apple's actual lyric CSS extracted from shadow roots
- `/tmp/amll-probe/three-layer-run.jsonl` — 498-tick log spanning the full 191s song
- `/tmp/amll-probe/screens_3l/*.png` — 79 screenshots covering 0 to 191s
- `/tmp/amll-probe/probe.py`, `/tmp/amll-probe/cdp.py` — Python CDP harness used to drive the observation
- `/tmp/amll-probe/stream_three_layers_v2.py` — the run that produced this data
- `/tmp/amll-probe/measure_layout.js` — DOM measurement script for spacing/size data
- `/tmp/amll-probe/dump_live.js` — frame-by-frame state dump for one syllable

To re-run the harness: launch `/tmp/amll-probe/launch_chrome.sh`, sign into Apple Music, play, then run any of the scripts above. Each script attaches to the page at `localhost:9222`.


## Addendum — second song observation, AKARUIHEYA by LEX & LANA (2024)

Captured 2026-06-01 against `music.apple.com/us/album/akaruiheya/1738640378?i=1738640379`. Japanese hip-hop/rap, 2:54, duet between LEX (v1) and LANA (v2). Streamed 0→170.92s via second isolated Chrome on port 9223. 117 screenshots, 555 ticks, 34 unique lines captured of 50 total. TTML payload at `/tmp/amll-probe/akaruiheya.ttml`.

This song tests cases the CreepHyp ballad didn't: rap timing, two-vocalist duet, mixed Japanese/English within a single lyric line, English-only lyric lines, and multi-line translation grouping.

### How rap timing differs from ballad timing

| Metric | CreepHyp ballad | AKARUIHEYA rap |
|---|---|---|
| Song length | 191s | 174s |
| Lyric lines | 30 | 50 |
| Total syllables | ~140 | 192 |
| Vocal coverage (% song with lyrics) | ~60% | 76% |
| Syllables per vocal-second | ~1.0 | 1.44 |
| Median syllable duration | ~900ms | 627ms |
| Fastest syllable | ~400ms | 168ms |
| Slowest syllable | ~2200ms | 2711ms |
| Syllables under 200ms | 0 | 5 |
| Syllables over 1000ms | many | 28 |

Rap density isn't shorter syllables on average. It's wider dynamic range — fast stab phrases (168ms) mixed with longer held notes (2.7s). The per-letter animation budget stays the same, but the visible wipe travels faster within shorter spans.

### Syllable chunking varies per song

For CreepHyp, syllables were small (mostly 1-3 characters per span). For AKARUIHEYA, syllables are much bigger — Apple groups by word/phrase boundary rather than mora:

```
L1: "昔は何にもなくて酷かった暮らし"  (15 chars) — 5 spans
L19: "落ちて俺はなんもなくなった いまはこれだけ" (24 chars) — 2 spans
```

When a span holds 10+ characters, the per-letter wipe is what makes the animation legible. A whole-syllable wipe would feel binary; per-letter makes the long span animate smoothly across many letters with the same 20% transition band sweeping across each one.

### Voice agent tags and duet rendering

TTML metadata contains three agents:

```xml
<ttm:agent type="person" xml:id="v1"/>
<ttm:agent type="person" xml:id="v2"/>
<ttm:agent type="other"  xml:id="v2000"/>
```

`v1` and `v2` map to LEX and LANA. `v2000` (type="other") is reserved for non-vocalist content like ad-libs or "everyone" sections, but I didn't see it rendered in the visible DOM during this song. Some lines use it in the TTML; Apple's renderer may hide them from the synced view.

Voice rotation across the song:
- L1-L5: v1 (LEX, primary)
- L6-L11: v2 (LANA, marked `is-secondary-vocalist`, right-aligned)
- L12-L20: v1 (LEX back to primary)
- L21+: alternating per line

The `<ruby>` element's class flips between `is-secondary-vocalist` and the default depending on the line's agent. CSS handles the rest:

```css
.display-synced-line.is-secondary-vocalist {
  transform-origin: right center;
  text-align: right;
}
.display-synced-line.is-secondary-vocalist .line {
  text-align: right;
}
```

For tracks marked `is-duet`, both voices' lines get capped to 60% of container width (`--lyrics-line-duet-width: 60%`), creating the visual split.

### Mixed Japanese/English handling

Line L8 in the TTML:

```xml
<p begin="29.971" end="32.447" itunes:key="L8" ttm:agent="v2">
  <span begin="29.971" end="30.345">I'll</span>
  <span begin="30.345" end="30.620">say</span>
  <span begin="30.620" end="31.140">goodbye</span>
  <span begin="31.140" end="31.350">to</span>
  <span begin="31.350" end="32.447">暗い闇</span>
</p>
```

In the rendered DOM, the supplementary (romaji) row reads "I'll say goodbye to kurai yami" — Apple keeps the English portions verbatim and only generates pronunciation for the Japanese portions. The translation row reads "I'll say goodbye to dark darkness" — Apple translates everything end-to-end including the English (treating 暗い闇 literally).

This means the supplementary `<rt>` per-syllable text uses `data-content="I'll"` (English same as source) for English spans and `data-content="kurai yami"` (romaji of just the JP part) for Japanese spans. Twins remain locked via shared `data-delay` and `data-duration`.

### English-only lyric lines

L24 "But I found music" is pure English. In this case:
- The supplementary row is empty/hidden (nothing to transliterate from English)
- The translation row is empty (already in English)
- Only the main row renders

This is detected at TTML parse time by checking if any span in the line has non-Latin `data-script` (Jpan, Hans, Hant, etc.). If not, supplementary is suppressed.

### Translation aggregation across multiple lines

The English translation for several JP line pairs combines into one English sentence rendered under the SECOND of the two JP lines:

```
L17: マイクを握っても        (no English translation row)
L18: ダメじゃん これじゃね   "It's no good even if you grab the microphone, this is it."
```

The first line's translation slot is empty; the combined English appears under the line that completes the thought. This is Apple's translation pipeline, not the TTML structure — TTML has each line's translation slot separately. Apple's translators decide how to chunk meaning across lines.

### Layout for line wrap

When a JP line is long enough to wrap into two visual rows, the slot height grows from 110px to ~170px. The supplementary row sits below BOTH wrapped lines. The translation row sits below that. So a 3-tier wrapped line takes ~200px vertical space, vs 140px for a 3-tier single-row line.

```
+---------- single-row line slot 140px (30 margin + 110 button) ----------+
|  kanji (22px, 1 row)                                                    |
|  romaji (15px)                                                          |
|  translation (13px)                                                     |
+-------------------------------------------------------------------------+

+---------- wrapped line slot 200px (30 margin + 170 button) -------------+
|  kanji line 1 (22px)                                                    |
|  kanji line 2 (22px)                                                    |
|  romaji aligned to kanji boundaries (15px)                              |
|  translation as paragraph below (13px, can take 1-2 lines)              |
+-------------------------------------------------------------------------+
```

### Pre-vocal silence handling

CreepHyp had explicit `leadingSilence="0.320"` in the TTML metadata. AKARUIHEYA's TTML has NO leadingSilence attribute. Apple's renderer falls back to using the first span's `begin` time minus a small margin (~0.5s) as the implicit silence period. During this period the first line is marked `.is-first.collapsible.is-current` and rendered at scale 0.1 (collapsed), then expands as the first syllable approaches.

### Faster syllables exercise the animation more aggressively

A 168ms syllable has the same animation budget as a 2700ms one. The shape is the same (ease-out progress curve, bell-shaped scale punch, anticipatory glow). What changes is the per-frame delta: at 60fps over 168ms, that's only ~10 frames to get from -20% progress to 100% progress. Apple's renderer handles it by computing in JS each frame — no per-letter optimization needed because the math is the same.

For Manaoke: if you implement Apple's approach, your fast syllables will animate the same as slow ones automatically. There's no "rap mode" vs "ballad mode" — the math is duration-parameterized.

### Anything else worth knowing

- Apple Music's web player has 7+ `<audio>` elements on the page; the live one is the only one whose `src` starts with `blob:`. Selecting `document.querySelector('audio')` returns the first which is an empty preview clip stuck at t=0. Use `audios.find(x => x.src && x.src.startsWith('blob:'))`.
- The CSS for instrumental breaks uses three CSS-animated dots inside `amp-lyrics-display-instrumental-line`. They scale 1.1 → 0.9 in a loop tied to `@keyframes gap-loop`. This is the ONE place Apple actually uses CSS keyframes — for the loading-dot ambient animation during instrumental silence.
- AKARUIHEYA's drawer at the moment of capture was full-screen (because Chrome window was wider), not the 300px side panel. The lyrics container expanded to fill available width, with the same proportional sizing. The `.line` font-size custom property scales accordingly.
- Apple's translations for hip-hop are aggressively colloquial. "鳴り止まないから" rendered as "Because [the pain] won't stop ringing" — Apple inserts bracketed clarifications where needed.

### Captured files for AKARUIHEYA

- `/tmp/amll-probe/akaruiheya-ttml.json` — raw API response with TTML
- `/tmp/amll-probe/akaruiheya.ttml` — extracted TTML XML
- `/tmp/amll-probe/akaruiheya-run.jsonl` — 555-tick log + 117 screen entries
- `/tmp/amll-probe/akaruiheya_screens/*.png` — 117 screenshots across full song
- `/tmp/amll-probe/stream_akaruiheya.py` — the runner that produced this data (uses port 9223)

