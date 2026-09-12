import sys
import os
import shutil
import json
import re
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
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineDownloadRequest
from PyQt6.QtCore import Qt, QUrl, QMimeData, QEvent, QTimer, QThread
from PyQt6.QtGui import QAction, QKeySequence, QKeyEvent, QShortcut

from core.file_ops import GitVersioning
from core.profiles import ProfileManager, ProfileWindowMixin
from core.collections_manager import CollectionManager, CollectionWindowMixin
from core.downloads import DownloadDialog
from scripts.website_tools.dialogs import WebsiteToolsDialog
from app.new_tab_page import render_new_tab_page
from core.task_manager import TaskManager
from agents.ai_manager import AgentConfigStore
from agents.agent_console import AgentConsolePanel
from agents.agent_runs import attach_bridge, render_agent_runs_page
from app.package_script_tab import PackageScriptTab
from app.cdp_har import CdpHarWorker
from web_common.json_store import SidebarAppsStore
from web_common.history import HistoryDialog, HistoryStore
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
        self.setWindowTitle("WebAgent")
        self.setGeometry(100, 100, 1600, 1000)
        self.setAcceptDrops(True)

        self.profile_manager = ProfileManager()
        self.collection_manager = CollectionManager()
        from core.paths import BROWSER_DATA_DIR

        self.sidebar_apps_store = SidebarAppsStore(
            str(BROWSER_DATA_DIR / "sidebar_apps.json"), []
        )
        self.web_engine_profiles: dict[str, QWebEngineProfile] = {}
        self.current_profile_id = self.profile_manager.profiles[0]["id"]
        self.history = HistoryStore(str(self.profile_manager.base_dir / "history.db"))
        self.history_session_id = self.history.new_session_id()
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
        self._har_thread = None
        self._har_worker = None
        self._har_action = None
        self._har_webview = None
        self._har_output_path = None
        self._pending_har_tab_close = None
        self._closing_wait_for_agents = False

        self._setup_ui()
        self._setup_status_bar()
        self._setup_menu()
        self._setup_shortcuts()
        self._load_profiles_list()
        self._load_collections_list()

        if not self._restore_session_or_default():
            self._activate_profile(self.current_profile_id, open_home=True)
        QTimer.singleShot(0, self._check_copilot_usage_for_all_profiles)

    # ------------------------------------------------------------------
    # Barra de estado
    # ------------------------------------------------------------------

    def _setup_status_bar(self):
        self.statusBar().showMessage("Listo")
        self.git_warning_label = QLabel("")
        self.git_warning_label.setStyleSheet("color: #b45309; font-weight: bold; padding-right: 10px;")
        self.statusBar().addPermanentWidget(self.git_warning_label)

    @staticmethod
    def _render_folder_view(page, folder_path):
        window = page.view_widget.window()
        folder_viewer.render_folder_view(
            page,
            folder_path,
            show_git_history=True,
            command_handler=getattr(window, "_run_package_script", None),
        )

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
            self._ensure_agent_profile,
            self._get_agent_collection_directories,
            auth_url_handler=self._open_copilot_auth_url,
            auth_success_handler=self._close_copilot_auth_tab,
            profile_name_getter=lambda profile_id: (
                self.profile_manager.get_profile(profile_id) or {}
            ).get("name", profile_id),
            profile_rotator=self._rotate_agent_profile,
            profile_usage_getter=lambda profile_id: (
                self.profile_manager.get_profile(profile_id) or {}
            ).get("copilot_usage"),
        )
        main_layout.addWidget(self.agent_console, 0)

        self.rail = SidebarRail()
        self.rail.on_toggle = self._on_sidebar_app_clicked
        self.rail.on_favicon_changed = (
            lambda app_id, favicon: self.sidebar_apps_store.update_item(
                app_id, favicon=favicon
            )
        )
        self.rail.rebuild(self.sidebar_apps_store.all())

        default_profile_id = self.profile_manager.get_default_profile_id()
        self.app_panel = AppPanelOverlay(self._get_qt_profile(default_profile_id))
        self.app_panel.on_new_window_request = self._handle_new_window_request

        container = SidebarContainer(self.rail, content_widget, self.app_panel)
        self.setCentralWidget(container)

    def _get_agent_collection_directories(self) -> list[tuple[str, str]]:
        """Devuelve únicamente las carpetas configuradas en Colecciones."""
        directories = []
        seen = set()
        for collection in self.collection_manager.collections:
            folder = str(collection.get("download_dir", "")).strip()
            if not folder:
                continue
            normalized = str(Path(folder).expanduser())
            key = os.path.normcase(os.path.normpath(normalized))
            if key in seen:
                continue
            seen.add(key)
            directories.append((collection.get("name", "Colección"), normalized))
        return directories

    def _check_copilot_usage_for_all_profiles(self):
        """Consulta el crédito de Copilot usando las cookies de cada perfil."""
        self._copilot_usage_pages = {}
        self._copilot_usage_pending = set(self.profile_manager.profile_ids())
        for profile_id in self._copilot_usage_pending.copy():
            page = QWebEnginePage(self._get_qt_profile(profile_id), self)
            self._copilot_usage_pages[profile_id] = page
            page.loadFinished.connect(
                lambda ok, pid=profile_id, checked_page=page:
                    self._read_copilot_usage(pid, checked_page, ok)
            )
            page.load(QUrl("https://github.com/settings/copilot/features"))
            QTimer.singleShot(
                30000,
                lambda pid=profile_id, checked_page=page:
                    self._finish_copilot_usage_check(pid, checked_page, None),
            )

    def _read_copilot_usage(self, profile_id, page, ok, attempts=0):
        if not ok:
            self._finish_copilot_usage_check(profile_id, page, None)
            return
        page.runJavaScript(
            """
            (() => {
              const label = [...document.querySelectorAll('*')].find(
                element => element.textContent.trim() === 'Included credits'
              );
              if (!label) return null;
              const container = label.parentElement?.parentElement || label.parentElement;
              const text = container?.innerText || '';
              const match = text.match(/(\\d+(?:\\.\\d+)?)\\s*%\\s*used/i);
              return match ? {text: match[0], percent: Number(match[1])} : null;
            })()
            """,
            lambda result, pid=profile_id, checked_page=page, try_count=attempts:
                self._retry_or_finish_copilot_usage(
                    pid, checked_page, result, try_count
                ),
        )

    def _retry_or_finish_copilot_usage(self, profile_id, page, result, attempts):
        if result is None and attempts < 10:
            QTimer.singleShot(
                1000,
                lambda: self._read_copilot_usage(
                    profile_id, page, True, attempts + 1
                ),
            )
            return
        self._finish_copilot_usage_check(profile_id, page, result)

    def _finish_copilot_usage_check(self, profile_id, page, result):
        if profile_id not in getattr(self, "_copilot_usage_pending", set()):
            return
        self._copilot_usage_pending.remove(profile_id)
        if isinstance(result, dict) and isinstance(result.get("percent"), (int, float)):
            usage = {
                "percent": result["percent"],
                "text": result.get("text", ""),
                "checked_at": datetime.now().isoformat(),
            }
        else:
            # Una consulta fallida no debe borrar el último valor conocido:
            # queda disponible mientras se vuelve a cargar la página.
            usage = (
                self.profile_manager.get_profile(profile_id) or {}
            ).get("copilot_usage")
            if not usage:
                usage = {
                    "percent": None,
                    "text": "No disponible",
                    "checked_at": datetime.now().isoformat(),
                }
        self.profile_manager.update_profile(profile_id, copilot_usage=usage)
        self._load_profiles_list()
        for dialog in self._agent_dialogs:
            if hasattr(dialog, "_update_copilot_usage_display"):
                dialog._update_copilot_usage_display()
        page.deleteLater()
        self._copilot_usage_pages.pop(profile_id, None)

    def _rotate_agent_profile(self, current_profile_id: str) -> str | None:
        profile_ids = self.profile_manager.agent_profile_ids()
        if len(profile_ids) < 2 or current_profile_id not in profile_ids:
            return None
        index = profile_ids.index(current_profile_id)
        return profile_ids[(index + 1) % len(profile_ids)]

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

    def _profile_id_for_webview(self, webview):
        return self.tab_data.get(id(webview), {}).get(
            "profile_id", self.current_profile_id
        )

    def _handle_new_window_request(self, request, source_webview=None):
        if not hasattr(request, "openIn"):
            source_webview = request
            profile_id = self._profile_id_for_webview(source_webview)
            return new_tab_page(lambda: self._add_tab(profile_id=profile_id))

        profile_id = self._profile_id_for_webview(source_webview)
        request.openIn(new_tab_page(lambda: self._add_tab(profile_id=profile_id)))

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
        if visible and not self._ensure_agent_profile():
            return
        self.agent_console.setVisible(visible)
        self.console_toggle.setText("⌃ Ocultar consola" if visible else "⌄ Consola de agentes")

    def _run_package_script(self, folder, script_name, command):
        package_tab = PackageScriptTab(folder, script_name, command, self)
        index = self.tabs.insertTab(
            self.tabs.indexOf(self.plus_widget), package_tab, f"npm: {script_name}"
        )
        self.tabs.setCurrentIndex(index)

    def _create_navbar(self) -> QToolBar:
        navbar = BasicNavbar(self)
        bind_navigation(
            navbar,
            self.current_webview,
            address_handler=self._on_address_bar_enter,
            history_handler=self.show_history,
            reload_handler=self._reload_current_webview,
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
            folder_view_handler=self._render_folder_view,
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
            load_finished_handler=self._record_history,
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

    def _reload_current_webview(self):
        """Actualiza vistas dinámicas sin volver a cargar su plantilla estática."""
        webview = self.current_webview()
        if webview is None:
            return
        if self.tab_data.get(id(webview), {}).get("agent_runs"):
            self._render_agent_runs_tab(webview)
            return
        webview.reload()

    def _render_agent_runs_tab(self, webview):
        bridges = getattr(self, "_agent_runs_bridges", None)
        if bridges is None:
            bridges = self._agent_runs_bridges = {}
        if id(webview) not in bridges:
            bridges[id(webview)] = attach_bridge(webview)
        webview.page().setHtml(
            render_agent_runs_page(),
            QUrl.fromLocalFile(
                str(Path(__file__).resolve().parent.parent / "agents" / "agent_runs.html")
            ),
        )

    def _handle_new_tab_request(self, source_webview=None):
        profile_id = self._profile_id_for_webview(source_webview)
        return new_tab_page(lambda: self._add_tab(profile_id=profile_id))

    def _execute_task(self, webview, profile_id: str, manager: TaskManager, task: dict):
        """Relaciona la tarea con una Colección mediante Gemini/Copilot."""
        if task.get("collection_id"):
            collection = self.collection_manager.get_collection(task["collection_id"])
            if collection:
                self._prepare_task_for_agents(task, collection.get("download_dir", ""))
            return

        try:
            context_dir = self.collection_manager.prepare_task_agent_context()
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(
                f"No se pudo preparar el contexto del agente de tareas: {exc}", 8000
            )
            return

        context_summary = context_dir / "collections_summary.md"
        if not self.collection_manager.collections:
            self._create_collection_for_task(webview, manager, task)
            return

        def finish_review(output: str):
            match = re.search(r"WEBAGENT_COLLECTION_RESULT:\s*(\{.*\})", output)
            decision = {}
            if match:
                try:
                    decision = json.loads(match.group(1))
                except json.JSONDecodeError:
                    decision = {}
            ids = decision.get("collection_ids", [])
            selected = next(
                (item for item in self.collection_manager.collections if item.get("id") in ids),
                None,
            )
            if selected is None:
                self._create_collection_for_task(webview, manager, task)
                return
            task["collection_id"] = selected["id"]
            task["collection_name"] = selected["name"]
            manager.save()
            webview.page().setHtml(render_new_tab_page(manager.tasks), QUrl("about:blank"))
            self._prepare_task_for_agents(task, selected.get("download_dir", ""))

        self.agent_console.run_collection_review(
            task["text"],
            str(context_dir),
            str(context_summary.resolve()),
            finish_review,
        )

    def _create_collection_for_task(self, webview, manager: TaskManager, task: dict):
        answer = QMessageBox.question(
            self,
            "Colección no identificada",
            "No se encontró una Colección adecuada para esta tarea. "
            "¿Querés crear una nueva Colección?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.statusBar().showMessage(
                "Tarea pendiente: no se creó ninguna Colección.", 6000
            )
            return None

        dialog = NewCollectionDialog(self, self.collection_manager.collections)
        dialog.name_edit.setText(task["text"][:60].strip() or "Nueva tarea")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        name, download_dir = dialog.get_values()
        if not name or not download_dir or not GitVersioning.is_available():
            QMessageBox.warning(
                self,
                "Aviso",
                "La nueva Colección necesita nombre, carpeta y Git disponible.",
            )
            return None
        download_dir = self._suggest_git_subfolder(download_dir)
        if not download_dir:
            return None
        try:
            collection = self.collection_manager.create_collection(
                name,
                download_dir,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.statusBar().showMessage(
                f"No se pudo crear la Colección para la tarea: {exc}", 8000
            )
            return None
        task["collection_id"] = collection["id"]
        task["collection_name"] = collection["name"]
        manager.save()
        webview.page().setHtml(render_new_tab_page(manager.tasks), QUrl("about:blank"))
        self._prepare_task_for_agents(task, collection["download_dir"])
        return collection

    def _prepare_task_for_agents(self, task: dict, folder: str) -> bool:
        """Analiza la tarea con Gemini y delega la ejecución final a Copilot."""
        if not self._ensure_agent_profile():
            return False
        if not self.agent_console.select_directory(folder):
            return False

        profile_id = self._active_profile_id_for_zoom()
        gemini_key = AgentConfigStore().get_profile_agent_token(
            folder, profile_id, "gemini"
        )
        original_task = task["text"]
        self.agent_console.setVisible(True)
        self.console_toggle.setText("⌃ Ocultar consola")

        def run_copilot(plan: str):
            prompt = (
                f"copilot {original_task}\n\n"
                "Gemini preparó este análisis inicial. Usalo como guía, verificá "
                "siempre el código real y ejecutá la tarea completa:\n\n"
                f"{plan[:12000]}"
            )
            self.agent_console.task_edit.setPlainText(prompt)
            self.agent_console.run_agent()

        if not gemini_key:
            self.statusBar().showMessage(
                "Gemini no está configurado; se ejecutará la tarea con Copilot.",
                5000,
            )
            self.agent_console.task_edit.setPlainText(f"copilot {original_task}")
            QTimer.singleShot(0, self.agent_console.run_agent)
            return True

        gemini_prompt = (
            "Analizá la tarea siguiente antes de modificar nada. Prepará un plan "
            "concreto para Copilot: archivos probables, pasos, riesgos y verificaciones. "
            "No ejecutes comandos ni inventes archivos; trabajá sólo con el repositorio "
            "real y devolvé el plan en texto claro.\n\n"
            f"TAREA:\n{original_task}"
        )
        self.agent_console.task_edit.setPlainText(f"gemini {gemini_prompt}")
        self.agent_console.run_agent(
            lambda output, exit_code: run_copilot(
                output if exit_code == 0 and output.strip() else
                "Gemini no pudo preparar un plan. Inspeccioná el repositorio y "
                "resolvé la tarea directamente."
            )
        )
        return True

    def _prepare_task_for_copilot(self, task: dict) -> bool:
        if not self._ensure_agent_profile():
            return False
        self.agent_console.task_edit.setPlainText(f"copilot {task['text']}")
        self.agent_console.setVisible(True)
        self.console_toggle.setText("⌃ Ocultar consola")
        return True

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
                if not self._ensure_agent_profile():
                    return
                self.agent_console.task_edit.setPlainText(f"copilot {task['text']}")
                self.agent_console.setVisible(True)
                self.console_toggle.setText("⌃ Ocultar consola")
            return
        if fragment.startswith("task?"):
            text = parse_qs(fragment.split("?", 1)[1], keep_blank_values=True).get("text", [""])[0]
            profile_id = self.tab_data.get(id(webview), {}).get("profile_id", self.current_profile_id)
            manager = TaskManager(profile_id)
            task = manager.add(text.strip(), self.collection_manager.collections)
            webview.page().setHtml(render_new_tab_page(manager.tasks), QUrl("about:blank"))
            self._execute_task(webview, profile_id, manager, task)
            return
        if url.scheme() == "ia" and url.host() == "task":
            text = parse_qs(url.query(), keep_blank_values=True).get("text", [""])[0]
            profile_id = self.tab_data.get(id(webview), {}).get("profile_id", self.current_profile_id)
            manager = TaskManager(profile_id)
            task = manager.add(text.strip(), self.collection_manager.collections)
            webview.page().setHtml(render_new_tab_page(manager.tasks), QUrl("about:blank"))
            self._execute_task(webview, profile_id, manager, task)
            return
        if webview is not self.current_webview():
            return
        sync_address_bar(
            self.tabs, webview, url, self.address_bar,
            plus_widget=self.plus_widget,
            extra_callback=self._refresh_collection_icon,
        )

    def _record_history(self, webview, ok):
        url = webview.url()
        if not ok or url.isEmpty() or url.toString() in ("about:blank", "about:srcdoc"):
            return
        self.history.add_history(
            url.toString(),
            webview.title() or url.toString(),
            self.history_session_id,
        )

    def show_history(self):
        dialog = HistoryDialog(
            self.history,
            on_open=lambda url: self._add_tab(profile_id=self.current_profile_id).setUrl(QUrl(url)),
        )
        dialog.exec()

    def _toggle_devtools(self):
        default_id = self.profile_manager.get_default_profile_id()
        window = TabbedPopupWindow(
            self._get_qt_profile(default_id),
            folder_view_handler=self._render_folder_view,
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
        widget = self.tabs.widget(index)
        if self._har_worker is not None and widget is self._har_webview:
            # La captura debe terminar de escribir el HAR antes de destruir
            # la pestaña que la originó.
            self._pending_har_tab_close = widget
            self._har_worker.stop()
            self.statusBar().showMessage(
                "Finalizando la captura HAR antes de cerrar la pestaña...",
                4000,
            )
            return
        self._close_tab_now(index)

    def _close_tab_now(self, index):
        close_shared_tab(
            self.tabs,
            index,
            self.plus_widget,
            before_delete=lambda widget: (
                self.tab_data.pop(id(widget), None),
                widget.stop() if isinstance(widget, VideoTab) else None,
                widget.stop() if isinstance(widget, PackageScriptTab) else None,
            ),
            ensure_tab=self._open_new_default_tab,
        )
        self.session_autosaver.schedule()

    def current_webview(self) -> QWebEngineView | None:
        """Return active web tab, never the '+' placeholder widget."""
        webview = active_tab(self.tabs, self.plus_widget)
        return webview if isinstance(webview, QWebEngineView) else None

    def _on_tab_changed(self, index: int):
        """Al cambiar de pestaña, sincroniza el combo de perfiles y la
        barra de dirección con la pestaña recién seleccionada."""
        def sync_tab(webview):
            if not isinstance(webview, QWebEngineView):
                return
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
        agent_runs_action = QAction("Historial de agentes...", self)
        agent_runs_action.setToolTip("Abrir el historial de ejecuciones de Copilot")
        agent_runs_action.triggered.connect(self._open_agent_runs)
        view_menu.addAction(agent_runs_action)
        devtools_action = QAction("Herramientas de desarrollador", self)
        devtools_action.setShortcut("F12")
        devtools_action.triggered.connect(self._toggle_devtools)
        view_menu.addAction(devtools_action)
        view_menu.addSeparator()

        website_action = QAction("🌐 Website Tools...", self)
        website_action.setToolTip("Crawlear y analizar una URL")
        website_action.triggered.connect(self._open_website_tools)
        view_menu.addAction(website_action)
        har_action = QAction("Capturar respuestas de la pestaña (HAR)...", self)
        har_action.setToolTip("Guardar automáticamente las respuestas de red de la pestaña activa")
        har_action.triggered.connect(self._toggle_har_capture)
        view_menu.addAction(har_action)
        self._har_action = har_action

    def _toggle_har_capture(self):
        if self._har_worker is not None:
            self._har_worker.stop()
            self.statusBar().showMessage("Finalizando captura HAR...", 3000)
            return

        webview = self.current_webview()
        if not webview or not webview.url().isValid():
            QMessageBox.warning(self, "Captura HAR", "No hay una pestaña web activa.")
            return
        tab_meta = self.tab_data.get(id(webview), {})
        target_dir, _ = self._resolve_target_folder(tab_meta)
        suggested = str(Path(target_dir) / "responses.har")
        output_path, _ = QFileDialog.getSaveFileName(
            self, "Guardar captura HAR", suggested, "HTTP Archive (*.har)"
        )
        if not output_path:
            return

        self._har_thread = QThread(self)
        self._har_worker = CdpHarWorker(
            webview.url().toString(), output_path
        )
        self._har_webview = webview
        self._har_output_path = Path(output_path)
        self._har_worker.moveToThread(self._har_thread)
        self._har_thread.started.connect(self._har_worker.run)
        self._har_worker.ready.connect(self._reload_har_webview)
        self._har_worker.output_path_changed.connect(
            lambda path: self._set_har_output_path(path)
        )
        self._har_worker.response_captured.connect(
            lambda count: self.statusBar().showMessage(
                f"Captura HAR: {count} response(s) guardadas", 3000
            )
        )
        self._har_worker.failed.connect(self._har_capture_failed)
        self._har_worker.finished.connect(self._har_capture_finished)
        self._har_thread.start()
        self._har_action.setText("Detener captura HAR")
        self.statusBar().showMessage(
            f"Preparando captura HAR y recargando la pestaña...", 5000
        )

    def _set_har_output_path(self, path):
        self._har_output_path = Path(path)
        self.statusBar().showMessage(
            f"El archivo elegido está bloqueado; guardando la captura en {path}",
            6000,
        )

    def _reload_har_webview(self):
        """Recarga la pestaña una vez habilitado el monitoreo CDP."""
        if self._har_worker is None or self._har_webview is None:
            return
        self._har_webview.reload()
        self.statusBar().showMessage(
            f"Capturando respuestas de la recarga en {self._har_output_path}",
            5000,
        )

    def _har_capture_failed(self, message):
        closing_tab = self._pending_har_tab_close is not None
        self._har_cleanup()
        if closing_tab:
            self.statusBar().showMessage(
                f"La captura HAR no pudo finalizar: {message}",
                6000,
            )
            self._finish_pending_har_tab_close()
            return
        QMessageBox.warning(self, "Captura HAR", f"No se pudo capturar tráfico CDP:\n{message}")

    def _har_capture_finished(self):
        output_path = self._har_output_path
        self._har_cleanup()
        if output_path and self._is_valid_har_file(output_path):
            self.statusBar().showMessage(
                f"Captura HAR finalizada y guardada en {output_path}", 6000
            )
        else:
            self.statusBar().showMessage(
                "La captura HAR terminó, pero el archivo no se guardó correctamente.",
                6000,
            )
        self._finish_pending_har_tab_close()

    @staticmethod
    def _is_valid_har_file(path):
        """Confirma que el archivo exista y contenga un documento HAR válido."""
        try:
            if not path.is_file() or path.stat().st_size == 0:
                return False
            document = json.loads(path.read_text(encoding="utf-8"))
            return (
                isinstance(document, dict)
                and isinstance(document.get("log"), dict)
                and document["log"].get("version") == "1.2"
                and isinstance(document["log"].get("entries"), list)
            )
        except (OSError, UnicodeError, ValueError):
            return False

    def _har_cleanup(self):
        thread = self._har_thread
        self._har_worker = None
        self._har_thread = None
        self._har_webview = None
        self._har_output_path = None
        if self._har_action:
            self._har_action.setText("Capturar respuestas de la pestaña (HAR)...")
        if thread:
            thread.quit()
            thread.wait(3000)
            thread.deleteLater()

    def _finish_pending_har_tab_close(self):
        widget = self._pending_har_tab_close
        self._pending_har_tab_close = None
        if widget is None:
            return
        index = self.tabs.indexOf(widget)
        if index >= 0:
            self._close_tab_now(index)

    def _open_agent_runs(self):
        """Abre una pestaña con el historial actualizado de ejecuciones."""
        webview = self._add_tab(profile_id=self.current_profile_id)
        self.tab_data[id(webview)]["agent_runs"] = True
        self._render_agent_runs_tab(webview)
        self.tabs.setTabText(self.tabs.indexOf(webview), "Historial de agentes")
        self.statusBar().showMessage("Historial de agentes cargado", 3000)

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
        if self._har_worker is not None:
            self._har_worker.stop()
            if self._har_thread:
                self._har_thread.quit()
                self._har_thread.wait(3000)
        if self.agent_console.has_running_processes():
            event.ignore()
            if not self._closing_wait_for_agents:
                self._closing_wait_for_agents = True
                self.statusBar().showMessage(
                    "Esperando a que terminen las consolas de agentes..."
                )
                QTimer.singleShot(100, self._close_after_agents_finish)
            return
        self._save_session()
        # El último uso válido ya está en profiles.json; guardarlo de nuevo
        # aquí asegura que quede persistido antes de cerrar el proceso.
        self.profile_manager.save_profiles()
        self.collection_manager.save_collections()
        event.accept()

    def _close_after_agents_finish(self):
        if self.agent_console.has_running_processes():
            QTimer.singleShot(100, self._close_after_agents_finish)
            return
        self.close()

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
