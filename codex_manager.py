"""
codex_manager.py - "Codex Manager" para IA Browser.

Le permite, para cualquier carpeta versionada con git (perfil o
Colección):
  1. Conectar un repositorio remoto que el usuario ya creó a mano en
     GitHub (u otro host), pegando su URL HTTPS o SSH — sin manejar
     tokens: usa las credenciales de git que ya estén configuradas en
     el sistema (SSH key, credential manager, etc).
  2. Configurar una rama "cruda" (donde caen los commits automáticos de
     IA Browser al descargar archivos — muchas veces con clutter de
     agregar/eliminar el mismo archivo varias veces) y una rama
     "limpia" de destino.
  3. Ejecutar Codex CLI (`codex exec`) para que analice esa rama cruda
     y genere commits prolijos, agrupados y con mensajes descriptivos
     en la rama limpia — sin duplicar mensajes de commit.
"""

import json
import shutil
from pathlib import Path

from PyQt6.QtCore import Qt, QProcess
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QTextEdit,
    QPushButton, QLabel, QDialogButtonBox, QMessageBox, QGroupBox
)
from PyQt6.QtGui import QFont

from file_ops import GitVersioning


# ======================================================================
# Configuración persistente por carpeta
# ======================================================================

class CodexConfigStore:
    """Guarda, por carpeta, la rama cruda/limpia configurada y el
    comando de Codex a usar. Clave = ruta absoluta de la carpeta."""

    def __init__(self):
        self.base_dir = Path.home() / ".ia_browser"
        self.base_dir.mkdir(exist_ok=True)
        self.config_file = self.base_dir / "codex_config.json"
        self.load()

    def load(self):
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
        else:
            self.data = {}

    def save(self):
        with open(self.config_file, "w") as f:
            json.dump(self.data, f, indent=2)

    def get(self, folder: str) -> dict:
        return self.data.get(str(folder), {
            "source_branch": "master",
            "target_branch": "main",
            "codex_command": 'codex exec --sandbox workspace-write "{prompt}"',
        })

    def set(self, folder: str, **kwargs):
        entry = self.get(folder)
        entry.update(kwargs)
        self.data[str(folder)] = entry
        self.save()


DEFAULT_PROMPT_TEMPLATE = """Estás parado en un repositorio git, en la rama '{target_branch}'.

La rama '{source_branch}' tiene commits automáticos generados por una herramienta \
de descargas de archivos. Esos commits muchas veces son ruidosos: agregan y \
eliminan (o modifican) el mismo archivo varias veces seguidas, dejando un \
historial con clutter innecesario.

Tu tarea:
1. Analizá el historial y los diffs de '{source_branch}' desde el punto en el \
que diverge de '{target_branch}' (podés usar `git log`, `git diff` y \
`git merge-base` para ubicarlo).
2. Agrupá los cambios de forma LÓGICA (por archivo o por tema), IGNORANDO idas \
y vueltas intermedias: si un archivo se agregó y después se modificó o se \
borró varias veces antes de asentarse, no hace falta un commit por cada paso \
intermedio — solo el resultado final tiene que quedar reflejado.
3. Creá commits prolijos en la rama '{target_branch}' (podés mergear, \
cherry-pickear o aplicar los cambios directamente, lo que te resulte más \
prolijo) con mensajes DESCRIPTIVOS: una primera línea corta en modo imperativo \
como resumen, y un cuerpo debajo explicando qué cambió y por qué, cuando sea \
relevante.
4. NO repitas el mismo mensaje de commit para cambios distintos — cada commit \
tiene que describir específicamente lo que aporta.
5. Al terminar, la rama '{target_branch}' tiene que estar actualizada y con el \
working tree limpio (sin cambios pendientes de commitear).

No toques la rama '{source_branch}' — dejala tal cual está, es el registro \
crudo de descargas."""


# ======================================================================
# Diálogo principal
# ======================================================================

