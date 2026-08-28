"""Persistencia y clasificación segura de tareas de la nueva pestaña."""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


class TaskManager:
    def __init__(self, profile_id: str):
        self.path = Path.home() / ".ia_browser" / "tasks" / f"{profile_id}.json"
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

    def add(self, text: str, collections: list[dict]) -> dict:
        task = {
            "id": uuid.uuid4().hex[:12],
            "text": text,
            "status": "pending",
            "collection_id": "",
            "collection_name": "",
            "created": datetime.now().isoformat(),
        }
        lowered = text.casefold()
        for collection in collections:
            if collection.get("name", "").casefold() in lowered:
                task["collection_id"] = collection["id"]
                task["collection_name"] = collection["name"]
                break
        self.tasks.append(task)
        self.save()
        return task

    def classify_with_anyapi(self, task: dict, collections: list[dict], api_key: str) -> dict:
        if not api_key:
            return task
        names = [c.get("name", "") for c in collections]
        payload = json.dumps({
            "model": os.environ.get("ANYAPI_MODEL", "openai/gpt-4-turbo"),
            "messages": [{"role": "user", "content": (
                "Clasifica esta tarea contra las colecciones disponibles. "
                "Responde solo JSON {\"collection\": string|null}. "
                f"Tarea: {task['text']}\nColecciones: {names}"
            )}],
        }).encode()
        request = Request(
            "https://api.anyapi.ai/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode())
        content = body["choices"][0]["message"]["content"]
        chosen = json.loads(content).get("collection")
        for collection in collections:
            if chosen and collection.get("name") == chosen:
                task["collection_id"] = collection["id"]
                task["collection_name"] = collection["name"]
                self.save()
                break
        return task
