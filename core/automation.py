"""Planificador local de verificaciones y cierre Git para repositorios.

La detección ocurre en WebAgent, no en el agente IA. Así los checks
repetibles no consumen tokens y solo los errores necesitan atención humana.
"""

import json
import shutil
from pathlib import Path


def _has_any(folder: Path, names: tuple[str, ...]) -> bool:
    return any((folder / name).exists() for name in names)


def _has_extension(folder: Path, extensions: tuple[str, ...]) -> bool:
    return any(
        path.is_file() and path.suffix.lower() in extensions
        for path in folder.iterdir()
    )


def build_autorun_plan(folder: str) -> list[str]:
    """Devuelve comandos seguros, independientes y ordenados para el repo."""
    root = Path(folder)
    commands = []
    if shutil.which("git") and (root / ".git").is_dir():
        commands.append("git diff --check")

    if _has_any(root, ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")):
        if shutil.which("python"):
            commands.append("python -m compileall -q .")
        if (root / "tests").is_dir() or _has_any(root, ("pytest.ini", "tox.ini")):
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
