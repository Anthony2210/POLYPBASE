"""Scientific-name comparison, without rewriting the stored scientific name.

Keep ScientificNameKey available for migrations. Whitespace follows Python's
str.split contract; case comparison retains the existing database Lower rule.
"""

import re

from django.db import models
from django.db.models.functions import Lower, Trim

# Accepted input for new or edited scientific names: Latin names, subgenera,
# qualifiers ("sp. 2", "cf."), hybrid markers. No wildcards or markup.
SCIENTIFIC_NAME_PATTERN = re.compile(r"^[A-Za-z×][A-Za-z0-9 .()'×-]*$")


# Explicit Python whitespace set, not a database/locale-dependent regex class.
WHITESPACE = "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"


def normalize_scientific_name(value):
    return " ".join(value.split())


class ScientificNameKey(models.Func):
    arity = 1
    output_field = models.CharField()

    def as_postgresql(self, compiler, connection, **extra_context):
        expression = Lower(Trim(models.Func(
            self.source_expressions[0], models.Value(f"[{WHITESPACE}]+"),
            models.Value(" "), models.Value("g"), function="REGEXP_REPLACE",
        )))
        return compiler.compile(expression)

    def as_sqlite(self, compiler, connection, **extra_context):
        return super().as_sql(
            compiler, connection, function="polypbase_species_name_key", **extra_context,
        )


def register_sqlite_name_key(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        # SQLite LOWER is ASCII-only, like its existing case-insensitive index.
        def name_key(value):
            if value is None:
                return None
            return normalize_scientific_name(value).translate(
                str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
            )

        connection.connection.create_function(
            "polypbase_species_name_key", 1, name_key, deterministic=True,
        )
