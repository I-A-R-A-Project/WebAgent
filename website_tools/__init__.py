"""Herramientas locales para descubrir, analizar y extraer datos web."""

from .crawler import CrawlConfig, CrawlResult, crawl
from .analyzer import SiteReport, analyze
from .downloader import DownloadConfig, DownloadResult, download_site

__all__ = [
    "CrawlConfig", "CrawlResult", "crawl", "SiteReport", "analyze",
    "DownloadConfig", "DownloadResult", "download_site",
]
