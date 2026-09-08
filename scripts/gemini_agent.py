"""Ejecuta un prompt contra la API de Google AI Studio."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="Enviar un prompt a Gemini.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"))
    parser.add_argument("--api-key", default=os.environ.get("GEMINI_API_KEY"))
    args = parser.parse_args()
    if not args.api_key:
        print("Falta GEMINI_API_KEY. Configurá la API key en Agentes IA.", file=sys.stderr)
        return 2
    payload = json.dumps({
        "contents": [{"parts": [{"text": args.prompt}]}],
    }).encode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(args.model, safe='')}:generateContent?key={quote(args.api_key, safe='')}"
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        print(f"Gemini respondió HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:4000]}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"Error al consultar Gemini: {exc}", file=sys.stderr)
        return 1
    try:
        content = body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        print(f"Gemini devolvió una respuesta sin contenido: {json.dumps(body)[:4000]}", file=sys.stderr)
        return 1
    print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
