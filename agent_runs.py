"""Renderizador de la pestaña de historial de ejecuciones de agentes."""

import json
import re
from pathlib import Path

from paths import IA_DATA_DIR


TEMPLATE_PATH = Path(__file__).with_name("agent_runs.html")
LOGS_DIR = IA_DATA_DIR / "agent_logs"
RAW_LOGS_PATTERN = re.compile(
    r"const RAW_LOGS = .*?(?=\r?\n/\* ================= PARSER ================= \*/)",
    flags=re.DOTALL,
)


def _read_logs() -> list[dict[str, str]]:
    if not LOGS_DIR.is_dir():
        return []

    logs = []
    for path in sorted(LOGS_DIR.glob("*.log"), reverse=True):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        logs.append({"filename": path.name, "content": content})
    return logs


def render_agent_runs_page() -> str:
    """Genera la vista HTML con el contenido actual de los logs."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    raw_logs = json.dumps(_read_logs(), ensure_ascii=False)
    replacement = f"const RAW_LOGS = {raw_logs};"
    return RAW_LOGS_PATTERN.sub(lambda _match: replacement, template, count=1)
