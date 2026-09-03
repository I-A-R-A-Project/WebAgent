"""Compatibilidad para imports antiguos de la página WebEngine de IA."""

from web_common.tabs import UnifiedWebEnginePage


class CustomWebEnginePage(UnifiedWebEnginePage):
    """Adaptador legado sobre la implementación compartida."""

    def __init__(self, profile, parent=None, folder_view_handler=None):
        super().__init__(
            profile,
            parent,
            folder_view_handler=folder_view_handler,
        )
