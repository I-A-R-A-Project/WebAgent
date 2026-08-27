#!/usr/bin/env python3
"""
Wrapper to run Rust refactors: cargo fmt and cargo fix.

Usage:
  python scripts/refactor_rust.py <project_root> [--fix]

This runs cargo fmt and optionally cargo fix to apply automated fixes.
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
    fix = "--fix" in sys.argv[2:]
    if shutil.which("cargo") is None:
        print("cargo not found. Install Rust (rustup) to use this script.")
        sys.exit(2)
    try:
        print("Running: cargo fmt")
        subprocess.run(["cargo", "fmt"], cwd=root, check=True)
        if fix:
            print("Running: cargo fix --allow-dirty --allow-staged")
            subprocess.run(["cargo", "fix", "--allow-dirty", "--allow-staged"], cwd=root, check=True)
    except subprocess.CalledProcessError as e:
        print("Rust refactor failed with code", e.returncode)
        sys.exit(e.returncode)

if __name__ == '__main__':
    main()
