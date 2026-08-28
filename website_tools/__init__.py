"""Herramientas locales para descubrir, analizar y extraer datos web."""

from .crawler import CrawlConfig, CrawlResult, crawl
from .analyzer import SiteReport, analyze

__all__ = ["CrawlConfig", "CrawlResult", "crawl", "SiteReport", "analyze"]
