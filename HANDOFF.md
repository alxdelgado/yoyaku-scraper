# Session Handoff — yoyaku-scraper

Full context for the scraper and its FastAPI backend — architecture, decisions,
and what's still open. Frontend-specific context lives in its own
`frontend/HANDOFF.md` rather than here.

---

## What the project does

**yoyaku-scraper** is a vinyl release discovery tool. It scrapes [yoyaku.io](https://yoyaku.io) — a WooCommerce-based record store — and returns every release whose style tags contain **all** of the requested genres simultaneously. Each result also carries its live stock status, and can be narrowed to yoyaku.io's own "Arrivals" category. The intersection result is written to `yoyaku_results.json` and `yoyaku_results.csv`, and surfaced in a browser UI backed by a FastAPI service.

Default filter: **Deep House + Techno + Tech House** (all three must be present).

Public-facing docs live in `README.md` (GitHub landing page) and `yoyaku_scraper.md` (CLI reference) — this file is the internal continuation log: architectural decisions, why things are built the way they are, and what's still open.

---

## Repository contents

| File | Purpose |
|---|---|
| `yoyaku_scraper.py` | Core scraper (async, two-phase, Cloudflare-bypassing) |
| `api.py` | FastAPI backend wrapping `run_scraper()` — job queue + SSE log streaming |
| `README.md` | GitHub-facing overview: features, usage, output schema, architecture |
| `yoyaku_scraper.md` | End-user CLI documentation (usage, style names, output format) |
| `july_updates.md` | Changelog for the in-stock tagging + recent-arrivals feature |
| `frontend/index.html` | Self-contained browser UI — demo mode + live API wiring |
| `frontend/HANDOFF.md` | Frontend-specific continuation log (design language, JS structure, open items) |
| `tests/test_scraper.py` | Unit tests for all pure/near-pure scraper functions |
| `tests/test_api.py` | Unit tests for the FastAPI backend (job lifecycle, SSE, isolation) |
| `HANDOFF.md` | This file — scraper/backend context. See `frontend/HANDOFF.md` for the UI |
| `REVIEW_TASKS.md` | Completed engineering review list (all 7 tasks done) |
| `high-severity-fixes.md` | Fix log for the FastAPI backend's initial high-severity bugs |
| `.gitignore` | Excludes `yoyaku_results.*`, `__pycache__`, `.cf_session/` |

Output artefacts (`yoyaku_results.json`, `yoyaku_results.csv`) are local-only — not committed.

---

## Dependencies

```bash
# Core scraper
pip install curl-cffi beautifulsoup4 lxml

# API backend
pip install fastapi uvicorn

# Tests
pip install pytest pytest-asyncio httpx
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
| `SLUG_PATH_OVERRIDES` | `{"arrivals": "category"}` | Routes the reserved `"arrivals"` slug to `/category/` instead of `/style/` — see below |

### Arrivals as a pseudo-style

yoyaku.io maintains its own "Arrivals" listing at `/category/arrivals/`, which uses byte-for-byte the same card markup and pagination as a `/style/{slug}/` page. Rather than adding a parallel code path for it, `run_scraper(recent=True)` just adds `"Arrivals"` into `required_styles` before slugs are built — `style_to_slug("Arrivals")` naturally resolves to `"arrivals"`, and `SLUG_PATH_OVERRIDES` routes that one slug to `/category/` instead of `/style/`. Every other part of the two-phase pipeline (probing, URL collection, intersection, caching) treats it as just another source to intersect. `_page_urls()` is the single source of truth for this routing — `probe_style()` calls `_page_urls(slug, 1)[0]` rather than rebuilding the URL itself, so there's nowhere for the two to drift apart.

This is why `--recent` costs nothing extra over a normal multi-style run: no per-release page fetch, no date parsing. It also means recency is *scoped to yoyaku.io's own curation window* for the Arrivals category, not an exact day-count — an exact `--added-since N days` cutoff was considered (it would require fetching each release's own page for its `datePublished` JSON-LD) and deliberately declined in favor of this zero-cost approach.

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

If a style name doesn't map to a real page, `probe_style` returns `0` and that style is skipped with a warning. If `recent=True` and the `"arrivals"` slug itself fails to probe, `run_scraper` logs an explicit `"err"`-typed warning (`Arrivals category unreachable — results are not filtered by recency`) rather than silently returning an unfiltered result — a real recency claim should never look identical to a network failure.

`"Arrivals"` is a reserved pseudo-style name — typing it as a literal CLI/API style argument has the same effect as `--recent`/`recent=True`, since it resolves to the same slug. This isn't documented as a supported entry point (the frontend only exposes it via the toggle), just a side effect of the routing design worth knowing about if a raw style list is ever accepted from an untrusted source.

### In-stock detection

`Release.in_stock` is read straight off the listing card's own class list — `"outofstock" not in card.get("class", [])` in `_parse_card()` — during the same Phase 2 parse that already extracts title/artists/price/etc. No new selector, no new request. Two things worth remembering if this ever needs debugging:

- There's an unrelated WooCommerce **taxonomy** class, `product_cat-out-of-stock`, which is a manually-assigned category tag, not the real stock status. Only the bare `outofstock` token (no `product_cat-` prefix) means anything here.
- A card with *neither* `instock` nor `outofstock` present is currently classified `in_stock=True` (absence of the negative token, not presence of the positive one) — this is exercised explicitly by `test_in_stock_defaults_true_when_no_stock_class_present` in `tests/test_scraper.py` so it stays a documented default rather than an accidental one.

**If a user reports a release showing in-stock when the site "looks" out of stock:** check the *specific* card on the *specific* listing page(s) the query actually parsed from (Phase 2 only reads the smallest source's cached pages) — not just the product detail page, which usually agrees but is a different DOM. A release's own product page also renders "Related products" / "You may also like" / "More items from {label}" sections further down, which are full `li.product` cards for *other* releases and can carry their own out-of-stock badges — easy to mistake for the main release's status when skimming the page. The reliable check on a product page itself is the schema.org JSON-LD (`"availability":"http://schema.org/InStock"`) or whether the button reads "Add to cart" (in stock) vs "Read more" (out of stock), not just eyeballing badges nearby.

---

## Test suite

88 tests total across both files. Run everything with `pytest tests/ -v`.

### `tests/test_scraper.py` — pure and near-pure scraper functions

| Class | Function tested | Cases |
|---|---|---|
| `TestParseStyles` | `parse_styles` | greedy multi-word matching, unknown passthrough, case-insensitive, empty |
| `TestStyleToSlug` | `style_to_slug` | hyphens, acronyms, spaces, whitespace edge cases |
| `TestText` | `_text` | None, plain text, whitespace collapse, nested children |
| `TestPageUrls` | `_page_urls` | page-1 bare URL quirk, subsequent pages, total count, Arrivals→`/category/` routing |
| `TestParseCard` | `_parse_card` | all fields, multi-artist, soup immutability (T1 regression guard), in-stock true/false/absent |

The soup immutability test (`test_soup_not_mutated_after_parse`) is a regression guard for Task 1 — it ensures `_parse_card` operates on a deep copy so the original card DOM is never destroyed.

`make_card()` takes a `stock_class` param (default `"instock"`) to drive the in-stock test cases without touching the ~15 pre-existing `TestParseCard` tests.

### `tests/test_api.py` — FastAPI backend

Covers `GET /styles` (bare array, matches `KNOWN_STYLES` exactly, excludes the reserved `"Arrivals"` pseudo-style), `POST /scrape` (job creation, validation, concurrency/recent/in_stock_only forwarding), `GET /stream/{id}` (SSE framing, message types, done-event termination), `GET /results/{id}` (ready/running/errored states), and job isolation across concurrent requests.

Every hand-written fake `run_scraper` replacement in this file has to accept `recent` and `in_stock_only` keyword args (with defaults) to match the real signature — if `run_scraper()`'s parameters change again, these fakes need updating in lockstep or `_run_job`'s call raises `TypeError` inside the background task (which the tests would then just see as a job that errors, not an obvious signature mismatch).

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

`frontend/index.html` is a self-contained, zero-dependency browser UI (no build
step). It has its own **`frontend/HANDOFF.md`** now — frontend design language,
technical structure, and open items live there, not here, so the two contexts
don't get tangled as both grow. Backend-relevant surface worth knowing about
from this side: `api.py` exposes `GET /styles` (returns `KNOWN_STYLES` verbatim,
bare array) specifically so the frontend never has to hardcode its own copy of
the style list again — added during the frontend redesign, but it's a backend
endpoint, hence noted here too.

---

## What's next

FastAPI backend (Phase A) is done — `api.py` exists, is wired to the frontend, and has full test coverage in `tests/test_api.py`. In-stock tagging and the `--recent` Arrivals filter (this session) are also done and pushed to `main`.

### Potential improvements

**Precise date-based recency** — `--recent` currently means "in yoyaku.io's own Arrivals category," not an exact day-count. An `--added-since N` cutoff using each release's `datePublished` (available via JSON-LD on the release's own page, confirmed present during this session's investigation) was scoped and explicitly declined in favor of the zero-extra-request Arrivals approach — revisit if exact-day precision becomes a real requirement, but it costs one HTTP request per matching release.

**Result caching with TTL** — Inventory changes slowly (new arrivals once or twice a day). A 6-hour TTL on results would eliminate repeat scrapes entirely.

**Persistent curl-cffi session** — Currently a new `AsyncSession` per run. If CF tightens session continuity checks, persisting cookies between runs would help.

**`--any` flag** — Switch filter from AND (`issubset`) to OR (any style matches). Small change, high utility.

**`--out` flag** — Output path is currently hardcoded to `yoyaku_results.*` in cwd.

### If Cloudflare protection escalates

1. Try a newer `IMPERSONATE_BROWSER` target (`"chrome124"`, `"chrome131"`) — curl-cffi ships multiple profiles.
2. If CF requires a JS or interactive challenge, reintroduce Playwright with a **persistent context** (`browser.launch_persistent_context(user_data_dir=".cf_session")`). First run pays the challenge; subsequent runs within cookie TTL (~30 min) are free.
3. CF challenge pages are detectable: `"just a moment" in soup.title.string.lower()` (see `CF_CHALLENGE_TITLE` constant).
