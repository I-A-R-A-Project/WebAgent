"""Captura de tráfico de una pestaña mediante Chrome DevTools Protocol."""

import base64
import copy
import json
import mimetypes
import os
import re
import threading
import time
import tempfile
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from PyQt6.QtCore import QObject, pyqtSignal


class CdpHarWorker(QObject):
    """Worker que mantiene una sesión CDP y escribe el HAR de forma incremental."""

    finished = pyqtSignal()
    failed = pyqtSignal(str)
    response_captured = pyqtSignal(int)
    ready = pyqtSignal()
    output_path_changed = pyqtSignal(str)

    def __init__(self, page_url: str, output_path: str, parent=None):
        super().__init__(parent)
        self.page_url = page_url
        self.output_path = Path(output_path)
        self._stop_event = threading.Event()
        self._entries = []
        self._requests = {}
        self._responses = {}
        self._pending_messages = deque()
        self._next_command_id = 1
        self._started_at = time.time()
        self._content_dir = self.output_path.with_name(
            f"{self.output_path.stem}_content"
        )

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            import websocket

            target = self._find_target()
            socket = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=1)
            try:
                self._send(
                    socket,
                    self._next_command_id,
                    "Network.enable",
                    {
                        # X puede devolver timelines GraphQL de varios MB.
                        "maxTotalBufferSize": 256 * 1024 * 1024,
                        "maxResourceBufferSize": 64 * 1024 * 1024,
                    },
                )
                self._next_command_id += 1
                if not self._stop_event.is_set():
                    self.ready.emit()
                self._write_har()
                while not self._stop_event.is_set():
                    try:
                        message = self._receive_message(socket)
                    except websocket.WebSocketTimeoutException:
                        continue
                    self._handle_message(socket, message)
            finally:
                for request_id in list(self._responses):
                    self._capture_response(socket, request_id)
                socket.close()
            self._write_har()
            self.finished.emit()
        except Exception as exc:
            if self._stop_event.is_set():
                try:
                    self._write_har()
                except Exception:
                    pass
                self.finished.emit()
            else:
                self.failed.emit(str(exc))

    def _find_target(self):
        endpoint = "http://127.0.0.1:9222/json/list"
        with urllib.request.urlopen(endpoint, timeout=3) as response:
            targets = json.load(response)
        pages = [
            item for item in targets
            if item.get("type") == "page" and item.get("webSocketDebuggerUrl")
        ]
        if not pages:
            raise RuntimeError(
                "No se encontraron pestañas CDP. Verificá que el navegador esté iniciado."
            )
        exact = [item for item in pages if item.get("url") == self.page_url]
        return exact[0] if exact else pages[0]

    def _send(self, socket, command_id, method, params=None):
        socket.send(json.dumps({
            "id": command_id,
            "method": method,
            "params": params or {},
        }))

    def _receive_message(self, socket):
        if self._pending_messages:
            return self._pending_messages.popleft()
        return json.loads(socket.recv())

    def _handle_message(self, socket, message):
        method = message.get("method")
        params = message.get("params", {})
        if method == "Network.requestWillBeSent":
            self._remember_request(params)
        elif method == "Network.responseReceived":
            self._remember_response(params)
        elif method == "Network.loadingFinished":
            self._capture_response(socket, params.get("requestId"))
        elif method == "Network.loadingFailed":
            self._capture_response(socket, params.get("requestId"))

    def _remember_request(self, params):
        request = params.get("request", {})
        self._requests[params.get("requestId")] = {
            "request": request,
            "startedDateTime": datetime.now(timezone.utc).isoformat(),
            "started": time.time(),
        }

    def _remember_response(self, params):
        request_id = params.get("requestId")
        request_info = self._requests.get(request_id)
        if not request_info:
            return
        self._responses[request_id] = {
            "request": request_info,
            "response": params.get("response", {}),
        }

    def _capture_response(self, socket, request_id):
        response_info = self._responses.pop(request_id, None)
        if not response_info:
            return
        self._requests.pop(request_id, None)
        request_info = response_info["request"]
        response = response_info["response"]
        body, _encoded = self._get_body(socket, request_id)
        content = {
            "mimeType": response.get("mimeType", ""),
        }
        if body:
            content["size"] = self._save_content(body, content["mimeType"])
            content["file"] = self._content_file_name(
                len(self._entries) + 1, content["mimeType"]
            )
        else:
            content["size"] = 0
        entry = {
            "startedDateTime": request_info["startedDateTime"],
            "time": max(0, (time.time() - request_info["started"]) * 1000),
            "request": {
                "method": request_info["request"].get("method", "GET"),
                "url": request_info["request"].get("url", ""),
                "httpVersion": "HTTP/1.1",
                "headers": self._headers(request_info["request"].get("headers", {})),
                "queryString": [
                    {"name": name, "value": value}
                    for name, value in parse_qsl(
                        urlsplit(request_info["request"].get("url", "")).query,
                        keep_blank_values=True,
                    )
                ],
                "cookies": [],
                "headersSize": -1,
                "bodySize": request_info["request"].get("encodedDataLength", -1),
            },
            "response": {
                "status": response.get("status", 0),
                "statusText": response.get("statusText", ""),
                "httpVersion": response.get("protocol", "h2"),
                "headers": self._headers(response.get("headers", {})),
                "cookies": [],
                "content": content,
                "redirectURL": response.get("redirectURL", ""),
                "headersSize": -1,
                "bodySize": response.get("encodedDataLength", -1),
            },
            "cache": {},
            "timings": {"send": 0, "wait": round(max(0, (time.time() - request_info["started"]) * 1000), 2), "receive": 0},
        }
        self._entries.append(entry)
        self._write_har()
        self.response_captured.emit(len(self._entries))

    @staticmethod
    def _headers(headers):
        return [{"name": str(key), "value": str(value)} for key, value in headers.items()]

    def _get_body(self, socket, request_id):
        command_id = self._next_command_id
        self._next_command_id += 1
        socket.send(json.dumps({
            "id": command_id,
            "method": "Network.getResponseBody",
            "params": {"requestId": request_id},
        }))
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                message = json.loads(socket.recv())
            except Exception:
                # El socket usa un timeout corto para poder detener la captura.
                # Un timeout aquí no significa que getResponseBody haya fallado.
                continue
            if message.get("id") != command_id:
                # getResponseBody shares the CDP socket with network events.
                # Dropping these events loses concurrent requests on busy pages.
                self._pending_messages.append(message)
                continue
            result = message.get("result", {})
            body = result.get("body", "")
            if result.get("base64Encoded"):
                try:
                    return base64.b64decode(body), True
                except Exception:
                    return body.encode("utf-8"), True
            return body.encode("utf-8"), False
        return b"", False

    def _content_file_name(self, index, mime_type):
        mime = mime_type.split(";", 1)[0].strip().lower()
        extension = {
            "application/javascript": ".js",
            "application/json": ".json",
            "application/manifest+json": ".webmanifest",
            "application/pdf": ".pdf",
            "application/wasm": ".wasm",
            "image/svg+xml": ".svg",
            "image/vnd.microsoft.icon": ".ico",
            "text/css": ".css",
            "text/html": ".html",
            "text/javascript": ".js",
            "text/plain": ".txt",
            "text/xml": ".xml",
        }.get(mime) or mimetypes.guess_extension(mime) or ".bin"
        extension = re.sub(r"[^a-z0-9.]+", "", extension.lower())
        return f"response-{index:06d}{extension}"

    def _save_content(self, body, mime_type):
        file_name = self._content_file_name(len(self._entries) + 1, mime_type)
        self._content_dir.mkdir(parents=True, exist_ok=True)
        content_path = self._content_dir / file_name
        content_path.write_bytes(body)
        return len(body)

    def _write_har(self):
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        entries = copy.deepcopy(self._entries)
        content_prefix = self._content_dir.name
        for entry in entries:
            content = entry["response"].get("content", {})
            if content.get("file"):
                content["file"] = f"{content_prefix}/{content['file']}"

        document = {
            "log": {
                "version": "1.2",
                "creator": {"name": "WebAgent", "version": "4.0"},
                "pages": [{
                    "startedDateTime": datetime.fromtimestamp(
                        self._started_at, timezone.utc
                    ).isoformat(),
                    "id": "page_1",
                    "title": self.page_url,
                    "pageTimings": {},
                }],
                "entries": entries,
            }
        }
        payload = json.dumps(document, ensure_ascii=False, indent=2)
        try:
            self._replace_with_temporary(payload)
        except PermissionError:
            if not self.output_path.exists():
                raise
            fallback = self._available_output_path()
            self.output_path = fallback
            self.output_path_changed.emit(str(fallback))
            self._replace_with_temporary(payload)

    def _replace_with_temporary(self, payload):
        """Escribe el documento usando un temporal único para evitar bloqueos stale."""
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.output_path.stem}-",
            suffix=".tmp",
            dir=self.output_path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.output_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _available_output_path(self):
        """Busca un nombre alternativo si el archivo elegido está bloqueado."""
        index = 1
        while True:
            candidate = self.output_path.with_name(
                f"{self.output_path.stem} ({index}){self.output_path.suffix}"
            )
            if not candidate.exists():
                return candidate
            index += 1
