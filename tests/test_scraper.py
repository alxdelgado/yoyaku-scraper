"""
Unit tests for yoyaku_scraper.py — pure and near-pure functions.

Coverage targets:
  parse_styles   — greedy multi-word CLI token matching
  style_to_slug  — URL slug generation
  _text          — whitespace normalisation from BeautifulSoup tags
  _page_urls     — per-style paginated URL list (page-1 quirk)
  _parse_card    — product card extraction and soup immutability (T1 regression)

Run with:
  pytest tests/test_scraper.py -v
"""

import os
import sys

# Add project root to path so yoyaku_scraper can be imported without installation.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from bs4 import BeautifulSoup

from yoyaku_scraper import (
    BASE_URL,
    SEL_STYLE_LINK,
    _page_urls,
    _parse_card,
    _text,
    parse_styles,
    style_to_slug,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_card(
    url: str = "https://yoyaku.io/release/test-release/",
    title: str = "Test Release",
    artists: list[str] | None = None,
    sku: str = "SKU-001",
    label: str = "Test Label",
    styles: list[str] | None = None,
    fmt: str = '12"',
    price: str = "€15.00",
) -> BeautifulSoup:
    """Return a BeautifulSoup ``li.product`` element with realistic structure.

    All fields default to valid values so individual tests only need to specify
    what they care about.
    """
    artists = artists if artists is not None else ["Artist A"]
    styles = styles if styles is not None else ["Tech House"]

    style_links = " | ".join(
        f'<a href="/style/{s.lower().replace(" ", "-")}/">{s}</a>'
        for s in styles
    )
    artist_links = ", ".join(f'<a href="#">{a}</a>' for a in artists)

    html = f"""
    <li class="product">
      <a class="woocommerce-LoopProduct-link" href="{url}">{title}</a>
      <p class="product-artists">{artist_links}</p>
      <p class="product-labels">
        <span class="product-sku">{sku}</span>
        <span class="product-label-name"><a href="#">{label}</a></span>
      </p>
      <p class="product-features">{style_links} | {fmt}</p>
      <span class="price">
        <span class="woocommerce-Price-amount">{price}</span>
      </span>
    </li>
    """
    return BeautifulSoup(html, "lxml").select_one("li.product")


# ── parse_styles ──────────────────────────────────────────────────────────────

class TestParseStyles:
    def test_single_known_style(self):
        assert parse_styles(["acid"]) == ["Acid"]

    def test_multi_word_style_joined(self):
        assert parse_styles(["tech", "house"]) == ["Tech House"]

    def test_multi_word_plus_single(self):
        assert parse_styles(["deep", "house", "techno"]) == ["Deep House", "Techno"]

    def test_unknown_token_is_title_cased_passthrough(self):
        assert parse_styles(["foobar"]) == ["Foobar"]

    def test_empty_tokens(self):
        assert parse_styles([]) == []

    def test_matching_is_case_insensitive(self):
        assert parse_styles(["ACID"]) == ["Acid"]
        assert parse_styles(["Tech", "House"]) == ["Tech House"]

    def test_greedy_match_prefers_longer_style(self):
        # "dub techno" must win over treating "dub" and "techno" separately.
        assert parse_styles(["dub", "techno"]) == ["Dub Techno"]

    def test_consecutive_multi_word_styles(self):
        assert parse_styles(["deep", "house", "tech", "house"]) == [
            "Deep House", "Tech House"
        ]

    def test_mixed_known_and_unknown_tokens(self):
        assert parse_styles(["acid", "my", "label"]) == ["Acid", "My", "Label"]


# ── style_to_slug ─────────────────────────────────────────────────────────────

class TestStyleToSlug:
    def test_multi_word_joined_with_hyphen(self):
        assert style_to_slug("Tech House") == "tech-house"

    def test_acronym_lowercased(self):
        assert style_to_slug("IDM") == "idm"

    def test_multi_word_with_special_prefix(self):
        assert style_to_slug("Nu Disco") == "nu-disco"

    def test_already_lowercase_unchanged(self):
        assert style_to_slug("acid") == "acid"

    def test_multiple_spaces_collapsed_to_single_hyphen(self):
        assert style_to_slug("Deep  House") == "deep-house"

    def test_leading_trailing_spaces_stripped(self):
        assert style_to_slug("  Techno  ") == "techno"


# ── _text ─────────────────────────────────────────────────────────────────────

class TestText:
    def _p(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml").find("p")

    def test_none_returns_empty_string(self):
        assert _text(None) == ""

    def test_plain_text(self):
        assert _text(self._p("<p>Hello World</p>")) == "Hello World"

    def test_internal_whitespace_collapsed(self):
        assert _text(self._p("<p>Hello   World</p>")) == "Hello World"

    def test_newlines_normalised(self):
        assert _text(self._p("<p>Hello\nWorld</p>")) == "Hello World"

    def test_leading_trailing_whitespace_stripped(self):
        assert _text(self._p("<p>  Hello  </p>")) == "Hello"

    def test_nested_children_concatenated(self):
        tag = BeautifulSoup("<p><a>Hello</a> <span>World</span></p>", "lxml").find("p")
        assert _text(tag) == "Hello World"


# ── _page_urls ────────────────────────────────────────────────────────────────

class TestPageUrls:
    def test_single_page_uses_bare_style_url(self):
        # yoyaku.io has no /page/1/ variant — the first page is the root style URL.
        assert _page_urls("tech-house", 1) == [f"{BASE_URL}/style/tech-house/"]

    def test_page_one_has_no_numeric_suffix(self):
        urls = _page_urls("techno", 3)
        assert urls[0] == f"{BASE_URL}/style/techno/"

    def test_subsequent_pages_carry_page_number(self):
        urls = _page_urls("techno", 3)
        assert urls[1] == f"{BASE_URL}/style/techno/page/2/"
        assert urls[2] == f"{BASE_URL}/style/techno/page/3/"

    def test_total_count_matches_argument(self):
        assert len(_page_urls("acid", 7)) == 7


# ── _parse_card ───────────────────────────────────────────────────────────────

CARD_URL = "https://yoyaku.io/release/test-release/"


class TestParseCard:
    def test_returns_none_when_url_not_in_keep_urls(self):
        card = make_card(url=CARD_URL)
        assert _parse_card(card, keep_urls=set()) is None

    def test_returns_release_when_url_in_keep_urls(self):
        card = make_card(url=CARD_URL)
        assert _parse_card(card, keep_urls={CARD_URL}) is not None

    def test_release_title_extracted(self):
        card = make_card(url=CARD_URL, title="My Release")
        r = _parse_card(card, keep_urls={CARD_URL})
        assert r.title == "My Release"

    def test_release_url_preserved(self):
        card = make_card(url=CARD_URL)
        r = _parse_card(card, keep_urls={CARD_URL})
        assert r.url == CARD_URL

    def test_single_artist(self):
        card = make_card(url=CARD_URL, artists=["DJ One"])
        r = _parse_card(card, keep_urls={CARD_URL})
        assert r.artists == "DJ One"

    def test_multiple_artists_comma_separated(self):
        card = make_card(url=CARD_URL, artists=["DJ One", "DJ Two"])
        r = _parse_card(card, keep_urls={CARD_URL})
        assert r.artists == "DJ One, DJ Two"

    def test_sku_extracted(self):
        card = make_card(url=CARD_URL, sku="SKU-999")
        r = _parse_card(card, keep_urls={CARD_URL})
        assert r.sku == "SKU-999"

    def test_label_extracted(self):
        card = make_card(url=CARD_URL, label="Best Records")
        r = _parse_card(card, keep_urls={CARD_URL})
        assert r.label == "Best Records"

    def test_single_style(self):
        card = make_card(url=CARD_URL, styles=["Techno"])
        r = _parse_card(card, keep_urls={CARD_URL})
        assert r.styles == "Techno"

    def test_multiple_styles_comma_separated(self):
        card = make_card(url=CARD_URL, styles=["Tech House", "Techno"])
        r = _parse_card(card, keep_urls={CARD_URL})
        assert r.styles == "Tech House, Techno"

    def test_format_excludes_style_names(self):
        # Style names are stripped from the features text; only the format token remains.
        card = make_card(url=CARD_URL, styles=["Tech House"], fmt='12"')
        r = _parse_card(card, keep_urls={CARD_URL})
        assert "Tech House" not in r.format

    def test_price_extracted(self):
        card = make_card(url=CARD_URL, price="€22.50")
        r = _parse_card(card, keep_urls={CARD_URL})
        assert r.price == "€22.50"

    def test_soup_not_mutated_after_parse(self):
        """Style links in the original card must survive a _parse_card call (T1 regression)."""
        card = make_card(url=CARD_URL, styles=["Tech House", "Techno"])
        links_before = len(card.select(SEL_STYLE_LINK))

        _parse_card(card, keep_urls={CARD_URL})

        links_after = len(card.select(SEL_STYLE_LINK))
        assert links_after == links_before, (
            "_parse_card mutated the soup — decompose() reached the original tree"
        )

    def test_soup_not_mutated_when_card_filtered_out(self):
        """Mutation guard holds even when the card is rejected by keep_urls."""
        card = make_card(url=CARD_URL, styles=["Tech House"])
        links_before = len(card.select(SEL_STYLE_LINK))

        _parse_card(card, keep_urls=set())

        assert len(card.select(SEL_STYLE_LINK)) == links_before
