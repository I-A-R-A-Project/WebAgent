"""Núcleo independiente de Qt para ciclos guiados de agentes IA."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

from core.automation import (
    CheckResult,
    build_framework_check_plan,
    run_checks,
)
from core.file_ops import GitVersioning


class FrameworkStatus(str, Enum):
    DRAFT = "DRAFT"
    PLAN_READY = "PLAN_READY"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    RUNNING_AGENT = "RUNNING_AGENT"
    RUNNING_CHECKS = "RUNNING_CHECKS"
    CHECKS_FAILED = "CHECKS_FAILED"
    CHECKS_PASSED = "CHECKS_PASSED"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


TERMINAL_STATUSES = {
    FrameworkStatus.COMPLETED,
    FrameworkStatus.CANCELLED,
    FrameworkStatus.BLOCKED,
    FrameworkStatus.FAILED,
}

REQUIRED_PLAN_SECTIONS = (
    "Objetivo",
    "Contexto y supuestos",
    "Archivos a modificar",
    "Pasos de implementación",
    "Criterios de aceptación verificables",
    "Checks esperados",
    "Riesgos y decisiones pendientes",
)


@dataclass
class CycleRecord:
    number: int
    status: str = FrameworkStatus.RUNNING_AGENT.value
    agent_exit_code: int | None = None
    checks: list[dict] = field(default_factory=list)
    commit: dict | None = None
    summary: str = ""
    next_steps: list[str] = field(default_factory=list)
    criteria_met: list[str] = field(default_factory=list)
    workspace_fingerprint_after: str = ""
    error: str = ""


@dataclass
class FrameworkRun:
    run_id: str
    folder: str
    agent_id: str
    request: str
    max_cycles: int = 10
    check_timeout_seconds: int = 600
    status: str = FrameworkStatus.DRAFT.value
    cycle: int = 0
    plan: str = ""
    plan_version: int = 0
    plan_versions: list[dict] = field(default_factory=list)
    plan_repo_path: str = ""
    base_head: str = ""
    base_branch: str = ""
    workspace_fingerprint: str = ""
    approved_at: str = ""
    approved_profile: str = ""
    cycles: list[dict] = field(default_factory=list)
    last_error: str = ""
    resume_status: str = ""
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("La ejecución necesita un identificador.")
        if not Path(self.folder).expanduser().is_absolute():
            raise ValueError("La carpeta del repositorio debe ser una ruta absoluta.")
        if not self.agent_id.strip() or not self.request.strip():
            raise ValueError("La ejecución necesita un agente y una solicitud.")
        if len(self.request) > 50_000:
            raise ValueError("La solicitud supera el máximo de 50.000 caracteres.")
        if not 1 <= self.max_cycles <= 100:
            raise ValueError("El límite de ciclos debe estar entre 1 y 100.")
        if not 1 <= self.check_timeout_seconds <= 3600:
            raise ValueError("El timeout de checks debe estar entre 1 y 3600 segundos.")
        if self.status not in {status.value for status in FrameworkStatus}:
            raise ValueError(f"Estado de framework desconocido: {self.status}")

    def save(self, store: "FrameworkStore") -> None:
        self.updated_at = _now()
        store.save(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FrameworkStore:
    """Almacén JSON atómico de ejecuciones, sin secretos ni logs completos."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "-", run_id)
        return self.base_dir / f"{safe_id}.json"

    def save(self, run: FrameworkRun) -> None:
        target = self._path(run.run_id)
        payload = asdict(run)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, run_id: str) -> FrameworkRun | None:
        try:
            with self._path(run_id).open(encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"No se pudo leer la ejecución {run_id}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"El archivo de ejecución {run_id} no contiene un objeto JSON.")
        try:
            return FrameworkRun(**data)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"El estado de la ejecución {run_id} es inválido: {exc}") from exc

    def all_runs(self, limit: int | None = 50) -> list[FrameworkRun]:
        runs = []
        for path in self.base_dir.glob("*.json"):
            run = self.load(path.stem)
            if run:
                runs.append(run)
        ordered = sorted(runs, key=lambda item: item.updated_at, reverse=True)
        return ordered if limit is None else ordered[:max(0, limit)]

    def incomplete(self) -> list[FrameworkRun]:
        return [
            run for run in self.all_runs(limit=None)
            if FrameworkStatus(run.status) not in TERMINAL_STATUSES
        ]


