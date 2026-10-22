#!/usr/bin/env python3
"""Verify this source-only repository's integrity; never run scientific code."""
import hashlib
from pathlib import Path, PurePosixPath
import sys


def main():
    root = Path(__file__).resolve().parent
    manifest = root / 'SHA256SUMS.txt'
    checked = set()
    failures = []
    for line in manifest.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        expected, relative = line.split(maxsplit=1)
        relative = relative.removeprefix('*')
        name = PurePosixPath(relative)
        if name.is_absolute() or '..' in name.parts or relative in checked:
            failures.append('Invalid or duplicate path: ' + relative)
            continue
        checked.add(relative)
        path = root / relative
        if path.is_symlink() or not path.is_file():
            failures.append('Missing or nonregular file: ' + relative)
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            failures.append('Hash mismatch: ' + relative)
    for message in failures:
        print(message, file=sys.stderr)
    if failures:
        print(f'FAILED: {len(failures)} problem(s).', file=sys.stderr)
        return 1
    print(f'PASS: {len(checked)} listed files match SHA256SUMS.txt.')
    print('Integrity check only; no analyses or jobs were run.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
