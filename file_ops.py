"""
file_ops.py - Utilidades de archivo para IA Browser.

Contiene:
  - GitVersioning: versiona con git una carpeta de descargas, en vez
    de guardar los archivos repetidos con numeración (archivo(1).txt).
  - FileOps: eliminado seguro (papelera o git rm) y división/unión de
    archivos grandes en partes con verificación por hash.
"""

import json
import shutil
import subprocess
import hashlib
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

    @staticmethod
    def split_file(filepath: str, chunk_size_mb: int) -> list:
        """Divide un archivo en partes de tamaño fijo + un manifest.json
        con el nombre original y el hash SHA-256 para verificar la unión."""
        src = Path(filepath)
        chunk_size = max(1, chunk_size_mb) * 1024 * 1024
        parts = []
        sha256 = hashlib.sha256()
        index = 0
        with open(src, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                sha256.update(data)
                index += 1
                part_path = Path(str(src) + f".part{index:03d}")
                with open(part_path, "wb") as out:
                    out.write(data)
                parts.append(part_path.name)

        manifest = {
            "original_name": src.name,
            "parts": parts,
            "sha256": sha256.hexdigest(),
            "created": datetime.now().isoformat(),
        }
        manifest_path = Path(str(src) + ".manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        return parts

    @staticmethod
    def join_files(manifest_path: str) -> str:
        """Reconstruye el archivo original a partir de un manifest.json,
        verificando el hash SHA-256. Devuelve la ruta resultante, o ''
        si falta alguna parte o el hash no coincide."""
        manifest_file = Path(manifest_path)
        with open(manifest_file, "r") as f:
            manifest = json.load(f)

        folder = manifest_file.parent
        output_path = folder / manifest["original_name"]
        sha256 = hashlib.sha256()
        with open(output_path, "wb") as out:
            for part_name in manifest["parts"]:
                part_path = folder / part_name
                if not part_path.exists():
                    return ""
                with open(part_path, "rb") as pf:
                    data = pf.read()
                    sha256.update(data)
                    out.write(data)

        if sha256.hexdigest() != manifest.get("sha256"):
            try:
                output_path.unlink()
            except OSError:
                pass
            return ""
        return str(output_path)
