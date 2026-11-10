"""Install the independently provisioned, exact Box 1902 SQL capability.

No role creation, ownership transfer or approval is part of this migration.
Non-PostgreSQL databases intentionally receive no substitute protection.
"""

from django.db import migrations
from django.db.migrations.exceptions import IrreversibleError

from apps.measurements.box_1902_sql import SQL, assert_topology, attest


def forwards(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    assert_topology(connection, installation=True)
    # params=None prevents interpolation of PostgreSQL format('%I', ...) tokens.
    schema_editor.execute(SQL, params=None)
    attest(connection, allow_retired=False)


def backwards(apps, schema_editor):
    raise IrreversibleError(
        "Box 1902 provenance and permanent evidence validation cannot be removed. "
        "Retire the one-off lock/apply entrypoints through reviewed provisioning instead."
    )


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("measurements", "0017_bucket_probe_last_measured_index")]
    operations = [migrations.RunPython(forwards, backwards)]
