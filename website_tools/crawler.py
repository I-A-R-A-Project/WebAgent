"""Crawler HTTP pequeño y controlado para la Fase 1 de Website Tools."""

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import ssl
import time


@dataclass
class CrawlConfig:
    start_url: str
    allowed_domain: str | None = None
    max_depth: int = 1
    max_pages: int = 100
    timeout: float = 15.0
    delay: float = 0.0
    user_agent: str = "IA-Browser-WebsiteTools/1.0"
    verify_tls: bool = True


@dataclass
class PageResult:
    url: str
    depth: int
    status: int | None = None
    content_type: str = ""
    title: str = ""
    description: str = ""
    headings: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class CrawlResult:
    pages: list[PageResult] = field(default_factory=list)
    visited: int = 0
    stopped: bool = False


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.description = ""
        self.headings: list[str] = []
        self.in_title = False
        self.current_heading: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag.lower() == "a" and attrs_dict.get("href"):
            self.links.append(attrs_dict["href"])
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() in {"h1", "h2", "h3"}:
            self.current_heading = []
        if tag.lower() == "meta":
            name = attrs_dict.get("name", "").lower()
            if name == "description":
                self.description = attrs_dict.get("content", "").strip()

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False
        if tag.lower() in {"h1", "h2", "h3"} and self.current_heading is not None:
            heading = " ".join("".join(self.current_heading).split())
            if heading:
                self.headings.append(heading)
            self.current_heading = None

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.current_heading is not None:
            self.current_heading.append(data)


def _normalize_url(url: str) -> str:
    clean, _ = urldefrag(url.strip())
    parts = urlsplit(clean)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _is_allowed(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    allowed = domain.lower().lstrip(".")
    return host == allowed or host.endswith("." + allowed)


def crawl(
    config: CrawlConfig,
    *,
    stop_requested: Callable[[], bool] | None = None,
    on_page: Callable[[PageResult], None] | None = None,
) -> CrawlResult:
    """Crawl HTML links while enforcing domain, depth and page limits."""
    start = _normalize_url(config.start_url)
    start_domain = config.allowed_domain or (urlsplit(start).hostname or "")
    queue: list[tuple[str, int]] = [(start, 0)]
    queued = {start}
    result = CrawlResult()
    context = ssl.create_default_context() if config.verify_tls else ssl._create_unverified_context()

    while queue and len(result.pages) < config.max_pages:
        if stop_requested and stop_requested():
            result.stopped = True
            break
        url, depth = queue.pop(0)
        page = PageResult(url=url, depth=depth)
        try:
            request = Request(url, headers={"User-Agent": config.user_agent})
            with urlopen(request, timeout=config.timeout, context=context) as response:
                page.status = response.status
                page.content_type = response.headers.get_content_type()
                raw = response.read(2_000_000)
            if page.content_type == "text/html":
                parser = _LinkParser()
                parser.feed(raw.decode("utf-8", errors="replace"))
                page.title = " ".join("".join(parser.title_parts).split())
                page.description = parser.description
                page.headings = parser.headings
                page.links = [
                    _normalize_url(urljoin(url, href))
                    for href in parser.links
                    if urlsplit(_normalize_url(urljoin(url, href))).scheme in {"http", "https"}
                ]
                if depth < config.max_depth:
                    for child in page.links:
                        if urlsplit(child).scheme in {"http", "https"} and _is_allowed(child, start_domain):
                            if child not in queued:
                                queued.add(child)
                                queue.append((child, depth + 1))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            page.error = str(exc)
        result.pages.append(page)
        result.visited = len(result.pages)
        if on_page:
            on_page(page)
        if config.delay:
            time.sleep(config.delay)
    return result
