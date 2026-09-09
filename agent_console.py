"""Panel inferior para ejecutar agentes o comandos y mostrar su salida en vivo."""

import shlex
import shutil
import re
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QPalette,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton,
    QComboBox, QLabel, QMessageBox, QTabBar, QTabWidget, QVBoxLayout, QWidget,
)
from paths import COPILOT_PROFILES_DIR
from paths import IA_DATA_DIR

from ai_manager import (
    AGENT_DEFS,
    AgentConfigStore,
    validate_autorun_command,
)
from file_ops import GitVersioning
from web_common.tabs import prepare_tab_widget


# Paleta "terminal oscura" para los inputs y pantallas de salida de la consola.
CONSOLE_BG = "#0b0d11"
CONSOLE_FG = "#e6e8ee"
CONSOLE_BORDER = "#2b2f3a"
CONSOLE_SELECTION_BG = "#3a3320"
CONSOLE_SELECTION_FG = "#ffffff"


def apply_console_style(widget):
    """Fuerza fondo negro / letra blanca en un input o pantalla de la consola."""
    widget.setStyleSheet(
        f"background-color: {CONSOLE_BG}; color: {CONSOLE_FG}; "
        f"border: 1px solid {CONSOLE_BORDER}; border-radius: 4px; "
        f"selection-background-color: {CONSOLE_SELECTION_BG}; "
        f"selection-color: {CONSOLE_SELECTION_FG}; padding: 3px;"
    )
    # El estilo de arriba no siempre alcanza para el color del cursor de texto
    # en QPlainTextEdit/QLineEdit, así que reforzamos también con la paleta.
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor(CONSOLE_BG))
    palette.setColor(QPalette.ColorRole.Text, QColor(CONSOLE_FG))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(CONSOLE_SELECTION_BG))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(CONSOLE_SELECTION_FG))
    widget.setPalette(palette)


