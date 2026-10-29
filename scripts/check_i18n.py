"""Read-only backend FR/EN gate; no Django imports, database, or generation.

Discovery includes backend .py and .html files. It deliberately excludes directories
named migrations, tests or __pycache__, and modules named tests, test_* or tests_*:
fixtures and historical migration labels are not production translation sources.
Only literal gettext/gettext_lazy calls through actual Django imports, and literal
template trans/translate tags (optionally `as name`), are supported. Dynamic calls,
indirection, shadowed callables, plural/context APIs and block tags fail closed.
"""
from __future__ import annotations

import ast
import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

try:
    from . import compile_django_messages as compiler
except ImportError:
    import compile_django_messages as compiler

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("fr", "en")
ADMIN_INVITATIONS = {
    "fr": "Merci de contacter un administrateur.",
    "en": "Please contact your administrator.",
}
# These are authorization refusals, not a lexical classification of validation.
PERMISSION_MSGIDS = (
    "This account cannot create subculture events.",
    "The parent box is outside the active organization.",
)


class ExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class CoverageException:
    language: str
    msgid: str
    # Repeated paths represent repeated occurrences, never line numbers.
    sources: tuple[str, ...]
    reason: str
    decision_ref: str = "B02-scope-deferred"

    @property
    def decision_identity(self) -> tuple[str, str, str, str]:
        return ("django", self.msgid, self.language.upper(),
                f"backend/locale/{self.language}/LC_MESSAGES/django.po")


@dataclass(frozen=True)
class RegisterEvent:
    status: str
    decision_ref: str


def _deferred(language, msgid, *sources):
    if msgid in ("A merged identity is read-only.",
                 "The culture identity changed. Reload the culture.",
                 "This code is reserved by a historical culture identity."):
        reason = IDENTITY_GUARD
    elif msgid.startswith("Duplicate measurement evidence"):
        reason = EVIDENCE_GUARD
    elif msgid in ("Use YYYY-MM-DD format.", "End must be on or after start.",
                   "The date range must not exceed 366 days, inclusive."):
        reason = DATE_FR
    elif msgid == "Lecture seule":
        reason = ROLE
    elif msgid == "Provenance non établie":
        reason = PROVENANCE
    elif msgid == "Armoire":
        reason = CABINET
    elif msgid == "À vérifier":
        reason = PENDING_FR
    elif msgid == "Non précisé":
        reason = "Active record metadata choice; retain the exact source fallback without harmonizing absence labels."
    elif msgid in ("Repiquage", "Repiquage à prévoir"):
        reason = "Lineage/observation choice; no separate Django Transplant wording approved in B02."
    elif any(path.endswith("/forms.py") for path in sources):
        reason = LEGACY_FORM
    else:
        reason = LEGACY_CHOICE
    return CoverageException(language, msgid, tuple(sources), reason)


LEGACY_CHOICE = "Legacy model choice label; no new FR/EN wording or identity entry approved in B02."
LEGACY_FORM = "Legacy measurement form label; no runtime importer found in B01; wording remains deferred."
IDENTITY_GUARD = "Culture identity integrity guard; localized wording is unapproved; retain source fallback."
EVIDENCE_GUARD = "Duplicate record evidence integrity guard; localized wording is unapproved; retain source fallback."
DATE_FR = "Date validation guard; French wording remains unresolved; only EN identity entries approved in B02."
ROLE = "Active role_label API/UI path; Django alignment with the product role terminology requires a separate decision."
PROVENANCE = "Movement provenance label; equivalence to the frontend's contextual exception remains unresolved."
CABINET = "Zone choice exposed by DRF metadata; B01 T033 deferred Django alignment separately from the frontend location label."
PENDING_FR = "Pending box model choice; EN Pending was approved in B01, but no explicit FR identity entry approved in B02."


