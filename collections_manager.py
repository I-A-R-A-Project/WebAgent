"""
collections_manager.py - "Colecciones" para IA Browser: grupos de
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
    QComboBox
)

from file_ops import GitVersioning
from profiles import ProfileManager


# ======================================================================
# Colecciones (grupos de marcadores multi-perfil)
# ======================================================================

class CollectionManager:
    """Maneja "Colecciones": grupos de marcadores que pueden pertenecer a distintos
    perfiles, cada Colección con su propia carpeta de descarga opcional."""

    def __init__(self):
        self.base_dir = Path.home() / ".ia_browser"
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
