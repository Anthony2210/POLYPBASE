"""PostgreSQL connections confined to a single pre-provisioned QA database."""

from contextlib import contextmanager

from django.db.backends.postgresql.base import DatabaseWrapper as PostgreSQLWrapper
from django.db.backends.postgresql.creation import DatabaseCreation

from config import historical_temperature_pgqa as qa


def forbidden_lifecycle(*args, **kwargs):
    raise RuntimeError("Database creation, destruction, cloning and maintenance connections are forbidden")


class NoDatabaseLifecycle(DatabaseCreation):
    create_test_db = forbidden_lifecycle
    _create_test_db = forbidden_lifecycle
    _execute_create_test_db = forbidden_lifecycle
    destroy_test_db = forbidden_lifecycle
    _destroy_test_db = forbidden_lifecycle
    clone_test_db = forbidden_lifecycle
    _clone_test_db = forbidden_lifecycle


class DatabaseWrapper(PostgreSQLWrapper):
    creation_class = NoDatabaseLifecycle

    def get_connection_params(self):
        qa.assert_config(self.settings_dict)
        return super().get_connection_params()

    def get_new_connection(self, conn_params):
        qa.assert_config(self.settings_dict)
        expected = {
            "host": qa.HOST, "port": qa.PORT, "dbname": qa.DATABASE,
            "user": self.settings_dict["USER"], "password": self.settings_dict["PASSWORD"],
            "sslmode": "disable", "connect_timeout": 5, "options": qa.OPTIONS,
            "application_name": "polypbase-temperature-isolated-qa", "passfile": qa.os.devnull,
        }
        if any(conn_params.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Unexpected effective PostgreSQL connection parameters")
        if set(conn_params) - set(expected) - {"client_encoding", "cursor_factory", "context", "prepare_threshold"}:
            raise RuntimeError("Unreviewed PostgreSQL connection options are forbidden")
        raw = super().get_new_connection(conn_params)
        qa.register_raw(raw)
        return raw

    def init_connection_state(self):
        super().init_connection_state()
        try:
            qa.verify_raw(self.connection, self.settings_dict["USER"])
        except BaseException:
            self.connection.close()
            raise

    @contextmanager
    def _nodb_cursor(self):
        forbidden_lifecycle()
        yield  # pragma: no cover
