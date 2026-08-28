#!/usr/bin/env python3
"""Serve the internal Website Tools test site on localhost."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Servidor local de pruebas para Website Tools.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    site_dir = Path(__file__).resolve().parents[1] / "website_tools" / "test_site"
    handler = partial(SimpleHTTPRequestHandler, directory=str(site_dir))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Sitio de prueba: http://{args.host}:{args.port}/")
    print("Presioná Ctrl+C para detenerlo.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