C = "backend/apps/cultures/models.py"
M = "backend/apps/measurements/models.py"
F = "backend/apps/measurements/forms.py"
A = "backend/apps/accounts/models.py"
D = "backend/apps/measurements/admin.py"
S = "backend/apps/cultures/serializers.py"
# Explicit reviewed inventory, not derived from whatever happens to be missing.
COVERAGE_EXCEPTIONS = (
    _deferred("fr", "A merged identity is read-only.", C),
    _deferred("fr", "Annulé", C),
    _deferred("fr", "Armoire", C),
    _deferred("fr", "Commentaire", F, F),
    _deferred("fr", "Duplicate measurement evidence cannot be deleted.", D, M, M),
    _deferred("fr", "Duplicate measurement evidence is read-only.", M),
    _deferred("fr", "Déplacement", C),
    _deferred("fr", "Emplacement à la qualification", C),
    _deferred("fr", "End must be on or after start.", S),
    _deferred("fr", "Générale", M),
    _deferred("fr", "Import historique", C, C),
    _deferred("fr", "Inactive", C),
    _deferred("fr", "Lecture seule", A),
    _deferred("fr", "Mortalité", M),
    _deferred("fr", "Non précisé", M),
    _deferred("fr", "Polypes", F),
    _deferred("fr", "Premier emplacement", C),
    _deferred("fr", "Provenance non établie", C),
    _deferred("fr", "Prévu", C),
    _deferred("fr", "Repiquage", C, C),
    _deferred("fr", "Repiquage à prévoir", M),
    _deferred("fr", "Reproduction sexuée", C, C),
    _deferred("fr", "Réactivation", C),
    _deferred("fr", "Strobiles", F),
    _deferred("fr", "Terminé", C),
    _deferred("fr", "The culture identity changed. Reload the culture.", C, C),
    _deferred("fr", "The date range must not exceed 366 days, inclusive.", S),
    _deferred("fr", "This code is reserved by a historical culture identity.", C,
                  "backend/apps/cultures/box_codes.py"),
    _deferred("fr", "Transfert", C),
    _deferred("fr", "Use YYYY-MM-DD format.", S),
    _deferred("fr", "Vigilance", F),
    _deferred("fr", "À vérifier", C),
    _deferred("fr", "Éphyrules", F),
    _deferred("en", "A merged identity is read-only.", C),
    _deferred("en", "Commentaire", F, F),
    _deferred("en", "Duplicate measurement evidence cannot be deleted.", D, M, M),
    _deferred("en", "Déplacement", C),
    _deferred("en", "Emplacement à la qualification", C),
    _deferred("en", "Inactive", C),
    _deferred("en", "Polypes", F),
    _deferred("en", "Premier emplacement", C),
    _deferred("en", "Provenance non établie", C),
    _deferred("en", "Réactivation", C),
    _deferred("en", "Strobiles", F),
    _deferred("en", "The culture identity changed. Reload the culture.", C, C),
    _deferred("en", "This code is reserved by a historical culture identity.", C,
                  "backend/apps/cultures/box_codes.py"),
    _deferred("en", "Transfert", C),
    _deferred("en", "Vigilance", F),
    _deferred("en", "Éphyrules", F),
)


