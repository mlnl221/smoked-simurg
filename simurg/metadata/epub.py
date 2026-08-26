"""EPUB metadata extraction via zipfile + ebooklib fallback."""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from pathlib import Path

from simurg.metadata.pagecount import count_words, estimate_pages_from_words


def _clean(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s if s else None


def extract_epub(path: Path) -> dict:
    """Extract title, authors, publisher, year, isbn, language, description, edition, illustrators, cover_path."""
    result: dict = {
        "title": None,
        "authors": [],
        "publisher": None,
        "year": None,
        "isbn": None,
        "language": None,
        "description": None,
        "edition": None,
        "illustrators": [],
        "editors": [],
        "translators": [],
        "cover_path": None,
        "page_count": None,
    }
    try:
        with zipfile.ZipFile(str(path), "r") as z:
            # Find OPF via container.xml
            opf_path = None
            try:
                container_xml = z.read("META-INF/container.xml")
                # simple parse
                m = re.search(rb'full-path="([^"]+\.opf)"', container_xml)
                if m:
                    opf_path = m.group(1).decode()
            except KeyError:
                pass
            if not opf_path:
                # fallback: find first .opf
                for name in z.namelist():
                    if name.endswith(".opf"):
                        opf_path = name
                        break
            if not opf_path:
                return result
            opf_data = z.read(opf_path)
            # Parse OPF
            # Use ET with namespaces handling via wildcard
            # ebooklib style: look for dc: tags
            # Quick regex fallback
            text = opf_data.decode("utf-8", errors="ignore")

            def _find_tag(tag: str):
                m = re.search(
                    rf"<dc:{tag}[^>]*>(.*?)</dc:{tag}>", text, flags=re.DOTALL | re.IGNORECASE
                )
                if m:
                    # strip inner tags
                    val = re.sub(r"<[^>]+>", "", m.group(1))
                    val = val.strip()
                    # unescape xml entities
                    import html as _html

                    val = _html.unescape(val)
                    return val if val else None
                return None

            def _find_all_tag(tag: str):
                vals = []
                for m in re.finditer(
                    rf"<dc:{tag}[^>]*>(.*?)</dc:{tag}>", text, flags=re.DOTALL | re.IGNORECASE
                ):
                    val = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                    if val:
                        import html as _html

                        vals.append(_html.unescape(val))
                return vals

            title = _find_tag("title")
            if title:
                result["title"] = _clean(title)
            # Contributors with roles: dc:creator may carry opf:role (aut/ill/edt/trl)
            # or be refined by <meta refines="#id" property="role" scheme="marc:relators">.
            authors = []
            illustrators = []
            editors = []
            translators = []
            for m in re.finditer(
                r"<dc:creator([^>]*)>(.*?)</dc:creator>", text, flags=re.DOTALL | re.IGNORECASE
            ):
                attrs = m.group(1)
                raw = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if not raw:
                    continue
                import html as _html

                val = _html.unescape(raw)
                role = None
                rm = re.search(r'opf:role="([^"]+)"', attrs, flags=re.IGNORECASE)
                if rm:
                    role = rm.group(1).lower()
                idm = re.search(r'id="([^"]+)"', attrs)
                if idm:
                    ref = re.search(
                        rf'<meta[^>]+refines="#{re.escape(idm.group(1))}"[^>]+property="role"[^>]+content="([^"]+)"',
                        text,
                        flags=re.IGNORECASE,
                    )
                    if ref:
                        role = ref.group(1).lower()
                if role in ("ill", "illustrator"):
                    illustrators.append(val)
                elif role in ("edt", "editor"):
                    editors.append(val)
                elif role in ("trl", "translator"):
                    translators.append(val)
                elif role is None or role in ("aut", "author", "cre", "creator"):
                    authors.append(val)
            if authors:
                result["authors"] = authors
            if illustrators:
                result["illustrators"] = illustrators
            if editors:
                result["editors"] = editors
            if translators:
                result["translators"] = translators
            publisher = _find_tag("publisher")
            if publisher:
                result["publisher"] = _clean(publisher)
            language = _find_tag("language")
            if language:
                result["language"] = _clean(language)
            description = _find_tag("description")
            if description:
                result["description"] = _clean(description)
            # Edition from <meta property="dcterms:edition"> / <meta name="edition"> / <dc:edition>
            edition = None
            for pat in (
                r'<meta[^>]+property="dcterms:edition"[^>]+content="([^"]+)"',
                r'<meta[^>]+name="edition"[^>]+content="([^"]+)"',
                r"<dc:edition[^>]*>(.*?)</dc:edition>",
            ):
                em = re.search(pat, text, flags=re.IGNORECASE | re.DOTALL)
                if em:
                    edition = re.sub(r"<[^>]+>", "", em.group(1)).strip()
                    break
            if edition:
                result["edition"] = _clean(edition)
            date = _find_tag("date")
            if date:
                # try extract year
                ym = re.search(r"(\d{4})", date)
                if ym:
                    try:
                        result["year"] = int(ym.group(1))
                    except Exception:
                        pass
            # ISBN from identifier
            identifiers = _find_all_tag("identifier")
            isbn = None
            for ident in identifiers:
                # look for 10/13 digits with dashes
                cleaned = re.sub(r"[^0-9Xx]", "", ident)
                if len(cleaned) == 10 or len(cleaned) == 13:
                    # basic check digits
                    isbn = cleaned
                    break
                # also check if ident contains isbn: prefix
                if "isbn" in ident.lower():
                    cleaned2 = re.sub(r"[^0-9Xx]", "", ident)
                    if len(cleaned2) in (10, 13):
                        isbn = cleaned2
                        break
            if isbn:
                result["isbn"] = isbn

            # Cover extraction: find manifest item with id cover or properties cover-image
            cover_href = None
            # parse manifest items
            for m in re.finditer(r"<item[^>]+>", text, flags=re.IGNORECASE):
                item = m.group(0)
                if 'properties="cover-image"' in item or 'id="cover"' in item.lower():
                    hm = re.search(r'href="([^"]+)"', item)
                    if hm:
                        cover_href = hm.group(1)
                        break
            if not cover_href:
                # look for meta name=cover
                cm = re.search(
                    r'<meta[^>]+name="cover"[^>]+content="([^"]+)"', text, flags=re.IGNORECASE
                )
                if cm:
                    cover_id = cm.group(1)
                    # find item with id=cover_id
                    for m in re.finditer(r"<item[^>]+>", text, flags=re.IGNORECASE):
                        item = m.group(0)
                        if f'id="{cover_id}"' in item:
                            hm = re.search(r'href="([^"]+)"', item)
                            if hm:
                                cover_href = hm.group(1)
                                break
            if cover_href:
                # resolve relative to opf_path dir
                base_dir = os.path.dirname(opf_path)
                full_href = os.path.join(base_dir, cover_href) if base_dir else cover_href
                # zip may have different slash handling
                full_href = full_href.replace("\\", "/")
                # try read cover
                if full_href in z.namelist():
                    data = z.read(full_href)
                    ext = os.path.splitext(full_href)[1].lower() or ".jpg"
                    if ext not in (".jpg", ".jpeg", ".png"):
                        ext = ".jpg"
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                    tmp.write(data)
                    tmp.close()
                    result["cover_path"] = tmp.name
            # also try ebooklib fallback if available for more robust cover?
            # Ebooklib may be not installed; we already handled basics

            # Page count: EPUB is reflowable, so estimate from the file.
            # Prefer an explicit page-count signal if the OPF declares one.
            total_pages = None
            for pat in (
                r'<meta[^>]+property="dcterms:total_page_count"[^>]+content="(\d+)"',
                r'<meta[^>]+property="rendition:total_page_count"[^>]+content="(\d+)"',
                r'<meta[^>]+name="calibre:total_page_count"[^>]+content="(\d+)"',
                r'<meta[^>]+content="(\d+)"[^>]+name="calibre:total_page_count"',
                r"<dcterms:extent[^>]*>.*?(\d+)\s*pages?",
            ):
                pm = re.search(pat, text, flags=re.IGNORECASE | re.DOTALL)
                if pm:
                    try:
                        total_pages = int(pm.group(1))
                        if total_pages > 0:
                            break
                    except Exception:
                        pass
            if not total_pages:
                # Estimate from the actual content (spine documents).
                words = 0
                for name in z.namelist():
                    low = name.lower()
                    if not (
                        low.endswith(".xhtml") or low.endswith(".html") or low.endswith(".htm")
                    ):
                        continue
                    if "toc" in low or low.endswith("nav.xhtml") or low.endswith("nav.html"):
                        continue
                    try:
                        doc = z.read(name).decode("utf-8", errors="ignore")
                        words += count_words(doc)
                    except Exception:
                        pass
                total_pages = estimate_pages_from_words(words)
            if total_pages:
                result["page_count"] = total_pages
    except Exception:
        pass
    return result
