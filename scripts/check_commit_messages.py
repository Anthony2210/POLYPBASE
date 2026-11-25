"""Check the title and non-merge commit subjects of a pull request."""

import json
import os
import re
import sys
from pathlib import Path

from ci_changes import commit_sha, git

TYPES = "feat fix perf refactor test docs ci build chore revert".split()
SCOPES = "boxes measurements probes zones species dna sharing accounts exports admin ui i18n deploy backup ci docs".split()
HEADLINE = re.compile(
    rf"(?:{'|'.join(TYPES)})(?:\((?:{'|'.join(SCOPES)})\))?: (\S(?:[^\r\n]*\S)?)"
)
DEPENDABOT = "dependabot[bot]"
DEPENDABOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"


def valid_headline(value):
    match = HEADLINE.fullmatch(value)
    return bool(
        match and len(value) <= 72 and value.isprintable() and not value.endswith(".")
    )


def check_pull_request(pull_request):
    base = commit_sha(pull_request["base"]["sha"])
    head = commit_sha(pull_request["head"]["sha"])
    errors = []
    if pull_request["user"]["login"] != DEPENDABOT and not valid_headline(
        pull_request["title"]
    ):
        errors.append("PR title does not match the commit headline format.")

    fields = (
        git(
            "log",
            "--no-merges",
            "--format=%H%x00%an%x00%ae%x00%s",
            "-z",
            f"{base}..{head}",
            "--",
        )
        .decode("utf-8", errors="replace")
        .split("\0")
    )
    if fields.pop() or len(fields) % 4:
        raise ValueError("Unexpected git log output")
    for oid, author, email, subject in zip(
        fields[::4], fields[1::4], fields[2::4], fields[3::4]
    ):
        if (author, email.lower()) == (DEPENDABOT, DEPENDABOT_EMAIL):
            continue
        if not valid_headline(subject):
            errors.append(
                f"Commit {commit_sha(oid)[:12]} does not match the commit headline format."
            )
    return errors


def main():
    event = json.loads(
        Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")
    )
    errors = check_pull_request(event["pull_request"])
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(
            "Use type(scope): Imperative English subject (maximum 72 characters, no final period).",
            file=sys.stderr,
        )
        return 1
    print("PR title and commit subjects follow the required format.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
