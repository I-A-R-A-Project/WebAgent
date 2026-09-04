import sys
import os
import shutil
from urllib.parse import parse_qs
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Qt WebEngine exposes Chromium DevTools through this local endpoint.
os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9222")

from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QToolBar, QTabWidget,
    QMessageBox, QLabel, QFileDialog, QComboBox, QTreeWidget
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineDownloadRequest
from PyQt6.QtCore import Qt, QUrl, QMimeData, QEvent
from PyQt6.QtGui import QAction, QKeySequence, QKeyEvent, QShortcut

from file_ops import GitVersioning
from profiles import ProfileManager, ProfileWindowMixin
from collections_manager import CollectionManager, CollectionWindowMixin
from downloads import DownloadDialog
from scripts.website_tools.dialogs import WebsiteToolsDialog
from new_tab_page import render_new_tab_page
from task_manager import TaskManager
from agent_console import AgentConsolePanel
from web_common.json_store import SidebarAppsStore
from web_common.navbar import BasicNavbar, bind_navigation, save_web_page
from web_common.navigation import (
    active_tab, adjust_zoom, handle_tab_changed, navigate_view, new_tab_page,
    open_default_tab, open_plus_tab, set_zoom, sync_address_bar,
)
from web_common.session import (
    load_tab_session,
    restore_tab_metadata,
    SessionAutoSaver,
    save_tab_session,
)
from web_common.sidebar import AppPanelOverlay, SidebarContainer, SidebarRail
from web_common.local_navigation import (
    handle_special_local_file as dispatch_special_local_file,
    open_local_file as choose_local_file,
    open_local_folder as choose_local_folder,
    open_local_target,
    replace_tab_with_epub,
)
from web_common.downloader_handoff import handoff_url_to_downloader
from web_common.tabs import (
    VIDEO_EXTS, UnifiedWebTab, TabbedPopupWindow,
    add_plus_tab, configure_tab_widget, prepare_tab_widget,
    close_tab as close_shared_tab, keep_plus_tab_last, update_tab_icon,
    update_tab_title,
)
from web_common.video_tab import VideoTab, open_video_tab as add_video_tab
from web_common.epub_tab import EpubTab
from web_common import folder_viewer
from web_common.web_profiles import build_web_profile


