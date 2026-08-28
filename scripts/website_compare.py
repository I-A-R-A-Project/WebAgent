#!/usr/bin/env python3
"""Compare two saved crawl JSON files."""

import argparse
from pathlib import Path

from website_tools.compare import compare_crawls, save_comparison


def main() -> int:
    parser = argparse.ArgumentParser(description="Comparar dos resultados de crawl.")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare_crawls(args.before, args.after)
    save_comparison(result, args.output)
    print(f"URLs agregadas: {len(result['added_urls'])}")
    print(f"URLs eliminadas: {len(result['removed_urls'])}")
    print(f"Nuevos errores: {len(result['new_errors'])}")
    print(f"Comparación: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
