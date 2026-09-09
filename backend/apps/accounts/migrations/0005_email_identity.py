import re

from django.db import migrations


EMAIL_LOCAL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
EMAIL_DOMAIN_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def canonical_email(value):
    candidate = str(value or "").strip()
    if candidate.count("@") != 1:
        return None
    local, domain = candidate.split("@")
    normalized_local = local.translate(
        str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    )
    if (
        not normalized_local
        or normalized_local.startswith(".")
        or normalized_local.endswith(".")
        or ".." in normalized_local
        or not normalized_local.isascii()
        or not EMAIL_LOCAL_PATTERN.fullmatch(normalized_local)
    ):
        return None
    try:
        normalized_domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not EMAIL_DOMAIN_PATTERN.fullmatch(normalized_domain):
        return None
    normalized = f"{normalized_local}@{normalized_domain}"
    return normalized if len(normalized) <= 254 else None


def normalize_existing_emails(apps, schema_editor):
    user_model = apps.get_model("auth", "User")
    seen = {}

    for user in user_model.objects.order_by("pk"):
        normalized = canonical_email(user.email)
        if normalized is None:
            raise RuntimeError(
                "Cannot enforce email identity: user "
                f"{user.pk} has no valid canonical email."
            )
        if normalized in seen and seen[normalized] != user.pk:
            raise RuntimeError(
                "Cannot enforce unique email identity: duplicate email addresses "
                f"for users {seen[normalized]} and {user.pk}."
            )
        seen[normalized] = user.pk
        if user.email != normalized:
            user.email = normalized
            user.save(update_fields=["email"])


def create_email_identity_index(apps, schema_editor):
    user_model = apps.get_model("auth", "User")
    table = schema_editor.quote_name(user_model._meta.db_table)
    index = schema_editor.quote_name("auth_user_email_identity_idx")
    schema_editor.execute(f"CREATE UNIQUE INDEX {index} ON {table} (email)")


def drop_email_identity_index(apps, schema_editor):
    index = schema_editor.quote_name("auth_user_email_identity_idx")
    schema_editor.execute(f"DROP INDEX {index}")


