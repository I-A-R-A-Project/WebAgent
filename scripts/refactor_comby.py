#!/usr/bin/env python3
"""
Wrapper for comby structural search-and-replace across languages.
Requires comby (https://comby.dev).

Usage:
  python scripts/refactor_comby.py <folder> '<match> => <rewrite>' -l <language> [--file-pattern '**/*.js']

Example:
  python scripts/refactor_comby.py . 'func $X($Y) { $B } => function $X($Y) { $B }' -l javascript --file-pattern "**/*.js"
"""
import sys
import shutil
import subprocess
from pathlib import Path


def usage():
    print(__doc__)


def main():
    if len(sys.argv) < 4:
        usage(); sys.exit(1)
    folder = sys.argv[1]
    rewrite = sys.argv[2]
    lang = None
    pattern = None
    args = sys.argv[3:]
    if '-l' in args:
        i = args.index('-l')
        if i+1 < len(args):
            lang = args[i+1]
    if '--file-pattern' in args:
        i = args.index('--file-pattern')
        if i+1 < len(args):
            pattern = args[i+1]
    if shutil.which('comby') is None:
        print('comby not found. Install comby: https://comby.dev')
        sys.exit(2)
    cmd = ['comby', rewrite, folder, '-l', lang or 'generic']
    if pattern:
        cmd += ['-f', pattern]
    print('Running:', ' '.join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print('comby failed with code', e.returncode)
        sys.exit(e.returncode)

if __name__ == '__main__':
    main()
