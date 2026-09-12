#!/usr/bin/env python3
"""
Toggle autorun settings in the per-folder codex_config.json used by the
AIAgents dialog. This allows enabling/disabling a local command to run after
an agent finishes (e.g., running tests) to detect regressions before
presenting results to the agent — reducing token usage for iterative fixes.

Usage:
  python scripts/toggle_autorun.py <folder> --enable --command "pytest -q"
  python scripts/toggle_autorun.py <folder> --disable

This edits ~/.ia_browser/codex_config.json directly.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.paths import IA_DATA_DIR
from agents.ai_manager import validate_autorun_command

CONFIG_PATH = IA_DATA_DIR / "codex_config.json"


def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def set_autorun(folder: str, enabled: bool, command: str | None = None):
    if enabled:
        valid, error = validate_autorun_command(command or "")
        if not valid:
            raise ValueError(error)
    cfg = load_config()
    key = str(Path(folder))
    entry = cfg.get(key, {})
    entry.setdefault("agents", {})
    entry.setdefault("autorun", {"enabled": False, "command": ""})
    entry["autorun"]["enabled"] = bool(enabled)
    if command is not None:
        entry["autorun"]["command"] = command
    cfg[key] = entry
    save_config(cfg)
    print(f"Set autorun for {key}: enabled={enabled}, command={entry['autorun']['command']}")


def usage():
    print(__doc__)


def main():
    if len(sys.argv) < 3:
        usage(); sys.exit(1)
    folder = sys.argv[1]
    args = sys.argv[2:]
    enable = "--enable" in args
    disable = "--disable" in args
    if enable and disable:
        print("Specify either --enable or --disable")
        sys.exit(1)
    if enable:
        # find --command value if present
        cmd = None
        if "--command" in args:
            i = args.index("--command")
            if i + 1 < len(args):
                cmd = args[i+1]
        try:
            set_autorun(folder, True, cmd)
        except ValueError as exc:
            print(f"Invalid autorun command: {exc}", file=sys.stderr)
            sys.exit(1)
    elif disable:
        set_autorun(folder, False, None)
    else:
        usage()

if __name__ == "__main__":
    main()
