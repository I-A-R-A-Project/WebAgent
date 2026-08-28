#!/usr/bin/env python3
"""Run a bounded website crawl and export compact JSON results."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from website_tools.crawler import CrawlConfig, crawl
from website_tools.storage import save_crawl


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawlear enlaces HTML dentro de un dominio.")
    parser.add_argument("url")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--domain")
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--allow-url", action="append", default=[], help="Patrón URL whitelist; repetir para varios")
    parser.add_argument("--block-url", action="append", default=[], help="Patrón URL blacklist; repetir para varios")
    parser.add_argument("--allow-selector", action="append", default=[], help="Selector CSS cuyo contenido se procesa")
    parser.add_argument("--block-selector", action="append", default=[], help="Selector CSS cuyo contenido se ignora")
    parser.add_argument("--ignore-robots", action="store_true", help="No consultar ni respetar robots.txt")
    parser.add_argument("--workers", type=int, default=1, help="Solicitudes concurrentes (máximo 16 y limitado por CPU)")
    args = parser.parse_args()
    if args.max_depth < 0 or args.max_pages < 1 or args.delay < 0 or args.workers < 1:
        parser.error("Los límites deben ser positivos; max-depth puede ser cero.")
    result = crawl(CrawlConfig(
        start_url=args.url,
        allowed_domain=args.domain,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        delay=args.delay,
        allowed_url_patterns=args.allow_url,
        blocked_url_patterns=args.block_url,
        allowed_content_selectors=args.allow_selector,
        blocked_content_selectors=args.block_selector,
        respect_robots=not args.ignore_robots,
        workers=args.workers,
    ))
    save_crawl(result, args.output)
    print(f"Páginas visitadas: {result.visited}")
    print(f"Resultado: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
