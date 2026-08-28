"""Panel inferior para ejecutar agentes y mostrar su salida en vivo."""

import shlex
import shutil
import re
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QMessageBox, QTabWidget, QVBoxLayout, QWidget,
)

from ai_manager import AGENT_DEFS, AGENT_ORDER, AgentConfigStore
from file_ops import GitVersioning


class AgentConsolePanel(QWidget):
    def __init__(self, parent, profile_getter, folder_getter, auth_url_handler=None):
        super().__init__(parent)
        self.profile_getter = profile_getter
        self.folder_getter = folder_getter
        self.auth_url_handler = auth_url_handler
        self.process = None
        self.autorun_process = None
        self.current_output = None
        self._copilot_output_buffer = ""
        self.setVisible(False)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.agent_combo = QComboBox()
        for agent_id in AGENT_ORDER:
            self.agent_combo.addItem(AGENT_DEFS[agent_id]["short_label"], agent_id)
        controls.addWidget(QLabel("IA:"))
        controls.addWidget(self.agent_combo)
        self.task_edit = QLineEdit()
        self.task_edit.setPlaceholderText("Tarea para el agente...")
        controls.addWidget(self.task_edit, 1)
        self.run_btn = QPushButton("▶ Ejecutar")
        self.run_btn.clicked.connect(self.run_agent)
        controls.addWidget(self.run_btn)
        self.stop_btn = QPushButton("⏹ Detener")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_agent)
        controls.addWidget(self.stop_btn)
        layout.addLayout(controls)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.stdin_edit = QLineEdit()
        self.stdin_edit.setPlaceholderText("Responder al agente y presionar Enter...")
        self.stdin_edit.returnPressed.connect(self.send_stdin)
        layout.addWidget(self.stdin_edit)

    def _environment(self, agent_id):
        profile_id = self.profile_getter()
        folder = self.folder_getter()
        config = AgentConfigStore().get(folder)
        environment = QProcessEnvironment.systemEnvironment()
        for name in ("COPILOT_HOME", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "ANYAPI_API_KEY"):
            environment.remove(name)
        environment.insert("COPILOT_HOME", str(Path.home() / ".ia_browser" / "copilot_profiles" / profile_id))
        environment.insert("BROWSER", "cmd.exe /c exit 0")
        token = config["agents"].get(agent_id, {}).get("auth_token", "")
        if agent_id == "copilot" and token:
            environment.insert("COPILOT_GITHUB_TOKEN", token)
        if agent_id == "anyapi" and token:
            environment.insert("ANYAPI_API_KEY", token)
        return environment

    def run_agent(self):
        if self.process is not None:
            return
        agent_id = self.agent_combo.currentData()
        task = self.task_edit.text().strip()
        if not task:
            return
        folder = self.folder_getter()
        config = AgentConfigStore().get(folder)
        command = config["agents"][agent_id].get("command", AGENT_DEFS[agent_id]["default_command"])
        prompt = task
        if agent_id == "copilot":
            prompt += self._git_context()
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            self._new_tab(agent_id, f"Comando inválido: {exc}")
            return
        script_dir = str(Path(__file__).resolve().parent)
        argv = [token.replace("{prompt}", prompt).replace("{script_dir}", script_dir) for token in tokens]
        if not argv or shutil.which(argv[0]) is None:
            self._new_tab(agent_id, f"No se encontró el comando: {argv[0] if argv else '(vacío)'}")
            return
        self._new_tab(agent_id, f"$ {' '.join(argv)}\n\n")
        self.process = QProcess(self)
        self.process.setWorkingDirectory(folder)
        self.process.setProcessEnvironment(self._environment(agent_id))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        if shutil.which("cmd.exe"):
            self.process.start("cmd.exe", ["/c", argv[0]] + argv[1:])
        else:
            self.process.start(argv[0], argv[1:])
        self.setVisible(True)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _new_tab(self, agent_id, initial=""):
        self.current_output = QPlainTextEdit()
        self.current_output.setReadOnly(True)
        self.current_output.setFont(QFont("Consolas", 10))
        self.current_output.setPlainText(initial)
        index = self.tabs.addTab(self.current_output, f"{AGENT_DEFS[agent_id]['short_label']} · ejecución")
        self.tabs.setCurrentIndex(index)

    def _read_output(self):
        if self.process and self.current_output:
            data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
            if self.agent_combo.currentData() == "copilot":
                self._copilot_output_buffer += data
                self._open_copilot_auth_url(data)
            self.current_output.insertPlainText(data)
            self.current_output.moveCursor(self.current_output.textCursor().MoveOperation.End)

    def _git_context(self):
        folder = self.folder_getter()
        if not GitVersioning.has_repo(folder):
            return "\nTrabajá en el repositorio según esta tarea y verificá tus cambios."
        status = GitVersioning.run(folder, ["status", "--short"], timeout=10)
        branch = GitVersioning.get_current_branch(folder) or "(detached)"
        return (
            "\nTrabajá en el repositorio según esta tarea y verificá tus cambios."
            f"\nContexto Git: rama={branch}\n"
            f"Working tree:\n{status[1][:4000]}\n"
            "Inspeccioná los cambios existentes antes de modificar archivos."
        )

    def _open_copilot_auth_url(self, text):
        if not self.auth_url_handler:
            return
        match = re.search(
            r"https?://(?:github\.com|github\.[^/\s]+)/(?:login/device|login/oauth/authorize)[^\s<>()\"]*",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            code_match = re.search(r"\b([A-Z0-9]{4,5}-[A-Z0-9]{4,5})\b", self._copilot_output_buffer)
            self.auth_url_handler(
                match.group(0).rstrip(".,);"),
                self.profile_getter(),
                code_match.group(1) if code_match else "",
            )

    def send_stdin(self):
        if self.process and self.stdin_edit.text():
            self.process.write((self.stdin_edit.text() + "\n").encode("utf-8"))
            self.stdin_edit.clear()

    def stop_agent(self):
        if self.process:
            self.process.kill()

    def _finished(self, exit_code, _status):
        self._read_output()
        if self.current_output:
            self.current_output.appendPlainText(f"\n--- terminó (código {exit_code}) ---")
        self.process = None
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        config = AgentConfigStore().get(self.folder_getter())
        autorun = config.get("autorun", {})
        command = autorun.get("command", "") if autorun.get("enabled") else ""
        if command:
            self._run_autorun(command)

    def _run_autorun(self, command):
        if self.autorun_process is not None:
            return
        if self.current_output:
            self.current_output.appendPlainText(f"\n--- autorun: {command} ---\n")
        process = QProcess(self)
        self.autorun_process = process
        process.setWorkingDirectory(self.folder_getter())
        process.setProcessEnvironment(self._environment(self.agent_combo.currentData()))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_autorun_output)
        process.finished.connect(self._autorun_finished)
        if shutil.which("cmd.exe"):
            process.start("cmd.exe", ["/c", command])
        else:
            process.start(command)

    def _read_autorun_output(self):
        if self.autorun_process and self.current_output:
            data = bytes(self.autorun_process.readAllStandardOutput()).decode(
                "utf-8", errors="replace"
            )
            self.current_output.insertPlainText(data)
            self.current_output.moveCursor(self.current_output.textCursor().MoveOperation.End)

    def _autorun_finished(self, exit_code, _status):
        self._read_autorun_output()
        if self.current_output:
            self.current_output.appendPlainText(
                f"\n--- autorun terminó (código {exit_code}) ---"
            )
        if exit_code != 0:
            QMessageBox.warning(
                self,
                "Autorun falló",
                f"El comando autorun devolvió código {exit_code}. Revisá la salida.",
            )
        self.autorun_process = None
