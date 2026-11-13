"""Safe, lightweight metadata extraction for user-submitted product URLs."""

from __future__ import annotations

from html.parser import HTMLParser
from ipaddress import ip_address
import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_HTML_BYTES = 1_000_000
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
DATE_KEYS = (
    "releaseDate",
    "publicationDate",
    "datePublished",
    "startDate",
    "availabilityStarts",
)
RELEVANT_TYPES = {
    "book": "Libro",
    "product": "Producto",
    "movie": "Película",
    "tvseries": "Serie",
    "creativework": "Obra",
    "event": "Evento",
}


def clean_url(value: str) -> str:
    return value.strip().rstrip(".,;:!?)]}")


def extract_url(value: str) -> str | None:
    match = URL_RE.search(value)
    return clean_url(match.group(0)) if match else None


def is_standalone_url(value: str) -> bool:
    url = extract_url(value)
    return bool(url and clean_url(value) == url)


def validate_public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("El enlace debe empezar por http:// o https://")
    if not parts.hostname or parts.username or parts.password:
        raise ValueError("El enlace no parece válido.")
    if parts.port not in (None, 80, 443):
        raise ValueError("Por seguridad, solo se admiten enlaces web estándar.")

    host = parts.hostname.rstrip(".").lower()
    try:
        addresses = {ip_address(host)}
    except ValueError:
        try:
            addresses = {
                ip_address(info[4][0])
                for info in socket.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
            }
        except OSError as exc:
            raise ValueError("No he podido localizar el sitio del enlace.") from exc

    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Ese enlace apunta a una dirección privada y no puedo abrirlo.")


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        target = urljoin(request.full_url, newurl)
        validate_public_url(target)
        return super().redirect_request(request, fp, code, message, headers, target)


class _MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.jsonld_parts: list[str] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._in_jsonld = False
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
            value = attrs.get("content", "").strip()
            if key and value:
                self.meta[key] = value
        elif tag.lower() == "title":
            self._in_title = True
        elif tag.lower() == "script":
            self._in_script = True
            if "ld+json" in attrs.get("type", "").lower():
                self._in_jsonld = True
                self.jsonld_parts.append("")

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False
        elif tag.lower() == "script":
            self._in_jsonld = False
            self._in_script = False

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        if self._in_jsonld and self.jsonld_parts:
            self.jsonld_parts[-1] += data
        if not self._in_script:
            self.text_parts.append(data)


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _text_value(value) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _text_value(value.get("name") or value.get("givenName"))
    if isinstance(value, list):
        values = [item for item in (_text_value(entry) for entry in value) if item]
        return ", ".join(values) if values else None
    return None


def _node_types(node: dict) -> set[str]:
    value = node.get("@type", [])
    values = [value] if isinstance(value, str) else value
    return {str(item).rsplit("/", 1)[-1].lower() for item in values if item}


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _name_matches(node: dict, title: str) -> bool:
    name = _text_value(node.get("name"))
    if not name or not title:
        return False
    normalized_name = _normalized(name)
    normalized_title = _normalized(title)
    return bool(
        normalized_name
        and normalized_title
        and (
            normalized_name in normalized_title
            or normalized_title in normalized_name
        )
    )


def _fetch_html(url: str) -> str:
    validate_public_url(url)
    opener = build_opener(_SafeRedirect())
    request = Request(
        url,
        headers={
            "User-Agent": "RecuerdameBot/1.0 (+metadata preview)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with opener.open(request, timeout=10) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ("text/html", "application/xhtml+xml"):
                raise ValueError("El enlace no contiene una página HTML legible.")
            body = response.read(MAX_HTML_BYTES + 1)
            if len(body) > MAX_HTML_BYTES:
                body = body[:MAX_HTML_BYTES]
            charset = response.headers.get_content_charset() or "utf-8"
            return body.decode(charset, errors="replace")
    except HTTPError as exc:
        raise ValueError(f"La página respondió con el error HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError("No he podido acceder a esa página ahora mismo.") from exc


def extract_metadata(url: str) -> dict[str, str | None]:
    """Return best-effort title, author, type and release date from a public page."""
    html = _fetch_html(url)
    parser = _MetadataParser()
    parser.feed(html)

    structured = []
    for raw in parser.jsonld_parts:
        try:
            structured.extend(_walk_json(json.loads(raw)))
        except (json.JSONDecodeError, TypeError):
            continue

    title = (
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or next(
            (
                _text_value(node.get("name"))
                for node in structured
                if _node_types(node) & RELEVANT_TYPES.keys() and node.get("name")
            ),
            None,
        )
        or " ".join(" ".join(parser.title_parts).split())
        or None
    )

    # PlanetadeLibros puts the author and publisher into og:title. Strip the
    # publisher suffix and use the explicit title suffix as the author fallback.
    planetadelibros = (urlsplit(url).hostname or "").lower().endswith("planetadelibros.com")
    title_author = None
    if title and planetadelibros:
        title = re.sub(r"\s*\|\s*PlanetadeLibros.*$", "", title, flags=re.IGNORECASE).strip()
        match = re.match(r"^(.*?)\s+[-–—]\s+(.+)$", title)
        if match:
            title, title_author = match.group(1).strip(), match.group(2).strip()

    relevant = [node for node in structured if _node_types(node) & RELEVANT_TYPES.keys()]
    matching = [node for node in relevant if title and _name_matches(node, title)]
    candidates = matching if matching else (relevant if len(relevant) == 1 else [])
    structured_author = next(
        (
            _text_value(node.get("author"))
            for node in candidates
            if "book" in _node_types(node)
            and node.get("author")
            and _text_value(node.get("author"))
        ),
        None,
    )
    author = title_author or structured_author if planetadelibros else structured_author

    release_date = None
    for key in DATE_KEYS:
        release_date = next(
            (str(node[key]) for node in candidates if node.get(key)),
            None,
        )
        if release_date:
            break
    if not release_date:
        for key in (
            "product:release_date",
            "book:release_date",
            "books:release_date",
            "release_date",
        ):
            if parser.meta.get(key):
                release_date = parser.meta[key]
                break
    if not release_date:
        visible_text = " ".join(" ".join(parser.text_parts).split())
        match = re.search(
            r"\b(?:publicaci[oó]n|fecha\s+de\s+publicaci[oó]n|"
            r"fecha\s+de\s+lanzamiento|lanzamiento)\s*"
            r"(?:\|\s*|:\s*|-\s*)?"
            r"((?:\d{1,2}\s+(?:de\s+)?[A-Za-záéíóúñÁÉÍÓÚÑ]+\s+(?:de\s+)?\d{4})|"
            r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{4})|(?:\d{4}-\d{2}-\d{2}))",
            visible_text,
            re.IGNORECASE,
        )
        if match:
            release_date = match.group(1)

    type_priority = ("book", "movie", "tvseries", "product", "creativework", "event")
    item_type = "Libro" if planetadelibros else next(
        (
            RELEVANT_TYPES[kind]
            for kind in type_priority
            if any(kind in _node_types(node) for node in candidates)
        ),
        None,
    )

    return {
        "title": title,
        "author": author,
        "item_type": item_type,
        "release_date": release_date,
    }
