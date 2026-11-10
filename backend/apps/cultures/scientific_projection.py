"""Read-only projection of one source Box into the scientific snapshot contract.

Internal service only: no endpoint, envelope, file encoding or destination
effect. It checks that the actor may read the source institution's data; the
authority to disclose a snapshot outside the institution belongs to each
future workflow (sharing, file exchange, in-app reception) and is not decided
here. Reads are not wrapped in a snapshot transaction: a caller needing a
frozen, mutually consistent view of Box and observations must own that
transaction strategy and prove it on PostgreSQL.
"""

from dataclasses import dataclass

from rest_framework.exceptions import PermissionDenied

from apps.accounts.permissions import get_authorized_organizations
from apps.measurements.models import BiologicalMeasurement
from apps.measurements.scientific_snapshot import (
    IDENTITY_SELECTORS,
    OBSERVATION_PROVENANCE_SELECTORS,
    OBSERVATION_TIME,
    OBSERVATION_VALUE_SELECTORS,
    SOURCE_SELECTORS,
    Diagnostic,
    FieldValue,
    ObservationRecord,
    SnapshotError,
    build_snapshot,
    select_disclosure,
)
from apps.measurements.scientific_time import ObservationTime
from apps.taxonomy.scoping import eligible_strains

from .models import Box


@dataclass(frozen=True)
class ProjectionResult:
    snapshot: dict | None
    diagnostics: tuple = ()

    @property
    def ok(self):
        return self.snapshot is not None and not self.diagnostics


def _authorized_source(actor, source_organization):
    if (
        actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active
        or getattr(source_organization, "pk", None) is None
    ):
        raise PermissionDenied("Source organization access is required.")
    # Reuse the existing read rule (active membership, technical superuser);
    # never trust the passed instance's state.
    source = get_authorized_organizations(actor).filter(pk=source_organization.pk, is_active=True).first()
    if source is None:
        raise PermissionDenied("Source organization access is required.")
    return source


def project_box_snapshot(*, actor, source_organization, box_id, selectors, required_selectors=frozenset()):
    """Project one Box of the explicitly selected source institution.

    `selectors` may come from a client and is validated against the catalogue
    before any scientific read. `required_selectors` is a server-side workflow
    requirement. A foreign, merged or missing Box yields the same diagnostic.
    Only canonical measurements of that Box are read; duplicate evidence,
    other Boxes, lineage, accounts, notes and temperatures are never read.
    Strain, Species and GlobalStrainIdentity rows are read only when an
    identity selector is disclosed; the strain eligibility check runs then.
    """
    source = _authorized_source(actor, source_organization)
    try:
        disclosure = select_disclosure(selectors, required=required_selectors)
    except SnapshotError as error:
        return ProjectionResult(None, error.diagnostics)
    if type(box_id) is not int or box_id < 1:
        return ProjectionResult(None, (Diagnostic("box_reference_invalid", "box_id"),))

    # Mandatory scope read: institution, merge state and existence are decided
    # in the WHERE clause on cultures_box alone, whatever the disclosure.
    # Columns and identity joins are then limited to what is disclosed.
    boxes = Box.objects.filter(pk=box_id, organization_id=source.pk, merged_into__isnull=True)
    columns = ["id", "strain"]
    if "source.box_code" in disclosure:
        columns.append("global_code")
    if disclosure.any_of(IDENTITY_SELECTORS):
        boxes = boxes.select_related("strain__species", "strain__global_identity")
        columns += [
            "strain__code", "strain__species", "strain__species__scientific_name",
            "strain__global_identity", "strain__global_identity__global_id",
        ]
    box = boxes.only(*columns).first()
    if box is None:
        return ProjectionResult(None, (Diagnostic("box_not_found", "box_id"),))

    source_values = {}
    if "source.institution_portable_id" in disclosure:
        source_values["source.institution_portable_id"] = FieldValue.of(str(source.portable_id))
    if "source.institution_name" in disclosure:
        source_values["source.institution_name"] = FieldValue.of_text(source.name)
    if "source.box_code" in disclosure:
        source_values["source.box_code"] = FieldValue.of_text(box.global_code)

    identity_values = {}
    if disclosure.any_of(IDENTITY_SELECTORS):
        # A Strain only qualifies when eligible in the source institution;
        # never disclose another institution's strain reference.
        if not eligible_strains(source).filter(pk=box.strain_id).exists():
            return ProjectionResult(None, (Diagnostic("strain_not_eligible", "identity"),))
        strain = box.strain
        if "identity.species_scientific_name" in disclosure:
            identity_values["identity.species_scientific_name"] = FieldValue.of_text(strain.species.scientific_name)
        if "identity.strain_code" in disclosure:
            identity_values["identity.strain_code"] = FieldValue.of_text(strain.code)
        if "identity.global_strain_id" in disclosure:
            # NULL means not established; an identity is never created here.
            global_identity = strain.global_identity
            identity_values["identity.global_strain_id"] = FieldValue.of(
                None if global_identity is None else str(global_identity.global_id),
            )

    observations = None
    if OBSERVATION_TIME in disclosure:
        observations = []
        diagnostics = []
        for index, row in enumerate(_observation_rows(source, box, disclosure)):
            try:
                time = ObservationTime(row["temporal_precision"], row["measured_on"], row["week_start"])
            except ValueError:
                # A technical write bypassed save(); never repair it silently.
                diagnostics.append(Diagnostic("stored_time_inconsistent", f"observations[{index}]"))
                continue
            observations.append(ObservationRecord(time, _observation_fields(row, disclosure)))
        if diagnostics:
            return ProjectionResult(None, tuple(diagnostics))

    try:
        snapshot = build_snapshot(
            disclosure,
            source=source_values or None,
            identity=identity_values or None,
            observations=observations,
        )
    except SnapshotError as error:
        return ProjectionResult(None, error.diagnostics)
    return ProjectionResult(snapshot)


def _observation_rows(source, box, disclosure):
    columns = ["measured_on", "week_start", "temporal_precision"]
    columns += [OBSERVATION_VALUE_SELECTORS[s] for s in sorted(OBSERVATION_VALUE_SELECTORS) if s in disclosure]
    for selector in sorted(OBSERVATION_PROVENANCE_SELECTORS):
        if selector in disclosure:
            columns += OBSERVATION_PROVENANCE_SELECTORS[selector]
    # One query; only disclosed columns are read. pk breaks no real tie because
    # canonical rows are unique per (box, week_start); it keeps order total.
    return (
        BiologicalMeasurement.objects.canonical()
        .filter(box_id=box.pk, box__organization_id=source.pk)
        .order_by("week_start", "pk")
        .values(*columns)
    )


def _observation_fields(row, disclosure):
    fields = {}
    for selector, column in OBSERVATION_VALUE_SELECTORS.items():
        if selector in disclosure:
            fields[selector] = FieldValue.of(row[column])
    if "observation.origin" in disclosure:
        fields["observation.origin"] = {
            "origin": FieldValue.of(row["origin"]),
            # Stored "" means not applicable outside file imports.
            "import_format": FieldValue.of(row["import_format"] or None),
        }
    if "observation.observer_status" in disclosure:
        fields["observation.observer_status"] = {"observer_status": FieldValue.of(row["observer_status"])}
    return fields
