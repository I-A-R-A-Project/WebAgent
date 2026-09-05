"""Diálogo Qt para probar las Website Tools desde WebAgent."""

from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTextEdit, QVBoxLayout,
)

from .analyzer import analyze
from .crawler import CrawlConfig, CrawlResult, crawl
from .downloader import DownloadConfig, DownloadResult, download_site


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


class _DownloadWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(object)

    def __init__(self, config, urls):
        super().__init__()
        self.config = config
        self.urls = urls

    def run(self):
        try:
            result = download_site(self.config, self.urls, on_entry=self.progress.emit)
            self.finished.emit(result)
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
        self.last_download: DownloadResult | None = None
        self.pages_processed = 0
        self._build_ui(initial_url)

    def _build_ui(self, initial_url):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url_edit = QLineEdit(initial_url)
        self.url_edit.setPlaceholderText("https://example.com")
        form.addRow("URL inicial:", self.url_edit)
        self.tool_combo = QComboBox()
        self.tool_combo.addItem("Crawl + analizar", "crawl")
        self.tool_combo.addItem("Analizar último crawl", "analyze")
        self.tool_combo.addItem("Descargar último crawl", "download")
        form.addRow("Herramienta:", self.tool_combo)
        self.depth_spin = QSpinBox()
        self.depth_spin.setRange(0, 10)
        self.depth_spin.setValue(1)
        form.addRow("Profundidad máxima:", self.depth_spin)
        self.pages_spin = QSpinBox()
        self.pages_spin.setRange(1, 10000)
        self.pages_spin.setValue(25)
        form.addRow("Máximo de páginas:", self.pages_spin)
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(1)
        self.workers_spin.setToolTip("Se limita automáticamente al número de núcleos disponibles.")
        form.addRow("Workers concurrentes:", self.workers_spin)
        self.allow_urls_edit = QLineEdit()
        self.allow_urls_edit.setPlaceholderText("https://example.com/*")
        form.addRow("Whitelist URLs:", self.allow_urls_edit)
        self.block_urls_edit = QLineEdit()
        self.block_urls_edit.setPlaceholderText("Separar patrones con comas")
        form.addRow("Blacklist URLs:", self.block_urls_edit)
        self.allow_selectors_edit = QLineEdit()
        self.allow_selectors_edit.setPlaceholderText("#main-content")
        form.addRow("Whitelist contenido:", self.allow_selectors_edit)
        self.block_selectors_edit = QLineEdit()
        self.block_selectors_edit.setPlaceholderText(".ads, footer")
        form.addRow("Blacklist contenido:", self.block_selectors_edit)
        output_row = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("Carpeta destino para descargar")
        output_row.addWidget(self.output_dir_edit, 1)
        browse_btn = QPushButton("Elegir...")
        browse_btn.clicked.connect(self._choose_output_dir)
        output_row.addWidget(browse_btn)
        form.addRow("Destino:", output_row)
        self.pages_status = QLabel("Páginas: 0 / 0")
        self.depth_status = QLabel("Profundidad: - / 0")
        status_row = QHBoxLayout()
        status_row.addWidget(self.pages_status)
        status_row.addWidget(self.depth_status)
        status_row.addStretch()
        layout.addLayout(status_row)
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
        if self.tool_combo.currentData() == "download":
            self._run_download()
            return
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
        self.pages_processed = 0
        self.pages_status.setText(f"Páginas: 0 / {self.pages_spin.value()}")
        self.depth_status.setText(f"Profundidad: - / {self.depth_spin.value()}")
        config = CrawlConfig(
            start_url=url,
            max_depth=self.depth_spin.value(),
            max_pages=self.pages_spin.value(),
            verify_tls=not self.insecure_tls.isChecked(),
            workers=self.workers_spin.value(),
            allowed_url_patterns=self._split_patterns(self.allow_urls_edit.text()),
            blocked_url_patterns=self._split_patterns(self.block_urls_edit.text()),
            allowed_content_selectors=self._split_patterns(self.allow_selectors_edit.text()),
            blocked_content_selectors=self._split_patterns(self.block_selectors_edit.text()),
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

    @staticmethod
    def _split_patterns(value):
        return [part.strip() for part in value.split(",") if part.strip()]

    def _choose_output_dir(self):
        from PyQt6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, "Elegir carpeta destino")
        if path:
            self.output_dir_edit.setText(path)

    def _run_download(self):
        if self.last_result is None or not self.last_result.pages:
            QMessageBox.information(self, "Website Tools", "Ejecutá primero un crawl.")
            return
        output_dir = self.output_dir_edit.text().strip()
        if not output_dir:
            self._choose_output_dir()
            output_dir = self.output_dir_edit.text().strip()
        if not output_dir:
            return
        answer = QMessageBox.question(
            self,
            "Confirmar descarga",
            f"Se descargarán hasta {self.pages_spin.value()} archivos en:\n{output_dir}\n\n¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.run_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.output.setPlainText("Descargando páginas y recursos...")
        self.pages_processed = 0
        urls = [page.url for page in self.last_result.pages]
        config = DownloadConfig(
            start_url=urls[0],
            output_dir=Path(output_dir),
            max_pages=self.pages_spin.value(),
            verify_tls=not self.insecure_tls.isChecked(),
        )
        self.thread = QThread(self)
        self.worker = _DownloadWorker(config, urls)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_download_progress)
        self.worker.finished.connect(self._on_download_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._clear_worker)
        self.thread.start()

    def _on_download_progress(self, entry):
        self.pages_processed += 1
        status = str(entry.status) if entry.status is not None else "error"
        detail = entry.error or f"{entry.bytes} bytes"
        self.output.append(f"[{status}] {entry.url} — {detail}")
        self.pages_status.setText(f"Archivos: {self.pages_processed} / {self.pages_spin.value()}")
        self.depth_status.setText("Profundidad: —")

    def _on_download_finished(self, result):
        self.last_download = result
        self.output.append(
            f"\nCompletado: {len(result.entries)} archivos, "
            f"{result.total_bytes} bytes\nManifest: "
            f"{Path(result.entries[0].local_path).parent if result.entries and result.entries[0].local_path else '(ver carpeta destino)'}"
        )
        self.pages_status.setText(f"Archivos: {len(result.entries)} / {self.pages_spin.value()}")
        self.run_btn.setEnabled(True)
        self.save_btn.setEnabled(False)

    def _on_progress(self, page):
        status = str(page.status) if page.status is not None else "error"
        title = f" — {page.title}" if page.title else ""
        self.output.append(f"[{status}] {page.url}{title}")
        self.pages_processed += 1
        self.pages_status.setText(f"Páginas: {self.pages_processed} / {self.pages_spin.value()}")
        self.depth_status.setText(f"Profundidad: {page.depth} / {self.depth_spin.value()}")

    def _on_finished(self, result):
        self.last_result = result
        report = analyze(result)
        self.output.setPlainText(
            f"Páginas visitadas: {result.visited}\n"
            f"Errores: {report.errors}\n"
            f"Hallazgos: {len(report.findings)}\n\n"
            f"{self._format_report(report)}"
        )
        self.pages_status.setText(f"Páginas: {result.visited} / {self.pages_spin.value()}")
        max_depth = self.depth_spin.value()
        current_depth = max((page.depth for page in result.pages), default=0)
        self.depth_status.setText(f"Profundidad: {current_depth} / {max_depth}")
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