def create_email_identity_guard(apps, schema_editor):
    user_model = apps.get_model("auth", "User")
    table = schema_editor.quote_name(user_model._meta.db_table)
    if schema_editor.connection.vendor == "sqlite":
        schema_editor.execute(
            f"""
            CREATE TRIGGER auth_user_email_canonical_insert
            BEFORE INSERT ON {table}
            WHEN NEW.email IS NULL
              OR NEW.email = ''
              OR length(NEW.email) > 254
              OR NEW.email GLOB '*[^ -~]*'
              OR NEW.email GLOB '*[A-Z]*'
              OR NEW.email NOT GLOB '*@*'
              OR NEW.email GLOB '*@*@*'
              OR substr(NEW.email, 1, instr(NEW.email, '@') - 1) = ''
              OR substr(NEW.email, instr(NEW.email, '@') + 1) = ''
              OR substr(NEW.email, 1, instr(NEW.email, '@') - 1)
                   GLOB '*[^A-Za-z0-9.!#$%&''*+/=?^_`{{|}}~-]*'
              OR substr(NEW.email, 1, instr(NEW.email, '@') - 1) GLOB '.*'
              OR substr(NEW.email, 1, instr(NEW.email, '@') - 1) GLOB '*.'
              OR substr(NEW.email, 1, instr(NEW.email, '@') - 1) GLOB '*..*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1)
                   GLOB '*[^A-Za-z0-9.-]*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) NOT GLOB '*.*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '.*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '*.'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '*..*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '*.-*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '*-.*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '-*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '*-'
            BEGIN
                SELECT CASE WHEN EXISTS (
                    WITH RECURSIVE labels(rest, label) AS (
                        SELECT substr(NEW.email, instr(NEW.email, '@') + 1), ''
                        UNION ALL
                        SELECT
                            CASE WHEN instr(rest, '.') = 0
                                THEN '' ELSE substr(rest, instr(rest, '.') + 1) END,
                            CASE WHEN instr(rest, '.') = 0
                                THEN rest ELSE substr(rest, 1, instr(rest, '.') - 1) END
                        FROM labels
                        WHERE rest <> ''
                    )
                    SELECT 1 FROM labels WHERE length(label) > 63
                ) THEN RAISE(ABORT, 'email domain label is too long') END;
                SELECT RAISE(ABORT, 'email must be canonical and valid');
            END
            """
        )
        schema_editor.execute(
            f"""
            CREATE TRIGGER auth_user_email_canonical_update
            BEFORE UPDATE OF email ON {table}
            WHEN NEW.email IS NULL
              OR NEW.email = ''
              OR length(NEW.email) > 254
              OR NEW.email GLOB '*[^ -~]*'
              OR NEW.email GLOB '*[A-Z]*'
              OR NEW.email NOT GLOB '*@*'
              OR NEW.email GLOB '*@*@*'
              OR substr(NEW.email, 1, instr(NEW.email, '@') - 1) = ''
              OR substr(NEW.email, instr(NEW.email, '@') + 1) = ''
              OR substr(NEW.email, 1, instr(NEW.email, '@') - 1)
                   GLOB '*[^A-Za-z0-9.!#$%&''*+/=?^_`{{|}}~-]*'
              OR substr(NEW.email, 1, instr(NEW.email, '@') - 1) GLOB '.*'
              OR substr(NEW.email, 1, instr(NEW.email, '@') - 1) GLOB '*.'
              OR substr(NEW.email, 1, instr(NEW.email, '@') - 1) GLOB '*..*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1)
                   GLOB '*[^A-Za-z0-9.-]*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) NOT GLOB '*.*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '.*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '*.'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '*..*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '*.-*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '*-.*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '-*'
              OR substr(NEW.email, instr(NEW.email, '@') + 1) GLOB '*-'
            BEGIN
                SELECT CASE WHEN EXISTS (
                    WITH RECURSIVE labels(rest, label) AS (
                        SELECT substr(NEW.email, instr(NEW.email, '@') + 1), ''
                        UNION ALL
                        SELECT
                            CASE WHEN instr(rest, '.') = 0
                                THEN '' ELSE substr(rest, instr(rest, '.') + 1) END,
                            CASE WHEN instr(rest, '.') = 0
                                THEN rest ELSE substr(rest, 1, instr(rest, '.') - 1) END
                        FROM labels
                        WHERE rest <> ''
                    )
                    SELECT 1 FROM labels WHERE length(label) > 63
                ) THEN RAISE(ABORT, 'email domain label is too long') END;
                SELECT RAISE(ABORT, 'email must be canonical and valid');
            END
            """
        )
        schema_editor.execute(
            f"""
            CREATE TRIGGER auth_user_email_domain_length_insert
            BEFORE INSERT ON {table}
            BEGIN
                SELECT CASE WHEN EXISTS (
                    WITH RECURSIVE labels(rest, label) AS (
                        SELECT substr(NEW.email, instr(NEW.email, '@') + 1), ''
                        UNION ALL
                        SELECT
                            CASE WHEN instr(rest, '.') = 0
                                THEN '' ELSE substr(rest, instr(rest, '.') + 1) END,
                            CASE WHEN instr(rest, '.') = 0
                                THEN rest ELSE substr(rest, 1, instr(rest, '.') - 1) END
                        FROM labels
                        WHERE rest <> ''
                    )
                    SELECT 1 FROM labels WHERE length(label) > 63
                ) THEN RAISE(ABORT, 'email domain label is too long') END;
            END
            """
        )
        schema_editor.execute(
            f"""
            CREATE TRIGGER auth_user_email_domain_length_update
            BEFORE UPDATE OF email ON {table}
            BEGIN
                SELECT CASE WHEN EXISTS (
                    WITH RECURSIVE labels(rest, label) AS (
                        SELECT substr(NEW.email, instr(NEW.email, '@') + 1), ''
                        UNION ALL
                        SELECT
                            CASE WHEN instr(rest, '.') = 0
                                THEN '' ELSE substr(rest, instr(rest, '.') + 1) END,
                            CASE WHEN instr(rest, '.') = 0
                                THEN rest ELSE substr(rest, 1, instr(rest, '.') - 1) END
                        FROM labels
                        WHERE rest <> ''
                    )
                    SELECT 1 FROM labels WHERE length(label) > 63
                ) THEN RAISE(ABORT, 'email domain label is too long') END;
            END
            """
        )
        return
    if schema_editor.connection.vendor == "postgresql":
        # Django defaults params to (), which makes psycopg parse literal percent
        # characters as placeholders. None sends this parameter-free SQL verbatim.
        schema_editor.execute(
            f"""
            ALTER TABLE {table}
            ADD CONSTRAINT auth_user_email_canonical
            CHECK (
                email IS NOT NULL
                AND email <> ''
                AND octet_length(email) = length(email)
                AND length(email) <= 254
                AND email COLLATE "C" ~
                    '^[a-z0-9.!#$%&''*+/=?^_`{{|}}~-]+@[a-z0-9]([a-z0-9-]{{0,61}}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{{0,61}}[a-z0-9])?)+$'
                AND split_part(email, '@', 1) NOT LIKE '.%'
                AND split_part(email, '@', 1) NOT LIKE '%.'
                AND split_part(email, '@', 1) NOT LIKE '%..%'
            )
            """,
            params=None,
        )
        return
    raise RuntimeError(
        f"Unsupported database for email identity guard: {schema_editor.connection.vendor}"
    )


def drop_email_identity_guard(apps, schema_editor):
    user_model = apps.get_model("auth", "User")
    table = schema_editor.quote_name(user_model._meta.db_table)
    if schema_editor.connection.vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER auth_user_email_canonical_insert")
        schema_editor.execute("DROP TRIGGER auth_user_email_canonical_update")
        schema_editor.execute("DROP TRIGGER auth_user_email_domain_length_insert")
        schema_editor.execute("DROP TRIGGER auth_user_email_domain_length_update")
        return
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT auth_user_email_canonical"
        )
        return
    raise RuntimeError(
        f"Unsupported database for email identity guard: {schema_editor.connection.vendor}"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_authenticationthrottle"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(normalize_existing_emails, migrations.RunPython.noop),
        migrations.RunPython(create_email_identity_index, drop_email_identity_index),
        migrations.RunPython(create_email_identity_guard, drop_email_identity_guard),
    ]
