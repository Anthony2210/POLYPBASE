#!/usr/bin/env python3
"""Mirror .agents/skills into .claude/skills, or check the mirror without writes.

Files are copied and compared byte for byte, including line endings. Both trees
use the same Git checkout rules: CRLF or LF is fine as long as the copies match.
"""

import argparse
import shutil
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SOURCE = Path('.agents/skills')
MIRROR = Path('.claude/skills')


def reject_link(path: Path) -> None:
    if path.is_symlink() or path.is_junction():
        raise ValueError(f'{path.relative_to(REPO)}: links are not supported')


def inventory(root: Path, *, allow_missing: bool = False) -> dict[str, str]:
    # Check the parent too, so sync never follows a redirected .claude directory.
    reject_link(root.parent)
    reject_link(root)
    if allow_missing and not root.exists():
        return {}
    if not root.is_dir():
        raise ValueError(f'{root.relative_to(REPO)}: expected a directory')

    entries: dict[str, str] = {}

    def collect(directory: Path) -> None:
        for path in directory.iterdir():
            reject_link(path)
            name = path.relative_to(root).as_posix()
            if path.is_dir():
                entries[name] = 'directory'
                collect(path)
            elif path.is_file():
                entries[name] = 'file'
            else:
                raise ValueError(f'{path.relative_to(REPO)}: not a regular file or directory')

    collect(root)
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='report drift without changing files')
    args = parser.parse_args()

    source = REPO / SOURCE
    mirror = REPO / MIRROR
    try:
        expected = inventory(source)
        actual = inventory(mirror, allow_missing=True)
        missing = sorted(expected.keys() - actual.keys())
        extra = sorted(actual.keys() - expected.keys())
        wrong_type = []
        changed = []
        for name in sorted(expected.keys() & actual.keys()):
            if expected[name] != actual[name]:
                wrong_type.append(name)
            elif expected[name] == 'file' and (source / name).read_bytes() != (mirror / name).read_bytes():
                changed.append(name)

        if args.check:
            if not mirror.is_dir():
                print(f'Missing directory: {MIRROR.as_posix()}', file=sys.stderr)
            for label, names in (
                ('Missing', missing), ('Extra', extra), ('Wrong type', wrong_type), ('Different bytes', changed)
            ):
                for name in names:
                    print(f'{label}: {MIRROR.as_posix()}/{name}', file=sys.stderr)
            if not mirror.is_dir() or missing or extra or wrong_type or changed:
                print('Run python scripts/sync_agent_skills.py to update the mirror.', file=sys.stderr)
                return 1
            print('Agent skills mirror is up to date.')
            return 0

        # Remove stale entries before creating replacements, deepest paths first.
        for name in sorted(extra + wrong_type, key=lambda name: name.count('/'), reverse=True):
            path = mirror / name
            if actual[name] == 'directory':
                path.rmdir()
            else:
                path.unlink()
        mirror.mkdir(parents=True, exist_ok=True)
        for name in sorted(missing + wrong_type + changed):
            target = mirror / name
            if expected[name] == 'directory':
                target.mkdir()
            else:
                target.unlink(missing_ok=True)
                shutil.copy2(source / name, target)
    except (OSError, ValueError) as exc:
        print(f'Cannot sync agent skills: {exc}', file=sys.stderr)
        return 1

    print('Agent skills mirror synchronized.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