class CodexManagerDialog(QDialog):
    """Diálogo de gestión: crear repo en GitHub (si falta), configurar
    ramas cruda/limpia, y disparar Codex CLI para limpiar el historial."""

    def __init__(self, parent, folder: str):
        super().__init__(parent)
        self.folder = folder
        self.config_store = CodexConfigStore()
        self.process: QProcess | None = None

        self.setWindowTitle(f"Codex Manager — {Path(folder).name}")
        self.resize(680, 640)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"📁 {folder}"))

        self._build_repo_section(layout)
        self._build_branches_section(layout)
        self._build_codex_section(layout)
        self._build_log_section(layout)

        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)

        self._refresh_repo_status()

    # ---------- Sección: estado del repo / GitHub ----------

    def _build_repo_section(self, layout):
        box = QGroupBox("Repositorio")
        box_layout = QVBoxLayout(box)

        self.repo_status_label = QLabel("")
        self.repo_status_label.setWordWrap(True)
        box_layout.addWidget(self.repo_status_label)

        btn_row = QHBoxLayout()
        self.init_repo_btn = QPushButton("Inicializar repositorio git aquí")
        self.init_repo_btn.clicked.connect(self._init_repo)
        self.connect_remote_btn = QPushButton("Conectar repositorio remoto...")
        self.connect_remote_btn.clicked.connect(self._connect_remote_dialog)
        btn_row.addWidget(self.init_repo_btn)
        btn_row.addWidget(self.connect_remote_btn)
        box_layout.addLayout(btn_row)

        layout.addWidget(box)

    def _refresh_repo_status(self):
        has_repo = GitVersioning.has_repo(self.folder)
        if not has_repo:
            self.repo_status_label.setText("⚠ Esta carpeta todavía no es un repositorio git.")
            self.init_repo_btn.setEnabled(True)
            self.connect_remote_btn.setEnabled(False)
            return

        self.init_repo_btn.setEnabled(False)
        branch = GitVersioning.get_current_branch(self.folder) or "?"
        has_remote = GitVersioning.has_remote(self.folder)
        remote_url = GitVersioning.get_remote_url(self.folder) if has_remote else ""

        if has_remote:
            self.repo_status_label.setText(f"✅ Repo git en rama '{branch}'.\n🔗 Remoto: {remote_url}")
            self.connect_remote_btn.setText("Cambiar repositorio remoto...")
        else:
            self.repo_status_label.setText(f"✅ Repo git en rama '{branch}'.\n⚠ Sin remoto configurado.")
            self.connect_remote_btn.setText("Conectar repositorio remoto...")
        self.connect_remote_btn.setEnabled(True)

    def _init_repo(self):
        GitVersioning.ensure_repo(self.folder)
        if not GitVersioning.check_identity(self.folder):
            QMessageBox.warning(
                self, "Falta identidad de git",
                "Git no tiene user.name/user.email configurados. Configuralos con:\n\n"
                'git config --global user.name "Tu Nombre"\n'
                'git config --global user.email "tu@email.com"\n\n'
                "y volvé a intentar.",
            )
        self._refresh_repo_status()

    def _connect_remote_dialog(self):
        """Conecta esta carpeta a un repositorio que el usuario ya creó
        a mano (en GitHub o donde sea): solo pide la URL (HTTPS o SSH)
        y hace 'remote add' + push, usando las credenciales de git que
        ya estén configuradas en el sistema. No maneja tokens."""
        current_url = GitVersioning.get_remote_url(self.folder) if GitVersioning.has_remote(self.folder) else ""
        dialog = _ConnectRemoteDialog(self, default_url=current_url)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        url = dialog.get_url()
        if not url:
            QMessageBox.warning(self, "Aviso", "Pegá la URL del repositorio (HTTPS o SSH).")
            return

        if not GitVersioning.has_repo(self.folder):
            self._init_repo()

        cfg = self.config_store.get(self.folder)
        target_branch = cfg["target_branch"]
        current_branch = GitVersioning.get_current_branch(self.folder)

        if not GitVersioning.is_clean(self.folder) or not current_branch:
            GitVersioning.run(self.folder, ["add", "-A"])
            GitVersioning.run(self.folder, ["commit", "-m", "Commit inicial"])
            current_branch = GitVersioning.get_current_branch(self.folder)

        if current_branch and current_branch != target_branch:
            GitVersioning.run(self.folder, ["branch", "-m", current_branch, target_branch])

        if GitVersioning.has_remote(self.folder):
            GitVersioning.run(self.folder, ["remote", "remove", "origin"])
        GitVersioning.run(self.folder, ["remote", "add", "origin", url])

        ok, out, err = GitVersioning.run(self.folder, ["push", "-u", "origin", target_branch], timeout=60)

        if ok:
            QMessageBox.information(self, "Repositorio conectado", f"Remoto configurado y publicado en:\n{url}")
        else:
            QMessageBox.warning(
                self, "Remoto conectado, pero el push falló",
                f"El remoto quedó configurado en:\n{url}\n\npero el push automático falló:\n\n{err}\n\n"
                "Puede ser un tema de autenticación (¿tenés una clave SSH cargada, o el "
                "credential manager de git configurado para HTTPS?). Podés intentar el "
                "push manualmente desde una terminal.",
            )

        self._refresh_repo_status()

    # ---------- Sección: ramas ----------

    def _build_branches_section(self, layout):
        box = QGroupBox("Ramas")
        form = QFormLayout(box)

        cfg = self.config_store.get(self.folder)
        self.source_branch_edit = QLineEdit(cfg["source_branch"])
        self.source_branch_edit.setPlaceholderText("Rama cruda (commits automáticos de descargas)")
        form.addRow("Rama cruda:", self.source_branch_edit)

        self.target_branch_edit = QLineEdit(cfg["target_branch"])
        self.target_branch_edit.setPlaceholderText("Rama limpia (destino de los commits de Codex)")
        form.addRow("Rama limpia (destino):", self.target_branch_edit)

        save_branches_btn = QPushButton("Guardar ramas")
        save_branches_btn.clicked.connect(self._save_branches)
        form.addRow("", save_branches_btn)

        layout.addWidget(box)

    def _save_branches(self):
        source = self.source_branch_edit.text().strip() or "master"
        target = self.target_branch_edit.text().strip() or "main"
        self.config_store.set(self.folder, source_branch=source, target_branch=target)
        self._append_log("ℹ Ramas guardadas")

    # ---------- Sección: Codex ----------

    def _build_codex_section(self, layout):
        box = QGroupBox("Codex")
        box_layout = QVBoxLayout(box)

        codex_available = shutil.which("codex") is not None
        if not codex_available:
            warn = QLabel(
                "⚠ No se encontró el comando 'codex' en el PATH. Instalalo con "
                "'npm install -g @openai/codex' y autenticate con 'codex login' "
                "antes de usar esta sección."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #b45309;")
            box_layout.addWidget(warn)

        form = QFormLayout()
        cfg = self.config_store.get(self.folder)
        self.command_edit = QLineEdit(cfg["codex_command"])
        form.addRow("Comando:", self.command_edit)
        box_layout.addLayout(form)

        box_layout.addWidget(QLabel("Instrucciones para Codex (se puede editar antes de correr):"))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlainText(self._default_prompt())
        self.prompt_edit.setMinimumHeight(140)
        box_layout.addWidget(self.prompt_edit)

        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("▶ Ejecutar Codex ahora")
        self.run_btn.clicked.connect(self._run_codex)
        self.stop_btn = QPushButton("⏹ Detener")
        self.stop_btn.clicked.connect(self._stop_codex)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.stop_btn)
        box_layout.addLayout(btn_row)

        layout.addWidget(box)

    def _default_prompt(self) -> str:
        cfg = self.config_store.get(self.folder)
        return DEFAULT_PROMPT_TEMPLATE.format(
            source_branch=cfg["source_branch"], target_branch=cfg["target_branch"]
        )

    # ---------- Sección: log de ejecución ----------

    def _build_log_section(self, layout):
        layout.addWidget(QLabel("Salida:"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas, Monaco, monospace", 10))
        self.log_view.setMinimumHeight(140)
        layout.addWidget(self.log_view)

    def _append_log(self, text: str):
        self.log_view.append(text)

    # ---------- Ejecutar Codex ----------

    def _run_codex(self):
        if shutil.which("codex") is None:
            QMessageBox.warning(
                self, "Codex no encontrado",
                "No se encontró el comando 'codex' en el PATH. Instalalo con "
                "'npm install -g @openai/codex' y autenticate con 'codex login'.",
            )
            return

        if not GitVersioning.has_repo(self.folder):
            QMessageBox.warning(self, "Aviso", "Esta carpeta todavía no es un repositorio git.")
            return

        source = self.source_branch_edit.text().strip() or "master"
        target = self.target_branch_edit.text().strip() or "main"
        self.config_store.set(self.folder, source_branch=source, target_branch=target,
                               codex_command=self.command_edit.text().strip())

        if not GitVersioning.branch_exists(self.folder, source):
            QMessageBox.warning(self, "Aviso", f"La rama cruda '{source}' no existe en este repo.")
            return
        if not GitVersioning.branch_exists(self.folder, target):
            resp = QMessageBox.question(
                self, "Rama destino inexistente",
                f"La rama '{target}' todavía no existe. ¿Crearla ahora a partir de '{source}'?",
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
            if not GitVersioning.create_branch(self.folder, target, source):
                QMessageBox.warning(self, "Error", f"No se pudo crear la rama '{target}'.")
                return

        if not GitVersioning.is_clean(self.folder):
            QMessageBox.warning(
                self, "Working tree sucio",
                "Hay cambios sin commitear en esta carpeta. Commiteá o descartá esos "
                "cambios antes de correr Codex, para no perder nada.",
            )
            return

        # Nos aseguramos de estar parados en la rama destino antes de correr.
        ok, _, err = GitVersioning.run(self.folder, ["checkout", target])
        if not ok:
            QMessageBox.warning(self, "Error", f"No se pudo cambiar a la rama '{target}':\n{err}")
            return

        prompt = self.prompt_edit.toPlainText().strip()
        command_template = self.command_edit.text().strip() or 'codex exec --sandbox workspace-write "{prompt}"'
        full_command = command_template.replace("{prompt}", prompt)

        self._append_log(f"$ {full_command}\n(cwd: {self.folder})\n")
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self.process = QProcess(self)
        self.process.setWorkingDirectory(self.folder)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._on_process_output)
        self.process.finished.connect(self._on_process_finished)

        # Usamos una shell para poder pasar el comando tal cual lo
        # escribió el usuario (con comillas, pipes, etc.) sin tener que
        # parsear argumentos nosotros mismos.
        if shutil.which("cmd.exe"):
            self.process.start("cmd.exe", ["/c", full_command])
        else:
            self.process.start("/bin/sh", ["-c", full_command])

    def _on_process_output(self):
        if not self.process:
            return
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self.log_view.moveCursor(self.log_view.textCursor().MoveOperation.End)
            self.log_view.insertPlainText(data)

    def _on_process_finished(self, exit_code: int, exit_status):
        self._append_log(f"\n--- Proceso terminado (código {exit_code}) ---\n")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.process = None
        self._refresh_repo_status()

    def _stop_codex(self):
        if self.process:
            self.process.kill()
            self._append_log("\n--- Detenido por el usuario ---\n")

    def closeEvent(self, event):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            confirm = QMessageBox.question(
                self, "Codex sigue corriendo",
                "Codex todavía está ejecutándose. ¿Detenerlo y cerrar?",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.process.kill()
        event.accept()


class _ConnectRemoteDialog(QDialog):
    """Sub-diálogo para pegar la URL (HTTPS o SSH) de un repositorio que
    el usuario ya creó a mano. No pide token: la autenticación la maneja
    git con lo que ya esté configurado en el sistema (SSH key,
    credential manager, etc)."""

    def __init__(self, parent, default_url: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Conectar repositorio remoto")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Pegá la URL del repositorio que ya creaste (HTTPS o SSH):"))

        self.url_edit = QLineEdit(default_url)
        self.url_edit.setPlaceholderText("https://github.com/usuario/repo.git")
        layout.addWidget(self.url_edit)

        examples = QLabel(
            "Ejemplos:\n"
            "  https://github.com/I-A-R-A-Project/IAbrower.git\n"
            "  git@github.com:I-A-R-A-Project/IAbrower.git"
        )
        examples.setStyleSheet("color: gray; font-size: 11px; font-family: monospace;")
        layout.addWidget(examples)

        hint = QLabel(
            "Usá HTTPS si tenés el credential manager de git configurado (lo habitual "
            "en Windows), o SSH si ya tenés una clave cargada en tu cuenta de GitHub. "
            "IA Browser no maneja tokens ni contraseñas: usa la autenticación que ya "
            "tengas configurada en tu sistema."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_url(self) -> str:
        return self.url_edit.text().strip()
