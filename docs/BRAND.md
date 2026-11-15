# Split2nd brand system

> **Provisional palette warning.** The eleven colours below are placeholders until
> the final palette arrives. Do not cut merchandise, print signage, or publish
> social assets from these values. Everything is wired so the swap is one file.

**Swap the palette in `frontend/tokens.css` only.** Edit the eleven raw values in
the palette block; every surface, state, boxing, and shadow token derives from
them. `frontend/app.css` contains no raw colour values.

---

## 1. Palette

| Token | Hex | Role |
|---|---|---|
| `--ink` | `#080A09` | App background, deepest surface |
| `--carbon` | `#111513` | Panels, pills, input fields |
| `--graphite` | `#1A1F1C` | Raised panel top, card gradient |
| `--line` | `#2A322C` | Borders, dividers, range track |
| `--chalk` | `#F1F5F0` | Primary text, eyes, S2 mark |
| `--fog` | `#98A39A` | Muted body text, secondary metadata |
| `--signal` | `#3DFFA2` | Primary action, Clean, live, GO |
| `--amber` | `#FFC857` | Solid / warm warning, XP, streak |
| `--red` | `#FF5B5B` | Slow / error / remove |
| `--violet` | `#B18CFF` | False start, special / versus |
| `--ice` | `#6EE7FF` | Informational accent, badges, tears |

Derived tokens resolve with `color-mix()` from the palette. The hex values in
the table below are the current provisional result, shown so designers can
check swatches without opening a browser.

### Surfaces and text

| Token | Current result | Role |
|---|---|---|
| `--bg` | `#080A09` | Page background (alias of `--ink`) |
| `--panel` | `#111513` | Card and control surface (alias of `--carbon`) |
| `--panel-2` / `--panel2` | `#1A1F1C` | Card gradient top (alias of `--graphite`) |
| `--panel-hover` | `#181D1A` | Button hover |
| `--well` | `#0B0E0C` | Chips, inset wells, XP chips |
| `--well-2` | `#0A0D0B` | Room-player rows |
| `--void` | `#040605` | Camera tiles and phone body |
| `--void-deep` | `#030404` | Stimulus overlay |
| `--scrim` | `ink @ 88%` | Sticky top bar, blurred |
| `--text` | `#F1F5F0` | Primary text (alias of `--chalk`) |
| `--muted` | `#98A39A` | Secondary text (alias of `--fog`) |
| `--dim` | `#818B83` | Captions, mono labels, footer |
| `--green-ink` | `#080A09` | Text on signal-green buttons |
| `--line-soft` | `#1C221E` | Hairline dividers inside cards |
| `--line-strong` | `#667068` | Hover / focus border; 3.58:1 on panel |
| `--line-bright` | `#5C655E` | Toast / elevated border |

### State washes

| Token | Current result | Role |
|---|---|---|
| `--good-bg` / `--good-line` | `#0C1E15` / `#2D5540` | Clean chip, turn row, winner |
| `--warn-bg` / `--warn-line` | `#1C190F` / `#595335` | Solid chip, warm warning |
| `--bad-bg` / `--bad-line` | `#1E1110` / `#593B36` | Slow chip, error |
| `--false-bg` / `--false-line` | `#16141D` / `#48465A` | False-start / versus |
| `--danger-bg` / `--danger-line` | `#3E1C1B` / `#6A3E3A` | Phone disconnect banner |
| `--danger-text` | `#FFB5B5` | Banner text on `--danger-bg` |

### Type, radii, spacing, motion

