"""Read-only Ruff and Biome checks for source changes from a PR base to HEAD."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_EXTENSIONS = {
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".css",
    ".html",
}
LINE_LIMIT = 500
DUPLICATE_LINES = 12


def git_changes(root, base):
    return subprocess.run(
        [
            "git",
            "--no-pager",
            "diff",
            "--raw",
            "-z",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            "--diff-filter=AMT",
            f"{base}^{{commit}}",
            "HEAD",
            "--",
        ],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        timeout=60,
    ).stdout


def source_paths(raw_diff):
    """Use destination modes; symlinks and submodules are not sources."""
    fields = raw_diff.split(b"\0")
    python, frontend = [], []
    for metadata, name in zip(fields[:-1:2], fields[1::2], strict=True):
        if metadata.split()[1] not in (b"100644", b"100755"):
            continue
        path = PurePosixPath(os.fsdecode(name))
        if path.suffix == ".py":
            python.append(path)
        elif (
            len(path.parts) > 1
            and path.parts[0] == "frontend"
            and path.parts[1] not in {"dist", "node_modules"}
            and path.suffix in FRONTEND_EXTENSIONS
        ):
            frontend.append(path)
    return sorted(python), sorted(frontend)


def warn(root, paths):
    """Flag physical length and exact repeated blocks, ignoring indent and blank lines."""
    seen = {}
    for path in paths:
        try:
            lines = (root / path).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            print(f"Warning: could not read {str(path)!r} for advisory checks.")
            continue
        if len(lines) > LINE_LIMIT:
            print(
                f"Warning: {str(path)!r} has {len(lines)} lines (limit: {LINE_LIMIT})."
            )

        content = [(n, line.strip()) for n, line in enumerate(lines, 1) if line.strip()]
        reported_duplicate = False
        for i in range(len(content) - DUPLICATE_LINES + 1):
            block = tuple(text for _, text in content[i : i + DUPLICATE_LINES])
            first_path, first_index, first_line = seen.setdefault(
                (path.suffix, block), (path, i, content[i][0])
            )
            if not reported_duplicate and (
                first_path != path or i - first_index >= DUPLICATE_LINES
            ):
                print(
                    f"Warning: duplicate block ({DUPLICATE_LINES} nonblank lines): "
                    f"{str(first_path)!r}:{first_line} and {str(path)!r}:{content[i][0]}."
                )
                reported_duplicate = True


def batches(paths):
    # Stay comfortably below Windows' command-line limit, including quoting.
    batch, size = [], 0
    for path in paths:
        arg = f"./{path}"
        if batch and size + len(arg) + 4 > 8_000:
            yield batch
            batch, size = [], 0
        batch.append(arg)
        size += len(arg) + 4
    if batch:
        yield batch


def run_checks(root, python, frontend):
    ruff = [sys.executable, "-m", "ruff"]
    commands = [
        ([*ruff, "check", "--config", "pyproject.toml", "--no-cache"], root, python),
        (
            [*ruff, "format", "--check", "--config", "pyproject.toml", "--no-cache"],
            root,
            python,
        ),
        (
            [
                "node",
                "node_modules/@biomejs/biome/bin/biome",
                "check",
                "--config-path=biome.json",
                "--error-on-warnings",
            ],
            root / "frontend",
            [path.relative_to("frontend") for path in frontend],
        ),
    ]
    failed = False
    for command, cwd, paths in commands:
        for batch in batches(paths):
            result = subprocess.run([*command, "--", *batch], cwd=cwd, timeout=120)
            failed |= result.returncode != 0
    return int(failed)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Full SHA of the PR base commit")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.base):
        parser.error("--base must be a full Git commit SHA")

    try:
        python, frontend = source_paths(git_changes(ROOT, args.base))
        if not (python or frontend):
            print("No changed Python or frontend source files.")
            return 0
        for path in [*python, *frontend]:
            file = ROOT / path
            if not file.is_file() or file.resolve() != file:
                raise ValueError(f"Expected a regular checkout file: {str(path)!r}")
        print(
            f"Checking {len(python)} Python and {len(frontend)} frontend files.",
            flush=True,
        )
        warn(ROOT, [*python, *frontend])
        sys.stdout.flush()
        return run_checks(ROOT, python, frontend)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Changed-source quality check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
