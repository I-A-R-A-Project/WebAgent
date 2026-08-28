#!/usr/bin/env python3
"""Analyze a crawl JSON file and export a compact site report."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from website_tools.analyzer import analyze, save_report
from website_tools.storage import load_crawl


def main() -> int:
    parser = argparse.ArgumentParser(description="Analizar resultados de un crawl.")
    parser.add_argument("crawl_json", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(load_crawl(args.crawl_json))
    save_report(report, args.output)
    print(f"Páginas analizadas: {report.pages}")
    print(f"Problemas encontrados: {len(report.findings)}")
    print(f"Informe: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