def parse_agent_signal(text: str) -> dict[str, Any]:
    """Extrae el último bloque JSON delimitado sin confiar en texto libre."""
    matches = re.findall(
        r"```(?:json)?\s*(\{.*?\})\s*```", text or "", flags=re.IGNORECASE | re.DOTALL
    )
    for candidate in reversed(matches):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("status") in {"continue", "complete", "blocked"}:
            next_steps = data.get("next_steps", [])
            criteria_met = data.get("criteria_met", [])
            return {
                "status": data["status"],
                "summary": str(data.get("summary", ""))[:2000],
                "next_steps": (
                    [str(item)[:500] for item in next_steps[:20]]
                    if isinstance(next_steps, list) else []
                ),
                "criteria_met": (
                    [str(item)[:500] for item in criteria_met[:20]]
                    if isinstance(criteria_met, list) else []
                ),
            }
    return {"status": "continue", "summary": "", "next_steps": [], "criteria_met": []}


class FrameworkOrchestrator:
    """Orquesta ciclos secuenciales de agente, checks y commits."""

    _folder_locks: dict[str, threading.Lock] = {}
    _folder_locks_guard = threading.Lock()

    def __init__(
        self,
        run: FrameworkRun,
        store: FrameworkStore,
        *,
        check_runner: Callable[..., list[CheckResult]] = run_checks,
        check_commands: Iterable[str] | Callable[[str], Iterable[str]] | None = None,
    ):
        self.run = run
        self.store = store
        self.check_runner = check_runner
        self.check_commands = check_commands

    @classmethod
    def _folder_lock(cls, folder: str) -> threading.Lock:
        key = os.path.normcase(os.path.realpath(folder))
        with cls._folder_locks_guard:
            return cls._folder_locks.setdefault(key, threading.Lock())

    def _set_status(
        self, status: FrameworkStatus, error: str | None = None
    ) -> None:
        self.run.status = status.value
        if error is not None:
            self.run.last_error = error
        self.run.save(self.store)

    def set_plan(self, plan: str) -> None:
        if self.run.status not in {
            FrameworkStatus.DRAFT.value,
            FrameworkStatus.REVISION_REQUESTED.value,
            FrameworkStatus.WAITING_APPROVAL.value,
            FrameworkStatus.PLAN_READY.value,
        }:
            raise RuntimeError(f"No se puede cambiar el plan desde {self.run.status}.")
        if not plan.strip():
            raise ValueError("El plan no puede estar vacío.")
        if len(plan) > 100_000:
            raise ValueError("El plan supera el máximo de 100.000 caracteres.")
        headings = {
            re.sub(r"\s+", " ", line.lstrip("#").strip()).casefold()
            for line in plan.splitlines()
            if re.match(r"^#{1,6}\s+", line)
        }
        missing = [
            section for section in REQUIRED_PLAN_SECTIONS
            if section.casefold() not in headings
        ]
        if missing:
            raise ValueError(
                "Al plan le faltan secciones obligatorias: " + ", ".join(missing)
            )
        self.run.plan_version += 1
        self.run.plan = plan
        self.run.plan_versions.append(
            {"version": self.run.plan_version, "created_at": _now(), "content": plan}
        )
        self._set_status(FrameworkStatus.WAITING_APPROVAL, "")

    def request_revision(self, observation: str) -> None:
        if self.run.status != FrameworkStatus.WAITING_APPROVAL.value:
            raise RuntimeError("Solo se puede pedir cambios antes de aprobar el plan.")
        if not observation.strip():
            raise ValueError("La observación de revisión no puede estar vacía.")
        self._set_status(FrameworkStatus.REVISION_REQUESTED, observation[:2000])

    def approve(self, profile_id: str = "") -> None:
        if self.run.status not in {
            FrameworkStatus.WAITING_APPROVAL.value,
            FrameworkStatus.PLAN_READY.value,
        }:
            raise RuntimeError(f"No se puede aprobar el plan desde {self.run.status}.")
        if not self.run.plan.strip():
            raise ValueError("No existe un plan para aprobar.")
        if not GitVersioning.has_repo(self.run.folder):
            self._set_status(FrameworkStatus.BLOCKED, "La carpeta no es un repositorio Git.")
            return
        if not GitVersioning.is_clean(self.run.folder):
            if not self._has_only_approved_plan_file():
                self._set_status(
                    FrameworkStatus.BLOCKED,
                    "El repositorio tiene cambios ajenos al plan; guardalos o descartalos antes de aprobar.",
                )
                return
        if not GitVersioning.check_identity(self.run.folder):
            self._set_status(
                FrameworkStatus.BLOCKED,
                "Git no tiene user.name y user.email configurados.",
            )
            return
        self.run.base_head = GitVersioning.get_head(self.run.folder)
        if not self.run.base_head:
            self._set_status(
                FrameworkStatus.BLOCKED, "No se pudo determinar HEAD de Git."
            )
            return
        self.run.base_branch = GitVersioning.get_current_branch(self.run.folder)
        if not self.run.base_branch:
            self._set_status(
                FrameworkStatus.BLOCKED,
                "No se pudo determinar la rama activa de Git.",
            )
            return
        try:
            self.run.workspace_fingerprint = GitVersioning.get_worktree_fingerprint(
                self.run.folder
            )
        except RuntimeError as exc:
            self._set_status(FrameworkStatus.BLOCKED, str(exc))
            return
        self.run.approved_at = _now()
        self.run.approved_profile = profile_id
        self._set_status(FrameworkStatus.PLAN_READY, "")

    def _has_only_approved_plan_file(self) -> bool:
        relative = self.run.plan_repo_path.strip()
        if not relative:
            return False
        root = Path(self.run.folder).resolve()
        target = (root / relative).resolve()
        if target == root or root not in target.parents or not target.is_file():
            return False
        try:
            if target.read_text(encoding="utf-8") != self.run.plan:
                return False
        except (OSError, UnicodeError):
            return False
        try:
            changed = GitVersioning.get_changed_files(self.run.folder)
        except RuntimeError:
            return False
        return changed == [relative]

    def recover_interrupted(self) -> None:
        resumable_states = {
            FrameworkStatus.PLAN_READY.value,
            FrameworkStatus.CHECKS_FAILED.value,
            FrameworkStatus.CHECKS_PASSED.value,
            FrameworkStatus.COMMITTED.value,
            FrameworkStatus.RUNNING_AGENT.value,
            FrameworkStatus.RUNNING_CHECKS.value,
            FrameworkStatus.COMMITTING.value,
        }
        if self.run.status not in resumable_states:
            return
        original_status = self.run.status
        was_interrupted = original_status in {
            FrameworkStatus.RUNNING_AGENT.value,
            FrameworkStatus.RUNNING_CHECKS.value,
            FrameworkStatus.CHECKS_PASSED.value,
            FrameworkStatus.COMMITTING.value,
        }
        self.run.resume_status = self.run.status
        if was_interrupted:
            self.run.resume_status = (
                FrameworkStatus.PLAN_READY.value
                if self.run.cycle == 0 else FrameworkStatus.CHECKS_FAILED.value
            )
        if self.run.cycles and self.run.cycles[-1].get("status") in {
            FrameworkStatus.RUNNING_AGENT.value,
            FrameworkStatus.RUNNING_CHECKS.value,
            FrameworkStatus.CHECKS_PASSED.value,
            FrameworkStatus.COMMITTING.value,
        }:
            self.run.cycles[-1]["status"] = FrameworkStatus.CHECKS_FAILED.value
            self.run.cycles[-1]["error"] = (
                "La aplicación se cerró durante este ciclo. Revisá el repositorio antes de reanudar."
            )
        recovery_note = (
            "La aplicación se cerró durante este ciclo. Revisá el repositorio antes de reanudar."
            if was_interrupted else None
        )
        self._set_status(FrameworkStatus.PAUSED, recovery_note)
        if was_interrupted:
            try:
                current = GitVersioning.get_worktree_fingerprint(self.run.folder)
            except RuntimeError as exc:
                self._set_status(FrameworkStatus.BLOCKED, str(exc))
            else:
                if self.run.workspace_fingerprint and current != self.run.workspace_fingerprint:
                    self.run.last_error = (
                        "El working tree cambió durante el cierre inesperado. "
                        "Revisá esos cambios antes de reanudar."
                    )
                self.run.workspace_fingerprint = current
                self.run.save(self.store)

    def build_planning_prompt(self, observation: str = "") -> str:
        return (
            "Inspeccioná el repositorio antes de proponer cambios. Devolvé únicamente "
            "un plan Markdown con las secciones # Objetivo, # Contexto y supuestos, "
            "# Archivos a modificar, # Pasos de implementación, # Criterios de "
            "aceptación verificables, # Checks esperados y # Riesgos y decisiones "
            "pendientes. No edites archivos, no ejecutes comandos y no hagas commits. "
            "Señalá incertidumbres en vez de inventar detalles.\n\n"
            f"TAREA ORIGINAL:\n{self.run.request}\n\n"
            f"OBSERVACIÓN DE REVISIÓN:\n{observation or '(ninguna)'}"
        )

    def build_cycle_prompt(self, cycle: int, check_context: str = "") -> str:
        changed_files = GitVersioning.get_changed_files(self.run.folder)
        files_context = "\n".join(f"- {path}" for path in changed_files[:100]) or "- (ninguno)"
        current_branch = GitVersioning.get_current_branch(self.run.folder) or "(desconocida)"
        return (
            f"Implementá el siguiente paso pendiente del plan. Es el ciclo {cycle} "
            f"de un máximo de {self.run.max_cycles}. No ejecutes chequeos, tests, builds, "
            "linters ni comandos de verificación: WebAgent los ejecutará automáticamente "
            "al terminar tu ciclo. No hagas el commit; WebAgent lo hará solo si todos "
            "los checks pasan. Inspeccioná primero los cambios existentes y no borres "
            "trabajo válido. Al final devolvé un bloque JSON cercado con status "
            "continue, complete o blocked, summary, next_steps y criteria_met; "
            "no marques complete si quedan tareas del plan.\n\n"
            f"ESTADO GIT: rama={current_branch}; HEAD={self.run.base_head or '(desconocido)'}\n"
            f"Archivos modificados desde el último commit:\n{files_context}\n\n"
            f"SOLICITUD:\n{self.run.request}\n\nPLAN APROBADO (v{self.run.plan_version}):\n"
            f"{self.run.plan}\n\nCONTEXTO DEL CICLO ANTERIOR:\n{check_context or '(primer ciclo)'}"
        )

    def run_cycle(
        self,
        agent_runner: Callable[[str], tuple[str, int]],
        *,
        on_check_output: Callable[[str, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> FrameworkRun:
        lock = self._folder_lock(self.run.folder)
        if not lock.acquire(blocking=False):
            raise RuntimeError("Ya hay un ciclo activo para esta carpeta.")
        try:
            return self._run_cycle(
                agent_runner,
                on_check_output=on_check_output,
                cancel_event=cancel_event,
            )
        finally:
            lock.release()

    def _run_cycle(
        self,
        agent_runner: Callable[[str], tuple[str, int]],
        *,
        on_check_output: Callable[[str, str], None] | None,
        cancel_event: threading.Event | None,
    ) -> FrameworkRun:
        if self.run.status not in {
            FrameworkStatus.PLAN_READY.value,
            FrameworkStatus.COMMITTED.value,
            FrameworkStatus.CHECKS_FAILED.value,
        }:
            raise RuntimeError(f"Estado no ejecutable: {self.run.status}")
        if self.run.cycle >= self.run.max_cycles:
            self._set_status(FrameworkStatus.BLOCKED, "Se alcanzó el máximo de ciclos.")
            return self.run
        if not GitVersioning.has_repo(self.run.folder):
            self._set_status(FrameworkStatus.BLOCKED, "La carpeta dejó de ser un repositorio Git.")
            return self.run
        if GitVersioning.get_head(self.run.folder) != self.run.base_head:
            self._set_status(FrameworkStatus.BLOCKED, "El repositorio cambió desde la aprobación.")
            return self.run
        if GitVersioning.get_current_branch(self.run.folder) != self.run.base_branch:
            self._set_status(FrameworkStatus.BLOCKED, "La rama cambió desde la aprobación.")
            return self.run
        try:
            current_fingerprint = GitVersioning.get_worktree_fingerprint(self.run.folder)
        except RuntimeError as exc:
            self._set_status(FrameworkStatus.BLOCKED, str(exc))
            return self.run
        if current_fingerprint != self.run.workspace_fingerprint:
            self._set_status(
                FrameworkStatus.BLOCKED,
                "El working tree cambió desde la aprobación o pausa; revisá los cambios antes de continuar.",
            )
            return self.run
        if cancel_event and cancel_event.is_set():
            self._set_status(FrameworkStatus.CANCELLED, "Ejecución cancelada.")
            return self.run

        check_context = self.run.last_error
        if self.run.cycles:
            previous = self.run.cycles[-1]
            previous_context = (
                f"Estado del ciclo {previous.get('number')}: "
                f"{previous.get('status', 'desconocido')}\n"
                f"Resumen: {previous.get('summary', '')}\n"
            )
            next_steps = previous.get("next_steps", [])
            if isinstance(next_steps, list) and next_steps:
                previous_context += "Pasos pendientes indicados: " + "; ".join(
                    str(item)[:500] for item in next_steps[:20]
                )
            criteria_met = previous.get("criteria_met", [])
            if isinstance(criteria_met, list) and criteria_met:
                previous_context += "\nCriterios declarados cumplidos: " + "; ".join(
                    str(item)[:500] for item in criteria_met[:20]
                )
            check_context = "\n".join(
                part for part in (previous_context, check_context) if part.strip()
            )[-6000:]
        self.run.cycle += 1
        cycle = CycleRecord(self.run.cycle)
        self.run.cycles.append(asdict(cycle))
        self._set_status(FrameworkStatus.RUNNING_AGENT)
        try:
            output, exit_code = agent_runner(
                self.build_cycle_prompt(self.run.cycle, check_context)
            )
        except (OSError, RuntimeError, ValueError) as exc:
            cycle.status = FrameworkStatus.FAILED.value
            cycle.error = str(exc)[-4000:]
            self.run.cycles[-1] = asdict(cycle)
            self._set_status(FrameworkStatus.FAILED, cycle.error)
            return self.run
        if cancel_event and cancel_event.is_set():
            cycle.status = FrameworkStatus.CANCELLED.value
            cycle.error = "Ejecución cancelada."
            self.run.cycles[-1] = asdict(cycle)
            self._set_status(FrameworkStatus.CANCELLED, cycle.error)
            return self.run
        cycle.agent_exit_code = exit_code
        signal = parse_agent_signal(output)
        if exit_code != 0:
            cycle.status = FrameworkStatus.FAILED.value
            cycle.error = (output or f"El agente terminó con código {exit_code}.")[-4000:]
            self._set_status(FrameworkStatus.FAILED, cycle.error)
            self.run.cycles[-1] = asdict(cycle)
            self.run.save(self.store)
            return self.run
        if (
            GitVersioning.get_head(self.run.folder) != self.run.base_head
            or GitVersioning.get_current_branch(self.run.folder) != self.run.base_branch
        ):
            cycle.status = FrameworkStatus.BLOCKED.value
            cycle.error = (
                "El agente cambió HEAD o la rama; el framework no continuará este ciclo."
            )
            self.run.cycles[-1] = asdict(cycle)
            self._set_status(FrameworkStatus.BLOCKED, cycle.error)
            return self.run
        cycle.summary = signal["summary"]
        cycle.next_steps = signal["next_steps"]
        cycle.criteria_met = signal["criteria_met"]
        if signal["status"] == "blocked":
            cycle.status = FrameworkStatus.BLOCKED.value
            cycle.error = signal["summary"] or "El agente solicitó intervención humana."
            self.run.cycles[-1] = asdict(cycle)
            self._set_status(FrameworkStatus.BLOCKED, cycle.error)
            return self.run
        if cancel_event and cancel_event.is_set():
            cycle.status = FrameworkStatus.CANCELLED.value
            cycle.error = "Ejecución cancelada."
            self.run.cycles[-1] = asdict(cycle)
            self._set_status(FrameworkStatus.CANCELLED, cycle.error)
            return self.run

        self._set_status(FrameworkStatus.RUNNING_CHECKS)
        commands = self._get_check_commands()
        try:
            checks = self.check_runner(
                self.run.folder,
                commands,
                timeout_seconds=self.run.check_timeout_seconds,
                on_output=on_check_output,
                cancel_event=cancel_event,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            checks = [
                CheckResult("", "process_error", error=f"No se pudieron ejecutar los checks: {exc}")
            ]
        cycle.checks = [item.to_dict() for item in checks]
        if cancel_event and cancel_event.is_set():
            cycle.status = FrameworkStatus.CANCELLED.value
            cycle.error = "Ejecución cancelada."
            self.run.cycles[-1] = asdict(cycle)
            self._set_status(FrameworkStatus.CANCELLED, cycle.error)
            return self.run
        failed = next((item for item in checks if item.status != "passed"), None)
        if not checks:
            failed = CheckResult(
                "",
                "configuration_error",
                error="No se ejecutó ningún check confiable.",
            )
            cycle.checks = [failed.to_dict()]
        if failed:
            cycle.status = FrameworkStatus.CHECKS_FAILED.value
            cycle.error = (
                f"Check: {failed.command or '(sin comando)'}\n"
                f"Estado: {failed.status}; código: {failed.exit_code}\n"
                f"Error: {failed.error}\n"
                f"Salida relevante:\n{failed.output}"
            )[-4000:]
            if failed.status == "cancelled":
                cycle.status = FrameworkStatus.CANCELLED.value
                cycle.error = cycle.error or "Ejecución cancelada."
                self._set_status(FrameworkStatus.CANCELLED, cycle.error)
            elif failed.status == "configuration_error":
                cycle.status = FrameworkStatus.BLOCKED.value
                cycle.error = cycle.error or "La configuración de checks no es válida."
                self._set_status(FrameworkStatus.BLOCKED, cycle.error)
            elif failed.status == "process_error":
                cycle.status = FrameworkStatus.FAILED.value
                cycle.error = cycle.error or "No se pudo iniciar el proceso de verificación."
                self._set_status(FrameworkStatus.FAILED, cycle.error)
            else:
                cycle.error = cycle.error or f"El check {failed.command!r} falló."
                try:
                    self.run.workspace_fingerprint = (
                        GitVersioning.get_worktree_fingerprint(self.run.folder)
                    )
                except RuntimeError as exc:
                    cycle.status = FrameworkStatus.BLOCKED.value
                    cycle.error = str(exc)
                    self._set_status(FrameworkStatus.BLOCKED, cycle.error)
                else:
                    cycle.workspace_fingerprint_after = self.run.workspace_fingerprint
                    previous = self.run.cycles[-2] if len(self.run.cycles) > 1 else {}
                    previous_failure = next(
                        (
                            item for item in previous.get("checks", [])
                            if item.get("status") != "passed"
                        ),
                        None,
                    )
                    unchanged_repeat = (
                        previous_failure is not None
                        and previous.get("workspace_fingerprint_after")
                        == self.run.workspace_fingerprint
                        and previous_failure.get("command") == failed.command
                        and previous_failure.get("status") == failed.status
                        and previous_failure.get("exit_code") == failed.exit_code
                    )
                    if unchanged_repeat:
                        cycle.status = FrameworkStatus.BLOCKED.value
                        cycle.error = (
                            f"El check {failed.command!r} volvió a fallar sin cambios en el working tree."
                        )
                        self._set_status(FrameworkStatus.BLOCKED, cycle.error)
                    else:
                        self._set_status(FrameworkStatus.CHECKS_FAILED, cycle.error)
            self.run.cycles[-1] = asdict(cycle)
            self.run.save(self.store)
            return self.run

        if GitVersioning.get_head(self.run.folder) != self.run.base_head:
            cycle.status = FrameworkStatus.BLOCKED.value
            cycle.error = "HEAD cambió durante el ciclo; no se creará un commit."
            self.run.cycles[-1] = asdict(cycle)
            self._set_status(FrameworkStatus.BLOCKED, cycle.error)
            return self.run
        if GitVersioning.get_current_branch(self.run.folder) != self.run.base_branch:
            cycle.status = FrameworkStatus.BLOCKED.value
            cycle.error = "La rama cambió durante el ciclo; no se creará un commit."
            self.run.cycles[-1] = asdict(cycle)
            self._set_status(FrameworkStatus.BLOCKED, cycle.error)
            return self.run

        self._set_status(FrameworkStatus.COMMITTING)
        ok, commit_hash, files, error = GitVersioning.commit_framework_changes(
            self.run.folder,
            f"chore(framework): complete cycle {self.run.cycle}",
            base_head=self.run.base_head,
            base_branch=self.run.base_branch,
            cancel_event=cancel_event,
        )
        if not ok and files:
            cycle.status = (
                FrameworkStatus.CANCELLED.value
                if "cancelada" in error.lower()
                else FrameworkStatus.BLOCKED.value
                if any(
                    marker in error
                    for marker in (
                        "HEAD cambió",
                        "rama cambió",
                        "identidad",
                        "Git no tiene",
                        "repositorio",
                    )
                )
                else FrameworkStatus.FAILED.value
            )
            cycle.error = error
            self._set_status(FrameworkStatus(cycle.status), error)
            self.run.cycles[-1] = asdict(cycle)
            self.run.save(self.store)
            return self.run
        if not ok and not files:
            if error == "No hay cambios para commitear." and signal["status"] == "complete":
                cycle.status = FrameworkStatus.COMPLETED.value
                self.run.cycles[-1] = asdict(cycle)
                self._set_status(FrameworkStatus.COMPLETED, "")
                return self.run
            if error != "No hay cambios para commitear.":
                cycle.status = (
                    FrameworkStatus.CANCELLED.value
                    if "cancelada" in error.lower()
                    else FrameworkStatus.BLOCKED.value
                    if any(
                        marker in error
                        for marker in (
                            "HEAD cambió",
                            "rama cambió",
                            "identidad",
                            "Git no tiene",
                            "repositorio",
                            "Ruta fuera",
                        )
                    )
                    else FrameworkStatus.FAILED.value
                )
                cycle.error = error or "No se pudo crear el commit."
                self._set_status(FrameworkStatus(cycle.status), cycle.error)
                self.run.cycles[-1] = asdict(cycle)
                self.run.save(self.store)
                return self.run
            cycle.status = FrameworkStatus.BLOCKED.value
            cycle.error = error or "El agente no produjo cambios."
            self._set_status(FrameworkStatus.BLOCKED, cycle.error)
            self.run.cycles[-1] = asdict(cycle)
            self.run.save(self.store)
            return self.run
        cycle.commit = {"hash": commit_hash, "files": files} if ok else None
        cycle.status = FrameworkStatus.COMMITTED.value
        self.run.cycles[-1] = asdict(cycle)
        self.run.base_head = commit_hash
        try:
            self.run.workspace_fingerprint = GitVersioning.get_worktree_fingerprint(
                self.run.folder
            )
        except RuntimeError as exc:
            cycle.status = FrameworkStatus.FAILED.value
            cycle.error = str(exc)
            self.run.cycles[-1] = asdict(cycle)
            self._set_status(FrameworkStatus.FAILED, cycle.error)
            return self.run
        if signal["status"] == "complete":
            self._set_status(FrameworkStatus.COMPLETED, "")
        else:
            self._set_status(FrameworkStatus.COMMITTED, "")
        return self.run

    def _get_check_commands(self) -> list[str]:
        if self.check_commands is None:
            return build_framework_check_plan(self.run.folder)
        commands = (
            self.check_commands(self.run.folder)
            if callable(self.check_commands) else self.check_commands
        )
        return [command for command in commands if isinstance(command, str)]

    def run_until_stopped(
        self,
        agent_runner: Callable[[str], tuple[str, int]],
        *,
        on_check_output: Callable[[str, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
    ) -> FrameworkRun:
        lock = self._folder_lock(self.run.folder)
        if not lock.acquire(blocking=False):
            raise RuntimeError("Ya hay una ejecución activa para esta carpeta.")
        try:
            while True:
                if cancel_event and cancel_event.is_set():
                    self._set_status(FrameworkStatus.CANCELLED, "Ejecución cancelada.")
                    return self.run
                if pause_event and pause_event.is_set():
                    self.run.resume_status = self.run.status
                    self._set_status(FrameworkStatus.PAUSED)
                    return self.run
                result = self._run_cycle(
                    agent_runner,
                    on_check_output=on_check_output,
                    cancel_event=cancel_event,
                )
                if result.status not in {
                    FrameworkStatus.COMMITTED.value,
                    FrameworkStatus.CHECKS_FAILED.value,
                }:
                    return result
                if pause_event and pause_event.is_set():
                    self.run.resume_status = result.status
                    self._set_status(FrameworkStatus.PAUSED)
                    return self.run
                if cancel_event and cancel_event.is_set():
                    self._set_status(FrameworkStatus.CANCELLED, "Ejecución cancelada.")
                    return self.run
        finally:
            lock.release()

    def resume(self) -> None:
        if self.run.status != FrameworkStatus.PAUSED.value:
            raise RuntimeError("Solo se puede reanudar una ejecución pausada.")
        next_status = self.run.resume_status or FrameworkStatus.PLAN_READY.value
        if next_status not in {
            FrameworkStatus.PLAN_READY.value,
            FrameworkStatus.COMMITTED.value,
            FrameworkStatus.CHECKS_FAILED.value,
        }:
            raise RuntimeError(f"No se puede reanudar desde {next_status}.")
        self.run.resume_status = ""
        self._set_status(FrameworkStatus(next_status))

    def cancel(self) -> None:
        if FrameworkStatus(self.run.status) in TERMINAL_STATUSES:
            return
        self._set_status(FrameworkStatus.CANCELLED, "Ejecución cancelada por el usuario.")
