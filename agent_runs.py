"""Renderizador de la pestaña de historial de ejecuciones de agentes.

La página ya NO incrusta el texto completo de cada log. Eso era lo que
crasheaba la vista con muchos logs: no importaba cuánto ajustáramos el
presupuesto de bytes, cada corrida igual se parseaba entera en el
navegador (varias regex por log: timeline, diff, agrupado de pasos) apenas
cargaba la página, todo de una sola vez y de forma síncrona. Con miles de
logs eso bloquea o crashea el proceso de render sin importar el tamaño
total en bytes.

Ahora Python manda un RESUMEN liviano por corrida (los campos que la
lista/dashboard necesitan: fecha, perfil, rama, tarea, estado, créditos,
líneas cambiadas) y el contenido completo de un log puntual recién se pide
al hacer click, a través de un puente Python↔JS (`AgentRunsBridge`, más
abajo). Ver `attach_bridge()` para cómo conectarlo.
"""

import json
import re
from pathlib import Path

from paths import IA_DATA_DIR

try:
    from PyQt6.QtCore import QObject, pyqtSlot
    from PyQt6.QtWebChannel import QWebChannel
except ImportError:  # pragma: no cover - entorno sin PyQt6 (tests, scripts)
    QObject = object

    def pyqtSlot(*_args, **_kwargs):  # type: ignore
        def _decorator(func):
            return func
        return _decorator

    QWebChannel = None


TEMPLATE_PATH = Path(__file__).with_name("agent_runs.html")
LOGS_DIR = IA_DATA_DIR / "agent_logs"

RAW_LOGS_PATTERN = re.compile(r"const RAW_LOGS = \[\];")

# Como cada entrada ahora es solo un puñado de campos cortos (no el log
# entero), este número puede ser generoso sin volver a crashear la vista.
# Es más una barrera de sanidad para historiales absurdamente grandes que
# un límite real de uso normal.
MAX_SUMMARIES = 5000

# Techo para el contenido completo que se manda al hacer click en UNA
# corrida puntual. Al ser una sola transferencia (no miles de golpe), esto
# es solo para protegerse de un caso extremo (un diff de un archivo
# gigante). El corte se hace desde el final, que es justo donde vive el
# diff completo ("=== Diff exacto de la ejecución ==="), preservando el
# encabezado, la tarea pedida y el relato del agente.
MAX_LOG_CONTENT_CHARS = 500_000
TRUNCATION_NOTICE = (
    "\n\n... (log truncado por tamaño; abrí el archivo .log directamente "
    "para verlo completo) ..."
)


# ---------------------------------------------------------------------------
# Resumen liviano (para la lista y el dashboard) — nunca carga el log entero
# ---------------------------------------------------------------------------

def _get(pattern: str, text: str, flags=re.MULTILINE):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _summarize_log(filename: str, raw: str) -> dict:
    """Extrae solo los campos que necesita la lista: nada del cuerpo
    completo, la actividad del agente ni el diff.
    """
    raw = raw.replace("\r\n", "\n")

    agent_label = _get(r"^=== (.+) ===\s*$", raw) or "Comando"
    is_command_run = agent_label == "Comando"

    started = _get(r"^started:\s*(.+)$", raw)
    profile_id = _get(r"^profile_id:\s*(.+)$", raw)
    profile_name = _get(r"^profile:\s*(.+)$", raw) or profile_id
    cwd = _get(r"^cwd:\s*(.+)$", raw)
    branch = _get(r"Contexto Git:\s*rama=(\S+)", raw) or "—"

    task_match = re.search(r"\ntask:\n(.*?)\n\n", raw, re.DOTALL)
    task_text = (
        task_match.group(1).strip()
        if task_match
        else (_get(r"^command:\s*(.+)$", raw) or "(sin descripción)")
    )
    is_git_diff_command = is_command_run and bool(
        re.match(r"^git diff\b", task_text.strip(), re.IGNORECASE)
    )

    changes_match = re.search(r"Changes\s+\+(\d+)\s+-(\d+)", raw)
    credits_match = re.search(r"AI Credits\s+([\d.]+)(?:\s*\(([^)]+)\))?", raw)

    exit_code = None
    for m in re.finditer(r"---\s*(?:.*?)\s*\(código\s*(-?\d+)\)\s*---", raw):
        exit_code = m.group(1)

    # Misma lógica que el parser JS: la detección de auth/permisos nunca
    # mira el log entero (un diff de código puede citar esas mismas
    # palabras como texto literal y disparar falsos positivos), y para
    # "Comando" el estado sale directo del código de salida del proceso.
    has_auth_error = False
    perm_denied = 0
    if not is_command_run:
        footer_idx = len(raw)
        fm = re.search(r"\n\n\nChanges\s+\+", raw)
        if fm:
            footer_idx = fm.start()
        else:
            em = re.search(r"---\s*.*\(código", raw)
            if em:
                footer_idx = em.start()
        body = raw[:footer_idx]
        narrative = "\n".join(
            line
            for line in body.split("\n")
            if not re.match(r"^(diff --git |index [0-9a-fA-F]|@@ |\+\+\+ |--- |[+-])", line)
        )
        has_auth_error = (
            bool(re.search(r"Error:\s*Authentication token", narrative, re.IGNORECASE))
            or "no autenticado" in narrative.lower()
        )
        perm_denied = len(re.findall(r"Permission denied", narrative))

    if has_auth_error:
        status = "auth"
    elif perm_denied > 0:
        status = "blocked"
    elif exit_code == "0":
        status = "success"
    elif exit_code is not None:
        status = "error"
    else:
        status = "unknown"

    return {
        "filename": filename,
        "startedStr": started,
        "agentLabel": agent_label,
        "isCommandRun": is_command_run,
        "isGitDiffCommand": is_git_diff_command,
        "profileId": profile_id,
        "profileName": profile_name,
        "cwd": cwd,
        "branch": branch,
        "taskText": task_text,
        "status": status,
        "exitCode": exit_code,
        "changes": (
            {"added": int(changes_match.group(1)), "removed": int(changes_match.group(2))}
            if changes_match
            else None
        ),
        "credits": (
            {"amount": float(credits_match.group(1)), "duration": credits_match.group(2)}
            if credits_match
            else None
        ),
    }