class ConsoleHighlighter(QSyntaxHighlighter):
    """Resalta la salida de la consola: diffs de git, marcadores y errores."""

    def __init__(self, document):
        super().__init__(document)
        self._rules = []

        def add(pattern, color, bold=False, italic=False):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if bold:
                fmt.setFontWeight(QFont.Weight.Bold)
            if italic:
                fmt.setFontItalic(True)
            self._rules.append((re.compile(pattern), fmt))

        # Comando ejecutado / entrada enviada al proceso.
        add(r"^\$ .*", "#7fd1e0", bold=True)
        add(r"^> .*", "#7fd1e0")

        # Encabezados de secciones del log ("=== Copilot ===", etc.).
        add(r"^=== .* ===\s*$", "#e2a63b", bold=True)

        # Diff de git: encabezados de archivo, hunks y líneas +/-.
        add(r"^diff --git .*", "#e6e8ee", bold=True)
        add(r"^index [0-9a-fA-F]{4,}\.\.[0-9a-fA-F]{4,}.*", "#7d8194", italic=True)
        add(r"^(new file|deleted file|similarity index|rename (from|to)|old mode|new mode).*", "#7d8194", italic=True)
        add(r"^\+\+\+ .*", "#7d8194", italic=True)
        add(r"^--- (?!.*\(código).*", "#7d8194", italic=True)
        add(r"^@@ .*@@.*", "#e2a63b", bold=True)
        add(r"^\+(?!\+\+).*", "#59c98a")
        add(r"^-(?!--).*", "#e5636b")

        # Contexto de git que Copilot recibe en el prompt.
        add(r"^Contexto Git: rama=.*", "#b39bf0", bold=True)
        add(r"^Working tree:\s*$", "#b39bf0", bold=True)

        # Estadísticas finales de la corrida.
        add(r"^Changes\s+[+\-0-9 ]+$", "#59c98a", bold=True)
        add(r"^AI Credits.*", "#e2a63b")
        add(r"^Tokens\s.*", "#7d8194")
        add(r"^Resume\s+.*", "#7d8194", italic=True)

        # Marcadores de fin de ejecución: verde si código 0, rojo si falló.
        add(r"^--- .*\(código\s*0\)\s*---\s*$", "#59c98a", bold=True)
        add(r"^--- .*\(código\s*-?[1-9][0-9]*\)\s*---\s*$", "#e5636b", bold=True)

        # Errores, permisos y avisos.
        add(r".*Permission denied.*", "#e5636b", bold=True)
        add(r".*\bError:.*", "#e5636b", bold=True)
        add(r"^⚠.*", "#e2a63b", bold=True)

    def highlightBlock(self, text):
        for pattern, fmt in self._rules:
            if pattern.match(text):
                self.setFormat(0, len(text), fmt)
                return


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
        profile_name_getter=None,
        profile_rotator=None,
        profile_usage_getter=None,
    ):
        super().__init__(parent)
        self.profile_getter = profile_getter
        self.profile_rotator = profile_rotator
        self._profile_override = None
        self.profile_name_getter = profile_name_getter
        self.profile_usage_getter = profile_usage_getter
        self.folder_getter = folder_getter
        self.config_store = AgentConfigStore()
        self.auth_url_handler = auth_url_handler
        self.auth_success_handler = auth_success_handler
        self.process = None
        self.current_agent_id = None
        self.current_run_kind = None
        self.copilot_login_process = None
        self.autorun_process = None
        self._autorun_output = None
        self.current_output = None
        self._log_file = None
        self._git_start_head = None
        self._copilot_output_buffer = ""
        self._copilot_quota_detected = False
        self._copilot_retry_pending = False
        self._copilot_original_task = ""
        self._copilot_profiles_tried = set()
        self._copilot_login_buffer = ""
        self._copilot_auth_urls_seen = set()
        self._auth_warning_shown = set()
        self._highlighters = []
        self.setVisible(False)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.task_edit = TaskInput(self.run_agent)
        self.task_edit.setFixedHeight(76)
        self.task_edit.setPlaceholderText(
            "copilot/codex/gemini/groq seguido de la tarea, o un comando de terminal... "
            "(Shift+Enter para ejecutar)"
        )
        apply_console_style(self.task_edit)
        controls.addWidget(self.task_edit, 1)
        buttons = QVBoxLayout()
        self.run_btn = QPushButton("▶ Ejecutar")
        self.run_btn.clicked.connect(self.run_agent)
        buttons.addWidget(self.run_btn)
        self.stop_btn = QPushButton("⏹ Detener")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_agent)
        buttons.addWidget(self.stop_btn)
        directory_row = QHBoxLayout()
        self.directory_combo = QComboBox()
        self.directory_combo.setToolTip(
            "Seleccioná una carpeta de una Colección para ejecutar el agente"
        )
        apply_console_style(self.directory_combo)
        directory_row.addWidget(self.directory_combo)
        buttons.addLayout(directory_row)
        controls.addLayout(buttons)
        layout.addLayout(controls)
        self._refresh_directory_options()

        self.tabs = QTabWidget()
        prepare_tab_widget(self.tabs)
        self.tabs.tabCloseRequested.connect(
            lambda index: self._close_output_tab(self.tabs.widget(index))
        )
        layout.addWidget(self.tabs, 1)
        self.stdin_edit = QLineEdit()
        self.stdin_edit.setPlaceholderText("Enviar entrada al proceso y presionar Enter...")
        self.stdin_edit.returnPressed.connect(self.send_stdin)
        apply_console_style(self.stdin_edit)
        layout.addWidget(self.stdin_edit)

    def _working_folder(self) -> str:
        return str(self.directory_combo.currentData() or "")

    def _profile_id(self):
        return self._profile_override or self.profile_getter()

    def _refresh_directory_options(self):
        current_folder = self._working_folder()
        self.directory_combo.blockSignals(True)
        self.directory_combo.clear()
        directories = self.folder_getter() or []
        for name, folder in directories:
            self.directory_combo.addItem(f"{name}", folder)
        index = self.directory_combo.findData(current_folder)
        if index >= 0:
            self.directory_combo.setCurrentIndex(index)
        self.directory_combo.blockSignals(False)
        self.directory_combo.setEnabled(bool(directories))
        if not directories:
            self.directory_combo.addItem("No hay carpetas configuradas en Colecciones")

    def run_cli_help(self, agent_id: str):
        """Muestra la ayuda del agente seleccionado en una pestaña de consola."""
        if agent_id not in ("copilot", "codex", "gemini", "groq") or self.process is not None:
            return
        self._refresh_directory_options()
        if agent_id in ("copilot", "codex"):
            if shutil.which(agent_id) is None:
                self._new_tab(agent_id, f"No se encontró el comando: {agent_id}", finished=True)
                return
            command = f"{agent_id} --help"
            program = agent_id
            arguments = ["--help"]
        else:
            script = Path(__file__).resolve().parent / "scripts" / f"{agent_id}_agent.py"
            command = f'"{sys.executable}" "{script}" --help'
            program = sys.executable
            arguments = [str(script), "--help"]
        folder = self._working_folder()
        if not folder or not Path(folder).is_dir():
            self._new_tab(agent_id, f"Directorio inexistente: {folder}", finished=True)
            return

        self.current_agent_id = agent_id
        self.current_run_kind = "help"
        self._start_log(agent_id, folder, command, command)
        self._new_tab(agent_id, f"$ {command}\n\n")
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
        if agent_id in ("copilot", "codex") and shutil.which("cmd.exe"):
            self.process.start("cmd.exe", ["/c", agent_id, "--help"])
        else:
            self.process.start(program, arguments)
        self.setVisible(True)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _start_log(self, agent_id: str, folder: str, command: str, task: str):
        log_dir = IA_DATA_DIR / "agent_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_file = log_dir / f"{timestamp}_{agent_id}.log"
        profile_id = self._profile_id() or "(sin perfil)"
        profile_name = (
            self.profile_name_getter(profile_id)
            if self.profile_name_getter
            else profile_id
        ) or profile_id
        self._git_start_head = None
        if GitVersioning.has_repo(folder):
            ok, head, _ = GitVersioning.run(folder, ["rev-parse", "HEAD"], timeout=10)
            if ok:
                self._git_start_head = head.strip()
        self._write_log(
            f"=== {AGENT_DEFS.get(agent_id, {}).get('short_label', 'Comando')} ===\n"
            f"started: {datetime.now().isoformat()}\n"
            f"profile_id: {profile_id}\n"
            f"profile: {profile_name}\n"
            f"cwd: {folder}\n"
            f"command: {command}\n"
            f"task:\n{task}\n\n"
        )

    def _write_git_diff(self):
        """Guarda en el log el diff exacto producido por la ejecución."""
        folder = self._working_folder()
        if not GitVersioning.has_repo(folder):
            return

        sections = []
        if self._git_start_head:
            ok, output, error = GitVersioning.run(
                folder, ["diff", "--no-ext-diff", "--unified=3", self._git_start_head, "HEAD"],
                timeout=60,
            )
            if ok and output:
                sections.append(
                    f"--- Diff de commits ({self._git_start_head[:12]}..HEAD) ---\n{output}"
                )
            elif not ok:
                sections.append(f"--- No se pudo obtener el diff de commits: {error} ---")

        for label, args in (
            ("Diff sin commitear", ["diff", "--no-ext-diff", "--unified=3"]),
            ("Diff staged", ["diff", "--cached", "--no-ext-diff", "--unified=3"]),
        ):
            ok, output, error = GitVersioning.run(folder, args, timeout=60)
            if ok and output:
                sections.append(f"--- {label} ---\n{output}")
            elif not ok:
                sections.append(f"--- No se pudo obtener {label.lower()}: {error} ---")

        if not sections:
            sections.append("--- No hay diff disponible (sin cambios detectados). ---")
        self._write_log("\n=== Diff exacto de la ejecución ===\n" + "\n\n".join(sections) + "\n")

    def _write_log(self, text: str):
        if self._log_file is None:
            return
        with self._log_file.open("a", encoding="utf-8") as stream:
            stream.write(text)

    def _environment(self, agent_id):
        profile_id = self._profile_id()
        if not profile_id:
            return None
        folder = self._working_folder()
        if not folder or not Path(folder).is_dir():
            self._new_tab(agent_id, f"Directorio inexistente: {folder}", finished=True)
            return
        config = AgentConfigStore().get(folder)
        environment = QProcessEnvironment.systemEnvironment()
        for name in (
            "COPILOT_HOME", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
            "GEMINI_API_KEY", "GROQ_API_KEY",
        ):
            environment.remove(name)
        environment.insert("COPILOT_ALLOW_ALL", "1")
        environment.insert("COPILOT_HOME", str(COPILOT_PROFILES_DIR / profile_id))
        environment.insert("BROWSER", "cmd.exe /c exit 0")
        token = config["agents"].get(agent_id, {}).get("auth_token", "")
        if agent_id == "copilot" and token:
            environment.insert("COPILOT_GITHUB_TOKEN", token)
        if agent_id == "gemini" and token:
            environment.insert("GEMINI_API_KEY", token)
        if agent_id == "groq" and token:
            environment.insert("GROQ_API_KEY", token)
        return environment

    def run_agent(self):
        if self.process is not None:
            return
        raw_task = self.task_edit.toPlainText().strip()
        if not raw_task:
            return
        self._refresh_directory_options()
        if not self._working_folder():
            self._new_tab(
                "command",
                "No hay una carpeta de descarga configurada en las Colecciones.",
                finished=True,
            )
            return
        parts = raw_task.split(None, 1)
        agent_id = parts[0].lower().rstrip(":")
        retrying_copilot = self._copilot_retry_pending
        if retrying_copilot:
            agent_id = "copilot"
            task = self._copilot_original_task
            self._copilot_retry_pending = False
        else:
            self._profile_override = None
            task = parts[1].strip() if len(parts) > 1 else ""
        if agent_id not in AGENT_DEFS:
            self._run_command(raw_task)
            return
        if not task:
            self._new_tab(
                agent_id,
                f"Falta la tarea después de «{parts[0]}».",
                finished=True,
            )
            return
        self.current_agent_id = agent_id
        if not self._profile_id():
            self._new_tab(
                agent_id,
                "No hay un perfil disponible para agentes. Creá uno distinto de Default.",
                finished=True,
            )
            return
        if agent_id == "copilot" and self._copilot_usage_is_at_limit():
            return
        self._auth_warning_shown.discard(agent_id)
        self._copilot_output_buffer = ""
        if agent_id == "copilot" and not retrying_copilot:
            self._copilot_quota_detected = False
            self._copilot_original_task = task
            self._copilot_profiles_tried = {self._profile_id()}
        self._copilot_auth_urls_seen.clear()
        folder = self._working_folder()
        config = AgentConfigStore().get(folder)
        command = config["agents"][agent_id].get("command", AGENT_DEFS[agent_id]["default_command"])
        prompt = task
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            self._new_tab(agent_id, f"Comando inválido: {exc}", finished=True)
            return
        script_dir = str(Path(__file__).resolve().parent)
        argv = [token.replace("{prompt}", prompt).replace("{script_dir}", script_dir) for token in tokens]
        if retrying_copilot:
            prompt = self._copilot_fallback_prompt()
            argv = [
                "copilot", "-p", prompt, "--allow-all-tools",
                "--allow-all-paths", "--allow-all-urls",
            ]
            self._copilot_quota_detected = False
        if not argv or shutil.which(argv[0]) is None:
            self._new_tab(
                agent_id,
                f"No se encontró el comando: {argv[0] if argv else '(vacío)'}",
                finished=True,
            )
            return
        self._start_log(agent_id, folder, " ".join(argv), task)
        self._new_tab(agent_id, f"$ {' '.join(argv)}\n\n")
        self.current_run_kind = "agent"
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

    def _copilot_usage_is_at_limit(self):
        """Selecciona otro perfil antes de iniciar Copilot si el uso es alto."""
        if not self.profile_usage_getter:
            return False

        current = self._profile_id()
        if not current:
            return False
        self._copilot_profiles_tried = {current}

        while True:
            usage = self.profile_usage_getter(self._profile_id())
            percent = usage.get("percent") if isinstance(usage, dict) else None
            if not isinstance(percent, (int, float)) or percent < 90:
                return False
            if not self._rotate_copilot_profile():
                self._write_log(
                    f"\n--- No hay otro perfil disponible; el uso de Copilot "
                    f"es {percent:g}% ---\n"
                )
                return True

    def _run_command(self, command):
        """Ejecuta un comando de terminal en el directorio seleccionado."""
        valid, error = validate_autorun_command(command)
        if not valid:
            self._new_tab("command", f"Comando bloqueado: {error}", finished=True)
            return
        folder = self._working_folder()
        if not folder or not Path(folder).is_dir():
            self._new_tab("command", f"Directorio inexistente: {folder}", finished=True)
            return

        self.current_agent_id = None
        self.current_run_kind = "command"
        self._start_log("command", folder, command, command)
        self._new_tab("command", f"$ {command}\n\n")
        self.process = QProcess(self)
        self.process.setWorkingDirectory(folder)
        self.process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        if shutil.which("cmd.exe"):
            self.process.start("cmd.exe", ["/c", command])
        else:
            try:
                tokens = shlex.split(command)
            except ValueError as exc:
                self.process = None
                self._new_tab("command", f"Comando inválido: {exc}", finished=True)
                return
            if not tokens or shutil.which(tokens[0]) is None:
                self.process = None
                self._new_tab(
                    "command",
                    f"No se encontró el comando: {tokens[0] if tokens else '(vacío)'}",
                    finished=True,
                )
                return
            self.process.start(tokens[0], tokens[1:])
        self.setVisible(True)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _new_tab(self, agent_id, initial="", finished=False):
        self.current_output = QPlainTextEdit()
        self.current_output.setReadOnly(True)
        self.current_output.setFont(QFont("Consolas", 10))
        apply_console_style(self.current_output)
        self._highlighters.append(ConsoleHighlighter(self.current_output.document()))
        self.current_output.setPlainText(initial)
        label = AGENT_DEFS.get(agent_id, {}).get("short_label", "Comando")
        if agent_id == "copilot":
            profile_id = self._profile_id()
            profile_name = (
                self.profile_name_getter(profile_id)
                if profile_id and self.profile_name_getter
                else profile_id
            ) or "sin perfil"
            label = f"{label} · {profile_name}"
        else:
            label = f"{label}"
        index = self.tabs.addTab(self.current_output, label)
        self.tabs.setCurrentIndex(index)
        self._add_close_button(self.current_output, enabled=finished)

    def _add_close_button(self, output, enabled=True):
        """Crea el botón de cierre y controla cuándo puede usarse."""
        index = self.tabs.indexOf(output)
        if index < 0:
            return
        close_btn = self.tabs.tabBar().tabButton(index, QTabBar.ButtonPosition.RightSide)
        if close_btn is not None:
            close_btn.setEnabled(enabled)

    def _close_output_tab(self, output):
        index = self.tabs.indexOf(output)
        if index < 0:
            return
        self.tabs.removeTab(index)
        output.deleteLater()
        if self.current_output is output:
            self.current_output = None

    def _read_output(self):
        if self.process and self.current_output:
            data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
            self._write_log(data)
            if self.current_run_kind == "agent" and self.current_agent_id == "copilot":
                self._copilot_output_buffer += data
                if self._looks_like_copilot_quota(self._copilot_output_buffer):
                    self._copilot_quota_detected = True
                self._open_copilot_auth_url(data)
            if self.current_run_kind == "agent":
                self._detect_auth_error(self.current_agent_id, data)
            self.current_output.insertPlainText(data)
            self.current_output.moveCursor(self.current_output.textCursor().MoveOperation.End)

    def _detect_auth_error(self, agent_id, text):
        if agent_id == "gemini" and "Falta GEMINI_API_KEY" in text:
            message = "Gemini no está autenticado. Configurá la API key en Configuración → Agentes IA."
        elif agent_id == "groq" and "Falta GROQ_API_KEY" in text:
            message = "Groq no está autenticado. Configurá la API key en Configuración → Agentes IA."
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
        profile_id = self._profile_id()
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
        profile_id = self._profile_id()
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
                self._profile_id(),
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
        run_kind = self.current_run_kind
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if (
            run_kind == "agent"
            and self.current_agent_id == "copilot"
            and exit_code == 1
            and self._copilot_quota_detected
            and self._rotate_copilot_profile()
        ):
            warning = (
                "\n--- Cuota mensual agotada; cambiando de perfil y "
                "relanzando el prompt ---\n"
            )
            if self._has_started_changes():
                warning += (
                    "⚠ El trabajo quedó incompleto y ya había cambios realizados. "
                    "El nuevo agente debe revisarlos antes de continuar.\n"
                )
            self._write_log(warning)
            if self.current_output:
                self.current_output.appendPlainText(warning)
                self._add_close_button(self.current_output)
            self._copilot_retry_pending = True
            self.current_run_kind = None
            self.current_agent_id = None
            QTimer.singleShot(250, self.run_agent)
            return
        if run_kind not in ("agent", "help"):
            self.current_run_kind = None
            self.current_agent_id = None
            self._write_git_diff()
            self._add_close_button(self.current_output)
            return
        config = AgentConfigStore().get(self._working_folder())
        autorun = config.get("autorun", {})
        command = autorun.get("command", "") if autorun.get("enabled") else ""
        if command:
            valid, error = validate_autorun_command(command)
            if not valid:
                self._new_tab("command", f"Autorun bloqueado: {error}", finished=True)
                self._write_log(f"\n--- autorun bloqueado: {error} ---\n")
                self.current_run_kind = None
                self.current_agent_id = None
                self._write_git_diff()
                self._add_close_button(self.current_output)
            elif not self._run_autorun(command):
                self.current_run_kind = None
                self.current_agent_id = None
                self._add_close_button(self.current_output)
        else:
            self._write_git_diff()
            self.current_run_kind = None
            self.current_agent_id = None
            self._add_close_button(self.current_output)

    def _has_started_changes(self):
        folder = self._working_folder()
        if not GitVersioning.has_repo(folder):
            return False
        status = GitVersioning.run(folder, ["status", "--short"], timeout=10)
        if status[1].strip():
            return True
        if self._git_start_head:
            ok, head, _ = GitVersioning.run(folder, ["rev-parse", "HEAD"], timeout=10)
            return ok and head.strip() != self._git_start_head.strip()
        return False

    def _looks_like_copilot_quota(self, text):
        return bool(re.search(
            r"(?im)^\s*you have exceeded your monthly quota\s*\(request id:",
            text,
        ))

    def _rotate_copilot_profile(self):
        current = self._profile_id()
        if not current or not self.profile_rotator:
            return False
        self._copilot_profiles_tried.add(current)
        next_profile = self.profile_rotator(current)
        if not next_profile or next_profile in self._copilot_profiles_tried:
            return False
        self._copilot_profiles_tried.add(next_profile)
        self._profile_override = next_profile
        self._write_log(
            f"\n--- Perfil de Copilot seleccionado: {next_profile} ---\n"
        )
        return True

    def _copilot_fallback_prompt(self):
        return (
            f"{self._copilot_original_task}\n\n"
            "El perfil anterior agotó su cuota. Continuá el trabajo pendiente, "
            "revisá los cambios existentes y no los pierdas."
        )

    def _run_autorun(self, command):
        if self.autorun_process is not None:
            return False
        if not self._profile_id():
            return False
        output = self.current_output
        self._autorun_output = output
        if output:
            output.appendPlainText(f"\n--- autorun: {command} ---\n")
        self._write_log(f"\n--- autorun: {command} ---\n")
        process = QProcess(self)
        self.autorun_process = process
        process.setWorkingDirectory(self._working_folder())
        process.setProcessEnvironment(self._environment(self.current_agent_id))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_autorun_output)
        process.finished.connect(self._autorun_finished)
        if shutil.which("cmd.exe"):
            process.start("cmd.exe", ["/c", command])
        else:
            process.start(command)
        return True

    def _read_autorun_output(self):
        if self.autorun_process and self._autorun_output:
            data = bytes(self.autorun_process.readAllStandardOutput()).decode(
                "utf-8", errors="replace"
            )
            self._write_log(data)
            self._autorun_output.insertPlainText(data)
            self._autorun_output.moveCursor(
                self._autorun_output.textCursor().MoveOperation.End
            )

    def _autorun_finished(self, exit_code, _status):
        self._read_autorun_output()
        output = self._autorun_output
        if output:
            output.appendPlainText(
                f"\n--- autorun terminó (código {exit_code}) ---"
            )
        self._write_log(f"\n--- autorun terminó (código {exit_code}) ---\n")
        if exit_code != 0:
            QMessageBox.warning(
                self,
                "Autorun falló",
                f"El comando autorun devolvió código {exit_code}. Revisá la salida.",
            )
        self._write_git_diff()
        self.autorun_process = None
        self._autorun_output = None
        self.current_run_kind = None
        self.current_agent_id = None
        self._add_close_button(output)
