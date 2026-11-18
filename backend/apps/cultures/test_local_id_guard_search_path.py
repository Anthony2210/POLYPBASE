"""cultures.0024: Local ID guards must not depend on the caller's search_path."""

import hashlib
from importlib import import_module
from unittest import skipUnless

from django.db import DatabaseError, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase

from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain
from config.disposable_postgres import DisposablePostgresFlushMixin

from .models import Box, BoxLocalIdAlias

hardening = import_module("apps.cultures.migrations.0024_local_id_guard_search_path")
original = import_module("apps.cultures.migrations.0023_box_local_id_alias")

FUNCTIONS = ("immutable_box_local_id_aliases", "lock_box_local_ids", "protect_box_alias_organization",
             "validate_box_local_id_alias", "validate_box_local_id_insert", "validate_box_local_id_update")
TRIGGERS = {
    ("cultures_box", "lock_box_local_ids"): "lock_box_local_ids",
    ("cultures_box", "protect_box_alias_organization"): "protect_box_alias_organization",
    ("cultures_box", "validate_box_local_id_insert"): "validate_box_local_id_insert",
    ("cultures_box", "validate_box_local_id_update"): "validate_box_local_id_update",
    ("cultures_boxlocalidalias", "immutable_box_local_id_aliases"): "immutable_box_local_id_aliases",
    ("cultures_boxlocalidalias", "lock_box_local_id_aliases"): "lock_box_local_ids",
    ("cultures_boxlocalidalias", "no_truncate_box_local_id_aliases"): "immutable_box_local_id_aliases",
    ("cultures_boxlocalidalias", "validate_box_local_id_alias"): "validate_box_local_id_alias",
}
GENERATED_SQL_SHA256 = "8ccb9e7c11e94789eaeb0f7da27485a2b6ff9296cf604aecfefc2743cb9ceb8a"


class HardenedDefinitionTests(SimpleTestCase):
    def test_bodies_equal_0023_except_schema_qualification(self):
        class Recorder:
            connection = type("Connection", (), {"vendor": "postgresql"})()

            def __init__(self):
                self.sql = []

            def execute(self, statement, params=None):
                self.sql.append(statement)

        recorder = Recorder()
        original.install_guards(None, recorder)
        installed = "\n".join(recorder.sql)

        def normalized(text):
            return " ".join(text.replace("public.", "").replace("pg_catalog.", "").split())

        definitions = hardening.hardened_definitions()
        self.assertEqual(sorted(definitions), sorted(FUNCTIONS))
        for name, body in definitions.items():
            with self.subTest(function=name):
                marker = f"CREATE FUNCTION {name}() RETURNS trigger AS $$"
                self.assertEqual(installed.count(marker), 1)
                old = installed.split(marker, 1)[1].split("$$", 1)[0]
                self.assertEqual(normalized(old), normalized(body))
                self.assertIsNone(hardening._RELATION.search(body.replace("public.cultures_box", "")))

    def test_generated_sql_is_frozen(self):
        # 0024 derives its SQL from the frozen 0023 builders; any later edit to
        # either migration would change what a fresh database installs.
        definitions = hardening.hardened_definitions()
        generated = "\n".join(hardening._function(name, definitions[name]) for name in sorted(definitions))
        self.assertEqual(hashlib.sha256(generated.encode()).hexdigest(), GENERATED_SQL_SHA256)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL trigger functions")
