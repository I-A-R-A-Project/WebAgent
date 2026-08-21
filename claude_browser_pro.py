#!/usr/bin/env python3
"""
Claude Browser Pro - Enhanced PyQt6 browser with persistent state
Features: tabs, favorites, history, zoom persistence, custom user agent
"""

import sys
import json
from pathlib import Path
from typing import Optional
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QToolBar, QTabWidget, QListWidget, QListWidgetItem,
    QMessageBox, QDialog, QLabel, QInputDialog, QComboBox
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
from PyQt6.QtCore import Qt, QUrl, QTimer, QSettings
from PyQt6.QtGui import QIcon, QAction, QKeySequence
import webbrowser


class CustomWebEnginePage(QWebEnginePage):
    """Custom page handler for popups and new windows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.popup_windows = []

    def createWindow(self, window_type):
        """
        Handle window.open() calls and popups.
        Abre popups de OAuth en una ventana separada en lugar de ignorarlas.
        """
        # Crear una nueva vista web para el popup
        popup_view = QWebEngineView()
        popup_page = CustomWebEnginePage(popup_view)
        popup_view.setPage(popup_page)

        # Crear ventana popup
        popup_window = QMainWindow()
        popup_window.setWindowTitle("Popup")
        popup_window.setCentralWidget(popup_view)
        popup_window.setGeometry(100, 100, 800, 600)
        popup_window.show()

        # Guardar referencia para evitar garbage collection
        self.popup_windows.append(popup_window)

        return popup_page


class ConfigManager:
    """Manage persistent application configuration."""

    def __init__(self):
        self.config_dir = Path.home() / ".claude_browser"
        self.config_dir.mkdir(exist_ok=True)
        self.config_file = self.config_dir / "config.json"
        self.history_file = self.config_dir / "history.json"
        self.favorites_file = self.config_dir / "favorites.json"
        self.load_config()

    def load_config(self):
        """Load configuration from disk."""
        if self.config_file.exists():
            with open(self.config_file, "r") as f:
                self.config = json.load(f)
        else:
            self.config = {
                "zoom": 1.0,
                "home_url": "https://claude.ai",
                "theme": "light"
            }
            self.save_config()

    def save_config(self):
        """Save configuration to disk."""
        with open(self.config_file, "w") as f:
            json.dump(self.config, f, indent=2)

    def add_history_item(self, url: str, title: str):
        """Add item to history."""
        if self.history_file.exists():
            with open(self.history_file, "r") as f:
                history = json.load(f)
        else:
            history = []

        # Remove duplicate if exists
        history = [h for h in history if h["url"] != url]

        # Add new item at the beginning
        history.insert(0, {
            "url": url,
            "title": title,
            "timestamp": datetime.now().isoformat()
        })

        # Keep only last 100 items
        history = history[:100]

        with open(self.history_file, "w") as f:
            json.dump(history, f, indent=2)

    def get_history(self) -> list:
        """Get browsing history."""
        if self.history_file.exists():
            with open(self.history_file, "r") as f:
                return json.load(f)
        return []

    def add_favorite(self, url: str, title: str):
        """Add item to favorites."""
        if self.favorites_file.exists():
            with open(self.favorites_file, "r") as f:
                favorites = json.load(f)
        else:
            favorites = []

        # Remove if already exists
        favorites = [f for f in favorites if f["url"] != url]

        # Add new favorite
        favorites.append({
            "url": url,
            "title": title,
            "timestamp": datetime.now().isoformat()
        })

        with open(self.favorites_file, "w") as f:
            json.dump(favorites, f, indent=2)

    def get_favorites(self) -> list:
        """Get favorites list."""
        if self.favorites_file.exists():
            with open(self.favorites_file, "r") as f:
                return json.load(f)
        return []

    def remove_favorite(self, url: str):
        """Remove a favorite."""
        if self.favorites_file.exists():
            with open(self.favorites_file, "r") as f:
                favorites = json.load(f)
            favorites = [f for f in favorites if f["url"] != url]
            with open(self.favorites_file, "w") as f:
                json.dump(favorites, f, indent=2)


class ClaudeBrowserPro(QMainWindow):
    """Enhanced Claude browser with tabs and persistent state."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Claude Browser Pro")
        self.setGeometry(100, 100, 1600, 1000)

        self.config = ConfigManager()
        self.current_url = ""

        # Setup UI
        self._setup_ui()
        self._setup_menu()
        self._load_favorites()

        # Load Claude
        self.load_url(self.config.config["home_url"])

        # Restore zoom (después de crear la primera pestaña)
        self.current_webview().setZoomFactor(self.config.config["zoom"])

    def _setup_ui(self):
        """Setup main user interface."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # Left sidebar for favorites and history
        self._setup_sidebar(main_layout)

        # Right side with tabs
        self._setup_tabs_and_navbar(main_layout)

    def _setup_sidebar(self, parent_layout):
        """Setup left sidebar with favorites and history."""
        sidebar_layout = QVBoxLayout()

        # Favorites label
        fav_label = QLabel("⭐ Favoritos")
        sidebar_layout.addWidget(fav_label)

        # Favorites list
        self.favorites_list = QListWidget()
        self.favorites_list.itemClicked.connect(self._on_favorite_clicked)
        sidebar_layout.addWidget(self.favorites_list)

        # Add favorite button
        add_fav_btn = QPushButton("+ Agregar favorito")
        add_fav_btn.clicked.connect(self._add_favorite)
        sidebar_layout.addWidget(add_fav_btn)

        sidebar_layout.addSpacing(20)

        # History label
        hist_label = QLabel("📚 Historial")
        sidebar_layout.addWidget(hist_label)

        # History list
        self.history_list = QListWidget()
        self.history_list.itemClicked.connect(self._on_history_clicked)
        sidebar_layout.addWidget(self.history_list)

        # Add to main layout with width constraint
        sidebar_container = QWidget()
        sidebar_container.setLayout(sidebar_layout)
        sidebar_container.setMaximumWidth(250)
        parent_layout.addWidget(sidebar_container)

    def _setup_tabs_and_navbar(self, parent_layout):
        """Setup tabs and navigation bar."""
        right_layout = QVBoxLayout()

        # Navigation toolbar
        navbar = self._create_navbar()
        right_layout.addWidget(navbar)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        right_layout.addWidget(self.tabs)

        # Create first tab
        self._add_tab()

        right_container = QWidget()
        right_container.setLayout(right_layout)
        parent_layout.addWidget(right_container, 1)

    def _create_navbar(self) -> QToolBar:
        """Create navigation toolbar."""
        navbar = QToolBar("Navigation")

        # Back
        back_action = QAction("◀", self)
        back_action.triggered.connect(lambda: self.current_webview().back())
        back_action.setShortcut(QKeySequence.StandardKey.Back)
        navbar.addAction(back_action)

        # Forward
        fwd_action = QAction("▶", self)
        fwd_action.triggered.connect(lambda: self.current_webview().forward())
        fwd_action.setShortcut(QKeySequence.StandardKey.Forward)
        navbar.addAction(fwd_action)

        # Reload
        reload_action = QAction("🔄", self)
        reload_action.triggered.connect(lambda: self.current_webview().reload())
        reload_action.setShortcut(QKeySequence.StandardKey.Refresh)
        navbar.addAction(reload_action)

        # Stop
        stop_action = QAction("⏹", self)
        stop_action.triggered.connect(lambda: self.current_webview().stop())
        navbar.addAction(stop_action)

        navbar.addSeparator()

        # Home
        home_action = QAction("🏠", self)
        home_action.triggered.connect(self.go_home)
        navbar.addAction(home_action)

        # New tab
        new_tab_action = QAction("+ Tab", self)
        new_tab_action.setShortcut("Ctrl+T")
        new_tab_action.triggered.connect(self._add_tab)
        navbar.addAction(new_tab_action)

        navbar.addSeparator()

        # Address bar
        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("URL...")
        self.address_bar.returnPressed.connect(self._on_address_bar_enter)
        navbar.addWidget(self.address_bar)

        # Add to favorites button
        fav_action = QAction("⭐", self)
        fav_action.triggered.connect(self._add_favorite)
        navbar.addAction(fav_action)

        return navbar

    def _add_tab(self):
        """Add a new browser tab."""
        webview = QWebEngineView()
        
        # Usar página personalizada para manejar popups
        custom_page = CustomWebEnginePage(webview)
        webview.setPage(custom_page)
        
        # Connect signals
        webview.urlChanged.connect(self._on_url_changed)
        webview.titleChanged.connect(self._on_title_changed)

        # Add to tabs
        tab_index = self.tabs.addTab(webview, "Nueva pestaña")
        self.tabs.setCurrentIndex(tab_index)

    def _close_tab(self, index: int):
        """Close a tab."""
        if self.tabs.count() > 1:
            self.tabs.removeTab(index)
        else:
            QMessageBox.information(self, "Información", "Debe haber al menos una pestaña abierta")

    def current_webview(self) -> QWebEngineView:
        """Get current active webview."""
        return self.tabs.currentWidget()

    def _on_address_bar_enter(self):
        """Handle address bar Enter."""
        url = self.address_bar.text()
        self.load_url(url)

    def _on_url_changed(self, url: QUrl):
        """Update UI when URL changes."""
        self.current_url = url.toString()
        self.address_bar.setText(self.current_url)

    def _on_title_changed(self, title: str):
        """Update tab title when page title changes."""
        if self.tabs.count() > 0:
            self.tabs.setTabText(self.tabs.currentIndex(), title[:30])
            self.config.add_history_item(self.current_url, title)
            self._refresh_history()

    def _setup_menu(self):
        """Create application menu."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&Archivo")
        exit_action = QAction("&Salir", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View menu
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

    def load_url(self, url: str):
        """Load URL in current tab."""
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        self.current_webview().setUrl(QUrl(url))

    def go_home(self):
        """Go home."""
        self.load_url(self.config.config["home_url"])

    def _add_favorite(self):
        """Add current page to favorites."""
        if not self.current_url:
            QMessageBox.warning(self, "Aviso", "No hay página cargada")
            return

        title = self.tabs.tabText(self.tabs.currentIndex())
        self.config.add_favorite(self.current_url, title)
        self._load_favorites()
        QMessageBox.information(self, "Éxito", f"Favorito agregado: {title}")

    def _load_favorites(self):
        """Load favorites into sidebar."""
        self.favorites_list.clear()
        for fav in self.config.get_favorites():
            item = QListWidgetItem(fav["title"])
            item.setData(Qt.ItemDataRole.UserRole, fav["url"])
            self.favorites_list.addItem(item)

    def _refresh_history(self):
        """Refresh history sidebar."""
        self.history_list.clear()
        for hist in self.config.get_history()[:20]:  # Show last 20
            item = QListWidgetItem(hist["title"])
            item.setData(Qt.ItemDataRole.UserRole, hist["url"])
            self.history_list.addItem(item)

    def _on_favorite_clicked(self, item: QListWidgetItem):
        """Load favorite when clicked."""
        url = item.data(Qt.ItemDataRole.UserRole)
        self.load_url(url)

    def _on_history_clicked(self, item: QListWidgetItem):
        """Load history item when clicked."""
        url = item.data(Qt.ItemDataRole.UserRole)
        self.load_url(url)

    def _zoom_in(self):
        """Increase zoom."""
        factor = self.current_webview().zoomFactor() + 0.1
        self.current_webview().setZoomFactor(factor)
        self.config.config["zoom"] = factor
        self.config.save_config()

    def _zoom_out(self):
        """Decrease zoom."""
        factor = self.current_webview().zoomFactor() - 0.1
        self.current_webview().setZoomFactor(factor)
        self.config.config["zoom"] = factor
        self.config.save_config()

    def _reset_zoom(self):
        """Reset zoom."""
        self.current_webview().setZoomFactor(1.0)
        self.config.config["zoom"] = 1.0
        self.config.save_config()

    def closeEvent(self, event):
        """Save state before closing."""
        self.config.save_config()
        event.accept()


def main():
    """Main entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("Claude Browser Pro")
    app.setApplicationVersion("2.0")

    window = ClaudeBrowserPro()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
