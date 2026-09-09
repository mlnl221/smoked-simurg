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

# Tracker tags field limit: upload.php rejects tags strings over 200 chars
# ("You must enter at least one tag. Maximum length is 200 characters.").
TAGS_MAX_LENGTH = 200

# Genre/theme lexicon for description-derived tag suggestions.
# Key = final tracker tag form (lowercase, dots). Values = lowercase trigger
# phrases matched with word boundaries against description + title + subjects.
# Triggers avoid bare generic words ("love", "fiction", "classic") so short or
# synthesized descriptions do not produce spurious tags.
GENRE_LEXICON: dict[str, list[str]] = {
    "fantasy": [
        "fantasy",
        "dragon",
        "dragons",
        "wizard",
        "witch",
        "witches",
        "elf",
        "elves",
        "dwarf",
        "sorcery",
        "enchanted",
        "mythical",
        "quest",
    ],
    "science.fiction": [
        "science fiction",
        "sci-fi",
        "scifi",
        "space opera",
        "spaceship",
        "extraterrestrial",
        "time travel",
        "cyberpunk",
    ],
    "dystopia": ["dystopia", "dystopian", "totalitarian", "post-apocalyptic", "post apocalyptic"],
    "mystery": ["mystery", "mysteries", "detective", "whodunit", "sleuth", "cozy mystery"],
    "thriller": [
        "thriller",
        "suspense",
        "espionage",
        "assassin",
        "conspiracy",
        "page-turner",
        "page turner",
    ],
    "crime": [
        "murder",
        "serial killer",
        "heist",
        "gangster",
        "mafia",
        "police procedural",
        "crime novel",
    ],
    "true.crime": ["true crime"],
    "horror": [
        "horror",
        "haunted",
        "ghost story",
        "vampire",
        "zombie",
        "supernatural",
        "terrifying",
        "gore",
    ],
    "romance": [
        "romance novel",
        "romantic suspense",
        "love story",
        "second chance",
        "enemies to lovers",
        "wedding",
    ],
    "historical.fiction": [
        "historical fiction",
        "historical novel",
        "regency",
        "victorian",
        "tudor",
        "medieval",
    ],
    "literary.fiction": [
        "literary fiction",
        "coming of age",
        "coming-of-age",
        "family saga",
        "book club",
    ],
    "adventure": ["adventure", "expedition", "treasure hunt", "lost world"],
    "young.adult": ["young adult", "ya novel", "high school", "teenager"],
    "children": ["picture book", "bedtime", "fairy tale", "middle grade", "for young readers"],
    "biography": ["biography", "autobiography", "life story"],
    "memoir": ["memoir"],
    "history": [
        "history of",
        "historian",
        "historical account",
        "world war i",
        "world war ii",
        "ancient rome",
        "ancient greece",
    ],
    "philosophy": [
        "philosophy",
        "philosopher",
        "stoic",
        "stoicism",
        "existential",
        "ethics",
        "metaphysics",
    ],
    "psychology": ["psychology", "psychologist", "cognitive", "behavioral", "trauma", "therapy"],
    "self.help": [
        "self-help",
        "self help",
        "personal growth",
        "productivity",
        "atomic habits",
        "mindfulness",
        "motivation",
    ],
    "business": ["business", "entrepreneur", "startup", "management", "leadership", "marketing"],
    "economics": ["economics", "economist", "finance", "investing", "capitalism", "market economy"],
    "science": [
        "scientist",
        "physics",
        "quantum",
        "biology",
        "evolution",
        "genetics",
        "astronomy",
        "chemistry",
    ],
    "technology": [
        "artificial intelligence",
        "machine learning",
        "programming",
        "software",
        "computer science",
        "internet",
    ],
    "politics": ["politics", "political", "democracy", "election", "president", "parliament"],
    "law": ["lawyer", "courtroom", "trial", "legal thriller", "law school"],
    "religion": [
        "bible",
        "biblical",
        "theology",
        "christian",
        "islam",
        "quran",
        "buddhist",
        "spirituality",
        "church history",
    ],
    "war": ["battlefield", "soldier", "military history", "navy seals", "army ranger"],
    "music": ["musician", "rock band", "jazz", "composer", "guitar", "album"],
    "art": ["painter", "painting", "photography", "sculpture", "art history", "graphic design"],
    "sports": ["football", "soccer", "baseball", "basketball", "olympics", "athlete", "coaching"],
    "cooking": ["cookbook", "recipe", "chef", "baking", "cuisine", "restaurant"],
    "travel": ["travel guide", "travelogue", "backpacking", "road trip"],
    "nature": [
        "wildlife",
        "wilderness",
        "national park",
        "birding",
        "gardening",
        "climate change",
        "environment",
    ],
    "health": ["fitness", "nutrition", "wellness", "diet plan", "medical", "disease", "doctor"],
    "parenting": [
        "parenting",
        "parenthood",
        "motherhood",
        "fatherhood",
        "raising kids",
        "pregnancy",
    ],
    "education": ["teacher", "classroom", "curriculum", "pedagogy", "academic", "homeschool"],
    "poetry": ["poetry", "poet", "poems", "verse"],
    "drama": ["playwright", "theatre", "theater production", "stage play"],
    "comedy": ["comedy", "comedic", "humor", "humorous", "satire", "funny memoir"],
    "survival": ["survival", "survival story", "stranded", "plane crash", "shipwreck"],
}

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

# Max files processed per `up` run. Large batches (4000+ files) stall startup
# (group detection reads every file), so process N then rerun same command.
# Overridable via `up --limit N` (0 = unlimited).
BATCH_LIMIT_DEFAULT = 50

# Per-request HTTP timeout (seconds) for all metadata scrapers.
SCRAPER_TIMEOUT = 6

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
