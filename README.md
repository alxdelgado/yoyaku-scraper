# yoyaku-scraper

A fast, dependency-light scraper for [yoyaku.io](https://yoyaku.io), a
WooCommerce-based vinyl record store. Given a set of genre/style tags, it
returns every release that carries **all** of them simultaneously — plus
whether each release is currently in stock, and an optional filter for
releases that are newly arrived.

Ships as three layers: a standalone CLI, a FastAPI backend, and a browser UI.

```bash
python3 yoyaku_scraper.py "Deep House" "Tech House" --recent --in-stock-only
```

---

## Features

- **Style intersection** — pass any number of style names; only releases tagged
  with *all* of them are returned (not "any").
- **In-stock tagging** — every result carries an `in_stock` field, read
  directly from the listing page's own markup at no extra request cost. Use
  `--in-stock-only` to drop out-of-stock releases from the output.
- **Recent arrivals filter** — `--recent` narrows results to releases that are
  both in yoyaku.io's own "Arrivals" category *and* match your selected styles.
- **No browser required** — bypasses Cloudflare bot detection via TLS
  fingerprint impersonation (`curl-cffi`), not a headless browser. A full run
  typically completes in 1–2 seconds.
- **FastAPI backend + browser UI** — for running scrapes interactively with a
  live log stream and a sortable results table, instead of the CLI.

---

## Requirements

Python 3.10+. No browser installation needed.

```bash
# Core scraper
pip install curl-cffi beautifulsoup4 lxml

# API backend (optional)
pip install fastapi uvicorn

# Tests (optional)
pip install pytest pytest-asyncio httpx
```

---

## Usage

### CLI

```bash
# Default: Deep House + Techno + Tech House
python3 yoyaku_scraper.py

# Custom styles — every one listed must match
python3 yoyaku_scraper.py "Deep House" "Minimal"
python3 yoyaku_scraper.py acid minimal tech house      # multi-word styles auto-detected

# Only releases that are also in yoyaku.io's Arrivals category
python3 yoyaku_scraper.py techno --recent

# Drop out-of-stock releases from the results
python3 yoyaku_scraper.py techno --in-stock-only

# Combine both, with a custom concurrency limit
python3 yoyaku_scraper.py minimal "tech house" --recent --in-stock-only -j 8
```

| Flag | Effect |
|---|---|
| `--recent` | Narrow results to yoyaku.io's Arrivals category ∩ the selected styles |
| `--in-stock-only` | Drop releases marked out of stock |
| `--concurrency N` / `-j N` | Max concurrent HTTP requests (default: 10) |

Results are written to `yoyaku_results.json` and `yoyaku_results.csv` in the
current directory, and a summary is printed to stdout.

### API

```bash
uvicorn api:app --reload
```

| Endpoint | Method | Description |
|---|---|---|
| `/scrape` | `POST` | Start a scrape job, returns `{"job_id": ...}` |
| `/stream/{job_id}` | `GET` | Server-sent events log stream for the job |
| `/results/{job_id}` | `GET` | Final release list once the job completes |

```json
POST /scrape
{
  "styles": ["Minimal", "Tech House"],
  "concurrency": 10,
  "recent": true,
  "in_stock_only": true
}
```

### Frontend

Open `frontend/index.html` directly in a browser (with the API running on
`localhost:8000`). Select styles, toggle **Recent arrivals** / **In stock
only**, set a concurrency, and execute — output streams into the log panel and
renders in a sortable results table with JSON/CSV export.

---

## Output format

Each release is returned with the following fields (JSON and CSV both use the
same schema):

| Field | Description |
|---|---|
| `title` | Release title |
| `url` | Direct link to the release page |
| `artists` | Artist(s), comma-separated if multiple |
| `label` | Record label name(s) |
| `sku` | Catalogue number |
| `styles` | All style tags on the release (not just the ones you filtered on) |
| `format` | Vinyl format and any edition notes |
| `price` | Price in euros |
| `in_stock` | `true`/`false` — whether the release is currently purchasable |

```json
[
  {
    "title": "Get Down",
    "url": "https://yoyaku.io/release/get-down-asc2/",
    "artists": "Various Artists",
    "label": "Aspect Music",
    "sku": "ASC2",
    "styles": "Deep House, Tech House, Techno",
    "format": "12\"",
    "price": "17,50 €",
    "in_stock": true
  }
]
```

---

## How it works

`curl-cffi` impersonates Chrome's TLS fingerprint to get past Cloudflare
without launching a browser. HTML is parsed with BeautifulSoup, targeting the
style listing pages directly (`/style/{slug}/`) rather than individual product
pages, since each listing card already carries all the metadata needed —
including stock status.

**Two-phase intersection algorithm:**

1. **URL collection** — every page of every requested style (and, with
   `--recent`, yoyaku.io's own Arrivals category, treated as one more style) is
   fetched in parallel. Only the release URL is extracted per card. This
   produces one URL set per source.
2. **Intersection and targeted parse** — the URL sets are intersected
   (smallest-first). The full card parse (title, artists, label, styles,
   format, price, stock status) runs only on releases that survived the
   intersection, against the smallest source's already-cached pages — no
   re-fetch required.

This keeps DOM work to a small fraction of the pages actually fetched, and
avoids any per-release detail-page requests.

---

## Project structure

| Path | Purpose |
|---|---|
| `yoyaku_scraper.py` | Core scraper — CLI entry point and `run_scraper()` |
| `api.py` | FastAPI backend wrapping `run_scraper()` for the browser UI |
| `frontend/index.html` | Self-contained browser UI (demo mode + live API wiring) |
| `tests/` | Unit tests for the scraper and API |
| `yoyaku_scraper.md` | Detailed CLI usage reference |
| `july_updates.md` | Changelog for the in-stock and recent-arrivals features |
| `HANDOFF.md` | Internal architecture/decision log for continuing development |

---

## Testing

```bash
pytest tests/ -v
```
