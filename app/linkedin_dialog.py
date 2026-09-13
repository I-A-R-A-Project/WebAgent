"""Diálogo para iniciar una publicación de LinkedIn desde una pestaña."""

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QTextEdit,
)


class LinkedInPublishDialog(QDialog):
    """Recoge el texto y el perfil aislado que usará LinkedIn."""

    def __init__(self, profiles, current_profile_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Publicar en LinkedIn")
        self.setMinimumWidth(520)

        layout = QFormLayout(self)
        self.profile_selector = QComboBox()
        for profile in profiles:
            self.profile_selector.addItem(profile["name"], profile["id"])
        current_index = self.profile_selector.findData(current_profile_id)
        if current_index >= 0:
            self.profile_selector.setCurrentIndex(current_index)
        layout.addRow("Perfil del navegador:", self.profile_selector)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Escribí el texto de la publicación...")
        self.text_edit.setAcceptRichText(False)
        self.text_edit.setMinimumHeight(140)
        layout.addRow("Texto:", self.text_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Abrir y publicar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self):
        return self.profile_selector.currentData(), self.text_edit.toPlainText().strip()
