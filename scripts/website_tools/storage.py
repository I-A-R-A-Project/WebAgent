"""Persistencia compacta de trabajos y resultados web."""

import json
from dataclasses import asdict
from pathlib import Path

from .crawler import CrawlResult


def save_crawl(result: CrawlResult, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")


def load_crawl(path: str | Path) -> CrawlResult:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    from .crawler import PageResult
    return CrawlResult(
        pages=[PageResult(**page) for page in data.get("pages", [])],
        visited=int(data.get("visited", 0)),
        stopped=bool(data.get("stopped", False)),
    )
