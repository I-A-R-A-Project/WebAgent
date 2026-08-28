"""Diálogo Qt para probar las Website Tools desde IA Browser."""

from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTextEdit, QVBoxLayout,
)

from .analyzer import analyze
from .crawler import CrawlConfig, CrawlResult, crawl


class _CrawlWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(object)

    def __init__(self, config: CrawlConfig):
        super().__init__()
        self.config = config

    def run(self):
        try:
            self.finished.emit(crawl(self.config, on_page=self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))


class WebsiteToolsDialog(QDialog):
    """Permite introducir una URL y ejecutar crawl o análisis desde la UI."""

    def __init__(self, parent=None, initial_url=""):
        super().__init__(parent)
        self.setWindowTitle("Website Tools")
        self.resize(700, 560)
        self.thread = None
        self.worker = None
        self.last_result: CrawlResult | None = None
        self._build_ui(initial_url)

    def _build_ui(self, initial_url):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url_edit = QLineEdit(initial_url)
        self.url_edit.setPlaceholderText("https://example.com o http://127.0.0.1:8765/")
        form.addRow("URL inicial:", self.url_edit)
        self.tool_combo = QComboBox()
        self.tool_combo.addItem("Crawl + analizar", "crawl")
        self.tool_combo.addItem("Analizar último crawl", "analyze")
        form.addRow("Herramienta:", self.tool_combo)
        self.depth_spin = QSpinBox()
        self.depth_spin.setRange(0, 10)
        self.depth_spin.setValue(1)
        form.addRow("Profundidad máxima:", self.depth_spin)
        self.pages_spin = QSpinBox()
        self.pages_spin.setRange(1, 10000)
        self.pages_spin.setValue(25)
        form.addRow("Máximo de páginas:", self.pages_spin)
        self.insecure_tls = QCheckBox("Omitir verificación TLS (solo sitios de prueba)")
        self.insecure_tls.setToolTip("No usar en sitios con credenciales o datos sensibles.")
        form.addRow("", self.insecure_tls)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.run_btn = QPushButton("Ejecutar")
        self.run_btn.clicked.connect(self._run)
        self.save_btn = QPushButton("Guardar JSON...")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_result)
        buttons.addWidget(self.run_btn)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 1)

    def _run(self):
        if self.tool_combo.currentData() == "analyze":
            if self.last_result is None:
                QMessageBox.information(self, "Website Tools", "Todavía no hay un crawl para analizar.")
                return
            report = analyze(self.last_result)
            self.output.setPlainText(self._format_report(report))
            return

        url = self.url_edit.text().strip()
        if not url.startswith(("http://", "https://")):
            QMessageBox.warning(self, "URL inválida", "Usá una URL http:// o https://.")
            return
        self.run_btn.setEnabled(False)
        self.output.setPlainText("Crawleando...")
        config = CrawlConfig(
            start_url=url,
            max_depth=self.depth_spin.value(),
            max_pages=self.pages_spin.value(),
            verify_tls=not self.insecure_tls.isChecked(),
        )
        self.thread = QThread(self)
        self.worker = _CrawlWorker(config)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._clear_worker)
        self.thread.start()

    def _on_progress(self, page):
        status = str(page.status) if page.status is not None else "error"
        title = f" — {page.title}" if page.title else ""
        self.output.append(f"[{status}] {page.url}{title}")

    def _on_finished(self, result):
        self.last_result = result
        report = analyze(result)
        self.output.setPlainText(
            f"Páginas visitadas: {result.visited}\n"
            f"Errores: {report.errors}\n"
            f"Hallazgos: {len(report.findings)}\n\n"
            f"{self._format_report(report)}"
        )
        self.run_btn.setEnabled(True)
        self.save_btn.setEnabled(True)

    def _on_failed(self, message):
        self.output.setPlainText(f"Error: {message}")
        self.run_btn.setEnabled(True)

    def _clear_worker(self):
        self.worker = None
        self.thread = None

    def _format_report(self, report):
        lines = [f"Títulos duplicados: {', '.join(report.duplicate_titles) or '(ninguno)'}"]
        for finding in report.findings:
            lines.append(f"\n{finding.url}\n  - " + "\n  - ".join(finding.issues))
        return "\n".join(lines)

    def _save_result(self):
        if self.last_result is None:
            return
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "Guardar crawl", "crawl.json", "JSON (*.json)")
        if path:
            from .storage import save_crawl
            save_crawl(self.last_result, Path(path))
