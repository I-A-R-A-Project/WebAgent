"""
ai_manager.py - "Agentes IA" para WebAgent.

Le permite, para cualquier carpeta versionada con git (perfil o
Colección), desde un diálogo con pestañas:
  - Config: conectar un repositorio remoto que el usuario ya creó a
    mano en GitHub (u otro host, pegando su URL HTTPS o SSH — sin
    manejar tokens, usa las credenciales de git ya configuradas en el
    sistema) y configurar una rama "cruda" (donde caen los commits
    automáticos de WebAgent al descargar archivos) y una rama
    "limpia" de destino.
  - Codex: revisa/reescribe el historial de la rama cruda y arma
    commits prolijos y descriptivos en la rama limpia.
  - Copilot: agente principal capaz de leer y modificar código y
    documentación, además de ejecutar verificaciones del proyecto.
Cada agente tiene su propio comando configurable; las tareas y la salida
se gestionan en la consola inferior de la ventana principal.
"""

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QTextEdit,
    QPushButton, QLabel, QDialogButtonBox, QMessageBox, QGroupBox,
    QTabWidget, QWidget, QComboBox, QCheckBox
)
from PyQt6.QtGui import QFont

from file_ops import GitVersioning
from paths import COPILOT_PROFILES_DIR, IA_DATA_DIR


AUTORUN_ALLOWED_COMMANDS = {
    "git", "git.exe", "git.cmd", "npm", "npm.exe", "npm.cmd",
    "python", "python.exe", "py", "pytest", "pytest.exe",
    "cargo", "cargo.exe", "go", "go.exe", "dotnet", "dotnet.exe",
}


def validate_autorun_command(command: str) -> tuple[bool, str]:
    """Valida que autorun ejecute un binario de verificación permitido."""
    command = command.strip()
    if not command:
        return False, "El comando no puede estar vacío."
    if re.search(r"[;&|<>()`\r\n%]", command) or "$(" in command:
        return False, "No se permiten operadores, redirecciones ni sustitución de comandos."
    match = re.match(r"""^\s*(['"]?)([A-Za-z0-9_.-]+)\1(?:\s|$)""", command)
    if not match or match.group(2).lower() not in AUTORUN_ALLOWED_COMMANDS:
        return False, "El comando debe comenzar con un verificador permitido (git, npm, python, pytest, cargo, go o dotnet)."
    return True, ""


# ======================================================================
# Definición de los agentes: comando por defecto, prompt, requisitos
# ======================================================================

AGENT_DEFS = {
    "codex": {
        "label": "🧹 Codex — Limpieza de commits",
        "short_label": "Codex",
        "description": "Analiza la rama cruda y reescribe el historial en la rama limpia con commits prolijos y descriptivos.",
        "default_command": 'codex exec --sandbox workspace-write "{prompt}"',
        "needs_task": False,
        "needs_source_branch": True,
        "prompt_template": "",
        "check_binary": "codex",
        "install_hint": "npm install -g @openai/codex   y luego  codex login",
    },
    "copilot": {
        "label": "🤖 Copilot — Ejecutor principal",
        "short_label": "Copilot",
        "description": "Ejecutor AI: puede leer y modificar código y documentación, generar commits atómicos y ejecutar verificaciones de proyecto.",
        "default_command": (
            'copilot -p "{prompt}" --allow-all-tools '
            "--allow-all-paths --allow-all-urls"
        ),
        "needs_task": False,
        "needs_source_branch": True,
        "prompt_template": "",
        "check_binary": "copilot",
        "install_hint": "requiere GitHub Copilot CLI (docs.github.com/copilot) y un plan de Copilot activo",
    },
    "gemini": {
        "label": "✨ Gemini — Google AI Studio",
        "short_label": "Gemini",
        "description": "Consulta los modelos Gemini directamente mediante la API de Google AI Studio.",
        "default_command": 'python "{script_dir}/scripts/gemini_agent.py" --prompt "{prompt}"',
        "needs_task": False,
        "needs_source_branch": False,
        "prompt_template": "",
        "check_binary": "python",
        "install_hint": "requiere Python y una API key de Google AI Studio (https://aistudio.google.com/u/3/docs)",
    },
    "groq": {
        "label": "⚡ Groq — Inferencia rápida",
        "short_label": "Groq",
        "description": "Consulta modelos open source mediante la API compatible con OpenAI de Groq.",
        "default_command": 'python "{script_dir}/scripts/groq_agent.py" --prompt "{prompt}"',
        "needs_task": False,
        "needs_source_branch": False,
        "prompt_template": "",
        "check_binary": "python",
        "install_hint": "requiere Python y una API key de Groq (https://console.groq.com/docs/overview)",
    },

}

AGENT_ORDER = ["gemini", "copilot", "codex", "groq"]

# Comandos por defecto de versiones anteriores que ya no aplican (se
# migran solos al default actual si el usuario nunca los tocó a mano).
LEGACY_DEFAULT_COMMANDS = {
    "copilot": ['copilot -p "{prompt}" --allow-all --no-ask-user'],
}


# ======================================================================
# Configuración persistente por carpeta
# ======================================================================

