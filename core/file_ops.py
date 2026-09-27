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
import os
import hashlib
import threading
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
        return (Path(directory) / ".git").exists()

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
        ok, stdout, _ = GitVersioning.run(
            directory, ["status", "--porcelain", "--untracked-files=all"], timeout=10
        )
        return ok and not stdout.strip()

    @staticmethod
    def get_head(directory: str) -> str:
        """Devuelve el hash completo de HEAD, o vacío si no existe."""
        ok, stdout, _ = GitVersioning.run(directory, ["rev-parse", "HEAD"], timeout=10)
        return stdout.strip() if ok else ""

    @staticmethod
    def get_status(directory: str) -> str:
        """Devuelve el estado corto sin ocultar errores del comando Git."""
        ok, stdout, stderr = GitVersioning.run(
            directory, ["status", "--short", "--untracked-files=all"], timeout=10
        )
        if not ok:
            raise RuntimeError(stderr.strip() or "No se pudo consultar el estado de Git.")
        return stdout

    @staticmethod
    def get_changed_files(directory: str) -> list[str]:
        ok, stdout, stderr = GitVersioning.run(
            directory,
            ["status", "--porcelain=v1", "--untracked-files=all", "-z"],
            timeout=10,
        )
        if not ok:
            raise RuntimeError(stderr.strip() or "No se pudo consultar el estado de Git.")

        entries = stdout.split("\0")
        changed: list[str] = []
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            if len(entry) < 4:
                raise RuntimeError("Git devolvió una entrada de estado inválida.")
            changed.append(entry[3:])
            if "R" in entry[:2] or "C" in entry[:2]:
                if index >= len(entries) or not entries[index]:
                    raise RuntimeError("Git devolvió una ruta de rename/copy incompleta.")
                changed.append(entries[index])
                index += 1
        return changed

    @staticmethod
    def get_worktree_fingerprint(directory: str) -> str:
        """Resume un ciclo solo si el estado no versionado sigue intacto."""
        root = Path(directory).resolve()
        ok, status, stderr = GitVersioning.run(
            str(root),
            ["status", "--porcelain=v1", "--untracked-files=all", "-z"],
            timeout=10,
        )
        if not ok:
            raise RuntimeError(stderr.strip() or "No se pudo capturar el estado de Git.")
        digest = hashlib.sha256(status.encode("utf-8", errors="surrogatepass"))
        ok, tree, stderr = GitVersioning.run(str(root), ["write-tree"], timeout=10)
        if not ok:
            raise RuntimeError(stderr.strip() or "No se pudo capturar el índice de Git.")
        digest.update(tree.strip().encode("ascii", errors="replace"))
        for relative in GitVersioning.get_changed_files(str(root)):
            candidate = root / relative
            if candidate.is_symlink():
                path = candidate.resolve()
                if path != root and root not in path.parents:
                    raise RuntimeError(f"Ruta modificada fuera del repositorio: {relative}")
                digest.update(relative.encode("utf-8", errors="surrogatepass"))
                digest.update(b"symlink:")
                digest.update(os.readlink(candidate).encode("utf-8", errors="surrogatepass"))
                continue
            path = candidate.resolve()
            if path != root and root not in path.parents:
                raise RuntimeError(f"Ruta modificada fuera del repositorio: {relative}")
            digest.update(relative.encode("utf-8", errors="surrogatepass"))
            if path.is_file():
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            else:
                digest.update(b"<missing>")
        return digest.hexdigest()

    @staticmethod
    def commit_framework_changes(
        directory: str,
        message: str,
        base_head: str | None = None,
        base_branch: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[bool, str, list[str], str]:
        """Staging acotado al repositorio y commit no vacío para el framework."""
        root = Path(directory).resolve()
        if not root.is_dir() or not GitVersioning.has_repo(str(root)):
            return False, "", [], "La carpeta no es un repositorio Git."
        if not GitVersioning.check_identity(str(root)):
            return False, "", [], "Git no tiene user.name y user.email configurados."
        if base_head and GitVersioning.get_head(str(root)) != base_head:
            return False, "", [], "HEAD cambió desde el inicio del ciclo."
        if base_branch and GitVersioning.get_current_branch(str(root)) != base_branch:
            return False, "", [], "La rama cambió desde la aprobación."
        if cancel_event and cancel_event.is_set():
            return False, "", [], "Ejecución cancelada antes de preparar el commit."
        try:
            changed = GitVersioning.get_changed_files(str(root))
        except RuntimeError as exc:
            return False, "", [], str(exc)
        if not changed:
            return False, "", [], "No hay cambios para commitear."
        for relative in changed:
            path = (root / relative).resolve()
            if path != root and root not in path.parents:
                return False, "", [], f"Ruta fuera del repositorio: {relative}"
        try:
            add = subprocess.run(
                ["git", "add", "-A", "--", *changed],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=20,
            )
            if add.returncode != 0:
                return False, "", changed, add.stderr.strip() or "No se pudo preparar el staging."
            if cancel_event and cancel_event.is_set():
                return False, "", changed, "Ejecución cancelada antes de crear el commit."
            if base_head and GitVersioning.get_head(str(root)) != base_head:
                return False, "", changed, "HEAD cambió antes de crear el commit."
            if base_branch and GitVersioning.get_current_branch(str(root)) != base_branch:
                return False, "", changed, "La rama cambió antes de crear el commit."
            staged = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if staged.returncode == 0:
                return False, "", changed, "No hay cambios preparados para commitear."
            if staged.returncode != 1:
                return False, "", changed, staged.stderr.strip() or "No se pudo validar el staging."
            commit = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=20,
            )
            if commit.returncode != 0:
                return False, "", changed, commit.stderr.strip() or "No se pudo crear el commit."
            commit_hash = GitVersioning.get_head(str(root))
            if not commit_hash:
                return False, "", changed, "Git creó el commit pero no se pudo obtener su hash."
            return True, commit_hash, changed, ""
        except (OSError, subprocess.SubprocessError) as exc:
            return False, "", changed, str(exc)

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
