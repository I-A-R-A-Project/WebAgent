"""
file_ops.py - Utilidades de archivo para WebAgent.

Contiene:
  - GitVersioning: versiona con git una carpeta de descargas, en vez
    de guardar los archivos repetidos con numeración (archivo(1).txt).
  - FileOps: eliminado seguro (papelera o git rm) y utilidades de archivos
"""

import shutil
import subprocess
import re
from pathlib import Path
from datetime import datetime


class GitVersioning:
    """Ayudante para versionar con git una carpeta de descargas, en vez
    de guardar los archivos repetidos con numeración (archivo(1).txt)."""

    @staticmethod
    def is_available() -> bool:
        return shutil.which("git") is not None

    @staticmethod
    def ensure_repo(directory: str):
        """Inicializa un repo git en la carpeta si todavía no existe."""
        if not GitVersioning.is_available():
            return
        git_dir = Path(directory) / ".git"
        if git_dir.exists():
            return
        try:
            subprocess.run(
                ["git", "init"], cwd=directory,
                capture_output=True, timeout=10,
            )
        except Exception:
            pass

    @staticmethod
    def commit_file(directory: str, filename: str, message: str) -> bool:
        """Agrega y commitea un archivo. Devuelve True si el commit se hizo."""
        if not GitVersioning.is_available():
            return False
        try:
            subprocess.run(
                ["git", "add", filename], cwd=directory,
                capture_output=True, timeout=10,
            )
            result = subprocess.run(
                ["git", "commit", "-m", message], cwd=directory,
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def commit_all(directory: str, message: str) -> bool:
        """Agrega y commitea TODOS los cambios de la carpeta (usado tras
        extraer un comprimido, que puede generar varios archivos nuevos)."""
        if not GitVersioning.is_available():
            return False
        try:
            subprocess.run(
                ["git", "add", "-A"], cwd=directory,
                capture_output=True, timeout=20,
            )
            result = subprocess.run(
                ["git", "commit", "-m", message], cwd=directory,
                capture_output=True, timeout=20,
            )
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def check_identity(directory: str) -> bool:
        """Chequea si git tiene user.name y user.email configurados
        (local o global) para esa carpeta. Sin esto, los commits fallan."""
        if not GitVersioning.is_available():
            return False
        try:
            name = subprocess.run(
                ["git", "config", "user.name"], cwd=directory,
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            email = subprocess.run(
                ["git", "config", "user.email"], cwd=directory,
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            return bool(name) and bool(email)
        except Exception:
            return False

    @staticmethod
    def has_repo(directory: str) -> bool:
        """Chequea si la carpeta ya es un repositorio git (tiene .git)."""
        return (Path(directory) / ".git").is_dir()

    @staticmethod
    def get_log(directory: str, limit: int = 30) -> list:
        """Devuelve los últimos commits como lista de strings
        'hash  mensaje  (fecha relativa)'. Lista vacía si no es un repo,
        no hay commits, o git no está disponible."""
        if not GitVersioning.is_available() or not GitVersioning.has_repo(directory):
            return []
        try:
            result = subprocess.run(
                ["git", "log", f"-n{limit}", "--pretty=format:%h  %s  (%cr)"],
                cwd=directory, capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return []
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            return lines
        except Exception:
            return []

    @staticmethod
    def get_current_branch(directory: str) -> str:
        """Devuelve el nombre de la rama actual, o '' si no se pudo determinar."""
        if not GitVersioning.is_available() or not GitVersioning.has_repo(directory):
            return ""
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"], cwd=directory,
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def branch_exists(directory: str, branch: str) -> bool:
        if not GitVersioning.is_available() or not GitVersioning.has_repo(directory):
            return False
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", branch],
                cwd=directory, capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def create_branch(directory: str, branch: str, from_ref: str = "HEAD") -> bool:
        """Crea 'branch' a partir de from_ref si todavía no existe."""
        if GitVersioning.branch_exists(directory, branch):
            return True
        try:
            result = subprocess.run(
                ["git", "branch", branch, from_ref], cwd=directory,
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def profile_branch_names(profile_id: str) -> tuple[str, str]:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(profile_id)).strip("-") or "default"
        return f"profile/{safe}-raw", f"profile/{safe}"

    @staticmethod
    def ensure_profile_branch(directory: str, profile_id: str, raw: bool = True) -> tuple[bool, str]:
        """Switch clean repository to profile-specific raw or clean branch."""
        if not GitVersioning.has_repo(directory):
            GitVersioning.ensure_repo(directory)
        if not GitVersioning.has_repo(directory):
            return False, ""
        if not GitVersioning.is_clean(directory):
            return False, GitVersioning.get_current_branch(directory)
        raw_branch, clean_branch = GitVersioning.profile_branch_names(profile_id)
        branch = raw_branch if raw else clean_branch
        if not GitVersioning.branch_exists(directory, branch):
            current = GitVersioning.get_current_branch(directory)
            has_commit, _, _ = GitVersioning.run(directory, ["rev-parse", "--verify", "HEAD"])
            if current and has_commit:
                if not GitVersioning.create_branch(directory, branch, current):
                    return False, current
            else:
                ok, _, _ = GitVersioning.run(directory, ["checkout", "-b", branch])
                return (ok, branch if ok else current)
        ok, _, _ = GitVersioning.run(directory, ["checkout", branch])
        return ok, branch if ok else GitVersioning.get_current_branch(directory)

    @staticmethod
    def has_remote(directory: str, name: str = "origin") -> bool:
        if not GitVersioning.is_available() or not GitVersioning.has_repo(directory):
            return False
        try:
            result = subprocess.run(
                ["git", "remote"], cwd=directory,
                capture_output=True, text=True, timeout=5,
            )
            return name in result.stdout.split()
        except Exception:
            return False

    @staticmethod
    def get_remote_url(directory: str, name: str = "origin") -> str:
        if not GitVersioning.has_remote(directory, name):
            return ""
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", name], cwd=directory,
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def is_clean(directory: str) -> bool:
        """True si no hay cambios sin commitear (working tree limpio)."""
        if not GitVersioning.is_available() or not GitVersioning.has_repo(directory):
            return True
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"], cwd=directory,
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip() == ""
        except Exception:
            return True

    @staticmethod
    def run(directory: str, args: list, timeout: int = 30) -> tuple:
        """Corre un comando git arbitrario en la carpeta. Devuelve
        (ok: bool, stdout: str, stderr: str)."""
        try:
            result = subprocess.run(
                ["git"] + args, cwd=directory,
                capture_output=True, text=True, timeout=timeout,
            )
            return result.returncode == 0, result.stdout, result.stderr
        except Exception as e:
            return False, "", str(e)


class FileOps:
    """Utilidades de archivo para las carpetas de Temas/perfiles:
    eliminado seguro (papelera o git rm) y división/unión de archivos
    grandes en partes con verificación por hash."""

    @staticmethod
    def trash_dir(folder: str) -> Path:
        trash = Path(folder) / ".papelera"
        trash.mkdir(exist_ok=True)
        return trash

    @staticmethod
    def soft_delete(folder: str, filename: str) -> bool:
        """Mueve el archivo a .papelera/ en vez de borrarlo (recuperable)."""
        src = Path(folder) / filename
        if not src.exists():
            return False
        trash = FileOps.trash_dir(folder)
        dest = trash / f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        try:
            shutil.move(str(src), str(dest))
            return True
        except Exception:
            return False

    @staticmethod
    def git_delete(folder: str, filename: str, message: str) -> bool:
        """Elimina con 'git rm' para que la baja quede versionada
        (recuperable desde el historial de git)."""
        try:
            subprocess.run(["git", "rm", "-f", filename], cwd=folder, capture_output=True, timeout=10)
            result = subprocess.run(
                ["git", "commit", "-m", message], cwd=folder, capture_output=True, timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False

