"""Ejecuta un prompt contra la API de Google AI Studio."""

import argparse
import os
import sys

from gemini_api import GeminiAPIError, generate_content


def main() -> int:
    parser = argparse.ArgumentParser(description="Enviar un prompt a Gemini.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"))
    parser.add_argument("--api-key", default=os.environ.get("GEMINI_API_KEY"))
    args = parser.parse_args()
    if not args.api_key:
        print("Falta GEMINI_API_KEY. Configurá la API key en Agentes IA.", file=sys.stderr)
        return 2
    try:
        content = generate_content(args.prompt, args.api_key, args.model)
    except GeminiAPIError as exc:
        print(f"Error al consultar Gemini: {exc}", file=sys.stderr)
        return 1
    print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
