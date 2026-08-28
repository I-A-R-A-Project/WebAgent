"""Comparación compacta entre dos resultados de crawl."""

from dataclasses import asdict
import json
from pathlib import Path

from .storage import load_crawl


def compare_crawls(before_path: str | Path, after_path: str | Path) -> dict:
    before = load_crawl(before_path)
    after = load_crawl(after_path)
    before_urls = {page.url for page in before.pages}
    after_urls = {page.url for page in after.pages}
    before_errors = {page.url for page in before.pages if page.error or (page.status or 0) >= 400}
    after_errors = {page.url for page in after.pages if page.error or (page.status or 0) >= 400}
    return {
        "before_pages": len(before.pages),
        "after_pages": len(after.pages),
        "added_urls": sorted(after_urls - before_urls),
        "removed_urls": sorted(before_urls - after_urls),
        "new_errors": sorted(after_errors - before_errors),
        "resolved_errors": sorted(before_errors - after_errors),
    }


def save_comparison(comparison: dict, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
