#!/usr/bin/env python3
"""Download bounded HTML pages from a previously saved crawl."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from website_tools.downloader import DownloadConfig, download_site
from website_tools.storage import load_crawl


def main() -> int:
    parser = argparse.ArgumentParser(description="Descargar páginas de un crawl a una carpeta.")
    parser.add_argument("crawl_json", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=25)
    parser.add_argument("--max-bytes", type=int, default=50_000_000)
    parser.add_argument("--insecure-tls", action="store_true")
    parser.add_argument("--no-resources", action="store_true", help="No descargar CSS, JS, imágenes ni manifest enlazados")
    args = parser.parse_args()
    if args.max_pages < 1 or args.max_bytes < 1:
        parser.error("Los límites deben ser mayores que cero.")
    crawl = load_crawl(args.crawl_json)
    result = download_site(
        DownloadConfig(
            start_url=crawl.pages[0].url if crawl.pages else "",
            output_dir=args.output,
            max_pages=args.max_pages,
            max_bytes=args.max_bytes,
            verify_tls=not args.insecure_tls,
        ),
        [page.url for page in crawl.pages],
        include_resources=not args.no_resources,
    )
    print(f"Archivos procesados: {len(result.entries)}")
    print(f"Bytes descargados: {result.total_bytes}")
    print(f"Manifest: {args.output / 'website-manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
