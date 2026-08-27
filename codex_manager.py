"""
codex_manager.py - "Agentes IA" para IA Browser.

Le permite, para cualquier carpeta versionada con git (perfil o
Colección), desde un diálogo con pestañas:
  - Config: conectar un repositorio remoto que el usuario ya creó a
    mano en GitHub (u otro host, pegando su URL HTTPS o SSH — sin
    manejar tokens, usa las credenciales de git ya configuradas en el
    sistema) y configurar una rama "cruda" (donde caen los commits
    automáticos de IA Browser al descargar archivos) y una rama
    "limpia" de destino.
  - Codex: revisa/reescribe el historial de la rama cruda y arma
    commits prolijos y descriptivos en la rama limpia.
  - Copilot: solo lee código para entender el proyecto, pero
    únicamente crea o modifica documentación (README, AGENTS.md, .txt,
    etc) — nunca código fuente.
Cada agente tiene su propio comando (editable) y su propio prompt
(generado desde una plantilla, también editable antes de correr).
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


# ======================================================================
# Definición de los 3 agentes: comando por defecto, prompt, requisitos
# ======================================================================

CODEX_PROMPT_TEMPLATE = """Estás parado en un repositorio git, en la rama '{target_branch}'.

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
3. Creá commits prolijos en la rama '{target_branch}' con mensajes \
DESCRIPTIVOS: una primera línea corta en modo imperativo como resumen, y un \
cuerpo debajo explicando qué cambió y por qué, cuando sea relevante.
4. NO repitas el mismo mensaje de commit para cambios distintos.
5. Al terminar, la rama '{target_branch}' tiene que estar actualizada y con el \
working tree limpio.

No toques la rama '{source_branch}' — dejala tal cual, es el registro crudo de \
descargas. No modifiques código más allá de lo que ya está en los commits que \
estás reorganizando (tu trabajo es de historial/commits, no de reescribir \
funcionalidad)."""

COPILOT_PROMPT_TEMPLATE = """Estás en un repositorio git, en la rama '{target_branch}'.

