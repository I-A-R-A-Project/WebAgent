"""
profiles.py - Perfiles de navegador para IA Browser.

Contiene:
  - ProfileManager: maneja perfiles persistentes e independientes
    (sesión/cookies/cache aislados, carpeta común de archivos, home_url y zoom).
  - NewProfileDialog: diálogo para crear / renombrar un perfil.
"""

import json
import uuid
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QLabel, QCheckBox, QDialogButtonBox, QFileDialog
)

from file_ops import GitVersioning

MINIBROWSER_PROFILE_DIR = Path.home() / '.minibrowser' / 'profile'


# ======================================================================
# Perfiles
# ======================================================================

class ProfileManager:
    """Maneja perfiles de navegador persistentes e independientes.

    Cada perfil tiene:
      - storage_path / cache_path propios (sesión, cookies, cache aislados)
      - almacenamiento del navegador y zoom propios
      - una carpeta común de archivos/descargas
    """

    def __init__(self):
        self.base_dir = Path.home() / ".ia_browser"
        self.base_dir.mkdir(exist_ok=True)
        self.profiles_dir = self.base_dir / "profiles"
        self.profiles_dir.mkdir(exist_ok=True)
        self.profiles_file = self.base_dir / "profiles.json"
        self.files_dir_file = self.base_dir / "files_dir.txt"
        self.load_profiles()

    def load_profiles(self):
        if self.profiles_file.exists():
            with open(self.profiles_file, "r") as f:
                self.profiles = json.load(f)
        else:
            self.profiles = []

        if not self.profiles:
            entry = self._build_profile_entry(
                name="Default",
                files_dir=str(self.base_dir / "downloads" / "default"),
            )
            entry["is_default"] = True
            self.profiles.append(entry)
            self.save_profiles()
        else:
            self._sync_default_with_minibrowser_profile()
            self._migrate_shared_files_dir()
            self.save_profiles()

    def get_files_dir(self) -> str:
        if self.files_dir_file.exists():
            value = self.files_dir_file.read_text(encoding="utf-8").strip()
            if value:
                return value
        if self.profiles:
            return self.profiles[0].get("files_dir", str(self.base_dir / "downloads"))
        return str(self.base_dir / "downloads")

    def set_files_dir(self, directory: str):
        directory = str(Path(directory))
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.files_dir_file.write_text(directory, encoding="utf-8")
        for profile in self.profiles:
            profile["files_dir"] = directory
        self.save_profiles()

    def _migrate_shared_files_dir(self):
        directory = self.get_files_dir()
        Path(directory).mkdir(parents=True, exist_ok=True)
        for profile in self.profiles:
            profile["files_dir"] = directory

    def _sync_default_with_minibrowser_profile(self):
        default = None
        for profile in self.profiles:
            if profile.get("is_default"):
                default = profile
                break
        if default is None and self.profiles:
            default = self.profiles[0]
            default["is_default"] = True
        if default is None:
            return
        storage_path = MINIBROWSER_PROFILE_DIR
        cache_path = MINIBROWSER_PROFILE_DIR / "cache"
        storage_path.mkdir(parents=True, exist_ok=True)
        cache_path.mkdir(parents=True, exist_ok=True)
        default["storage_path"] = str(storage_path)
        default["cache_path"] = str(cache_path)

    def save_profiles(self):
        with open(self.profiles_file, "w") as f:
            json.dump(self.profiles, f, indent=2)

    def _build_profile_entry(self, name: str, files_dir: str, git_versioning: bool = False) -> dict:
        profile_id = uuid.uuid4().hex[:12]
        storage_path = self.profiles_dir / profile_id / "storage"
        cache_path = self.profiles_dir / profile_id / "cache"
        storage_path.mkdir(parents=True, exist_ok=True)
        cache_path.mkdir(parents=True, exist_ok=True)
        Path(files_dir).mkdir(parents=True, exist_ok=True)
        if git_versioning:
            GitVersioning.ensure_repo(files_dir)

        return {
            "id": profile_id,
            "name": name,
            "storage_path": str(storage_path),
            "cache_path": str(cache_path),
            "files_dir": files_dir,
            "home_url": "https://www.google.com",
            "zoom": 1.0,
            "is_default": False,
            # Si está activo: al descargar un archivo con el mismo nombre
            # se SOBRESCRIBE (en vez de numerarlo) y se commitea con git.
            "git_versioning": git_versioning,
            "created": datetime.now().isoformat(),
        }

    def create_profile(self, name: str, files_dir: str | None = None, git_versioning: bool = False) -> dict:
        entry = self._build_profile_entry(name, files_dir or self.get_files_dir(), git_versioning)
        self.profiles.append(entry)
        self.save_profiles()
        return entry

    def get_profile(self, profile_id: str) -> dict | None:
        for p in self.profiles:
            if p["id"] == profile_id:
                return p
        return None

    def get_default_profile_id(self) -> str:
        for p in self.profiles:
            if p.get("is_default"):
                return p["id"]
        return self.profiles[0]["id"]

    def update_profile(self, profile_id: str, **kwargs):
        p = self.get_profile(profile_id)
        if p:
            p.update(kwargs)
            self.save_profiles()

    def rename_profile(self, profile_id: str, new_name: str):
        self.update_profile(profile_id, name=new_name)

    def delete_profile(self, profile_id: str):
        target = self.get_profile(profile_id)
        if target and target.get("is_default"):
            return False
        if len(self.profiles) <= 1:
            return False
        self.profiles = [p for p in self.profiles if p["id"] != profile_id]
        self.save_profiles()
        return True


class NewProfileDialog(QDialog):
    """Dialog to create or rename a browser profile."""

    def __init__(self, parent=None, default_name: str = "", default_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Perfil")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(default_name)
        self.name_edit.setPlaceholderText("Ej: Trabajo, Personal...")
        form.addRow("Nombre del perfil:", self.name_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_values(self):
        return self.name_edit.text().strip(), "", False
