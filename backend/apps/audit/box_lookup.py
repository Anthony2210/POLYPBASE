"""Organization-scoped typed identities; codes remain ambiguous evidence."""

from django.db.models import BooleanField, Case, Value, When

from .expressions import AuditBoxId
from .models import AuditLog


def box_audits(*, organization_id, box_ids, codes=()):
    scoped = AuditLog.objects.filter(organization_id=organization_id, object_type="box")
    numeric = scoped.alias(box_identity=AuditBoxId("metadata")).filter(box_identity__in=box_ids)
    if not codes:
        return numeric.annotate(is_numeric_box_audit=Value(True, output_field=BooleanField()))
    # Separate PK branches let each identity use its own composite index. UNION
    # deduplicates audits matching both identities before loading model instances.
    numeric_ids = numeric.order_by().values("pk")
    legacy_ids = scoped.filter(object_id__in=codes).order_by().values("pk")
    # Both branches enforce scope; fetch only those PKs, without encouraging an
    # outer organization/date scan instead of primary-key lookups on SQLite.
    return (AuditLog.objects.filter(pk__in=numeric_ids.union(legacy_ids))
            .alias(box_identity=AuditBoxId("metadata"))
            .annotate(is_numeric_box_audit=Case(
                When(box_identity__in=box_ids, then=Value(True)),
                default=Value(False), output_field=BooleanField(),
            )))


def numeric_box_audit_values(audits):
    """Keep database membership; decoded JSON floats can lose numeric precision."""
    fields = AuditLog._meta.concrete_fields
    return [
        {field.attname: getattr(audit, field.attname) for field in fields}
        for audit in sorted(audits, key=lambda audit: audit.pk)
        if audit.is_numeric_box_audit
    ]
