#!/usr/bin/env python3
"""Extract structured rows from pages listed in a crawl JSON."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from website_tools.scraper import load_rule, save_rows, scrape_urls
from website_tools.storage import load_crawl


def main() -> int:
    parser = argparse.ArgumentParser(description="Extraer datos con una regla CSS JSON.")
    parser.add_argument("crawl_json", type=Path)
    parser.add_argument("--rule", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--insecure-tls", action="store_true")
    args = parser.parse_args()
    crawl = load_crawl(args.crawl_json)
    result = scrape_urls(
        [page.url for page in crawl.pages],
        load_rule(args.rule),
        verify_tls=not args.insecure_tls,
    )
    save_rows(result, args.output, args.format)
    print(f"Filas extraídas: {len(result.rows)}")
    print(f"Errores: {len(result.errors)}")
    print(f"Resultado: {args.output}")
    return 0 if not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
