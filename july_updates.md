# July 2026 updates

Two additions to `yoyaku_scraper.py`, `api.py`, and `frontend/index.html`: in-stock
tagging and a recent-arrivals filter. Both are additive — existing usage without the
new flags behaves exactly as before.

---

## In-stock tagging

Every release in the output now carries an `in_stock` boolean field. It's read
directly off the listing card yoyaku.io already sends (the `instock` /
`outofstock` CSS class WooCommerce puts on each `li.product`), so it costs **zero
extra requests**.

By default nothing is filtered — out-of-stock releases still appear in the
results, just tagged `"in_stock": false`. Pass a flag to drop them instead.

## Recent arrivals filter

yoyaku.io maintains its own "Arrivals" category at `/category/arrivals/`, using
the exact same listing markup as a style page. The `--recent` flag treats it as
one more style to intersect: your requested styles ∩ Arrivals. This narrows
results to releases that are both currently in the Arrivals bucket *and* tagged
with all your chosen styles — it does not run independently of style selection.

No per-release date lookups are involved (that would need one extra request per
result); this reuses the scraper's existing two-phase intersection engine
unchanged, just with one more source.

If the Arrivals page itself fails to load, the run doesn't silently drop the
recency filter — it prints an explicit warning (`Arrivals category unreachable —
results are not filtered by recency`) so a network hiccup isn't mistaken for
"everything shown is recent."

---

## Flags needed

### CLI (`yoyaku_scraper.py`)

| Flag | Effect |
|---|---|
| `--recent` | Intersect results with yoyaku.io's Arrivals category |
| `--in-stock-only` | Drop releases marked out of stock |
| `--concurrency N` / `-j N` | Existing flag, unrelated — max concurrent requests |

Both new flags are boolean switches (no value) and can be combined with any
number of style arguments and with each other.

```bash
python3 yoyaku_scraper.py minimal "tech house" --recent
python3 yoyaku_scraper.py techno --in-stock-only
python3 yoyaku_scraper.py minimal "tech house" --recent --in-stock-only -j 8
```

### API (`POST /scrape`)

| Field | Type | Default | Effect |
|---|---|---|---|
| `styles` | `list[str]` | — | Existing field, required |
| `concurrency` | `int` | `10` | Existing field |
| `recent` | `bool` | `false` | Same as `--recent` |
| `in_stock_only` | `bool` | `false` | Same as `--in-stock-only` |

```json
POST /scrape
{
  "styles": ["Minimal", "Tech House"],
  "recent": true,
  "in_stock_only": true
}
```

### Frontend (`frontend/index.html`)

Two checkboxes sit under the style list, next to the existing threads/execute
controls: **Recent arrivals** and **In stock only**. Both are wired straight into
the `/scrape` POST body above; neither affects the Execute button's existing
"at least one style selected" gating.

---

## Live example

```bash
python3 yoyaku_scraper.py minimal "tech house" --recent -j 8
```

```
Filtering for releases with ALL of: ['Arrivals', 'Minimal', 'Tech House']
Probing page counts—
  Minimal: 10 page(s)
  Arrivals: 37 page(s)
  Tech House: 8 page(s)
  URLs per style: {'Minimal': 486, 'Arrivals': 1795, 'Tech House': 379}
  Intersection: 141 release(s)
```

`--recent` pulled in Arrivals (1795 URLs across 37 pages) as a third set,
intersected with Minimal (486) and Tech House (379). Result: 141 releases tagged
Minimal *and* Tech House *and* currently in the Arrivals bucket, e.g.:

| Title | Styles | Price | In stock |
|---|---|---|---|
| Assumetro EP | Minimal, Tech House | 10,00 € | true |
| Inertia | Minimal, Tech House | 8,33 € | true |
| Wolkenbett | Minimal, Minimal Techno, Tech House, Techno | 9,17 € | true |
| Yoyaku Slapfunk | House, Minimal, Progressive House, Tech House | 15,03 € | true |

Adding `--in-stock-only` to the same command would drop any of the 141 with
`in_stock: false` from both the printed summary and `yoyaku_results.json`/`.csv` —
in this particular run none were out of stock, so the output would be identical.
