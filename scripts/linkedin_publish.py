"""Publica texto en LinkedIn usando la interfaz del navegador vía CDP.

El navegador debe estar abierto con una sesión de LinkedIn iniciada y CDP
habilitado en el puerto 9222. No se llama a la API de LinkedIn directamente.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FEED_URL = "https://www.linkedin.com/feed/?shareActive=true"


@dataclass
class Step:
    name: str
    detail: str
    ok: bool


class Browser:
    def __init__(self, port: int = 9222):
        self.port = port
        self.socket = None
        self.command_id = 0

    def connect(self) -> None:
        import websocket

        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/json/list", timeout=3
        ) as response:
            targets = json.load(response)
        target = next(
            (
                item
                for item in targets
                if item.get("type") == "page"
                and "linkedin.com" in item.get("url", "")
                and item.get("webSocketDebuggerUrl")
            ),
            None,
        )
        if target is None:
            raise RuntimeError(
                "No hay una pestaña de LinkedIn disponible. "
                "Abrí LinkedIn en WebAgent con CDP habilitado."
            )
        self.socket = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=10
        )

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self.socket is None:
            raise RuntimeError("El navegador no está conectado.")
        self.command_id += 1
        self.socket.send(
            json.dumps(
                {"id": self.command_id, "method": method, "params": params or {}}
            )
        )
        while True:
            message = json.loads(self.socket.recv())
            if message.get("id") == self.command_id:
                if "error" in message:
                    raise RuntimeError(message["error"].get("message", "Error CDP"))
                return message.get("result", {})

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        exception = result.get("exceptionDetails")
        if exception:
            raise RuntimeError(exception.get("text", "Error JavaScript"))
        return result.get("result", {}).get("value")

    def close(self) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None


def _find_button_script(labels: list[str]) -> str:
    encoded = json.dumps([label.casefold() for label in labels])
    return f"""
        (() => {{
          const labels = {encoded};
          const visible = el => !!(el.offsetWidth || el.offsetHeight);
          const candidates = [];
          const collect = root => {{
            const descendants = [...root.querySelectorAll('*')];
            candidates.push(...descendants);
            descendants.forEach(item => {{
              if (item.shadowRoot) collect(item.shadowRoot);
            }});
          }};
          collect(document);
          const editor = candidates.find(el =>
            el.matches(
              '.ql-editor, [contenteditable], [role="textbox"], textarea, input[type="text"]'
            ) &&
            visible(el) &&
            !el.hasAttribute('readonly') &&
            !el.disabled &&
            el.getAttribute('aria-hidden') !== 'true'
          );
          if (editor) return true;
          const button = candidates.find(el => {{
            if (!el.matches('button, [role="button"]')) return false;
            const text = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
            return visible(el) && labels.some(label => text === label || text.includes(label));
          }});
          if (!button) return false;
          button.click();
          return true;
        }})()
    """


def _wait_for(browser: Browser, script: str, timeout: float = 20) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = browser.evaluate(script)
        if value:
            return value
        time.sleep(0.25)
    raise RuntimeError("Se agotó el tiempo esperando la interfaz de LinkedIn.")


def publish(
    text: str,
    *,
    port: int = 9222,
    capture: Path | None = None,
    dry_run: bool = False,
) -> list[Step]:
    if not text.strip():
        raise ValueError("El texto de la publicación no puede estar vacío.")
    steps = [
        Step("open_sharebox", "Abrir el cuadro de nueva publicación", False),
        Step("write_text", "Escribir el texto en el editor", False),
        Step("publish", "Pulsar el botón de publicación", False),
    ]
    if dry_run:
        if capture:
            capture.write_text(
                json.dumps(
                    {
                        "mode": "browser-ui",
                        "url": FEED_URL,
                        "steps": [step.__dict__ for step in steps],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        return steps

    browser = Browser(port)
    try:
        browser.connect()
        browser.call("Page.navigate", {"url": FEED_URL})
        _wait_for(
            browser,
            "document.readyState === 'complete' || document.readyState === 'interactive'",
        )
        _wait_for(
            browser,
            _find_button_script(
                [
                    "start a post",
                    "iniciar una publicación",
                    "crear publicación",
                    "crear una publicación",
                ]
            ),
        )
        steps[0] = Step(steps[0].name, steps[0].detail, True)

        editor_script = """
            (() => {
              const elements = [];
              const collect = root => {
                const descendants = [...root.querySelectorAll('*')];
                elements.push(...descendants);
                descendants.forEach(item => {
                  if (item.shadowRoot) collect(item.shadowRoot);
                });
              };
              collect(document);
              const editor = elements.filter(item => item.matches(
                '.ql-editor, [contenteditable], [role="textbox"], textarea, input[type="text"]'
              )).find(item =>
                (item.offsetWidth || item.offsetHeight) &&
                !item.hasAttribute('readonly') &&
                !item.disabled &&
                item.getAttribute('aria-hidden') !== 'true'
              );
              if (!editor) return false;
              editor.focus();
              return true;
            })()
        """
        _wait_for(browser, editor_script)
        browser.call("Input.insertText", {"text": text})
        steps[1] = Step(steps[1].name, steps[1].detail, True)
        _wait_for(browser, _find_button_script(["post", "publicar"]))
        steps[2] = Step(steps[2].name, steps[2].detail, True)
    finally:
        browser.close()
    if capture:
        capture.write_text(
            json.dumps(
                {
                    "mode": "browser-ui",
                    "url": FEED_URL,
                    "steps": [step.__dict__ for step in steps],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True, help="Texto de la publicación.")
    parser.add_argument("--port", type=int, default=9222, help="Puerto CDP de WebAgent.")
    parser.add_argument("--capture", type=Path, help="Captura JSON de los pasos.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        steps = publish(
            args.text, port=args.port, capture=args.capture, dry_run=args.dry_run
        )
        print(json.dumps([step.__dict__ for step in steps], ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
