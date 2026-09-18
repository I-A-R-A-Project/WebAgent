"""Ejecuta un prompt contra la API compatible con OpenAI de OpenRouter."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openrouter/auto"


def main() -> int:
    parser = argparse.ArgumentParser(description="Enviar un prompt a OpenRouter.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),
        help="ID del modelo de OpenRouter (por defecto: openrouter/auto).",
    )
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"))
    parser.add_argument(
        "--site-url",
        default=os.environ.get("OPENROUTER_SITE_URL", ""),
        help="URL opcional de la aplicación para los rankings de OpenRouter.",
    )
    parser.add_argument(
        "--app-name",
        default=os.environ.get("OPENROUTER_APP_NAME", "WebAgent"),
        help="Nombre opcional de la aplicación.",
    )
    args = parser.parse_args()

    if not args.api_key:
        print(
            "Falta OPENROUTER_API_KEY. Configurá la API key en Agentes IA.",
            file=sys.stderr,
        )
        return 2

    payload = json.dumps({
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
    }).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": args.site_url,
        "X-Title": args.app_name,
    }
    request = Request(API_URL, data=payload, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:4000]
        print(f"OpenRouter respondió HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"Error al consultar OpenRouter: {exc}", file=sys.stderr)
        return 1

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        print(
            f"OpenRouter devolvió una respuesta sin contenido: {json.dumps(body)[:4000]}",
            file=sys.stderr,
        )
        return 1

    if not isinstance(content, str):
        print(
            "OpenRouter devolvió contenido en un formato no compatible.",
            file=sys.stderr,
        )
        return 1
    print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
