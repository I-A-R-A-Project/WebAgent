"""Diálogo para planificar y ejecutar ciclos verificados de agentes IA."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
)

from agents.ai_manager import AGENT_DEFS, AGENT_ORDER
from core.agent_framework import (
    FrameworkOrchestrator,
    FrameworkRun,
    FrameworkStatus,
    FrameworkStore,
)
from core.automation import build_framework_check_plan
from core.file_ops import GitVersioning
from core.paths import IA_DATA_DIR


class FrameworkWorker(QThread):
    """Coordina un run fuera del hilo UI y delega los agentes al runner Qt."""

    agent_requested = pyqtSignal(str, str, str, str, str)
    check_output = pyqtSignal(str)

    def __init__(self, orchestrator: FrameworkOrchestrator, parent=None):
        super().__init__(parent)
        self.orchestrator = orchestrator
        self.run_id = orchestrator.run.run_id
        self.agent_id = orchestrator.run.agent_id
        self.folder = orchestrator.run.folder
        self.profile_id = orchestrator.run.approved_profile
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self._agent_result_ready = threading.Event()
        self._agent_result_lock = threading.Lock()
        self._agent_result: tuple[str, int] = ("", 1)

    def deliver_agent_result(self, run_id: str, output: str, exit_code: int) -> None:
        if run_id != self.run_id:
            return
        with self._agent_result_lock:
            self._agent_result = (output, exit_code)
        self._agent_result_ready.set()

    def run(self) -> None:
        try:
            self.orchestrator.run_until_stopped(
                self._run_agent,
                on_check_output=lambda command, text: self.check_output.emit(
                    f"[{command}] {text.rstrip()}"
                ),
                cancel_event=self.cancel_event,
                pause_event=self.pause_event,
            )
        except Exception as exc:
            self.orchestrator.run.status = FrameworkStatus.FAILED.value
            self.orchestrator.run.last_error = str(exc)
            self.orchestrator.run.save(self.orchestrator.store)

    def _run_agent(self, prompt: str) -> tuple[str, int]:
        if self.cancel_event.is_set():
            return "", 1
        self._agent_result_ready.clear()
        with self._agent_result_lock:
            self._agent_result = ("", 1)
        self.agent_requested.emit(
            self.run_id, self.agent_id, self.folder, self.profile_id, prompt
        )
        while not self._agent_result_ready.wait(0.1):
            if self.cancel_event.is_set():
                return "", 1
        with self._agent_result_lock:
            return self._agent_result


class AgentFrameworkDialog(QDialog):
    """Permite revisar un plan y administrar la ejecución persistente."""

    def __init__(self, parent, agent_console, folder_getter):
        super().__init__(parent)
        self.agent_console = agent_console
        self.folder_getter = folder_getter
        self.store = FrameworkStore(IA_DATA_DIR / "agent_framework")
        self.orchestrator: FrameworkOrchestrator | None = None
        self.worker: FrameworkWorker | None = None
        self._planning = False
        self._planning_run_id = ""
        self.setWindowTitle("Framework de tarea")
        self.resize(900, 720)
        self._build_ui()
        self._populate_folders()
        self._populate_agents()
        self._populate_existing_runs()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(300)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.folder_combo = QComboBox()
        self.agent_combo = QComboBox()
        self.cycle_limit = QSpinBox()
        self.cycle_limit.setRange(1, 100)
        self.cycle_limit.setValue(10)
        self.check_timeout = QSpinBox()
        self.check_timeout.setRange(1, 3600)
        self.check_timeout.setValue(600)
        self.check_timeout.setSuffix(" s")
        form.addRow("Repositorio:", self.folder_combo)
        form.addRow("Agente:", self.agent_combo)
        form.addRow("Límite de ciclos:", self.cycle_limit)
        form.addRow("Timeout por check:", self.check_timeout)
        layout.addLayout(form)

        self.request_edit = QPlainTextEdit()
        self.request_edit.setPlaceholderText("Describí el resultado que querés obtener.")
        self.request_edit.setMaximumHeight(100)
        layout.addWidget(QLabel("Solicitud"))
        layout.addWidget(self.request_edit)

        self.existing_combo = QComboBox()
        self.load_button = QPushButton("Cargar ejecución")
        self.load_button.clicked.connect(self._load_selected_run)
        existing_row = QHBoxLayout()
        existing_row.addWidget(self.existing_combo, 1)
        existing_row.addWidget(self.load_button)
        layout.addLayout(existing_row)

        self.tabs = QTabWidget()
        self.plan_edit = QPlainTextEdit()
        self.plan_edit.setPlaceholderText("El plan generado aparecerá aquí; podés editarlo antes de aprobar.")
        self.tabs.addTab(self.plan_edit, "Plan Markdown")
        self.activity_view = QPlainTextEdit()
        self.activity_view.setReadOnly(True)
        self.activity_view.document().setMaximumBlockCount(5000)
        self.tabs.addTab(self.activity_view, "Actividad")
        layout.addWidget(self.tabs, 1)

        copy_row = QHBoxLayout()
        self.save_plan_checkbox = QCheckBox(
            "Guardar una copia aprobada en el repositorio"
        )
        self.plan_path_edit = QLineEdit("framework_plan.md")
        self.plan_path_edit.setEnabled(False)
        self.save_plan_checkbox.toggled.connect(self.plan_path_edit.setEnabled)
        copy_row.addWidget(self.save_plan_checkbox)
        copy_row.addWidget(self.plan_path_edit, 1)
        layout.addLayout(copy_row)

        buttons = QHBoxLayout()
        self.generate_button = QPushButton("Generar plan")
        self.revision_button = QPushButton("Pedir cambios")
        self.approve_button = QPushButton("Aprobar y ejecutar")
        self.pause_button = QPushButton("Pausar al final del ciclo")
        self.resume_button = QPushButton("Reanudar")
        self.cancel_button = QPushButton("Cancelar")
        self.new_button = QPushButton("Nueva tarea")
        for button in (
            self.new_button,
            self.generate_button,
            self.revision_button,
            self.approve_button,
            self.pause_button,
            self.resume_button,
            self.cancel_button,
        ):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.state_label = QLabel("Sin ejecución cargada")
        layout.addWidget(self.state_label)

        self.generate_button.clicked.connect(self._generate_plan)
        self.new_button.clicked.connect(self._new_run)
        self.revision_button.clicked.connect(self._request_revision)
        self.approve_button.clicked.connect(self._approve_and_start)
        self.pause_button.clicked.connect(self._pause)
        self.resume_button.clicked.connect(self._resume)
        self.cancel_button.clicked.connect(self._cancel)
        self._refresh_status()

    def _populate_folders(self) -> None:
        for name, folder in self.folder_getter() or []:
            normalized = str(Path(folder).resolve())
            if self.folder_combo.findData(normalized) < 0:
                self.folder_combo.addItem(name, normalized)

    def _populate_agents(self) -> None:
        for agent_id in AGENT_ORDER:
            if agent_id in AGENT_DEFS:
                self.agent_combo.addItem(
                    AGENT_DEFS[agent_id].get("short_label", agent_id), agent_id
                )

    def _populate_existing_runs(self) -> None:
        self.existing_combo.clear()
        try:
            runs = self.store.incomplete()
            known_ids = {run.run_id for run in runs}
            runs.extend(
                run for run in self.store.all_runs()
                if run.run_id not in known_ids
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._show_error(f"No se pudieron cargar ejecuciones guardadas: {exc}")
            return
        for run in runs:
            label = f"{run.updated_at[:19]} · {run.agent_id} · {run.status} · {run.request[:50]}"
            self.existing_combo.addItem(label, run.run_id)

    def _create_run(self) -> FrameworkOrchestrator:
        folder = str(self.folder_combo.currentData() or "")
        agent_id = str(self.agent_combo.currentData() or "")
        request = self.request_edit.toPlainText().strip()
        if not folder or not Path(folder).is_dir():
            raise ValueError("Seleccioná una carpeta de repositorio válida.")
        if not request:
            raise ValueError("Escribí la solicitud antes de generar el plan.")
        run = FrameworkRun(
            run_id=uuid.uuid4().hex,
            folder=folder,
            agent_id=agent_id,
            request=request,
            max_cycles=self.cycle_limit.value(),
            check_timeout_seconds=self.check_timeout.value(),
        )
        self.orchestrator = self._make_orchestrator(run)
        run.save(self.store)
        self._add_run_to_selector(run)
        return self.orchestrator

    def _make_orchestrator(self, run: FrameworkRun) -> FrameworkOrchestrator:
        config = self.agent_console.config_store.get(run.folder)
        configured_command = (
            config.get("autorun", {}).get("command", "").strip()
        )
        commands = build_framework_check_plan(run.folder, configured_command)
        return FrameworkOrchestrator(
            run,
            self.store,
            check_commands=commands,
        )

    def _add_run_to_selector(self, run: FrameworkRun) -> None:
        if self.existing_combo.findData(run.run_id) < 0:
            label = f"{run.updated_at[:19]} · {run.agent_id} · {run.status} · {run.request[:50]}"
            self.existing_combo.addItem(label, run.run_id)
        self.existing_combo.setCurrentIndex(self.existing_combo.findData(run.run_id))

    def _generate_plan(self, observation: str = "") -> None:
        if self._planning or self._is_worker_running():
            return
        try:
            if self.orchestrator is None:
                self._create_run()
            assert self.orchestrator is not None
            run = self.orchestrator.run
            if observation:
                self.orchestrator.request_revision(observation)
            prompt = self.orchestrator.build_planning_prompt(observation)
            self._planning = True
            self._planning_run_id = run.run_id
            self._append_activity("Generando plan sin editar el repositorio.")
            self._refresh_status()

            def finished(output: str, exit_code: int) -> None:
                self._planning = False
                self._planning_run_id = ""
                if run.status == FrameworkStatus.CANCELLED.value:
                    self._append_activity("La planificación fue cancelada.")
                elif exit_code != 0:
                    self._append_activity("Falló la generación del plan.")
                    self._show_error("El agente no pudo generar el plan. Revisá su salida en la consola.")
                elif not output.strip():
                    self._show_error("El agente devolvió un plan vacío.")
                else:
                    self.plan_edit.setPlainText(output.strip())
                    try:
                        self.orchestrator.set_plan(output.strip())
                    except (OSError, ValueError, RuntimeError) as exc:
                        self._show_error(f"No se pudo guardar el plan: {exc}")
                    else:
                        self._append_activity(f"Plan v{run.plan_version} generado.")
                self.agent_console.finish_framework_run(run.run_id)
                self._refresh_status()

            self.agent_console.run_framework_task(
                run.run_id,
                run.agent_id,
                prompt,
                run.folder,
                finished,
                profile_id=self.agent_console.framework_profile_id(),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._planning = False
            if self.orchestrator is not None:
                self.agent_console.finish_framework_run(
                    self.orchestrator.run.run_id
                )
            self._refresh_status()
            self._show_error(str(exc))

    def _request_revision(self) -> None:
        if self.orchestrator is None or not self.orchestrator.run.plan:
            self._show_error("Primero generá un plan.")
            return
        observation, accepted = QInputDialog.getMultiLineText(
            self,
            "Pedir cambios al plan",
            "¿Qué debe revisar el agente?",
        )
        if accepted and observation.strip():
            self._generate_plan(observation.strip())

    def _new_run(self) -> None:
        if self._is_worker_running() or self._planning:
            self._show_error("Esperá a que termine la actividad actual.")
            return
        if self.orchestrator and self.orchestrator.run.status not in {
            FrameworkStatus.COMPLETED.value,
            FrameworkStatus.CANCELLED.value,
            FrameworkStatus.BLOCKED.value,
            FrameworkStatus.FAILED.value,
            FrameworkStatus.PAUSED.value,
        }:
            self._show_error("Cargá o terminá la ejecución actual antes de iniciar otra.")
            return
        self.orchestrator = None
        self.request_edit.clear()
        self.plan_edit.clear()
        self.activity_view.clear()
        self.save_plan_checkbox.setChecked(False)
        self.plan_path_edit.setText("framework_plan.md")
        self._refresh_status()

    def _approve_and_start(self) -> None:
        if self.orchestrator is None:
            self._show_error("Primero generá o cargá un plan.")
            return
        run = self.orchestrator.run
        revised_plan = self.plan_edit.toPlainText().strip()
        if not revised_plan:
            self._show_error("El plan no puede estar vacío.")
            return
        try:
            run.max_cycles = self.cycle_limit.value()
            run.check_timeout_seconds = self.check_timeout.value()
            if revised_plan != run.plan:
                self.orchestrator.set_plan(revised_plan)
            if self.save_plan_checkbox.isChecked():
                self._write_plan_copy(run, revised_plan)
            self.orchestrator.approve(self.agent_console.framework_profile_id())
        except (OSError, RuntimeError, ValueError) as exc:
            self._show_error(f"No se pudo aprobar el plan: {exc}")
            return
        if run.status != FrameworkStatus.PLAN_READY.value:
            self._show_error(run.last_error or "El repositorio no está listo para aprobar.")
            self._refresh_status()
            return
        self._append_activity(
            f"Plan v{run.plan_version} aprobado para el perfil {run.approved_profile or '(sin perfil)'}."
        )
        self._start_worker()

    def _write_plan_copy(self, run: FrameworkRun, content: str) -> None:
        if not GitVersioning.has_repo(run.folder):
            raise ValueError("La copia del plan solo puede guardarse en un repositorio Git.")
        if not GitVersioning.is_clean(run.folder):
            raise ValueError("Guardá o descartá los cambios existentes antes de copiar el plan al repositorio.")
        relative = Path(self.plan_path_edit.text().strip())
        if (
            not str(relative)
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix.lower() != ".md"
        ):
            raise ValueError("La ruta de la copia debe ser relativa y terminar en .md.")
        if not GitVersioning.check_identity(run.folder):
            raise ValueError("Git no tiene user.name y user.email configurados.")
        root = Path(run.folder).resolve()
        target = (root / relative).resolve()
        if target == root or root not in target.parents:
            raise ValueError("La ruta de la copia debe permanecer dentro del repositorio.")
        if target.exists():
            raise FileExistsError(
                f"El archivo {relative} ya existe; no se sobrescribirá."
            )
        ok, _, error = GitVersioning.run(
            run.folder, ["check-ignore", "--quiet", "--", relative.as_posix()], timeout=10
        )
        if ok:
            raise ValueError("Git ignora la ruta elegida; seleccioná otra ubicación.")
        if error:
            raise RuntimeError(error.strip())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        run.plan_repo_path = relative.as_posix()
        run.save(self.store)

    def _start_worker(self) -> None:
        if self.orchestrator is None or self._is_worker_running():
            return
        worker = FrameworkWorker(self.orchestrator, self)
        worker.agent_requested.connect(self._start_agent_task)
        worker.check_output.connect(self._append_activity)
        worker.finished.connect(self._worker_finished)
        self.worker = worker
        self._append_activity("Iniciando ejecución del framework.")
        worker.start()
        self._refresh_status()

    def _start_agent_task(
        self, run_id: str, agent_id: str, folder: str, profile_id: str, prompt: str
    ) -> None:
        worker = self.worker
        if worker is None or worker.run_id != run_id or not worker.isRunning():
            return
        try:
            self.agent_console.run_framework_task(
                run_id,
                agent_id,
                prompt,
                folder,
                worker.deliver_agent_result,
                profile_id=profile_id,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._append_activity(f"Error al iniciar el agente: {exc}")
            worker.deliver_agent_result(run_id, str(exc), 1)

    def _pause(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.pause_event.set()
            self._append_activity("La ejecución se pausará al finalizar el ciclo actual.")
            self._refresh_status()

    def _resume(self) -> None:
        if self.orchestrator is None:
            return
        try:
            self.orchestrator.resume()
        except RuntimeError as exc:
            self._show_error(str(exc))
            return
        self._start_worker()

    def _cancel(self) -> None:
        if self._planning and self.orchestrator is not None:
            run_id = self._planning_run_id
            self.orchestrator.cancel()
            callback_expected = self.agent_console.cancel_framework_task(run_id)
            if not callback_expected:
                self._planning = False
                self._planning_run_id = ""
                self.agent_console.finish_framework_run(run_id)
            self._append_activity("Cancelación de la planificación solicitada.")
            self._refresh_status()
            return
        if self.worker and self.worker.isRunning():
            self.worker.cancel_event.set()
            self.agent_console.cancel_framework_task(self.worker.run_id)
            self._append_activity("Cancelación solicitada.")
        elif self.orchestrator is not None:
            self.orchestrator.cancel()
            self._append_activity("Ejecución cancelada.")
        self._refresh_status()

    def _load_selected_run(self) -> None:
        if self._is_worker_running() or self._planning:
            self._show_error("Esperá a que termine la actividad actual antes de cargar otra ejecución.")
            return
        run_id = str(self.existing_combo.currentData() or "")
        if not run_id:
            return
        try:
            run = self.store.load(run_id)
            if run is None:
                raise FileNotFoundError(f"No se encontró la ejecución {run_id}.")
            self.orchestrator = self._make_orchestrator(run)
            self.orchestrator.recover_interrupted()
            self.request_edit.setPlainText(run.request)
            self.plan_edit.setPlainText(run.plan)
            self.cycle_limit.setValue(run.max_cycles)
            self.check_timeout.setValue(run.check_timeout_seconds)
            agent_index = self.agent_combo.findData(run.agent_id)
            if agent_index >= 0:
                self.agent_combo.setCurrentIndex(agent_index)
            folder_index = self.folder_combo.findData(run.folder)
            if folder_index < 0:
                label = (
                    f"{Path(run.folder).name} (guardado)"
                    if Path(run.folder).is_dir()
                    else f"{run.folder} (no existe)"
                )
                self.folder_combo.addItem(label, run.folder)
                folder_index = self.folder_combo.findData(run.folder)
            if folder_index >= 0:
                self.folder_combo.setCurrentIndex(folder_index)
            self.save_plan_checkbox.setChecked(bool(run.plan_repo_path))
            self.plan_path_edit.setText(run.plan_repo_path or "framework_plan.md")
            self._render_history()
        except (OSError, RuntimeError, ValueError) as exc:
            self._show_error(f"No se pudo cargar la ejecución: {exc}")
        self._refresh_status()

    def _worker_finished(self) -> None:
        worker = self.sender()
        if worker is self.worker:
            self.agent_console.finish_framework_run(worker.run_id)
            self.worker = None
        self._append_activity(
            f"Ejecución detenida: {self.orchestrator.run.status if self.orchestrator else 'sin estado'}."
        )
        self._render_history()
        self._populate_existing_runs()
        self._refresh_status()

    def _render_history(self) -> None:
        if self.orchestrator is None:
            return
        run = self.orchestrator.run
        entries = []
        for cycle in run.cycles:
            entries.append(
                f"Ciclo {cycle.get('number')}: {cycle.get('status', 'desconocido')}"
            )
            if cycle.get("summary"):
                entries.append(f"  {cycle['summary']}")
            if cycle.get("next_steps"):
                entries.append(f"  Siguientes pasos: {', '.join(cycle['next_steps'])}")
            for check in cycle.get("checks", []):
                entries.append(
                    f"  Check {check.get('command', '')}: {check.get('status', 'desconocido')}"
                )
            commit = cycle.get("commit")
            if commit:
                entries.append(f"  Commit {commit.get('hash', '')}")
            if cycle.get("error"):
                entries.append(f"  Error: {cycle['error']}")
        self.activity_view.setPlainText("\n".join(entries))

    def _append_activity(self, text: str) -> None:
        self.activity_view.appendPlainText(text)

    def _refresh_status(self) -> None:
        run = self.orchestrator.run if self.orchestrator else None
        if run:
            self.state_label.setText(
                f"{run.status} · ciclo {run.cycle}/{run.max_cycles}"
                + (f" · {run.last_error}" if run.last_error else "")
            )
            run_index = self.existing_combo.findData(run.run_id)
            if run_index >= 0:
                self.existing_combo.setItemText(
                    run_index,
                    f"{run.updated_at[:19]} · {run.agent_id} · {run.status} · {run.request[:50]}",
                )
        else:
            self.state_label.setText("Sin ejecución cargada")
        running = self._is_worker_running()
        editable = run is None
        self.folder_combo.setEnabled(editable)
        self.agent_combo.setEnabled(editable)
        self.request_edit.setEnabled(editable)
        self.cycle_limit.setEnabled(editable)
        self.check_timeout.setEnabled(editable)
        self.plan_edit.setReadOnly(
            bool(
                run
                and run.status not in {
                    FrameworkStatus.DRAFT.value,
                    FrameworkStatus.REVISION_REQUESTED.value,
                    FrameworkStatus.WAITING_APPROVAL.value,
                }
            )
            or running
            or self._planning
        )
        can_generate = run is None or (
            run.status in {
                FrameworkStatus.DRAFT.value,
                FrameworkStatus.REVISION_REQUESTED.value,
            }
        )
        self.generate_button.setEnabled(
            not running and not self._planning and can_generate
        )
        self.new_button.setEnabled(not running and not self._planning)
        self.revision_button.setEnabled(
            not running
            and not self._planning
            and bool(run and run.plan and run.status == FrameworkStatus.WAITING_APPROVAL.value)
        )
        self.approve_button.setEnabled(
            not running
            and not self._planning
            and bool(
                run
                and (
                    run.status in {
                        FrameworkStatus.WAITING_APPROVAL.value,
                        FrameworkStatus.PLAN_READY.value,
                    }
                    or (
                        run.status == FrameworkStatus.DRAFT.value
                        and self.plan_edit.toPlainText().strip()
                    )
                )
            )
        )
        self.pause_button.setEnabled(running)
        self.resume_button.setEnabled(
            not running and bool(run and run.status == FrameworkStatus.PAUSED.value)
        )
        self.cancel_button.setEnabled(
            bool(run and run.status not in {
                FrameworkStatus.COMPLETED.value,
                FrameworkStatus.CANCELLED.value,
                FrameworkStatus.BLOCKED.value,
                FrameworkStatus.FAILED.value,
            })
        )
        if self._planning:
            self.cancel_button.setEnabled(True)

    def _is_worker_running(self) -> bool:
        return self.worker is not None

    def has_active_worker(self) -> bool:
        return self._is_worker_running()

    def pause_for_shutdown(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.pause_event.set()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Framework de tarea", message)

    def closeEvent(self, event) -> None:
        if self._is_worker_running():
            self.pause_for_shutdown()
            self._append_activity(
                "Se solicitó pausar al cerrar; el ciclo actual terminará antes de guardar el estado pausado."
            )
            event.ignore()
            return
        event.accept()
