"""Panel inferior para ejecutar agentes y mostrar su salida en vivo."""

import shlex
import shutil
import re
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QFileDialog, QMessageBox, QTabWidget, QVBoxLayout, QWidget,
)
from paths import COPILOT_PROFILES_DIR
from paths import IA_DATA_DIR

from ai_manager import AGENT_DEFS, AGENT_ORDER, AgentConfigStore
from file_ops import GitVersioning


class TaskInput(QPlainTextEdit):
    def __init__(self, execute_handler, parent=None):
        super().__init__(parent)
        self.execute_handler = execute_handler
        self.setTabChangesFocus(False)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.execute_handler()
            else:
                super().keyPressEvent(event)
            return
        super().keyPressEvent(event)


class AgentConsolePanel(QWidget):
    def __init__(
        self,
        parent,
        profile_getter,
        folder_getter,
        auth_url_handler=None,
        auth_success_handler=None,
    ):
        super().__init__(parent)
        self.profile_getter = profile_getter
        self.folder_getter = folder_getter
        self.auth_url_handler = auth_url_handler
        self.auth_success_handler = auth_success_handler
        self.process = None
        self.copilot_login_process = None
        self.autorun_process = None
        self.current_output = None
        self._log_file = None
        self._copilot_output_buffer = ""
        self._copilot_login_buffer = ""
        self._copilot_auth_urls_seen = set()
        self._auth_warning_shown = set()
        self.setVisible(False)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.agent_combo = QComboBox()
        for agent_id in AGENT_ORDER:
            self.agent_combo.addItem(AGENT_DEFS[agent_id]["short_label"], agent_id)
        controls.addWidget(QLabel("IA:"))
        controls.addWidget(self.agent_combo)
        self.task_edit = TaskInput(self.run_agent)
        self.task_edit.setFixedHeight(76)
        self.task_edit.setPlaceholderText("Tarea para el agente... (Shift+Enter para ejecutar)")
        controls.addWidget(self.task_edit, 1)
        self.run_btn = QPushButton("▶ Ejecutar")
        self.run_btn.clicked.connect(self.run_agent)
        controls.addWidget(self.run_btn)
        self.stop_btn = QPushButton("⏹ Detener")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_agent)
        controls.addWidget(self.stop_btn)
        layout.addLayout(controls)

        directory_row = QHBoxLayout()
        directory_row.addWidget(QLabel("Directorio:"))
        self.directory_edit = QLineEdit(self.folder_getter())
        self.directory_edit.setToolTip("Directorio de trabajo de la consola")
        directory_row.addWidget(self.directory_edit, 1)
        browse_btn = QPushButton("Elegir...")
        browse_btn.clicked.connect(self._choose_directory)
        directory_row.addWidget(browse_btn)
        layout.addLayout(directory_row)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.stdin_edit = QLineEdit()
        self.stdin_edit.setPlaceholderText("Responder al agente y presionar Enter...")
        self.stdin_edit.returnPressed.connect(self.send_stdin)
        layout.addWidget(self.stdin_edit)

    def _working_folder(self) -> str:
        return self.directory_edit.text().strip() or self.folder_getter()

    def _start_log(self, agent_id: str, folder: str, command: str, task: str):
        log_dir = IA_DATA_DIR / "agent_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_file = log_dir / f"{timestamp}_{agent_id}.log"
        self._write_log(
            f"=== {AGENT_DEFS[agent_id]['short_label']} ===\n"
            f"started: {datetime.now().isoformat()}\n"
            f"cwd: {folder}\n"
            f"command: {command}\n"
            f"task:\n{task}\n\n"
        )

    def _write_log(self, text: str):
        if self._log_file is None:
            return
        with self._log_file.open("a", encoding="utf-8") as stream:
            stream.write(text)

    def _environment(self, agent_id):
        profile_id = self.profile_getter()
        if not profile_id:
            return None
        folder = self._working_folder()
        if not Path(folder).is_dir():
            self._new_tab(agent_id, f"Directorio inexistente: {folder}")
            return
        config = AgentConfigStore().get(folder)
        environment = QProcessEnvironment.systemEnvironment()
        for name in ("COPILOT_HOME", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "ANYAPI_API_KEY"):
            environment.remove(name)
        environment.insert("COPILOT_ALLOW_ALL", "1")
        environment.insert("COPILOT_HOME", str(COPILOT_PROFILES_DIR / profile_id))
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
        if not self.profile_getter():
            self._new_tab(
                self.agent_combo.currentData(),
                "No hay un perfil disponible para agentes. Creá uno distinto de Default.",
            )
            return
        agent_id = self.agent_combo.currentData()
        self._auth_warning_shown.discard(agent_id)
        self._copilot_output_buffer = ""
        self._copilot_auth_urls_seen.clear()
        task = self.task_edit.toPlainText().strip()
        if not task:
            return
        folder = self._working_folder()
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
        self._start_log(agent_id, folder, " ".join(argv), task)
        self._new_tab(agent_id, f"$ {' '.join(argv)}\n\n")
        self.process = QProcess(self)
        self.process.setWorkingDirectory(folder)
        environment = self._environment(agent_id)
        if environment is None:
            self.process = None
            return
        self.process.setProcessEnvironment(environment)
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
        label = AGENT_DEFS.get(agent_id, {}).get("short_label", "Comando")
        index = self.tabs.addTab(self.current_output, f"{label} · ejecución")
        self.tabs.setCurrentIndex(index)

    def _read_output(self):
        if self.process and self.current_output:
            data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
            self._write_log(data)
            if self.agent_combo.currentData() == "copilot":
                self._copilot_output_buffer += data
                self._open_copilot_auth_url(data)
            self._detect_auth_error(self.agent_combo.currentData(), data)
            self.current_output.insertPlainText(data)
            self.current_output.moveCursor(self.current_output.textCursor().MoveOperation.End)

    def _choose_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Directorio de trabajo de la consola", self.directory_edit.text()
        )
        if directory:
            self.directory_edit.setText(directory)

    def _detect_auth_error(self, agent_id, text):
        if agent_id == "anyapi" and "Falta ANYAPI_API_KEY" in text:
            message = "AnyAPI no está autenticado. Configurá la API key en Configuración → Agentes IA."
        elif agent_id == "copilot" and "To authenticate, you can use" in text:
            if agent_id not in self._auth_warning_shown:
                self._auth_warning_shown.add(agent_id)
                self._start_copilot_login()
            return
        else:
            return
        if agent_id not in self._auth_warning_shown:
            self._auth_warning_shown.add(agent_id)
            if self.current_output:
                self.current_output.appendPlainText(f"\n⚠ {message}\n")
            QMessageBox.warning(self, "Autenticación requerida", message)

    def _start_copilot_login(self):
        if self.copilot_login_process is not None:
            return
        profile_id = self.profile_getter()
        if not profile_id or shutil.which("copilot") is None:
            return
        environment = self._environment("copilot")
        if environment is None:
            return

        self._copilot_login_buffer = ""
        self.copilot_login_process = QProcess(self)
        self.copilot_login_process.setWorkingDirectory(self._working_folder())
        self.copilot_login_process.setProcessEnvironment(environment)
        self.copilot_login_process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.copilot_login_process.readyReadStandardOutput.connect(
            self._read_copilot_login_output
        )
        self.copilot_login_process.finished.connect(
            self._copilot_login_finished
        )
        if self.current_output:
            self.current_output.appendPlainText(
                "\n--- Copilot no autenticado; iniciando login ---\n"
            )
        self._write_log(
            f"\n--- Copilot no autenticado; iniciando login ---\n"
            f"cwd: {self._working_folder()}\n"
        )
        if shutil.which("cmd.exe"):
            self.copilot_login_process.start(
                "cmd.exe", ["/c", "copilot", "login", "--device-code"]
            )
        else:
            self.copilot_login_process.start("copilot", ["login", "--device-code"])

    def _read_copilot_login_output(self):
        if not self.copilot_login_process:
            return
        data = bytes(
            self.copilot_login_process.readAllStandardOutput()
        ).decode("utf-8", errors="replace")
        self._copilot_login_buffer += data
        self._write_log(data)
        self._open_copilot_auth_url(data, self._copilot_login_buffer)
        if self.current_output:
            self.current_output.insertPlainText(data)
            self.current_output.moveCursor(
                self.current_output.textCursor().MoveOperation.End
            )

    def _copilot_login_finished(self, exit_code, _status):
        self._read_copilot_login_output()
        profile_id = self.profile_getter()
        if self.current_output:
            self.current_output.appendPlainText(
                f"\n--- login de Copilot terminó (código {exit_code}) ---"
            )
        self._write_log(
            f"\n--- login de Copilot terminó (código {exit_code}) ---\n"
        )
        self.copilot_login_process = None
        if exit_code == 0 and profile_id and self.auth_success_handler:
            self.auth_success_handler(profile_id)

    def _git_context(self):
        folder = self._working_folder()
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

    def _open_copilot_auth_url(self, text, output_buffer=None):
        if not self.auth_url_handler:
            return
        match = re.search(
            r"https?://(?:github\.com|github\.[^/\s]+)/(?:login/device|login/oauth/authorize)[^\s<>()\"]*",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            url = match.group(0).rstrip(".,);")
            if url in self._copilot_auth_urls_seen:
                return
            self._copilot_auth_urls_seen.add(url)
            if url.lower().endswith("/login/device"):
                url += "?skip_account_picker=true"
            code_match = re.search(
                r"\b([A-Z0-9]{4,5}-[A-Z0-9]{4,5})\b",
                output_buffer or self._copilot_output_buffer,
            )
            self.auth_url_handler(
                url,
                self.profile_getter(),
                code_match.group(1) if code_match else "",
            )

    def send_stdin(self):
        if self.process and self.stdin_edit.text():
            text = self.stdin_edit.text()
            self.process.write((text + "\n").encode("utf-8"))
            self._write_log(f"\n> {text}\n")
            self.stdin_edit.clear()

    def stop_agent(self):
        if self.process:
            self.process.kill()
        if self.copilot_login_process:
            self.copilot_login_process.kill()

    def _finished(self, exit_code, _status):
        self._read_output()
        if self.current_output:
            self.current_output.appendPlainText(f"\n--- terminó (código {exit_code}) ---")
        self._write_log(f"\n--- terminó (código {exit_code}) ---\n")
        self.process = None
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        config = AgentConfigStore().get(self._working_folder())
        autorun = config.get("autorun", {})
        command = autorun.get("command", "") if autorun.get("enabled") else ""
        if command:
            self._run_autorun(command)

    def _run_autorun(self, command):
        if self.autorun_process is not None:
            return
        if not self.profile_getter():
            return
        if self.current_output:
            self.current_output.appendPlainText(f"\n--- autorun: {command} ---\n")
        self._write_log(f"\n--- autorun: {command} ---\n")
        process = QProcess(self)
        self.autorun_process = process
        process.setWorkingDirectory(self._working_folder())
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
            self._write_log(data)
            self.current_output.insertPlainText(data)
            self.current_output.moveCursor(self.current_output.textCursor().MoveOperation.End)

    def _autorun_finished(self, exit_code, _status):
        self._read_autorun_output()
        if self.current_output:
            self.current_output.appendPlainText(
                f"\n--- autorun terminó (código {exit_code}) ---"
            )
        self._write_log(f"\n--- autorun terminó (código {exit_code}) ---\n")
        if exit_code != 0:
            QMessageBox.warning(
                self,
                "Autorun falló",
                f"El comando autorun devolvió código {exit_code}. Revisá la salida.",
            )
        self.autorun_process = None
