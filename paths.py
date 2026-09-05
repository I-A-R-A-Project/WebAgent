"""Centralized WebAgent user-data paths."""

from web_common.paths import app_data_dir


IA_DATA_DIR = app_data_dir("WebAgent")
BROWSER_DATA_DIR = app_data_dir("MiniBrowser")
COPILOT_PROFILES_DIR = IA_DATA_DIR / "copilot_profiles"