class AgentConfigStore:
    """Guarda, por carpeta, ramas, comandos y credenciales por perfil.
    Clave = ruta absoluta de la carpeta."""

    def __init__(self):
        self.base_dir = IA_DATA_DIR
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
        self._migrate_profile_tokens()

    def _migrate_profile_tokens(self):
        """Mueve la disposición intermedia por carpeta al nivel de perfil."""
        migrated = False
        profile_agents = self.data.setdefault("profile_agents", {})
        for folder, entry in list(self.data.items()):
            if folder == "profile_agents":
                continue
            if not isinstance(entry, dict) or "profile_agents" not in entry:
                continue
            for profile_id, agents in entry.pop("profile_agents", {}).items():
                destination = profile_agents.setdefault(profile_id, {})
                for agent_id, config in agents.items():
                    if agent_id not in destination:
                        destination[agent_id] = config
            migrated = True
        if migrated:
            self.save()

    def save(self):
        with open(self.config_file, "w") as f:
            json.dump(self.data, f, indent=2)

    def _default_entry(self) -> dict:
        return {
            "source_branch": "master",
            "target_branch": "main",
            "agents": {
                aid: {"command": defn["default_command"], "last_task": "", "options": {}}
                for aid, defn in AGENT_DEFS.items()
            },
            # Autorun: si enabled=True, ejecutar 'autorun.command' tras un run de agente
            "autorun": {
                "enabled": False,
                "command": "",
                "auto_detect": True,
                "auto_commit": True,
            },
        }

    def _migrate(self, entry: dict) -> dict:
        """Compatibilidad con la config vieja (un solo 'codex_command')
        y asegura que estén los 3 agentes aunque se hayan agregado
        después de que el usuario ya tuviera config guardada. También
        migra comandos por defecto viejos conocidos (ej: el --yolo de
        gemini) al default actual, siempre que el usuario no lo haya
        editado a mano a otra cosa."""
        if "agents" not in entry:
            legacy_cmd = entry.pop("codex_command", None)
            entry["agents"] = {
                aid: {"command": defn["default_command"], "last_task": "", "options": {}}
                for aid, defn in AGENT_DEFS.items()
            }
            if legacy_cmd:
                entry["agents"]["codex"]["command"] = legacy_cmd
        else:
            entry["agents"].pop("anyapi", None)
            for aid, defn in AGENT_DEFS.items():
                entry["agents"].setdefault(
                    aid, {"command": defn["default_command"], "last_task": "", "options": {}}
                )
                entry["agents"][aid].setdefault("options", {})

        # Las credenciales no pertenecen a la configuración de una carpeta.
        for agent_config in entry["agents"].values():
            agent_config.pop("auth_token", None)

        # Ensure autorun key exists for backward compatibility
        entry.setdefault("autorun", {})
        entry["autorun"].setdefault("command", "")
        entry["autorun"].setdefault("enabled", False)
        entry["autorun"].setdefault("auto_detect", True)
        entry["autorun"].setdefault("auto_commit", True)
        entry["source_branch"] = "master"
        entry["target_branch"] = "main"

        for aid, defn in AGENT_DEFS.items():
            current = entry["agents"][aid].get("command", "")
            if current in LEGACY_DEFAULT_COMMANDS.get(aid, []) or (
                aid == "copilot"
                and current in (
                    'copilot -i "{prompt}" --allow-all',
                    'copilot -p "{prompt}"',
                    'copilot -p "{prompt}" --allow-all',
                )
            ):
                entry["agents"][aid]["command"] = defn["default_command"]

        return entry

    def get(self, folder: str) -> dict:
        entry = self.data.get(str(folder))
        if entry is None:
            return self._default_entry()
        return self._migrate(entry)

    def set_branches(self, folder: str, source_branch: str, target_branch: str):
        entry = self.get(folder)
        entry["source_branch"] = source_branch
        entry["target_branch"] = target_branch
        self.data[str(folder)] = entry
        self.save()

    def set_agent_field(self, folder: str, agent_id: str, **kwargs):
        entry = self.get(folder)
        entry["agents"][agent_id].update(kwargs)
        self.data[str(folder)] = entry
        self.save()

    def get_profile_agent_token(self, folder: str, profile_id: str, agent_id: str) -> str:
        profile_config = self.data.get("profile_agents", {}).get(profile_id, {})
        return profile_config.get(agent_id, {}).get("auth_token", "")

    def set_profile_agent_token(
        self, folder: str, profile_id: str, agent_id: str, token: str
    ):
        profile_config = self.data.setdefault("profile_agents", {}).setdefault(profile_id, {})
        profile_config.setdefault(agent_id, {})["auth_token"] = token
        self.save()

    def set_autorun(self, folder: str, enabled: bool, command: str):
        if enabled:
            valid, error = validate_autorun_command(command)
            if not valid:
                raise ValueError(error)
        entry = self.get(folder)
        entry["autorun"] = {"enabled": bool(enabled), "command": command}
        self.data[str(folder)] = entry
        self.save()


# ======================================================================
# Diálogo principal
# ======================================================================

