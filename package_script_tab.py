"""Pestaña especializada para ejecutar scripts de package.json."""

import shutil
import html
import re
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget
from PyQt6.QtWebEngineWidgets import QWebEngineView


_ANSI_RE = re.compile(
    r"(?:\x1b\][^\x07]*(?:\x07|\x1b\\))|(?:\x1b\[[0-?]*[ -/]*[@-~])"
)
_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_LOCAL_URL_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)


class PackageScriptTab(QWidget):
    """Ejecuta un script npm y muestra su consola dentro de la pestaña."""

    def __init__(self, folder, script_name, command, parent=None):
        super().__init__(parent)
        self.folder = Path(folder).resolve()
        self.script_name = script_name
        self.command = command
        self.process = None

        layout = QVBoxLayout(self)
        details = QLabel(
            f"npm run {script_name}\n"
            f"Directorio: {self.folder}\n"
            f"Comando: {command}"
        )
        details.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(details)

        self.preview = QWebEngineView()
        self.preview.setHtml(
            "<html><body style='background:#16171a;color:#9aa0a6;"
            "font-family:Segoe UI,Arial;padding:20px'>"
            "La vista previa se abrirá cuando el servidor local esté listo."
            "</body></html>"
        )
        layout.addWidget(self.preview, 2)

        self.console = QTextBrowser()
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Consolas", 10))
        self.console.setOpenLinks(False)
        self.console.anchorClicked.connect(self._open_url)
        self._append_output(f"$ npm run {script_name}\n\n")
        layout.addWidget(self.console, 1)

        self.stop_button = QPushButton("⏹ Detener")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        layout.addWidget(self.stop_button)

        self._start()

    def _append_output(self, text):
        text = self._clean_output(text)
        self._load_preview_url(text)
        if not text:
            return
        self.console.moveCursor(self.console.textCursor().MoveOperation.End)
        self.console.insertHtml(self._linkify(text))
        self.console.moveCursor(self.console.textCursor().MoveOperation.End)

    def _load_preview_url(self, text):
        match = _LOCAL_URL_RE.search(text)
        if not match:
            return
        url = match.group(0).rstrip(".,);]")
        qurl = self.preview.url()
        if qurl.toString() == url:
            return
        self.preview.setUrl(QUrl.fromUserInput(url))

    @staticmethod
    def _clean_output(text):
        """Quita controles de terminal y conserva el último estado de cada línea."""
        text = _ANSI_RE.sub("", text).replace("\x08", "")
        lines = []
        for line in text.splitlines(keepends=True):
            content = line.rstrip("\r\n").split("\r")[-1]
            lines.append(content + ("\n" if line.endswith(("\r", "\n")) else ""))
        return "".join(lines)

    @staticmethod
    def _linkify(text):
        chunks = []
        position = 0
        for match in _URL_RE.finditer(text):
            chunks.append(html.escape(text[position:match.start()]))
            url = match.group(0).rstrip(".,);]")
            chunks.append(
                f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>'
            )
            chunks.append(html.escape(match.group(0)[len(url):]))
            position = match.end()
        chunks.append(html.escape(text[position:]))
        return "".join(chunks).replace("\n", "<br>")

    def _open_url(self, url):
        if url.scheme().lower() in {"http", "https"}:
            window = self.parentWidget()
            add_tab = getattr(window, "_add_tab", None)
            if add_tab is not None:
                webview = add_tab()
                webview.setUrl(url)
            else:
                QDesktopServices.openUrl(url)

    def _start(self):
        if not self.folder.is_dir() or not self.command.strip():
            self._append_output("No se puede ejecutar el script: directorio o comando inválido.\n")
            return
        executable = shutil.which("npm.cmd") or shutil.which("npm")
        if not executable:
            self._append_output("No se encontró npm en el PATH.\n")
            return

        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(self.folder))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("NO_COLOR", "1")
        environment.insert("FORCE_COLOR", "0")
        environment.insert("npm_config_color", "false")
        self.process.setProcessEnvironment(environment)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.start(executable, ["run", self.script_name])
        self.stop_button.setEnabled(True)

    def _read_output(self):
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._append_output(data)

    def _finished(self, exit_code, _status):
        self._read_output()
        self._append_output(f"\n--- terminó (código {exit_code}) ---")
        self.process = None
        self.stop_button.setEnabled(False)

    def stop(self):
        if self.process is not None:
            self.process.kill()

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)
