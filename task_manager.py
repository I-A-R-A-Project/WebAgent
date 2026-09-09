"""Persistencia y clasificación segura de tareas de la nueva pestaña."""

import json
import os
import uuid
from datetime import datetime
from urllib.parse import quote
from urllib.request import Request, urlopen

from paths import IA_DATA_DIR


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
        lowered = text.casefold()
        for collection in collections:
            if collection.get("name", "").casefold() in lowered:
                task["collection_id"] = collection["id"]
                task["collection_name"] = collection["name"]
                break
        self.tasks.append(task)
        self.save()
        return task

    def classify_with_gemini(self, task: dict, collections: list[dict], api_key: str) -> dict:
        if not api_key:
            return task
        names = [c.get("name", "") for c in collections]
        prompt = (
            "Clasifica esta tarea contra las colecciones disponibles. "
            "Responde solo JSON {\"collection\": string|null}. "
            f"Tarea: {task['text']}\nColecciones: {names}"
        )
        payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(model, safe='')}:generateContent?key={quote(api_key, safe='')}"
        )
        request = Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode())
        content = body["candidates"][0]["content"]["parts"][0]["text"]
        content = content.strip().removeprefix("```json").removesuffix("```").strip()
        chosen = json.loads(content).get("collection")
        for collection in collections:
            if chosen and collection.get("name") == chosen:
                task["collection_id"] = collection["id"]
                task["collection_name"] = collection["name"]
                self.save()
                break
        return task
