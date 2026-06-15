"""Generate QR codes for boxes.

Each box QR code encodes the stable public URL ``{PUBLIC_BASE_URL}/bac/<id>/``.
Scanning it opens the box detail sheet in the biologist's browser.

The script reads boxes from Django and writes one image per box to a chosen
output folder. By default it writes to ``data/qr_codes/`` which is git-ignored,
so generated labels never land in the repository.

Examples (run from the repository root):

    uv run python scripts/generate_qr_codes.py
    uv run python scripts/generate_qr_codes.py --format png --sync-tags
    uv run python scripts/generate_qr_codes.py --output C:/tmp/labels --organization 1
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "qr_codes"


def _setup_django() -> None:
    """Make the Django backend importable and configure settings."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    import django

    django.setup()


def _slugify_code(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", code).strip("-") or "box"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate box QR codes.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output folder for the images (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--format",
        choices=["svg", "png"],
        default="svg",
        help="Image format (default: svg, vector and best for printing).",
    )
    parser.add_argument(
        "--organization",
        type=int,
        default=None,
        help="Only generate codes for boxes of this organization id.",
    )
    parser.add_argument(
        "--sync-tags",
        action="store_true",
        help="Also create or refresh the QR IdentificationTag for each box.",
    )
    args = parser.parse_args()

    _setup_django()

    from apps.cultures import qr
    from apps.cultures.models import Box

    boxes = Box.objects.select_related("organization").order_by("global_code")
    if args.organization is not None:
        boxes = boxes.filter(organization_id=args.organization)

    output_dir: Path = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    render = qr.render_qr_png if args.format == "png" else qr.render_qr_svg
    count = 0
    for box in boxes:
        data = qr.box_scan_url(box)
        image = render(data)
        filename = f"bac-{box.id}-{_slugify_code(box.global_code)}.{args.format}"
        (output_dir / filename).write_bytes(image)
        if args.sync_tags:
            qr.sync_box_qr_tag(box)
        count += 1
        print(f"{box.global_code} -> {filename} ({data})")

    print(f"\nGenerated {count} QR code(s) in {output_dir}")


if __name__ == "__main__":
    main()
