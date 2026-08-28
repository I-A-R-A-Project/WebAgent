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
    args = parser.parse_args()
    if args.max_depth < 0 or args.max_pages < 1 or args.delay < 0:
        parser.error("Los límites deben ser positivos; max-depth puede ser cero.")
    result = crawl(CrawlConfig(
        start_url=args.url,
        allowed_domain=args.domain,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        delay=args.delay,
    ))
    save_crawl(result, args.output)
    print(f"Páginas visitadas: {result.visited}")
    print(f"Resultado: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
