#!/usr/bin/env python3
"""
Run Prettier (JS/TS/HTML/CSS) via npx to apply formatting and small structural
changes (where supported). Requires Node.js and npx.

Usage:
  python scripts/refactor_prettier.py <project_root> [--patterns "src/**/*.js"]
"""
import sys
import shutil
import subprocess
from pathlib import Path


def usage():
    print(__doc__)


def main():
    if len(sys.argv) < 2:
        usage(); sys.exit(1)
    root = sys.argv[1]
    patterns = []
    if "--patterns" in sys.argv:
        i = sys.argv.index("--patterns")
        if i + 1 < len(sys.argv):
            patterns = sys.argv[i+1].split(";")
    if shutil.which("node") is None and shutil.which("npx") is None:
        print("Node.js and npx are required. Install Node.js (includes npx).")
        sys.exit(2)
    cmd = ["npx", "prettier", "--write"] + (patterns if patterns else ["."])
    print("Running:", " ".join(cmd))
    try:
        subprocess.run(cmd, cwd=root, check=True)
    except subprocess.CalledProcessError as e:
        print("prettier failed with code", e.returncode)
        sys.exit(e.returncode)

if __name__ == '__main__':
    main()
