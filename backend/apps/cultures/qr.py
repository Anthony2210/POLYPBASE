"""QR code helpers for boxes.

A box QR code simply encodes a stable public URL:

    {PUBLIC_BASE_URL}/bac/<id>/

When a biologist scans it, the phone browser opens that URL, which redirects
to the box detail page. The QR target is intentionally a short, permanent
alias decoupled from the internal page route, so printed labels never break
even if the detail URL changes later.
"""
from __future__ import annotations

import io

import qrcode
from django.conf import settings
from django.urls import reverse
from qrcode.image.svg import SvgPathImage

from .models import Box, IdentificationTag


def box_scan_url(box: Box) -> str:
    """Return the absolute URL encoded in the box QR code."""
    return f"{settings.PUBLIC_BASE_URL}/bac/{box.id}/"


def box_qr_image_url(box: Box) -> str:
    """Return the absolute URL of the box QR code SVG image."""
    return f"{settings.PUBLIC_BASE_URL}{reverse('qr_boite', args=[box.id])}"


def render_qr_svg(data: str, *, box_size: int = 10, border: int = 2) -> bytes:
    """Render ``data`` as a self-contained SVG QR code (vector, printable)."""
    return _build_qr(data, box_size=box_size, border=border, factory=SvgPathImage)


def render_qr_png(data: str, *, box_size: int = 10, border: int = 2) -> bytes:
    """Render ``data`` as a PNG QR code (needs Pillow)."""
    return _build_qr(data, box_size=box_size, border=border, factory=None)


def _build_qr(data: str, *, box_size: int, border: int, factory) -> bytes:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(image_factory=factory)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue()


def sync_box_qr_tag(box: Box) -> IdentificationTag:
    """Create or refresh the QR :class:`IdentificationTag` for a box.

    The tag stores the URL the printed code points to so it can be listed in
    the admin and the API without recomputing it.
    """
    tag, _ = IdentificationTag.objects.update_or_create(
        box=box,
        tag_type=IdentificationTag.TagType.QR,
        defaults={"code": f"box:{box.id}", "url": box_scan_url(box)},
    )
    return tag