class IABrowser(ProfileWindowMixin, CollectionWindowMixin, QMainWindow):
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
        self._setup_shortcuts()
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

    def _setup_shortcuts(self):
        persist = lambda factor: self.profile_manager.update_profile(
            self._active_profile_id_for_zoom(), zoom=factor
        )
        QShortcut(
            QKeySequence("Ctrl+="), self,
            activated=lambda: adjust_zoom(self.current_webview(), 0.1, persist),
        )
        QShortcut(
            QKeySequence("Ctrl+-"), self,
            activated=lambda: adjust_zoom(self.current_webview(), -0.1, persist),
        )
        QShortcut(
            QKeySequence("Ctrl+0"), self,
            activated=lambda: set_zoom(self.current_webview(), 1.0, persist),
        )

    def _active_profile_id_for_zoom(self) -> str:
        webview = self.current_webview()
        data = self.tab_data.get(id(webview), {})
        return data.get("profile_id", self.current_profile_id)

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
        request.openIn(new_tab_page(self._add_tab))

    def _setup_sidebar(self, parent_layout):
        """Sidebar con Perfiles y Colecciones."""
        sidebar_layout = QVBoxLayout()

        # --- Perfil activo ---
        profile_label = QLabel("👤 Perfil")
        sidebar_layout.addWidget(profile_label)

        self.profile_selector = QComboBox()
        self.profile_selector.setToolTip("Cambiar el perfil del navegador")
        self.profile_selector.currentIndexChanged.connect(
            self._on_profile_selector_changed
        )
        sidebar_layout.addWidget(self.profile_selector)

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
        prepare_tab_widget(self.tabs)
        self.plus_widget = add_plus_tab(self.tabs)
        configure_tab_widget(
            self.tabs,
            close_tab=self._close_tab,
            plus_widget=self.plus_widget,
            current_changed=self._on_tab_changed,
            tab_bar_clicked=lambda index: open_plus_tab(
                self.tabs, index, self.plus_widget, self._open_new_default_tab
            ),
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

    def _add_tab(
        self,
        profile_id: str | None = None,
        collection_id: str | None = None,
        insert_index: int | None = None,
    ):
        """Agrega una pestaña usando el perfil indicado o Default."""
        profile_id = profile_id or self.profile_manager.get_default_profile_id()
        qt_profile = self._get_qt_profile(profile_id)

        webview = UnifiedWebTab(
            qt_profile,
            parent_window=self,
            folder_view_handler=folder_viewer.render_folder_view,
            file_view_handler=folder_viewer.render_file_view,
            new_tab_handler=self._handle_new_tab_request,
            new_window_handler=self._handle_new_window_request,
            url_changed_handler=self._on_tab_url_changed,
            title_changed_handler=lambda tab, title: update_tab_title(
                self.tabs, tab, title
            ),
            icon_changed_handler=lambda tab, icon: update_tab_icon(
                self.tabs, tab, icon
            ),
            special_local_handler=self.handle_special_local_file,
        )

        self.tab_data[id(webview)] = {"profile_id": profile_id, "collection_id": collection_id}

        last_index = self.tabs.indexOf(self.plus_widget)
        insert_at = (
            max(0, min(insert_index, last_index))
            if insert_index is not None and last_index >= 0
            else last_index
        )
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
        return new_tab_page(self._add_tab)

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
        sync_address_bar(
            self.tabs, webview, url, self.address_bar,
            plus_widget=self.plus_widget,
            extra_callback=self._refresh_collection_icon,
        )

    def _toggle_devtools(self):
        default_id = self.profile_manager.get_default_profile_id()
        window = TabbedPopupWindow(
            self._get_qt_profile(default_id),
            folder_view_handler=folder_viewer.render_folder_view,
            file_view_handler=folder_viewer.render_file_view,
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

    def _open_new_default_tab(self):
        """Botón '+ Tab': siempre abre con el perfil Default,
        independientemente de cuál esté seleccionado en el combo."""
        return open_default_tab(self._add_tab)

    def _close_tab(self, index: int):
        close_shared_tab(
            self.tabs,
            index,
            self.plus_widget,
            before_delete=lambda widget: (
                self.tab_data.pop(id(widget), None),
                widget.stop() if isinstance(widget, VideoTab) else None,
            ),
            ensure_tab=self._open_new_default_tab,
        )
        self.session_autosaver.schedule()

    def current_webview(self) -> QWebEngineView | None:
        """Return active web tab, never the '+' placeholder widget."""
        return active_tab(self.tabs, self.plus_widget)

    def _on_tab_changed(self, index: int):
        """Al cambiar de pestaña, sincroniza el combo de perfiles y la
        barra de dirección con la pestaña recién seleccionada."""
        def sync_tab(webview):
            meta = self.tab_data.get(id(webview), {})
            self.current_profile_id = meta.get(
                "profile_id", self.current_profile_id
            )
            self._select_profile_in_sidebar(self.current_profile_id)
            self._highlight_active_profile()
            self.current_url = webview.url().toString()
            sync_address_bar(
                self.tabs, webview, webview.url(), self.address_bar,
                plus_widget=self.plus_widget,
                extra_callback=self._refresh_collection_icon,
            )

        handle_tab_changed(self.tabs, index, self.plus_widget, sync_tab)
    
    def _on_address_bar_enter(self, text: str):
        self.load_url(text)

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
        navigate_view(self.current_webview, url)

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

    def open_local_file(self):
        return choose_local_file(self, self._open_local_path_in_new_tab)

    def open_local_folder(self):
        return choose_local_folder(self, self._open_local_path_in_new_tab)

    def _open_local_path_in_new_tab(self, path):
        webview = self._add_tab()
        webview.setUrl(QUrl.fromLocalFile(path))
        return webview

    def handle_special_local_file(self, tab, local_path):
        return dispatch_special_local_file(
            tab,
            local_path,
            video_extensions=VIDEO_EXTS,
            video_handler=lambda path: add_video_tab(
                self.tabs, path, self, title_limit=22
            ),
            target_handler=self._open_local_target,
        )

    def _open_local_target(self, tab, local_path):
        cache_dir = self.profile_manager.base_dir / "archives_cache"
        return open_local_target(
            tab,
            local_path,
            cache_dir,
            epub_handler=self._replace_tab_with_epub,
        )

    def _replace_tab_with_epub(self, tab, local_path, cache_dir):
        def create_epub(source_tab, path, cache):
            return EpubTab(
                source_tab.page().profile(),
                path,
                cache_dir=cache,
                parent=self,
            )

        def preserve_metadata(source_tab, epub):
            metadata = self.tab_data.pop(id(source_tab), {})
            self.tab_data[id(epub)] = {
                "profile_id": metadata.get("profile_id", self.current_profile_id),
                "collection_id": metadata.get("collection_id"),
            }

        return replace_tab_with_epub(
            tab,
            self.tabs,
            local_path,
            cache_dir,
            epub_factory=create_epub,
            on_replaced=preserve_metadata,
        )

    def _resolve_target_folder(self, tab_meta: dict) -> tuple[str, str]:
        return self.collection_manager.resolve_target_folder(
            tab_meta,
            self.profile_manager,
            self.current_profile_id,
        )

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
            webview = self._add_tab(
                profile_id=t["profile_id"],
                collection_id=t.get("collection_id"),
            )
            restore_tab_metadata(self.tabs, self.tabs.indexOf(webview), t)
            webview.setUrl(QUrl(t["url"]))

        active = session.get("active_profile_id")
        if active and self.profile_manager.get_profile(active):
            self.current_profile_id = active
        else:
            self.current_profile_id = valid_tabs[-1]["profile_id"]

        active_index = session.get("active_index")
        if isinstance(active_index, int) and valid_tabs:
            active_index = max(0, min(active_index, len(valid_tabs) - 1))
            self.tabs.setCurrentIndex(active_index)
            active_widget = self.current_webview()
            active_meta = self.tab_data.get(id(active_widget), {})
            self.current_profile_id = active_meta.get("profile_id", self.current_profile_id)

        data = self.profile_manager.get_profile(self.current_profile_id)
        if data:
            self.current_webview().setZoomFactor(data.get("zoom", 1.0))
        self._highlight_active_profile()
        self.statusBar().showMessage(f"Sesión restaurada: {len(valid_tabs)} pestaña(s)", 5000)
        return True
