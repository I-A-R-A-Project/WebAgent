"""Análisis técnico compacto sobre resultados del crawler."""

from dataclasses import dataclass, asdict
import json
from pathlib import Path

from .crawler import CrawlResult


@dataclass
class PageFinding:
    url: str
    issues: list[str]


@dataclass
class SiteReport:
    pages: int
    errors: int
    duplicate_titles: list[str]
    findings: list[PageFinding]

    def to_dict(self) -> dict:
        return asdict(self)


def analyze(result: CrawlResult) -> SiteReport:
    title_urls: dict[str, list[str]] = {}
    for page in result.pages:
        if page.title:
            title_urls.setdefault(page.title.casefold(), []).append(page.url)

    duplicates = sorted(title for title, urls in title_urls.items() if len(urls) > 1)
    crawled_urls = {page.url for page in result.pages}
    findings: list[PageFinding] = []
    for page in result.pages:
        issues = []
        if page.error:
            issues.append("request-error")
        if page.status is not None and page.status >= 400:
            issues.append("http-error")
        if not page.title:
            issues.append("missing-title")
        if not page.description:
            issues.append("missing-meta-description")
        if not page.headings:
            issues.append("missing-heading")
        if page.title and len(title_urls.get(page.title.casefold(), [])) > 1:
            issues.append("duplicate-title")
        for link in page.links:
            if link in crawled_urls and any(item.url == link and item.error for item in result.pages):
                issues.append("link-to-error-page")
                break
        if issues:
            findings.append(PageFinding(page.url, issues))

    return SiteReport(
        pages=len(result.pages),
        errors=sum(bool(page.error) or (page.status is not None and page.status >= 400) for page in result.pages),
        duplicate_titles=duplicates,
        findings=findings,
    )


def save_report(report: SiteReport, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
