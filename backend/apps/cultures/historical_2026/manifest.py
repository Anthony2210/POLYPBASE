"""Fingerprinting, serialization and validation of the reviewed manifest."""

import hashlib
import json
from pathlib import Path

from . import decisions

DEFAULT_MANIFEST_PATH = Path(__file__).with_name("manifest_2026.json")
_LIST_KEYS = ("skipped_sheets", "boxes", "measurements", "excluded")


class ManifestError(Exception):
    """The manifest is missing, altered or not the expected one."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_fingerprint(manifest):
    body = {key: value for key, value in manifest.items() if key != "fingerprint"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def with_fingerprint(manifest):
    sealed = {key: value for key, value in manifest.items() if key != "fingerprint"}
    sealed["fingerprint"] = compute_fingerprint(sealed)
    return sealed


def dumps(manifest):
    """Stable, review-friendly layout: one record per line, sorted keys."""
    lines = ["{"]
    keys = sorted(manifest)
    for index, key in enumerate(keys):
        comma = "," if index < len(keys) - 1 else ""
        value = manifest[key]
        if key in _LIST_KEYS:
            lines.append(f'  "{key}": [')
            for position, item in enumerate(value):
                tail = "," if position < len(value) - 1 else ""
                lines.append("    " + _canonical(item) + tail)
            lines.append(f"  ]{comma}")
        else:
            lines.append(f'  "{key}": ' + _canonical(value) + comma)
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_manifest(manifest, path=DEFAULT_MANIFEST_PATH):
    Path(path).write_text(dumps(with_fingerprint(manifest)), encoding="utf-8", newline="\n")


def load_manifest(path=DEFAULT_MANIFEST_PATH, *, expected_fingerprint=None):
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ManifestError(f"Manifest cannot be read: {error}") from error
    stored = manifest.get("fingerprint")
    if stored != compute_fingerprint(manifest):
        raise ManifestError("Manifest fingerprint does not match its content.")
    if expected_fingerprint is not None and stored != expected_fingerprint:
        raise ManifestError(
            f"Manifest fingerprint {stored} differs from the expected {expected_fingerprint}."
        )
    if (manifest.get("schema"), manifest.get("schema_version")) != (
        decisions.SCHEMA,
        decisions.SCHEMA_VERSION,
    ):
        raise ManifestError("Unsupported manifest schema or version.")
    if manifest["source"]["sha256"] != decisions.EXPECTED_WORKBOOK_SHA256:
        raise ManifestError("Manifest was not generated from the expected workbook.")
    if manifest["conventions"].get("iso_year") != decisions.ISO_YEAR:
        raise ManifestError("Manifest ISO year is not the expected one.")
    for item in manifest["measurements"]:
        if item["strobila"] is not None:
            raise ManifestError("A manifest measurement carries a strobila value.")
    return manifest
