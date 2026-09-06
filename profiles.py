"""
profiles.py - Perfiles de navegador para WebAgent.

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
    QPushButton, QLabel, QCheckBox, QDialogButtonBox, QFileDialog,
    QInputDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QUrl, QTimer

from file_ops import GitVersioning
from paths import BROWSER_DATA_DIR, IA_DATA_DIR
from web_common.tabs import keep_plus_tab_last

MINIBROWSER_PROFILE_DIR = BROWSER_DATA_DIR / "profile"


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
        self.base_dir = IA_DATA_DIR
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

    def delete_profile(self, profile_id: str) -> bool:
        target = self.get_profile(profile_id)
        if target and target.get("is_default"):
            return False
        if len(self.profiles) <= 1:
            return False
        self.profiles = [profile for profile in self.profiles if profile["id"] != profile_id]
        self.save_profiles()
        return True

    def profile_ids(self) -> list[str]:
        return [profile["id"] for profile in self.profiles]

    def agent_profiles(self) -> list[dict]:
        return [profile for profile in self.profiles if not profile.get("is_default")]

    def agent_profile_ids(self) -> list[str]:
        return [profile["id"] for profile in self.agent_profiles()]

    def get_agent_profile_id(self) -> str | None:
        profile_ids = self.agent_profile_ids()
        return profile_ids[0] if profile_ids else None

    def is_agent_profile(self, profile_id: str | None) -> bool:
        profile = self.get_profile(profile_id) if profile_id else None
        return bool(profile and not profile.get("is_default"))

    def profile_names(self) -> dict[str, str]:
        return {profile["id"]: profile["name"] for profile in self.profiles}

    def set_git_versioning_for_all(self, enabled: bool):
        for profile in self.profiles:
            profile["git_versioning"] = bool(enabled)
        self.save_profiles()

    def toggle_git_versioning(self, profile_id: str) -> tuple[dict | None, bool]:
        profile = self.get_profile(profile_id)
        if not profile:
            return None, False
        enabled = not profile.get("git_versioning", False)
        profile["git_versioning"] = enabled
        self.save_profiles()
        return profile, enabled


class ProfileWindowMixin:
    """Comportamiento de UI de perfiles usado por la ventana principal de IA."""

    def _load_profiles_list(self):
        self._load_profile_selector()
        self._highlight_active_profile()

    def _load_profile_selector(self):
        self.profile_selector.blockSignals(True)
        self.profile_selector.clear()
        for profile in self.profile_manager.profiles:
            self.profile_selector.addItem(profile["name"], profile["id"])
        self._select_profile_in_sidebar(self.current_profile_id)
        self.profile_selector.blockSignals(False)

    def _select_profile_in_sidebar(self, profile_id: str):
        if not hasattr(self, "profile_selector"):
            return
        index = self.profile_selector.findData(profile_id)
        if index >= 0 and index != self.profile_selector.currentIndex():
            self.profile_selector.blockSignals(True)
            self.profile_selector.setCurrentIndex(index)
            self.profile_selector.blockSignals(False)

    def _on_profile_selector_changed(self, index: int):
        if index < 0:
            return
        profile_id = self.profile_selector.itemData(index)
        if profile_id and profile_id != self.current_profile_id:
            self._switch_current_tab_profile(profile_id)

    def _switch_current_tab_profile(self, profile_id: str):
        profile = self.profile_manager.get_profile(profile_id)
        current = self.current_webview()
        if not profile or current is None:
            return
        old_index = self.tabs.indexOf(current)
        if old_index < 0:
            return
        current_url = current.url()
        current_title = self.tabs.tabText(old_index)
        metadata = dict(self.tab_data.get(id(current), {}))
        replacement = self._add_tab(
            profile_id=profile_id,
            collection_id=metadata.get("collection_id"),
            insert_index=old_index,
        )
        replacement.setProperty("_session_title", current.property("_session_title"))
        if not current_url.isEmpty() and current_url.toString() != "about:blank":
            replacement.setUrl(current_url)
        self.tabs.setTabText(self.tabs.indexOf(replacement), current_title)
        self.tab_data.pop(id(current), None)
        current_index = self.tabs.indexOf(current)
        if current_index >= 0:
            self.tabs.removeTab(current_index)
        current.deleteLater()
        keep_plus_tab_last(self.tabs, self.plus_widget)
        self.current_profile_id = profile_id
        self._select_profile_in_sidebar(profile_id)
        self._highlight_active_profile()
        self.session_autosaver.schedule()

    def _activate_profile(self, profile_id: str, open_home: bool = False):
        data = self.profile_manager.get_profile(profile_id)
        if not data:
            return
        self.current_profile_id = profile_id
        while self.tabs.count() > 0:
            widget = self.tabs.widget(0)
            if widget is self.plus_widget:
                break
            self.tab_data.pop(id(widget), None)
            self.tabs.removeTab(0)
        self._add_tab()
        if open_home:
            self.load_url(data["home_url"])
        self.current_webview().setZoomFactor(data.get("zoom", 1.0))
        self._select_profile_in_sidebar(profile_id)
        self._highlight_active_profile()

    def _highlight_active_profile(self):
        data = self.profile_manager.get_profile(self.current_profile_id)
        if data:
            self.setWindowTitle(f"WebAgent — {data['name']}")

    def _open_ai_manager(self, profile_id: str):
        from ai_manager import AIAgentsDialog

        profile_id = self._ensure_agent_profile()
        if not profile_id:
            return
        profile = self.profile_manager.get_profile(profile_id)
        if not profile:
            return
        folder = self.profile_manager.get_files_dir()
        Path(folder).mkdir(parents=True, exist_ok=True)
        dialog = AIAgentsDialog(
            self,
            folder,
            profile_id=profile_id,
            profile_ids=self.profile_manager.agent_profile_ids(),
            profile_names=self.profile_manager.profile_names(),
            profile_changed_handler=self._on_agent_profile_changed,
            new_profile_handler=self._create_profile_dialog,
            rename_profile_handler=self._rename_profile,
            delete_profile_handler=self._delete_profile,
            folder_changed_handler=self._change_shared_folder,
            git_changed_handler=self._set_shared_git,
            auth_url_handler=self._open_copilot_auth_url,
            auth_success_handler=self._close_copilot_auth_tab,
        )
        dialog.setModal(False)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._agent_dialogs.append(dialog)
        dialog.destroyed.connect(
            lambda _object=None, item=dialog: (
                self._agent_dialogs.remove(item)
                if item in self._agent_dialogs else None
            )
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _ensure_agent_profile(self) -> str | None:
        profile_id = self.profile_manager.get_agent_profile_id()
        if profile_id:
            return profile_id

        QMessageBox.information(
            self,
            "Perfil requerido",
            "Necesitás crear un perfil separado de Default para usar agentes IA.",
        )
        created = self._create_profile_dialog()
        return created["id"] if created else None

    def _on_agent_profile_changed(self, profile_id):
        if profile_id:
            self.statusBar().showMessage(
                "Perfil de configuración cambiado; las pestañas normales usan Default.",
                4000,
            )

    def _open_copilot_auth_url(self, url: str, profile_id: str, code: str = ""):
        """Open Copilot device authorization in the requested profile."""
        if not self.profile_manager.is_agent_profile(profile_id):
            QMessageBox.warning(
                self,
                "Perfil requerido",
                "La autenticación de Copilot no puede usar el perfil Default.",
            )
            return
        webview = self._add_tab(profile_id=profile_id)
        webview._copilot_auth_code = code
        webview.loadFinished.connect(
            lambda ok, view=webview, expected=code: self._fill_copilot_code(
                view, expected, ok
            )
        )
        webview.setUrl(QUrl(url))
        self.statusBar().showMessage(
            f"Autenticación de Copilot abierta en el perfil {profile_id}. "
            "Completá el código mostrado por Copilot.",
            10000,
        )

    def _fill_copilot_code(
        self, webview, code: str, ok: bool, attempts: int = 0
    ):
        if not ok or not code:
            return
        script = f"""
            (() => {{
            const code = {json.dumps(code)};
            const input = document.querySelector(
                'input[name="user_code"], input[id*="code" i], '
                'input[autocomplete="one-time-code"], input[type="text"]'
            );
            if (!input) return false;
            const setter = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(input, code);
            input.dispatchEvent(new Event('input', {{bubbles: true}}));
            input.dispatchEvent(new Event('change', {{bubbles: true}}));
            const form = input.form;
            const button = form?.querySelector(
                'button[type="submit"], input[type="submit"]'
            ) || [...document.querySelectorAll('button')].find(item =>
                /continue|authorize|submit/i.test(item.innerText));
            if (button) button.click();
            else if (form) form.submit();
            return true;
            }})()
            """
        webview.page().runJavaScript(
            script,
            lambda filled: (
                None
                if filled or attempts >= 10
                else QTimer.singleShot(
                    500,
                    lambda: self._fill_copilot_code(
                        webview, code, True, attempts + 1
                    ),
                )
            ),
        )

    def _close_copilot_auth_tab(self, profile_id: str):
        for index in range(self.tabs.count() - 1, -1, -1):
            widget = self.tabs.widget(index)
            if getattr(widget, "_copilot_auth_code", None) is not None:
                metadata = self.tab_data.get(id(widget), {})
                if metadata.get("profile_id") == profile_id:
                    self.tabs.removeTab(index)
                    widget.deleteLater()
                    keep_plus_tab_last(self.tabs, self.plus_widget)
                    return

    def _change_shared_folder(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta común de archivos",
            self.profile_manager.get_files_dir(),
        )
        if directory:
            self.profile_manager.set_files_dir(directory)
            for qt_profile in self.web_engine_profiles.values():
                try:
                    qt_profile.setDownloadPath(directory)
                except AttributeError:
                    pass
            self._highlight_active_profile()

    def _set_shared_git(self, enabled: bool):
        if enabled and not GitVersioning.is_available():
            QMessageBox.warning(self, "Git no encontrado", "No se encontró 'git' en el sistema.")
            return
        self.profile_manager.set_git_versioning_for_all(enabled)
        if enabled:
            GitVersioning.ensure_repo(self.profile_manager.get_files_dir())
        self._highlight_active_profile()

    def _toggle_profile_git(self, profile_id: str):
        data = self.profile_manager.get_profile(profile_id)
        if not data:
            return
        new_value = not data.get("git_versioning", False)
        if new_value and not GitVersioning.is_available():
            QMessageBox.warning(self, "Git no encontrado", "No se encontró 'git' en el sistema.")
            return
        data, new_value = self.profile_manager.toggle_git_versioning(profile_id)
        if new_value:
            GitVersioning.ensure_repo(self.profile_manager.get_files_dir())
            if not GitVersioning.check_identity(self.profile_manager.get_files_dir()):
                self._set_git_warning(
                    "⚠ Git no tiene user.name/user.email configurados: los commits "
                    "no se van a guardar hasta que los configures."
                )
            else:
                self._clear_git_warning()
                self.statusBar().showMessage(
                    f"Git activado para el perfil '{data['name']}'", 5000
                )
        else:
            self.statusBar().showMessage(
                f"Git desactivado para el perfil '{data['name']}'", 5000
            )

    def _rename_profile(self, profile_id: str):
        data = self.profile_manager.get_profile(profile_id)
        if not data:
            return
        new_name, ok = QInputDialog.getText(
            self, "Cambiar nombre", "Nuevo nombre del perfil:", text=data["name"]
        )
        if ok and new_name.strip():
            self.profile_manager.rename_profile(profile_id, new_name.strip())
            self._load_profiles_list()

    def _create_profile_dialog(self):
        dialog = NewProfileDialog(self, default_dir=str(Path.home() / "Downloads"))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        name, _files_dir, git_versioning = dialog.get_values()
        if not name:
            QMessageBox.warning(self, "Aviso", "El perfil necesita un nombre")
            return None
        files_dir = self.profile_manager.get_files_dir()
        if git_versioning and not GitVersioning.is_available():
            QMessageBox.warning(
                self, "Git no encontrado",
                "No se encontró 'git' en el sistema. Se creará el perfil "
                "igual, pero sin versionado hasta que instales git.",
            )
        shared_git = any(p.get("git_versioning") for p in self.profile_manager.profiles)
        profile = self.profile_manager.create_profile(name, files_dir, shared_git)
        self._load_profiles_list()
        self._open_new_default_tab()
        if git_versioning and not GitVersioning.check_identity(files_dir):
            self._set_git_warning(
                "⚠ Git no tiene user.name/user.email configurados: los commits "
                "no se van a guardar hasta que los configures."
            )
        return profile

    def _delete_profile(self, profile_id: str):
        data = self.profile_manager.get_profile(profile_id)
        if not data:
            return
        if data.get("is_default"):
            QMessageBox.information(self, "Aviso", "El perfil Default no se puede eliminar.")
            return
        confirm = QMessageBox.question(
            self, "Eliminar perfil",
            f"¿Eliminar el perfil '{data['name']}'? Esto no borra los archivos descargados, "
            "solo la sesión/cache del navegador.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        was_active = profile_id == self.current_profile_id
        if not self.profile_manager.delete_profile(profile_id):
            QMessageBox.warning(self, "Aviso", "No se pudo eliminar el perfil")
            return
        self.web_engine_profiles.pop(profile_id, None)
        for i in reversed(range(self.tabs.count())):
            widget = self.tabs.widget(i)
            if self.tab_data.get(id(widget), {}).get("profile_id") == profile_id:
                self.tab_data.pop(id(widget), None)
                self.tabs.removeTab(i)
        self._load_profiles_list()
        if self.tabs.count() == 0:
            fallback_id = self.profile_manager.get_default_profile_id()
            self._add_tab(profile_id=fallback_id)
            data = self.profile_manager.get_profile(fallback_id)
            if data:
                self.load_url(data["home_url"])
        if was_active:
            widget = self.current_webview()
            meta = self.tab_data.get(id(widget), {})
            self.current_profile_id = meta.get(
                "profile_id", self.profile_manager.get_default_profile_id()
            )
            self._highlight_active_profile()

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
