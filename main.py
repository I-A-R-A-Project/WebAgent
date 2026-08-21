"""
main.py - IA Browser: ventana principal.

Navegador con pestañas, perfiles aislados (sesión/cookies/cache propios) y
"Colecciones" (grupos de marcadores multi-perfil con carpeta de descarga propia).

Ejecutar con: python main.py
"""

import sys
import os
import re
import shutil
import html as html_escape_module
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QToolBar, QTabWidget,
    QMessageBox, QDialog, QLabel, QInputDialog, QFileDialog, QMenu, QComboBox,
    QTreeWidget, QTreeWidgetItem
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineDownloadRequest
from PyQt6.QtCore import Qt, QUrl, QMimeData, QEvent
from PyQt6.QtGui import QAction, QKeySequence, QKeyEvent

from file_ops import GitVersioning
from profiles import ProfileManager, NewProfileDialog
from collections_manager import CollectionManager, NewCollectionDialog, SaveToCollectionDialog
from downloads import DownloadDialog
from iara_common.json_store import SidebarAppsStore
from iara_common.session import load_tab_session, save_tab_session
from iara_common.sidebar import AppPanelOverlay, SidebarContainer, SidebarRail
from iara_common import local_viewer
from iara_common.pdf_tab import PdfTab
from iara_common.tabs import VIDEO_EXTS, UnifiedWebTab
from iara_common.video_tab import VideoTab
from iara_common.web_profiles import build_web_profile
from codex_manager import CodexManagerDialog


