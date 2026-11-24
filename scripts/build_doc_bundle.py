"""Build a context bundle from the committed documentation at HEAD."""

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINTS = ("AGENTS.md", "docs/INDEX.md", "docs/ETAT.md")


def git(*args):
    return subprocess.check_output(["git", "--no-pager", *args], cwd=ROOT).decode("utf-8")


def build():
    sha = git("rev-parse", "HEAD").strip()
    date = git("show", "-s", "--format=%cs", sha).strip()
    reference = git("ls-tree", "-r", "--name-only", "-z", sha, "docs/reference/").split("\0")
    files = [*ENTRY_POINTS, *sorted(path for path in reference if path.endswith(".md"))]
    parts = [f"# Polypbase\n\nSource : `{sha}` ({date}) — contenu généré, ne pas éditer."]
    for path in files:
        parts.append(f"<!-- {path} -->\n\n{git('show', f'{sha}:{path}').strip()}")
    return "\n\n---\n\n".join(parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("polypbase-contexte.md"))
    args = parser.parse_args()
    args.output.write_text(build(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
