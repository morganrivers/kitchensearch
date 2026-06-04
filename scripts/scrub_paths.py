#!/usr/bin/env python3
"""
Scrub build-machine paths from a Nuitka dist.

Walks every file under the given directory and replaces leaked build-machine
path prefixes (/home/<build-user>/...) with same-length neutral strings.

Equal byte length is mandatory: changing file size would shift ELF section
offsets and break .so loading. Each replacement is validated at startup.

Usage:
    python scrub_paths.py <dist_directory>
"""

import pathlib
import sys

REPLACEMENTS = [
    (b"/home/dmrivers",  b"/opt/anonymous"),
    (b"/home/conda",     b"/var/conda_"),
    (b"/home/runner",    b"/var/runner_"),
    (b"/home/guido",     b"/srv/guido_"),
    (b"/home/user",      b"/srv/user_"),
]


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: scrub_paths.py <dist_directory>")

    for old, new in REPLACEMENTS:
        assert len(old) == len(new), f"length mismatch: {old!r} vs {new!r}"

    root = pathlib.Path(sys.argv[1])
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")

    totals = {old: 0 for old, _ in REPLACEMENTS}
    files_touched = 0

    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        # Only touch shared libraries. The main Nuitka binary checksums its
        # embedded constants blob, so any byte modification (even same-length)
        # produces 'Error, corrupted constants object' at startup.
        name = path.name
        if not (name.endswith(".so") or ".so." in name):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue

        new_data = data
        for old, new in REPLACEMENTS:
            n = new_data.count(old)
            if n:
                new_data = new_data.replace(old, new)
                totals[old] += n

        if new_data != data:
            assert len(new_data) == len(data)
            path.write_bytes(new_data)
            files_touched += 1

    print(f"scrubbed {files_touched} files")
    for old, new in REPLACEMENTS:
        print(f"  {old.decode():<16} -> {new.decode():<16} {totals[old]:>6} occurrences")


if __name__ == "__main__":
    main()
