"""
downloads.py - Diálogo de descarga para IA Browser.

DownloadDialog: la descarga arranca sola a una carpeta temporal apenas se
abre el diálogo. Si es un comprimido y el checkbox de extracción está
activo, se lee su contenido y se muestra en la lista. "Aceptar" mueve/extrae
del temporal a la carpeta real, commitea con git si corresponde, y cierra.
"""

import shutil
import tempfile
from pathlib import Path
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QCheckBox, QListWidget, QListWidgetItem, QMessageBox, QProgressBar
)

from file_ops import GitVersioning, FileOps


class DownloadDialog(QDialog):
    """Diálogo de descarga.

    La descarga arranca SOLA apenas se abre el diálogo, a una carpeta
    temporal (no hace falta apretar nada). Apenas termina, si el
    checkbox de "extraer automáticamente" está activo y es un
    comprimido, se lee su contenido real y se muestra en la lista —ya,
    sin pasos intermedios—. Si se destilda el checkbox, se muestra el
    archivo comprimido en su lugar. Tocar cualquiera de los dos
    checkboxes actualiza la vista al instante (el archivo ya está en
    disco, en el temporal, así que no hace falta re-descargar nada).

    Los archivos que YA estaban en la carpeta destino tienen checkbox
    desde el arranque para marcarlos obsoletos y eliminarlos en
    cualquier momento (papelera o git rm + commit).

    "Aceptar" es el único paso final: mueve/extrae del temporal a la
    carpeta real, commitea si corresponde, y CIERRA el diálogo en el
    acto."""

    ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar", ".gz", ".bz2", ".rar", ".7z")

    def __init__(self, parent, download, target_dir: str, filename: str,
                 git_versioning: bool, on_finished_callback=None):
        super().__init__(parent)
        self.download = download
        self.target_dir = target_dir
        self.filename = filename
        self.git_versioning = git_versioning
        self.on_finished_callback = on_finished_callback

        self.exists = (Path(target_dir) / filename).exists()
        self.is_archive = self._detect_archive(filename)

        self._downloaded = False
        self._cancelled = False
        self._finalized = False
        self.temp_dir = tempfile.mkdtemp(prefix="iab_dl_")
        self.temp_path = Path(self.temp_dir) / filename
        self.archive_entries = None  # se llena al terminar de descargar

        folder = Path(target_dir)
        self.pre_existing_names = set(p.name for p in folder.iterdir()) if folder.is_dir() else set()

        self.setWindowTitle("Descargar archivo")
        self.resize(560, 520)
        self._build_ui()
        self._begin_temp_download()

    # ---------- UI ----------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.name_label = QLabel(f"📄 {self.filename}")
        font = self.name_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        self.name_label.setFont(font)
        layout.addWidget(self.name_label)

        layout.addWidget(QLabel(f"Carpeta destino: {self.target_dir}"))

        layout.addWidget(QLabel(
            "Contenido (tildá un archivo existente para marcarlo obsoleto):"
        ))
        self.folder_list = QListWidget()
        layout.addWidget(self.folder_list)

        self.delete_marked_btn = QPushButton("🗑 Eliminar marcados como obsoletos")
        self.delete_marked_btn.clicked.connect(self._delete_marked_items)
        layout.addWidget(self.delete_marked_btn)

        self.replace_checkbox = QCheckBox()
        if self.exists:
            self.replace_checkbox.setText("Ya existe un archivo con este nombre — reemplazarlo (sobrescribir)")
            self.replace_checkbox.setChecked(True)
        else:
            self.replace_checkbox.setText("No hay ningún archivo con este nombre en la carpeta")
            self.replace_checkbox.setChecked(True)
            self.replace_checkbox.setEnabled(False)
        layout.addWidget(self.replace_checkbox)

        self.extract_checkbox = QCheckBox(
            "Es un archivo comprimido: extraer su contenido automáticamente "
            "(se guardan los archivos de adentro, no el .zip/.rar/etc)"
        )
        self.extract_checkbox.setChecked(True)
        self.extract_checkbox.setVisible(self.is_archive)
        layout.addWidget(self.extract_checkbox)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Descargando...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        self.accept_btn = QPushButton("Aceptar")
        self.accept_btn.setEnabled(False)
        self.accept_btn.clicked.connect(self._finalize_and_close)
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self.accept_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        # Conectar DESPUÉS de que todo lo demás exista, para que el
        # toggle actualice la vista al instante.
        self.replace_checkbox.toggled.connect(self._populate_folder_list)
        self.extract_checkbox.toggled.connect(self._populate_folder_list)

        self._populate_folder_list()

    # ---------- Descarga automática a carpeta temporal ----------

    def _begin_temp_download(self):
        try:
            self.download.setDownloadDirectory(self.temp_dir)
            self.download.setDownloadFileName(self.filename)
        except AttributeError:
            self.download.setPath(str(self.temp_path))

        self.download.receivedBytesChanged.connect(self._update_progress)
        self.download.totalBytesChanged.connect(self._update_progress)
        self.download.isFinishedChanged.connect(self._on_temp_download_finished)
        self.download.accept()

    def _update_progress(self):
        try:
            total = self.download.totalBytes()
            received = self.download.receivedBytes()
        except Exception:
            return
        if total and total > 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(received * 100 / total))
        else:
            self.progress_bar.setRange(0, 0)  # indeterminado

    def _on_temp_download_finished(self):
        if self._cancelled or not self.download.isFinished():
            return

        self._downloaded = True
        self.progress_bar.setVisible(False)
        self.status_label.setText("✅ Descarga completa")
        self.accept_btn.setEnabled(True)

        if self.is_archive:
            self.archive_entries = self._read_archive_entries(self.temp_path)
            if self.archive_entries is None:
                self.status_label.setText(
                    "⚠ No se pudo leer el contenido del comprimido (formato no soportado, falta "
                    "una librería opcional, o está dañado). Se va a guardar tal cual."
                )
                self.extract_checkbox.setChecked(False)
                self.extract_checkbox.setEnabled(False)

        self._populate_folder_list()

    # ---------- Lista de carpeta + vista previa en vivo ----------

    def _populate_folder_list(self, *_args):
        self.folder_list.clear()
        folder = Path(self.target_dir)
        entries = []
        if folder.is_dir():
            try:
                entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except OSError:
                entries = []

        existing_names = {e.name for e in entries if not e.name.startswith(".")}
        incoming_top_names = self._incoming_top_level_names() if self._downloaded else set()

        for entry in entries:
            if entry.name.startswith("."):
                continue
            label = entry.name + ("/" if entry.is_dir() else "")
            if entry.name in incoming_top_names:
                label += "  ⏳ (se va a reemplazar)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry.name)
            if entry.name in self.pre_existing_names and entry.is_file():
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
            self.folder_list.addItem(item)

        # ---- Vista previa de lo que se va a agregar (solo lo que NO ----
        # ---- ya está listado arriba, para no duplicar visualmente) ----
        if not self._downloaded:
            self._add_preview_item("⏳ Descargando...")
            return

        if self.is_archive and self.extract_checkbox.isChecked() and self.archive_entries:
            for name in self.archive_entries:
                top_name = name.split("/")[0] if "/" in name else name
                if top_name in existing_names:
                    continue  # ya anotado como "(se va a reemplazar)" en la entrada real
                self._add_preview_item(f"⏳ {name}  (se va a agregar)")
        else:
            final_name = self._resolve_final_name()
            if self.exists and self.replace_checkbox.isChecked():
                pass  # ya anotado como "(se va a reemplazar)" en la entrada real
            elif self.exists and not self.replace_checkbox.isChecked():
                self._add_preview_item(f"⏳ {final_name}  (se va a guardar con otro nombre)")
            else:
                self._add_preview_item(f"⏳ {final_name}  (se va a agregar)")

    def _incoming_top_level_names(self) -> set:
        """Nombres de nivel superior que van a aparecer/reemplazar algo
        en la carpeta destino: para comprimidos extraídos, el primer
        componente de cada ruta interna; para archivo simple, su nombre
        final. Se usa para anotar (en vez de duplicar) las entradas de
        la carpeta que ya existen y van a ser reemplazadas."""
        if self.is_archive and self.extract_checkbox.isChecked() and self.archive_entries:
            return {name.split("/")[0] if "/" in name else name for name in self.archive_entries}
        if self.exists and self.replace_checkbox.isChecked():
            return {self._resolve_final_name()}
        return set()

    def _add_preview_item(self, text: str):
        item = QListWidgetItem(text)
        f = item.font()
        f.setItalic(True)
        f.setBold(True)
        item.setFont(f)
        self.folder_list.addItem(item)

    @staticmethod
    def _detect_archive(filename: str) -> bool:
        lower = filename.lower()
        return any(lower.endswith(ext) for ext in DownloadDialog.ARCHIVE_SUFFIXES)

    # ---------- Eliminar archivos marcados (disponible en cualquier momento) ----------

    def _delete_marked_items(self):
        to_delete = []
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            if (item.flags() & Qt.ItemFlag.ItemIsUserCheckable) and item.checkState() == Qt.CheckState.Checked:
                to_delete.append(item.data(Qt.ItemDataRole.UserRole))

        if not to_delete:
            QMessageBox.information(self, "Aviso", "No marcaste ningún archivo para eliminar.")
            return

        confirm = QMessageBox.question(
            self, "Eliminar archivos obsoletos",
            "¿Eliminar estos archivos?\n\n" + "\n".join(to_delete)
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        use_git = self.git_versioning and GitVersioning.is_available()
        deleted = 0
        for name in to_delete:
            ok = FileOps.git_delete(
                self.target_dir, name,
                f"Elimina {name} (obsoleto) - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ) if use_git else FileOps.soft_delete(self.target_dir, name)
            if ok:
                deleted += 1
                self.pre_existing_names.discard(name)

        modo = "commiteados con git" if use_git else "movidos a la papelera"
        self.status_label.setText(f"🗑 {deleted} archivo(s) eliminados ({modo})")
        self._populate_folder_list()

    # ---------- Lógica de nombre / colisión ----------

    def _resolve_final_name(self) -> str:
        if not self.exists or self.replace_checkbox.isChecked():
            return self.filename
        base = Path(self.filename)
        stem, suffix = base.stem, base.suffix
        i = 1
        while (Path(self.target_dir) / f"{stem}({i}){suffix}").exists():
            i += 1
        return f"{stem}({i}){suffix}"

    # ---------- Cancelar ----------

    def _cancel(self):
        self._cancelled = True
        try:
            if not self.download.isFinished():
                self.download.cancel()
        except Exception:
            pass
        self._cleanup_temp()
        self.reject()

    def closeEvent(self, event):
        if not self._finalized and not self._cancelled:
            self._cancelled = True
            try:
                if not self.download.isFinished():
                    self.download.cancel()
            except Exception:
                pass
            self._cleanup_temp()
        event.accept()

    def _cleanup_temp(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ---------- Aceptar: mover/extraer a destino y CERRAR ----------

    def _finalize_and_close(self):
        if not self._downloaded:
            return
        self._finalized = True

        final_name = self._resolve_final_name()
        final_target = Path(self.target_dir) / final_name

        if self.replace_checkbox.isChecked() and self.exists:
            try:
                if final_target.exists():
                    final_target.unlink()
            except OSError:
                pass

        extracted = False
        try:
            if self.is_archive and self.extract_checkbox.isChecked() and self.archive_entries:
                extracted = self._extract_temp_into_target()
            else:
                Path(self.target_dir).mkdir(parents=True, exist_ok=True)
                shutil.move(str(self.temp_path), str(final_target))
        except Exception as e:
            QMessageBox.warning(self, "Error", f"No se pudo guardar el archivo: {e}")
            self._cleanup_temp()
            self.reject()
            return

        self._cleanup_temp()

        if self.on_finished_callback:
            self.on_finished_callback(self.target_dir, final_name, extracted, self.git_versioning)

        self.accept()  # cierra el diálogo ya

    # ---------- Lectura / extracción del comprimido ----------

    @staticmethod
    def _read_archive_entries(path: Path):
        """Devuelve la lista de nombres dentro del comprimido, o None si
        no se pudo leer (formato no soportado, falta librería, o dañado)."""
        lower = path.name.lower()
        try:
            if lower.endswith(".zip"):
                import zipfile
                with zipfile.ZipFile(path) as z:
                    return [n for n in z.namelist() if not n.endswith("/")]
            elif lower.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar")):
                import tarfile
                with tarfile.open(path) as t:
                    return [m.name for m in t.getmembers() if m.isfile()]
            elif lower.endswith(".rar"):
                import rarfile
                with rarfile.RarFile(path) as r:
                    return [n for n in r.namelist() if not n.endswith("/")]
            elif lower.endswith(".7z"):
                import py7zr
                with py7zr.SevenZipFile(path, mode="r") as z:
                    return list(z.getnames())
            elif lower.endswith((".gz", ".bz2")):
                return [path.stem]
        except ImportError:
            return None
        except Exception:
            return None
        return None

    def _extract_temp_into_target(self) -> bool:
        """Extrae TODO el contenido del comprimido (ya descargado en el
        temporal) directo a la carpeta destino."""
        path = self.temp_path
        folder = Path(self.target_dir)
        folder.mkdir(parents=True, exist_ok=True)
        lower = path.name.lower()
        try:
            if lower.endswith(".zip"):
                import zipfile
                with zipfile.ZipFile(path) as z:
                    z.extractall(folder)
            elif lower.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar")):
                import tarfile
                with tarfile.open(path) as t:
                    t.extractall(folder)
            elif lower.endswith(".gz"):
                import gzip
                out_path = folder / path.stem
                with gzip.open(path, "rb") as f_in, open(out_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            elif lower.endswith(".bz2"):
                import bz2
                out_path = folder / path.stem
                with bz2.open(path, "rb") as f_in, open(out_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            elif lower.endswith(".rar"):
                import rarfile
                with rarfile.RarFile(path) as r:
                    r.extractall(folder)
            elif lower.endswith(".7z"):
                import py7zr
                with py7zr.SevenZipFile(path, mode="r") as z:
                    z.extractall(path=folder)
            else:
                return False
            return True
        except Exception as e:
            # Si algo falla en el último paso, al menos guardamos el
            # comprimido entero para no perder la descarga.
            try:
                shutil.move(str(path), str(folder / path.name))
            except Exception:
                pass
            QMessageBox.warning(self, "Error al extraer", f"No se pudo extraer: {e}\nSe guardó el comprimido.")
            return False
