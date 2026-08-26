"""Constants for smoked-simurg."""

import re

import click

# Blacklisted chars for filenames — same as salmon constants.py:5
BLACKLISTED_CHARS = r'[:\?<>\\*\|"\/]'

# E-book allowed extensions (lowercase)
ALLOWED_EXTENSIONS = {".pdf", ".epub", ".mobi", ".azw3", ".djvu"}

# Format mapping uppercase as Simurg expects
FORMAT_MAP = {
    ".epub": "EPUB",
    ".pdf": "PDF",
    ".mobi": "MOBI",
    ".azw3": "AZW3",
    ".djvu": "DJVU",
}

# Magazine allowed extensions (PDF/CBR/CBZ/DJVU per rules.txt:127)
MAGAZINE_EXTENSIONS = {".pdf", ".cbr", ".cbz", ".djvu"}

# Magazine format mapping (rules.txt:127)
MAGAZINE_FORMAT_MAP = {
    ".pdf": "PDF",
    ".cbr": "CBR",
    ".cbz": "CBZ",
    ".djvu": "DJVU",
}

# Valid source labels per rules.txt:58-66
SOURCE_LABELS = {"Retail", "Scan", "OCR", "Convert", "Other"}

# Forbidden tags per rules.txt:53 + tracker rules
FORBIDDEN_TAGS = {"epub", "pdf", "mobi", "scan", "retail", "azw3", "djvu"}

# Edition words to strip for dupe search / canonical title
EDITION_RE = re.compile(
    r"\s*\(.*?\)|\b(illustrated|2nd edition|3rd edition|\d+(st|nd|rd|th)\s+edition|vol\.?\s*\d+|volume\s*\d+|edition)\b",
    flags=re.IGNORECASE,
)

# Regex to clean edition from canonical title more precisely
CANONICAL_STRIP_RE = re.compile(
    r"\s*[\(\[][^\)\]]*(illustrated|edition|volume|vol\.?|deluxe|annotated|revised)[^\)\]]*[\)\]]",
    flags=re.IGNORECASE,
)

# Type enum — Simurg upload form type for ebooks
UPLOAD_TYPE = "E-Book"

# Consistent output symbols (docs/ux-improvements.md §3.3)
OK_SYMBOL = click.style("✓", fg="green")
FAIL_SYMBOL = click.style("✗", fg="red")
SKIP_SYMBOL = click.style("→", fg="yellow")
INFO_SYMBOL = click.style("·", fg="cyan")
WARN_SYMBOL = click.style("!", fg="yellow", bold=True)


# Terminal URL link styling — imitates salmon's clickable links
# (salmon/uploader/spectrals.py:311 -> click.style(url, fg="blue", underline=True))
def fmt_url(url: str) -> str:
    """Render a URL as a blue, underlined, clickable terminal link (salmon style)."""
    return click.style(str(url), fg="blue", underline=True)


def fmt_urls(urls: list[str] | None, sep: str = " · ") -> str:
    """Render a list of URLs as salmon-style clickable links, joined by `sep`."""
    if not urls:
        return ""
    return sep.join(fmt_url(u) for u in urls if u)
