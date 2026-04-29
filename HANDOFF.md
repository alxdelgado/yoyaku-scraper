# Session Handoff — yoyaku-scraper

This document captures the full context of how the scraper was designed and built, the decisions made along the way, and what to be aware of when making future edits.

---

## What the script does

`yoyaku_scraper.py` scrapes [yoyaku.io](https://yoyaku.io) — a WooCommerce-based record store — and returns every release whose style tags contain **all** of the styles specified as CLI arguments. Results are written to `yoyaku_results.json` and `yoyaku_results.csv`.

Default filter: **Deep House + Techno + Tech House** (all three must be present on a release).

---

## Repository contents

| File | Purpose |
|---|---|
| `yoyaku_scraper.py` | The scraper |
| `yoyaku_scraper.md` | End-user documentation (usage, style names, output format) |
| `HANDOFF.md` | This file |
| `.gitignore` | Excludes `yoyaku_results.json`, `yoyaku_results.csv`, `__pycache__`, `.cf_session/` |

`yoyaku_results.json` and `yoyaku_results.csv` are intentionally local-only — they are output artefacts, not source files.

---

## Dependencies

```bash
pip install curl-cffi beautifulsoup4 lxml
```

No browser installation required. Python 3.10+ (uses structural pattern matching and `dataclass`).

---

## Architecture decisions

### Why curl-cffi instead of Playwright

The site is behind Cloudflare Bot Management. Playwright (headless Chromium) was the first approach but ran into a hard constraint: **Cloudflare re-challenges every new browser context**, meaning each paginated listing page required a full browser launch (~4–5 minutes for 32 pages across 3 styles).

Several workarounds were attempted in sequence:
- `wait_until="networkidle"` — worked for the first page per session, timed out on subsequent ones
- `wait_until="load"` + polling — pages showed CF "Just a moment..." indefinitely
- Persistent context with `cf_clearance` cookie reuse — CF re-challenged anyway (cookie is fingerprint-bound)
- `playwright-stealth` patches — insufficient against CF Managed Challenge
- Headed browser (headless=False) — still challenged after the first page

**Resolution:** `curl-cffi` impersonates Chrome's TLS fingerprint at the socket level. Cloudflare's primary bot signal is the TLS client hello — matching Chrome's exactly causes CF to pass the request without issuing a JS challenge. Zero browser launches needed. Runtime dropped from ~4 minutes to ~1–2 seconds.

### Why style listing pages instead of individual product pages

Individual product pages (`/release/<slug>/`) had stricter CF protection than listing pages — they were blocked even with valid clearance cookies. The listing pages (`/style/<slug>/`) are sufficient because each product card already contains all needed metadata: title, artists, label, SKU, styles, format, and price. No secondary requests to product pages are needed.

### Two-phase URL intersection algorithm

Naively fetching full card data from all pages and then filtering wastes ~98% of DOM work (1,600 cards parsed, ~20 kept for the default filter).

**Phase 1 — URL sweep:** All pages across all requested styles are fetched in parallel (concurrency 10). Only one selector is evaluated per card — the release URL. This produces one URL set per style. Cost: O(N) with minimal per-card work.

**Phase 2 — Intersection + targeted parse:** URL sets are intersected smallest-first (fast pruning). Full card parsing runs only on the surviving URLs, fetched from the smallest style's pages. For the default three-style filter this is ~20 cards, reducing DOM selector calls by ~85%.

---

## Key constants (top of script)

| Constant | Default | Notes |
|---|---|---|
| `DEFAULT_STYLES` | `["Deep House", "Techno", "Tech House"]` | Used when no CLI args are passed |
| `CONCURRENCY` | `10` | Simultaneous HTTP requests. Safe to increase further. |
| `BASE_URL` | `"https://yoyaku.io"` | Change if the site moves |

---

## Data extraction details

All metadata is extracted from the product card HTML on the listing pages. The relevant selectors:

| Field | Selector |
|---|---|
| Title | `a.woocommerce-LoopProduct-link` (inner text) |
| URL | `a.woocommerce-LoopProduct-link[href]` |
| Artists | `p.product-artists a` |
| SKU | `p.product-labels .product-sku` |
| Label | `p.product-labels .product-label-name a` |
| Styles | `p.product-features a[href*='/style/']` |
| Format | `p.product-features` text with style names and pipes stripped |
| Price | `span.price .woocommerce-Price-amount` |

**Known edge case — format field:** `p.product-features` contains style links, a pipe separator, and the format string. The format is extracted by stripping style names and `|` characters from the full text. For a small number of releases, this element also contains a long product description (e.g. advance/limited releases with editorial copy), which bleeds into the format field. Not currently handled — low frequency, acceptable noise.

---

## Style name resolution

Style names are converted to URL slugs at runtime:

```python
def style_to_slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
```

`"Deep House"` → `"deep-house"` → `https://yoyaku.io/style/deep-house/`

If a style name doesn't map to a real page on the site, `probe_style` returns `0` and that style is skipped with a warning. No error is raised.

---

## Potential improvements

**Result caching with TTL** — The site's inventory changes slowly (new arrivals once or twice a day). Storing results locally with a timestamp and skipping the scrape when data is fresh would eliminate repeat runs entirely. Suggested TTL: 6 hours.

**Persistent curl-cffi session headers** — Currently a new `AsyncSession` is created per run. If CF tightens checks on cookie/session continuity, persisting the session to disk (cookies + headers) between runs would help.

**`--any` flag** — Currently the filter is strictly AND (all styles must match). An `--any` flag to switch to OR behaviour (at least one style matches) would be a natural extension and is a small change to the `issubset` call in `main()`.

**`--out` flag** — Output path is hardcoded to `yoyaku_results.*` in the working directory. A `--out` argument would make the script more portable.

---

## If Cloudflare protection changes

The site could increase CF protection at any time. If `curl-cffi` starts returning CF challenge pages (detectable by `"just a moment" in soup.title.string.lower()`), the fallback path is:

1. Try a newer `impersonate` target in `AsyncSession` (e.g. `"chrome124"`, `"chrome131"`) — `curl-cffi` ships multiple browser profiles.
2. If CF escalates to a JS or interactive challenge, reintroduce Playwright with a **persistent context** (`browser.launch_persistent_context(user_data_dir=".cf_session")`). The first run pays the challenge cost; subsequent runs within the cookie TTL (~30 min) reuse the session for free.
