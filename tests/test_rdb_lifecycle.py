"""Every database lifecycle operation reaches a remote host through
RemoteMySQLBackend, against a fake MySQL server PyMySQL is patched to return."""

import datetime as dt
import ssl
from pathlib import Path

import pytest

from rdb_fakes import FakeMySQLServer, make_host, make_session, make_user


@pytest.fixture
def server(monkeypatch):
    import pymysql

    fake = FakeMySQLServer()
    monkeypatch.setattr(pymysql, "connect", fake.connect)
    return fake


@pytest.fixture
def db():
    return make_session()


def _remote_account(db, host, name="shop"):
    from app.core.secrets import encrypt
    from app.models.entities import DatabaseAccount

    user = make_user(db, username=f"owner_{name}")
    account = DatabaseAccount(
        owner_id=user.id, db_name=name, db_user=name, db_password=encrypt("pw-123456789012"), db_host_id=host.id
    )
    db.add(account)
    db.commit()
    return account


# --- client host restriction --------------------------------------------------

@pytest.mark.parametrize("value", ["%", "%.%", "%.%.%.%", "_%", "0.0.0.0/0.0.0.0", "a'b", "x y", "10.0.0.1;DROP"])
def test_client_hosts_never_accept_everyone_or_garbage(value):
    from app.services import remote_mysql

    with pytest.raises(ValueError):
        remote_mysql.validate_client_host(value)


def test_client_hosts_accept_addresses_netmasks_patterns_and_names():
    from app.services import remote_mysql

    assert remote_mysql.parse_client_hosts("10.0.0.10, 2001:db8::1 10.0.0.% web1.example.com;10.1.0.0/255.255.0.0, 10.0.0.10") == [
        "10.0.0.10", "2001:db8::1", "10.0.0.%", "web1.example.com", "10.1.0.0/255.255.0.0",
    ]


def test_client_hosts_fall_back_to_the_detected_source_address(db, monkeypatch):
    from app.services import remote_mysql

    host = make_host(db, allowed_client_hosts="")
    monkeypatch.setattr(remote_mysql, "detect_source_address", lambda h, p: "192.0.2.7")
    assert remote_mysql.client_hosts_for(host) == ["192.0.2.7"]
    monkeypatch.setattr(remote_mysql, "detect_source_address", lambda h, p: None)
    with pytest.raises(remote_mysql.RemoteDatabaseError, match="Allowed client hosts"):
        remote_mysql.client_hosts_for(host)


def test_render_create_statements_names_each_client_host_and_never_percent():
    from app.services import remote_mysql

    statements = remote_mysql.render_create_statements("shop", "shop", "pw", ["10.0.0.10", "10.0.0.11"])
    users = [params for sql, params in statements if sql.startswith("CREATE USER")]
    assert users == [("shop", "10.0.0.10", "pw"), ("shop", "10.0.0.11", "pw")]
    grants = [sql for sql, _ in statements if sql.startswith("GRANT")]
    assert grants == ["GRANT ALL PRIVILEGES ON `shop`.* TO %s@%s"] * 2
    assert all("%" not in str(params[1]) for sql, params in statements if params)
    with pytest.raises(ValueError):
        remote_mysql.render_create_statements("shop", "shop", "pw", ["%"])
    with pytest.raises(ValueError):
        remote_mysql.render_create_statements("shop`; DROP", "shop", "pw", ["10.0.0.10"])


# --- lifecycle against the fake server ---------------------------------------

