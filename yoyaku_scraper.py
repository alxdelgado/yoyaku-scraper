"""
yoyaku.io scraper — returns releases that carry ALL specified styles simultaneously.

Uses curl-cffi to impersonate Chrome's TLS fingerprint, bypassing Cloudflare
without any browser launches. Parsing is done with BeautifulSoup.

Usage:
  python3 yoyaku_scraper.py                      # default: Deep House + Techno + Tech House
  python3 yoyaku_scraper.py acid minimal         # single-word styles, space-separated
  python3 yoyaku_scraper.py acid minimal tech house  # multi-word styles joined automatically

  Style names are case-insensitive. Multi-word styles (e.g. "tech house", "deep house")
  are recognised automatically — no quotes required.

Algorithm (two-phase):
  Phase 1 — fetch all style pages in parallel, extract only release URLs.
             Compute the intersection: releases present in ALL styles.
  Phase 2 — re-fetch only the pages from the smallest style that contain
             intersecting URLs. Full card parse on those pages only.

Output: yoyaku_results.json + yoyaku_results.csv

Requirements:
  pip install curl-cffi beautifulsoup4 lxml
"""

import argparse
import asyncio
import csv
import json
import re
import sys
from dataclasses import dataclass, asdict

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

DEFAULT_STYLES = ["Deep House", "Techno", "Tech House"]
BASE_URL = "https://yoyaku.io"
CONCURRENCY = 10   # curl-cffi is lightweight — higher concurrency is safe

# All style names available on yoyaku.io (title-cased canonical forms).
KNOWN_STYLES = [
    "Acid", "Ambient", "Breaks", "Chicago", "Deep House", "Detroit",
    "Dub", "Dub Techno", "Electro", "Experimental", "House", "IDM",
    "Jungle", "Minimal", "Minimal Techno", "Nu Disco", "Progressive House",
    "Soul", "Tech House", "Techno",
]
_KNOWN_LOWER: dict[str, str] = {s.lower(): s for s in KNOWN_STYLES}


def parse_styles(tokens: list[str]) -> list[str]:
    """Greedily match CLI tokens to known multi-word style names, longest match first.

    Allows passing styles without quotes, e.g.:
        python3 yoyaku_scraper.py acid minimal tech house
    where 'tech house' is resolved to the single style 'Tech House'.
    Unrecognised tokens are title-cased and passed through as-is.
    """
    styles: list[str] = []
    i = 0
    while i < len(tokens):
        matched: str | None = None
        for length in range(len(tokens) - i, 0, -1):
            candidate = " ".join(tokens[i : i + length]).lower()
            if candidate in _KNOWN_LOWER:
                matched = _KNOWN_LOWER[candidate]
                i += length
                break
        if matched is None:
            matched = tokens[i].title()
            i += 1
        styles.append(matched)
    return styles


@dataclass
class Release:
    title: str
    url: str
    artists: str
    label: str
    sku: str
    styles: str
    format: str
    price: str


