"""
collections_manager.py - "Colecciones" para WebAgent: grupos de
marcadores multi-perfil.

Contiene:
  - CollectionManager: maneja Colecciones (grupos de marcadores/urls que
    pueden pertenecer a distintos perfiles), cada una con su propia
    carpeta de descarga opcional.
  - NewCollectionDialog: crear una Colección nueva.
  - SaveToCollectionDialog: guardar la página actual en una Colección.

La vista de una Colección (sus marcadores + acceso a su carpeta) se
muestra como un árbol desplegable directamente en el sidebar de
main.py — ya no como una ventana modal aparte.
"""

import json
import uuid
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QLabel, QCheckBox, QDialogButtonBox, QFileDialog,
    QComboBox, QMessageBox, QInputDialog, QMenu, QTreeWidgetItem
)
from PyQt6.QtCore import Qt, QUrl

from file_ops import GitVersioning
from paths import IA_DATA_DIR
from profiles import ProfileManager


# ======================================================================
# Colecciones (grupos de marcadores multi-perfil)
# ======================================================================

class CollectionManager:
    """Maneja "Colecciones": grupos de marcadores que pueden pertenecer a distintos
    perfiles, cada Colección con su propia carpeta de descarga opcional."""

    def __init__(self):
        self.base_dir = IA_DATA_DIR
        self.base_dir.mkdir(exist_ok=True)
        self.collections_file = self.base_dir / "collections.json"
        self.load_collections()

    def load_collections(self):
        if self.collections_file.exists():
            with open(self.collections_file, "r") as f:
                self.collections = json.load(f)
        else:
            self.collections = []

    def save_collections(self):
        with open(self.collections_file, "w") as f:
            json.dump(self.collections, f, indent=2)

    def create_collection(self, name: str, download_dir: str = "", git_versioning: bool = False) -> dict:
        if download_dir and git_versioning:
            GitVersioning.ensure_repo(download_dir)
        entry = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "download_dir": download_dir,
            "git_versioning": git_versioning,
            "items": [],
            "created": datetime.now().isoformat(),
        }
        self.collections.append(entry)
        self.save_collections()
        return entry

    def set_git_versioning(self, collection_id: str, enabled: bool):
        t = self.get_collection(collection_id)
        if t:
            t["git_versioning"] = enabled
            if enabled and t.get("download_dir"):
                GitVersioning.ensure_repo(t["download_dir"])
            self.save_collections()

    def get_collection(self, collection_id: str) -> dict | None:
        for t in self.collections:
            if t["id"] == collection_id:
                return t
        return None

    def find_collection_for_url(self, url: str) -> dict | None:
        for collection in self.collections:
            if any(item.get("url") == url for item in collection.get("items", [])):
                return collection
        return None

    def collection_item_count(self, collection_id: str) -> int:
        collection = self.get_collection(collection_id)
        return len(collection.get("items", [])) if collection else 0

    def contains_url(self, url: str) -> bool:
        return self.find_collection_for_url(url) is not None

    def sidebar_entries(self, profile_names: dict[str, str]) -> list[dict]:
        """Devuelve los datos necesarios para renderizar el árbol lateral."""
        entries = []
        for collection in self.collections:
            items = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "profile_id": item.get("profile_id"),
                    "profile_name": profile_names.get(item.get("profile_id"), "?"),
                }
                for item in collection.get("items", [])
            ]
            entries.append({
                "id": collection["id"],
                "name": collection["name"],
                "items": items,
                "download_dir": collection.get("download_dir", ""),
            })
        return entries

    def resolve_target_folder(
        self, tab_meta: dict, profile_manager, current_profile_id: str
    ) -> tuple[str, str]:
        """Resuelve la carpeta de descarga de una pestaña."""
        collection_id = tab_meta.get("collection_id")
        if collection_id:
            collection = self.get_collection(collection_id)
            if collection and collection.get("download_dir"):
                return (
                    collection["download_dir"],
                    f"Colección '{collection['name']}'",
                )

        profile_id = tab_meta.get("profile_id", current_profile_id)
        if profile_manager.get_profile(profile_id):
            return (
                profile_manager.get_files_dir(),
                "carpeta común de archivos",
            )
        return str(Path.home()), "carpeta personal"


