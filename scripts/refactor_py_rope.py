#!/usr/bin/env python3
"""
Simple wrapper to run rope refactorings for Python projects.
Usage:
  python scripts/refactor_py_rope.py <project_root> rename <old_name> <new_name>
  python scripts/refactor_py_rope.py <project_root> move <source_file> <dest_file>

This script requires `rope` (pip install rope). It attempts to perform a
project-wide rename or move and prints a short summary. The intention is to
provide a low-token way for the agent to call a local refactoring tool instead
of asking the agent to manually edit many files.
"""
import sys
from pathlib import Path

try:
    from rope.base import project
    from rope.refactor.rename import Rename
    from rope.refactor.move import create_move
except Exception:
    print("rope is not installed. Install with: pip install rope")
    sys.exit(2)


def rename_symbol(project_root: str, old: str, new: str) -> None:
    proj = project.Project(project_root)
    try:
        ren = Rename(proj, proj.get_resource(old))
    except Exception:
        # Fallback: try to run Rename on a python module/symbol via project root
        # rope usually needs a resource; a robust approach requires analysis.
        print("Rename via resource lookup failed; rope rename by symbol name may require an IDE integration.")
        proj.close()
        return
    changes = ren.get_changes(new)
    proj.do(changes)
    proj.close()
    print(f"Renamed resource {old} -> {new}")


def move_file(project_root: str, src: str, dst: str) -> None:
    proj = project.Project(project_root)
    src_res = proj.get_resource(src)
    dst_parent = proj.get_resource(str(Path(dst).parent))
    if not src_res.exists():
        print(f"Source not found: {src}")
        proj.close()
        return
    move_refactor = create_move(proj, src_res)
    try:
        changes = move_refactor.get_changes(str(dst_parent))
        proj.do(changes)
        print(f"Moved {src} -> {dst}")
    except Exception as e:
        print("Move failed:", e)
    finally:
        proj.close()


def usage():
    print(__doc__)


def main():
    if len(sys.argv) < 4:
        usage(); sys.exit(1)
    root = sys.argv[1]
    cmd = sys.argv[2]
    if cmd == "rename" and len(sys.argv) == 5:
        rename_symbol(root, sys.argv[3], sys.argv[4])
    elif cmd == "move" and len(sys.argv) == 5:
        move_file(root, sys.argv[3], sys.argv[4])
    else:
        usage()


if __name__ == "__main__":
    main()
