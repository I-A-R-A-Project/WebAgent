"""Persistencia segura de tareas de la nueva pestaña."""

import json
import uuid
from datetime import datetime

from core.paths import IA_DATA_DIR


class TaskManager:
    def __init__(self, profile_id: str):
        self.path = IA_DATA_DIR / "tasks" / f"{profile_id}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.tasks = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        except (OSError, json.JSONDecodeError):
            self.tasks = []

    def save(self):
        self.path.write_text(json.dumps(self.tasks, ensure_ascii=False, indent=2), encoding="utf-8")

    def cancel(self, task_id: str) -> dict | None:
        for task in self.tasks:
            if task.get("id") == task_id and task.get("status") not in ("completed", "cancelled"):
                task["status"] = "cancelled"
                task["cancelled"] = datetime.now().isoformat()
                self.save()
                return task
        return None

    def delete(self, task_id: str) -> bool:
        original_count = len(self.tasks)
        self.tasks = [task for task in self.tasks if task.get("id") != task_id]
        if len(self.tasks) == original_count:
            return False
        self.save()
        return True

    def add(self, text: str, collections: list[dict]) -> dict:
        task = {
            "id": uuid.uuid4().hex[:12],
            "text": text,
            "status": "pending",
            "collection_id": "",
            "collection_name": "",
            "created": datetime.now().isoformat(),
        }
        self.tasks.append(task)
        self.save()
        return task
