"""Page-count estimation from ebook file content.

PDFs carry a real page count (handled in pdf.py via ``len(reader.pages)``).
EPUB/MOBI/AZW3 are reflowable and have no genuine page count, so we estimate
from the extracted text using a typical book average (``WORDS_PER_PAGE``).
"""

from __future__ import annotations

import re

# Typical adult book: ~250 words per printed page. Used only for estimates.
WORDS_PER_PAGE = 250

_TAG_RE = re.compile(r"<[^>]+>")
# Control characters (incl. null) are not whitespace per Python's \s, but for
# word counting on raw ebook text they should separate tokens.
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_WS_RE = re.compile(r"\s+")


def count_words(text: str) -> int:
    """Count whitespace-delimited words in a chunk of (markup-stripped) text."""
    if not text:
        return 0
    cleaned = _TAG_RE.sub(" ", text)
    cleaned = _CTRL_RE.sub(" ", cleaned)
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    if not cleaned:
        return 0
    return len(cleaned.split(" "))


def estimate_pages_from_text(text: str, words_per_page: int = WORDS_PER_PAGE) -> int | None:
    """Estimate page count from raw/HTML text.

    Returns ``None`` when there is essentially no prose (e.g. image-only comics),
    so the caller can fall back to a scraper value instead of guessing ``1``.
    """
    words = count_words(text)
    if words < words_per_page:
        return None
    pages = max(1, round(words / words_per_page))
    return pages


def estimate_pages_from_words(word_count: int, words_per_page: int = WORDS_PER_PAGE) -> int | None:
    if word_count < words_per_page:
        return None
    return max(1, round(word_count / words_per_page))