| Token | Value | Role |
|---|---|---|
| `--mono` | SF Mono → Cascadia → Roboto Mono → ui-monospace | Times, scores, labels, chips |
| `--sans` | Inter → Segoe UI → system-ui → sans-serif | Headlines, prose, buttons |
| `--radius` / `--radius-lg` | `14px` | Cards, gate mark |
| `--radius-md` | `10px` | Buttons, inputs, video, rows |
| `--radius-sm` | `8px` | Header mark, grade chip, camera tile |
| `--radius-xs` | `5px` | Keycap |
| `--radius-pill` | `999px` | Pills, chips, toast, XP |
| `--space-1…9` | `4 6 8 10 12 14 18 22 30px` | Spacing scale for new UI |
| `--motion-lights` | `420ms` | Start-light cadence |
| `--motion-snap` | `120ms` | Result / grade snap |
| `--motion-xp` | `400ms` | XP fill |
| `--motion-fast` | `120ms` | Hover and button feedback |
| `--motion-light-fade` | `40ms` | Single lamp on/off |
| `--motion-go` | `60ms` | GO reveal |
| `--motion-toast` | `180ms` | Toast in/out |
| `--motion-blink` | `1.4s` | Camera recording dot |
| `--ease-out` | `cubic-bezier(.16,1,.3,1)` | Entrances |
| `--ease-snap` | `cubic-bezier(.2,.8,.2,1)` | Result snap |

No webfont is loaded. The stacks above are deliberate: they use fonts already on
the demo laptop and phone, so the page works offline and with no CDN.

---

## 2. Boxing tokens and vignette contract

The boxing UI owns `boxing.css`. `tokens.css` owns the values; do not hard-code
new greens, reds, or glows there.

| Token | Maps to | Word that must accompany it |
|---|---|---|
| `--box-good` | `--signal` `#3DFFA2` | **Clean** |
| `--box-slow` | `--amber` `#FFC857` | **Solid** |
| `--box-bad` | `--red` `#FF5B5B` | **Slow** |
| `--box-false` | `--violet` `#B18CFF` | **False start** |
| `--box-glow-good` | signal @ 60% | Clean vignette / rim |
| `--box-glow-slow` | amber @ 55% | Solid vignette / rim |
| `--box-glow-bad` | red @ 55% | Slow vignette / rim |
| `--box-glow-false` | violet @ 50% | False-start vignette / rim |

Recommended class contract for `boxing.css` (intentionally not defined in
`app.css`, so the boxing worker owns layout):

```css
.box-vignette { position: relative; overflow: hidden; }
.box-vignette::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  border-radius: inherit;
  opacity: 0;
  transition: opacity var(--motion-snap) var(--ease-snap);
}
.box-vignette--good::after  { box-shadow: inset 0 0 64px var(--box-glow-good);  opacity: 1; }
.box-vignette--slow::after  { box-shadow: inset 0 0 64px var(--box-glow-slow);  opacity: 1; }
.box-vignette--bad::after   { box-shadow: inset 0 0 64px var(--box-glow-bad);   opacity: 1; }
.box-vignette--false::after { box-shadow: inset 0 0 64px var(--box-glow-false); opacity: 1; }

.box-good  { color: var(--box-good); }
.box-slow  { color: var(--box-slow); }
.box-bad   { color: var(--box-bad); }
.box-false { color: var(--box-false); }
```

Rules:

- The vignette is an edge glow, never a full-screen colour flood. The athlete
  must still read the RT number.
- Pair every colour with its word. A red border alone is not a result.
- Start lights stay red until GO, then signal green. Do not use violet for GO.
- `--shadow-glow-good`, `--shadow-glow-bad`, and `--shadow-focus` are available
  for single-element rim/focus effects.

---

## 3. Logo and mascot

### Wordmark and mark

The wordmark is live text in the page: `SPLIT<em>2</em>ND`, with the `2` in
`--green`. The square S2 mark is `--green` with `--green-ink` lettering, radius
`--radius-sm` in the top bar and `--radius-lg` on the phone gate.

- Keep clearspace equal to at least the height of the `S` on the mark.
- Minimum wordmark height: `18px` on screen; below that use the S2 tile alone.
- Do not stretch, outline, add a gradient, or set the mark on signal green.
- The header mark and gate mark are existing classes (`.brand .mark`,
  `.gate .mark`); keep those selectors stable.

### Mascot states

The mascot is a bold boxing glove with eyes and an S2 cuff. All four are the same
silhouette, so switching state does not move layout.

