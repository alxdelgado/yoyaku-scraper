# yoyaku_scraper

Scrapes [yoyaku.io](https://yoyaku.io) and returns every release whose style tags include **all** of the styles you specify. Results are written to `yoyaku_results.json` and `yoyaku_results.csv`.

---

## Requirements

Python 3.10+ and two lightweight libraries — no browser installation required.

```bash
pip install curl-cffi beautifulsoup4 lxml
```

---

## Running the script

### Default run — Deep House + Techno + Tech House

```bash
python3 yoyaku_scraper.py
```

Returns releases tagged with **all three** of Deep House, Techno, and Tech House simultaneously.

### Custom styles

Pass any number of style names as arguments. Every style you list must be present on a release for it to appear in the results.

```bash
python3 yoyaku_scraper.py "Techno" "Acid"
python3 yoyaku_scraper.py "Deep House" "Minimal"
python3 yoyaku_scraper.py "House" "Dub Techno" "Ambient"
```

### Single style

```bash
python3 yoyaku_scraper.py "Techno"
```

Returns all releases tagged Techno.

---

## Style names

Style names map directly to the style pages on yoyaku.io (e.g. `Deep House` → `yoyaku.io/style/deep-house/`). The script converts them automatically — spacing and capitalisation are flexible:

| You type | Resolves to |
|---|---|
| `Deep House` | `/style/deep-house/` |
| `deep house` | `/style/deep-house/` |
| `DEEP HOUSE` | `/style/deep-house/` |
| `Tech House` | `/style/tech-house/` |
| `Techno` | `/style/techno/` |

Any style that has a listing page on yoyaku.io can be used. Examples of valid style names:

`Acid` · `Ambient` · `Breaks` · `Chicago` · `Deep House` · `Detroit` · `Dub` · `Dub Techno` · `Electro` · `Experimental` · `House` · `IDM` · `Jungle` · `Minimal` · `Minimal Techno` · `Nu Disco` · `Progressive House` · `Soul` · `Tech House` · `Techno`

---

## Output files

Both files are written to the same directory you run the script from. They are overwritten on each run.

### `yoyaku_results.json`

An array of release objects:

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
    "price": "17,50 €"
  }
]
```

### `yoyaku_results.csv`

The same fields as the JSON, one release per row, with a header row.

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

---

## How it works

The script uses `curl-cffi` to make HTTP requests that impersonate Chrome's TLS fingerprint. This passes Cloudflare's bot detection without launching a browser, making the script fast (~1–2 seconds for a full run) and dependency-light.

HTML is parsed with BeautifulSoup. The script targets the style-specific listing pages (`/style/deep-house/` etc.) rather than individual product pages — each listing card already contains all the metadata needed, so no secondary requests are made.

### Two-phase algorithm

**Phase 1 — URL collection:** All pages across all requested styles are fetched in parallel (concurrency 10). Only one selector is evaluated per card — the release URL. This produces one URL set per style.

**Phase 2 — Intersection and targeted parse:** The URL sets are intersected (smallest-first for fast pruning). The full card parse — title, artists, label, styles, format, price — runs only on the releases that survived the intersection. With the default three-style filter this is typically ~20 cards out of ~1,600 fetched, cutting DOM work by over 98%.

### Performance comparison

| Version | Approach | Wall time |
|---|---|---|
| Initial | Playwright, fresh browser per page, sequential styles | ~4–5 min |
| Intermediate | Playwright, two-phase, parallel styles | ~3–4 min |
| **Current** | **curl-cffi, two-phase, no browser** | **~1–2 sec** |
