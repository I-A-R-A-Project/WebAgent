"""
window.py - ventana principal de IA Browser.

Navegador con pestañas, perfiles aislados (sesión/cookies/cache propios) y
"Colecciones" (grupos de marcadores multi-perfil con carpeta de descarga propia).

El punto de entrada es main.py.
"""

import sys
import os
import shutil
from pathlib import Path
from urllib.parse import parse_qs

# Qt WebEngine exposes Chromium DevTools through this local endpoint.
os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9222")

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
from web_common.json_store import SidebarAppsStore
from web_common.navbar import BasicNavbar, address_to_url, bind_navigation, save_web_page
from web_common.session import (
    load_tab_session,
    restore_tab_metadata,
    SessionAutoSaver,
    save_tab_session,
)
from web_common.sidebar import AppPanelOverlay, SidebarContainer, SidebarRail
from web_common import local_viewer
from web_common.downloader_handoff import handoff_url_to_downloader
from web_common.tabs import (
    VIDEO_EXTS, UnifiedWebTab, ContextTabBar, TabbedPopupWindow,
    add_plus_tab, install_tab_context_menu, keep_plus_tab_last,
    update_tab_icon, update_tab_title,
)
from web_common.media_tabs import open_video_tab as add_video_tab
from web_common.video_tab import VideoTab
from web_common.epub_tab import EpubTab
from web_common import folder_viewer
from web_common.web_profiles import build_web_profile
from ai_manager import AIAgentsDialog
from scripts.website_tools.dialogs import WebsiteToolsDialog
from new_tab_page import render_new_tab_page
from task_manager import TaskManager
from agent_console import AgentConsolePanel


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
        self.session_autosaver = SessionAutoSaver(self._save_session)

        # Metadata por pestaña: id(webview) -> {"profile_id":.., "collection_id": .. or None}
        self.tab_data: dict[int, dict] = {}
        self.current_url = ""

        # Mantiene vivos los diálogos de descarga en curso (no modales)
        # para que no se destruyan mientras la descarga sigue en background.
        self._download_dialogs = []
        self._agent_dialogs = []
        self._devtools_windows = []

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
            download_path=self.profile_manager.get_files_dir(),
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

        if git_versioning and GitVersioning.is_available():
            switched, branch = GitVersioning.ensure_profile_branch(target_dir, profile_id, raw=True)
            if not switched:
                QMessageBox.warning(
                    self,
                    "Carpeta ocupada",
                    "No se puede cambiar a la rama segura del perfil porque hay "
                    "cambios sin commitear. Commiteá o descartá esos cambios antes "
                    "de iniciar otra descarga.",
                )
                return
            self.statusBar().showMessage(f"Descarga aislada en rama {branch}", 5000)

        dialog = DownloadDialog(
            self, download, target_dir, filename,
            git_versioning=git_versioning and GitVersioning.is_available(),
            on_finished_callback=lambda directory, name, was_extracted, uses_git,
                pid=profile_id: self._handle_download_finished(
                    directory, name, was_extracted, uses_git, pid
                ),
        )
        self._download_dialogs.append(dialog)
        dialog.finished.connect(lambda _r=None, d=dialog: self._forget_download_dialog(d))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forget_download_dialog(self, dialog):
        if dialog in self._download_dialogs:
            self._download_dialogs.remove(dialog)

    def _handle_download_finished(
        self, target_dir: str, filename: str, extracted: bool,
        git_versioning: bool, profile_id: str | None = None,
    ):
        """Callback del DownloadDialog al terminar: se encarga del commit
        con git si corresponde (un commit por archivo, o 'add -A' si se
        extrajo un comprimido y aparecieron varios archivos nuevos)."""
        if not git_versioning or not GitVersioning.is_available():
            self.statusBar().showMessage(f"✅ Descarga completa: {filename}", 6000)
            return

        GitVersioning.ensure_repo(target_dir)
        if profile_id:
            switched, branch = GitVersioning.ensure_profile_branch(
                target_dir, profile_id, raw=True
            )
            if not switched:
                self._set_git_warning(
                    "⚠ No se pudo volver a la rama segura del perfil; "
                    "el commit fue cancelado para evitar mezclar cambios."
                )
                return
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

        self._add_tab()
        if open_home:
            self.load_url(data["home_url"])
        self.current_webview().setZoomFactor(data.get("zoom", 1.0))
        self._highlight_active_profile()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self):
        content_widget = QWidget()
        main_layout = QVBoxLayout(content_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        content_row = QWidget()
        row_layout = QHBoxLayout(content_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        self._setup_sidebar(row_layout)
        self._setup_tabs_and_navbar(row_layout)
        main_layout.addWidget(content_row, 1)
        self.console_toggle = QPushButton("⌄ Consola de agentes")
        self.console_toggle.clicked.connect(self._toggle_agent_console)
        main_layout.addWidget(self.console_toggle, 0)
        self.agent_console = AgentConsolePanel(
            self,
            lambda: self.current_profile_id,
            lambda: self.profile_manager.get_files_dir(),
            auth_url_handler=self._open_copilot_auth_url,
        )
        main_layout.addWidget(self.agent_console, 0)

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
        webview = self._add_tab()
        request.openIn(webview.page())

    def _setup_sidebar(self, parent_layout):
        """Sidebar con Perfiles y Colecciones."""
        sidebar_layout = QVBoxLayout()

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
        self.tabs.setTabBar(ContextTabBar(self.tabs))
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self._setup_plus_tab()
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabBarClicked.connect(self._on_tab_bar_clicked)
        self.tabs.tabBar().tabMoved.connect(self._on_tab_moved)
        install_tab_context_menu(
            self.tabs,
            close_tab=self._close_tab,
            plus_widget=self.plus_widget,
            direct_right_click=True,
        )
        right_layout.addWidget(self.tabs)
        right_container = QWidget()
        right_container.setLayout(right_layout)
        parent_layout.addWidget(right_container, 1)

    def _toggle_agent_console(self):
        visible = not self.agent_console.isVisible()
        self.agent_console.setVisible(visible)
        self.console_toggle.setText("⌃ Ocultar consola" if visible else "⌄ Consola de agentes")

    def _create_navbar(self) -> QToolBar:
        navbar = BasicNavbar(self)
        bind_navigation(
            navbar,
            self.current_webview,
            address_handler=self._on_address_bar_enter,
            save_handler=lambda: save_web_page(
                self.current_webview(),
                target_dir=self.profile_manager.base_dir / "saved_pages",
                status_callback=self.statusBar().showMessage,
            ),
        )

        # Guardar referencias
        self.address_bar = navbar.address_bar

        send_action = QAction("⬇", navbar)
        send_action.setToolTip("Enviar URL actual al Downloader")
        send_action.triggered.connect(self.send_current_url_to_downloader)
        navbar.addAction(send_action)
        
        # Agregar botones específicos de IA
        self.collection_action = QAction("☆", navbar)
        self.collection_action.setToolTip("Guardar página en Colección")
        self.collection_action.triggered.connect(self._save_current_to_collection)
        navbar.addAction(self.collection_action)

        open_files_action = QAction("📁", navbar)
        open_files_action.setToolTip("Abrir carpeta de archivos activa")
        open_files_action.triggered.connect(self._open_current_folder)
        navbar.addAction(open_files_action)

        attach_action = QAction("📎", navbar)
        attach_action.setToolTip(
            "Copia el archivo a la carpeta de la Colección activa (o del perfil) y\n"
            "además intenta pegarlo (Ctrl+V) en el chat actual. Hacé clic en\n"
            "el cuadro de texto del chat ANTES de usar este botón. No funciona\n"
            "en todos los sitios (depende de que soporten pegar archivos)."
        )
        attach_action.triggered.connect(self._attach_to_chat_dialog)
        navbar.addAction(attach_action)

        return navbar

    def send_current_url_to_downloader(self):
        webview = self.current_webview()
        tab_meta = self.tab_data.get(id(webview), {}) if webview else {}
        folder, _ = self._resolve_target_folder(tab_meta)
        handoff_url_to_downloader(
            webview.url().toString() if webview else "",
            __file__,
            path=folder,
            title=webview.title() if webview else "",
            status_callback=self.statusBar().showMessage,
        )

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _add_tab(self, profile_id: str | None = None, collection_id: str | None = None):
        """Agrega una pestaña usando el perfil indicado o Default."""
        profile_id = profile_id or self.profile_manager.get_default_profile_id()
        qt_profile = self._get_qt_profile(profile_id)

        webview = UnifiedWebTab(
            qt_profile,
            parent_window=self,
            folder_view_handler=self._render_folder_view,
            file_view_handler=self._render_file_view,
            new_tab_handler=self._handle_new_tab_request,
            new_window_handler=self._handle_new_window_request,
            url_changed_handler=self._on_tab_url_changed,
            title_changed_handler=self._on_tab_title_changed,
            icon_changed_handler=self._on_tab_icon_changed,
            special_local_handler=self.handle_special_local_file,
        )

        self.tab_data[id(webview)] = {"profile_id": profile_id, "collection_id": collection_id}

        insert_at = self.tabs.indexOf(self.plus_widget)
        tab_index = self.tabs.insertTab(insert_at, webview, "Nueva pestaña")
        self.tabs.setCurrentIndex(tab_index)
        task_manager = TaskManager(profile_id)
        webview.page().setHtml(
            render_new_tab_page(task_manager.tasks),
            QUrl("about:blank"),
        )
        self.session_autosaver.schedule()
        return webview

    def _handle_new_tab_request(self):
        return self._add_tab().page()

    def _setup_plus_tab(self):
        self.plus_widget = add_plus_tab(self.tabs)

    def _on_tab_bar_clicked(self, index: int):
        if self.tabs.widget(index) is self.plus_widget:
            self._open_new_default_tab()

    def _on_tab_moved(self, from_index: int, to_index: int):
        keep_plus_tab_last(self.tabs, self.plus_widget)

    def _on_tab_url_changed(self, webview, url: QUrl):
        fragment = url.fragment()
        if fragment.startswith("cancel:"):
            task_id = fragment.split(":", 1)[1]
            profile_id = self.tab_data.get(id(webview), {}).get("profile_id", self.current_profile_id)
            manager = TaskManager(profile_id)
            task = next((item for item in manager.tasks if item.get("id") == task_id), None)
            changed = (
                manager.delete(task_id)
                if task and task.get("status") == "cancelled"
                else manager.cancel(task_id)
            )
            if changed:
                webview.page().setHtml(render_new_tab_page(manager.tasks), QUrl("about:blank"))
                self.statusBar().showMessage(
                    "Tarea eliminada"
                    if task and task.get("status") == "cancelled"
                    else "Tarea cancelada",
                    3000,
                )
            return
        if fragment.startswith("copilot:"):
            task_id = fragment.split(":", 1)[1]
            profile_id = self.tab_data.get(id(webview), {}).get("profile_id", self.current_profile_id)
            manager = TaskManager(profile_id)
            task = next((item for item in manager.tasks if item["id"] == task_id), None)
            profile = self.profile_manager.get_profile(profile_id)
            if task and profile:
                self.agent_console.agent_combo.setCurrentIndex(
                    self.agent_console.agent_combo.findData("copilot")
                )
                self.agent_console.task_edit.setText(task["text"])
                self.agent_console.setVisible(True)
                self.console_toggle.setText("⌃ Ocultar consola")
            return
        if fragment.startswith("task?"):
            text = parse_qs(fragment.split("?", 1)[1], keep_blank_values=True).get("text", [""])[0]
            profile_id = self.tab_data.get(id(webview), {}).get("profile_id", self.current_profile_id)
            manager = TaskManager(profile_id)
            task = manager.add(text.strip(), self.collection_manager.collections)
            webview.page().setHtml(render_new_tab_page(manager.tasks), QUrl("about:blank"))
            if not task["collection_id"]:
                answer = QMessageBox.question(self, "Nueva Colección", f"La tarea no coincide con una Colección.\n¿Crear una para «{text}»?")
                if answer == QMessageBox.StandardButton.Yes:
                    collection = self.collection_manager.create_collection(text[:60].strip() or "Nueva tarea")
                    task["collection_id"] = collection["id"]
                    task["collection_name"] = collection["name"]
                    manager.save()
                    webview.page().setHtml(render_new_tab_page(manager.tasks), QUrl("about:blank"))
            return
        if url.scheme() == "ia" and url.host() == "task":
            text = parse_qs(url.query(), keep_blank_values=True).get("text", [""])[0]
            profile_id = self.tab_data.get(id(webview), {}).get("profile_id", self.current_profile_id)
            manager = TaskManager(profile_id)
            task = manager.add(text.strip(), self.collection_manager.collections)
            api_key = os.environ.get("ANYAPI_API_KEY", "")
            if api_key:
                try:
                    task = manager.classify_with_anyapi(task, self.collection_manager.collections, api_key)
                except (OSError, ValueError, KeyError) as exc:
                    self.statusBar().showMessage(f"AnyAPI no pudo clasificar la tarea: {exc}", 6000)
            webview.page().setHtml(render_new_tab_page(manager.tasks), QUrl("about:blank"))
            if not task["collection_id"]:
                answer = QMessageBox.question(self, "Nueva Colección", f"La tarea no coincide con una Colección.\n¿Crear una para «{text}»?")
                if answer == QMessageBox.StandardButton.Yes:
                    collection = self.collection_manager.create_collection(text[:60].strip() or "Nueva tarea")
                    task["collection_id"] = collection["id"]
                    task["collection_name"] = collection["name"]
                    manager.save()
                    webview.page().setHtml(render_new_tab_page(manager.tasks), QUrl("about:blank"))
            return
        if webview is not self.current_webview():
            return
        self.current_url = url.toString()
        self.address_bar.setText("" if self.current_url == "about:blank" else self.current_url)
        self._refresh_collection_icon(self.current_url)

    def _toggle_devtools(self):
        default_id = self.profile_manager.get_default_profile_id()
        window = TabbedPopupWindow(
            self._get_qt_profile(default_id),
            folder_view_handler=self._render_folder_view,
            file_view_handler=self._render_file_view,
            special_local_handler=self.handle_special_local_file,
        )
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.setWindowTitle("Herramientas de desarrollador — Chromium")
        window.resize(1100, 760)
        window.current_view().setUrl(QUrl("http://localhost:9222"))
        self._devtools_windows.append(window)
        window.destroyed.connect(
            lambda _obj=None, item=window: (
                self._devtools_windows.remove(item)
                if item in self._devtools_windows else None
            )
        )
        window.show()
        window.raise_()
        window.activateWindow()

    def _on_tab_title_changed(self, webview, title: str):
        update_tab_title(self.tabs, webview, title)

    def _on_tab_icon_changed(self, webview, icon):
        update_tab_icon(self.tabs, webview, icon)

    def _open_new_default_tab(self):
        """Botón '+ Tab': siempre abre con el perfil Default,
        independientemente de cuál esté seleccionado en el combo."""
        return self._add_tab()

    def _close_tab(self, index: int):
        if self.tabs.widget(index) is self.plus_widget:
            return

        widget = self.tabs.widget(index)
        self.tab_data.pop(id(widget), None)
        self.tabs.removeTab(index)
        if isinstance(widget, VideoTab):
            widget.stop()
        widget.deleteLater()

        if self.tabs.count() <= 1:
            self._open_new_default_tab()
        self.session_autosaver.schedule()

    def current_webview(self) -> QWebEngineView | None:
        """Return active web tab, never the '+' placeholder widget."""
        current = self.tabs.currentWidget()
        if isinstance(current, QWebEngineView):
            return current

        for index in range(self.tabs.count() - 1, -1, -1):
            widget = self.tabs.widget(index)
            if isinstance(widget, QWebEngineView):
                return widget
        return None

    def _on_tab_changed(self, index: int):
        """Al cambiar de pestaña, sincroniza el combo de perfiles y la
        barra de dirección con la pestaña recién seleccionada."""
        if index < 0:
            return
        webview = self.tabs.widget(index)
        if webview is None or webview is self.plus_widget:
            return
        meta = self.tab_data.get(id(webview), {})
        self.current_profile_id = meta.get("profile_id", self.current_profile_id)
        self._highlight_active_profile()

        self.current_url = webview.url().toString()
        self.address_bar.setText("" if self.current_url == "about:blank" else self.current_url)
        self._refresh_collection_icon(self.current_url)
    def _on_address_bar_enter(self, text: str):
        self.load_url(text)

    def _on_url_changed(self, url: QUrl):
        webview = self.sender()
        if webview is not self.current_webview():
            return
        self.current_url = url.toString()
        self.address_bar.setText("" if self.current_url == "about:blank" else self.current_url)

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

        view_menu = menubar.addMenu("&Configuración")

        agents_action = QAction("Agentes IA...", self)
        agents_action.triggered.connect(lambda: self._open_ai_manager(self.current_profile_id))
        view_menu.addAction(agents_action)
        devtools_action = QAction("Herramientas de desarrollador", self)
        devtools_action.setShortcut("F12")
        devtools_action.triggered.connect(self._toggle_devtools)
        view_menu.addAction(devtools_action)
        view_menu.addSeparator()

        website_action = QAction("🌐 Website Tools...", self)
        website_action.setToolTip("Crawlear y analizar una URL")
        website_action.triggered.connect(self._open_website_tools)
        view_menu.addAction(website_action)

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

    def _open_website_tools(self):
        webview = self.current_webview()
        initial_url = webview.url().toString() if webview else ""
        dialog = WebsiteToolsDialog(self, initial_url=initial_url)
        self._website_tools_dialogs = getattr(self, "_website_tools_dialogs", [])
        self._website_tools_dialogs.append(dialog)
        dialog.finished.connect(lambda: self._website_tools_dialogs.remove(dialog) if dialog in self._website_tools_dialogs else None)
        dialog.show()

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def load_url(self, url: str):
        target = address_to_url(url)
        if target is None:
            return
        self.current_webview().setUrl(target)

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
        if ext in VIDEO_EXTS:
            add_video_tab(self.tabs, local_path, self, title_limit=22)
            return
        self._open_local_target(tab, local_path)

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
                dest = local_viewer.extract_rar(local_path, cache_dir)
                if dest:
                    tab.setUrl(QUrl.fromLocalFile(dest))
                    return
                tab.page().setHtml(
                    local_viewer.render_error(
                        local_path,
                        "No se pudo extraer. Instalá 7-Zip o WinRAR, "
                        "o configurá 7z/unrar/unar en el PATH.",
                    ),
                    QUrl.fromLocalFile(local_path),
                )
                return
            if ext == ".epub":
                epub = EpubTab(
                    tab.page().profile(),
                    local_path,
                    cache_dir=cache_dir,
                    parent=self,
                )
                index = self.tabs.indexOf(tab)
                metadata = self.tab_data.get(id(tab), {})
                self.tabs.removeTab(index)
                self.tab_data.pop(id(tab), None)
                self.tab_data[id(epub)] = {
                    "profile_id": metadata.get("profile_id", self.current_profile_id),
                    "collection_id": metadata.get("collection_id"),
                }
                self.tabs.insertTab(index, epub, epub.title())
                self.tabs.setCurrentIndex(index)
                tab.deleteLater()
                return
        except Exception as exc:
            tab.page().setHtml(
                local_viewer.render_error(local_path, f"Error al procesar el archivo: {exc}"),
                QUrl.fromLocalFile(local_path),
            )

    def _render_folder_view(self, page, folder_path: str):
        folder_viewer.render_folder_view(page, folder_path)

    def _render_file_view(self, page, file_path: str):
        folder_viewer.render_file_view(page, file_path)

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
            return self.profile_manager.get_files_dir(), "carpeta común de archivos"
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
        """Actualiza el estado del perfil activo sin mostrarlo en la sidebar."""
        self._highlight_active_profile()

    def _highlight_active_profile(self):
        data = self.profile_manager.get_profile(self.current_profile_id)
        if data:
            git_txt = " · Git ✅" if data.get("git_versioning") else ""
            self.setWindowTitle(f"IA Browser — {data['name']}")

    def _open_ai_manager(self, profile_id: str):
        profile = self.profile_manager.get_profile(profile_id)
        if not profile:
            return
        folder = self.profile_manager.get_files_dir()
        Path(folder).mkdir(parents=True, exist_ok=True)
        dialog = AIAgentsDialog(
            self,
            folder,
            profile_id=profile_id,
            profile_ids=[p["id"] for p in self.profile_manager.profiles],
            profile_names={p["id"]: p["name"] for p in self.profile_manager.profiles},
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

    def _on_agent_profile_changed(self, profile_id):
        if profile_id:
            self.statusBar().showMessage(
                "Perfil de configuración cambiado; las pestañas normales usan Default.",
                4000,
            )

    def _change_shared_folder(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta común de archivos",
            self.profile_manager.get_files_dir(),
        )
        if directory:
            self.profile_manager.set_files_dir(directory)
            for profile_id, qt_profile in self.web_engine_profiles.items():
                try:
                    qt_profile.setDownloadPath(directory)
                except AttributeError:
                    pass
            self._highlight_active_profile()

    def _set_shared_git(self, enabled: bool):
        if enabled and not GitVersioning.is_available():
            QMessageBox.warning(self, "Git no encontrado", "No se encontró 'git' en el sistema.")
            return
        for profile in self.profile_manager.profiles:
            profile["git_versioning"] = bool(enabled)
        self.profile_manager.save_profiles()
        if enabled:
            GitVersioning.ensure_repo(self.profile_manager.get_files_dir())
        self._highlight_active_profile()

    def _open_copilot_auth_url(self, url: str, profile_id: str, code: str = ""):
        """Open Copilot device authorization inside matching IA profile."""
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

    def _fill_copilot_code(self, webview, code: str, ok: bool):
        if not ok or not code:
            return
        script = f"""
(() => {{
  const code = {json.dumps(code)};
  const input = document.querySelector(
    'input[name="user_code"], input[id*="code" i], input[autocomplete="one-time-code"], input[type="text"]'
  );
  if (!input) return false;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype, 'value'
  ).set;
  setter.call(input, code);
  input.dispatchEvent(new Event('input', {{bubbles: true}}));
  input.dispatchEvent(new Event('change', {{bubbles: true}}));
  const form = input.form;
  const button = form?.querySelector('button[type="submit"], input[type="submit"]')
    || [...document.querySelectorAll('button')].find(item => /continue|authorize|submit/i.test(item.innerText));
  if (button) button.click();
  else if (form) form.submit();
  return true;
}})()
"""
        webview.page().runJavaScript(script)

    def _close_copilot_auth_tab(self, profile_id: str):
        for index in range(self.tabs.count() - 1, -1, -1):
            widget = self.tabs.widget(index)
            if getattr(widget, "_copilot_auth_code", None) is not None:
                meta = self.tab_data.get(id(widget), {})
                if meta.get("profile_id") == profile_id:
                    self.tabs.removeTab(index)
                    widget.deleteLater()
                    return

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
            GitVersioning.ensure_repo(self.profile_manager.get_files_dir())
            if not GitVersioning.check_identity(self.profile_manager.get_files_dir()):
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
            name, _files_dir, git_versioning = dialog.get_values()
            if not name:
                QMessageBox.warning(self, "Aviso", "El perfil necesita un nombre")
                return
            files_dir = self.profile_manager.get_files_dir()
            if git_versioning and not GitVersioning.is_available():
                QMessageBox.warning(
                    self, "Git no encontrado",
                    "No se encontró 'git' en el sistema. Se creará el perfil "
                    "igual, pero sin versionado hasta que instales git.",
                )

            shared_git = any(p.get("git_versioning") for p in self.profile_manager.profiles)
            new_profile = self.profile_manager.create_profile(name, files_dir, shared_git)
            self._load_profiles_list()
            self._open_new_default_tab()
            if git_versioning and not GitVersioning.check_identity(files_dir):
                self._set_git_warning(
                    "⚠ Git no tiene user.name/user.email configurados: los commits "
                    "no se van a guardar hasta que los configures."
                )

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
            self._add_tab()
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

        dialog = SaveToCollectionDialog(
            self,
            self.collection_manager.collections,
            current_url=self.current_url,
            collection_manager=self.collection_manager
        )
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
        self._refresh_collection_icon(self.current_url)
        self.statusBar().showMessage("Página agregada a la Colección", 4000)

    def _refresh_collection_icon(self, url: str):
        """Actualiza el ícono de colección (☆/★) según si la URL está en alguna colección."""
        is_in_collection = False
        for collection in self.collection_manager.collections:
            col_data = self.collection_manager.get_collection(collection["id"])
            if col_data and any(item["url"] == url for item in col_data.get("items", [])):
                is_in_collection = True
                break
        
        self.collection_action.setText("★" if is_in_collection else "☆")

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
            webview = self._add_tab(collection_id=data["collection_id"])
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
        webview = self._add_tab(collection_id=collection_id)
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
            delete_action = menu.addAction("Eliminar Colección")

            action = menu.exec(self.collections_tree.mapToGlobal(pos))
            if action == rename_action:
                self._rename_collection(collection_id)
            elif action == folder_action:
                self._pick_collection_folder(collection_id)
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
        self.collection_manager.save_collections()
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
            webview = self._add_tab(collection_id=t.get("collection_id"))
            restore_tab_metadata(self.tabs, self.tabs.indexOf(webview), t)
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