def test_create_drop_password_on_remote(db, server):
    from app.services import db_backends

    host = make_host(db, allowed_client_hosts="10.0.0.10, 10.0.0.11")
    backend = db_backends.backend_for_host(host)
    backend.create_database_credentials("shop", "shop", "pw-123456789012")
    assert server.accounts == {"shop": ["10.0.0.10", "10.0.0.11"]}
    assert server.statements("CREATE DATABASE IF NOT EXISTS `shop`")
    assert len(server.statements("ALTER USER %s@%s WITH MAX_USER_CONNECTIONS")) == 2

    with pytest.raises(ValueError, match="already exists"):
        backend.create_database_credentials("shop2", "shop", "pw")
    with pytest.raises(ValueError):
        backend.create_database_credentials("x", "root", "pw")

    backend.change_password("shop", "new-password-1234")
    assert [p for _, p in server.statements("ALTER USER %s@%s IDENTIFIED BY")] == [
        ("shop", "10.0.0.10", "new-password-1234"), ("shop", "10.0.0.11", "new-password-1234"),
    ]

    # The host's client list changing later must not strand the old accounts.
    host.allowed_client_hosts = "10.0.0.99"
    backend.drop_database("shop", "shop")
    assert server.statements("DROP DATABASE IF EXISTS `shop`")
    assert server.accounts == {}

    from app.services.remote_mysql import RemoteDatabaseError
    with pytest.raises(RemoteDatabaseError, match="does not exist"):
        backend.change_password("shop", "x" * 12)


def test_write_lock_tables_optimize_and_limits(db, server):
    from app.services import db_backends

    host = make_host(db)
    server.accounts["shop"] = ["10.0.0.10"]
    backend = db_backends.backend_for_host(host)

    backend.set_write_lock("shop", "shop", True)
    assert server.statements("REVOKE ALL PRIVILEGES ON `shop`.* FROM %s@%s") == [
        ("REVOKE ALL PRIVILEGES ON `shop`.* FROM %s@%s", ("shop", "10.0.0.10"))
    ]
    assert server.statements("GRANT SELECT, DELETE, DROP")
    backend.set_write_lock("shop", "shop", False)
    assert server.statements("GRANT ALL PRIVILEGES ON `shop`.* TO %s@%s")

    server.tables = [("wp_posts", 10, 4096, 1024, 0, "InnoDB"), ("BadCase", 1, 10, 0, 0, "InnoDB")]
    tables = backend.tables_breakdown("shop")
    assert tables[0]["table_name"] == "wp_posts" and tables[0]["total_bytes"] == 5120
    result = backend.optimize("shop")
    assert result["optimized_tables"] == 1
    assert server.statements("OPTIMIZE TABLE") == [("OPTIMIZE TABLE `shop`.`wp_posts`", ())]
    assert server.connect_kwargs[-1]["read_timeout"] == 900

    backend.enforce_resource_limits("shop", max_user_connections=8, max_statement_time=3)
    assert server.statements("ALTER USER %s@%s WITH MAX_USER_CONNECTIONS 8 MAX_STATEMENT_TIME 3")


def test_resource_limits_fall_back_for_mysql_without_statement_time(db, server):
    import pymysql
    from app.services import db_backends

    host = make_host(db)
    server.accounts["shop"] = ["10.0.0.10"]
    server.fail_on["ALTER USER %s@%s WITH MAX_USER_CONNECTIONS 25 MAX_STATEMENT_TIME"] = pymysql.err.ProgrammingError(1064, "syntax")
    db_backends.backend_for_host(host).enforce_resource_limits("shop")
    assert server.statements("ALTER USER %s@%s WITH MAX_USER_CONNECTIONS 25")[-1][0] == "ALTER USER %s@%s WITH MAX_USER_CONNECTIONS 25"


def test_sizes_stats_and_snipe(db, server):
    from app.services import db_backends

    host = make_host(db)
    backend = db_backends.backend_for_host(host)
    server.sizes = {"shop": 5 * 1024 * 1024}
    assert backend.database_sizes(["shop", "blog"]) == {"shop": 5 * 1024 * 1024, "blog": 0}

    server.processlist = [
        (11, "shop", "shop", 9, "Sending data", "SELECT * FROM wp_posts"),
        (12, "shop", "shop", 9, "Updating", "UPDATE wp_options SET x=1"),
        (13, "other", "other", 9, "Sending data", "SELECT 1"),
    ]
    killed = backend.snipe_slow_queries(["shop"], max_time_sec=2)
    assert [k["query_id"] for k in killed] == [11]
    assert server.statements("KILL QUERY") == [("KILL QUERY %s", (11,))]


