"""Punto de entrada de WebAgent."""

import sys
from pathlib import Path


def _ensure_local_imports():
    """Asegura que el repo y su directorio hermano `web_common` queden en sys.path.

    WebAgent espera que `web_common` viva al lado del repositorio actual (por
    ejemplo: `.../IARA/web_common` junto a `.../IARA/WebAgent`). Si se ejecuta
    desde un checkout anidado o con una estructura distinta, esta comprobación
    deja la ruta correcta antes de importar el resto del proyecto.
    """
    repo_root = Path(__file__).resolve().parent.parent
    for base_dir in (repo_root.parent, repo_root):
        if (base_dir / "web_common").is_dir():
            sys.path.insert(0, str(base_dir))
            return


_ensure_local_imports()

from PyQt6.QtWidgets import QApplication

try:
    from app.window import IABrowser
except ModuleNotFoundError as exc:
    if exc.name == "web_common" or "web_common" in str(exc):
        print(
            "WebAgent requires the sibling 'web_common' package to be present next to "
            "this repo. Place both folders under the same parent directory and run again.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    raise


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("WebAgent")
    app.setApplicationVersion("4.0")

    window = IABrowser()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
