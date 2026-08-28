#!/usr/bin/env python3
"""
Wrapper for ast-grep structural search and rewrite.

Requires the `ast-grep`/`sg` CLI:
  npm install --global @ast-grep/cli
  pip install ast-grep-cli
  cargo install ast-grep --locked

Examples:
  python scripts/refactor_ast_grep.py . --pattern "console.log($$$ARGS)" --lang ts
  python scripts/refactor_ast_grep.py . --pattern "var $A = $B" --rewrite "let $A = $B" --lang js

Use --dry-run to inspect matches without changing files. The wrapper delegates
all matching semantics to ast-grep and keeps output compact for agent usage.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Buscar o reescribir código con ast-grep.")
    parser.add_argument("folder", type=Path, help="Carpeta o archivo a inspeccionar")
    parser.add_argument("--pattern", "-p", required=True, help="Patrón AST de ast-grep")
    parser.add_argument("--rewrite", "-r", help="Reemplazo AST; si se omite solo busca")
    parser.add_argument("--lang", "-l", required=True, help="Lenguaje, por ejemplo js, ts, py o rust")
    parser.add_argument("--dry-run", action="store_true", help="No modificar archivos")
    args = parser.parse_args()

    executable = shutil.which("ast-grep") or shutil.which("sg")
    if executable is None:
        print(
            "ast-grep no encontrado. Instalar con "
            "`npm install --global @ast-grep/cli`, `pip install ast-grep-cli` "
            "o `cargo install ast-grep --locked`.",
            file=sys.stderr,
        )
        return 2

    if not args.folder.exists():
        print(f"La ruta no existe: {args.folder}", file=sys.stderr)
        return 1

    command = [
        executable,
        "--pattern",
        args.pattern,
        "--lang",
        args.lang,
    ]
    # ast-grep prints matches/rewrite results by default; --update-all is only
    # added for explicit in-place rewrites. Dry-run remains the safe default.
    if args.rewrite and not args.dry_run:
        command.append("--rewrite")
        command.append(args.rewrite)
        command.append("--update-all")
    command.append(str(args.folder))

    print("Running:", " ".join(command))
    try:
        completed = subprocess.run(command, check=False)
    except OSError as exc:
        print(f"No se pudo ejecutar ast-grep: {exc}", file=sys.stderr)
        return 1
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
