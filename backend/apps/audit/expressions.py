from django.db.models.fields.json import KeyTransform


class AuditBoxId(KeyTransform):
    """Typed JSON box identity with a fixed, indexable SQLite path."""

    def __init__(self, lhs, **extra):
        super().__init__("box_id", lhs, **extra)

    def as_sqlite(self, compiler, connection):
        lhs, params = compiler.compile(self.lhs)
        # SQLite expression indexes cannot match Django's parameterized JSON path.
        # Keep both the path and type order fixed in queries and migration DDL.
        path = "'$.\"box_id\"'"
        sql = (
            f"(CASE WHEN JSON_TYPE({lhs}, {path}) IN ('null','false','true') "
            f"THEN JSON_TYPE({lhs}, {path}) ELSE JSON_EXTRACT({lhs}, {path}) END)"
        )
        return sql, tuple(params) * 3