def _log_paths():
    if not LOGS_DIR.is_dir():
        return []
    return sorted(LOGS_DIR.glob("*.log"), reverse=True)


def render_agent_runs_page() -> str:
    """Genera la vista HTML con un resumen liviano de cada log.

    El contenido completo NO se manda acá: se pide bajo demanda con
    `AgentRunsBridge.get_log_content` cuando el usuario hace click en una
    corrida (ver `attach_bridge`).
    """
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    summaries = []
    for path in _log_paths()[:MAX_SUMMARIES]:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            summaries.append(_summarize_log(path.name, raw))
        except Exception as exc:  # un log corrupto no debe tumbar toda la vista
            summaries.append(
                {
                    "filename": path.name,
                    "error": f"No se pudo leer el resumen de este log: {exc}",
                }
            )
    raw_logs = json.dumps(summaries, ensure_ascii=True)
    replacement = f"const RAW_LOGS = {raw_logs};"
    rendered, count = RAW_LOGS_PATTERN.subn(lambda _match: replacement, template, count=1)
    if count == 0:
        # Si esto no matchea, el placeholder del template cambió de forma y
        # la página quedaría silenciosamente sin datos (como pasó antes: un
        # cambio en el HTML corrió el marcador de referencia y la
        # sustitución se comió código real sin que nadie lo notara).
        raise RuntimeError(
            "No se encontró 'const RAW_LOGS = [];' en agent_runs.html — "
            "¿se cambió el placeholder en el template?"
        )
    return rendered


# ---------------------------------------------------------------------------
# Carga bajo demanda del contenido completo de UN log (al hacer click)
# ---------------------------------------------------------------------------

def get_log_content(filename: str):
    """Devuelve el texto completo de un log puntual, o None si no existe."""
    candidate = LOGS_DIR / Path(filename).name  # nunca aceptar rutas (../..)
    if not candidate.is_file():
        return None
    try:
        content = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(content) > MAX_LOG_CONTENT_CHARS:
        content = content[:MAX_LOG_CONTENT_CHARS] + TRUNCATION_NOTICE
    return content


def delete_log_files(filenames) -> list[str]:
    """Borra logs de verdad de disco (no solo de la vista).

    A diferencia de "Ocultar" en la interfaz (que solo filtra en el
    navegador y no libera nada), esto elimina los archivos .log. Devuelve
    los nombres que efectivamente se borraron.
    """
    deleted = []
    for filename in filenames:
        candidate = LOGS_DIR / Path(filename).name
        try:
            candidate.unlink()
            deleted.append(candidate.name)
        except OSError:
            continue
    return deleted


class AgentRunsBridge(QObject):
    """Puente Python↔JS para esta pestaña.

    La página le pide el contenido completo de un log recién cuando el
    usuario hace click en él, en vez de traer todo de arranque.
    """

    @pyqtSlot(str, result=str)
    def get_log_content(self, filename: str) -> str:
        content = get_log_content(filename)
        return content if content is not None else ""

    @pyqtSlot(list, result=list)
    def delete_logs(self, filenames) -> list:
        return delete_log_files(list(filenames))


def attach_bridge(view) -> AgentRunsBridge:
    """Conecta el puente Python↔JS a un QWebEngineView ya creado.

    Llamar UNA vez, apenas se crea la vista (antes o después de cargar el
    HTML con setHtml(render_agent_runs_page()) — Qt tolera los dos
    órdenes, pero antes es más prolijo):

        view = QWebEngineView()
        agent_runs_bridge = attach_bridge(view)   # <- una sola vez
        view.setHtml(render_agent_runs_page())

    Sin esto, la lista y el dashboard funcionan igual (ya no dependen del
    contenido completo), pero al hacer click en una corrida para ver el
    detalle, la página no va a poder pedirle el log completo a Python.
    """
    if QWebChannel is None:
        raise RuntimeError(
            "PyQt6.QtWebChannel no está disponible en este entorno; "
            "instalá/activá el módulo QtWebChannel de PyQt6."
        )
    bridge = AgentRunsBridge(view)
    channel = QWebChannel(view.page())
    channel.registerObject("pyBridge", bridge)
    view.page().setWebChannel(channel)
    return bridge
