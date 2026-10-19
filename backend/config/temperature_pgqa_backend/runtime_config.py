"""Connection-free validation of explicit disposable-cluster configuration."""

import ipaddress
import os
import re


def required(name):
    value = os.environ.get(name, "")
    if not value or value != value.strip():
        raise RuntimeError(f"Missing or invalid explicit configuration: {name}")
    return value


def load_target(prefix="POLYPBASE_QA_"):
    host = required(prefix + "HOST")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise RuntimeError("QA host must be a numeric loopback address") from None
    if not address.is_loopback or str(address) != host:
        raise RuntimeError("QA host must be a canonical numeric loopback address")
    port = required(prefix + "PORT")
    if not port.isascii() or not port.isdecimal() or str(int(port)) != port or not 1 <= int(port) <= 65535 or int(port) == 55438:
        raise RuntimeError("QA port must be explicit, valid and not the forbidden shared port")
    database = required(prefix + "DATABASE")
    owner = required(prefix + "OWNER")
    runtime = required(prefix + "RUNTIME")
    if any(not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value) for value in (database, owner, runtime)):
        raise RuntimeError("QA database and roles must be plain lowercase PostgreSQL identifiers")
    if database in {"postgres", "template0", "template1"} or owner == runtime or any(value.startswith("pg_") or value == "postgres" for value in (owner, runtime)):
        raise RuntimeError("QA requires a dedicated database and distinct non-reserved roles")
    system_identifier = required(prefix + "SYSTEM_IDENTIFIER")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", system_identifier) or int(system_identifier) > 2**64 - 1:
        raise RuntimeError("QA system identifier must be an explicit PostgreSQL control identifier")
    return host, port, database, system_identifier, owner, runtime


def disable_dotenv():
    """Also support python-dotenv versions without the disabling environment flag."""
    import dotenv

    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    dotenv.load_dotenv = lambda *args, **kwargs: False
