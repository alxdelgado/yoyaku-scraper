# Frontend Handoff — yoyaku-scraper

Scope: `frontend/index.html` only. For the scraper core and the FastAPI backend
(`api.py`), see the root `../HANDOFF.md` — this file exists so frontend context
doesn't get buried in that one as both grow independently.

---

## What it is

A single self-contained HTML file — vanilla JS, inline CSS, no build step, no
npm/node dependency to *run* it (Node is only used incidentally in this repo for
`tests/test_frontend_links.js`). Deliberately kept this way rather than moving to
React/Vite/shadcn — confirmed directly with the user during the redesign that
followed. It must keep working opened directly via `file://`, which is why it's
one non-module `<script>` tag rather than `<script type="module">` split across
files: Chromium refuses ES module loads under `file://`, classic scripts don't
care.

---

## Design language — "warehouse flyer × record-shop ticket counter"

This replaced an earlier "immeasurable.com"-inspired near-black/serif-wordmark
identity outright (not an evolution of it — that direction was fully discarded
per the user's explicit choice). Two deliberate registers, mapped onto the
existing sidebar/main structural split rather than applied uniformly — a loud
flyer aesthetic and a dense sortable table fight each other if treated the same:

- **Sidebar = "flyer" register.** Loud, textured, allowed to be irregular.
  Ink-black background (`--ink: #16130f`) with a halftone dot texture
  (`radial-gradient` tiled at 5px, tinted faintly with the accent color), bold
  condensed poster display face (**Anton**) for the wordmark and section labels,
  one accent color used freely: **acid green** (`--accent: #c8fb3c`) — a
  thematic nod to "Acid"/"Acid House" being real yoyaku.io style tags, and the
  classic 90s rave-flyer spot color. Loading state is a spinning-vinyl motif
  (`.vinyl`, a small CSS radial/repeating-radial-gradient disc, `animation:
  vinyl-spin`) instead of the old "breathing" execute button.
- **Main panel (log + results) = "ticket-counter" register.** Calm, aligned,
  high legibility. Warm paper background (`--paper: #ece0c2`, not stark white),
  near-black ink text, ledger-style rules. A dashed divider (`border-bottom: 3px
  dashed`) between the log and results panels stands in for a ticket-tear
  perforation. `#main::before` carries a very faint SVG grain (opacity 0.02) —
  this is the *only* texture allowed in the main panel, and it's still
  effectively invisible behind the table specifically because `#res-tw` sits on
  its own opaque `background: var(--paper)` above it (`z-index: 1`).
- **Data font**: **Courier Prime** — literal typewriter/receipt character,
  distinct from the prior identity's DM Mono.
- **Hard rule, load-bearing for legibility**: accent color never appears inside
  `<tbody>` except one deliberate use — the in-stock/out-of-stock dot
  (`.stock-dot.on`). Every other cell stays ink-on-paper. Don't add a second
  accent use inside the table without revisiting this rule on purpose.
- **`sku` is rendered for the first time** as a "stamped catalog number"
  (`.stamp` — bordered tag, `rotate(-1.5deg)`) — it was already flowing through
  every `Release` object and every JSON/CSV export, just never shown in the
  table before this redesign.
- **Custom cursor removed, not reimplemented.** The prior `cursor: none` +
  JS-tracked crosshair (`#cur`, a `mousemove` listener) gave zero feedback on
  touch/keyboard input. Now just the native system cursor — a strict fix, not
  new scope, made while the file was already being rebuilt.

---

## Technical structure

Reorganized the previously flat top-level `const`/`function` soup into named
IIFEs in the same single `<script>` tag (no physical file split, no bundler):

| Namespace | Responsibility |
|---|---|
| `Styles` | Fetches the style list (see below), owns the fallback list |
| `State` | `selStyles` Set, `currentResults`, `sortCol`/`sortAsc`, via getters/setters |
| `Api` | `startScrape()`, `streamJob()`, `getResults()` — all `fetch`/SSE calls |
| `Render` | Table (re)rendering, `{ animate }` option (see below) |
| `Motion` | Row-reveal timing/cap, `prefers-reduced-motion` check |
| `Log` | Log panel printing, including progress-line collapsing (see below) |

`buildStyleChecklist()`, the execute/run flow (`runScraper`/`runLive`/`runDemo`),
and the export handlers stayed as top-level functions — they're one-shot wiring
called once at init, not state anyone else needs to share, so wrapping them in
a namespace would've been ceremony without benefit.

---

## `KNOWN_STYLES` drift — root cause fixed, not just re-synced

The style checklist used to hardcode its own 20-item list, separate from
`yoyaku_scraper.py`'s `KNOWN_STYLES` (39 real styles) — confirmed via git
history (`b563836`) that this list already drifted once for real: the backend
list grew and the frontend was never updated, and 3 of the frontend's own
entries (Chicago, Jungle, Soul) were never valid backend styles at all.

**Fix**: `api.py` now exposes `GET /styles`, returning `yoyaku_scraper.py`'s
`KNOWN_STYLES` verbatim as a bare JSON array (`GET /results` already returns a
bare array, so this stays consistent rather than introducing a wrapper object).
The frontend's `Styles.load()` fetches this once at init instead of hardcoding
anything — the two can't drift again because there's only one list now.

**Fallback behavior**, since a no-backend/demo mode already existed and needed
a story here:
- `CONFIG.DEMO_MODE === true` never calls `/styles` at all — it's already a
  no-backend mode, so it always uses `Styles.FALLBACK_STYLES` (10 common
  genres, hand-picked, all confirmed-real backend style names).
- Live mode fetches with a bounded timeout (`AbortSignal.timeout(3000)`) so a
  *hung* backend fails fast, not just an absent one. Any failure — timeout,
  non-200, empty array — falls back to the same `FALLBACK_STYLES` constant.
- Critically, a failed fetch also flips on a **persistent** banner
  (`#styles-degraded`, shown via `showDegraded()`) reading e.g. *"OFFLINE — 10
  fallback styles shown. Start the API and reload for the full list."* — this
  does **not** go through the log panel, because `Log.clear()` wipes that panel
  on every run (`runScraper()` calls it first thing) and a one-time log message
  would vanish the moment the user executes anything. The whole point of this
  fix was "never silently show a wrong/incomplete list again," so the signal
  has to survive longer than one log clear.
- Deliberately not caching a successful `/styles` response anywhere
  (`localStorage` etc.) — it's cheap and low-traffic; caching would just add a
  new staleness class for no real benefit.

Verified live: killed `uvicorn` mid-session and reloaded — banner appeared and
stayed visible, fallback list still let the UI function.

---

## Row-reveal animation — capped, and split from sort re-renders

Two real problems in the prior version, both hit while rebuilding this exact
code path for the new aesthetic:

1. Every row got its own `setTimeout(i * 60ms)` with no ceiling. A 1,800-row
   result set (a real number seen from an unfiltered Arrivals query) would
   have taken ~108 seconds to finish animating in, and scheduled 1,800 timers
   regardless.
2. `Render.table()` was also the sort-click redraw path — every header click
   wiped and recreated every `<tr>` and replayed the *entire* staggered reveal
   again.

**Fix**, in `Motion.revealRows()` + `Render.table(data, { animate })`:
- Only the first `REVEAL_CAP` (40) rows get a staggered `setTimeout` (22ms
  step); everything past the cap gets its `vis` class applied **synchronously**
  in the same pass — no timer scheduled at all for those, not just a
  zero-delay one.
- `Render.table()` takes `{ animate = true }`. A fresh result set (first render
  after a scrape completes) animates; the sort-click handler explicitly passes
  `{ animate: false }` so re-sorting only repaints, never re-fades the table.
- `Motion.reducedMotion()` checks `matchMedia('(prefers-reduced-motion:
  reduce)')` in JS and, if true, applies `vis` to every row immediately with no
  stagger at all — the CSS-only reduced-motion handling (still present, zeroes
  `animation-duration`/`transition-duration` globally) doesn't touch the
  JS-computed per-row `setTimeout` delay by itself, so this JS-level check is
  what actually removes the wait, not just the visible fade.

Verified live against a real 171-release Acid query: table rendered promptly,
clicking the Stock column header re-sorted with all rows already visible (no
re-fade), confirmed via screenshot.

---

## Log panel — progress-line collapsing

`yoyaku_scraper.py`'s Phase 1 emits one `"  [i/N]"` line per page fetched —
for a large multi-style run that's easily hundreds of separate lines, each
previously getting its own animated DOM node appended to `#log` with no cap
(same unbounded-growth shape as the row-reveal issue above, just in the log
panel instead of the table).

**Fix**, in `Log.print()`: a line matching `/^\s*\[\d+\/\d+\]\s*$/` reuses a
single tracked node (`progressNode`) and just updates its `textContent`,
instead of appending a new `<span>` per update. Any non-progress line resets
`progressNode` to `null`, so the next progress sequence (if a run has more than
one) starts a fresh node rather than resuming the old one. Verified live: a
4-page Acid probe showed as one line reading `[4/4]`, not four separate lines.

---

## Explicitly out of scope (documented, not forgotten)

Per the user's own prioritization when this redesign was scoped, these are
known gaps this pass did **not** address — noted so they don't get
accidentally made worse, not because they don't matter:

- **Responsive/mobile layout** — still a fixed `100vh` grid with `overflow:
  hidden` and a fixed-width sidebar (`--sb: 264px`). Unusable below a certain
  viewport width.
- **Deployability** — `CONFIG.API_BASE` is still hardcoded to
  `http://localhost:8000`; nothing is configurable via env/build, and no UI
  state (selected styles, toggles) persists across a reload.
- `tests/test_frontend_links.js` still hardcodes its own 20-style list, but as
  fixture input for generic slug-generation logic, not as a copy of the
  selectable style list — left untouched, not part of the drift this session
  fixed.

---

## What's next

If picking this back up: the two "explicitly out of scope" items above
(responsive layout, deployability/persisted state) are the most likely next
asks given how this session's prioritization conversation went. A component
build-out (React/Vite/shadcn) was considered and declined this round in favor
of staying single-file — worth re-confirming that choice still holds before
assuming it, rather than assuming this file's structure is permanent.
