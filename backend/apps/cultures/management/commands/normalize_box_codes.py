from django.core.management.base import BaseCommand
from django.db import transaction

from apps.audit.models import AuditLog
from apps.cultures.models import Box, IdentificationTag


class Command(BaseCommand):
    help = (
        "Normalize legacy box global codes to the strain-code format. "
        "Example: TAI-AFL-1.005 -> AFL-TAI-1.005 when the box strain is AFL-TAI-1."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the changes. Without this flag, only prints a dry run.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        candidates, skipped = self._build_candidates()

        if not candidates and not skipped:
            self.stdout.write(self.style.SUCCESS("Aucun code boîte à corriger."))
            return

        if skipped:
            self.stdout.write(self.style.WARNING("Codes ignorés :"))
            for reason, old_code, new_code in skipped:
                self.stdout.write(f"  - {old_code} -> {new_code or '-'} : {reason}")

        if not candidates:
            self.stdout.write(self.style.WARNING("Aucune correction applicable sans collision."))
            return

        self.stdout.write("Corrections prévues :" if not apply_changes else "Corrections appliquées :")
        for box, new_code in candidates:
            self.stdout.write(f"  - {box.global_code} -> {new_code}")

        if not apply_changes:
            self.stdout.write(self.style.WARNING("Simulation seulement. Relancer avec --apply pour appliquer."))
            return

        with transaction.atomic():
            for box, new_code in candidates:
                old_code = box.global_code
                box.global_code = new_code
                box.save(update_fields=["global_code"])

                AuditLog.objects.filter(object_type="box", object_id=old_code).update(object_id=new_code)
                self._update_qr_tag(old_code, new_code)

        self.stdout.write(self.style.SUCCESS(f"{len(candidates)} code(s) corrigé(s)."))

    def _build_candidates(self):
        boxes = Box.objects.select_related("strain").order_by("id")
        existing_codes = set(Box.objects.values_list("global_code", flat=True))
        planned_codes = set()
        candidates = []
        skipped = []

        for box in boxes:
            expected_prefix = f"{box.strain.code}."
            if box.global_code.startswith(expected_prefix):
                continue

            number = _extract_box_number(box.global_code)
            if not number:
                skipped.append(("numéro introuvable", box.global_code, None))
                continue

            new_code = f"{expected_prefix}{number}"
            if new_code in existing_codes or new_code in planned_codes:
                skipped.append(("collision avec un code existant", box.global_code, new_code))
                continue

            candidates.append((box, new_code))
            planned_codes.add(new_code)

        return candidates, skipped

    def _update_qr_tag(self, old_code, new_code):
        old_tag_code = f"QR-{old_code}"
        new_tag_code = f"QR-{new_code}"
        if IdentificationTag.objects.filter(code=new_tag_code).exists():
            return
        IdentificationTag.objects.filter(code=old_tag_code).update(code=new_tag_code)


def _extract_box_number(global_code):
    if "." not in global_code:
        return None
    suffix = global_code.rsplit(".", 1)[1]
    number = []
    for char in suffix:
        if not char.isdigit():
            break
        number.append(char)
    return "".join(number) or None
