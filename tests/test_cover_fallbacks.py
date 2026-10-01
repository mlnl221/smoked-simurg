"""Amazon ISBN + Bing thumbnail cover fallbacks."""

from simurg.metadata.scrapers.amazon import amazon_cover_url, isbn_to_isbn10
from simurg.metadata.scrapers.bing import extract_image_urls


def test_isbn13_to_isbn10():
    assert isbn_to_isbn10("9780593439630") == "0593439635"
    assert isbn_to_isbn10("9780441172719") == "0441172717"
    assert isbn_to_isbn10("0441172717") == "0441172717"
    assert isbn_to_isbn10("978-0-441-17271-9") == "0441172717"


def test_isbn10_x_check_digit():
    # 9780306406153 -> 0306406152; X case: 978020161622X? use known: 155860832X
    assert isbn_to_isbn10("9781558608320") == "155860832X"


def test_isbn_unconvertible():
    assert isbn_to_isbn10(None) is None
    assert isbn_to_isbn10("") is None
    assert isbn_to_isbn10("9791090636074") is None  # 979 prefix: no ISBN-10
    assert isbn_to_isbn10("123") is None


def test_amazon_url_shape():
    assert (
        amazon_cover_url("9780593439630")
        == "https://images.amazon.com/images/P/0593439635.01.LZZZZZZZ.jpg"
    )
    assert amazon_cover_url(None) is None
    assert amazon_cover_url("9791090636074") is None


def test_bing_extract_orders_amazon_then_thumbs():
    page = (
        '{"murl":"https://example.com/a.jpg"}'
        '{"murl":"https://m.media-amazon.com/images/I/91Ax.jpg"}'
        '{"turl":"https://tse1.mm.bing.net/th?id=OIP.x"}'
    )
    out = extract_image_urls(page)
    assert out[0] == "https://m.media-amazon.com/images/I/91Ax.jpg"
    assert "https://tse1.mm.bing.net/th?id=OIP.x" in out
    assert "https://example.com/a.jpg" in out
    assert out.index("https://tse1.mm.bing.net/th?id=OIP.x") < out.index(
        "https://example.com/a.jpg"
    )


def test_bing_extract_dedupes():
    page = '{"murl":"https://example.com/a.jpg"}{"murl":"https://example.com/a.jpg"}'
    assert extract_image_urls(page) == ["https://example.com/a.jpg"]


def test_bing_extract_empty():
    assert extract_image_urls("<html>no results</html>") == []