Tu tarea principal es asistir como agente AI principal del proyecto. Este bloque
es la plantilla oficial de instrucciones para Copilot y se usa SOLO en
`.github/copilot-instructions.md` cuando el usuario la agrega desde la UI.
"""

COPILOT_DEFAULT_PROMPT = """Actuá como el agente Copilot del repositorio. Lee el código y responde según la tarea indicada. Si se solicitan cambios, proponé commits atómicos y comandos de verificación. Mantén mensajes breves."""


AGENT_DEFS = {
    "codex": {
        "label": "🧹 Codex — Limpieza de commits",
        "short_label": "Codex",
        "description": "Analiza la rama cruda y reescribe el historial en la rama limpia con commits prolijos y descriptivos.",
        "default_command": 'codex exec --sandbox workspace-write "{prompt}"',
        "needs_task": False,
        "needs_source_branch": True,
        "prompt_template": CODEX_PROMPT_TEMPLATE,
        "check_binary": "codex",
        "install_hint": "npm install -g @openai/codex   y luego  codex login",
    },
    "copilot": {
        "label": "🤖 Copilot — Agente principal",
        "short_label": "Copilot",
        "description": "Agente AI principal: puede leer y modificar código y documentación, generar commits atómicos y ejecutar verificaciones de proyecto.",
        "default_command": 'copilot -i "{prompt}" --allow-all',
        "needs_task": False,
        "needs_source_branch": True,
        "prompt_template": COPILOT_DEFAULT_PROMPT,
        "check_binary": "copilot",
        "install_hint": "requiere GitHub Copilot CLI (docs.github.com/copilot) y un plan de Copilot activo",
    },

}

AGENT_ORDER = ["copilot", "codex"]

# Comandos por defecto de versiones anteriores que ya no aplican (se
# migran solos al default actual si el usuario nunca los tocó a mano).
LEGACY_DEFAULT_COMMANDS = {
    "copilot": ['copilot -p "{prompt}" --allow-all --no-ask-user'],
}


# ======================================================================
# Configuración persistente por carpeta
# ======================================================================

class AgentConfigStore:
    """Guarda, por carpeta, las ramas cruda/limpia y el comando (y
    última tarea, para Gemini) de cada uno de los 3 agentes.
    Clave = ruta absoluta de la carpeta."""

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

    def _default_entry(self) -> dict:
        return {
            "source_branch": "master",
            "target_branch": "main",
            "agents": {
                aid: {"command": defn["default_command"], "last_task": ""}
                for aid, defn in AGENT_DEFS.items()
            },
            # Autorun: si enabled=True, ejecutar 'autorun.command' tras un run de agente
            "autorun": {"enabled": False, "command": ""},
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
                aid: {"command": defn["default_command"], "last_task": ""}
                for aid, defn in AGENT_DEFS.items()
            }
            if legacy_cmd:
                entry["agents"]["codex"]["command"] = legacy_cmd
        else:
            for aid, defn in AGENT_DEFS.items():
                entry["agents"].setdefault(aid, {"command": defn["default_command"], "last_task": ""})

        # Ensure autorun key exists for backward compatibility
        entry.setdefault("autorun", {"enabled": False, "command": ""})

        for aid, defn in AGENT_DEFS.items():
            current = entry["agents"][aid].get("command", "")
            if current in LEGACY_DEFAULT_COMMANDS.get(aid, []):
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


# ======================================================================
# Diálogo principal
# ======================================================================

class AIAgentsDialog(QDialog):
    """Diálogo de gestión: conectar repo remoto, configurar ramas
    cruda/limpia, y disparar cada uno de los 3 agentes (Codex, Copilot,
    Gemini) por separado sobre la misma carpeta."""

    def __init__(self, parent, folder: str, profile_id: str | None = None,
                 profile_ids: list[str] | None = None, profile_names: dict[str, str] | None = None,
                 auth_url_handler=None, auth_success_handler=None):
        super().__init__(parent)
        # Normalizamos a separadores nativos del SO (Qt suele devolver
        # rutas con "/" incluso en Windows; con "\" nativo evitamos
        # problemas raros al pasarle la carpeta a cmd.exe / QProcess).
        self.folder = str(Path(folder))
        self.profile_id = profile_id or "default"
        self.profile_ids = profile_ids or [self.profile_id]
        self.profile_names = profile_names or {}
        self.auth_url_handler = auth_url_handler
        self.auth_success_handler = auth_success_handler
        self.copilot_home = Path.home() / ".ia_browser" / "copilot_profiles" / self.profile_id
        self.raw_branch, self.profile_branch = GitVersioning.profile_branch_names(self.profile_id)
        self.config_store = AgentConfigStore()
        config = self.config_store.get(self.folder)
        if config["source_branch"] == "master" and config["target_branch"] == "main":
            self.config_store.set_branches(
                self.folder, self.raw_branch, self.profile_branch
            )
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

        self.setWindowTitle(f"Agentes IA — {Path(folder).name}")
        self.resize(680, 640)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"📁 {folder}"))
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Cuenta Copilot / perfil:"))
        self.copilot_profile_combo = QComboBox()
        for item in self.profile_ids:
            self.copilot_profile_combo.addItem(self.profile_names.get(item, item), item)
        current_index = self.copilot_profile_combo.findData(self.profile_id)
        if current_index >= 0:
            self.copilot_profile_combo.setCurrentIndex(current_index)
        self.copilot_profile_combo.currentIndexChanged.connect(self._select_copilot_profile)
        profile_row.addWidget(self.copilot_profile_combo, 1)
        layout.addLayout(profile_row)

        self.agents_tabs = QTabWidget()

        config_tab = QWidget()
        config_layout = QVBoxLayout(config_tab)
        self._build_repo_section(config_layout)
        self._build_branches_section(config_layout)
        config_layout.addStretch()
        self.agents_tabs.addTab(config_tab, "⚙ Config")

        for agent_id in AGENT_ORDER:
            tab = QWidget()
            self._build_agent_tab(tab, agent_id)
            self.agents_tabs.addTab(tab, AGENT_DEFS[agent_id]["short_label"])
        layout.addWidget(self.agents_tabs, 1)

        self._build_log_section(layout)

        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)

        self._refresh_repo_status()

    def _copilot_environment(self):
        self.copilot_home.mkdir(parents=True, exist_ok=True)
        environment = QProcessEnvironment.systemEnvironment()
        for name in ("COPILOT_HOME", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
            environment.remove(name)
        environment.insert("COPILOT_HOME", str(self.copilot_home))
        # Device flow link is opened by IA Browser, never by system browser.
        environment.insert("BROWSER", "cmd.exe /c exit 0")
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
        selected = self.copilot_profile_combo.itemData(index)
        if not selected or selected == self.profile_id:
            return
        if self.process is not None:
            self.copilot_profile_combo.blockSignals(True)
            self.copilot_profile_combo.setCurrentIndex(
                self.copilot_profile_combo.findData(self.profile_id)
            )
            self.copilot_profile_combo.blockSignals(False)
            QMessageBox.information(self, "Copilot", "Detené el proceso antes de cambiar de perfil.")
            return
        self.profile_id = selected
        self.copilot_home = Path.home() / ".ia_browser" / "copilot_profiles" / selected

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

    # ---------- Sección: ramas ----------

    def _build_branches_section(self, layout):
        box = QGroupBox("Ramas")
        form = QFormLayout(box)

        cfg = self.config_store.get(self.folder)
        self.source_branch_edit = QLineEdit(cfg["source_branch"])
        self.source_branch_edit.setPlaceholderText("Rama cruda (commits automáticos de descargas)")
        form.addRow("Rama cruda:", self.source_branch_edit)

        self.target_branch_edit = QLineEdit(cfg["target_branch"])
        self.target_branch_edit.setPlaceholderText("Rama limpia (donde trabajan los 3 agentes)")
        form.addRow("Rama limpia (destino):", self.target_branch_edit)

        save_branches_btn = QPushButton("Guardar ramas y regenerar prompts")
        save_branches_btn.clicked.connect(self._save_branches)
        form.addRow("", save_branches_btn)

        layout.addWidget(box)

    def _save_branches(self):
        source = self.source_branch_edit.text().strip() or self.raw_branch
        target = self.target_branch_edit.text().strip() or self.profile_branch
        self.config_store.set_branches(self.folder, source, target)
        for agent_id in AGENT_ORDER:
            self._regenerate_prompt(agent_id)
        self._append_log("ℹ Ramas guardadas y prompts regenerados")

    # ---------- Sección: un agente ----------

    def _build_agent_tab(self, tab: QWidget, agent_id: str):
        defn = AGENT_DEFS[agent_id]
        tab_layout = QVBoxLayout(tab)

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
        form.addRow("Comando:", command_edit)
        tab_layout.addLayout(form)

        task_edit = None
        if defn["needs_task"]:
            tab_layout.addWidget(QLabel("Descripción de la tarea a implementar:"))
            task_edit = QTextEdit()
            task_edit.setPlainText(agent_cfg.get("last_task", ""))
            task_edit.setPlaceholderText("Ej: Agregar un botón para exportar la lista a CSV...")
            task_edit.setMinimumHeight(60)
            tab_layout.addWidget(task_edit)

        tab_layout.addWidget(QLabel("Prompt final (editable antes de correr):"))
        prompt_edit = QTextEdit()
        prompt_edit.setMinimumHeight(120)
        tab_layout.addWidget(prompt_edit, 1)

        btn_row = QHBoxLayout()
        regen_btn = QPushButton("🔄 Regenerar desde plantilla")
        run_btn = QPushButton(f"▶ Ejecutar {defn['short_label']}")
        preview_btn = QPushButton(f"👁️ Previsualizar {defn['short_label']}")
        stop_btn = QPushButton("⏹ Detener")
        stop_btn.setEnabled(False)
        btn_row.addWidget(regen_btn)
        btn_row.addWidget(run_btn)
        btn_row.addWidget(preview_btn)
        btn_row.addWidget(stop_btn)

        if agent_id == "copilot":
            # checkbox to control using template when regenerating
            use_template_cb = QCheckBox("Usar plantilla al regenerar")
            use_template_cb.setChecked(False)
            btn_row.addWidget(use_template_cb)
            add_template_btn = QPushButton("➕ Agregar plantilla a copilot-instructions.md")
            add_template_btn.clicked.connect(lambda: self._add_template_to_instructions())
            btn_row.addWidget(add_template_btn)
            login_btn = QPushButton("🔐 Iniciar sesión en este perfil")
            login_btn.clicked.connect(self._start_copilot_login)
            btn_row.addWidget(login_btn)
        tab_layout.addLayout(btn_row)

        self.agent_widgets[agent_id] = {
            "command_edit": command_edit, "task_edit": task_edit, "prompt_edit": prompt_edit,
            "run_btn": run_btn, "stop_btn": stop_btn,
        }

        # store copilot-specific widgets
        if agent_id == "copilot":
            self.agent_widgets[agent_id]["use_template_cb"] = use_template_cb
            self.agent_widgets[agent_id]["add_template_btn"] = add_template_btn

        regen_btn.clicked.connect(lambda: self._regenerate_prompt(agent_id))
        run_btn.clicked.connect(lambda: self._run_agent(agent_id))
        stop_btn.clicked.connect(self._stop_current_agent)

        # connect preview button
        preview_btn.clicked.connect(lambda: self._run_agent(agent_id, preview=True))

        self._regenerate_prompt(agent_id)

    def _regenerate_prompt(self, agent_id: str):
        defn = AGENT_DEFS[agent_id]
        cfg = self.config_store.get(self.folder)
        widgets = self.agent_widgets[agent_id]

        # For Copilot: only inject the template if the "use_template" checkbox is checked
        if agent_id == "copilot":
            use_template = widgets.get("use_template_cb")
            if use_template and not use_template.isChecked():
                # If the prompt box is empty, populate it once; otherwise leave user edits intact
                if not widgets["prompt_edit"].toPlainText().strip():
                    prompt = defn["prompt_template"].format(
                        source_branch=cfg["source_branch"], target_branch=cfg["target_branch"]
                    )
                    widgets["prompt_edit"].setPlainText(prompt)
                return

        if defn["needs_task"]:
            task = widgets["task_edit"].toPlainText().strip() or "(completá la descripción de la tarea arriba)"
            prompt = defn["prompt_template"].format(target_branch=cfg["target_branch"], task=task)
        else:
            prompt = defn["prompt_template"].format(
                source_branch=cfg["source_branch"], target_branch=cfg["target_branch"]
            )
        widgets["prompt_edit"].setPlainText(prompt)

    # ---------- Sección: log de ejecución (compartido entre agentes) ----------

    def _build_log_section(self, layout):
        layout.addWidget(QLabel("Salida:"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas, Monaco, monospace", 10))
        self.log_view.setMinimumHeight(140)
        layout.addWidget(self.log_view)

        stdin_row = QHBoxLayout()
        self.stdin_edit = QLineEdit()
        self.stdin_edit.setPlaceholderText(
            "Si el agente te pregunta algo, respondé acá y Enter (deshabilitado si no hay nada corriendo)"
        )
        self.stdin_edit.setEnabled(False)
        self.stdin_edit.returnPressed.connect(self._send_to_process)
        self.send_stdin_btn = QPushButton("Enviar")
        self.send_stdin_btn.setEnabled(False)
        self.send_stdin_btn.clicked.connect(self._send_to_process)
        stdin_row.addWidget(self.stdin_edit)
        stdin_row.addWidget(self.send_stdin_btn)
        layout.addLayout(stdin_row)

    def _append_log(self, text: str):
        self.log_view.append(text)

    def _send_to_process(self):
        if not self.process or self.process.state() == QProcess.ProcessState.NotRunning:
            return
        text = self.stdin_edit.text()
        if not text:
            return
        self.process.write((text + "\n").encode("utf-8"))
        self._append_log(f"> {text}")
        self.stdin_edit.clear()

    # ---------- Ejecutar un agente ----------

    def _run_agent(self, agent_id: str, preview: bool = False):
        defn = AGENT_DEFS[agent_id]

        if shutil.which(defn["check_binary"]) is None:
            QMessageBox.warning(
                self, f"{defn['check_binary']} no encontrado",
                f"No se encontró '{defn['check_binary']}' en el PATH.\n\nInstalación: {defn['install_hint']}",
            )
            return

        if self.process is not None:
            QMessageBox.information(
                self, "Aviso",
                f"Ya hay un agente corriendo ({AGENT_DEFS[self.active_agent]['short_label']}). "
                "Esperá a que termine o detenelo antes de lanzar otro.",
            )
            return

        if not GitVersioning.has_repo(self.folder):
            QMessageBox.warning(self, "Aviso", "Esta carpeta todavía no es un repositorio git.")
            return

        cfg = self.config_store.get(self.folder)
        source = self.source_branch_edit.text().strip() or self.raw_branch
        target = self.target_branch_edit.text().strip() or self.profile_branch
        self.config_store.set_branches(self.folder, source, target)

        widgets = self.agent_widgets[agent_id]
        extra = {"command": widgets["command_edit"].text().strip() or defn["default_command"]}
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
        self.stdin_edit.setEnabled(True)
        self.send_stdin_btn.setEnabled(True)

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
        if agent_id == "copilot":
            self.process.setProcessEnvironment(self._copilot_environment())

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
        self.stdin_edit.setEnabled(True)
        self.send_stdin_btn.setEnabled(True)
        self._append_log(f"\n=== Autenticando Copilot ({self.profile_id}) ===\n")
        self.process = QProcess(self)
        self.process.setWorkingDirectory(self.folder)
        self.process.setProcessEnvironment(self._copilot_environment())
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
        for tok in tokens:
            if tok == "{prompt}":
                argv.append(prompt)
            elif "{prompt}" in tok:
                argv.append(tok.replace("{prompt}", prompt))
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
                if self._looks_like_copilot_limit(data):
                    self._rotate_copilot_profile()
            self.log_view.moveCursor(self.log_view.textCursor().MoveOperation.End)
            self.log_view.insertPlainText(data)

    def _on_process_finished(self, exit_code: int, exit_status):
        retry_copilot = self.pending_copilot_retry
        start_login = self.pending_copilot_login
        agent_label = AGENT_DEFS[self.active_agent]["short_label"] if self.active_agent else "?"
        self._append_log(f"\n--- {agent_label} terminó (código {exit_code}) ---\n")

        if self.active_agent and self.active_agent in self.agent_widgets:
            self.agent_widgets[self.active_agent]["run_btn"].setEnabled(True)
            self.agent_widgets[self.active_agent]["stop_btn"].setEnabled(False)
        self._set_other_agents_enabled(self.active_agent or "", True)
        self.stdin_edit.setEnabled(False)
        self.send_stdin_btn.setEnabled(False)

        self.process = None
        self.active_agent = None
        self.copilot_rotation_in_progress = False
        self.pending_copilot_retry = False
        self.pending_copilot_login = False
        self._refresh_repo_status()

        # Autorun (opcional): ejecutar comando local configurado por carpeta
        cfg = self.config_store.get(self.folder)
        autorun = cfg.get("autorun", {})
        autorun_enabled = bool(autorun.get("enabled")) and bool(autorun.get("command"))
        if autorun_enabled and not start_login and not retry_copilot:
            cmd = autorun.get("command")
            if cmd:
                self._append_log(f"\n--- Ejecutando autorun: {cmd} ---\n")
                self._run_autorun(cmd)

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
                GitVersioning.run(self.folder, ["checkout", target])
                GitVersioning.run(self.folder, ["branch", "-D", preview])
            except Exception as e:
                self._append_log(f"\n--- Error limpiando preview: {e} ---\n")
            finally:
                self.preview_mode = False
                self.preview_branch = None

        if start_login:
            QTimer.singleShot(250, self._start_copilot_login)
        elif retry_copilot:
            QTimer.singleShot(250, lambda: self._run_agent("copilot"))

    def _looks_like_copilot_limit(self, text):
        lowered = text.lower()
        return any(pattern in lowered for pattern in (
            "rate limit", "limit reached", "usage limit", "quota exhausted",
            "exhausted", "too many requests", "no premium requests",
            "maximum number of requests",
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
        self.copilot_home = Path.home() / ".ia_browser" / "copilot_profiles" / next_profile
        self.copilot_profile_combo.blockSignals(True)
        self.copilot_profile_combo.setCurrentIndex(
            self.copilot_profile_combo.findData(next_profile)
        )
        self.copilot_profile_combo.blockSignals(False)
        self._append_log(f"\n--- Cuota agotada; rotando a perfil {next_profile} ---\n")
        if self.process:
            self.process.kill()

    def _stop_current_agent(self):
        if self.process:
            self.process.kill()
            self._append_log("\n--- Detenido por el usuario ---\n")

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

    def _run_autorun(self, command: str):
        """Ejecuta un comando local (autorun) en la carpeta y vuelca su salida al log."""
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
