"""Graba una ventana de Windows usando la captura gdigrab de FFmpeg."""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable


WINDOW_TITLE = "WebAgent"
ROOT_DIR = Path(__file__).resolve().parents[1]


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("Este script solo funciona en Windows.")


def _visible_windows() -> list[str]:
    """Devuelve los títulos de las ventanas visibles de nivel superior."""
    titles: list[str] = []
    user32 = ctypes.windll.user32
    enum_windows = user32.EnumWindows
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )
    enum_windows.argtypes = [callback_type, ctypes.c_void_p]
    enum_windows.restype = ctypes.c_bool

    @callback_type
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if title:
            titles.append(title)
        return True

    enum_windows(callback, 0)
    return titles


def _find_window_title(query: str) -> str:
    titles = _visible_windows()
    exact = next((title for title in titles if title.casefold() == query.casefold()), None)
    if exact:
        return exact
    partial = next((title for title in titles if query.casefold() in title.casefold()), None)
    if partial:
        return partial
    raise RuntimeError(
        f"No se encontró una ventana visible que coincida con {query!r}. "
        "Usa --list-windows para consultar los títulos disponibles."
    )


def _ffmpeg_path(value: str | None) -> str:
    if value:
        path = Path(value).expanduser()
        if not path.is_file():
            raise RuntimeError(f"No existe el ejecutable FFmpeg: {path}")
        return str(path)

    bundled = ROOT_DIR / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise RuntimeError(
        "No se encontró FFmpeg. Coloca ffmpeg.exe en la raíz de WebAgent "
        "o indica su ruta con --ffmpeg."
    )


def _default_output() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT_DIR / "recordings" / f"window_{timestamp}.mp4"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Graba una ventana visible de Windows en formato MP4."
    )
    parser.add_argument(
        "--title",
        default=WINDOW_TITLE,
        help=f"Texto exacto o parcial del título de la ventana (por defecto: {WINDOW_TITLE}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Archivo MP4 de salida (por defecto: recordings/window_YYYYMMDD_HHMMSS.mp4).",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="Cuadros por segundo, entre 1 y 120 (por defecto: 30).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="Duración máxima en segundos. Sin este parámetro, graba hasta Ctrl+C.",
    )
    parser.add_argument("--ffmpeg", help="Ruta alternativa al ejecutable ffmpeg.exe.")
    parser.add_argument(
        "--list-windows",
        action="store_true",
        help="Muestra las ventanas visibles y termina.",
    )
    return parser.parse_args()


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Solicita a FFmpeg que finalice y espere a que cierre el archivo."""
    if process.poll() is not None:
        return
    if process.stdin:
        try:
            process.stdin.write(b"q\n")
            process.stdin.flush()
            process.wait(timeout=5)
            return
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            pass
    process.terminate()
    process.wait(timeout=5)


def record_window(
    title: str,
    output: Path,
    fps: int,
    duration: float | None,
    ffmpeg: str,
    process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> int:
    window_title = _find_window_title(title)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-f",
        "gdigrab",
        "-framerate",
        str(fps),
        "-draw_mouse",
        "1",
        "-i",
        f"title={window_title}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
    ]
    if duration is not None:
        command.extend(["-t", str(duration)])
    command.extend(["-movflags", "+faststart", str(output)])

    print(f"Grabando «{window_title}» en {output}")
    print("Pulsa Ctrl+C para detener y guardar el archivo.")
    process = process_factory(command, stdin=subprocess.PIPE)
    try:
        return process.wait()
    except KeyboardInterrupt:
        print("\nDeteniendo grabación...")
        _stop_process(process)
        return process.returncode or 0


def main() -> int:
    args = _parse_args()
    try:
        _require_windows()
        if args.list_windows:
            for title in _visible_windows():
                print(title)
            return 0
        if not 1 <= args.fps <= 120:
            raise RuntimeError("--fps debe estar entre 1 y 120.")
        if args.duration is not None and args.duration <= 0:
            raise RuntimeError("--duration debe ser mayor que cero.")
        ffmpeg = _ffmpeg_path(args.ffmpeg)
        return record_window(
            args.title,
            args.output or _default_output(),
            args.fps,
            args.duration,
            ffmpeg,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
