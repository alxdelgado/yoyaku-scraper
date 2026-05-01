# yoyaku_scraper.py — Review Task List

Source file: `yoyaku_scraper.py`
Review date: 2026-04-30
Reviewer: Code Reviewer (engineering-team)

---

## Task 1 — Fix style-text stripping in `_parse_card` ✓ DONE

**Severity:** Medium | **Category:** Correctness

**Location:** `yoyaku_scraper.py:187–195`

```python
feat = card.select_one("p.product-features")
styles_list: list[str] = []
fmt = ""
if feat:
    styles_list = [_text(a) for a in feat.select("a[href*='/style/']")]
    full_text = _text(feat)
    for s in styles_list:
        full_text = full_text.replace(s, "")       # ← fragile: order-dependent
    fmt = re.sub(r"[|\s]+", " ", full_text).strip(" |")
```

**Problem:** `str.replace()` is applied in DOM order. If `styles_list` contains substring relationships (e.g. `"House"` before `"Tech House"`), stripping `"House"` first corrupts the `"Tech House"` token before it can be removed cleanly.

**Fix:** Decompose the style `<a>` tags from the DOM before calling `get_text()`, so no string manipulation is needed.

```python
if feat:
    styles_list = [_text(a) for a in feat.select("a[href*='/style/']")]
    for a in feat.select("a[href*='/style/']"):
        a.decompose()
    fmt = re.sub(r"[|\s]+", " ", _text(feat)).strip(" |")
```

---

## Task 2 — Add unit tests for pure functions

**Severity:** High | **Category:** Testing

**Location:** No test file exists. Functions to cover:

| Function | Location | Notes |
|----------|----------|-------|
| `parse_styles` | `yoyaku_scraper.py:52–74` | Greedy multi-word token matching |
| `style_to_slug` | `yoyaku_scraper.py:93–94` | URL slug generation |
| `_text` | `yoyaku_scraper.py:89–90` | Whitespace normalization |
| `_parse_card` | `yoyaku_scraper.py:171–202` | Full card parse (needs HTML fixture) |

**Fix:** Create `tests/test_scraper.py`. Minimum cases:

```python
# parse_styles
assert parse_styles(["tech", "house"]) == ["Tech House"]
assert parse_styles(["deep", "house", "techno"]) == ["Deep House", "Techno"]
assert parse_styles(["acid"]) == ["Acid"]
assert parse_styles(["unknown"]) == ["Unknown"]   # passthrough

# style_to_slug
assert style_to_slug("Tech House") == "tech-house"
assert style_to_slug("Nu Disco")   == "nu-disco"
assert style_to_slug("IDM")        == "idm"

# _text — requires a minimal BeautifulSoup fixture
# _parse_card — requires an HTML fixture from a real product card
```

---

## Task 3 — Promote CSS selectors and sentinel strings to named constants

**Severity:** Medium | **Category:** Maintainability

**Location:** Magic strings spread across `yoyaku_scraper.py`:

| String | Line(s) | Used in |
|--------|---------|---------|
| `"a.woocommerce-LoopProduct-link"` | 163, 172 | `fetch_urls`, `_parse_card` |
| `"li.product"` | 215 | `fetch_cards` |
| `"p.product-artists a"` | 180 | `_parse_card` |
| `"p.product-labels .product-sku"` | 182 | `_parse_card` |
| `"p.product-labels .product-label-name a"` | 183–184 | `_parse_card` |
| `"p.product-features"` | 187 | `_parse_card` |
| `"a[href*='/style/']"` | 191 | `_parse_card` |
| `"span.price .woocommerce-Price-amount"` | 197 | `_parse_card` |
| `".page-numbers[href]"` | 143 | `probe_style` |
| `"just a moment"` | 126 | `get_soup` (CF detection) |
| `"chrome120"` | 243 | `main` (curl-cffi impersonation) |

**Fix:** Add a constants block near the top of the file (after `BASE_URL`):

```python
# ── Selectors & sentinels ────────────────────────────────────────────────────
SEL_PRODUCT_LINK    = "a.woocommerce-LoopProduct-link"
SEL_PRODUCT_CARD    = "li.product"
SEL_ARTISTS         = "p.product-artists a"
SEL_SKU             = "p.product-labels .product-sku"
SEL_LABEL_NAME      = "p.product-labels .product-label-name a"
SEL_FEATURES        = "p.product-features"
SEL_STYLE_LINK      = "a[href*='/style/']"
SEL_PRICE           = "span.price .woocommerce-Price-amount"
SEL_PAGE_NUMBERS    = ".page-numbers[href]"
CF_CHALLENGE_TITLE  = "just a moment"
IMPERSONATE_BROWSER = "chrome120"
```

---

