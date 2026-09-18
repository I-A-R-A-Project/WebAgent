"""Cliente compartido para descubrir modelos y consultar Gemini."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.6-flash"


class GeminiAPIError(OSError):
    """Error de Gemini después de agotar los modelos utilizables."""


def _read_error(exc: HTTPError) -> str:
    return exc.read().decode("utf-8", errors="replace")[:4000]


def list_generate_models(api_key: str) -> list[str]:
    """Devuelve modelos disponibles para generateContent, ordenados por utilidad."""
    url = f"{API_BASE}/models?key={quote(api_key, safe='')}"
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))

    models = []
    for model in body.get("models", []):
        methods = model.get("supportedGenerationMethods", [])
        name = str(model.get("name", ""))
        if "generateContent" not in methods or not name.startswith("models/"):
            continue
        model_id = name.removeprefix("models/")
        excluded_terms = ("-tts", "-image", "computer-use", "audio")
        if any(term in model_id.casefold() for term in excluded_terms):
            continue
        models.append(model_id)

    preferred = [
        DEFAULT_MODEL,
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-pro",
    ]
    return sorted(
        set(models),
        key=lambda name: (
            preferred.index(name) if name in preferred else len(preferred),
            name,
        ),
    )


def generate_content(prompt: str, api_key: str, model: str | None = None, timeout: int = 120) -> str:
    """Consulta Gemini y cambia de modelo si el elegido no está disponible."""
    if not api_key:
        raise GeminiAPIError("Falta GEMINI_API_KEY.")

    configured = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    try:
        available = list_generate_models(api_key)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        available = []

    candidates = []
    for candidate in [configured, *available]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        candidates = [configured]

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
    }).encode("utf-8")
    failures = []
    for candidate in candidates:
        url = (
            f"{API_BASE}/models/{quote(candidate, safe='')}:generateContent"
            f"?key={quote(api_key, safe='')}"
        )
        request = Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except HTTPError as exc:
            detail = _read_error(exc)
            failures.append(f"{candidate}: HTTP {exc.code}")
            interactions_only = (
                exc.code == 400
                and "interactions api" in detail.casefold()
            )
            if exc.code not in (404, 429, 500, 502, 503, 504) and not interactions_only:
                raise GeminiAPIError(
                    f"Gemini respondió HTTP {exc.code}: {detail}"
                ) from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            failures.append(f"{candidate}: {exc}")

    raise GeminiAPIError(
        "No hay modelos Gemini disponibles para esta solicitud. "
        + ("Intentos: " + ", ".join(failures) if failures else "")
    )
