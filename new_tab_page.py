"""Página local de nueva pestaña con búsqueda y bandeja de tareas."""

import html
from urllib.parse import quote


def render_new_tab_page(tasks=None) -> str:
    tasks = tasks or []
    rows = "".join(
        f"<li><strong>{html.escape(item['text'])}</strong> "
        f"<small>{html.escape(item.get('status', 'pending'))}"
        f"{' · ' + html.escape(item['collection_name']) if item.get('collection_name') else ''}</small></li>"
        for item in tasks[-20:]
    ) or "<li class='empty'>Todavía no hay tareas.</li>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Nueva pestaña</title>
<style>body{{background:#202124;color:#e8eaed;font:16px Segoe UI;margin:0}}
.wrap{{max-width:760px;margin:8vh auto;padding:24px}}h1{{color:#8ab4f8}}
form{{margin:18px 0}}input{{width:100%;padding:14px;border-radius:24px;border:1px solid #5f6368;background:#292a2d;color:white;box-sizing:border-box}}
button{{margin-top:8px;padding:10px 18px;border:0;border-radius:18px;background:#8ab4f8;color:#202124}}
ul{{padding:0;list-style:none}}li{{padding:12px;border-bottom:1px solid #3c4043}}small{{color:#9aa0a6}}.empty{{color:#9aa0a6}}</style></head>
<body><main class="wrap"><h1>IA Browser</h1>
<form action="https://www.google.com/search" method="GET"><input name="q" autofocus placeholder="Buscar en Google o escribir una URL"></form>
<form action="ia://task" method="GET"><input name="text" placeholder="Agregar tarea para AnyAPI"><button type="submit">Agregar tarea</button></form>
<h2>Tareas</h2><ul>{rows}</ul></main></body></html>"""


def task_url(text: str) -> str:
    return f"ia://task?text={quote(text)}"
