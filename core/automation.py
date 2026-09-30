"""Planificador local de verificaciones y cierre Git para repositorios.

La detección ocurre en WebAgent, no en el agente IA. Así los checks
repetibles no consumen tokens y solo los errores necesitan atención humana.
"""

import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable


AUTORUN_ALLOWED_COMMANDS = {
    "git", "git.exe", "git.cmd", "npm", "npm.exe", "npm.cmd",
    "python", "python.exe", "py", "pytest", "pytest.exe",
    "cargo", "cargo.exe", "go", "go.exe", "dotnet", "dotnet.exe",
}


def validate_autorun_command(command: str) -> tuple[bool, str]:
    """Valida que autorun ejecute un binario de verificación permitido."""
    command = command.strip()
    if not command:
        return False, "El comando no puede estar vacío."
    if re.search(r"[;&|<>()`\r\n%]", command) or "$(" in command:
        return False, "No se permiten operadores, redirecciones ni sustitución de comandos."
    match = re.match(r"""^\s*(['"]?)([A-Za-z0-9_.-]+)\1(?:\s|$)""", command)
    if not match or match.group(2).lower() not in AUTORUN_ALLOWED_COMMANDS:
        return False, (
            "El comando debe comenzar con un verificador permitido "
            "(git, npm, python, pytest, cargo, go o dotnet)."
        )
    return True, ""


def redact_sensitive_text(text: str, secret_values: Iterable[str] = ()) -> str:
    """Oculta secretos conocidos y patrones comunes antes de persistir salida."""
    redacted = re.sub(
        r"(?i)(\b(?:authorization\s*:\s*)?bearer\s+)[A-Za-z0-9._~+/-]+=*",
        r"\1[redacted]",
        text,
    )
    redacted = re.sub(
        r"(?i)\b([A-Za-z0-9_-]*(?:token|api[_-]?key|password|secret|credential))"
        r"([\"']?\s*[:=]\s*[\"']?)([^\s,\"'}]+)",
        r"\1\2[redacted]",
        redacted,
    )
    redacted = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|"
        r"sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|"
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b",
        "[redacted]",
        redacted,
    )
    if secret_values:
        for secret in sorted(
            {value for value in secret_values if value and len(value) >= 6},
            key=len,
            reverse=True,
        ):
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _has_any(folder: Path, names: tuple[str, ...]) -> bool:
    return any((folder / name).exists() for name in names)


def _has_extension(folder: Path, extensions: tuple[str, ...]) -> bool:
    return any(
        path.is_file() and path.suffix.lower() in extensions
        for path in folder.iterdir()
    )


