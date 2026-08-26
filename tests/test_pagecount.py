from simurg.metadata.pagecount import (
    count_words,
    estimate_pages_from_text,
    estimate_pages_from_words,
)


def test_count_words_strips_tags():
    assert count_words("<p>hello world</p>") == 2
    assert count_words("one two three") == 3
    # control chars / junk ignored as whitespace
    assert count_words("a\u0000b c") == 3


def test_estimate_pages_from_words():
    assert estimate_pages_from_words(0) is None
    assert estimate_pages_from_words(100) is None  # below one page threshold
    assert estimate_pages_from_words(250) == 1
    assert estimate_pages_from_words(5000) == 20


def test_estimate_pages_from_text():
    text = " ".join(f"w{i}" for i in range(5000))
    assert estimate_pages_from_text(text) == 20
    # no prose -> None
    assert estimate_pages_from_text("<p>  </p>") is None
