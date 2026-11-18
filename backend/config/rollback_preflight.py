"""Refuse a lossy rollback plan before Django unapplies any of its migrations.

Django unapplies each migration of a plan in its own transaction. A protected
migration's reverse guard therefore runs only after every dependent migration
earlier in the same plan has already been unapplied and committed: a refusal
would leave a partially downgraded schema. The ``migrate`` command sends
``pre_migrate`` with the complete plan first. This preflight runs every
applicable reverse guard, in plan order, in one transaction, before anything is
unapplied. The guards themselves are unchanged and still run again inside each
migration's own transaction.

A plan containing multiple backwards migrations and any protected migration
is refused unconditionally, even when empty. Preflight table locks end with its
transaction, and receipt writers need not take the registry advisory key: those
locks cannot make a multi-migration downgrade safe. Such a downgrade requires a
separately reviewed offline preservation procedure, not automatic ``migrate``.

A single backwards migration remains subject to its own reverse guard in the
same transaction as its schema changes. On PostgreSQL the preflight additionally
takes the Local ID registry advisory key until ``post_migrate`` to exclude
cooperating writers; this is not protection against arbitrary receipt inserts.
A failed ``migrate`` keeps that key until its database connection closes.

Direct ``MigrationExecutor`` use does not send ``pre_migrate``; each migration's
own guard still preserves its evidence there. The dedicated production
migration runner refuses every backwards plan.
"""

from importlib import import_module
from types import SimpleNamespace

from django.db import DatabaseError, connections, transaction
from django.db.migrations.exceptions import IrreversibleError


# Migrations whose reverse refuses while they hold durable evidence, mapped to
# the name of that reverse guard in the migration module.
REVERSE_GUARDS = {
    ("cultures", "0022_box_global_identity"): "refuse_identity_loss",
    ("cultures", "0023_box_local_id_alias"): "refuse_lossy_rollback",
    ("measurements", "0019_box_creation_initial_origin"): "refuse_populated_rollback",
    ("taxonomy", "0013_institutionstraincreationrequest"): "refuse_receipt_loss",
    ("taxonomy", "0014_speciescodechangerequest"): "refuse_receipt_loss",
    ("taxonomy", "0015_provenance_country_acquisition_types"): "refuse_populated_rollback",
}
# Same key as apps.cultures.box_codes.lock_local_id_registry.
REGISTRY_LOCK = (192837, 19)
_held = set()


def guarded_migrations(plan):
    return [
        (migration, REVERSE_GUARDS[(migration.app_label, migration.name)])
        for migration, backwards in plan or ()
        if backwards and (migration.app_label, migration.name) in REVERSE_GUARDS
    ]


def release_registry_lock(using):
    """Release the session-level key taken by a passed preflight, if any."""
    if using not in _held:
        return
    _held.discard(using)
    connection = connections[using]
    if connection.connection is None:
        return  # Closing the connection already released it.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s, %s)", REGISTRY_LOCK)


def preflight_rollback_plan(*, plan, apps, using):
    guarded = guarded_migrations(plan)
    if not guarded:
        return
    if sum(bool(backwards) for _, backwards in plan) > 1:
        raise IrreversibleError(
            "Multi-migration rollback across protected evidence is refused, even when empty. "
            "Use a separately reviewed offline preservation procedure."
        )
    connection = connections[using]
    if connection.vendor == "postgresql" and using not in _held:
        if connection.in_atomic_block:
            raise IrreversibleError("A protected rollback must not run inside an outer transaction.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", REGISTRY_LOCK)
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise IrreversibleError("Identity writers are active. Stop the application before this rollback.")
        _held.add(using)
    try:
        with transaction.atomic(using=using):
            # The guards only read schema_editor.connection; no DDL runs here.
            editor = SimpleNamespace(connection=connection)
            for migration, name in guarded:
                getattr(import_module(type(migration).__module__), name)(apps, editor)
    except DatabaseError as error:
        release_registry_lock(using)
        cause = error.__cause__
        if (getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)) == "55P03":
            raise IrreversibleError("Protected tables are in use. Stop the application before this rollback.") from error
        raise
    except BaseException:
        release_registry_lock(using)
        raise


def refuse_lossy_rollback_plan(sender, *, plan=None, apps=None, using="default", **kwargs):
    preflight_rollback_plan(plan=plan, apps=apps, using=using)


def end_rollback(sender, *, using="default", **kwargs):
    release_registry_lock(using)