def _text(tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip() if tag else ""


def style_to_slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def _page_urls(slug: str, total: int) -> list[str]:
    return [
        f"{BASE_URL}/style/{slug}/" if n == 1
        else f"{BASE_URL}/style/{slug}/page/{n}/"
        for n in range(1, total + 1)
    ]


# ── HTTP fetch helper ────────────────────────────────────────────────────────

async def get_soup(
    session: AsyncSession,
    url: str,
    sem: asyncio.Semaphore,
) -> BeautifulSoup | None:
    """Fetch a URL and return a parsed BeautifulSoup, or None on failure."""
    async with sem:
        try:
            r = await session.get(url, timeout=30)
        except Exception as exc:
            print(f"  [error] {url}: {exc}", file=sys.stderr)
            return None

    if r.status_code != 200:
        print(f"  [skip] {url} → HTTP {r.status_code}", file=sys.stderr)
        return None

    soup = BeautifulSoup(r.text, "lxml")
    title = soup.title.string if soup.title else ""
    if "just a moment" in title.lower():
        print(f"  [CF] {url}", file=sys.stderr)
        return None

    return soup


# ── Probe ────────────────────────────────────────────────────────────────────

async def probe_style(
    session: AsyncSession,
    slug: str,
    sem: asyncio.Semaphore,
) -> int:
    soup = await get_soup(session, f"{BASE_URL}/style/{slug}/", sem)
    if soup is None:
        return 0
    page_links = soup.select(".page-numbers[href]")
    nums = [
        int(m.group(1))
        for a in page_links
        if (m := re.search(r"/page/(\d+)/", a.get("href", "")))
    ]
    return max(nums) if nums else 1


# ── Phase 1 — URL collection ─────────────────────────────────────────────────

async def fetch_urls(
    session: AsyncSession,
    url: str,
    sem: asyncio.Semaphore,
) -> set[str]:
    soup = await get_soup(session, url, sem)
    if soup is None:
        return set()
    return {
        a["href"]
        for a in soup.select("a.woocommerce-LoopProduct-link")
        if a.get("href")
    }


# ── Phase 2 — full card parse ────────────────────────────────────────────────

def _parse_card(card, keep_urls: set[str]) -> Release | None:
    link = card.select_one("a.woocommerce-LoopProduct-link")
    if not link or link.get("href") not in keep_urls:
        return None

    card_url = link["href"]
    title = _text(link)

    artists = ", ".join(
        _text(a) for a in card.select("p.product-artists a")
    )
    sku = _text(card.select_one("p.product-labels .product-sku"))
    label = ", ".join(
        _text(a) for a in card.select("p.product-labels .product-label-name a")
    )

    feat = card.select_one("p.product-features")
    styles_list: list[str] = []
    fmt = ""
    if feat:
        styles_list = [_text(a) for a in feat.select("a[href*='/style/']")]
        for a in feat.select("a[href*='/style/']"):
            a.decompose()
        fmt = re.sub(r"[|\s]+", " ", _text(feat)).strip(" |")

    price = _text(card.select_one("span.price .woocommerce-Price-amount"))

    return Release(
        title=title, url=card_url, artists=artists, label=label,
        sku=sku, styles=", ".join(styles_list), format=fmt, price=price,
    )


async def fetch_cards(
    session: AsyncSession,
    url: str,
    sem: asyncio.Semaphore,
    keep_urls: set[str],
) -> list[Release]:
    soup = await get_soup(session, url, sem)
    if soup is None:
        return []
    results = []
    for card in soup.select("li.product"):
        release = _parse_card(card, keep_urls)
        if release:
            results.append(release)
    return results


# ── Orchestration helpers ────────────────────────────────────────────────────

async def probe_all_styles(
    session: AsyncSession,
    style_slugs: dict[str, str],
    sem: asyncio.Semaphore,
) -> dict[str, int]:
    """Probe each style for its page count; return {slug: count} for reachable styles only."""
    print("\nProbing page counts…")
    results = await asyncio.gather(*[
        probe_style(session, slug, sem) for slug in style_slugs
    ])
    page_counts = dict(zip(style_slugs, results))
    for slug, label in style_slugs.items():
        print(f"  {label}: {page_counts[slug]} page(s)")
    return {slug: count for slug, count in page_counts.items() if count > 0}


async def phase1_collect_urls(
    session: AsyncSession,
    valid: dict[str, int],
    style_slugs: dict[str, str],
    sem: asyncio.Semaphore,
) -> dict[str, set[str]]:
    """Fetch all style pages in parallel and collect release URLs per slug."""
    total_pages = sum(valid.values())
    print(f"\nPhase 1 — collecting URLs ({total_pages} pages, concurrency={CONCURRENCY})…")

    async def _fetch(slug: str, url: str) -> tuple[str, set[str]]:
        return slug, await fetch_urls(session, url, sem)

    all_coros = [
        _fetch(slug, page_url)
        for slug, count in valid.items()
        for page_url in _page_urls(slug, count)
    ]

    style_url_sets: dict[str, set[str]] = {s: set() for s in valid}
    done = 0
    for fut in asyncio.as_completed(all_coros):
        slug, urls = await fut
        style_url_sets[slug].update(urls)
        done += 1
        print(f"  [{done}/{total_pages}]", end="\r")

    print(f"\n  URLs per style: { {style_slugs[s]: len(v) for s, v in style_url_sets.items()} }")
    return style_url_sets


async def phase2_parse_cards(
    session: AsyncSession,
    smallest_slug: str,
    page_count: int,
    intersection: set[str],
    sem: asyncio.Semaphore,
) -> list[Release]:
    """Fetch and parse product cards for the smallest style, filtered to the URL intersection."""
    print(f"\nPhase 2 — parsing {len(intersection)} matching cards…")
    card_tasks = [
        asyncio.create_task(fetch_cards(session, url, sem, intersection))
        for url in _page_urls(smallest_slug, page_count)
    ]

    results: list[Release] = []
    seen: set[str] = set()
    for task in asyncio.as_completed(card_tasks):
        for r in await task:
            if r.url not in seen:
                seen.add(r.url)
                results.append(r)
    return results


def write_output(results: list[Release]) -> None:
    """Print a summary to stdout and write yoyaku_results.json and yoyaku_results.csv."""
    print(f"\n{'='*60}")
    print(f"Total unique matching releases: {len(results)}")
    for r in results:
        print(f"  {r.title}  [{r.styles}]  {r.price}")

    with open("yoyaku_results.json", "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=2)
    print("\nSaved: yoyaku_results.json")

    if results:
        fields = list(asdict(results[0]).keys())
        with open("yoyaku_results.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(asdict(r) for r in results)
        print("Saved: yoyaku_results.csv")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="Scrape yoyaku.io for releases matching ALL specified styles."
    )
    parser.add_argument(
        "styles",
        nargs="*",
        default=DEFAULT_STYLES,
        help=f"Style names (ALL must match). Defaults to: {DEFAULT_STYLES}",
    )
    args = parser.parse_args()

    required_styles = set(parse_styles(args.styles))
    style_slugs = {style_to_slug(s): s for s in required_styles}

    print(f"Filtering for releases with ALL of: {sorted(required_styles)}")

    sem = asyncio.Semaphore(CONCURRENCY)

    async with AsyncSession(impersonate="chrome120") as session:
        valid = await probe_all_styles(session, style_slugs, sem)
        if not valid:
            print("No styles could be loaded.")
            return

        style_url_sets = await phase1_collect_urls(session, valid, style_slugs, sem)

        sorted_sets = sorted(style_url_sets.values(), key=len)
        intersection: set[str] = sorted_sets[0].copy()
        for s in sorted_sets[1:]:
            intersection &= s
            if not intersection:
                break

        print(f"  Intersection: {len(intersection)} release(s)")
        if not intersection:
            print("No releases match all specified styles.")
            return

        smallest_slug = min(valid, key=lambda s: valid[s])
        all_results = await phase2_parse_cards(
            session, smallest_slug, valid[smallest_slug], intersection, sem
        )

    all_results.sort(key=lambda r: r.title.lower())
    write_output(all_results)


if __name__ == "__main__":
    asyncio.run(main())
