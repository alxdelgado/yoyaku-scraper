# Session Handoff — yoyaku-scraper

Full context for the scraper, its architecture, the frontend, and everything needed to continue development without re-discovering prior decisions.

---

## What the project does

**yoyaku-scraper** is a vinyl release discovery tool. It scrapes [yoyaku.io](https://yoyaku.io) — a WooCommerce-based record store — and returns every release whose style tags contain **all** of the requested genres simultaneously. The intersection result is written to `yoyaku_results.json` and `yoyaku_results.csv`, and surfaced in a browser UI.

Default filter: **Deep House + Techno + Tech House** (all three must be present).

---

## Repository contents

| File | Purpose |
|---|---|
| `yoyaku_scraper.py` | Core scraper (async, two-phase, Cloudflare-bypassing) |
| `yoyaku_scraper.md` | End-user documentation (usage, style names, output format) |
| `frontend/index.html` | Self-contained browser UI — demo mode + live API wiring |
| `tests/test_scraper.py` | Unit tests for all pure/near-pure scraper functions |
| `HANDOFF.md` | This file |
| `REVIEW_TASKS.md` | Completed engineering review list (all 7 tasks done) |
| `.gitignore` | Excludes `yoyaku_results.*`, `__pycache__`, `.cf_session/` |

Output artefacts (`yoyaku_results.json`, `yoyaku_results.csv`) are local-only — not committed.

---

## Dependencies

```bash
pip install curl-cffi beautifulsoup4 lxml pytest
```

Python 3.10+ required (structural pattern matching, `dataclass`). No browser installation needed.

---

## Scraper architecture

### Why curl-cffi instead of Playwright

The site runs Cloudflare Bot Management. Every approach with Playwright was blocked:

- `wait_until="networkidle"` — worked page 1 only, timed out on subsequent pages
- Persistent context with `cf_clearance` cookie reuse — CF re-challenged (cookie is fingerprint-bound)
- `playwright-stealth` — insufficient against CF Managed Challenge
- Headed browser — still challenged after page 1

**Resolution:** `curl-cffi` impersonates Chrome's TLS fingerprint at the socket level. Cloudflare's primary signal is the TLS client hello — matching Chrome's exactly causes CF to pass without a JS challenge. Runtime: ~1–2 seconds vs ~4 minutes.

Impersonation target is `IMPERSONATE_BROWSER = "chrome120"` (top of file). Upgrade to `"chrome124"` or later if CF starts challenging again.

### Why listing pages, not product pages

Individual product pages (`/release/<slug>/`) had stricter CF protection. Listing pages (`/style/<slug>/`) are sufficient — each product card already contains title, artists, label, SKU, styles, format, and price. No secondary requests needed.

### Two-phase URL intersection

Naively parsing all cards and filtering wastes ~98% of DOM work (1,600 cards for 20 results).

**Phase 1 — URL sweep:** All style pages fetched in parallel (concurrency 10 by default, configurable via `-j`). Per card: one selector for the release URL only. Produces one URL set per style. Soups for the *smallest* style's pages are cached in memory (avoids re-fetch in Phase 2). Collection uses `asyncio.as_completed` — results processed as they arrive.

**Phase 2 — Intersection + targeted parse:** URL sets intersected smallest-first (fast pruning). Full card parsing runs only on survivors, from the cached soups. For the default three-style filter: ~20 cards parsed instead of ~1,600.

### Key constants (top of file)

| Constant | Default | Notes |
|---|---|---|
| `DEFAULT_STYLES` | `["Deep House", "Techno", "Tech House"]` | Used when no CLI args passed |
| `CONCURRENCY` | `10` | Default; overridden at runtime by `-j` |
| `BASE_URL` | `"https://yoyaku.io"` | Change if site moves |
| `IMPERSONATE_BROWSER` | `"chrome120"` | curl-cffi TLS target — upgrade if CF tightens |

### CSS selectors (all centralised)

All selectors are named constants near the top of `yoyaku_scraper.py`. A yoyaku.io markup change requires edits in one place only.

| Constant | Selector |
|---|---|
| `SEL_PRODUCT_LINK` | `a.woocommerce-LoopProduct-link` |
| `SEL_PRODUCT_CARD` | `li.product` |
| `SEL_ARTISTS` | `p.product-artists a` |
| `SEL_SKU` | `p.product-labels .product-sku` |
| `SEL_LABEL_NAME` | `p.product-labels .product-label-name a` |
| `SEL_FEATURES` | `p.product-features` |
| `SEL_STYLE_LINK` | `a[href*='/style/']` |
| `SEL_PRICE` | `span.price .woocommerce-Price-amount` |
| `SEL_PAGE_NUMBERS` | `.page-numbers[href]` |

### Known edge case — format field

`p.product-features` contains style links, a pipe separator, and the format string. Style `<a>` tags are `.decompose()`'d from a deep copy of the soup before `get_text()` is called — clean, no string surgery. For a small number of advance/limited releases, this element also contains editorial copy which bleeds into the format field. Low frequency, acceptable noise.

### Style name resolution

```python
def style_to_slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
```

`"Deep House"` → `"deep-house"` → `https://yoyaku.io/style/deep-house/`

If a style name doesn't map to a real page, `probe_style` returns `0` and that style is skipped with a warning.

---

## Test suite

`tests/test_scraper.py` covers all pure and near-pure functions.

| Class | Function tested | Cases |
|---|---|---|
| `TestParseStyles` | `parse_styles` | greedy multi-word matching, unknown passthrough, case-insensitive, empty |
| `TestStyleToSlug` | `style_to_slug` | hyphens, acronyms, spaces, whitespace edge cases |
| `TestText` | `_text` | None, plain text, whitespace collapse, nested children |
| `TestPageUrls` | `_page_urls` | page-1 bare URL quirk, subsequent pages, total count |
| `TestParseCard` | `_parse_card` | all fields, multi-artist, soup immutability (T1 regression guard) |

Run with: `pytest tests/test_scraper.py -v`

The soup immutability test (`test_soup_not_mutated_after_parse`) is a regression guard for Task 1 — it ensures `_parse_card` operates on a deep copy so the original card DOM is never destroyed.

---

## Completed engineering review (REVIEW_TASKS.md)

All 7 tasks from the code review are done.

| # | Task | What changed |
|---|---|---|
| 1 | Fix `_parse_card` style stripping | `.decompose()` on a `copy.copy()` — eliminates order-dependent string surgery |
| 2 | Unit tests | `tests/test_scraper.py` — 30+ cases |
| 3 | Selectors → named constants | One block at top of file, all magic strings removed |
| 4 | Cache Phase 1 soups | `fetch_urls` returns `tuple[set[str], BeautifulSoup]`; Phase 2 uses cached soups, no re-fetch |
| 5 | Break `main()` apart | `probe_all_styles`, `phase1_collect_urls`, `phase2_parse_cards`, `write_output` — `main()` is now a thin orchestrator |
| 6 | `--concurrency` / `-j` CLI flag | `argparse` argument, replaces hardcoded semaphore |
| 7 | `asyncio.as_completed` in Phase 1 | Matches Phase 2 — results processed as they land, not in creation order |

---

## Frontend

`frontend/index.html` is a self-contained, zero-dependency browser UI. No build step.

### Design language

Inspired by [immeasurable.com](https://www.immeasurable.com/) — typographic restraint, whitespace as the dominant element, mystery through subtraction.

- **Background:** `#0b0b09` warm near-black (not pure digital black)
- **Foreground:** `#e6dece` warm cream through five opacity stops — hierarchy without hue changes
- **Serif:** Cormorant Garamond 300 — brand wordmark only
- **Mono:** DM Mono 300 — all labels, data, log output
- **Texture:** All 20 genre names repeat across 14 rows behind the main panel at 5% opacity — the raw material of the engine, rendered as paper
- **Grain:** SVG `feTurbulence` noise at 2.8% opacity, no file dependency
- **Cursor:** 1px crosshair (18px span each axis) — no pointer, no circle

### Micro-expressions

| Element | Rest | Hover | Active/Selected |
|---|---|---|---|
| Style items | `opacity: 0.4` | `0.72` + hairline underline draws across name | `1.0` + 3px dot appears (spring bounce) |
| `execute` button | `opacity: 0.2`, italic serif | `0.88` + underline draws in over 0.6s | breathes `0.3↔0.6` while running |
| Log lines | — | — | fade in from `translateY(4px)`, 0.65s |
| Table rows | — | `background: rgba(paper, 0.022)` | stagger in at 60ms increments |
| App shell | `opacity: 0` | — | fades to 1 over 1.4s on load |

### Functional wiring

The UI has two modes controlled by `DEMO_MODE` at the top of the script block:

**`DEMO_MODE = true`** — `runDemo()` simulates a full scraper run in the browser with staged delays. No backend needed. Good for design review and demos.

**`DEMO_MODE = false`** — `runLive()` calls the FastAPI backend at `API_BASE = 'http://localhost:8000'`. Expects:
- `POST /scrape` — body: `{ styles: string[], concurrency: number }` → returns `{ job_id: string }`
- `GET /stream/{job_id}` — SSE stream of `{ type: "hi"|"err"|"", text: string }` events, terminated by a `done` event
- `GET /results/{job_id}` — returns the full `Release[]` JSON array

### What the frontend does NOT have yet

- The FastAPI backend (`api.py` or similar) that `runLive` connects to does not exist yet
- The scraper currently writes to disk; the backend would need to run it in a subprocess or refactor `main()` to return results instead of writing files

---

## What's next

### Immediate — FastAPI backend (Phase A)

Wire the existing scraper to the frontend via a small FastAPI app:

```
api.py
  POST /scrape        → spawn scraper job, return job_id
  GET  /stream/{id}   → SSE stream of log lines
  GET  /results/{id}  → return Release[] JSON
```

The scraper's `main()` already returns a `list[Release]` internally after Task 5's refactor — the backend just needs to capture that instead of letting `write_output()` handle it.

### Potential improvements (from original HANDOFF)

**Result caching with TTL** — Inventory changes slowly (new arrivals once or twice a day). A 6-hour TTL on results would eliminate repeat scrapes entirely.

**Persistent curl-cffi session** — Currently a new `AsyncSession` per run. If CF tightens session continuity checks, persisting cookies between runs would help.

**`--any` flag** — Switch filter from AND (`issubset`) to OR (any style matches). Small change, high utility.

**`--out` flag** — Output path is currently hardcoded to `yoyaku_results.*` in cwd.

### If Cloudflare protection escalates

1. Try a newer `IMPERSONATE_BROWSER` target (`"chrome124"`, `"chrome131"`) — curl-cffi ships multiple profiles.
2. If CF requires a JS or interactive challenge, reintroduce Playwright with a **persistent context** (`browser.launch_persistent_context(user_data_dir=".cf_session")`). First run pays the challenge; subsequent runs within cookie TTL (~30 min) are free.
3. CF challenge pages are detectable: `"just a moment" in soup.title.string.lower()` (see `CF_CHALLENGE_TITLE` constant).