| State | Asset | Use | Expression |
|---|---|---|---|
| Idle | `/static/assets/mascot-idle.svg` | Waiting, camera gate, empty board | Neutral eyes, small smile, soft signal glow |
| Happy | `/static/assets/mascot-happy.svg` | Clean rep, PB, streak | Raised brows, big smile, amber sparks |
| Sad | `/static/assets/mascot-sad.svg` | Slow, miss, false start | Droopy lids, frown, ice tear |
| Fire | `/static/assets/mascot-fire.svg` | New PB, hot streak, match win | Narrow eyes, gritted teeth, flames |

The SVGs are **currentColor-friendly when inlined**. Set `color` for the eyes,
glints, and S2 mark, and override these custom properties if a surface needs it:

```css
--mascot-body: var(--green);
--mascot-ink: var(--ink);
--mascot-glow: var(--green);
--mascot-spark: var(--amber);      /* happy */
--mascot-tear: var(--cyan);        /* sad */
--mascot-flame: var(--amber);      /* fire */
--mascot-flame-hot: var(--red);    /* fire */
```

When used through `<img>`, the SVG uses its own fallback palette; page custom
properties do not cross the image boundary. Use `<img alt="...">` for decorative
or labelled use, or inline the SVG when you need live theming.

- Minimum size: `48px` for the mascot, `24px` for a badge.
- Do not put the signal-green mascot directly on a signal-green surface.
- Do not mirror, rotate, or add drop shadows to the mascot.

### Badges

| Asset | Meaning |
|---|---|
| `/static/assets/badge-streak.svg` | Consecutive training days |
| `/static/assets/badge-chest.svg` | PB / milestone reward |
| `/static/assets/badge-star.svg` | Standout rep or achievement |
| `/static/assets/badge-level.svg` | Level-up / rank |

Badges use the same ink rim and bold outline. Pair each with a text label —
never rely on the icon alone.

---

## 4. Asset paths

| Asset | Path | Size |
|---|---|---|
| Idle mascot | `/static/assets/mascot-idle.svg` | 256 × 256 |
| Happy mascot | `/static/assets/mascot-happy.svg` | 256 × 256 |
| Sad mascot | `/static/assets/mascot-sad.svg` | 256 × 256 |
| Fire mascot | `/static/assets/mascot-fire.svg` | 256 × 256 |
| Streak badge | `/static/assets/badge-streak.svg` | 128 × 128 |
| Chest badge | `/static/assets/badge-chest.svg` | 128 × 128 |
| Star badge | `/static/assets/badge-star.svg` | 128 × 128 |
| Level badge | `/static/assets/badge-level.svg` | 128 × 128 |
| Favicon | `/static/assets/favicon.svg` | 64 × 64 |
| App icon | `/static/assets/icon-1024.svg` | 1024 × 1024 |
| Social card | `/static/assets/og-card.svg` | 1200 × 630 |
| Web manifest | `/static/manifest.webmanifest` | — |

The static mount serves `frontend/` at `/static`, so these paths are live without
any backend change.

**Apple touch icon:** no PNG was generated because the repository has no
rasterizer and adding one was out of scope. Use `icon-1024.svg` in the
`apple-touch-icon` link for now. If a PNG becomes required, render
`icon-1024.svg` to `frontend/assets/apple-touch-icon.png` at 180 × 180 and point
the link at that file. Document the change here.

---

## 5. Exact `<link>` tags

Both pages currently load only `/static/app.css`, which begins with
`@import url("tokens.css")`, so tokens already reach the page without an HTML
edit. When the HTML owners touch the heads, add these tags in this order.

### `frontend/index.html`

```html
<meta name="theme-color" content="#080A09">
<link rel="stylesheet" href="/static/tokens.css">
<link rel="stylesheet" href="/static/app.css">
<link rel="icon" href="/static/assets/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/static/assets/icon-1024.svg">
<link rel="manifest" href="/static/manifest.webmanifest">
```

### `frontend/play.html`

```html
<meta name="theme-color" content="#080A09">
<link rel="stylesheet" href="/static/tokens.css">
<link rel="stylesheet" href="/static/app.css">
<link rel="icon" href="/static/assets/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/static/assets/icon-1024.svg">
<link rel="manifest" href="/static/manifest.webmanifest">
```

