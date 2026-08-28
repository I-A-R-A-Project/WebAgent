#!/usr/bin/env python3
"""Run a single AnyAPI chat completion for an AI Manager prompt."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a prompt to AnyAPI.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default=os.environ.get("ANYAPI_MODEL", "openai/gpt-4-turbo"))
    parser.add_argument("--api-key", default=os.environ.get("ANYAPI_API_KEY"))
    parser.add_argument("--base-url", default="https://api.anyapi.ai/v1")
    args = parser.parse_args()

    if not args.api_key:
        print("Falta ANYAPI_API_KEY. Configurá la API key en Agentes IA.", file=sys.stderr)
        return 2

    payload = json.dumps({
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
    }).encode("utf-8")
    request = Request(
        f"{args.base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {args.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        print(f"AnyAPI respondió HTTP {exc.code}: {details[:4000]}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"No se pudo conectar con AnyAPI: {exc}", file=sys.stderr)
        return 1
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"Error al consultar AnyAPI: {exc}", file=sys.stderr)
        return 1

    choices = body.get("choices") or []
    if not choices:
        print(f"AnyAPI devolvió una respuesta sin choices: {json.dumps(body)[:4000]}", file=sys.stderr)
        return 1
    content = choices[0].get("message", {}).get("content")
    if not content:
        print(f"AnyAPI devolvió un mensaje sin contenido: {json.dumps(body)[:4000]}", file=sys.stderr)
        return 1
    print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
