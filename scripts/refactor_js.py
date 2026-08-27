#!/usr/bin/env python3
"""
Wrapper to run jscodeshift transforms (JS/TS). Requires Node.js and npx.

Usage:
  python scripts/refactor_js.py <project_root> --transform <path/to/transform.js> [--extensions js,ts,tsx] [--parser babylon|ts]

This minimizes token use by running local codemods (jscodeshift) instead of
asking the agent to edit many files manually.
"""
import sys
import shutil
import subprocess
from pathlib import Path


def usage():
    print(__doc__)


def main():
    if len(sys.argv) < 3:
        usage(); sys.exit(1)
    root = sys.argv[1]
    args = sys.argv[2:]
    if shutil.which("node") is None and shutil.which("npx") is None:
        print("Node.js and npx are required. Install Node.js (includes npx).")
        sys.exit(2)
    cmd = ["npx", "jscodeshift"] + args + [root]
    print("Running:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print("jscodeshift failed with code", e.returncode)
        sys.exit(e.returncode)


if __name__ == '__main__':
    main()
