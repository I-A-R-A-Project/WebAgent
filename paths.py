"""Centralized WebAgent user-data paths."""

import sys
from pathlib import Path


def _ensure_local_imports():
    repo_root = Path(__file__).resolve().parent
    for base_dir in (repo_root.parent, repo_root):
        if (base_dir / "web_common").is_dir():
            sys.path.insert(0, str(base_dir))
            return


_ensure_local_imports()

try:
    from web_common.paths import app_data_dir
except ModuleNotFoundError as exc:  # pragma: no cover - dependency layout check
    raise ModuleNotFoundError(
        "web_common not found. Keep WebAgent and web_common in the same parent directory "
        "so the shared browser components can be imported."
    ) from exc


IA_DATA_DIR = app_data_dir("WebAgent")
BROWSER_DATA_DIR = app_data_dir("MiniBrowser")
COPILOT_PROFILES_DIR = IA_DATA_DIR / "copilot_profiles"
TASK_AGENT_CONTEXT_DIR = IA_DATA_DIR / "task_agent_context"