def test_remote_errors_are_clear_and_name_the_host(db, monkeypatch):
    import pymysql
    from app.services import db_backends, remote_mysql

    host = make_host(db)

    def unreachable(**_kw):
        raise pymysql.err.OperationalError(2003, "Can't connect to MySQL server on '10.0.0.5' (timed out)")

    monkeypatch.setattr(pymysql, "connect", unreachable)
    with pytest.raises(remote_mysql.RemoteDatabaseError) as exc:
        db_backends.backend_for_host(host).drop_database("shop", "shop")
    assert "db1 (10.0.0.5:3306)" in str(exc.value) and "cannot reach" in str(exc.value)

    def tls_fail(**_kw):
        raise pymysql.err.OperationalError(
            2003, "Can't connect to MySQL server on '10.0.0.5' ([SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed)"
        )

    monkeypatch.setattr(pymysql, "connect", tls_fail)
    with pytest.raises(remote_mysql.RemoteDatabaseError, match="TLS verification failed"):
        db_backends.backend_for_host(host).tables_breakdown("shop")


# --- TLS ----------------------------------------------------------------------

def _self_signed_ca_pem() -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "spanel test CA")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def test_tls_connection_verifies_certificate_and_hostname(db, server):
    from app.services import remote_mysql

    pem = _self_signed_ca_pem()
    assert remote_mysql.validate_ca_cert(pem).startswith("-----BEGIN CERTIFICATE-----")
    host = make_host(db, use_tls=True, ca_cert=pem)
    remote_mysql.test_host(db, host.id)
    ctx = server.connect_kwargs[-1]["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    assert any(c.get("subject") == ((("commonName", "spanel test CA"),),) for c in ctx.get_ca_certs())

    plain = make_host(db, host="10.0.0.6", use_tls=False)
    remote_mysql.test_host(db, plain.id)
    assert "ssl" not in server.connect_kwargs[-1]


def test_client_option_file_quotes_password_and_verifies_tls(db):
    from app.services import remote_mysql

    host = make_host(db, use_tls=True, admin_user="admin")
    text = remote_mysql.render_client_defaults(host, 'p"a\\ss\nword', "/tmp/ca.pem")
    lines = text.splitlines()
    assert lines[0] == "[client]"
    assert 'password="p\\"a\\\\ss\\nword"' in lines
    assert "ssl-ca=/tmp/ca.pem" in lines and "ssl-verify-server-cert" in lines
    assert len(lines) == 7  # nothing smuggled onto a line of its own

    host.use_tls = False
    assert "ssl" not in remote_mysql.render_client_defaults(host, "pw", None)


def test_export_and_import_use_a_private_option_file_not_argv(db, monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services import db_backends, remote_mysql
    from app.services.shell import CommandResult

    host = make_host(db, use_tls=True, ca_cert=_self_signed_ca_pem())
    seen = {}

    def fake_run(args, check=True, input=None, sensitive=False, timeout=None):
        cnf = Path(args[1].split("=", 1)[1])
        seen["args"] = args
        seen["cnf"] = cnf.read_text(encoding="utf-8")
        seen["ca"] = (cnf.parent / "ca.pem").read_text(encoding="utf-8")
        return CommandResult("[redacted]", 0, "", "")

    monkeypatch.setattr(remote_mysql.shell, "run", fake_run)
    db_backends.backend_for_host(host).export_database("shop", str(tmp_path / "shop.sql"))
    assert seen["args"][0] == "mysqldump" and seen["args"][1].startswith("--defaults-file=")
    assert "adminpw" not in " ".join(seen["args"])
    assert 'password="adminpw"' in seen["cnf"] and "ssl-verify-server-cert" in seen["cnf"]
    assert "BEGIN CERTIFICATE" in seen["ca"]
    assert not Path(seen["args"][1].split("=", 1)[1]).exists()  # removed afterwards

    sql = tmp_path / "in.sql"
    sql.write_text("SELECT 1;", encoding="utf-8")
    calls = {}

    class Done:
        returncode = 0
        stderr = b""

    def fake_subprocess_run(args, stdin=None, **kwargs):
        calls["args"] = args
        calls["stdin"] = stdin.read()
        return Done()

    monkeypatch.setattr(settings, "command_dry_run", False)
    monkeypatch.setattr(remote_mysql.subprocess, "run", fake_subprocess_run)
    db_backends.backend_for_host(host).import_database("shop", str(sql))
    assert calls["args"][0] == "mysql" and calls["args"][-1] == "shop"
    assert calls["stdin"] == b"SELECT 1;"


# --- callers go through the backend -------------------------------------------

def test_backend_for_picks_local_or_remote(db):
    from app.models.entities import DatabaseAccount
    from app.services import db_backends

    host = make_host(db)
    remote = _remote_account(db, host)
    local = DatabaseAccount(owner_id=remote.owner_id, db_name="loc", db_user="loc", db_password="x")
    db.add(local)
    db.commit()
    assert db_backends.backend_for(local) is db_backends.LOCAL
    assert db_backends.backend_for(remote).is_remote and db_backends.backend_for(remote).host.id == host.id
    groups = db_backends.group_by_backend([remote, local])
    assert groups[0][0] is db_backends.LOCAL and [a.db_name for a in groups[1][1]] == ["shop"]
    assert db_backends.backend_for_db_name(db, "shop").is_remote
    assert db_backends.backend_for_db_name(db, "nope") is db_backends.LOCAL


def test_api_creates_on_the_chosen_host_and_reports_where(db, server):
    from app.api import databases as databases_api
    from app.schemas.schemas import DatabaseCreate

    host = make_host(db)
    admin = make_user(db, username="root_admin", role="admin")
    out = databases_api.create_database(
        DatabaseCreate(db_name="shop", db_user="shop", db_password="pw-123456789012", db_host_id=host.id),
        db=db, current_user=admin,
    )
    assert out.db_host_id == host.id and out.db_host_address == "10.0.0.5"
    assert server.accounts == {"shop": ["10.0.0.10"]}

    hosts = databases_api.list_database_hosts(db=db, current_user=admin)["hosts"]
    assert hosts[0]["id"] is None and hosts[1]["id"] == host.id

    host.enabled = False
    db.commit()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        databases_api.create_database(
            DatabaseCreate(db_name="shop2", db_password="pw-123456789012", db_host_id=host.id), db=db, current_user=admin,
        )
    assert exc.value.status_code == 404

    databases_api.delete_database_record(out.id, db=db, current_user=admin)
    assert server.accounts == {}


def test_api_maps_remote_failure_to_bad_gateway(db, monkeypatch):
    import pymysql
    from fastapi import HTTPException
    from app.api import databases as databases_api
    from app.schemas.schemas import DatabaseCreate

    host = make_host(db)
    admin = make_user(db, username="root_admin", role="admin")

    def unreachable(**_kw):
        raise pymysql.err.OperationalError(2003, "timed out")

    monkeypatch.setattr(pymysql, "connect", unreachable)
    with pytest.raises(HTTPException) as exc:
        databases_api.create_database(
            DatabaseCreate(db_name="shop", db_password="pw-123456789012", db_host_id=host.id), db=db, current_user=admin,
        )
    assert exc.value.status_code == 502 and "10.0.0.5:3306" in exc.value.detail


def test_quota_collector_measures_and_locks_remote_databases(db, server, monkeypatch):
    from app.services import resource_monitor

    host = make_host(db)
    account = _remote_account(db, host)
    owner = account.owner  # held: the limit below is a plain attribute in this test
    owner.mysql_max_db_size_mb = 1
    server.accounts["shop"] = ["10.0.0.10"]
    server.sizes = {"shop": 3 * 1024 * 1024}
    # The local server happens to have a schema of the same name; it must not count.
    monkeypatch.setattr(resource_monitor, "_query_mysql_sizes_map", lambda: {"shop": 1})

    totals = resource_monitor.collect_and_enforce_mysql_quotas(db)
    assert account.size_bytes == 3 * 1024 * 1024
    assert totals[account.owner_id] == 3 * 1024 * 1024
    assert account.write_locked is True
    assert server.statements("REVOKE ALL PRIVILEGES ON `shop`.*")