class IABrowser(QMainWindow):
    """Navegador con pestañas, perfiles y Colecciones."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("IA Browser")
        self.setGeometry(100, 100, 1600, 1000)
        self.setAcceptDrops(True)

        self.profile_manager = ProfileManager()
        self.collection_manager = CollectionManager()
        self.sidebar_apps_store = SidebarAppsStore(str(Path.home() / '.minibrowser' / 'sidebar_apps.json'), [])
        self.web_engine_profiles: dict[str, QWebEngineProfile] = {}
        self.current_profile_id = self.profile_manager.profiles[0]["id"]
        self.session_file = self.profile_manager.base_dir / "session.json"

        # Metadata por pestaña: id(webview) -> {"profile_id":.., "collection_id": .. or None}
        self.tab_data: dict[int, dict] = {}
        self.current_url = ""

        # Mantiene vivos los diálogos de descarga en curso (no modales)
        # para que no se destruyan mientras la descarga sigue en background.
        self._download_dialogs = []

        self._setup_ui()
        self._setup_status_bar()
        self._setup_menu()
        self._load_profiles_list()
        self._load_collections_list()

        if not self._restore_session_or_default():
            self._activate_profile(self.current_profile_id, open_home=True)

    # ------------------------------------------------------------------
    # Barra de estado
    # ------------------------------------------------------------------

    def _setup_status_bar(self):
        self.statusBar().showMessage("Listo")
        self.git_warning_label = QLabel("")
        self.git_warning_label.setStyleSheet("color: #b45309; font-weight: bold; padding-right: 10px;")
        self.statusBar().addPermanentWidget(self.git_warning_label)

    def _set_git_warning(self, text: str):
        self.git_warning_label.setText(text)

    def _clear_git_warning(self):
        self.git_warning_label.setText("")

    # ------------------------------------------------------------------
    # Drag & drop de archivos -> carpeta de la Colección/perfil activo
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        """Al soltar archivos sobre la ventana, se copian a la carpeta del
        Colección activa (o del perfil si no hay Colección). Nota: si el archivo se
        suelta directamente sobre el contenido de la página web, Chromium
        puede manejar el drop internamente (para subirlo al chat) y este
        evento no se dispara; funciona de forma confiable al soltar sobre
        el resto de la ventana (pestañas, barra lateral, toolbar)."""
        urls = event.mimeData().urls()
        if not urls:
            super().dropEvent(event)
            return

        webview = self.current_webview()
        tab_meta = self.tab_data.get(id(webview), {}) if webview else {}
        target_dir, context_label = self._resolve_target_folder(tab_meta)

        copied = 0
        for url in urls:
            if url.isLocalFile():
                self._copy_file_to_folder(url.toLocalFile(), target_dir, context_label, silent=True)
                copied += 1

        if copied:
            self.statusBar().showMessage(
                f"📎 {copied} archivo(s) copiados a {context_label} ({target_dir})", 6000
            )
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Qt profile handling
    # ------------------------------------------------------------------

    def _get_qt_profile(self, profile_id: str) -> QWebEngineProfile:
        if profile_id in self.web_engine_profiles:
            return self.web_engine_profiles[profile_id]

        data = self.profile_manager.get_profile(profile_id)
        qt_profile = build_web_profile(
            profile_id,
            self,
            data["storage_path"],
            data["cache_path"],
            download_path=data.get("files_dir"),
        )
        qt_profile.downloadRequested.connect(self._on_download_requested)

        self.web_engine_profiles[profile_id] = qt_profile
        return qt_profile

    def _on_download_requested(self, download: "QWebEngineDownloadRequest"):
        """Resuelve la carpeta de descarga (Colección activa o perfil de la
        pestaña) y abre el diálogo de descarga para que el usuario
        confirme nombre, reemplazo y extracción antes de arrancar."""
        webview = self.current_webview()
        tab_meta = self.tab_data.get(id(webview), {}) if webview else {}
        collection_id = tab_meta.get("collection_id")
        profile_id = tab_meta.get("profile_id", self.current_profile_id)

        target_dir, _ = self._resolve_target_folder(tab_meta)

        git_versioning = False
        collection_has_dir = False
        if collection_id:
            collection = self.collection_manager.get_collection(collection_id)
            if collection and collection.get("download_dir"):
                collection_has_dir = True
                git_versioning = bool(collection.get("git_versioning"))
        if not collection_has_dir:
            profile_data = self.profile_manager.get_profile(profile_id)
            git_versioning = bool(profile_data and profile_data.get("git_versioning"))

        filename = Path(download.downloadFileName()).name

        dialog = DownloadDialog(
            self, download, target_dir, filename,
            git_versioning=git_versioning and GitVersioning.is_available(),
            on_finished_callback=self._handle_download_finished,
        )
        self._download_dialogs.append(dialog)
        dialog.finished.connect(lambda _r=None, d=dialog: self._forget_download_dialog(d))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forget_download_dialog(self, dialog):
        if dialog in self._download_dialogs:
            self._download_dialogs.remove(dialog)

    def _handle_download_finished(self, target_dir: str, filename: str, extracted: bool, git_versioning: bool):
        """Callback del DownloadDialog al terminar: se encarga del commit
        con git si corresponde (un commit por archivo, o 'add -A' si se
        extrajo un comprimido y aparecieron varios archivos nuevos)."""
        if not git_versioning or not GitVersioning.is_available():
            self.statusBar().showMessage(f"✅ Descarga completa: {filename}", 6000)
            return

        GitVersioning.ensure_repo(target_dir)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if extracted:
            message = f"Extrae {filename} - {timestamp}"
            committed = GitVersioning.commit_all(target_dir, message)
        else:
            message = f"Actualiza {filename} - {timestamp}"
            committed = GitVersioning.commit_file(target_dir, filename, message)

        if committed:
            self.statusBar().showMessage(f"✅ {filename} commiteado con git", 6000)
            self._clear_git_warning()
        elif not GitVersioning.check_identity(target_dir):
            self._set_git_warning(
                "⚠ Git no tiene user.name/user.email configurados: los cambios "
                "no se están commiteando. Configurá con "
                "'git config --global user.name \"Tu Nombre\"' y "
                "'git config --global user.email \"tu@email.com\"'"
            )
        else:
            self.statusBar().showMessage(
                f"⚠ {filename} guardado, pero no se pudo commitear (sin cambios o error de git)", 6000
            )

    def _activate_profile(self, profile_id: str, open_home: bool = False):
        """Cambia el perfil activo: cierra las pestañas y abre una nueva
        con el cache/sesión del nuevo perfil."""
        data = self.profile_manager.get_profile(profile_id)
        if not data:
            return

        self.current_profile_id = profile_id

        while self.tabs.count() > 0:
            widget = self.tabs.widget(0)
            self.tab_data.pop(id(widget), None)
            self.tabs.removeTab(0)

        self._add_tab(profile_id=profile_id)
        if open_home:
            self.load_url(data["home_url"])
        self.current_webview().setZoomFactor(data.get("zoom", 1.0))
        self._highlight_active_profile()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self):
        content_widget = QWidget()
        main_layout = QHBoxLayout(content_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self._setup_sidebar(main_layout)
        self._setup_tabs_and_navbar(main_layout)

        self.rail = SidebarRail()
        self.rail.on_toggle = self._on_sidebar_app_clicked
        self.rail.rebuild(self.sidebar_apps_store.all())

        default_profile_id = self.profile_manager.get_default_profile_id()
        self.app_panel = AppPanelOverlay(self._get_qt_profile(default_profile_id))
        self.app_panel.on_new_window_request = self._handle_new_window_request

        container = SidebarContainer(self.rail, content_widget, self.app_panel)
        self.setCentralWidget(container)

    def _on_sidebar_app_clicked(self, app):
        app_id = app["id"]
        if self.app_panel.is_open_for(app_id):
            self.app_panel.close_panel()
            self.rail.set_checked(app_id, False)
        else:
            self.rail.uncheck_all()
            self.app_panel.open_app(app)
            self.rail.set_checked(app_id, True)

    def refresh_sidebar_apps(self):
        self.rail.rebuild(self.sidebar_apps_store.all(), self.app_panel.active_app_id)

    def _handle_new_window_request(self, request):
        webview = self._add_tab(profile_id=self.current_profile_id)
        request.openIn(webview.page())

    def _setup_sidebar(self, parent_layout):
        """Sidebar con Perfiles y Colecciones."""
        sidebar_layout = QVBoxLayout()

        # --- Perfiles (menú desplegable) ---
        profiles_label = QLabel("👤 Perfiles")
        sidebar_layout.addWidget(profiles_label)

        profile_row = QHBoxLayout()
        self.profiles_combo = QComboBox()
        self.profiles_combo.currentIndexChanged.connect(self._on_profile_combo_changed)
        profile_row.addWidget(self.profiles_combo, 1)

        manage_profile_btn = QPushButton("⚙")
        manage_profile_btn.setFixedWidth(32)
        manage_profile_btn.setToolTip("Nuevo perfil, renombrar, cambiar carpeta, Git o eliminar")
        manage_profile_btn.clicked.connect(self._manage_profile_menu)
        profile_row.addWidget(manage_profile_btn)
        sidebar_layout.addLayout(profile_row)

        self.profile_info_label = QLabel("")
        self.profile_info_label.setWordWrap(True)
        self.profile_info_label.setStyleSheet("color: gray; font-size: 11px;")
        sidebar_layout.addWidget(self.profile_info_label)

        sidebar_layout.addSpacing(16)

        # --- Colecciones ---
        collections_label = QLabel("🗂 Colecciones")
        sidebar_layout.addWidget(collections_label)

        self.collections_tree = QTreeWidget()
        self.collections_tree.setHeaderHidden(True)
        self.collections_tree.setIndentation(14)
        self.collections_tree.itemClicked.connect(self._on_collection_tree_item_clicked)
        self.collections_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.collections_tree.customContextMenuRequested.connect(self._on_collection_context_menu)
        sidebar_layout.addWidget(self.collections_tree)

        add_collection_btn = QPushButton("+ Nueva Colección")
        add_collection_btn.clicked.connect(self._create_collection_dialog)
        sidebar_layout.addWidget(add_collection_btn)

        sidebar_layout.addStretch()

        sidebar_container = QWidget()
        sidebar_container.setLayout(sidebar_layout)
        sidebar_container.setMaximumWidth(280)
        parent_layout.addWidget(sidebar_container)

    def _setup_tabs_and_navbar(self, parent_layout):
        right_layout = QVBoxLayout()

        navbar = self._create_navbar()
        right_layout.addWidget(navbar)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        right_layout.addWidget(self.tabs)

        right_container = QWidget()
        right_container.setLayout(right_layout)
        parent_layout.addWidget(right_container, 1)

    def _create_navbar(self) -> QToolBar:
        navbar = QToolBar("Navigation")

        back_action = QAction("◀", self)
        back_action.triggered.connect(lambda: self.current_webview().back())
        back_action.setShortcut(QKeySequence.StandardKey.Back)
        navbar.addAction(back_action)

        fwd_action = QAction("▶", self)
        fwd_action.triggered.connect(lambda: self.current_webview().forward())
        fwd_action.setShortcut(QKeySequence.StandardKey.Forward)
        navbar.addAction(fwd_action)

        reload_action = QAction("🔄", self)
        reload_action.triggered.connect(lambda: self.current_webview().reload())
        reload_action.setShortcut(QKeySequence.StandardKey.Refresh)
        navbar.addAction(reload_action)

        stop_action = QAction("⏹", self)
        stop_action.triggered.connect(lambda: self.current_webview().stop())
        navbar.addAction(stop_action)

        navbar.addSeparator()

        home_action = QAction("🏠", self)
        home_action.triggered.connect(self.go_home)
        navbar.addAction(home_action)

        new_tab_action = QAction("+ Tab", self)
        new_tab_action.setToolTip("Nueva pestaña con el perfil Default")
        new_tab_action.setShortcut("Ctrl+T")
        new_tab_action.triggered.connect(self._open_new_default_tab)
        navbar.addAction(new_tab_action)

        navbar.addSeparator()

        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("URL...")
        self.address_bar.returnPressed.connect(self._on_address_bar_enter)
        navbar.addWidget(self.address_bar)

        save_theme_action = QAction("⭐ Colección", self)
        save_theme_action.setToolTip("Guardar esta página en una Colección")
        save_theme_action.triggered.connect(self._save_current_to_collection)
        navbar.addAction(save_theme_action)

        open_files_action = QAction("📁", self)
        open_files_action.setToolTip("Abrir carpeta de archivos activa")
        open_files_action.triggered.connect(self._open_current_folder)
        navbar.addAction(open_files_action)

        attach_action = QAction("📎", self)
        attach_action.setToolTip(
            "Copia el archivo a la carpeta de la Colección activa (o del perfil) y\n"
            "además intenta pegarlo (Ctrl+V) en el chat actual. Hacé clic en\n"
            "el cuadro de texto del chat ANTES de usar este botón. No funciona\n"
            "en todos los sitios (depende de que soporten pegar archivos)."
        )
        attach_action.triggered.connect(self._attach_to_chat_dialog)
        navbar.addAction(attach_action)

        return navbar

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _add_tab(self, profile_id: str | None = None, collection_id: str | None = None):
        """Agrega una pestaña usando el perfil indicado (o el activo)."""
        profile_id = profile_id or self.current_profile_id
        qt_profile = self._get_qt_profile(profile_id)

        webview = UnifiedWebTab(
            qt_profile,
            parent_window=self,
            folder_view_handler=self._render_folder_view,
            new_window_handler=self._handle_new_window_request,
            url_changed_handler=self._on_tab_url_changed,
            title_changed_handler=self._on_tab_title_changed,
            icon_changed_handler=self._on_tab_icon_changed,
            special_local_handler=self.handle_special_local_file,
        )

        self.tab_data[id(webview)] = {"profile_id": profile_id, "collection_id": collection_id}

        tab_index = self.tabs.addTab(webview, "Nueva pestaña")
        self.tabs.setCurrentIndex(tab_index)
        return webview

    def _on_tab_url_changed(self, webview, url: QUrl):
        if webview is not self.current_webview():
            return
        self.current_url = url.toString()
        self.address_bar.setText(self.current_url)

    def _on_tab_title_changed(self, webview, title: str):
        index = self.tabs.indexOf(webview)
        if index >= 0:
            self.tabs.setTabText(index, (title or "Nueva pestaña")[:30])

    def _on_tab_icon_changed(self, webview, icon):
        index = self.tabs.indexOf(webview)
        if index >= 0:
            self.tabs.setTabIcon(index, icon)

    def _open_new_default_tab(self):
        """Botón '+ Tab': siempre abre con el perfil Default,
        independientemente de cuál esté seleccionado en el combo."""
        default_id = self.profile_manager.get_default_profile_id()
        self._open_new_tab_with_profile(default_id)

    def _open_new_tab_with_profile(self, profile_id: str):
        """Abre una pestaña nueva usando el perfil indicado, cargando su
        home_url, sin tocar las pestañas existentes."""
        webview = self._add_tab(profile_id=profile_id)
        data = self.profile_manager.get_profile(profile_id)
        if data:
            self.load_url(data["home_url"])
            webview.setZoomFactor(data.get("zoom", 1.0))
        return webview

    def _close_tab(self, index: int):
        if self.tabs.count() > 1:
            widget = self.tabs.widget(index)
            self.tab_data.pop(id(widget), None)
            self.tabs.removeTab(index)
            if isinstance(widget, VideoTab):
                widget.stop()
            widget.deleteLater()
        else:
            QMessageBox.information(self, "Información", "Debe haber al menos una pestaña abierta")

    def current_webview(self) -> QWebEngineView:
        return self.tabs.currentWidget()

    def _on_tab_changed(self, index: int):
        """Al cambiar de pestaña, sincroniza el combo de perfiles y la
        barra de dirección con la pestaña recién seleccionada."""
        if index < 0:
            return
        webview = self.tabs.widget(index)
        if webview is None:
            return
        meta = self.tab_data.get(id(webview), {})
        self.current_profile_id = meta.get("profile_id", self.current_profile_id)
        self._highlight_active_profile()

        self.current_url = webview.url().toString()
        self.address_bar.setText(self.current_url)

    def _on_address_bar_enter(self):
        self.load_url(self.address_bar.text())

    def _on_url_changed(self, url: QUrl):
        webview = self.sender()
        if webview is not self.current_webview():
            return
        self.current_url = url.toString()
        self.address_bar.setText(self.current_url)

    def _on_title_changed(self, title: str):
        webview = self.sender()
        index = self.tabs.indexOf(webview)
        if index >= 0:
            self.tabs.setTabText(index, title[:30])

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _setup_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&Archivo")
        exit_action = QAction("&Salir", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menubar.addMenu("&Vista")

        zoom_in = QAction("Aumentar zoom", self)
        zoom_in.setShortcut(QKeySequence.StandardKey.ZoomIn)
        zoom_in.triggered.connect(self._zoom_in)
        view_menu.addAction(zoom_in)

        zoom_out = QAction("Disminuir zoom", self)
        zoom_out.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_out.triggered.connect(self._zoom_out)
        view_menu.addAction(zoom_out)

        reset_zoom = QAction("Resetear zoom", self)
        reset_zoom.setShortcut("Ctrl+0")
        reset_zoom.triggered.connect(self._reset_zoom)
        view_menu.addAction(reset_zoom)

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    _WINDOWS_PATH_RE = re.compile(r"^[a-zA-Z]:[\\/]")

    def _looks_like_local_path(self, text: str) -> bool:
        return (
            text.startswith("file://")
            or bool(self._WINDOWS_PATH_RE.match(text))
            or text.startswith("/")
            or text.startswith("~")
        )

    def load_url(self, url: str):
        text = url.strip()
        if not text:
            return
        if self._looks_like_local_path(text):
            local_path = QUrl(text).toLocalFile() if text.startswith("file://") else os.path.expanduser(text)
            self.current_webview().setUrl(QUrl.fromLocalFile(local_path))
            return
        if not text.startswith(("http://", "https://", "file://")):
            text = f"https://{text}"
        self.current_webview().setUrl(QUrl(text))

    def go_home(self):
        webview = self.current_webview()
        data = self.tab_data.get(id(webview), {})
        profile_data = self.profile_manager.get_profile(data.get("profile_id", self.current_profile_id))
        if profile_data:
            self.load_url(profile_data["home_url"])

    def _open_current_folder(self):
        """Abre (como file://) la carpeta de descarga que se usaría ahora
        mismo: la de la Colección activa, o si no, la del perfil de la pestaña.
        Se abre en una pestaña NUEVA, con el mismo perfil y Colección que la
        pestaña actual."""
        webview = self.current_webview()
        tab_meta = self.tab_data.get(id(webview), {}) if webview else {}
        folder, _ = self._resolve_target_folder(tab_meta)

        Path(folder).mkdir(parents=True, exist_ok=True)
        file_url = QUrl.fromLocalFile(folder).toString()

        new_webview = self._add_tab(
            profile_id=tab_meta.get("profile_id", self.current_profile_id),
            collection_id=tab_meta.get("collection_id"),
        )
        new_webview.setUrl(QUrl(file_url))

    def handle_special_local_file(self, tab, local_path):
        ext = os.path.splitext(local_path)[1].lower()
        if ext == ".pdf":
            self.open_pdf_tab(local_path)
            return
        if ext in VIDEO_EXTS:
            self.open_video_tab(local_path)
            return
        self._open_local_target(tab, local_path)

    def open_pdf_tab(self, path):
        tab = PdfTab(path, self)
        title = os.path.basename(path)
        index = self.tabs.addTab(tab, title[:30] or "PDF")
        self.tabs.setCurrentIndex(index)
        return tab

    def open_video_tab(self, path):
        tab = VideoTab(path, self)
        title = os.path.basename(path)
        index = self.tabs.addTab(tab, title[:30] or "Video")
        self.tabs.setCurrentIndex(index)
        return tab

    def _open_local_target(self, tab, local_path):
        ext = os.path.splitext(local_path)[1].lower()
        cache_dir = self.profile_manager.base_dir / "archives_cache"
        try:
            if ext == ".zip":
                tab.setUrl(QUrl.fromLocalFile(local_viewer.extract_zip(local_path, cache_dir)))
                return
            if ext == ".7z":
                dest = local_viewer.extract_7z(local_path, cache_dir)
                if dest is None:
                    tab.page().setHtml(
                        local_viewer.render_missing_dependency(local_path, "py7zr"),
                        QUrl.fromLocalFile(local_path),
                    )
                else:
                    tab.setUrl(QUrl.fromLocalFile(dest))
                return
            if ext == ".rar":
                try:
                    html = local_viewer.render_rar_listing(local_path)
                except ImportError:
                    html = local_viewer.render_missing_dependency(
                        local_path,
                        "rarfile",
                        "Además necesitás tener instalado unrar o unar en el sistema para leer el archivo.",
                    )
                tab.page().setHtml(html, QUrl.fromLocalFile(local_path))
                return
            if ext == ".epub":
                tab.setUrl(QUrl.fromLocalFile(local_viewer.extract_epub_root(local_path, cache_dir)))
                return
        except Exception as exc:
            tab.page().setHtml(
                local_viewer.render_error(local_path, f"Error al procesar el archivo: {exc}"),
                QUrl.fromLocalFile(local_path),
            )

    # ------------------------------------------------------------------
    # Vista custom de carpetas (file:// -> HTML propio con commits git)
    # ------------------------------------------------------------------

    def _render_folder_view(self, page, folder_path: str):
        """Callback pasado a CustomWebEnginePage: en vez del listado
        nativo de Chromium, renderiza una página propia con el contenido
        de la carpeta a la izquierda y los commits de git (si es un
        repo) a la derecha. Se llama automáticamente cada vez que se
        navega a un file:// que apunta a una carpeta (incluida la
        navegación inicial y los clicks en subcarpetas dentro de la
        misma vista)."""
        html_content = self._build_folder_html(folder_path)
        base_url = QUrl.fromLocalFile(str(Path(folder_path)) + "/")
        page.setHtml(html_content, base_url)

    def _build_folder_html(self, folder_path: str) -> str:
        esc = html_escape_module.escape
        folder = Path(folder_path)

        entries = []
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            pass

        rows = []
        if folder.parent != folder:
            parent_url = QUrl.fromLocalFile(str(folder.parent) + "/").toString()
            rows.append(f'<a class="entry dir" href="{esc(parent_url)}">⬆ .. (subir un nivel)</a>')

        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                url = QUrl.fromLocalFile(str(entry) + "/").toString()
                rows.append(f'<a class="entry dir" href="{esc(url)}">📁 {esc(entry.name)}</a>')
            else:
                url = QUrl.fromLocalFile(str(entry)).toString()
                try:
                    size_kb = entry.stat().st_size / 1024
                    size_txt = f"{size_kb:,.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:,.1f} MB"
                except OSError:
                    size_txt = ""
                rows.append(
                    f'<a class="entry file" href="{esc(url)}">📄 {esc(entry.name)}'
                    f'<span class="size">{esc(size_txt)}</span></a>'
                )

        files_html = "\n".join(rows) if rows else '<p class="empty">Carpeta vacía</p>'

        has_git = GitVersioning.has_repo(folder_path)
        if has_git:
            commits = GitVersioning.get_log(folder_path, limit=50)
            if commits:
                commit_rows = "\n".join(
                    f'<div class="commit">{esc(c)}</div>' for c in commits
                )
                git_html = f'<div class="git-status">🔀 {len(commits)} commit(s)</div>{commit_rows}'
            else:
                git_html = '<div class="git-status">🔀 Repositorio git (sin commits todavía)</div>'
        else:
            git_html = '<div class="git-status muted">🔀 Esta carpeta no es un repositorio git</div>'

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0;
    font-family: -apple-system, "Segoe UI", Arial, sans-serif;
    background: #1e1e1e; color: #e6e6e6;
  }}
  header {{
    padding: 14px 20px; border-bottom: 1px solid #3a3a3a;
    font-size: 14px; color: #a0a0a0; word-break: break-all;
  }}
  .columns {{ display: flex; height: calc(100vh - 52px); }}
  .col {{ width: 50%; overflow-y: auto; padding: 12px 16px; }}
  .col-left {{ border-right: 1px solid #3a3a3a; }}
  .col h2 {{
    font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
    color: #888; margin: 0 0 10px 0;
  }}
  a.entry {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 7px 10px; border-radius: 6px; color: #e6e6e6;
    text-decoration: none; font-size: 13.5px;
  }}
  a.entry:hover {{ background: #2c2c2c; }}
  a.entry.dir {{ font-weight: 600; }}
  .size {{ color: #888; font-size: 11.5px; margin-left: 12px; white-space: nowrap; }}
  .empty {{ color: #777; font-size: 13px; padding: 8px 10px; }}
  .git-status {{ font-size: 12.5px; color: #a0a0a0; margin-bottom: 10px; }}
  .git-status.muted {{ color: #666; }}
  .commit {{
    font-size: 12.5px; font-family: "SF Mono", Consolas, monospace;
    padding: 6px 10px; border-radius: 6px; color: #d0d0d0;
  }}
  .commit:hover {{ background: #2c2c2c; }}
</style>
</head>
<body>
  <header>📁 {esc(str(folder))}</header>
  <div class="columns">
    <div class="col col-left">
      <h2>Contenido</h2>
      {files_html}
    </div>
    <div class="col col-right">
      <h2>Historial de Git</h2>
      {git_html}
    </div>
  </div>
</body>
</html>"""

    def _resolve_target_folder(self, tab_meta: dict) -> tuple[str, str]:
        """Devuelve (carpeta, etiqueta) según: primero la Colección de la
        pestaña (si tiene carpeta propia), sino la carpeta del perfil."""
        collection_id = tab_meta.get("collection_id")
        if collection_id:
            collection = self.collection_manager.get_collection(collection_id)
            if collection and collection.get("download_dir"):
                return collection["download_dir"], f"Colección '{collection['name']}'"

        profile_id = tab_meta.get("profile_id", self.current_profile_id)
        profile_data = self.profile_manager.get_profile(profile_id)
        if profile_data:
            return profile_data["files_dir"], f"perfil '{profile_data['name']}'"
        return str(Path.home()), "carpeta personal"

    def _copy_file_to_folder(self, filepath: str, target_dir: str, context_label: str, silent: bool = False):
        src = Path(filepath)
        if not src.exists():
            return
        try:
            Path(target_dir).mkdir(parents=True, exist_ok=True)
            dest = Path(target_dir) / src.name
            shutil.copy2(src, dest)
            if not silent:
                self.statusBar().showMessage(
                    f"📎 {src.name} copiado a {context_label} ({target_dir})", 6000
                )
        except Exception as e:
            self.statusBar().showMessage(f"⚠ No se pudo copiar {src.name}: {e}", 8000)

    def _attach_to_chat_dialog(self):
        """Copia el archivo a la carpeta activa Y además intenta
        'pegarlo' en la página actual (Ctrl+V), por si el chat soporta
        adjuntar archivos por portapapeles. No hay forma pública de
        simular un drag&drop real dentro del contenido web con
        QtWebEngine (Chromium lo maneja internamente), así que esto usa
        un paste real como mejor alternativa disponible."""
        webview = self.current_webview()
        tab_meta = self.tab_data.get(id(webview), {}) if webview else {}
        target_dir, context_label = self._resolve_target_folder(tab_meta)

        filepath, _ = QFileDialog.getOpenFileName(
            self, f"Adjuntar archivo (se copia a {context_label})", str(Path.home())
        )
        if not filepath:
            return

        self._copy_file_to_folder(filepath, target_dir, context_label)
        self._attach_file_to_chat(filepath)

    def _attach_file_to_chat(self, filepath: str):
        """Pone el archivo en el portapapeles del sistema y simula un
        Ctrl+V real (a través del pipeline de eventos de Qt/Chromium,
        no de JavaScript) sobre la pestaña activa."""
        webview = self.current_webview()
        if not webview:
            return

        file_mime = QMimeData()
        file_mime.setUrls([QUrl.fromLocalFile(filepath)])
        QApplication.clipboard().setMimeData(file_mime)

        target = webview.focusProxy() or webview
        target.setFocus()

        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        QApplication.sendEvent(target, press)
        QApplication.sendEvent(target, release)

        self.statusBar().showMessage(
            "📋 Intentando pegar el archivo en el chat (Ctrl+V). Si no aparece adjuntado, "
            "el sitio no soporta pegar archivos desde el portapapeles — el archivo ya está "
            "copiado en la carpeta (usá 📁 para abrirla y subirlo manualmente desde el chat).",
            10000,
        )

    # ------------------------------------------------------------------
    # Perfiles: UI
    # ------------------------------------------------------------------

    def _load_profiles_list(self):
        """Repuebla el combo de perfiles sin disparar el cambio de activo."""
        self.profiles_combo.blockSignals(True)
        self.profiles_combo.clear()
        for p in self.profile_manager.profiles:
            self.profiles_combo.addItem(p["name"], p["id"])
        self.profiles_combo.blockSignals(False)
        self._highlight_active_profile()

    def _highlight_active_profile(self):
        index = self.profiles_combo.findData(self.current_profile_id)
        if index >= 0 and self.profiles_combo.currentIndex() != index:
            self.profiles_combo.blockSignals(True)
            self.profiles_combo.setCurrentIndex(index)
            self.profiles_combo.blockSignals(False)

        data = self.profile_manager.get_profile(self.current_profile_id)
        if data:
            git_txt = " · Git ✅" if data.get("git_versioning") else ""
            self.profile_info_label.setText(f"Archivos: {data['files_dir']}{git_txt}")
            self.setWindowTitle(f"IA Browser — {data['name']}")

    def _on_profile_combo_changed(self, index: int):
        """Al elegir un perfil del combo, abre una PESTAÑA NUEVA con ese
        perfil (no cierra las pestañas existentes)."""
        profile_id = self.profiles_combo.itemData(index)
        if not profile_id or profile_id == self.current_profile_id:
            return
        self._open_new_tab_with_profile(profile_id)

    def _manage_profile_menu(self):
        """Menú de gestión (nuevo, renombrar, carpeta, Git, eliminar) para
        el perfil seleccionado en el combo."""
        profile_id = self.profiles_combo.currentData()
        data = self.profile_manager.get_profile(profile_id)
        if not data:
            return
        git_on = bool(data.get("git_versioning"))

        menu = QMenu(self)
        new_action = menu.addAction("+ Nuevo perfil...")
        menu.addSeparator()
        rename_action = menu.addAction("Cambiar nombre...")
        home_action = menu.addAction("Cambiar página de inicio...")
        change_folder_action = menu.addAction("Cambiar carpeta de archivos...")
        git_action = menu.addAction(
            "✅ Git: sobrescribir (activado)" if git_on else "☐ Git: sobrescribir (desactivado)"
        )
        codex_action = menu.addAction("🤖 Codex Manager...")
        delete_action = menu.addAction("Eliminar perfil")
        if data.get("is_default"):
            delete_action.setEnabled(False)
            delete_action.setToolTip("El perfil Default no se puede eliminar")

        sender = self.sender()
        action = menu.exec(sender.mapToGlobal(sender.rect().bottomLeft()))
        if action == new_action:
            self._create_profile_dialog()
        elif action == rename_action:
            self._rename_profile(profile_id)
        elif action == home_action:
            self._change_profile_home(profile_id)
        elif action == change_folder_action:
            self._change_profile_folder(profile_id)
        elif action == codex_action:
            self._open_codex_manager(data["files_dir"])
        elif action == git_action:
            self._toggle_profile_git(profile_id)
        elif action == delete_action:
            self._delete_profile(profile_id)

    def _open_codex_manager(self, folder: str):
        Path(folder).mkdir(parents=True, exist_ok=True)
        dialog = CodexManagerDialog(self, folder)
        dialog.exec()

    def _change_profile_home(self, profile_id: str):
        data = self.profile_manager.get_profile(profile_id)
        if not data:
            return
        new_home, ok = QInputDialog.getText(
            self, "Página de inicio", "URL de inicio para este perfil:", text=data["home_url"]
        )
        new_home = new_home.strip()
        if not ok or not new_home:
            return
        if not new_home.startswith(("http://", "https://")):
            new_home = f"https://{new_home}"
        self.profile_manager.update_profile(profile_id, home_url=new_home)
        self.statusBar().showMessage(f"Página de inicio de '{data['name']}' actualizada", 4000)

    def _toggle_profile_git(self, profile_id: str):
        data = self.profile_manager.get_profile(profile_id)
        if not data:
            return
        new_value = not data.get("git_versioning", False)
        if new_value and not GitVersioning.is_available():
            QMessageBox.warning(self, "Git no encontrado", "No se encontró 'git' en el sistema.")
            return
        self.profile_manager.update_profile(profile_id, git_versioning=new_value)
        if new_value:
            GitVersioning.ensure_repo(data["files_dir"])
            if not GitVersioning.check_identity(data["files_dir"]):
                self._set_git_warning(
                    "⚠ Git no tiene user.name/user.email configurados: los commits "
                    "no se van a guardar hasta que los configures."
                )
            else:
                self._clear_git_warning()
                self.statusBar().showMessage(f"Git activado para el perfil '{data['name']}'", 5000)
        else:
            self.statusBar().showMessage(f"Git desactivado para el perfil '{data['name']}'", 5000)

    def _rename_profile(self, profile_id: str):
        data = self.profile_manager.get_profile(profile_id)
        if not data:
            return
        new_name, ok = QInputDialog.getText(
            self, "Cambiar nombre", "Nuevo nombre del perfil:", text=data["name"]
        )
        new_name = new_name.strip()
        if ok and new_name:
            self.profile_manager.rename_profile(profile_id, new_name)
            self._load_profiles_list()

    def _create_profile_dialog(self):
        dialog = NewProfileDialog(self, default_dir=str(Path.home() / "Downloads"))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, files_dir, git_versioning = dialog.get_values()
            if not name:
                QMessageBox.warning(self, "Aviso", "El perfil necesita un nombre")
                return
            if not files_dir:
                files_dir = str(Path.home() / "Downloads")
            if git_versioning and not GitVersioning.is_available():
                QMessageBox.warning(
                    self, "Git no encontrado",
                    "No se encontró 'git' en el sistema. Se creará el perfil "
                    "igual, pero sin versionado hasta que instales git.",
                )

            new_profile = self.profile_manager.create_profile(name, files_dir, git_versioning)
            self._load_profiles_list()
            self._open_new_tab_with_profile(new_profile["id"])
            if git_versioning and not GitVersioning.check_identity(files_dir):
                self._set_git_warning(
                    "⚠ Git no tiene user.name/user.email configurados: los commits "
                    "no se van a guardar hasta que los configures."
                )

    def _change_profile_folder(self, profile_id: str):
        data = self.profile_manager.get_profile(profile_id)
        if not data:
            return
        directory = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta de archivos", data["files_dir"]
        )
        if directory:
            self.profile_manager.update_profile(profile_id, files_dir=directory)
            qt_profile = self.web_engine_profiles.get(profile_id)
            if qt_profile:
                try:
                    qt_profile.setDownloadPath(directory)
                except AttributeError:
                    pass
            self._highlight_active_profile()

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

        # Cerrar las pestañas que usaban el perfil eliminado (ya no es válido)
        for i in reversed(range(self.tabs.count())):
            widget = self.tabs.widget(i)
            meta = self.tab_data.get(id(widget), {})
            if meta.get("profile_id") == profile_id:
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
            self.current_profile_id = meta.get("profile_id", self.profile_manager.get_default_profile_id())
            self._highlight_active_profile()

    # ------------------------------------------------------------------
    # Colecciones: UI
    # ------------------------------------------------------------------

    def _load_collections_list(self):
        """Repuebla el árbol de Colecciones: cada Colección es un nodo
        desplegable con sus marcadores como hijos, más un último hijo
        para acceder/asignar su carpeta de descarga."""
        expanded_ids = {
            self.collections_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)["id"]
            for i in range(self.collections_tree.topLevelItemCount())
            if self.collections_tree.topLevelItem(i).isExpanded()
        }

        self.collections_tree.clear()
        for c in self.collection_manager.collections:
            top = QTreeWidgetItem([f"🗂 {c['name']} ({len(c['items'])})"])
            top.setData(0, Qt.ItemDataRole.UserRole, {"type": "collection", "id": c["id"]})

            for it in c["items"]:
                profile = self.profile_manager.get_profile(it["profile_id"])
                pname = profile["name"] if profile else "?"
                child = QTreeWidgetItem([f"⭐ {it['title']}  —  [{pname}]"])
                child.setData(0, Qt.ItemDataRole.UserRole, {
                    "type": "bookmark", "collection_id": c["id"],
                    "url": it["url"], "profile_id": it["profile_id"],
                })
                top.addChild(child)

            download_dir = c.get("download_dir")
            folder_label = f"📁 Carpeta: {Path(download_dir).name}" if download_dir else "📁 Asignar carpeta de descarga..."
            folder_child = QTreeWidgetItem([folder_label])
            folder_child.setData(0, Qt.ItemDataRole.UserRole, {"type": "folder", "collection_id": c["id"]})
            f = folder_child.font(0)
            f.setItalic(True)
            folder_child.setFont(0, f)
            top.addChild(folder_child)

            self.collections_tree.addTopLevelItem(top)
            top.setExpanded(c["id"] in expanded_ids)

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
        if not webview or not self.current_url:
            QMessageBox.warning(self, "Aviso", "No hay página cargada")
            return

        title = self.tabs.tabText(self.tabs.currentIndex())
        data = self.tab_data.get(id(webview), {})
        profile_id = data.get("profile_id", self.current_profile_id)

        dialog = SaveToCollectionDialog(self, self.collection_manager.collections)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        collection_id, new_name = dialog.get_values()
        if collection_id is None:
            if not new_name:
                QMessageBox.warning(self, "Aviso", "La Colección necesita un nombre")
                return
            collection = self.collection_manager.create_collection(new_name)
            collection_id = collection["id"]

        self.collection_manager.add_item(collection_id, self.current_url, title, profile_id)
        self._load_collections_list()
        self.statusBar().showMessage("Página agregada a la Colección", 4000)

    def _on_collection_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Maneja los 3 tipos de nodo del árbol: Colección (expande/
        colapsa), marcador (selecciona la pestaña si ya está abierta, o
        abre una nueva) o carpeta (abre/asigna la carpeta de descarga)."""
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
            webview = self._add_tab(profile_id=data["profile_id"], collection_id=data["collection_id"])
            webview.setUrl(QUrl(data["url"]))
        elif kind == "folder":
            self._open_collection_folder(data["collection_id"])

    def _find_tab_index(self, url: str, profile_id: str) -> int | None:
        """Busca una pestaña ya abierta con esa URL exacta y ese perfil.
        Devuelve su índice, o None si no hay ninguna."""
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            meta = self.tab_data.get(id(widget), {})
            if meta.get("profile_id") == profile_id and widget.url().toString() == url:
                return i
        return None

    def _open_collection_folder(self, collection_id: str):
        """Abre la carpeta de descarga de la Colección en una pestaña
        nueva (file://). Si todavía no tiene una asignada, la pide."""
        collection = self.collection_manager.get_collection(collection_id)
        if not collection:
            return

        download_dir = collection.get("download_dir")
        if not download_dir:
            directory = QFileDialog.getExistingDirectory(
                self, "Carpeta de descarga de la Colección", str(Path.home())
            )
            if not directory:
                return
            self.collection_manager.set_download_dir(collection_id, directory)
            self._load_collections_list()
            download_dir = directory

        Path(download_dir).mkdir(parents=True, exist_ok=True)
        file_url = QUrl.fromLocalFile(download_dir).toString()
        webview = self._add_tab(profile_id=self.current_profile_id, collection_id=collection_id)
        webview.setUrl(QUrl(file_url))

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
            rename_action = menu.addAction("Cambiar nombre...")
            folder_action = menu.addAction("Carpeta de descarga...")
            git_action = menu.addAction(
                "✅ Git: sobrescribir (activado)" if git_on else "☐ Git: sobrescribir (desactivado)"
            )
            codex_action = menu.addAction("🤖 Codex Manager...")
            if not (collection and collection.get("download_dir")):
                codex_action.setEnabled(False)
                codex_action.setToolTip("Asigná primero una carpeta de descarga a esta Colección")
            delete_action = menu.addAction("Eliminar Colección")

            action = menu.exec(self.collections_tree.mapToGlobal(pos))
            if action == rename_action:
                self._rename_collection(collection_id)
            elif action == folder_action:
                self._pick_collection_folder(collection_id)
            elif action == codex_action:
                self._open_codex_manager(collection["download_dir"])
            elif action == git_action:
                self._toggle_collection_git(collection_id)
            elif action == delete_action:
                self._delete_collection(collection_id)

        elif kind == "bookmark":
            menu = QMenu(self)
            remove_action = menu.addAction("Quitar de la Colección")
            action = menu.exec(self.collections_tree.mapToGlobal(pos))
            if action == remove_action:
                self.collection_manager.remove_item(data["collection_id"], data["url"])
                self._load_collections_list()

        elif kind == "folder":
            menu = QMenu(self)
            change_action = menu.addAction("Cambiar carpeta de descarga...")
            action = menu.exec(self.collections_tree.mapToGlobal(pos))
            if action == change_action:
                self._pick_collection_folder(data["collection_id"])

    def _toggle_collection_git(self, collection_id: str):
        collection = self.collection_manager.get_collection(collection_id)
        if not collection:
            return
        new_value = not collection.get("git_versioning", False)
        if new_value and not GitVersioning.is_available():
            QMessageBox.warning(self, "Git no encontrado", "No se encontró 'git' en el sistema.")
            return
        self.collection_manager.set_git_versioning(collection_id, new_value)
        if new_value and collection.get("download_dir"):
            if not GitVersioning.check_identity(collection["download_dir"]):
                self._set_git_warning(
                    "⚠ Git no tiene user.name/user.email configurados: los commits "
                    "no se van a guardar hasta que los configures."
                )
            else:
                self._clear_git_warning()
                self.statusBar().showMessage(f"Git activado para la Colección '{collection['name']}'", 5000)
        elif new_value:
            self.statusBar().showMessage(
                f"Git activado para '{collection['name']}' — asignale una carpeta de descarga", 6000
            )
        else:
            self.statusBar().showMessage(f"Git desactivado para la Colección '{collection['name']}'", 5000)

    def _rename_collection(self, collection_id: str):
        collection = self.collection_manager.get_collection(collection_id)
        if not collection:
            return
        new_name, ok = QInputDialog.getText(
            self, "Cambiar nombre", "Nuevo nombre de la Colección:", text=collection["name"]
        )
        new_name = new_name.strip()
        if ok and new_name:
            self.collection_manager.rename_collection(collection_id, new_name)
            self._load_collections_list()

    def _pick_collection_folder(self, collection_id: str):
        collection = self.collection_manager.get_collection(collection_id)
        if not collection:
            return
        directory = QFileDialog.getExistingDirectory(
            self, "Carpeta de descarga de la Colección", collection.get("download_dir") or str(Path.home())
        )
        if directory:
            self.collection_manager.set_download_dir(collection_id, directory)
            self._load_collections_list()

    def _delete_collection(self, collection_id: str):
        collection = self.collection_manager.get_collection(collection_id)
        if not collection:
            return
        confirm = QMessageBox.question(self, "Eliminar Colección", f"¿Eliminar la Colección '{collection['name']}'?")
        if confirm == QMessageBox.StandardButton.Yes:
            self.collection_manager.delete_collection(collection_id)
            self._load_collections_list()

    # ------------------------------------------------------------------
    # Zoom (persistido en el perfil de la pestaña activa)
    # ------------------------------------------------------------------

    def _active_profile_id_for_zoom(self) -> str:
        webview = self.current_webview()
        data = self.tab_data.get(id(webview), {})
        return data.get("profile_id", self.current_profile_id)

    def _zoom_in(self):
        factor = self.current_webview().zoomFactor() + 0.1
        self.current_webview().setZoomFactor(factor)
        self.profile_manager.update_profile(self._active_profile_id_for_zoom(), zoom=factor)

    def _zoom_out(self):
        factor = self.current_webview().zoomFactor() - 0.1
        self.current_webview().setZoomFactor(factor)
        self.profile_manager.update_profile(self._active_profile_id_for_zoom(), zoom=factor)

    def _reset_zoom(self):
        self.current_webview().setZoomFactor(1.0)
        self.profile_manager.update_profile(self._active_profile_id_for_zoom(), zoom=1.0)

    def closeEvent(self, event):
        self._save_session()
        self.profile_manager.save_profiles()
        self.collection_manager.save_themes()
        event.accept()

    # ------------------------------------------------------------------
    # Sesión: recordar las últimas pestañas abiertas
    # ------------------------------------------------------------------

    def _save_session(self):
        try:
            save_tab_session(
                self.session_file,
                self.tabs,
                metadata_for_widget=lambda widget: {
                    "profile_id": self.tab_data.get(id(widget), {}).get("profile_id", self.current_profile_id),
                    "collection_id": self.tab_data.get(id(widget), {}).get("collection_id"),
                },
                extra={"active_profile_id": self.current_profile_id},
            )
        except Exception:
            pass

    def _load_session(self) -> dict | None:
        return load_tab_session(self.session_file) or None

    def _restore_session_or_default(self) -> bool:
        """Reabre las pestañas de la última sesión (con su perfil y Colección
        originales). Devuelve True si pudo restaurar algo."""
        session = self._load_session()
        if not session or not session.get("tabs"):
            return False

        valid_tabs = [
            t for t in session["tabs"]
            if self.profile_manager.get_profile(t.get("profile_id"))
        ]
        if not valid_tabs:
            return False

        for t in valid_tabs:
            webview = self._add_tab(profile_id=t["profile_id"], collection_id=t.get("collection_id"))
            webview.setUrl(QUrl(t["url"]))

        active = session.get("active_profile_id")
        if active and self.profile_manager.get_profile(active):
            self.current_profile_id = active
        else:
            self.current_profile_id = valid_tabs[-1]["profile_id"]

        active_index = session.get("active_index")
        if isinstance(active_index, int) and self.tabs.count() > 0:
            self.tabs.setCurrentIndex(max(0, min(active_index, self.tabs.count() - 1)))
            active_widget = self.current_webview()
            active_meta = self.tab_data.get(id(active_widget), {})
            self.current_profile_id = active_meta.get("profile_id", self.current_profile_id)

        data = self.profile_manager.get_profile(self.current_profile_id)
        if data:
            self.current_webview().setZoomFactor(data.get("zoom", 1.0))
        self._highlight_active_profile()
        self.statusBar().showMessage(f"Sesión restaurada: {len(valid_tabs)} pestaña(s)", 5000)
        return True


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("IA Browser")
    app.setApplicationVersion("4.0")

    window = IABrowser()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()


