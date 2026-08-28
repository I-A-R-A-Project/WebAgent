"""Crawler HTTP pequeño y controlado para la Fase 1 de Website Tools."""

from dataclasses import dataclass, field
from fnmatch import fnmatch
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor
import os
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib import robotparser
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
    allowed_url_patterns: list[str] = field(default_factory=list)
    blocked_url_patterns: list[str] = field(default_factory=list)
    allowed_content_selectors: list[str] = field(default_factory=list)
    blocked_content_selectors: list[str] = field(default_factory=list)
    respect_robots: bool = True
    workers: int = 1


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
    def __init__(self, allowed_selectors=None, blocked_selectors=None):
        super().__init__()
        self.allowed_selectors = allowed_selectors or []
        self.blocked_selectors = blocked_selectors or []
        self.scope_stack: list[bool] = [not self.allowed_selectors]
        self.blocked_stack: list[bool] = [False]
        self.blocked_depth = 0
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.description = ""
        self.headings: list[str] = []
        self.in_title = False
        self.current_heading: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        matches_allowed = any(_matches_selector(tag, attrs_dict, selector) for selector in self.allowed_selectors)
        matches_blocked = any(_matches_selector(tag, attrs_dict, selector) for selector in self.blocked_selectors)
        self.scope_stack.append(self.scope_stack[-1] or matches_allowed)
        self.blocked_stack.append(matches_blocked)
        if matches_blocked:
            self.blocked_depth += 1
        in_scope = self.scope_stack[-1] and not self.blocked_depth
        if tag.lower() == "a" and attrs_dict.get("href"):
            if in_scope:
                self.links.append(attrs_dict["href"])
        if tag.lower() == "title":
            self.in_title = True
        if in_scope and tag.lower() in {"h1", "h2", "h3"}:
            self.current_heading = []
        if in_scope and tag.lower() == "meta":
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
        if len(self.scope_stack) > 1:
            self.scope_stack.pop()
        if self.blocked_stack.pop() and self.blocked_depth:
            self.blocked_depth -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.current_heading is not None:
            self.current_heading.append(data)


def _matches_selector(tag: str, attrs: dict[str, str], selector: str) -> bool:
    """Coincide selectores simples: tag, #id, .clase y [atributo[=valor]]."""
    selector = selector.strip()
    if not selector or any(char in selector for char in " >+~"):
        return False
    attribute = ""
    if "[" in selector and selector.endswith("]"):
        selector, attribute = selector[:-1].split("[", 1)
    element_id = ""
    classes = []
    if "#" in selector:
        selector, element_id = selector.split("#", 1)
    if "." in selector:
        selector, *classes = selector.split(".")
    if selector and selector != "*" and selector.lower() != tag.lower():
        return False
    if element_id and attrs.get("id") != element_id:
        return False
    if classes and not set(classes).issubset(set(attrs.get("class", "").split())):
        return False
    if attribute:
        name, _, value = attribute.partition("=")
        value = value.strip("\"'")
        if name not in attrs or (value and attrs[name] != value):
            return False
    return True


def _normalize_url(url: str) -> str:
    clean, _ = urldefrag(url.strip())
    parts = urlsplit(clean)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _is_allowed(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    allowed = domain.lower().lstrip(".")
    return host == allowed or host.endswith("." + allowed)


def _matches_url_patterns(url: str, allowed: list[str], blocked: list[str]) -> bool:
    if blocked and any(fnmatch(url, pattern) for pattern in blocked):
        return False
    return not allowed or any(fnmatch(url, pattern) for pattern in allowed)


def crawl(
    config: CrawlConfig,
    *,
    stop_requested: Callable[[], bool] | None = None,
    on_page: Callable[[PageResult], None] | None = None,
) -> CrawlResult:
    """Crawl HTML links while enforcing domain, depth and page limits."""
    start = _normalize_url(config.start_url)
    if not _matches_url_patterns(start, config.allowed_url_patterns, config.blocked_url_patterns):
        return CrawlResult(
            pages=[PageResult(url=start, depth=0, error="URL inicial fuera de whitelist/blacklist")],
            visited=1,
        )
    start_domain = config.allowed_domain or (urlsplit(start).hostname or "")
    queue: list[tuple[str, int]] = [(start, 0)]
    queued = {start}
    result = CrawlResult()
    context = ssl.create_default_context() if config.verify_tls else ssl._create_unverified_context()
    robots = robotparser.RobotFileParser()
    robots.set_url(urljoin(start, "/robots.txt"))
    if config.respect_robots:
        try:
            request = Request(robots.url, headers={"User-Agent": config.user_agent})
            with urlopen(request, timeout=config.timeout, context=context) as response:
                robots.parse(response.read(1_000_000).decode("utf-8", errors="replace").splitlines())
        except (OSError, URLError):
            robots = None
    else:
        robots = None

    def fetch(item):
        url, depth = item
        page = PageResult(url=url, depth=depth)
        if robots is not None and not robots.can_fetch(config.user_agent, url):
            page.error = "blocked-by-robots.txt"
            return page
        try:
            request = Request(url, headers={"User-Agent": config.user_agent})
            with urlopen(request, timeout=config.timeout, context=context) as response:
                page.status = response.status
                page.content_type = response.headers.get_content_type()
                raw = response.read(2_000_000)
            if page.content_type == "text/html":
                parser = _LinkParser(config.allowed_content_selectors, config.blocked_content_selectors)
                parser.feed(raw.decode("utf-8", errors="replace"))
                page.title = " ".join("".join(parser.title_parts).split())
                page.description = parser.description
                page.headings = parser.headings
                page.links = [_normalize_url(urljoin(url, href)) for href in parser.links]
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            page.error = str(exc)
        return page

    worker_count = max(1, min(config.workers, os.cpu_count() or 1, 16))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        while queue and len(result.pages) < config.max_pages:
            if stop_requested and stop_requested():
                result.stopped = True
                break
            batch = queue[:worker_count]
            del queue[:len(batch)]
            for page in executor.map(fetch, batch[: config.max_pages - len(result.pages)]):
                result.pages.append(page)
                result.visited = len(result.pages)
                if on_page:
                    on_page(page)
                if page.depth < config.max_depth:
                    for child in page.links:
                        if (
                            _is_allowed(child, start_domain)
                            and _matches_url_patterns(child, config.allowed_url_patterns, config.blocked_url_patterns)
                            and child not in queued
                        ):
                            queued.add(child)
                            queue.append((child, page.depth + 1))
            if config.delay:
                time.sleep(config.delay)
    return result