class LocalIdGuardSearchPathTests(DisposablePostgresFlushMixin, TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Search path laboratory")
        self.other_org = Organization.objects.create(name="Other search path laboratory")
        species = Species.objects.create(scientific_name="Search path species", genus_species_code="SPS")
        self.strain = Strain.objects.create(organization=self.org, species=species, code="SPS-LAB-1")
        self.other_strain = Strain.objects.create(organization=self.other_org, species=species, code="SPS-OTH-1")

    def test_functions_are_hardened_in_place_and_triggers_stay_attached(self):
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT p.proname, p.proconfig, p.prosecdef, pg_catalog.pg_get_userbyid(p.proowner)
                FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND p.proname = ANY(%s)""", [list(FUNCTIONS)])
            rows = {row[0]: row[1:] for row in cursor.fetchall()}
            cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class c "
                           "WHERE c.oid = 'public.cultures_box'::regclass")
            table_owner = cursor.fetchone()[0]
            cursor.execute("""
                SELECT c.relname, t.tgname, p.proname, t.tgenabled
                FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
                JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
                WHERE NOT t.tgisinternal AND p.proname = ANY(%s)""", [list(FUNCTIONS)])
            triggers = {(row[0], row[1]): row[2:] for row in cursor.fetchall()}
        self.assertEqual(sorted(rows), sorted(FUNCTIONS))
        for name, (config, definer, owner) in rows.items():
            with self.subTest(function=name):
                self.assertEqual(config, ["search_path=pg_catalog, pg_temp"])
                self.assertFalse(definer)
                self.assertEqual(owner, table_owner)
        self.assertEqual(triggers, {key: (function, "O") for key, function in TRIGGERS.items()})

    def restricted(self):
        """Run a statement as a SECURITY DEFINER caller would: without public on the path."""
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL search_path = pg_catalog, pg_temp")

    def test_guards_accept_valid_and_refuse_invalid_writes_under_restricted_search_path(self):
        source = Box.objects.create(organization=self.org, strain=self.strain,
                                    global_code="SPS-LAB-1.001", box_number="001")
        foreign = Box.objects.create(organization=self.other_org, strain=self.other_strain,
                                     global_code="SPS-OTH-1.001", box_number="001")
        with transaction.atomic():
            self.restricted()
            with connection.cursor() as cursor:
                cursor.execute("UPDATE public.cultures_box SET global_code = %s WHERE id = %s",
                               ["SPS-LAB-1.101", source.pk])
                cursor.execute("INSERT INTO public.cultures_boxlocalidalias (code, box_id, created_at) "
                               "VALUES (%s, %s, pg_catalog.now())", ["SPS-LAB-1.001", source.pk])
        self.assertTrue(BoxLocalIdAlias.objects.filter(code="SPS-LAB-1.001", box=source).exists())
        refusals = (
            # A foreign institution cannot claim another institution's retained Local ID.
            ("UPDATE public.cultures_box SET previous_global_code = %s WHERE id = %s", ["SPS-LAB-1.001", foreign.pk]),
            ("UPDATE public.cultures_box SET global_code = %s WHERE id = %s", ["SPS-LAB-1.001", foreign.pk]),
            ("INSERT INTO public.cultures_boxlocalidalias (code, box_id, created_at) "
             "VALUES (%s, %s, pg_catalog.now())", ["SPS-LAB-1.101", foreign.pk]),
            ("UPDATE public.cultures_box SET organization_id = %s WHERE id = %s", [self.other_org.pk, source.pk]),
            ("DELETE FROM public.cultures_boxlocalidalias WHERE box_id = %s", [source.pk]),
        )
        for statement, params in refusals:
            with self.subTest(statement=statement):
                with self.assertRaises(DatabaseError) as raised, transaction.atomic():
                    self.restricted()
                    with connection.cursor() as cursor:
                        cursor.execute(statement, params)
                self.assertEqual(getattr(raised.exception.__cause__, "sqlstate", None), "23514")
        self.assertEqual(list(BoxLocalIdAlias.objects.values_list("code", "box_id")), [("SPS-LAB-1.001", source.pk)])
        foreign.refresh_from_db()
        self.assertEqual((foreign.global_code, foreign.previous_global_code, foreign.organization_id),
                         ("SPS-OTH-1.001", "", self.other_org.pk))

    def test_repeatable_read_is_still_refused_under_restricted_search_path(self):
        source = Box.objects.create(organization=self.org, strain=self.strain,
                                    global_code="SPS-LAB-1.002", box_number="002")
        with self.assertRaises(DatabaseError) as raised, transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self.restricted()
            with connection.cursor() as cursor:
                cursor.execute("UPDATE public.cultures_box SET global_code = %s WHERE id = %s",
                               ["SPS-LAB-1.202", source.pk])
        self.assertEqual(getattr(raised.exception.__cause__, "sqlstate", None), "23514")
