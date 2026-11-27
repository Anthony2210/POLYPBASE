"""Select CI checks from the Git trees tested by GitHub Actions."""

import os
from pathlib import Path
import re
import subprocess
import sys


CHECKS = ("frontend", "backend", "backend_postgres", "deploy_scripts")
SHA = re.compile(r"[0-9a-f]{40}")


def classify(paths):
    selected = set()
    for path in paths:
        if path.startswith(b".github/workflows/") or path == b".gitattributes":
            return set(CHECKS)
        if path.startswith(b"frontend/"):
            selected.add("frontend")
        if path == b"scripts/pr_tools.ps1":
            selected.add("deploy_scripts")
        elif path.startswith(b"scripts/"):
            selected.update(("backend", "backend_postgres"))
        if path.startswith(b"deploy/"):
            selected.add("deploy_scripts")
        if path.startswith(b"backend/") or path in (b"pyproject.toml", b"uv.lock"):
            selected.update(("backend", "backend_postgres", "deploy_scripts"))
    return selected


def git(*args):
    return subprocess.run(
        ["git", "--no-pager", *args],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout


def commit_sha(value):
    if not SHA.fullmatch(value) or value == "0" * 40:
        raise ValueError("missing or invalid event commit")
    return value


def changed_paths(env):
    head = commit_sha(env.get("GITHUB_SHA", ""))
    if git("rev-parse", "HEAD").strip() != head.encode("ascii"):
        raise ValueError("checkout does not match the event commit")
    base = commit_sha(env.get("PR_BASE_SHA", ""))
    pr_head = commit_sha(env.get("PR_HEAD_SHA", ""))
    # Compare the merge result we actually test to its base, not a moving ref.
    parents = git("rev-list", "--parents", "-n", "1", head).split()
    if parents != [sha.encode("ascii") for sha in (head, base, pr_head)]:
        raise ValueError("checkout is not the expected PR merge commit")

    # Keep filenames as bytes and treat renames as deletion + addition so both
    # sides of a move can trigger their checks. No filenames enter the output.
    return git(
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        f"{base}^{{commit}}",
        f"{head}^{{commit}}",
        "--",
    ).split(b"\0")


def select_checks(env):
    if env.get("GITHUB_EVENT_NAME") != "pull_request":
        # A main push must provide a complete CI result for the release commit.
        return set(CHECKS)
    try:
        return classify(changed_paths(env))
    except (ValueError, OSError, subprocess.SubprocessError):
        print("CI diff unavailable or unverified; running all checks.", file=sys.stderr)
        return set(CHECKS)


def main():
    selected = select_checks(os.environ)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        for check in CHECKS:
            print(f"{check}={'true' if check in selected else 'false'}", file=output)


if __name__ == "__main__":
    main()