class CollectionWindowMixin:
    """Comportamiento de UI de colecciones usado por WebAgent."""

    def _load_collections_list(self):
        expanded_ids = {
            self.collections_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)["id"]
            for i in range(self.collections_tree.topLevelItemCount())
            if self.collections_tree.topLevelItem(i).isExpanded()
        }
        self.collections_tree.clear()
        for collection in self.collection_manager.sidebar_entries(
            self.profile_manager.profile_names()
        ):
            top = QTreeWidgetItem([
                f"🗂 {collection['name']} ({len(collection['items'])})"
            ])
            top.setData(0, Qt.ItemDataRole.UserRole, {
                "type": "collection", "id": collection["id"]
            })
            for item in collection["items"]:
                child = QTreeWidgetItem([
                    f"⭐ {item['title']}  —  [{item['profile_name']}]"
                ])
                child.setData(0, Qt.ItemDataRole.UserRole, {
                    "type": "bookmark",
                    "collection_id": collection["id"],
                    "url": item["url"],
                    "profile_id": item["profile_id"],
                })
                top.addChild(child)
            download_dir = collection.get("download_dir")
            folder_label = (
                f"📁 Carpeta: {Path(download_dir).name}"
                if download_dir else "📁 Asignar carpeta de descarga..."
            )
            folder_child = QTreeWidgetItem([folder_label])
            folder_child.setData(0, Qt.ItemDataRole.UserRole, {
                "type": "folder", "collection_id": collection["id"]
            })
            font = folder_child.font(0)
            font.setItalic(True)
            folder_child.setFont(0, font)
            top.addChild(folder_child)
            self.collections_tree.addTopLevelItem(top)
            top.setExpanded(collection["id"] in expanded_ids)

    def _create_collection_dialog(self):
        dialog = NewCollectionDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, download_dir, git_versioning = dialog.get_values()
        if not name:
            QMessageBox.warning(self, "Aviso", "La Colección necesita un nombre")
            return
        if git_versioning and not GitVersioning.is_available():
            QMessageBox.warning(
                self, "Git no encontrado",
                "No se encontró 'git' en el sistema. Se creará la Colección "
                "igual, pero sin versionado hasta que instales git.",
            )
        self.collection_manager.create_collection(name, download_dir, git_versioning)
        self._load_collections_list()
        if git_versioning and download_dir and not GitVersioning.check_identity(download_dir):
            self._set_git_warning(
                "⚠ Git no tiene user.name/user.email configurados: los commits "
                "no se van a guardar hasta que los configures."
            )

    def _save_current_to_collection(self):
        webview = self.current_webview()
        current_url = webview.url().toString() if webview else ""
        if not webview or not current_url or current_url == "about:blank":
            QMessageBox.warning(self, "Aviso", "No hay página cargada")
            return
        title = self.tabs.tabText(self.tabs.currentIndex())
        data = self.tab_data.get(id(webview), {})
        profile_id = data.get("profile_id", self.current_profile_id)
        dialog = SaveToCollectionDialog(
            self, self.collection_manager.collections,
            current_url=current_url,
            collection_manager=self.collection_manager,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        collection_id, new_name = dialog.get_values()
        if collection_id is None:
            if not new_name:
                QMessageBox.warning(self, "Aviso", "La Colección necesita un nombre")
                return
            collection_id = self.collection_manager.create_collection(new_name)["id"]
        self.collection_manager.add_item(collection_id, current_url, title, profile_id)
        self._load_collections_list()
        self._refresh_collection_icon(current_url)
        self.statusBar().showMessage("Página agregada a la Colección", 4000)

    def _refresh_collection_icon(self, url: str):
        self.collection_action.setText(
            "★" if self.collection_manager.contains_url(url) else "☆"
        )

    def _on_collection_tree_item_clicked(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind = data.get("type")
        if kind == "collection":
            item.setExpanded(not item.isExpanded())
        elif kind == "bookmark":
            existing_index = self._find_tab_index(data["url"], data["profile_id"])
            if existing_index is not None:
                self.tabs.setCurrentIndex(existing_index)
                return
            webview = self._add_tab(
                profile_id=data["profile_id"],
                collection_id=data["collection_id"],
            )
            webview.setUrl(QUrl(data["url"]))
        elif kind == "folder":
            self._open_collection_folder(data["collection_id"])

    def _find_tab_index(self, url: str, profile_id: str) -> int | None:
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            metadata = self.tab_data.get(id(widget), {})
            if metadata.get("profile_id") == profile_id and widget.url().toString() == url:
                return index
        return None

    def _open_collection_folder(self, collection_id: str):
        collection = self.collection_manager.get_collection(collection_id)
        if not collection:
            return
        download_dir = collection.get("download_dir")
        if not download_dir:
            download_dir = QFileDialog.getExistingDirectory(
                self, "Carpeta de descarga de la Colección", str(Path.home())
            )
            if not download_dir:
                return
            self.collection_manager.set_download_dir(collection_id, download_dir)
            self._load_collections_list()
        Path(download_dir).mkdir(parents=True, exist_ok=True)
        webview = self._add_tab(collection_id=collection_id)
        webview.setUrl(QUrl.fromLocalFile(download_dir))

    def _on_collection_context_menu(self, pos):
        item = self.collections_tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind = data.get("type")
        if kind == "collection":
            collection_id = data["id"]
            collection = self.collection_manager.get_collection(collection_id)
            git_on = bool(collection and collection.get("git_versioning"))
            menu = QMenu(self)
            rename = menu.addAction("Cambiar nombre...")
            folder = menu.addAction("Carpeta de descarga...")
            git = menu.addAction(
                "✅ Git: sobrescribir (activado)"
                if git_on else "☐ Git: sobrescribir (desactivado)"
            )
            delete = menu.addAction("Eliminar Colección")
            action = menu.exec(self.collections_tree.mapToGlobal(pos))
            if action == rename:
                self._rename_collection(collection_id)
            elif action == folder:
                self._pick_collection_folder(collection_id)
            elif action == git:
                self._toggle_collection_git(collection_id)
            elif action == delete:
                self._delete_collection(collection_id)
        elif kind == "bookmark":
            menu = QMenu(self)
            remove = menu.addAction("Quitar de la Colección")
            if menu.exec(self.collections_tree.mapToGlobal(pos)) == remove:
                self.collection_manager.remove_item(
                    data["collection_id"], data["url"]
                )
                self._load_collections_list()
        elif kind == "folder":
            menu = QMenu(self)
            change = menu.addAction("Cambiar carpeta de descarga...")
            if menu.exec(self.collections_tree.mapToGlobal(pos)) == change:
                self._pick_collection_folder(data["collection_id"])

    def _toggle_collection_git(self, collection_id: str):
        collection = self.collection_manager.get_collection(collection_id)
        if not collection:
            return
        enabled = not collection.get("git_versioning", False)
        if enabled and not GitVersioning.is_available():
            QMessageBox.warning(self, "Git no encontrado", "No se encontró 'git' en el sistema.")
            return
        self.collection_manager.set_git_versioning(collection_id, enabled)
        if enabled and collection.get("download_dir"):
            if not GitVersioning.check_identity(collection["download_dir"]):
                self._set_git_warning(
                    "⚠ Git no tiene user.name/user.email configurados: los commits "
                    "no se van a guardar hasta que los configures."
                )
            else:
                self._clear_git_warning()
                self.statusBar().showMessage(
                    f"Git activado para la Colección '{collection['name']}'", 5000
                )
        elif enabled:
            self.statusBar().showMessage(
                f"Git activado para '{collection['name']}' — asignale una carpeta de descarga",
                6000,
            )
        else:
            self.statusBar().showMessage(
                f"Git desactivado para la Colección '{collection['name']}'", 5000
            )

    def _rename_collection(self, collection_id: str):
        collection = self.collection_manager.get_collection(collection_id)
        if not collection:
            return
        name, ok = QInputDialog.getText(
            self, "Cambiar nombre", "Nuevo nombre de la Colección:",
            text=collection["name"],
        )
        if ok and name.strip():
            self.collection_manager.rename_collection(collection_id, name.strip())
            self._load_collections_list()

    def _pick_collection_folder(self, collection_id: str):
        collection = self.collection_manager.get_collection(collection_id)
        if not collection:
            return
        directory = QFileDialog.getExistingDirectory(
            self, "Carpeta de descarga de la Colección",
            collection.get("download_dir") or str(Path.home()),
        )
        if directory:
            self.collection_manager.set_download_dir(collection_id, directory)
            self._load_collections_list()

    def _delete_collection(self, collection_id: str):
        collection = self.collection_manager.get_collection(collection_id)
        if not collection:
            return
        confirm = QMessageBox.question(
            self, "Eliminar Colección",
            f"¿Eliminar la Colección '{collection['name']}'?",
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.collection_manager.delete_collection(collection_id)
            self._load_collections_list()

    def rename_collection(self, collection_id: str, new_name: str):
        t = self.get_collection(collection_id)
        if t:
            t["name"] = new_name
            self.save_collections()

    def set_download_dir(self, collection_id: str, download_dir: str):
        t = self.get_collection(collection_id)
        if t:
            t["download_dir"] = download_dir
            if t.get("git_versioning"):
                GitVersioning.ensure_repo(download_dir)
            self.save_collections()

    def add_item(self, collection_id: str, url: str, title: str, profile_id: str):
        t = self.get_collection(collection_id)
        if not t:
            return
        t["items"] = [i for i in t["items"] if i["url"] != url]
        t["items"].append({
            "url": url,
            "title": title,
            "profile_id": profile_id,
            "added": datetime.now().isoformat(),
        })
        self.save_collections()

    def remove_item(self, collection_id: str, url: str):
        t = self.get_collection(collection_id)
        if t:
            t["items"] = [i for i in t["items"] if i["url"] != url]
            self.save_collections()

    def delete_collection(self, collection_id: str):
        self.collections = [t for t in self.collections if t["id"] != collection_id]
        self.save_collections()


class NewCollectionDialog(QDialog):
    """Dialog para crear una Colección nueva, con carpeta de descarga opcional
    y checkbox de versionado con Git."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nueva Colección")
        self.selected_dir = ""

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Ej: Proyecto X, Investigación...")
        form.addRow("Nombre de la Colección:", self.name_edit)

        dir_row = QHBoxLayout()
        self.dir_label = QLabel("(usa la carpeta del perfil de cada chat)")
        dir_btn = QPushButton("Elegir carpeta...")
        dir_btn.clicked.connect(self._choose_dir)
        dir_row.addWidget(self.dir_label, 1)
        dir_row.addWidget(dir_btn)
        form.addRow("Carpeta de descarga\n(opcional):", dir_row)

        layout.addLayout(form)

        self.git_checkbox = QCheckBox(
            "Usar Git para versionar y SOBRESCRIBIR archivos con el mismo\n"
            "nombre (código, texto, etc.) en vez de numerarlos"
        )
        layout.addWidget(self.git_checkbox)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_dir(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Carpeta de descarga de la Colección", self.selected_dir or str(Path.home())
        )
        if directory:
            self.selected_dir = directory
            self.dir_label.setText(directory)

    def get_values(self):
        return self.name_edit.text().strip(), self.selected_dir, self.git_checkbox.isChecked()


class SaveToCollectionDialog(QDialog):
    """Dialog para guardar la página actual en una Colección (existente o nuevo)."""

    def __init__(self, parent, collections: list, current_url: str = None, collection_manager=None):
        super().__init__(parent)
        self.setWindowTitle("Guardar en Colección")
        self.current_url = current_url
        self.collection_manager = collection_manager
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.combo = QComboBox()
        self.combo.addItem("+ Crear nueva Colección...", None)
        
        # Detectar en qué colección está la URL actual
        current_collection_id = None
        if current_url and collection_manager:
            for collection in collections:
                col_data = collection_manager.get_collection(collection["id"])
                if col_data and any(item["url"] == current_url for item in col_data.get("items", [])):
                    current_collection_id = collection["id"]
                    break
        
        for t in collections:
            self.combo.addItem(t["name"], t["id"])
        
        # Seleccionar colección actual si la URL ya está en una
        if current_collection_id:
            for i in range(self.combo.count()):
                if self.combo.itemData(i) == current_collection_id:
                    self.combo.setCurrentIndex(i)
                    break
        
        form.addRow("Colección:", self.combo)

        self.new_name_edit = QLineEdit()
        self.new_name_edit.setPlaceholderText("Nombre de la nueva Colección")
        form.addRow("Nombre nueva Colección:", self.new_name_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_values(self):
        """Devuelve (collection_id_or_None, new_name_or_None)."""
        collection_id = self.combo.currentData()
        if collection_id is None:
            return None, self.new_name_edit.text().strip()
        return collection_id, None
