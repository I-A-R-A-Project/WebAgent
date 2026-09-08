"""Ejecuta un prompt contra la API de Groq."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="Enviar un prompt a Groq.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
    parser.add_argument("--api-key", default=os.environ.get("GROQ_API_KEY"))
    args = parser.parse_args()
    if not args.api_key:
        print("Falta GROQ_API_KEY. Configurá la API key en Agentes IA.", file=sys.stderr)
        return 2
    payload = json.dumps({
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
    }).encode("utf-8")
    request = Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {args.api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        print(f"Groq respondió HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:4000]}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"Error al consultar Groq: {exc}", file=sys.stderr)
        return 1
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        print(f"Groq devolvió una respuesta sin contenido: {json.dumps(body)[:4000]}", file=sys.stderr)
        return 1
    print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