def extract_python(text: str, filename: str) -> list[str]:
    tree = ast.parse(text, filename=filename)
    aliases = {}

    def bind(name, target):
        previous = aliases.get(name, target)
        if previous != target and any(
                value == "django" or value.startswith("django.utils")
                for value in (previous, target)):
            raise ExtractionError(f"{filename}: conflicting translation import {name}")
        aliases[name] = target

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*" and node.module.startswith("django.utils"):
                    raise ExtractionError(f"{filename}: wildcard translation import")
                bind(alias.asname or alias.name, f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bind(alias.asname or alias.name.split('.')[0],
                     alias.name if alias.asname else alias.name.split('.')[0])

    def resolve(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{resolve(node.value)}.{node.attr}"
        return ""

    def translation_function(node):
        name = resolve(node)
        return name.startswith("django.utils.translation.") and (
            "gettext" in name.rsplit('.', 1)[-1])

    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    protected = {name for name, target in aliases.items()
                 if target == "django" or target.startswith("django.utils")}

    def scope(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                 ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                return node
        return tree

    bindings = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bindings[scope(node)].add(node.id)
        elif isinstance(node, ast.arg):
            bindings[scope(node)].add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings[scope(node)].add(node.name)

    def shadowed(node):
        base = node
        while isinstance(base, ast.Attribute):
            base = base.value
        if not isinstance(base, ast.Name):
            return False
        owner = scope(node)
        while owner is not tree:
            if base.id in bindings[owner]:
                return True
            owner = scope(owner)
        return base.id in bindings[tree]

    messages = []
    for node in ast.walk(tree):
        if ((isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
             and node.id in protected and scope(node) is tree)
                or (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                    and resolve(node).startswith("django.utils.translation"))):
            raise ExtractionError(f"{filename}:{node.lineno}: rebound translation import")
        if not isinstance(node, (ast.Name, ast.Attribute)):
            continue
        parent = parents.get(node)
        if resolve(node) == "django.utils.translation" and not shadowed(node):
            if not isinstance(parent, ast.Attribute) or parent.value is not node:
                raise ExtractionError(f"{filename}:{node.lineno}: indirect translation namespace")
            if parent.attr.startswith("__"):
                raise ExtractionError(f"{filename}:{node.lineno}: dynamic translation namespace")
        if not translation_function(node):
            continue
        if shadowed(node):
            if isinstance(parent, ast.Call) and parent.func is node:
                raise ExtractionError(f"{filename}:{node.lineno}: shadowed translation callable")
            continue
        if not isinstance(parent, ast.Call) or parent.func is not node:
            raise ExtractionError(f"{filename}:{node.lineno}: indirect translation reference")
        if resolve(node) not in ("django.utils.translation.gettext", "django.utils.translation.gettext_lazy"):
            raise ExtractionError(f"{filename}:{node.lineno}: unsupported gettext function {resolve(node)}")
        if (len(parent.args) != 1 or parent.keywords
                or not isinstance(parent.args[0], ast.Constant)
                or not isinstance(parent.args[0].value, str)):
            raise ExtractionError(f"{filename}:{node.lineno}: gettext requires one literal string")
        messages.append(parent.args[0].value)
    return messages


def extract_template(text: str, filename: str) -> list[str]:
    blank = lambda match: "\n" * match.group().count("\n")
    text = re.sub(r"{#.*?#}|{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", blank, text, flags=re.S)
    messages = []
    for match in re.finditer(r"{%\s*(.*?)\s*%}|{{(.*?)}}", text, re.S):
        tag = match.group(1)
        location = f"{filename}:{text.count(chr(10), 0, match.start()) + 1}"
        body = tag if tag is not None else match.group(2)
        if re.search(r"_\s*\(", body):
            raise ExtractionError(f"{location}: unsupported inline template gettext")
        if tag is None:
            continue
        command = tag.split()[0] if tag.split() else ""
        if command in ("blocktrans", "blocktranslate"):
            raise ExtractionError(f"{location}: unsupported {command}")
        if command not in ("trans", "translate"):
            continue
        literal = re.fullmatch(r'''(?:trans|translate)\s+("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')(?:\s+as\s+[A-Za-z_]\w*)?''', tag, re.S)
        if not literal:
            raise ExtractionError(f"{location}: unsupported translation tag: {tag}")
        quoted = literal.group(1)
        # Django template literals only unescape the matching quote and backslash.
        messages.append(quoted[1:-1].replace("\\" + quoted[0], quoted[0]).replace("\\\\", "\\"))
    # A malformed/unclosed translation tag must not disappear from discovery.
    remainder = re.sub(r"{%.*?%}|{{.*?}}", blank, text, flags=re.S)
    unclosed = re.search(r"{%\s*(?:trans|translate|blocktrans|blocktranslate)\b", remainder)
    if unclosed:
        line = remainder.count("\n", 0, unclosed.start()) + 1
        raise ExtractionError(f"{filename}:{line}: unclosed translation tag")
    return messages


def discover_sources(root: Path) -> dict[str, tuple[str, ...]]:
    found = defaultdict(list)
    for path in sorted((root / "backend").rglob("*")):
        relative = path.relative_to(root)
        if (not path.is_file() or path.suffix not in (".py", ".html")
                or any(part in ("migrations", "tests", "__pycache__") for part in relative.parts)
                or path.stem == "tests" or path.stem.startswith(("test_", "tests_"))):
            continue
        extract = extract_python if path.suffix == ".py" else extract_template
        for msgid in extract(path.read_text(encoding="utf-8-sig"), relative.as_posix()):
            found[msgid].append(relative.as_posix())
    return {msgid: tuple(paths) for msgid, paths in sorted(found.items())}


@dataclass
class Report:
    total: int
    raw_missing: dict[str, list[str]] = field(default_factory=dict)
    exempted: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def check_catalogs(sources, catalogs, *, exceptions=(), decision_events=None,
                   permission_msgids=PERMISSION_MSGIDS) -> Report:
    """decision_events maps exact register identities to their latest RegisterEvent."""
    if decision_events is None:
        decision_events = {}
    report = Report(total=len(sources))
    valid_exceptions = {}
    for exception in exceptions:
        key = (exception.language, exception.msgid)
        problems = []
        if key in valid_exceptions:
            problems.append("duplicate exception")
        if exception.language not in LANGUAGES:
            problems.append("unsupported language")
        if not exception.reason.strip():
            problems.append("missing exception reason")
        event = decision_events.get(exception.decision_identity)
        if (event is None or event.status != "DEFERRED" or not exception.decision_ref
                or event.decision_ref != exception.decision_ref):
            problems.append("latest exact register event must be DEFERRED with matching decision_ref")
        if (not exception.sources or exception.msgid not in sources
                or Counter(exception.sources) != Counter(sources.get(exception.msgid, ()))):
            problems.append("source occurrence scope drift")
        if exception.msgid in catalogs.get(exception.language, {}):
            problems.append("stale exception: catalog entry now exists")
        if problems:
            report.errors.append(f"{key}: {'; '.join(problems)}")
        else:
            valid_exceptions[key] = exception

    for language in LANGUAGES:
        messages = catalogs.get(language, {})
        missing = [msgid for msgid in sources if msgid not in messages]
        report.raw_missing[language] = missing
        report.exempted[language] = [msgid for msgid in missing if (language, msgid) in valid_exceptions]
        for msgid in missing:
            if (language, msgid) not in valid_exceptions:
                report.errors.append(f"{language}: missing translation {msgid!r}: {sources[msgid]}")
        for msgid, value in messages.items():
            if not msgid:  # PO metadata, not a user-facing translation.
                continue
            for label, text in (("msgid", msgid), ("msgstr", value)):
                if not text.strip() or text != text.strip():
                    report.errors.append(f"{language}: empty/edge whitespace {label} for {msgid!r}")
                if "·" in text:
                    report.errors.append(f"{language}: middle dot in {label} for {msgid!r}")
            try:
                if compiler.placeholder_signature(msgid) != compiler.placeholder_signature(value):
                    report.errors.append(f"{language}: placeholder mismatch for {msgid!r}")
            except ValueError as error:
                report.errors.append(f"{language}: invalid placeholders for {msgid!r}: {error}")
        for msgid in permission_msgids:
            if messages.get(msgid, "").count(ADMIN_INVITATIONS[language]) != 1:
                report.errors.append(f"{language}: permission invitation must occur once for {msgid!r}")
    # Validate source strings even where coverage is explicitly deferred.
    for msgid in sources:
        if not msgid.strip() or msgid != msgid.strip() or "·" in msgid:
            report.errors.append(f"source: empty/edge whitespace/middle dot in {msgid!r}")
        try:
            compiler.placeholder_signature(msgid)
        except ValueError as error:
            report.errors.append(f"source: invalid placeholders for {msgid!r}: {error}")
    return report


def load_register_events(root: Path) -> dict[tuple[str, str, str, str], RegisterEvent]:
    """Read append-only register events; the last row for each exact identity wins.

    Keep all statuses until after latest-event selection: filtering DEFERRED first
    would incorrectly resurrect decisions superseded by APPLIED or other statuses.
    Language case, catalog path and surface are matched exactly, without aliases.
    """
    path = root / "docs/translation-review/application/register.csv"
    events = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"surface", "key_or_msgid", "language", "source_file", "status", "decision_ref"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("register is missing required identity/event columns")
        for row in reader:
            if any(row[column] is None for column in required):
                raise ValueError(f"register:{reader.line_num}: incomplete identity/event row")
            identity = (row["surface"], row["key_or_msgid"], row["language"], row["source_file"])
            events[identity] = RegisterEvent(row["status"], row["decision_ref"])
    return events


def check_tree(root: Path = ROOT) -> Report:
    try:
        sources = discover_sources(root)
    except (ValueError, SyntaxError, OSError) as error:
        return Report(0, errors=[f"source extraction: {error}"])
    catalogs = {}
    errors = []
    for language in LANGUAGES:
        po = root / "backend/locale" / language / "LC_MESSAGES/django.po"
        try:
            messages = compiler.parse_po_file(po)
            catalogs[language] = messages
            if po.with_suffix(".mo").read_bytes() != compiler.compile_messages(messages):
                errors.append(f"{language}: MO bytes differ from strict PO compilation")
        except (ValueError, OSError) as error:
            errors.append(f"{language}: catalog error: {error}")
    try:
        events = load_register_events(root)
    except (OSError, ValueError, KeyError, csv.Error) as error:
        events = {}
        errors.append(f"decision register: {error}")
    report = check_catalogs(sources, catalogs, exceptions=COVERAGE_EXCEPTIONS,
                            decision_events=events)
    report.errors[:0] = errors
    return report


def main() -> int:
    report = check_tree()
    print(f"Backend source inventory: {report.total} unique msgids")
    for language in LANGUAGES:
        missing = report.raw_missing.get(language, [])
        exempted = report.exempted.get(language, [])
        print(f"{language}: raw coverage {report.total - len(missing)}/{report.total}; "
              f"raw gaps {len(missing)}; exempted {len(exempted)}; "
              f"blocking coverage gaps {len(missing) - len(exempted)}")
    for error in report.errors:
        print(f"ERROR: {error}")
    print(f"Blocking errors: {len(report.errors)}")
    return int(bool(report.errors))


if __name__ == "__main__":
    raise SystemExit(main())
