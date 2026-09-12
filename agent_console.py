"""Panel inferior para ejecutar agentes o comandos y mostrar su salida en vivo."""

import json
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
    QHBoxLayout, QPlainTextEdit, QPushButton,
    QComboBox, QLabel, QMessageBox, QTabBar, QTabWidget, QVBoxLayout, QWidget,
)
from paths import COPILOT_PROFILES_DIR, COPILOT_USAGE_DIR
from paths import IA_DATA_DIR

from ai_manager import (
    AGENT_DEFS,
    AgentConfigStore,
    validate_autorun_command,
)
from file_ops import GitVersioning
from automation import build_autorun_plan
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
        self.processes = set()
        self._stopping_processes = set()
        self._process_outputs = {}
        self._active_folder = None
        self.current_agent_id = None
        self.current_run_kind = None
        self.copilot_login_process = None
        self.autorun_process = None
        self._autorun_output = None
        self._autorun_queue = []
        self._autorun_failures = []
        self._autorun_auto_commit = False
        self._pending_runs = []
        self._queue_drain_scheduled = False
        self.current_output = None
        self._log_file = None
        self._current_task = None
        self._git_start_head = None
        self._copilot_output_buffer = ""
        self._copilot_quota_detected = False
        self._copilot_retry_pending = False
        self._copilot_original_task = ""
        self._copilot_allow_all_paths = False
        self._copilot_profiles_tried = set()
        self._copilot_login_buffer = ""
        self._copilot_auth_urls_seen = set()
        self._copilot_usage_output_file = None
        self._auth_warning_shown = set()
        self._highlighters = []
        self._collection_review_callback = None
        self._agent_completion_callback = None
        self.setVisible(False)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.task_edit = TaskInput(self.run_agent)
        self.task_edit.setFixedHeight(76)
        self.task_edit.setPlaceholderText(
            "copilot/codex/gemini/groq seguido de la tarea, o un comando de terminal... "
            "(--resume=<id> para retomar una sesión; -continue copilot para la pestaña activa; "
            "Shift+Enter para ejecutar)"
        )
        apply_console_style(self.task_edit)
        controls.addWidget(self.task_edit, 1)
        buttons = QVBoxLayout()
        self.run_btn = QPushButton("▶ Ejecutar")
        self.run_btn.clicked.connect(self.run_agent)
        buttons.addWidget(self.run_btn)
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

    def has_running_processes(self) -> bool:
        """Indica si queda algún proceso de agente, login o verificación activo."""
        return bool(
            self.processes
            or self.autorun_process is not None
            or self.copilot_login_process is not None
        )

    def _has_active_execution(self) -> bool:
        """Indica si la consola está ocupada con una ejecución o su autorun."""
        return bool(self.processes or self.autorun_process is not None)

    def _queue_run(self, raw_task, folder, completion_callback):
        self._pending_runs.append(
            {
                "task": raw_task,
                "folder": folder,
                "completion_callback": completion_callback,
            }
        )
        self.task_edit.clear()

    def _start_next_queued_run(self):
        if self._queue_drain_scheduled or self._has_active_execution():
            return
        self._queue_drain_scheduled = True

        def start():
            self._queue_drain_scheduled = False
            if self._has_active_execution() or not self._pending_runs:
                return
            queued = self._pending_runs.pop(0)
            if not self.select_directory(queued["folder"]):
                self._new_tab(
                    "command",
                    f"Directorio inexistente: {queued['folder']}",
                    finished=True,
                )
                self._start_next_queued_run()
                return
            self.task_edit.setPlainText(queued["task"])
            self.run_agent(queued["completion_callback"], _from_queue=True)

        QTimer.singleShot(0, start)

    def _working_folder(self) -> str:
        return str(self.directory_combo.currentData() or "")

    def _run_folder(self) -> str:
        return self._active_folder or self._working_folder()

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
        if index < 0 and current_folder and Path(current_folder).is_dir():
            self.directory_combo.addItem("Contexto de la ejecución", current_folder)
            index = self.directory_combo.findData(current_folder)
        if index >= 0:
            self.directory_combo.setCurrentIndex(index)
        self.directory_combo.blockSignals(False)
        self.directory_combo.setEnabled(bool(directories))
        if not directories:
            self.directory_combo.addItem("No hay carpetas configuradas en Colecciones")

    def select_directory(self, folder: str) -> bool:
        """Selecciona la carpeta de una Colección para la próxima ejecución."""
        self._refresh_directory_options()
        index = self.directory_combo.findData(folder)
        if index < 0:
            if not folder or not Path(folder).is_dir():
                return False
            self.directory_combo.addItem("Contexto del agente de tareas", folder)
            index = self.directory_combo.findData(folder)
        self.directory_combo.setCurrentIndex(index)
        return True

    def run_collection_review(
        self, task_text: str, context_dir: str, summary_path: str, callback
    ):
        """Relaciona una tarea con Colecciones usando Gemini y luego Copilot."""
        if not self.select_directory(context_dir):
            callback("")
            return
        copilot_prompt = (
            "Revisá la tarea siguiente contra el índice general de Colecciones "
            "ubicado en este directorio de contexto. "
            "No modifiques ningún archivo ni ejecutes comandos. Determiná qué "
            "Colección existente contiene el repositorio o los repositorios que "
            "la tarea quiere modificar. Usá primero el resumen de contenido y jerarquía; "
            "considerá también la sección Tags de cada Colección como ayuda semántica; "
            "si el índice no alcanza para decidir, leé los README individuales "
            "disponibles en el subdirectorio 'readmes' de este contexto hasta "
            "encontrar la Colección correcta; si sigue sin ser suficiente, leé "
            "todos los README. "
            "Si la tarea menciona varios repositorios, elegí la Colección raíz "
            "que contiene sus carpetas, en lugar de crear una Colección nueva. "
            "Una tarea que pide actualizar, revisar o hacer commits en repositorios "
            "existentes corresponde a una Colección existente aunque no mencione "
            "su nombre exacto. "
            "Respondé al final con una única línea exactamente en este formato JSON, "
            "sin markdown: "
            'WEBAGENT_COLLECTION_RESULT: {"collection_ids":["id"],"create_name":null}. '
            "Usá los IDs asociados a las rutas. Dejá "
            '"collection_ids":[] y proponé un nombre breve en create_name únicamente '
            "si la tarea realmente requiere un repositorio que no existe en la lista.\n\n"
            f"TAREA:\n{task_text}\n\nÍNDICE GENERAL:\n{summary_path}"
        )
        self._collection_review_callback = callback
        profile_id = self._profile_id()
        gemini_key = self.config_store.get_profile_agent_token(
            context_dir, profile_id, "gemini"
        )
        if gemini_key:
            try:
                summary = Path(summary_path).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                summary = ""
            gemini_prompt = (
                "Clasificá la tarea contra el índice de Colecciones incluido abajo. "
                "Usá especialmente los Tags, README, jerarquía, carpetas y marcadores "
                "como evidencia; compará todas las Colecciones antes de decidir. "
                "No modifiques archivos. Respondé al final con una única línea "
                'exactamente en este formato JSON: WEBAGENT_COLLECTION_RESULT: '
                '{"collection_ids":["id"],"create_name":null}. '
                "Elegí una Colección existente si la tarea se refiere a un proyecto "
                "o repositorio ya presente. Si la tarea describe una parte de un "
                "proyecto existente, elegí ese proyecto aunque no nombre la Colección "
                "literalmente. Sólo devolvé collection_ids vacío si ninguna Colección "
                "es compatible.\n\n"
                f"TAREA:\n{task_text}\n\nÍNDICE:\n{summary}"
            )
            self.task_edit.setPlainText(f"gemini {gemini_prompt}")
            self.setVisible(True)
            def finish_gemini_review(output, exit_code):
                if exit_code == 0 and output.strip():
                    self._collection_review_callback = None
                    callback(output)
                else:
                    self._run_collection_review_with_copilot(
                        copilot_prompt, callback
                    )

            self.run_agent(finish_gemini_review)
            return
        self._run_collection_review_with_copilot(copilot_prompt, callback)

    def _run_collection_review_with_copilot(self, prompt: str, callback):
        if shutil.which("copilot") is None:
            self._collection_review_callback = None
            callback("")
            return
        self.task_edit.setPlainText(f"copilot {prompt}")
        self.setVisible(True)
        self.run_agent()

    def run_cli_help(self, agent_id: str):
        """Muestra la ayuda del agente seleccionado en una pestaña de consola."""
        if agent_id not in ("copilot", "codex", "gemini", "groq"):
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
        self._copilot_usage_output_file = None
        self.current_run_kind = "help"
        self._active_folder = folder
        self._start_log(agent_id, folder, command, command)
        self._new_tab(agent_id, "\n", prompt=f"$ {command}")
        self.process = QProcess(self)
        self.processes.add(self.process)
        self._process_outputs[self.process] = self.current_output
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

    def _save_gemini_response(self, task: str, response: str):
        """Guarda la respuesta Markdown junto al repositorio consultado."""
        if self.current_agent_id != "gemini" or not response.strip():
            return
        folder = Path(self._active_folder or "")
        if not folder.is_dir() or self._log_file is None:
            return

        response_path = folder / f"{self._log_file.stem}.md"
        suffix = 2
        while response_path.exists():
            response_path = folder / f"{self._log_file.stem}-{suffix}.md"
            suffix += 1
        content = (
            "# Respuesta de Gemini\n\n"
            f"- **Fecha:** {datetime.now().isoformat()}\n"
            f"- **Log:** `{self._log_file.name}`\n\n"
            "## Pregunta\n\n"
            f"{task.strip()}\n\n"
            "## Respuesta\n\n"
            f"{response.strip()}\n"
        )
        try:
            response_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            warning = f"\n--- No se pudo guardar la respuesta de Gemini: {exc} ---\n"
            self._write_log(warning)
            if self.current_output:
                self.current_output.appendPlainText(warning)

    def _write_git_diff(self):
        """Guarda en el log el diff exacto producido por la ejecución."""
        folder = self._run_folder()
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
        environment = QProcessEnvironment.systemEnvironment()
        for name in (
            "COPILOT_HOME", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
            "GEMINI_API_KEY", "GROQ_API_KEY",
        ):
            environment.remove(name)
        environment.insert("COPILOT_HOME", str(COPILOT_PROFILES_DIR / profile_id))
        environment.insert("BROWSER", "cmd.exe /c exit 0")
        token = self.config_store.get_profile_agent_token(
            folder, profile_id, agent_id
        )
        if agent_id == "copilot" and token:
            environment.insert("COPILOT_GITHUB_TOKEN", token)
        if agent_id == "gemini" and token:
            environment.insert("GEMINI_API_KEY", token)
        if agent_id == "groq" and token:
            environment.insert("GROQ_API_KEY", token)
        return environment

    def run_agent(self, completion_callback=None, _from_queue=False):
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
        if not _from_queue and self._has_active_execution():
            self._queue_run(raw_task, self._working_folder(), completion_callback)
            return
        if completion_callback is not None:
            self._agent_completion_callback = completion_callback
        parts = raw_task.split(None, 1)
        agent_id = parts[0].lower().rstrip(":")
        retrying_copilot = self._copilot_retry_pending
        if retrying_copilot:
            agent_id = "copilot"
            task = self._copilot_original_task
            allow_all_paths = self._copilot_allow_all_paths
            self._copilot_retry_pending = False
        else:
            self._profile_override = None
            task = parts[1].strip() if len(parts) > 1 else ""
            allow_all_paths = False
        continue_requested = False
        if parts[0].lower() in ("-continue", "--continue"):
            continue_requested = True
            continuation_parts = task.split(None, 1)
            if not continuation_parts:
                self._new_tab(
                    "copilot",
                    "Indicá «-continue copilot» y, opcionalmente, la nueva tarea.",
                    finished=True,
                )
                return
            agent_id = continuation_parts[0].lower().rstrip(":")
            task = continuation_parts[1].strip() if len(continuation_parts) > 1 else ""
            if agent_id != "copilot":
                self._new_tab(
                    agent_id,
                    "La opción «-continue» sólo está disponible para Copilot.",
                    finished=True,
                )
                return
        if agent_id == "copilot" and not retrying_copilot:
            task, explicit_resume_id = self._extract_copilot_resume(task)
            task, allow_all_paths = self._extract_copilot_path_override(task)
        else:
            explicit_resume_id = None
        if agent_id not in AGENT_DEFS:
            self._run_command(raw_task)
            return
        resume_requested = continue_requested or explicit_resume_id is not None
        if not task and not resume_requested:
            self._new_tab(
                agent_id,
                f"Falta la tarea después de «{parts[0]}».",
                finished=True,
            )
            return
        self.current_agent_id = agent_id
        self._copilot_usage_output_file = None
        self._current_task = task
        if agent_id == "copilot":
            self._copilot_allow_all_paths = allow_all_paths
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
        resume_id = None
        if continue_requested:
            resume_id = explicit_resume_id or self._resume_id_from_current_tab()
            if not resume_id:
                self._new_tab(
                    agent_id,
                    "La pestaña de consola abierta no contiene un resume de Copilot.",
                    finished=True,
                )
                return
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            self._new_tab(agent_id, f"Comando inválido: {exc}", finished=True)
            return
        script_dir = str(Path(__file__).resolve().parent)
        argv = [token.replace("{prompt}", prompt).replace("{script_dir}", script_dir) for token in tokens]
        if agent_id == "copilot":
            # Los permisos globales sólo se habilitan mediante la bandera explícita
            # escrita al final de la tarea.
            argv = [
                token for token in argv
                if token not in ("--allow-all-paths", "--allow-all")
            ]
            if continue_requested:
                argv.extend([f"--resume={resume_id}"])
                if not task:
                    for index in range(len(argv) - 1, 0, -1):
                        if argv[index] == "" and argv[index - 1] in ("-p", "--prompt"):
                            del argv[index - 1:index + 1]
                            break
            elif explicit_resume_id:
                argv.extend([f"--resume={explicit_resume_id}"])
                if not task:
                    for index in range(len(argv) - 1, 0, -1):
                        if argv[index] == "" and argv[index - 1] in ("-p", "--prompt"):
                            del argv[index - 1:index + 1]
                            break
            argv.extend(
                argument
                for folder_path in self._selected_collection_directory_for_copilot()
                for argument in ("--add-dir", folder_path)
            )
            if allow_all_paths:
                argv.append("--allow-all-paths")
        if retrying_copilot:
            prompt = self._copilot_fallback_prompt()
            argv = [
                "copilot", "-p", prompt, "--allow-all-tools",
                "--allow-all-urls",
            ]
            argv.extend(
                argument
                for folder_path in self._selected_collection_directory_for_copilot()
                for argument in ("--add-dir", folder_path)
            )
            if allow_all_paths:
                argv.append("--allow-all-paths")
            self._copilot_quota_detected = False
        if not argv or shutil.which(argv[0]) is None:
            self._new_tab(
                agent_id,
                f"No se encontró el comando: {argv[0] if argv else '(vacío)'}",
                finished=True,
            )
            return
        if agent_id == "copilot":
            argv = self._attach_copilot_usage_output(argv)
        if agent_id == "gemini":
            prompt_for_process = prompt + self._repository_context_for_gemini(prompt)
            argv = [
                token.replace("{prompt}", prompt_for_process).replace(
                    "{script_dir}", script_dir
                )
                for token in tokens
            ]
        self._start_log(agent_id, folder, " ".join(argv), task)
        if self._copilot_usage_output_file is not None:
            self._write_log(
                f"usage_output_file: {self._copilot_usage_output_file}\n"
            )
        self._new_tab(
            agent_id,
            "\n",
            prompt=f"$ {' '.join(argv)}",
        )
        self.current_run_kind = "agent"
        self._active_folder = folder
        self.process = QProcess(self)
        self.processes.add(self.process)
        self._process_outputs[self.process] = self.current_output
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
        self.task_edit.clear()

    @staticmethod
    def _extract_copilot_path_override(task: str) -> tuple[str, bool]:
        """Extrae ``--allow-all-paths`` cuando aparece al final de la tarea."""
        match = re.search(r"(?:^|\s)--allow-all-paths\s*$", task)
        if not match:
            return task, False
        return task[: match.start()].rstrip(), True

    @staticmethod
    def _extract_copilot_resume(task: str) -> tuple[str, str | None]:
        """Extrae ``--resume=<id>`` para pasarlo como opción real de Copilot."""
        match = re.search(
            r"(?:^|\s)--resume(?:=|\s+)([A-Za-z0-9._:-]+)(?=\s|$)",
            task,
            flags=re.IGNORECASE,
        )
        if not match:
            return task, None
        remaining = (task[: match.start()] + task[match.end():]).strip()
        return remaining, match.group(1)

    def _selected_collection_directory_for_copilot(self) -> list[str]:
        """Devuelve sólo la carpeta seleccionada para ``copilot --add-dir``."""
        folder = self._working_folder()
        if not folder:
            return []
        path = Path(folder).expanduser()
        return [str(path.resolve())] if path.is_dir() else []

    def _attach_copilot_usage_output(self, argv: list[str]) -> list[str]:
        """Agrega un archivo de métricas fuera del workspace para esta corrida."""
        cleaned = []
        index = 0
        while index < len(argv):
            if argv[index] == "--usage-output-file":
                index += 2
                continue
            if argv[index].startswith("--usage-output-file="):
                index += 1
                continue
            cleaned.append(argv[index])
            index += 1

        profile_id = self._profile_id() or "unknown-profile"
        output_dir = COPILOT_USAGE_DIR / profile_id
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_file = output_dir / f"{timestamp}_copilot.json"
        self._copilot_usage_output_file = output_file
        cleaned.extend(["--usage-output-file", str(output_file)])
        return cleaned

    def _record_copilot_usage(self):
        """Registra métricas finales en el log sin copiar datos sensibles."""
        usage_file = self._copilot_usage_output_file
        if self.current_agent_id != "copilot" or usage_file is None:
            return
        if not usage_file.is_file():
            self._write_log(
                f"\n--- Copilot no generó estadísticas: {usage_file} ---\n"
            )
            return
        try:
            usage = json.loads(usage_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._write_log(
                f"\n--- No se pudieron leer estadísticas de Copilot: {exc} ---\n"
            )
            return
        if not isinstance(usage, dict):
            self._write_log(
                "\n--- Estadísticas de Copilot con formato inesperado ---\n"
            )
            return
        selected = {
            key: usage[key]
            for key in (
                "ai_credits",
                "duration",
                "model",
                "input_tokens",
                "output_tokens",
                "total_tokens",
            )
            if key in usage
        }
        self._write_log(
            "\n=== Estadísticas de Copilot ===\n"
            + json.dumps(selected, ensure_ascii=True, sort_keys=True)
            + "\n"
        )

    def _resume_id_from_current_tab(self) -> str | None:
        """Extrae el identificador de resume de la pestaña de consola activa."""
        output = self._output_for_tab_content(self.tabs.currentWidget())
        text = output.toPlainText() if output else ""
        match = re.search(
            r"\bResume\s+copilot\s+--resume[=\s]+([A-Za-z0-9._:-]+)",
            text,
            flags=re.IGNORECASE,
        )
        return match.group(1) if match else None

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
        self._active_folder = folder
        self._start_log("command", folder, command, command)
        self._new_tab("command", "\n", prompt=f"$ {command}")
        self.process = QProcess(self)
        self.processes.add(self.process)
        self._process_outputs[self.process] = self.current_output
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
        self.task_edit.clear()

    def _new_tab(self, agent_id, initial="", finished=False, prompt=""):
        self.current_output = QPlainTextEdit()
        self.current_output.setReadOnly(True)
        self.current_output.setFont(QFont("Consolas", 10))
        apply_console_style(self.current_output)
        self._highlighters.append(ConsoleHighlighter(self.current_output.document()))
        self.current_output.setPlainText(initial)
        header = QLabel(prompt)
        header.setWordWrap(True)
        header.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        header.setStyleSheet(
            f"background-color: {CONSOLE_BG}; color: #e2a63b; "
            f"border: 1px solid {CONSOLE_BORDER}; padding: 6px;"
        )
        tab_content = QWidget()
        tab_layout = QVBoxLayout(tab_content)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        if prompt:
            tab_layout.addWidget(header)
        tab_layout.addWidget(self.current_output, 1)
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
        index = self.tabs.addTab(tab_content, label)
        self.tabs.setCurrentIndex(index)
        self._add_close_button(self.current_output, enabled=True)

    def _add_close_button(self, output, enabled=True):
        """Crea el botón de cierre y controla cuándo puede usarse."""
        index = self._tab_index_for_output(output)
        if index < 0:
            return
        close_btn = self.tabs.tabBar().tabButton(index, QTabBar.ButtonPosition.RightSide)
        if close_btn is not None:
            close_btn.setEnabled(enabled)

    def _process_for_output(self, output):
        return next(
            (process for process, process_output in self._process_outputs.items()
             if process_output is output),
            None,
        )

    def _tab_content_for_output(self, output):
        if output is None:
            return None
        return output.parentWidget()

    def _tab_index_for_output(self, output):
        return self.tabs.indexOf(self._tab_content_for_output(output))

    def _output_for_tab_content(self, tab_content):
        return tab_content.findChild(QPlainTextEdit) if tab_content else None

    def _close_output_tab(self, tab_content):
        output = self._output_for_tab_content(tab_content)
        index = self.tabs.indexOf(tab_content)
        if index < 0:
            return
        process = self._process_for_output(output)
        if process is not None:
            process.kill()
            return
        if self.autorun_process is not None and self._autorun_output is output:
            self.autorun_process.kill()
            return
        if self.copilot_login_process is not None and output is self.current_output:
            self.copilot_login_process.kill()
            return
        self.tabs.removeTab(index)
        output.deleteLater()
        if self.current_output is output:
            self.current_output = None

    def _read_output(self):
        process = self.sender()
        output = self._process_outputs.get(process)
        if process and output:
            data = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
            self._write_log(data)
            if self.current_run_kind == "agent" and self.current_agent_id == "copilot":
                self._copilot_output_buffer += data
                if self._looks_like_copilot_quota(self._copilot_output_buffer):
                    self._copilot_quota_detected = True
                self._open_copilot_auth_url(data)
            if self.current_run_kind == "agent":
                self._detect_auth_error(self.current_agent_id, data)
            output.insertPlainText(data)
            output.moveCursor(output.textCursor().MoveOperation.End)

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

    def _repository_context_for_gemini(self, task: str):
        """Incluye sólo las rutas del repositorio mencionadas con ``@``."""
        folder = Path(self._working_folder())
        if not folder.is_dir():
            return (
                "\n\nCONTEXTO DEL PROYECTO:\n"
                "No se pudo inspeccionar la carpeta de trabajo. No asumas un stack "
                "distinto; pedí el contexto faltante si es necesario."
            )

        git_context = ""
        if GitVersioning.has_repo(str(folder)):
            branch = GitVersioning.get_current_branch(str(folder)) or "(detached)"
            ok, status, _ = GitVersioning.run(
                str(folder), ["status", "--short"], timeout=10
            )
            git_context = (
                f"\nRama actual: {branch}\n"
                f"Estado Git:\n{status[:1500] if ok else '(no disponible)'}\n"
            )

        references = []
        for match in re.finditer(r"(?<!\w)@([^\s]+)", task):
            raw_reference = match.group(1).rstrip(".,;:!?)]}\"'")
            if not raw_reference:
                continue
            candidate = Path(raw_reference)
            if candidate.is_absolute() or ".." in candidate.parts:
                continue
            resolved = folder / candidate
            if not resolved.exists():
                continue
            relative = resolved.relative_to(folder)
            if any(part in {".git", "__pycache__", "profiles", "cache"} for part in relative.parts):
                continue
            display = str(relative)
            if resolved.is_dir():
                display += "\\"
            if display not in references:
                references.append(display)

        references_context = ""
        if references:
            references_context = (
                "\nRutas del repositorio mencionadas explícitamente en la tarea:\n- "
                + "\n- ".join(references)
                + "\n"
            )

        return (
            "\n\nCONTEXTO OBLIGATORIO DEL PROYECTO:\n"
            "Este repositorio es WebAgent, una aplicación de escritorio para Windows "
            "escrita en Python con PyQt6 y PyQt6-WebEngine. No es una aplicación web "
            "React/TypeScript/Tailwind y no debes inventar componentes, APIs HTTP ni "
            "archivos que no existan. Antes de proponer cambios, usa las rutas y el "
            "código real indicado abajo. Si la tarea es ambigua, explica qué archivos "
            "reales se deben modificar y conserva las convenciones existentes.\n"
            f"Carpeta de trabajo: {folder}\n"
            "No se envía un listado automático del repositorio. Para aportar contexto "
            "de una ruta, mencionála en la tarea con @ruta/ o @archivo.\n"
            + references_context
            + git_context
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

    def _finished(self, exit_code, _status):
        process = self.sender()
        output = self._process_outputs.pop(process, None)
        self.processes.discard(process)
        was_stopped = process in self._stopping_processes
        self._stopping_processes.discard(process)
        if process is self.process:
            self.process = None
        if process and output:
            data = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
            if data:
                self._write_log(data)
                output.insertPlainText(data)
                output.moveCursor(output.textCursor().MoveOperation.End)
        if output:
            output.appendPlainText(f"\n--- terminó (código {exit_code}) ---")
        self._write_log(f"\n--- terminó (código {exit_code}) ---\n")
        self._record_copilot_usage()
        run_kind = self.current_run_kind
        result = output.toPlainText() if output else ""
        if run_kind == "agent" and exit_code == 0:
            response = re.split(r"\n--- terminó \(código -?\d+\) ---", result, maxsplit=1)[0]
            self._save_gemini_response(self._current_task or "", response)
        if run_kind == "agent" and self._agent_completion_callback:
            callback = self._agent_completion_callback
            self._agent_completion_callback = None
            self.current_run_kind = None
            self.current_agent_id = None
            self._write_git_diff()
            self._add_close_button(output)
            callback(result, exit_code)
            self._start_next_queued_run()
            return
        if run_kind == "agent" and self._collection_review_callback:
            callback = self._collection_review_callback
            self._collection_review_callback = None
            self.current_run_kind = None
            self.current_agent_id = None
            self._write_git_diff()
            self._add_close_button(output)
            callback(result if exit_code == 0 else "")
            self._start_next_queued_run()
            return
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
            if output:
                output.appendPlainText(warning)
                self._add_close_button(output)
            self._copilot_retry_pending = True
            self.current_run_kind = None
            self.current_agent_id = None
            QTimer.singleShot(250, self.run_agent)
            return
        if run_kind not in ("agent", "help"):
            self.current_run_kind = None
            self.current_agent_id = None
            self._write_git_diff()
            self._add_close_button(output)
            self._start_next_queued_run()
            return
        if was_stopped:
            self._finish_after_autorun(output)
            return
        config = AgentConfigStore().get(self._run_folder())
        autorun = config.get("autorun", {})
        if autorun.get("enabled"):
            command = autorun.get("command", "").strip()
            commands = [command] if command else (
                build_autorun_plan(self._run_folder())
                if autorun.get("auto_detect", True) else []
            )
            self._autorun_auto_commit = bool(autorun.get("auto_commit", True))
            if commands and self._run_autorun(commands):
                return
            self._finish_after_autorun(output)
        else:
            self._finish_after_autorun(output)

    def _finish_after_autorun(self, output):
        self._write_git_diff()
        self.current_run_kind = None
        self.current_agent_id = None
        self._active_folder = None
        self._add_close_button(output)
        self._start_next_queued_run()

    def _has_started_changes(self):
        folder = self._run_folder()
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

    def _run_autorun(self, commands):
        if self.autorun_process is not None:
            return False
        if not self._profile_id():
            return False
        self._autorun_queue = list(commands)
        self._autorun_failures = []
        for command in self._autorun_queue:
            valid, error = validate_autorun_command(command)
            if not valid:
                self._write_log(f"\n--- autorun bloqueado: {error} ---\n")
                return False
        return self._start_next_autorun()

    def _start_next_autorun(self):
        if not self._autorun_queue:
            return False
        command = self._autorun_queue.pop(0)
        output = self.current_output
        self._autorun_output = output
        if output:
            output.appendPlainText(
                f"\n--- check local: {command} "
                f"({len(self._autorun_queue) + 1} pendiente(s)) ---\n"
            )
        self._write_log(f"\n--- autorun: {command} ---\n")
        process = QProcess(self)
        self.autorun_process = process
        process.setWorkingDirectory(self._run_folder())
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
        process = self.autorun_process
        was_stopped = process in self._stopping_processes
        self._stopping_processes.discard(process)
        if output:
            output.appendPlainText(
                f"\n--- autorun terminó (código {exit_code}) ---"
            )
        self._write_log(f"\n--- check terminó (código {exit_code}) ---\n")
        if was_stopped:
            self._autorun_queue.clear()
        elif exit_code != 0:
            self._autorun_failures.append(
                "El check falló; revisar la salida anterior antes de pedir ayuda al agente."
            )
            QMessageBox.warning(
                self,
                "Check del proyecto falló",
                "El error quedó registrado localmente. No se envió automáticamente "
                "la salida completa al agente.",
            )
            self._autorun_queue.clear()
        else:
            if self._start_next_autorun():
                return
        if not was_stopped:
            self._commit_autorun_changes()
        self.autorun_process = None
        self._autorun_output = None
        self._finish_after_autorun(output)

    def _commit_autorun_changes(self):
        """Cierra Git sin pedirle al agente que haga commit."""
        folder = self._run_folder()
        if (
            not self._autorun_auto_commit
            or not GitVersioning.has_repo(folder)
            or self._autorun_failures
        ):
            return
        if GitVersioning.is_clean(folder) or not GitVersioning.check_identity(folder):
            return
        if GitVersioning.commit_all(folder, "chore: verificar cambios del proyecto"):
            self._write_log("\n--- Git: cambios verificados y commiteados automáticamente ---\n")