def build_autorun_plan(folder: str) -> list[str]:
    """Devuelve comandos seguros, independientes y ordenados para el repo."""
    root = Path(folder).resolve()
    if not root.is_dir():
        return []
    commands = []
    if shutil.which("git") and (root / ".git").exists():
        commands.append("git diff --check")

    if _has_any(root, ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")):
        if shutil.which("python"):
            commands.append("python -m compileall -q .")
        if any((root / name).is_dir() for name in ("test", "tests")) or _has_any(
            root, ("pytest.ini", "tox.ini")
        ):
            if shutil.which("pytest") or shutil.which("python"):
                commands.append("pytest -q" if shutil.which("pytest") else "python -m pytest -q")

    package = root / "package.json"
    if package.is_file() and shutil.which("npm"):
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
        if isinstance(scripts, dict) and scripts.get("test"):
            commands.append("npm test")

    if (root / "Cargo.toml").is_file() and shutil.which("cargo"):
        commands.append("cargo test --quiet")
    if (root / "go.mod").is_file() and shutil.which("go"):
        commands.append("go test ./...")
    if _has_extension(root, (".sln", ".csproj")) and shutil.which("dotnet"):
        commands.append("dotnet test --no-restore")

    return commands


def build_framework_check_plan(
    folder: str, configured_command: str | None = None
) -> list[str]:
    """Prioriza el comando configurado; en su ausencia detecta checks locales."""
    if configured_command and configured_command.strip():
        return [configured_command.strip()]
    return build_autorun_plan(folder)


@dataclass
class CheckResult:
    """Resultado serializable de una verificación local."""

    command: str
    status: str
    exit_code: int | None = None
    output: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def run_checks(
    folder: str,
    commands: Iterable[str],
    *,
    timeout_seconds: int | None = 600,
    output_limit: int = 20_000,
    environment: dict[str, str] | None = None,
    on_output: Callable[[str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> list[CheckResult]:
    """Ejecuta checks validados en secuencia y detiene el primer fallo."""
    root = Path(folder).resolve()
    if not root.is_dir():
        return [CheckResult("", "configuration_error", error="La carpeta no existe.")]
    if timeout_seconds is not None and timeout_seconds <= 0:
        return [CheckResult("", "configuration_error", error="El timeout debe ser mayor que cero.")]
    if output_limit <= 0:
        return [CheckResult("", "configuration_error", error="El límite de salida debe ser mayor que cero.")]

    command_list = list(commands)
    if not command_list:
        return [
            CheckResult(
                "",
                "configuration_error",
                error="No se detectaron checks confiables para este repositorio.",
            )
        ]
    safe_environment, secret_values = _safe_check_environment(environment)

    results: list[CheckResult] = []
    for command in command_list:
        reported_command = _redact_check_text(command, secret_values)
        valid, error = validate_autorun_command(command)
        if not valid:
            result = CheckResult(reported_command, "configuration_error", error=error)
            results.append(result)
            break
        try:
            command_tokens = shlex.split(command, posix=os.name != "nt")
        except ValueError as exc:
            results.append(
                CheckResult(
                    reported_command,
                    "configuration_error",
                    error=f"Comando de check inválido: {exc}",
                )
            )
            break
        if not command_tokens or not shutil.which(command_tokens[0].strip("\"'")):
            results.append(
                CheckResult(
                    reported_command,
                    "configuration_error",
                    error="No se encontró el verificador configurado.",
                )
            )
            break
        if cancel_event and cancel_event.is_set():
            results.append(
                CheckResult(reported_command, "cancelled", error="Ejecución cancelada.")
            )
            break

        started = time.monotonic()
        output_queue: queue.Queue[str | None] = queue.Queue(maxsize=1000)
        process: subprocess.Popen | None = None
        output_parts: deque[str] = deque()
        output_size = 0
        try:
            if os.name == "nt":
                argv = ["cmd.exe", "/d", "/c", command]
            else:
                argv = shlex.split(command)
            process = subprocess.Popen(
                argv,
                cwd=str(root),
                env=safe_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            reader = threading.Thread(
                target=_read_process_output,
                args=(process, output_queue),
                daemon=True,
            )
            reader.start()
            timed_out = False
            cancelled = False
            reader_finished = False
            while True:
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    _stop_process(process)
                if (
                    timeout_seconds is not None
                    and time.monotonic() - started > timeout_seconds
                ):
                    timed_out = True
                    _stop_process(process)
                try:
                    line = output_queue.get(timeout=0.05)
                except queue.Empty:
                    line = None
                    received = False
                else:
                    received = True
                if received and line is None:
                    reader_finished = True
                elif line is not None:
                    safe_line = _redact_check_text(line, secret_values)
                    output_size = _append_bounded_output(
                        output_parts, safe_line, output_size, output_limit
                    )
                    if on_output:
                        on_output(reported_command, safe_line)
                if reader_finished and process.poll() is not None:
                    break
            process.wait(timeout=2)
            reader.join(timeout=1)
            output = "".join(output_parts)
            if cancelled:
                results.append(
                    CheckResult(
                        reported_command,
                        "cancelled",
                        exit_code=process.returncode,
                        output=output,
                        duration_seconds=time.monotonic() - started,
                        error="Ejecución cancelada.",
                    )
                )
                break
            if timed_out:
                results.append(
                    CheckResult(
                        reported_command,
                        "timeout",
                        exit_code=process.returncode,
                        output=output,
                        duration_seconds=time.monotonic() - started,
                        timed_out=True,
                        error=f"El check superó el límite de {timeout_seconds} segundos.",
                    )
                )
                break
            exit_code = process.returncode
            result = CheckResult(
                reported_command,
                "passed" if exit_code == 0 else "failed",
                exit_code=exit_code,
                output=output,
                duration_seconds=time.monotonic() - started,
            )
            results.append(result)
            if exit_code != 0:
                break
        except (
            OSError,
            subprocess.SubprocessError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            if process is not None and process.poll() is None:
                _stop_process(process)
            results.append(
                CheckResult(
                    reported_command,
                    "process_error",
                    output="".join(output_parts),
                    duration_seconds=time.monotonic() - started,
                    error=_redact_check_text(str(exc), secret_values),
                )
            )
            break
    return results


def _read_process_output(
    process: subprocess.Popen, output_queue: queue.Queue[str | None]
) -> None:
    try:
        if process.stdout:
            for line in process.stdout:
                output_queue.put(line)
    finally:
        output_queue.put(None)


def _safe_check_environment(
    environment: dict[str, str] | None,
) -> tuple[dict[str, str], list[str]]:
    source = dict(os.environ if environment is None else environment)
    secret_markers = ("TOKEN", "KEY", "SECRET", "PASSWORD", "COOKIE", "CREDENTIAL")
    secrets = [
        value for key, value in source.items()
        if any(marker in key.upper() for marker in secret_markers)
        and len(value) >= 6
    ]
    safe = {
        key: value for key, value in source.items()
        if not any(marker in key.upper() for marker in secret_markers)
    }
    return safe, sorted(set(secrets), key=len, reverse=True)


def sensitive_environment_values() -> list[str]:
    """Devuelve valores sensibles de entorno para redactarlos de la salida IA."""
    return _safe_check_environment(None)[1]


def _redact_check_text(text: str, secrets: list[str]) -> str:
    return redact_sensitive_text(text, secrets)


def _append_bounded_output(
    parts: deque[str], text: str, current_size: int, limit: int
) -> int:
    if len(text) >= limit:
        parts.clear()
        parts.append(text[-limit:])
        return limit
    parts.append(text)
    current_size += len(text)
    while current_size > limit and parts:
        excess = current_size - limit
        first = parts[0]
        if len(first) <= excess:
            parts.popleft()
            current_size -= len(first)
        else:
            parts[0] = first[excess:]
            current_size -= excess
    return current_size


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill.exe") or shutil.which("taskkill")
        if taskkill:
            try:
                subprocess.run(
                    [taskkill, "/T", "/F", "/PID", str(process.pid)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
