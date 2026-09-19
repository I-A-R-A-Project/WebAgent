#!/usr/bin/env python3
"""Genera una tabla con los modelos y precios publicados por OpenRouter."""

import argparse
import csv
import io
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://openrouter.ai/api/v1/models"
PRICE_FIELDS = ("prompt", "completion", "request", "image", "web_search")
TABLE_FIELDS = (
    "id",
    "name",
    "prompt_per_million",
    "completion_per_million",
    "request",
    "image",
    "web_search",
)


def fetch_models(
    *, output_modalities: str = "text", timeout: int = 30
) -> list[dict[str, Any]]:
    """Consulta el catálogo público de OpenRouter y valida su forma básica."""
    query = {"output_modalities": output_modalities}
    request = Request(
        f"{API_URL}?{urlencode(query)}",
        headers={"Accept": "application/json", "User-Agent": "WebAgent/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"OpenRouter respondió HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"No se pudo consultar OpenRouter: {exc}") from exc

    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise RuntimeError("La respuesta de OpenRouter no contiene una lista 'data'.")
    return [model for model in models if isinstance(model, dict)]


def price_per_million(value: Any) -> str:
    """Convierte USD por token a USD por millón de tokens."""
    if value in (None, ""):
        return "-"
    try:
        amount = Decimal(str(value)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError):
        return str(value)
    if amount < 0:
        return "-"
    return f"{amount:.6f}".rstrip("0").rstrip(".")


def model_row(model: dict[str, Any]) -> dict[str, str]:
    pricing = model.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
    row = {
        "id": str(model.get("id") or ""),
        "name": str(model.get("name") or model.get("id") or ""),
    }
    for field in PRICE_FIELDS:
        key = f"{field}_per_million" if field in ("prompt", "completion") else field
        row[key] = price_per_million(pricing.get(field))
    return row


def select_rows(
    models: list[dict[str, Any]],
    query: str = "",
    sort_by: str = "id",
    free_only: bool = False,
) -> list[dict[str, str]]:
    rows = [model_row(model) for model in models]
    if query:
        needle = query.casefold()
        rows = [row for row in rows if needle in f"{row['id']} {row['name']}".casefold()]
    if free_only:
        rows = [
            row
            for row in rows
            if row["prompt_per_million"] == "0"
            and row["completion_per_million"] == "0"
        ]
    if sort_by == "prompt":
        rows.sort(key=lambda row: Decimal(row["prompt_per_million"]) if row["prompt_per_million"] != "-" else Decimal("Infinity"))
    elif sort_by == "completion":
        rows.sort(key=lambda row: Decimal(row["completion_per_million"]) if row["completion_per_million"] != "-" else Decimal("Infinity"))
    else:
        rows.sort(key=lambda row: row[sort_by].casefold())
    return rows


def render_table(rows: list[dict[str, str]]) -> str:
    headers = {
        "id": "ID",
        "name": "Nombre",
        "prompt_per_million": "Entrada USD/M",
        "completion_per_million": "Salida USD/M",
        "request": "Request USD",
        "image": "Imagen USD",
        "web_search": "Web search USD",
    }
    values = [[row[field] for field in TABLE_FIELDS] for row in rows]
    widths = [
        max([len(headers[field])] + [len(value[index]) for value in values])
        for index, field in enumerate(TABLE_FIELDS)
    ]
    separator = "-+-".join("-" * width for width in widths)
    lines = [
        " | ".join(headers[field].ljust(widths[index]) for index, field in enumerate(TABLE_FIELDS)),
        separator,
    ]
    lines.extend(
        " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in values
    )
    return "\n".join(lines)


def render_csv(rows: list[dict[str, str]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=TABLE_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mostrar modelos y precios del catálogo público de OpenRouter.",
        epilog=(
            "Ejemplos:\n"
            "  python scripts/openrouter_models.py --sort prompt --limit 25\n"
            "  python scripts/openrouter_models.py --free\n"
            "  python scripts/openrouter_models.py --query gemini --format csv --output modelos.csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-modalities",
        default="text",
        help="Modalidades a incluir: text, image, audio, embeddings o all.",
    )
    parser.add_argument("--query", help="Filtrar por ID o nombre.")
    parser.add_argument(
        "--free",
        action="store_true",
        help="Mostrar solo modelos con entrada y salida gratuitas.",
    )
    parser.add_argument(
        "--sort",
        choices=("id", "name", "prompt", "completion"),
        default="id",
        help="Orden de la tabla.",
    )
    parser.add_argument("--limit", type=int, help="Máximo de filas.")
    parser.add_argument(
        "--format", choices=("table", "csv", "json"), default="table"
    )
    parser.add_argument("--output", type=Path, help="Archivo de salida (por defecto, stdout).")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit debe ser mayor que cero.")
    try:
        rows = select_rows(
            fetch_models(output_modalities=args.output_modalities, timeout=args.timeout),
            query=args.query or "",
            sort_by=args.sort,
            free_only=args.free,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.limit:
        rows = rows[: args.limit]

    if args.format == "table":
        content = render_table(rows)
    elif args.format == "csv":
        content = render_csv(rows).rstrip("\r\n")
    else:
        content = json.dumps(rows, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n", encoding="utf-8")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
