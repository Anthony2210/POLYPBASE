"""Regenerate the reviewed 2026 manifest from the authoritative workbook."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.cultures.historical_2026 import manifest as manifest_module
from apps.cultures.historical_2026.source import SourceError, build_manifest


class Command(BaseCommand):
    help = (
        "Build the deterministic 2026 import manifest from the authoritative workbook. "
        "Without --write it only verifies the committed manifest is identical."
    )

    def add_arguments(self, parser):
        parser.add_argument("--workbook", required=True, help="Path to Suivi_2026_actualisé.xlsx.")
        parser.add_argument("--output", default=str(manifest_module.DEFAULT_MANIFEST_PATH))
        parser.add_argument("--write", action="store_true", help="Overwrite the manifest file.")

    def handle(self, *args, **options):
        try:
            built = manifest_module.with_fingerprint(build_manifest(options["workbook"]))
        except (SourceError, OSError) as error:
            raise CommandError(str(error)) from error
        rendered = manifest_module.dumps(built)
        output = Path(options["output"])
        self.stdout.write(f"fingerprint: {built['fingerprint']}")
        self.stdout.write(f"counts: {built['counts']}")
        if options["write"]:
            output.write_text(rendered, encoding="utf-8", newline="\n")
            self.stdout.write(self.style.SUCCESS(f"Manifest written to {output}."))
            return
        current = output.read_text(encoding="utf-8") if output.exists() else None
        if current != rendered:
            raise CommandError("The committed manifest differs from the regenerated one.")
        self.stdout.write(self.style.SUCCESS("The committed manifest is reproducible."))
