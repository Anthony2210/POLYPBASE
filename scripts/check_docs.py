"""Check repository documentation, Django settings and the model inventory."""

import argparse
import ast
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit
from unittest.mock import patch

from gen_api_doc import PREFLIGHT_ENV, ROOT, forbid_connection

LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\s*\)|^\s{0,3}\[[^\]\n]+\]:\s*(<[^>\n]+>|\S+)", re.M)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[ `~]*$", re.M | re.S)
INLINE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
ROOT_PATH = re.compile(r"(?:\.\.?/|(?:backend|frontend|docs|deploy|scripts|\.github|\.agents|\.claude)/)[\w./-]+(?:\.[\w.-]+|/)$")
CHURN = re.compile(r"\b(?:non committ[eé]|uncommitted|local only|travail local|worktree)\b|C:\\Users\\", re.I)
ENV_ENTRY = re.compile(r"^\s*(?:#\s*)?([A-Z][A-Z0-9_]*)\s*=", re.M)
SETTINGS = ("settings.py", "demo_settings.py", "demo.py")
IMPACT = re.compile(r"^(?:deploy/|backend/config/(?:.*settings\.py|(?:api_)?urls\.py)$|backend/apps/.+/(?:[^/]*models\.py|[^/]*api[^/]*\.py|urls\.py|migrations/)|frontend/src/components/AdminView\.tsx$)")


def read(path):
    return path.read_text(encoding="utf-8-sig")


def markdown_files(root):
    return sorted({*root.glob("*.md"), *(root / "docs").rglob("*.md"),
                   *(root / ".agents/skills").rglob("*.md"), *(root / ".claude/skills").rglob("*.md"),
                   *(root / "frontend/src/styles").glob("README.md")})


def anchors(text):
    seen = set()
    counters = Counter()
    for heading in re.findall(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", FENCE.sub("", text), re.M):
        label = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", heading)
        slug = re.sub(r"[^\w -]", "", label.lower()).replace(" ", "-")
        candidate = slug
        while candidate in seen:
            counters[slug] += 1
            candidate = f"{slug}-{counters[slug]}"
        seen.add(candidate)
    return seen | set(re.findall(r"<(?:a|h[1-6])\b[^>]*\b(?:id|name)=[\"']([^\"']+)", text))


def check_markdown(root):
    paths = markdown_files(root)
    texts = {path: read(path) for path in paths}
    index = root / "docs/INDEX.md"
    archive = root / "docs/archive"
    indexed = set()
    errors = []
    for path, raw in texts.items():
        relative = path.relative_to(root).as_posix()
        text = FENCE.sub("", raw)
        if not path.is_relative_to(archive) and (match := CHURN.search(text)):
            errors.append(f"{relative}: outdated status vocabulary: {match.group()!r}")
        for match in LINK.finditer(text):
            address = (match[1] or match[2]).strip("<>")
            parts = urlsplit(address)
            if parts.scheme or parts.netloc:
                continue
            target = (path.parent / unquote(parts.path)).resolve() if parts.path else path
            if not target.is_relative_to(root) or not target.exists():
                errors.append(f"{relative}: broken link: {address}")
                continue
            if target.suffix == ".md" and parts.fragment and unquote(parts.fragment) not in anchors(texts.get(target) or read(target)):
                errors.append(f"{relative}: missing anchor: {address}")
            if path == index:
                indexed.add(target)
        if path.is_relative_to(archive):
            continue  # Historical code citations can refer to deleted artifacts.
        for token in INLINE.findall(LINK.sub("", text)):
            if ROOT_PATH.fullmatch(token) and not ((root / token).exists() or (path.parent / token).exists()):
                errors.append(f"{relative}: unknown repository path: {token}")
    for path in paths:
        if path.is_relative_to(root / "docs") and not path.is_relative_to(archive) and path != index and path not in indexed:
            errors.append(f"docs/INDEX.md: orphan: {path.relative_to(root).as_posix()}")
    state = root / "docs/ETAT.md"
    if state not in texts:
        errors.append("docs/ETAT.md: missing project status")
    elif len(texts[state].splitlines()) > 100:
        errors.append("docs/ETAT.md: exceeds 100 lines")
    return errors


def settings_variables(root):
    def get_name(node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    names = {"PYTHON_DOTENV_DISABLED"}  # Consumed by python-dotenv, not os.getenv in settings.py.
    for filename in SETTINGS:
        tree = ast.parse(read(root / "backend/config" / filename))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.args:
                function = ast.unparse(node.func)
                if function in {"os.getenv", "os.environ.get", "env_flag", "_optional_positive_seconds", "env.get"}:
                    argument = node.args[0]
                elif function == "required" and len(node.args) > 1:
                    argument = node.args[1]
                else:
                    continue
                if name := get_name(argument):
                    names.add(name)
            elif isinstance(node, ast.Subscript) and ast.unparse(node.value) == "os.environ":
                if name := get_name(node.slice):
                    names.add(name)
    return names


def check_env(root):
    configured = Counter(ENV_ENTRY.findall(read(root / "backend/.env.example")))
    expected = settings_variables(root)
    return [f"backend/.env.example: {problem}: {name}"
            for problem, names in (("missing variable", expected - configured.keys()),
                                   ("unknown variable", configured.keys() - expected),
                                   ("duplicate variable", {key for key, count in configured.items() if count > 1}))
            for name in sorted(names)]


def check_models(root):
    with patch.dict(os.environ, PREFLIGHT_ENV, clear=True), patch.object(sys, "path", [str(root / "backend"), *sys.path]):
        import django
        from django.apps import apps
        from django.db.backends.base.base import BaseDatabaseWrapper

        with patch.object(BaseDatabaseWrapper, "connect", forbid_connection):
            django.setup()
        expected = {(model._meta.app_label, model.__name__) for model in apps.get_models()
                    if model.__module__.startswith("apps.") and not model._meta.proxy}
    path = root / "docs/reference/modele-donnees.md"
    if not path.is_file():
        return ["docs/reference/modele-donnees.md: missing model inventory"]
    doc = read(path).partition("\n## Modèles concrets du projet\n")[2].partition("\n## ")[0]
    present = set()
    for app, entries in re.findall(r"^\| `([a-z_]+)` \| (.+?) \|$", doc, re.M):
        present.update((app, model) for model in INLINE.findall(entries))
    return [f"docs/reference/modele-donnees.md: {problem}: {app}.{model}"
            for problem, models in (("missing model", expected - present), ("unknown model", present - expected))
            for app, model in sorted(models)]


def warn_impact(base):
    files = subprocess.check_output(["git", "--no-pager", "diff", "--name-only", "-z", f"{base}...HEAD", "--"], cwd=ROOT, text=True).split("\0")
    touched = any(path.startswith(("docs/reference/", "docs/operations/", "docs/utilisateur/")) or path == "docs/ETAT.md" for path in files)
    if not touched and any(IMPACT.match(path) for path in files):
        print("::warning title=Documentation impact::A PR changes models, routes, settings or deployment without updating the live documentation.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--impact-base", help="Only report documentation impact against this base commit")
    args = parser.parse_args()
    if args.impact_base:
        if not re.fullmatch(r"[a-f0-9]{40}", args.impact_base):
            parser.error("--impact-base must be a full Git commit SHA")
        warn_impact(args.impact_base)
        return 0
    errors = check_markdown(ROOT) + check_env(ROOT) + check_models(ROOT)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Documentation links, settings and model inventory are up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
