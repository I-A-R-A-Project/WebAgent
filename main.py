"""Punto de entrada de IA Browser."""

import sys

from PyQt6.QtWidgets import QApplication

from window import IABrowser


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("IA Browser")
    app.setApplicationVersion("4.0")

    window = IABrowser()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