class AIAgentsDialog(QDialog):
    """Diálogo no modal para configurar agentes y el repositorio del perfil."""

    def __init__(self, parent, folder: str, profile_id: str | None = None,
                 profile_ids: list[str] | None = None, profile_names: dict[str, str] | None = None,
                 profile_changed_handler=None, new_profile_handler=None,
                 rename_profile_handler=None, delete_profile_handler=None,
                 folder_changed_handler=None, git_changed_handler=None,
                 auth_url_handler=None, auth_success_handler=None):
        super().__init__(parent)
        # Normalizamos a separadores nativos del SO (Qt suele devolver
        # rutas con "/" incluso en Windows; con "\" nativo evitamos
        # problemas raros al pasarle la carpeta a cmd.exe / QProcess).
        self.folder = str(Path(folder))
        self.profile_ids = list(profile_ids or [])
        self.profile_id = (
            profile_id if profile_id in self.profile_ids
            else (self.profile_ids[0] if self.profile_ids else None)
        )
        self.profile_names = profile_names or {}
        self.profile_changed_handler = profile_changed_handler
        self.new_profile_handler = new_profile_handler
        self.rename_profile_handler = rename_profile_handler
        self.delete_profile_handler = delete_profile_handler
        self.folder_changed_handler = folder_changed_handler
        self.git_changed_handler = git_changed_handler
        self.auth_url_handler = auth_url_handler
        self.auth_success_handler = auth_success_handler
        self.copilot_home = COPILOT_PROFILES_DIR / (self.profile_id or "")
        self.raw_branch = "master"
        self.profile_branch = "main"
        self.config_store = AgentConfigStore()
        config = self.config_store.get(self.folder)
        self.process: QProcess | None = None
        self.active_agent: str | None = None
        self.agent_widgets: dict = {}
        self.copilot_rotation_in_progress = False
        self.pending_copilot_retry = False
        self.pending_copilot_login = False
        self.last_copilot_prompt = ""
        self.auth_urls_seen = set()
        self.copilot_output_buffer = ""
        self.copilot_auth_code = ""
        self.copilot_quota_detected = False

        self.setWindowTitle(f"Agentes IA — {Path(folder).name}")
        self.resize(680, 640)

        layout = QVBoxLayout(self)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Perfil:"))
        self.profile_combo = QComboBox()
        for item in self.profile_ids:
            self.profile_combo.addItem(self.profile_names.get(item, item), item)
        self.profile_combo.setCurrentIndex(max(0, self.profile_combo.findData(self.profile_id)))
        self.profile_combo.currentIndexChanged.connect(self._select_copilot_profile)
        profile_row.addWidget(self.profile_combo, 1)
        new_btn = QPushButton("+ Nuevo")
        new_btn.clicked.connect(lambda: self.new_profile_handler and self.new_profile_handler())
        profile_row.addWidget(new_btn)
        rename_btn = QPushButton("Renombrar")
        rename_btn.clicked.connect(lambda: self.rename_profile_handler and self.rename_profile_handler(self.profile_combo.currentData()))
        profile_row.addWidget(rename_btn)
        delete_btn = QPushButton("Eliminar")
        delete_btn.clicked.connect(lambda: self.delete_profile_handler and self.delete_profile_handler(self.profile_combo.currentData()))
        profile_row.addWidget(delete_btn)
        layout.addLayout(profile_row)
        self._update_copilot_usage_display()
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel(f"Archivos: {folder}"), 1)
        folder_btn = QPushButton("Cambiar carpeta...")
        folder_btn.clicked.connect(lambda: self.folder_changed_handler and self.folder_changed_handler())
        folder_row.addWidget(folder_btn)
        layout.addLayout(folder_row)
        git_box = QCheckBox("Usar Git para sobrescribir y versionar archivos")
        active_profile = next((p for p in (self.profile_ids or []) if p == self.profile_id), None)
        if active_profile:
            # The main window supplies the authoritative shared setting.
            profile_data = getattr(parent, "profile_manager", None)
            git_box.setChecked(bool(profile_data.get_profile(active_profile).get("git_versioning")) if profile_data else False)
        git_box.toggled.connect(lambda value: self.git_changed_handler and self.git_changed_handler(value))
        layout.addWidget(git_box)
        self.agents_tabs = QTabWidget()

        for agent_id in AGENT_ORDER:
            tab = QWidget()
            self._build_agent_tab(tab, agent_id)
            self.agents_tabs.addTab(tab, AGENT_DEFS[agent_id]["short_label"])
        self._update_copilot_usage_display()
        layout.addWidget(self.agents_tabs, 1)
 
        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)
 
    def _agent_environment(self, agent_id: str):
        if not self.profile_id:
            return None
        self.copilot_home.mkdir(parents=True, exist_ok=True)
        environment = QProcessEnvironment.systemEnvironment()
        for name in (
            "COPILOT_HOME", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
            "GEMINI_API_KEY", "GROQ_API_KEY",
        ):
            environment.remove(name)
        environment.insert("COPILOT_ALLOW_ALL", "1")
        environment.insert("COPILOT_HOME", str(self.copilot_home))
        # Device flow link is opened by WebAgent, never by system browser.
        environment.insert("BROWSER", "cmd.exe /c exit 0")
        token = self.config_store.get_profile_agent_token(
            self.folder, self.profile_id, agent_id
        )
        if token:
            if agent_id == "copilot":
                environment.insert("COPILOT_GITHUB_TOKEN", token)
            elif agent_id == "gemini":
                environment.insert("GEMINI_API_KEY", token)
            elif agent_id == "groq":
                environment.insert("GROQ_API_KEY", token)
        return environment

    def _open_copilot_auth_url(self, text):
        matches = re.findall(
            r"https?://(?:github\.com|github\.[^/\s]+)/(?:login/device|login/oauth/authorize)[^\s<>()\"]*",
            text,
            flags=re.IGNORECASE,
        )
        if matches and self.auth_url_handler:
            url = matches[-1].rstrip(".,);")
            if url not in self.auth_urls_seen:
                self.auth_urls_seen.add(url)
                code_match = re.search(r"\b([A-Z0-9]{4,5}-[A-Z0-9]{4,5})\b", text)
                self.auth_url_handler(
                    url, self.profile_id,
                    code_match.group(1) if code_match else self.copilot_auth_code,
                )
                self.copilot_auth_code = ""

    def _select_copilot_profile(self, index):
        selected = self.profile_combo.itemData(index)
        if not selected or selected == self.profile_id:
            return
        if self.process is not None:
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(
                self.profile_combo.findData(self.profile_id)
            )
            self.profile_combo.blockSignals(False)
            QMessageBox.information(self, "Copilot", "Detené el proceso antes de cambiar de perfil.")
            return
        self.profile_id = selected
        self.copilot_home = COPILOT_PROFILES_DIR / selected
        self._refresh_profile_credentials()
        self._update_copilot_usage_display()
        if self.profile_changed_handler:
            self.profile_changed_handler(selected)

    def _refresh_profile_credentials(self):
        for agent_id in ("copilot", "gemini", "groq"):
            widgets = self.agent_widgets.get(agent_id)
            if not widgets or "token_edit" not in widgets:
                continue
            widgets["token_edit"].setText(
                self.config_store.get_profile_agent_token(
                    self.folder, self.profile_id, agent_id
                )
            )

    def _update_copilot_usage_display(self):
        profile_manager = getattr(self.parent(), "profile_manager", None)
        profile = profile_manager.get_profile(self.profile_id) if profile_manager and self.profile_id else None
        usage = (profile or {}).get("copilot_usage", {}).get("text")
        usage_label = self.agent_widgets.get("copilot", {}).get("usage_label")
        if usage_label is not None:
            usage_label.setText(f"Uso: {usage or 'No disponible'}")

    # ---------- Sección: estado del repo / remoto ----------

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

    def _build_autorun_section(self, layout):
        box = QGroupBox("Autorun al terminar un agente")
        form = QFormLayout(box)
        cfg = self.config_store.get(self.folder)
        autorun = cfg.get("autorun", {})
        enabled = QCheckBox("Ejecutar automáticamente")
        enabled.setChecked(bool(autorun.get("enabled")))
        command = QLineEdit(autorun.get("command", ""))
        command.setPlaceholderText("Vacío: detectar checks por lenguaje; o Ej: npm test")
        hint = QLabel(
            "Los checks corren localmente y en secuencia. Solo los errores quedan "
            "para revisión; Git puede cerrar los cambios automáticamente."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        save = QPushButton("Guardar autorun")
        def save_autorun():
            try:
                self.config_store.set_autorun(
                    self.folder, enabled.isChecked(), command.text().strip()
                )
            except ValueError as exc:
                QMessageBox.warning(self, "Autorun inválido", str(exc))
                return
            QMessageBox.information(
                self, "Autorun guardado",
                "Vacío detecta checks por lenguaje; un comando escrito reemplaza esa secuencia.",
            )
        save.clicked.connect(save_autorun)
        form.addRow(enabled)
        form.addRow("Comando:", command)
        form.addRow("", hint)
        form.addRow("", save)
        layout.addWidget(box)

    # ---------- Sección: un agente ----------

    def _save_agent_config(self, agent_id: str):
        widgets = self.agent_widgets[agent_id]
        self.config_store.set_agent_field(
            self.folder,
            agent_id,
            command=widgets["command_edit"].text().strip() or AGENT_DEFS[agent_id]["default_command"],
        )

    def _build_agent_tab(self, tab: QWidget, agent_id: str):
        defn = AGENT_DEFS[agent_id]
        tab_layout = QVBoxLayout(tab)

        usage_label = QLabel("Uso: No disponible")
        tab_layout.addWidget(usage_label)

        desc = QLabel(defn["description"])
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray; font-size: 11px;")
        tab_layout.addWidget(desc)

        if shutil.which(defn["check_binary"]) is None:
            warn = QLabel(f"⚠ No se encontró '{defn['check_binary']}' en el PATH. Instalación: {defn['install_hint']}")
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #b45309;")
            tab_layout.addWidget(warn)

        cfg = self.config_store.get(self.folder)
        agent_cfg = cfg["agents"][agent_id]

        form = QFormLayout()
        command_edit = QLineEdit(agent_cfg.get("command", defn["default_command"]))
        command_edit.setPlaceholderText("Editá la línea completa; usá {prompt} donde deba ir la tarea.")
        form.addRow("Comando:", command_edit)
        save_command_btn = QPushButton("Guardar comando")
        save_command_btn.clicked.connect(
            lambda: self._save_agent_config(agent_id)
        )
        form.addRow("", save_command_btn)
        tab_layout.addLayout(form)

        btn_row = QHBoxLayout()
        if agent_id in ("copilot", "codex", "gemini", "groq"):
            help_btn = QPushButton("❔ Ver ayuda en la consola")
            help_btn.clicked.connect(lambda: self._show_cli_help(agent_id))
            btn_row.addWidget(help_btn)

        if agent_id in ("copilot", "gemini", "groq"):
            token_edit = QLineEdit(
                self.config_store.get_profile_agent_token(
                    self.folder, self.profile_id, agent_id
                )
            )
            token_edit.setEchoMode(QLineEdit.EchoMode.Password)
            token_row = QHBoxLayout()
            token_row.addWidget(QLabel("API key:" if agent_id != "copilot" else "PAT:"))
            token_row.addWidget(token_edit, 1)
            tab_layout.addLayout(token_row)
            # Keep only the button to append the long template to copilot-instructions.md and login controls
            if agent_id == "copilot":
                add_template_btn = QPushButton("➕ Agregar plantilla a copilot-instructions.md")
                add_template_btn.clicked.connect(lambda: self._add_template_to_instructions())
                btn_row.addWidget(add_template_btn)
            else:
                save_key_btn = QPushButton("Guardar API key")
                save_key_btn.clicked.connect(
                    lambda: self.config_store.set_profile_agent_token(
                        self.folder,
                        self.profile_id,
                        agent_id,
                        token_edit.text().strip(),
                    )
                )
                btn_row.addWidget(save_key_btn)
        tab_layout.addLayout(btn_row)

        self.agent_widgets[agent_id] = {
            "command_edit": command_edit,
            "usage_label": usage_label,
        }
        if agent_id in ("copilot", "gemini", "groq"):
            self.agent_widgets[agent_id]["token_edit"] = token_edit

        if agent_id == "copilot":
            self.agent_widgets[agent_id]["add_template_btn"] = add_template_btn

    def _show_cli_help(self, agent_id: str):
        """Ejecuta el help del CLI en la consola inferior de la ventana."""
        console = getattr(self.parent(), "agent_console", None)
        if console is None:
            QMessageBox.warning(self, "Consola no disponible", "No se encontró la consola de agentes.")
            return
        console.run_cli_help(agent_id)
        console.setVisible(True)
        console.raise_()

    def _regenerate_prompt(self, agent_id: str):
        """Regenerate the prompt from template for agents that use templates.
        For Copilot, keep the prompt untouched (stay empty) — template is only stored in .github/copilot-instructions.md when user requests it.
        """
        if agent_id == "copilot":
            # Intentionally leave Copilot prompt empty and do not auto-fill from template
            return

        return

    # ---------- Sección: log de ejecución (compartido entre agentes) ----------

    def _build_log_section(self, layout):
        layout.addWidget(QLabel("Salida:"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas, Monaco, monospace", 10))
        self.log_view.setMinimumHeight(140)
        layout.addWidget(self.log_view)


    def _append_log(self, text: str):
        self.log_view.append(text)

    # ---------- Ejecutar un agente ----------

    def _run_agent(self, agent_id: str, preview: bool = False):
        defn = AGENT_DEFS[agent_id]

        if not self.profile_id or self.profile_id not in self.profile_ids:
            QMessageBox.warning(
                self,
                "Perfil requerido",
                "Seleccioná un perfil distinto de Default antes de ejecutar un agente.",
            )
            return

        if shutil.which(defn["check_binary"]) is None:
            QMessageBox.warning(
                self, f"{defn['check_binary']} no encontrado",
                f"No se encontró '{defn['check_binary']}' en el PATH.\n\nInstalación: {defn['install_hint']}",
            )
            return

        if not GitVersioning.has_repo(self.folder):
            QMessageBox.warning(self, "Aviso", "Esta carpeta todavía no es un repositorio git.")
            return

        cfg = self.config_store.get(self.folder)
        source = "master"
        target = "main"
        self.config_store.set_branches(self.folder, source, target)

        widgets = self.agent_widgets[agent_id]
        extra = {
            "command": widgets["command_edit"].text().strip() or defn["default_command"],
        }
        # track preview button for UI state
        widgets["preview_btn"] = widgets.get("preview_btn") or None
        if defn["needs_task"]:
            extra["last_task"] = widgets["task_edit"].toPlainText().strip()
        self.config_store.set_agent_field(self.folder, agent_id, **extra)

        if defn["needs_source_branch"] and not GitVersioning.branch_exists(self.folder, source):
            QMessageBox.warning(self, "Aviso", f"La rama cruda '{source}' no existe en este repo.")
            return

        if not GitVersioning.branch_exists(self.folder, target):
            resp = QMessageBox.question(
                self, "Rama destino inexistente",
                f"La rama '{target}' todavía no existe. ¿Crearla ahora?",
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
            base = source if GitVersioning.branch_exists(self.folder, source) else "HEAD"
            if not GitVersioning.create_branch(self.folder, target, base):
                QMessageBox.warning(self, "Error", f"No se pudo crear la rama '{target}'.")
                return

        if not GitVersioning.is_clean(self.folder):
            QMessageBox.warning(
                self, "Working tree sucio",
                "Hay cambios sin commitear en esta carpeta. Commiteá o descartá esos "
                "cambios antes de correr un agente, para no perder nada.",
            )
            return

        ok, _, err = GitVersioning.run(self.folder, ["checkout", target])
        if not ok:
            QMessageBox.warning(self, "Error", f"No se pudo cambiar a la rama '{target}':\n{err}")
            return

        prompt = widgets["prompt_edit"].toPlainText().strip()
        command_template = widgets["command_edit"].text().strip() or defn["default_command"]

        # Aplanamos el prompt a una sola línea (defensivo: cmd.exe puede
        # llegar a comportarse raro con saltos de línea embebidos aunque
        # vayan como un solo argumento) y neutralizamos comillas dobles
        # internas (podrían chocar con las comillas que use el propio
        # cmd.exe al reinterpretar la línea). El cuadro de texto de la
        # UI sigue mostrando el prompt con formato normal.
        prompt_for_process = " ".join(prompt.split()).replace('"', "'")
        if agent_id == "copilot":
            prompt_for_process += self._git_context_for_prompt()

        # If preview requested, create a temporary preview branch from target
        if preview:
            cfg = self.config_store.get(self.folder)
            target_branch = cfg.get("target_branch")
            import time
            preview_branch = f"preview/{int(time.time())}"
            ok, _, err = GitVersioning.run(self.folder, ["checkout", "-b", preview_branch, target_branch])
            if not ok:
                QMessageBox.warning(self, "Error", f"No se pudo crear la rama de preview: {err}")
                return
            self.preview_branch = preview_branch
            self.preview_mode = True

        try:
            argv = self._build_argv(command_template, prompt_for_process)
        except ValueError as e:
            QMessageBox.warning(self, "Comando inválido", f"No se pudo interpretar el comando:\n{e}")
            return
        if not argv:
            QMessageBox.warning(self, "Aviso", "El comando está vacío.")
            return

        try:
            display_command = subprocess.list2cmdline(argv)
        except Exception:
            display_command = " ".join(argv)
        self._append_log(f"\n=== {defn['label']} ===\n$ {display_command}\n(cwd: {self.folder})\n")

        self.active_agent = agent_id
        self._set_other_agents_enabled(agent_id, False)
        widgets["run_btn"].setEnabled(False)
        # disable preview button while running
        # preview button stored as agent_widgets[agent_id]["preview_btn"] if present
        try:
            widgets.get("preview_btn") and widgets["preview_btn"].setEnabled(False)
        except Exception:
            pass
        widgets["stop_btn"].setEnabled(True)

        self.process = QProcess(self)
        self.process.setWorkingDirectory(self.folder)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._on_process_output)
        self.process.finished.connect(self._on_process_finished)

        program, args = argv[0], argv[1:]

        # En Windows, los binarios que instala npm (codex/copilot/gemini)
        # suelen ser shims .cmd/.ps1, que CreateProcess no puede ejecutar
        # directo — hace falta cmd.exe como intérprete. Le pasamos cada
        # token COMO ELEMENTO SEPARADO de la lista (nunca como un string
        # ya armado con comillas), para que Qt aplique su propio
        # escapado una sola vez por argumento, evitando el anidamiento
        # de comillas que rompía el prompt antes.
        if agent_id in ("copilot", "gemini", "groq"):
            self.process.setProcessEnvironment(self._agent_environment(agent_id))

        # If preview mode, ensure we run on the preview branch (already checked out)
        if preview:
            self._append_log(f"ℹ Ejecutando en modo PREVIEW (branch: {getattr(self, 'preview_branch', '(unknown)')})")

        if shutil.which("cmd.exe"):
            self.process.start("cmd.exe", ["/c", program] + args)
        else:
            self.process.start(program, args)

    def _git_context_for_prompt(self):
            status = GitVersioning.run(self.folder, ["status", "--short"], timeout=10)
            diff = GitVersioning.run(self.folder, ["diff", "--", "."], timeout=10)
            recent = GitVersioning.run(self.folder, ["diff", "HEAD~1", "HEAD"], timeout=10)
            branch = GitVersioning.get_current_branch(self.folder)
            return (
                f"\n\nCurrent git context: branch={branch or '(detached)'}\n"
                f"Working tree:\n{status[1][:4000]}\n"
                f"Uncommitted diff:\n{diff[1][:12000]}\n"
                f"Most recent commit diff:\n{recent[1][:12000]}\n"
                "Inspect git diff before changing files so you preserve work from other agents."
            )

    def _start_copilot_login(self):
        if not self.profile_id or self.profile_id not in self.profile_ids:
            QMessageBox.warning(
                self,
                "Perfil requerido",
                "Seleccioná un perfil distinto de Default antes de autenticar Copilot.",
            )
            return
        if self.process is not None:
            QMessageBox.information(self, "Copilot", "Ya hay un proceso ejecutándose.")
            return
        if shutil.which("copilot") is None:
            QMessageBox.warning(self, "Copilot no encontrado", AGENT_DEFS["copilot"]["install_hint"])
            return
        self.active_agent = "copilot"
        self._set_other_agents_enabled("copilot", False)
        self.agent_widgets["copilot"]["run_btn"].setEnabled(False)
        self.agent_widgets["copilot"]["stop_btn"].setEnabled(True)
        self._append_log(f"\n=== Autenticando Copilot ({self.profile_id}) ===\n")
        self.process = QProcess(self)
        self.process.setWorkingDirectory(self.folder)
        self.process.setProcessEnvironment(self._agent_environment("copilot"))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._on_process_output)
        self.process.finished.connect(self._on_process_finished)
        if shutil.which("cmd.exe"):
            self.process.start("cmd.exe", ["/c", "copilot", "login", "--device-code"])
        else:
            self.process.start("copilot", ["login", "--device-code"])

    def _build_argv(self, command_template: str, prompt: str) -> list:
        """Tokeniza el template de comando (ej. 'gemini -p "{prompt}"')
        y devuelve la lista de argumentos con el prompt insertado como
        UN SOLO elemento — nunca como texto embebido dentro de otro
        string — para que no haga falta escapar comillas/saltos de
        línea manualmente al pasarlo al proceso."""
        tokens = shlex.split(command_template)
        argv = []
        script_dir = str(Path(__file__).resolve().parent)
        for tok in tokens:
            if tok == "{prompt}":
                argv.append(prompt)
            elif "{prompt}" in tok:
                argv.append(tok.replace("{prompt}", prompt))
            elif "{script_dir}" in tok:
                argv.append(tok.replace("{script_dir}", script_dir))
            else:
                argv.append(tok)
        return argv

    def _set_other_agents_enabled(self, running_agent: str, enabled: bool):
        for aid, widgets in self.agent_widgets.items():
            if aid == running_agent:
                continue
            widgets["run_btn"].setEnabled(enabled)

    def _on_process_output(self):
        if not self.process:
            return
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            if self.active_agent == "copilot":
                self.copilot_output_buffer += data
                code_match = re.search(r"\b([A-Z0-9]{4,5}-[A-Z0-9]{4,5})\b", self.copilot_output_buffer)
                if code_match:
                    self.copilot_auth_code = code_match.group(1)
                if "Error: No authentication information found." in self.copilot_output_buffer:
                    self.copilot_output_buffer = ""
                    self.pending_copilot_login = True
                    self.pending_copilot_retry = True
                    self._append_log("\n--- Autenticación requerida; iniciando login ---\n")
                    self.process.kill()
                if "Signed in successfully" in self.copilot_output_buffer:
                    self.copilot_output_buffer = ""
                    self.pending_copilot_retry = True
                    if self.auth_success_handler:
                        self.auth_success_handler(self.profile_id)
                self._open_copilot_auth_url(data)
                if self._looks_like_copilot_limit(self.copilot_output_buffer):
                    self.copilot_quota_detected = True
            self.log_view.moveCursor(self.log_view.textCursor().MoveOperation.End)
            self.log_view.insertPlainText(data)

    def _on_process_finished(self, exit_code: int, exit_status):
        retry_copilot = self.pending_copilot_retry
        start_login = self.pending_copilot_login
        quota_copilot = (
            self.active_agent == "copilot"
            and exit_code == 1
            and self.copilot_quota_detected
        )
        agent_label = AGENT_DEFS[self.active_agent]["short_label"] if self.active_agent else "?"
        self._append_log(f"\n--- {agent_label} terminó (código {exit_code}) ---\n")

        if self.active_agent and self.active_agent in self.agent_widgets:
            self.agent_widgets[self.active_agent]["run_btn"].setEnabled(True)
            self.agent_widgets[self.active_agent]["stop_btn"].setEnabled(False)
        self._set_other_agents_enabled(self.active_agent or "", True)

        self.process = None
        self.active_agent = None
        self.copilot_rotation_in_progress = False
        self.pending_copilot_retry = False
        self.pending_copilot_login = False
        self.copilot_quota_detected = False
        # Autorun (opcional): ejecutar comando local configurado por carpeta
        cfg = self.config_store.get(self.folder)
        autorun = cfg.get("autorun", {})
        autorun_enabled = bool(autorun.get("enabled")) and bool(autorun.get("command"))
        if autorun_enabled and not start_login and not retry_copilot:
            cmd = autorun.get("command")
            if cmd:
                valid, error = validate_autorun_command(cmd)
                if valid:
                    self._append_log(f"\n--- Ejecutando autorun: {cmd} ---\n")
                    self._run_autorun(cmd)
                else:
                    self._append_log(f"\n--- Autorun bloqueado: {error} ---\n")
                    QMessageBox.warning(self, "Autorun bloqueado", error)

        # If we were running in preview, after autorun/refresh we should show diff and cleanup
        if getattr(self, 'preview_mode', False) and getattr(self, 'preview_branch', None):
            try:
                cfg = self.config_store.get(self.folder)
                target = cfg.get('target_branch')
                preview = self.preview_branch
                ok, diff_out, diff_err = GitVersioning.run(self.folder, ["diff", f"{target}..{preview}"], timeout=60)
                if ok:
                    self._append_log("\n--- Preview diff (target..preview) ---\n")
                    self._append_log(diff_out[:20000])
                else:
                    self._append_log(f"\n--- No se pudo obtener diff de preview: {diff_err} ---\n")
                # cleanup: checkout target and delete preview branch
                # show list of commits introduced in preview
                okc, commits_out, commits_err = GitVersioning.run(self.folder, ["log", "--pretty=format:%h %s", f"{target}..{preview}"], timeout=30)
                if okc and commits_out.strip():
                    self._append_log("\n--- Commits in preview branch (new) ---\n")
                    self._append_log(commits_out)
                    # reveal merge button so user can apply preview as a merge commit
                    self.preview_merge_btn.setVisible(True)
                GitVersioning.run(self.folder, ["checkout", target])
                GitVersioning.run(self.folder, ["branch", "-D", preview])
            except Exception as e:
                self._append_log(f"\n--- Error limpiando preview: {e} ---\n")
            finally:
                self.preview_mode = False
                self.preview_branch = None

        if quota_copilot and self._rotate_copilot_profile():
            self._append_log(
                "\n⚠ Copilot agotó la cuota mensual y terminó con código 1. "
                "El trabajo puede haber quedado incompleto; revisá los cambios "
                "existentes al reintentar con el nuevo perfil.\n"
            )
        if start_login:
            QTimer.singleShot(250, self._start_copilot_login)
        elif retry_copilot:
            QTimer.singleShot(250, lambda: self._run_agent("copilot"))

    def _looks_like_copilot_limit(self, text):
        return bool(re.search(
            r"(?im)^\s*you have exceeded your monthly quota\s*\(request id:",
            text,
        ))

    def _rotate_copilot_profile(self):
        if self.copilot_rotation_in_progress or len(self.profile_ids) < 2:
            return
        current_index = self.profile_ids.index(self.profile_id)
        next_profile = next(
            (self.profile_ids[(current_index + offset) % len(self.profile_ids)]
             for offset in range(1, len(self.profile_ids) + 1)
             if self.profile_ids[(current_index + offset) % len(self.profile_ids)] != self.profile_id),
            None,
        )
        if not next_profile:
            return
        self.copilot_rotation_in_progress = True
        self.pending_copilot_retry = True
        self.profile_id = next_profile
        self.copilot_home = COPILOT_PROFILES_DIR / next_profile
        self.profile_combo.blockSignals(True)
        self.profile_combo.setCurrentIndex(
            self.profile_combo.findData(next_profile)
        )
        self.profile_combo.blockSignals(False)
        self._append_log(f"\n--- Cuota agotada; rotando a perfil {next_profile} ---\n")
        if self.process:
            self.process.kill()

    def _stop_current_agent(self):
        if self.process:
            self.process.kill()
            self._append_log("\n--- Detenido por el usuario ---\n")

    def _merge_preview_into_target(self):
        """Merge the preview branch into the configured target branch as a single merge commit.
        This is a conservative apply action after a preview run.
        """
        if not getattr(self, 'preview_branch', None):
            QMessageBox.information(self, "No hay preview", "No hay una rama de preview activa para aplicar.")
            return
        cfg = self.config_store.get(self.folder)
        target = cfg.get('target_branch')
        preview = self.preview_branch
        resp = QMessageBox.question(self, "Aplicar preview?", f"Aplicar los cambios de la rama '{preview}' a '{target}' mediante un merge (merge commit)?")
        if resp != QMessageBox.StandardButton.Yes:
            return
        # Ask for commit message
        from PyQt6.QtWidgets import QInputDialog
        msg, ok = QInputDialog.getText(self, "Mensaje de merge", "Mensaje para el commit de merge:", text=f"Merge preview {preview} into {target}")
        if not ok:
            return
        # Checkout target and merge
        okc, outc, errc = GitVersioning.run(self.folder, ["checkout", target])
        if not okc:
            QMessageBox.warning(self, "Error", f"No se pudo cambiar a la rama destino: {errc}")
            return
        okm, outm, errm = GitVersioning.run(self.folder, ["merge", "--no-ff", preview, "-m", msg], timeout=60)
        if okm:
            QMessageBox.information(self, "Merge aplicado", "Los cambios de preview se aplicaron a la rama destino.")
            # delete preview branch
            GitVersioning.run(self.folder, ["branch", "-D", preview])
            self.preview_branch = None
            self.preview_merge_btn.setVisible(False)
        else:
            QMessageBox.warning(self, "Merge falló", f"El merge falló:\n{errm}")

    def _add_template_to_instructions(self):
        """Append the COPILOT_PROMPT_TEMPLATE to .github/copilot-instructions.md and commit.
        If the repository is not initialized, ask the user whether to initialize it.
        """
        gh_dir = Path(self.folder) / ".github"
        gh_dir.mkdir(parents=True, exist_ok=True)
        target = gh_dir / "copilot-instructions.md"
        template_text = COPILOT_PROMPT_TEMPLATE
        # Simple uniqueness check
        existing = ""
        if target.exists():
            try:
                existing = target.read_text(encoding='utf-8')
            except Exception:
                existing = ""
            if "Tu tarea principal es asistir como agente AI principal" in existing:
                QMessageBox.information(self, "Plantilla existente", "El archivo ya contiene la plantilla de Copilot.")
                return
        # Prepare content to append
        content = (
            "\n\n---\n### Copilot prompt template (auto-added)\n\n```text\n"
            + template_text.replace('```', '` ` `')
            + "\n```\n"
        )
        try:
            if target.exists():
                with open(target, "a", encoding='utf-8') as f:
                    f.write(content)
            else:
                header = "# Copilot instructions\n\n"
                with open(target, "w", encoding='utf-8') as f:
                    f.write(header + content)
        except Exception as e:
            QMessageBox.warning(self, "Error al escribir archivo", f"No se pudo escribir {target}: {e}")
            return

        # Commit if repo available / desired
        if GitVersioning.has_repo(self.folder):
            if not GitVersioning.check_identity(self.folder):
                QMessageBox.warning(self, "Falta identidad de git", "Configura user.name y user.email antes de commitear.\n\nEjemplo:\n  git config --global user.name \"Tu Nombre\"\n  git config --global user.email \"tu@email.com\"")
                return
            rel = str(Path('.github') / 'copilot-instructions.md')
            ok, out, err = GitVersioning.run(self.folder, ["add", rel])
            if not ok:
                QMessageBox.warning(self, "Git add falló", f"git add falló:\n{err}")
                return
            commit_msg = "docs: add Copilot prompt template to copilot-instructions.md\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
            ok, out, err = GitVersioning.run(self.folder, ["commit", "-m", commit_msg])
            if ok:
                QMessageBox.information(self, "Commit creado", "Se agregó la plantilla y se creó un commit.")
            else:
                QMessageBox.warning(self, "Commit falló", f"git commit falló:\n{err}")
        else:
            # Not a repo — ask user whether to init and commit
            resp = QMessageBox.question(self, "No es repo git", "Esta carpeta no es un repositorio git. ¿Inicializar repo y commitear la plantilla?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if resp == QMessageBox.StandardButton.Yes:
                GitVersioning.ensure_repo(self.folder)
                if not GitVersioning.check_identity(self.folder):
                    QMessageBox.warning(self, "Falta identidad de git", "Configura user.name y user.email antes de commitear.")
                    return
                rel = str(Path('.github') / 'copilot-instructions.md')
                ok, out, err = GitVersioning.run(self.folder, ["add", rel])
                if not ok:
                    QMessageBox.warning(self, "Git add falló", f"git add falló:\n{err}")
                    return
                commit_msg = "docs: add Copilot prompt template to copilot-instructions.md\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
                ok, out, err = GitVersioning.run(self.folder, ["commit", "-m", commit_msg])
                if ok:
                    QMessageBox.information(self, "Repo inicializado y commit creado", "Se inicializó el repo y se creó el commit con la plantilla.")
                else:
                    QMessageBox.warning(self, "Commit falló", f"git commit falló:\n{err}")
            else:
                QMessageBox.information(self, "Archivo creado", f"Se creó {target} sin commit. Si querés commitearlo, inicializá un repo en esta carpeta y commiteá manualmente.")
        # hide preview merge button until next preview run
        self.preview_merge_btn.setVisible(False)

    def _run_autorun(self, command: str):
        """Ejecuta un comando local (autorun) en la carpeta y vuelca su salida al log."""
        valid, error = validate_autorun_command(command)
        if not valid:
            self._append_log(f"\n--- Autorun bloqueado: {error} ---\n")
            return
        if getattr(self, 'autourun_process', None) is not None:
            return
        self.autorun_process = QProcess(self)
        self.autorun_process.setWorkingDirectory(self.folder)
        self.autorun_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.autorun_process.readyReadStandardOutput.connect(self._on_autorun_output)
        self.autorun_process.finished.connect(self._on_autorun_finished)
        # Ejecutar a través de cmd.exe en Windows si está disponible
        if shutil.which("cmd.exe"):
            self.autorun_process.start("cmd.exe", ["/c", command])
        else:
            # dividir simple en tokens; si hay casos complejos, el usuario puede
            # especificar un script file path en la configuración
            parts = shlex.split(command)
            if parts:
                prog, args = parts[0], parts[1:]
                self.autorun_process.start(prog, args)

    def _on_autorun_output(self):
        if not getattr(self, 'autourun_process', None):
            return
        data = bytes(self.autorun_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self.log_view.moveCursor(self.log_view.textCursor().MoveOperation.End)
            self.log_view.insertPlainText(data)

    def _on_autorun_finished(self, exit_code: int, exit_status):
        self._append_log(f"\n--- Autorun terminó (código {exit_code}) ---\n")
        if exit_code != 0:
            QMessageBox.warning(self, "Autorun falló", f"El comando autorun devolvió código {exit_code}.\nRevisá la salida en la sección 'Salida'.")
        try:
            self.autorun_process = None
        except Exception:
            self.autorun_process = None

    def closeEvent(self, event):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            confirm = QMessageBox.question(
                self, "Un agente sigue corriendo",
                "Todavía hay un agente ejecutándose. ¿Detenerlo y cerrar?",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.process.kill()
        event.accept()


class RepositoryConfigDialog(QDialog):
    """Configura Git y autorun para un repositorio concreto."""

    def __init__(self, parent, folder: str, repository_name: str = ""):
        super().__init__(parent)
        self.folder = str(Path(folder))
        self.config_store = AgentConfigStore()
        self.setWindowTitle(f"Configuración del repositorio — {repository_name or Path(folder).name}")
        self.resize(620, 430)

        layout = QVBoxLayout(self)
        repo_box = QGroupBox("Repositorio")
        repo_layout = QVBoxLayout(repo_box)
        self.repo_status_label = QLabel()
        self.repo_status_label.setWordWrap(True)
        repo_layout.addWidget(self.repo_status_label)
        repo_buttons = QHBoxLayout()
        init_btn = QPushButton("Inicializar repositorio git aquí")
        init_btn.clicked.connect(self._init_repo)
        self.connect_remote_btn = QPushButton("Conectar repositorio remoto...")
        self.connect_remote_btn.clicked.connect(self._connect_remote)
        repo_buttons.addWidget(init_btn)
        repo_buttons.addWidget(self.connect_remote_btn)
        repo_layout.addLayout(repo_buttons)
        layout.addWidget(repo_box)

        autorun_box = QGroupBox("Autorun al terminar un agente")
        form = QFormLayout(autorun_box)
        autorun = self.config_store.get(self.folder).get("autorun", {})
        self.autorun_enabled = QCheckBox("Ejecutar automáticamente")
        self.autorun_enabled.setChecked(bool(autorun.get("enabled")))
        self.autorun_command = QLineEdit(autorun.get("command", ""))
        self.autorun_command.setPlaceholderText(
            "Vacío: detectar checks por lenguaje; o Ej: npm test"
        )
        hint = QLabel(
            "Los checks corren localmente y en secuencia. Solo los errores quedan "
            "para revisión; Git puede cerrar los cambios automáticamente."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow(self.autorun_enabled)
        form.addRow("Comando:", self.autorun_command)
        form.addRow("", hint)
        layout.addWidget(autorun_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_repo_status()

    def _refresh_repo_status(self):
        if not GitVersioning.has_repo(self.folder):
            self.repo_status_label.setText(
                "⚠ Esta carpeta todavía no es un repositorio git."
            )
            self.connect_remote_btn.setEnabled(False)
            return
        branch = GitVersioning.get_current_branch(self.folder) or "?"
        if GitVersioning.has_remote(self.folder):
            remote = GitVersioning.get_remote_url(self.folder)
            self.repo_status_label.setText(
                f"✅ Repo git en rama '{branch}'.\n🔗 Remoto: {remote}"
            )
            self.connect_remote_btn.setText("Cambiar repositorio remoto...")
        else:
            self.repo_status_label.setText(
                f"✅ Repo git en rama '{branch}'.\n⚠ Sin remoto configurado."
            )
            self.connect_remote_btn.setText("Conectar repositorio remoto...")
        self.connect_remote_btn.setEnabled(True)

    def _init_repo(self):
        GitVersioning.ensure_repo(self.folder)
        if not GitVersioning.check_identity(self.folder):
            QMessageBox.warning(
                self,
                "Falta identidad de git",
                "Git no tiene user.name/user.email configurados. Configuralos con:\n\n"
                'git config --global user.name "Tu Nombre"\n'
                'git config --global user.email "tu@email.com"',
            )
        self._refresh_repo_status()

    def _connect_remote(self):
        current_url = (
            GitVersioning.get_remote_url(self.folder)
            if GitVersioning.has_remote(self.folder)
            else ""
        )
        dialog = _ConnectRemoteDialog(self, default_url=current_url)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        url = dialog.get_url()
        if not url:
            return
        if not GitVersioning.is_available():
            QMessageBox.warning(self, "Git no encontrado", "No se encontró 'git' en el sistema.")
            return
        if not GitVersioning.has_repo(self.folder):
            self._init_repo()
        config = self.config_store.get(self.folder)
        target_branch = config["target_branch"]
        current_branch = GitVersioning.get_current_branch(self.folder)
        if not GitVersioning.is_clean(self.folder) or not current_branch:
            GitVersioning.run(self.folder, ["commit", "-m", "Commit inicial"])
            current_branch = GitVersioning.get_current_branch(self.folder)
        if current_branch and current_branch != target_branch:
            GitVersioning.run(
                self.folder, ["branch", "-m", current_branch, target_branch]
            )
        if GitVersioning.has_remote(self.folder):
            GitVersioning.run(self.folder, ["remote", "remove", "origin"])
        GitVersioning.run(self.folder, ["remote", "add", "origin", url])
        ok, _, err = GitVersioning.run(
            self.folder, ["push", "-u", "origin", target_branch], timeout=60
        )
        if ok:
            QMessageBox.information(
                self, "Repositorio conectado",
                f"Remoto configurado y publicado en:\n{url}",
            )
        else:
            QMessageBox.warning(
                self, "Remoto conectado, pero el push falló",
                f"El remoto quedó configurado en:\n{url}\n\npero el push automático falló:\n\n{err}",
            )
        self._refresh_repo_status()

    def _save(self):
        try:
            self.config_store.set_autorun(
                self.folder,
                self.autorun_enabled.isChecked(),
                self.autorun_command.text().strip(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Autorun inválido", str(exc))
            return
        self.accept()


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
            "WebAgent no maneja tokens ni contraseñas: usa la autenticación que ya "
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
