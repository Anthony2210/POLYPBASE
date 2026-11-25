"""Verify every Django connection, including worker-thread connections."""

from django.db.backends.postgresql.base import DatabaseWrapper as PostgreSQLWrapper

from config.backend_postgres_ci import require, require_service


HISTORY_RACE_TIMEOUTS = (" -c lock_timeout=10000 -c statement_timeout=15000"
                         " -c idle_in_transaction_session_timeout=20000")


def startup_role(options):
    return "box1902_owner" if options.endswith(" -c role=box1902_owner") else None


class DatabaseWrapper(PostgreSQLWrapper):
    def get_connection_params(self):
        service = require_service()
        config = self.settings_dict
        role = startup_role(config["OPTIONS"].get("options", ""))
        expected = service.django_config(config["NAME"], config["USER"], role=role)
        if (config["NAME"] == service.prefix + "revisions" and role is None
                and config["OPTIONS"].get("options") == expected["OPTIONS"]["options"] + HISTORY_RACE_TIMEOUTS):
            expected["OPTIONS"]["options"] += HISTORY_RACE_TIMEOUTS
        require(all(config.get(key) == value for key, value in expected.items()),
                "Refusing unexpected Django service connection settings.")
        return super().get_connection_params()

    def get_new_connection(self, conn_params):
        conn = super().get_new_connection(conn_params)
        try:
            require_service().verify(conn, conn_params["dbname"], conn_params["user"],
                                     startup_role(conn_params["options"]))
            # Identity SELECTs must not leave a transaction open for Django.
            conn.rollback()
        except BaseException:
            conn.close()
            raise
        return conn
