"""
collections_manager.py - "Colecciones" para WebAgent: grupos de
marcadores multi-perfil.

Contiene:
  - CollectionManager: maneja Colecciones (grupos de marcadores/urls que
    pueden pertenecer a distintos perfiles), cada una con su propia
    carpeta, repositorio Git y README.md.
  - NewCollectionDialog: crear una Colección nueva.
  - SaveToCollectionDialog: guardar la página actual en una Colección.

La vista de una Colección (sus marcadores + acceso a su carpeta) se
muestra como un árbol desplegable directamente en el sidebar de
main.py — ya no como una ventana modal aparte.
"""

import json
import shutil
import uuid
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QLabel, QDialogButtonBox, QFileDialog,
    QComboBox, QMessageBox, QInputDialog, QMenu, QTreeWidgetItem
)
from PyQt6.QtCore import Qt, QUrl

from file_ops import GitVersioning
from paths import IA_DATA_DIR, TASK_AGENT_CONTEXT_DIR
from profiles import ProfileManager


# ======================================================================
# Colecciones (grupos de marcadores multi-perfil)
# ======================================================================

class CollectionManager:
    """Maneja "Colecciones": grupos de marcadores que pueden pertenecer a distintos
    perfiles, cada Colección con carpeta, Git y README.md obligatorios."""

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
        for collection in self.collections:
            collection.setdefault("id", uuid.uuid4().hex[:12])
            collection.setdefault("parent_id", None)
            if not collection.get("download_dir"):
                collection["download_dir"] = str(
                    self.base_dir / "collections" / collection["id"]
                )
            collection["git_versioning"] = True
            collection.setdefault("items", [])
            if collection["download_dir"]:
                Path(collection["download_dir"]).mkdir(parents=True, exist_ok=True)
                if GitVersioning.is_available():
                    GitVersioning.ensure_repo(collection["download_dir"])
                self.ensure_readme(collection["download_dir"], collection["name"])
        self._sync_parents_from_folders()
        self.save_collections()

    def _sync_parents_from_folders(self):
        """Agrupa las colecciones según la contención de sus carpetas."""
        paths = {}
        for collection in self.collections:
            download_dir = collection.get("download_dir")
            if download_dir:
                paths[collection["id"]] = Path(download_dir).resolve()

        for collection in self.collections:
            collection_path = paths.get(collection["id"])
            if not collection_path:
                collection["parent_id"] = None
                continue
            ancestors = []
            for candidate in self.collections:
                if candidate["id"] == collection["id"]:
                    continue
                candidate_path = paths.get(candidate["id"])
                if not candidate_path or candidate_path == collection_path:
                    continue
                try:
                    collection_path.relative_to(candidate_path)
                except ValueError:
                    continue
                ancestors.append((len(candidate_path.parts), candidate["id"]))
            collection["parent_id"] = max(ancestors)[1] if ancestors else None

    def save_collections(self):
        with open(self.collections_file, "w") as f:
            json.dump(self.collections, f, indent=2)

    def prepare_task_agent_context(self) -> Path:
        """Prepara los datos aislados que necesita el agente de nuevas tareas."""
        context_dir = TASK_AGENT_CONTEXT_DIR
        context_dir.mkdir(parents=True, exist_ok=True)
        readmes_dir = context_dir / "readmes"
        readmes_dir.mkdir(parents=True, exist_ok=True)

        context_collections = []
        for collection in self.collections:
            download_dir = str(collection.get("download_dir", "")).strip()
            if not download_dir:
                raise ValueError(
                    f'La Colección "{collection.get("name", "sin nombre")}" no tiene carpeta'
                )
            source_readme = Path(download_dir) / "README.md"
            readme_copy = readmes_dir / f'{collection["id"]}.md'
            if not source_readme.is_file():
                self.ensure_readme(
                    download_dir,
                    collection.get("name", "Colección"),
                )
            shutil.copyfile(source_readme, readme_copy)
            context_collections.append({
                "id": collection.get("id", ""),
                "name": collection.get("name", ""),
                "download_dir": collection.get("download_dir", ""),
                "readme": str(readme_copy.resolve()),
                "items": collection.get("items", []),
            })

        (context_dir / "collections.json").write_text(
            json.dumps(context_collections, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return context_dir

    def create_collection(
        self, name: str, download_dir: str, parent_id: str | None = None
    ) -> dict:
        if not download_dir:
            raise ValueError("La Colección necesita una carpeta")
        Path(download_dir).mkdir(parents=True, exist_ok=True)
        if not GitVersioning.is_available():
            raise RuntimeError("Git no está disponible")
        GitVersioning.ensure_repo(download_dir)
        if not GitVersioning.has_repo(download_dir):
            raise RuntimeError("No se pudo inicializar Git")
        self.ensure_readme(download_dir, name)
        entry = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "download_dir": download_dir,
            "git_versioning": True,
            "parent_id": parent_id,
            "items": [],
            "created": datetime.now().isoformat(),
        }
        self.collections.append(entry)
        self._sync_parents_from_folders()
        self.save_collections()
        return entry

    @staticmethod
    def ensure_readme(download_dir: str, name: str):
        readme = Path(download_dir) / "README.md"
        if not readme.exists():
            readme.write_text(
                f"# {name}\n\nCarpeta de la Colección **{name}**.\n",
                encoding="utf-8",
            )

    @staticmethod
    def git_subfolders(directory: str) -> list[Path]:
        """Devuelve las subcarpetas inmediatas que ya son repositorios Git."""
        try:
            return sorted(
                (
                    child for child in Path(directory).iterdir()
                    if child.is_dir() and (child / ".git").exists()
                ),
                key=lambda path: path.name.lower(),
            )
        except OSError:
            return []

    def set_git_versioning(self, collection_id: str, enabled: bool):
        t = self.get_collection(collection_id)
        if t:
            t["git_versioning"] = True
            if t.get("download_dir"):
                GitVersioning.ensure_repo(t["download_dir"])
                self.ensure_readme(t["download_dir"], t["name"])
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

    def rename_collection(self, collection_id: str, new_name: str):
        collection = self.get_collection(collection_id)
        if collection:
            collection["name"] = new_name
            self.save_collections()

    def set_download_dir(self, collection_id: str, download_dir: str):
        collection = self.get_collection(collection_id)
        if collection:
            if not GitVersioning.is_available():
                raise RuntimeError("Git no está disponible")
            collection["download_dir"] = download_dir
            Path(download_dir).mkdir(parents=True, exist_ok=True)
            GitVersioning.ensure_repo(download_dir)
            if not GitVersioning.has_repo(download_dir):
                raise RuntimeError("No se pudo inicializar Git")
            self.ensure_readme(download_dir, collection["name"])
            collection["git_versioning"] = True
            self._sync_parents_from_folders()
            self.save_collections()

    def set_parent(self, collection_id: str, parent_id: str | None):
        collection = self.get_collection(collection_id)
        if not collection or collection_id == parent_id:
            return
        descendants = {collection_id}
        changed = True
        while changed:
            changed = False
            for candidate in self.collections:
                if candidate.get("parent_id") in descendants and candidate["id"] not in descendants:
                    descendants.add(candidate["id"])
                    changed = True
        if parent_id in descendants:
            raise ValueError("Una Colección no puede contenerse a sí misma")
        if parent_id and not self.get_collection(parent_id):
            raise ValueError("La Colección padre no existe")
        collection["parent_id"] = parent_id
        self.save_collections()

    def add_item(self, collection_id: str, url: str, title: str, profile_id: str):
        collection = self.get_collection(collection_id)
        if not collection:
            return
        collection["items"] = [
            item for item in collection.get("items", []) if item.get("url") != url
        ]
        collection.setdefault("items", []).append({
            "url": url,
            "title": title,
            "profile_id": profile_id,
            "added": datetime.now().isoformat(),
        })
        self.save_collections()

    def remove_item(self, collection_id: str, url: str):
        collection = self.get_collection(collection_id)
        if collection:
            collection["items"] = [
                item for item in collection.get("items", []) if item.get("url") != url
            ]
            self.save_collections()

    def delete_collection(self, collection_id: str):
        self.collections = [
            collection for collection in self.collections
            if collection["id"] != collection_id
        ]
        self.save_collections()

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
                "parent_id": collection.get("parent_id"),
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
        entries = self.collection_manager.sidebar_entries(
            self.profile_manager.profile_names()
        )
        by_parent = {}
        for collection in entries:
            by_parent.setdefault(collection.get("parent_id"), []).append(collection)

        def add_collection(collection, parent_item=None):
            top = QTreeWidgetItem(
                parent_item or self.collections_tree,
                [f"🗂 {collection['name']} ({len(collection['items'])})"],
            )
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
            top.setExpanded(collection["id"] in expanded_ids)
            for nested in by_parent.get(collection["id"], []):
                add_collection(nested, top)

        for collection in by_parent.get(None, []):
            add_collection(collection)
        agent_console = getattr(self, "agent_console", None)
        if agent_console is not None:
            agent_console._refresh_directory_options()

    def _create_collection_dialog(self):
        dialog = NewCollectionDialog(self, self.collection_manager.collections)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, download_dir = dialog.get_values()
        if not name:
            QMessageBox.warning(self, "Aviso", "La Colección necesita un nombre")
            return
        if not GitVersioning.is_available():
            QMessageBox.warning(
                self, "Git no encontrado",
                "No se encontró 'git' en el sistema. Instala Git para crear Colecciones.",
            )
            return
        if not download_dir:
            QMessageBox.warning(self, "Aviso", "La Colección necesita una carpeta")
            return
        download_dir = self._suggest_git_subfolder(download_dir)
        if not download_dir:
            return
        try:
            self.collection_manager.create_collection(name, download_dir)
        except (RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "No se pudo crear la Colección", str(error))
            return
        self._load_collections_list()
        if not GitVersioning.check_identity(download_dir):
            self._set_git_warning(
                "⚠ Git no tiene user.name/user.email configurados: los commits "
                "no se van a guardar hasta que los configures."
            )

    def _suggest_git_subfolder(self, directory: str) -> str | None:
        """Pregunta si los repositorios anidados deben ser Colecciones propias."""
        repositories = self.collection_manager.git_subfolders(directory)
        if not repositories:
            return directory
        names = ", ".join(repository.name for repository in repositories)
        answer = QMessageBox.question(
            self,
            "Repositorios Git detectados",
            "La carpeta elegida contiene estos repositorios Git propios:\n"
            f"{names}\n\n"
            "¿Quieres agregarlos como Colecciones independientes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            created = []
            skipped = []
            for repository in repositories:
                repository_path = repository.resolve()
                existing = next(
                    (
                        collection for collection in self.collection_manager.collections
                        if collection.get("download_dir")
                        and Path(collection["download_dir"]).resolve() == repository_path
                    ),
                    None,
                )
                if existing:
                    skipped.append(repository.name)
                    continue
                try:
                    self.collection_manager.create_collection(
                        repository.name, str(repository)
                    )
                    created.append(repository.name)
                except (RuntimeError, ValueError) as error:
                    QMessageBox.warning(
                        self,
                        "No se pudo agregar el repositorio",
                        f"No se pudo agregar '{repository.name}' como Colección:\n{error}",
                    )
            self._load_collections_list()
            details = []
            if created:
                details.append(f"Agregadas: {', '.join(created)}.")
            if skipped:
                details.append(
                    f"Ya existentes: {', '.join(skipped)}."
                )
            if details:
                QMessageBox.information(
                    self,
                    "Colecciones actualizadas",
                    " ".join(details),
                )
            # Las subcolecciones se crean como complemento; la carpeta
            # elegida sigue siendo la Colección principal.
            return directory
        return directory

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
            create_dialog = NewCollectionDialog(self, self.collection_manager.collections)
            create_dialog.name_edit.setText(new_name)
            if create_dialog.exec() != QDialog.DialogCode.Accepted:
                return
            name, download_dir = create_dialog.get_values()
            if not name or not download_dir or not GitVersioning.is_available():
                QMessageBox.warning(
                    self, "Aviso",
                    "La nueva Colección necesita nombre, carpeta y Git disponible.",
                )
                return
            download_dir = self._suggest_git_subfolder(download_dir)
            if not download_dir:
                return
            try:
                collection_id = self.collection_manager.create_collection(
                    name, download_dir
                )["id"]
            except (RuntimeError, ValueError) as error:
                QMessageBox.warning(self, "No se pudo crear la Colección", str(error))
                return
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
            menu = QMenu(self)
            rename = menu.addAction("Cambiar nombre...")
            folder = menu.addAction("Carpeta de descarga...")
            delete = menu.addAction("Eliminar Colección")
            action = menu.exec(self.collections_tree.mapToGlobal(pos))
            if action == rename:
                self._rename_collection(collection_id)
            elif action == folder:
                self._pick_collection_folder(collection_id)
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
        if not GitVersioning.is_available():
            QMessageBox.warning(self, "Git no encontrado", "No se encontró 'git' en el sistema.")
            return
        self.collection_manager.set_git_versioning(collection_id, True)
        if collection.get("download_dir"):
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
            try:
                self.collection_manager.set_download_dir(collection_id, directory)
            except RuntimeError as error:
                QMessageBox.warning(self, "No se pudo configurar la carpeta", str(error))
                return
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

class NewCollectionDialog(QDialog):
    """Diálogo para crear una Colección."""

    def __init__(
        self, parent=None, collections=None, title="Nueva Colección",
        name="", selected_dir="", allow_name=True,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.selected_dir = selected_dir

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setText(name)
        self.name_edit.setEnabled(allow_name)
        self.name_edit.setPlaceholderText("Ej: Proyecto X, Investigación...")
        form.addRow("Nombre de la Colección:", self.name_edit)

        dir_row = QHBoxLayout()
        self.dir_label = QLabel(self.selected_dir or "(obligatoria)")
        dir_btn = QPushButton("Elegir carpeta...")
        dir_btn.clicked.connect(self._choose_dir)
        dir_row.addWidget(self.dir_label, 1)
        dir_row.addWidget(dir_btn)
        form.addRow("Carpeta de la Colección:", dir_row)

        layout.addLayout(form)

        layout.addWidget(QLabel("Git y README.md son obligatorios para toda Colección."))

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
        return (
            self.name_edit.text().strip(),
            self.selected_dir,
        )


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
