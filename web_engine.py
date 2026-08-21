"""
web_engine.py - Página web personalizada para IA Browser.

CustomWebEnginePage:
  1. Maneja los popups (ej. "Continuar con Google") abriéndolos en una
     ventana separada que comparte el mismo perfil (sesión/cache) que la
     pestaña que los originó. Sin esto, QWebEngineView ignora
     window.open() y los popups de OAuth nunca aparecen.
  2. Intercepta las navegaciones a file:// que apuntan a una CARPETA
     (no a un archivo) y, en vez de dejar que Chromium muestre su
     listado nativo, delega en folder_view_handler (provisto por
     IABrowser) para renderizar una página propia con el contenido de
     la carpeta + el historial de commits de git si corresponde.
"""

from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage


class CustomWebEnginePage(QWebEnginePage):
    """Custom page handler for popups, new windows, and folder views."""

    def __init__(self, profile: QWebEngineProfile, parent=None,
                 folder_view_handler: Optional[Callable[["CustomWebEnginePage", str], None]] = None):
        super().__init__(profile, parent)
        self.popup_windows = []
        # Callback(page, folder_path) que renderiza la vista custom de
        # una carpeta (llamado por IABrowser._render_folder_view).
        self.folder_view_handler = folder_view_handler

    def createWindow(self, window_type):
        """Abre popups (ej. OAuth) en una ventana separada, usando el
        MISMO perfil (misma sesión/cache) que la pestaña que los origina."""
        popup_view = QWebEngineView()
        popup_page = CustomWebEnginePage(self.profile(), popup_view, self.folder_view_handler)
        popup_view.setPage(popup_page)

        popup_window = QMainWindow()
        popup_window.setWindowTitle("Popup")
        popup_window.setCentralWidget(popup_view)
        popup_window.setGeometry(100, 100, 800, 600)
        popup_window.show()

        self.popup_windows.append(popup_window)
        return popup_page

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        """Si la navegación es a file:// y apunta a una carpeta, la
        intercepta y renderiza la vista custom en vez de dejar que
        Chromium muestre su listado de directorio nativo.

        IMPORTANTE: no se puede llamar setHtml() (u otra modificación
        de la página) de forma SÍNCRONA acá adentro — Chromium todavía
        está resolviendo esta navegación y hacerlo revienta el proceso
        nativo sin ningún traceback de Python. Por eso se difiere con
        QTimer.singleShot(0, ...) para que corra recién en el próximo
        ciclo del event loop, una vez que esta navegación ya se canceló."""
        from web_common import folder_viewer
        if is_main_frame and url.scheme() == "browser-action":
            return not folder_viewer.handle_action(self, url)
        if is_main_frame and self.folder_view_handler and url.scheme() == "file":
            local_path = url.toLocalFile()
            if local_path and Path(local_path).is_dir():
                handler = self.folder_view_handler
                QTimer.singleShot(0, lambda: handler(self, local_path))
                return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)