Keep the existing `viewport`, `apple-mobile-web-app-capable`, and
`apple-mobile-web-app-status-bar-style` metas. The manifest sets
`name`/`short_name` `Split2nd`, `start_url` `/play`, `display` `standalone`,
`theme_color` and `background_color` `#080A09`.

Optional social preview (not required for the demo):

```html
<meta property="og:title" content="Split2nd — train the first 250 milliseconds">
<meta property="og:description" content="Your phone is the sensor and the scoreboard.">
<meta property="og:image" content="/static/assets/og-card.svg">
<meta name="twitter:card" content="summary_large_image">
```

---

## 6. Motion

| Moment | Value | Notes |
|---|---|---|
| Start lights | **420ms** cadence | Mirrors backend `LIGHT_INTERVAL_MS`; the JS schedules lamps, CSS only fades a lamp in 40ms |
| Result snap | **120ms** | `--motion-snap` + `--ease-snap`; RT/grade appear, never count up |
| XP | **400ms** | `--motion-xp`; fill/chip settles once, no looping shine |
| Hover / press | 120ms / 60ms | `--motion-fast`, `--motion-go` |
| Toast | 180ms | `--motion-toast` |

`app.css` ends with a global reduced-motion block:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

New boxing animations must respect the same media query and must not be driven
only by JS timers. If a user prefers reduced motion, jump straight to the final
state, including the vignette and XP fill.

---

## 7. Accessibility

### Contrast (provisional palette)

| Foreground | Background | Ratio | Use |
|---|---|---|---|
| `--chalk` | `--ink` | 18.02:1 | Body / headline |
| `--fog` | `--ink` | 7.60:1 | Muted text |
| `--dim` | `--ink` | 5.63:1 | Captions |
| `--dim` | `--graphite` | 4.74:1 | Worst-case caption on a card |
| `--signal` | `--ink` | 15.15:1 | Primary accent |
| `--amber` | `--ink` | 12.91:1 | Solid / XP |
| `--red` | `--ink` | 6.52:1 | Slow |
| `--violet` | `--ink` | 7.62:1 | False start |
| `--ice` | `--ink` | 13.74:1 | Informational |
| `--danger-text` | `--danger-bg` | 9.03:1 | Disconnect banner |
| `--line-strong` | `--panel` | 3.58:1 | Focus/hover border (non-text, 3:1) |

The old `--dim` (`#5E6961`) measured **3.47:1** on `--ink` and failed AA for
normal text. The tokenised value resolves to `#818B83`: 5.63:1 on ink and 4.74:1
on graphite. Keep caption text on `--dim`, never on `--line`.

### Non-negotiables

- **Never colour alone.** Every result carries a word: **Clean**, **Solid**,
  **Slow**, **False start** (plus **Missed**). `common.js:gradeWord()` already
  supplies these words; do not replace them with a coloured dot.
- Keep the grade letter G/Y/R/F visible alongside the word and colour.
- Give the result card an `aria-live="polite"` announcement in the boxing UI.
- Focus states must be visible: use `--shadow-focus` or `--line-strong`; do not
  rely on the browser default against a dark panel.
- Numerals use `--mono` with `font-variant-numeric: tabular-nums`; never animate
  the RT value during a measurement.
- Every decorative SVG gets `alt=""`; every meaningful SVG gets a text
  alternative from the surrounding label.
- Do not use pure red/green as the only signal for colour-vision deficiency.
  The words and letters carry the same information.
- Minimum tap target is 44 × 44 CSS px on the phone; the existing pills are
  display-only, not controls.

---

## 8. File ownership and change rules

- `frontend/tokens.css` — brand palette and every derived token. **Only place
  with raw hex values.**
- `frontend/app.css` — consumes tokens; existing class names and selector
  behaviour are stable. No raw colour literals.
- `frontend/assets/**` — mascots, badges, favicon, app icon, social card.
- `frontend/manifest.webmanifest` — install metadata.
- `docs/BRAND.md` — this document.
- `boxing.css` — owned by the boxing UI worker; consume the tokens above rather
  than repeating palette values.

When the final palette arrives: edit the eleven values in the palette block,
check the contrast table, re-render nothing else, and update the "current
result" column here if it is still present.
