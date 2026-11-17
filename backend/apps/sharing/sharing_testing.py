"""Synthetic three-institution fixture shared by the sharing test modules."""

from datetime import date, timedelta
import re
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.cultures.models import Box, BoxLineage
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

# Lifetimes are test inputs only; no product default exists.
SHARING_SETTINGS = {
    "EMAIL_DELIVERY_ENABLED": True,
    "BOX_SHARING_INVITATION_MAX_LIFETIME_SECONDS": 7 * 86400,
    "BOX_SHARING_ACCESS_MAX_LIFETIME_SECONDS": 90 * 86400,
}
BASE_SELECTORS = ["source.institution_name"]
OBSERVATION_SELECTORS = [
    "source.institution_name", "observation.time", "observation.polyp_count",
    "observation.ephyrae_count", "observation.strobila_count",
]
TOKEN_PATTERN = re.compile(r"#token=([A-Za-z0-9_-]{43})")
PASSWORD = "synthetic-Passw0rd!"


def build_world():
    User = get_user_model()
    w = SimpleNamespace()
    w.source = Organization.objects.create(name="Synthetic source lab", slug="share-source")
    w.recipient_org = Organization.objects.create(name="Synthetic recipient lab", slug="share-recipient")
    w.third = Organization.objects.create(name="Synthetic third lab", slug="share-third")

    def user(name, organization=None, role=None, **extra):
        account = User.objects.create_user(
            username=f"share-{name}", email=f"{name}@example.test", password=PASSWORD, **extra,
        )
        if organization is not None:
            OrganizationMembership.objects.create(user=account, organization=organization, role=role)
        return account

    Role = OrganizationMembership.Role
    w.admin = user("admin", w.source, Role.ADMIN)
    w.second_admin = user("second-admin", w.source, Role.ADMIN)
    w.technician = user("technician", w.source, Role.LAB_TECHNICIAN)
    w.viewer = user("viewer", w.source, Role.VIEWER)
    w.recipient = user("recipient", w.recipient_org, Role.ADMIN)
    w.third_admin = user("third-admin", w.third, Role.ADMIN)
    # An account with no membership anywhere.
    w.loner = user("loner")
    w.superuser = user("root", is_superuser=True, is_staff=True)

    w.species = Species.objects.create(scientific_name="Aurelia synthetica")
    w.strain = Strain.objects.create(species=w.species, organization=w.source, code="SHR-AAA-1")
    w.box = Box.objects.create(
        organization=w.source, strain=w.strain, global_code="SHR-AAA-1.001", box_number="1",
    )
    w.sibling = Box.objects.create(
        organization=w.source, strain=w.strain, global_code="SHR-AAA-1.777", box_number="777",
    )
    BoxLineage.objects.create(parent_box=w.box, child_box=w.sibling)
    w.third_strain = Strain.objects.create(species=w.species, organization=w.third, code="THD-AAA-1")
    w.third_box = Box.objects.create(
        organization=w.third, strain=w.third_strain, global_code="THD-AAA-1.001", box_number="1",
    )
    w.zero = BiologicalMeasurement.objects.create(
        box=w.box, measured_on=date(2026, 4, 15), polyp_count=0, ephyrae_count=0, strobila_count=None,
        notes="private synthetic note",
    )
    BiologicalMeasurement.objects.create(
        box=w.sibling, measured_on=date(2026, 4, 15), polyp_count=999, ephyrae_count=998,
    )
    BiologicalMeasurement.objects.create(
        box=w.third_box, measured_on=date(2026, 4, 15), polyp_count=555, ephyrae_count=554,
    )
    return w


def client_for(user=None, organization=None, *, enforce_csrf=False):
    client = APIClient(enforce_csrf_checks=enforce_csrf)
    if user is not None:
        client.force_login(user)
    if organization is not None:
        client.credentials(HTTP_X_ORGANIZATION_ID=str(organization.pk))
    return client


def in_days(days):
    return (timezone.now() + timedelta(days=days)).isoformat()


def create_payload(w, **overrides):
    payload = {
        "box_id": w.box.pk,
        "recipient_email": w.recipient.email,
        "mode": "live",
        "selectors": OBSERVATION_SELECTORS,
        "invitation_expires_at": in_days(2),
        "access_expires_at": in_days(30),
    }
    payload.update(overrides)
    return payload


def last_token():
    """Secret of the most recently emailed invitation."""
    return TOKEN_PATTERN.search(mail.outbox[-1].body).group(1)


def create_share(w, **overrides):
    """Create through the API as the source Admin; return (share data, token)."""
    response = client_for(w.admin, w.source).post(
        "/api/admin/box-shares/", create_payload(w, **overrides), format="json",
    )
    assert response.status_code == 201, response.content
    return response.json()["share"], last_token()


def accept(user, token):
    return client_for(user).post("/api/shared-boxes/invitations/accept/", {"token": token}, format="json")


def create_accepted_share(w, user=None, **overrides):
    """Return (admin share data, reader public id) of an accepted share."""
    share, token = create_share(w, **overrides)
    response = accept(user or w.recipient, token)
    assert response.status_code == 200, response.content
    return share, response.json()["share"]["id"]