## Task 4 — Cache Phase 1 soups to avoid re-fetching in Phase 2

**Severity:** Medium | **Category:** Performance

**Location:** `yoyaku_scraper.py:154–166` (Phase 1 fetch) and `yoyaku_scraper.py:205–219` (Phase 2 fetch)

**Problem:** `fetch_urls` fetches and parses each page in Phase 1 but discards the `BeautifulSoup` object after extracting URLs. Phase 2 (`fetch_cards`) re-fetches those same pages for the smallest style, doubling HTTP requests for it.

```python
# Phase 1 — soup is discarded after URL extraction
async def fetch_urls(session, url, sem) -> set[str]:
    soup = await get_soup(session, url, sem)   # fetched here ...
    return {a["href"] for a in soup.select(SEL_PRODUCT_LINK) if a.get("href")}
    # ... soup is thrown away

# Phase 2 — same URL fetched again
async def fetch_cards(session, url, sem, keep_urls) -> list[Release]:
    soup = await get_soup(session, url, sem)   # re-fetched here
```

**Fix:** Change `fetch_urls` to return `tuple[set[str], BeautifulSoup]`, store the soups for the smallest-style pages during Phase 1, and pass them directly into Phase 2 instead of re-fetching.

---

## Task 5 — Break `main()` into focused helper functions

**Severity:** Medium | **Category:** Maintainability

**Location:** `yoyaku_scraper.py:224–329` (`main` is ~105 lines covering 5 distinct responsibilities)

**Problem:** `main()` handles argument parsing, style resolution, page probing, Phase 1 URL collection, Phase 2 card parsing, deduplication, and file output in a single function. This makes it difficult to test phases in isolation and hard to follow the control flow.

**Fix:** Extract into discrete functions:

```python
async def probe_all_styles(session, style_slugs, sem) -> dict[str, int]:
    """Returns {slug: page_count} for all reachable styles."""

async def phase1_collect_urls(session, valid, sem) -> dict[str, set[str]]:
    """Fetches all style pages and returns {slug: set_of_release_urls}."""

async def phase2_parse_cards(session, slug, page_count, intersection, sem) -> list[Release]:
    """Fetches and parses cards for the smallest style, filtered to intersection."""

def write_output(results: list[Release]) -> None:
    """Writes yoyaku_results.json and yoyaku_results.csv."""
```

`main()` becomes a thin orchestrator calling these in sequence.

---

## Task 6 — Add `--concurrency` CLI flag

**Severity:** Low | **Category:** Maintainability / Usability

**Location:** `yoyaku_scraper.py:41` (hardcoded constant) and `yoyaku_scraper.py:224–234` (argparse setup)

```python
CONCURRENCY = 10   # ← not user-configurable
```

**Fix:** Add an optional argument to the existing `argparse` block:

```python
parser.add_argument(
    "--concurrency", "-j",
    type=int,
    default=10,
    help="Max concurrent HTTP requests (default: 10)",
)
```

Then replace `asyncio.Semaphore(CONCURRENCY)` with `asyncio.Semaphore(args.concurrency)`.

---

## Task 7 — Use `asyncio.as_completed` in Phase 1 result collection ✓ DONE

**Severity:** Low | **Category:** Performance / Correctness

**Location:** `yoyaku_scraper.py:263–275`

```python
all_tasks: list[tuple[str, asyncio.Task]] = []
for slug, count in valid.items():
    for page_url in _page_urls(slug, count):
        all_tasks.append((slug, asyncio.create_task(
            fetch_urls(session, page_url, sem)
        )))

for slug, task in all_tasks:           # ← awaits in creation order
    style_url_sets[slug].update(await task)
    done += 1
    print(f"  [{done}/{total_pages}]", end="\r")
```

**Problem:** Tasks run concurrently but results are collected in creation order. A slow first task stalls the progress counter and delays processing of completed tasks. Phase 2 (lines 302–306) correctly uses `asyncio.as_completed` — Phase 1 should match.

**Fix:** Restructure Phase 1 collection to use `asyncio.as_completed`, attaching the `slug` via a wrapper coroutine so results can still be routed to the right `style_url_sets` bucket.

---

## Summary

| # | Task | Severity | Category |
|---|------|----------|----------|
| 1 | Fix `_parse_card` style stripping | Medium | Correctness |
| 2 | Add unit tests for pure functions | High | Testing |
| 3 | Promote selectors/sentinels to constants | Medium | Maintainability |
| 4 | Cache Phase 1 soups (avoid re-fetch) | Medium | Performance |
| 5 | Break `main()` into helper functions | Medium | Maintainability |
| 6 | Add `--concurrency` CLI flag | Low | Usability |
| 7 | Use `asyncio.as_completed` in Phase 1 | Low | Performance |
