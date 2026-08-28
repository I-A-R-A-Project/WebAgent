"""Scraper MVP basado en selectores CSS simples y reglas JSON."""

from dataclasses import dataclass, field, asdict
import csv
import json
import sqlite3
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
import ssl


@dataclass
class ScrapeField:
    selector: str
    attribute: str = ""


@dataclass
class ScrapeRule:
    item_selector: str
    fields: dict[str, ScrapeField]
    next_selector: str = ""
    max_pages: int = 1


@dataclass
class ScrapeResult:
    rows: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class _Element:
    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list[_Element] = []
        self.text_parts: list[str] = []

    @property
    def text(self):
        parts = list(self.text_parts)
        for child in self.children:
            parts.append(child.text)
        return " ".join(" ".join(parts).split())


class _TreeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = _Element("document", [])
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        element = _Element(tag.lower(), attrs)
        self.stack[-1].children.append(element)
        if tag.lower() not in {"meta", "link", "img", "input", "br", "hr"}:
            self.stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag.lower():
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].text_parts.append(data)


def _matches(element: _Element, selector: str) -> bool:
    selector = selector.strip()
    if not selector:
        return False
    attr_name = attr_value = ""
    if "[" in selector and selector.endswith("]"):
        selector, attribute = selector[:-1].split("[", 1)
        if "=" in attribute:
            attr_name, attr_value = attribute.split("=", 1)
            attr_value = attr_value.strip("\"'")
        else:
            attr_name = attribute
    element_id = ""
    classes: list[str] = []
    if "#" in selector:
        selector, element_id = selector.split("#", 1)
    if "." in selector:
        selector, *classes = selector.split(".")
    if selector and selector != "*" and selector.lower() != element.tag:
        return False
    if element_id and element.attrs.get("id") != element_id:
        return False
    if classes and not set(classes).issubset(set(element.attrs.get("class", "").split())):
        return False
    return not attr_name or (attr_name in element.attrs and (not attr_value or element.attrs[attr_name] == attr_value))


def _find(root: _Element, selector: str) -> list[_Element]:
    if selector.strip() == ".":
        return [root]
    matches = []
    for child in root.children:
        if _matches(child, selector):
            matches.append(child)
        matches.extend(_find(child, selector))
    return matches


def scrape_html(html: str, rule: ScrapeRule) -> list[dict[str, str]]:
    parser = _TreeParser()
    parser.feed(html)
    rows = []
    for item in _find(parser.root, rule.item_selector):
        row = {}
        for name, field in rule.fields.items():
            values = _find(item, field.selector)
            if not values:
                row[name] = ""
            elif field.attribute:
                row[name] = values[0].attrs.get(field.attribute, "")
            else:
                row[name] = values[0].text
        rows.append(row)
    return rows


def load_rule(path: str | Path) -> ScrapeRule:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    fields = {
        name: ScrapeField(**value)
        for name, value in data.get("fields", {}).items()
    }
    return ScrapeRule(
        item_selector=data["item_selector"],
        fields=fields,
        next_selector=data.get("next_selector", ""),
        max_pages=max(1, int(data.get("max_pages", 1))),
    )


def scrape_urls(
    urls: list[str],
    rule: ScrapeRule,
    *,
    timeout=15.0,
    verify_tls=True,
    max_pages: int | None = None,
) -> ScrapeResult:
    result = ScrapeResult()
    context = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()
    pending = list(urls)
    visited = set()
    page_limit = max(1, max_pages if max_pages is not None else rule.max_pages)
    while pending and len(visited) < page_limit:
        url = pending.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            request = Request(url, headers={"User-Agent": "IA-Browser-WebsiteTools/1.0"})
            with urlopen(request, timeout=timeout, context=context) as response:
                html = response.read(2_000_000).decode("utf-8", errors="replace")
            result.rows.extend(scrape_html(html, rule))
            if rule.next_selector:
                parser = _TreeParser()
                parser.feed(html)
                next_nodes = _find(parser.root, rule.next_selector)
                if next_nodes:
                    next_url = next_nodes[0].attrs.get("href", "")
                    if next_url:
                        from urllib.parse import urljoin
                        pending.append(urljoin(url, next_url))
        except OSError as exc:
            result.errors.append(f"{url}: {exc}")
    return result


def save_rows(result: ScrapeResult, path: str | Path, fmt: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        target.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    elif fmt == "jsonl":
        with target.open("w", encoding="utf-8") as handle:
            for row in result.rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    elif fmt == "csv":
        fields = sorted({key for row in result.rows for key in row})
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(result.rows)
    elif fmt == "sqlite":
        fields = sorted({key for row in result.rows for key in row})
        with sqlite3.connect(target) as connection:
            connection.execute("DROP TABLE IF EXISTS scraped_rows")
            if fields:
                columns = ", ".join(f'"{field}" TEXT' for field in fields)
                placeholders = ", ".join("?" for _ in fields)
                connection.execute(f'CREATE TABLE scraped_rows ({columns})')
                connection.executemany(
                    f'INSERT INTO scraped_rows ({", ".join(f""" "{field}" """ for field in fields)}) '
                    f"VALUES ({placeholders})",
                    [[row.get(field, "") for field in fields] for row in result.rows],
                )
            connection.commit()
    else:
        raise ValueError("Formato no soportado; usar json, jsonl, csv o sqlite")
