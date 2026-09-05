"""Punto de entrada de WebAgent."""

import sys

from PyQt6.QtWidgets import QApplication

from window import IABrowser


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("WebAgent")
    app.setApplicationVersion("4.0")

    window = IABrowser()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
