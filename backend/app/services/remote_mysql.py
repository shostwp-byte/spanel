"""Remote MySQL / MariaDB hosts.

A customer database can live on the local MariaDB (DatabaseAccount.db_host_id
is NULL) or on one of the hosts in the remote_db_hosts table. This module owns
everything about the second kind: the host records, the connection to them,
and RemoteMySQLBackend, the remote twin of app.services.mariadb that
app.services.db_backends hands out for a remote database.

Three rules hold for every remote operation:

* Customer accounts are created for the address(es) this web server reaches
  the host from - never for '%'. An admin can name the client hosts on the
  host record; otherwise the source address of a connection to it is used.
* With use_tls the server certificate is verified, against the host's own CA
  certificate when one is stored and the system trust store otherwise, and the
  hostname is checked. A TLS connection that is not verified is only an
  encrypted connection to whoever answered.
* Every connection has a connect and read timeout, and every failure comes
  back as RemoteDatabaseError naming the host and what went wrong, never a
  bare driver traceback.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.secrets import decrypt, encrypt, is_encrypted
from app.models.entities import DatabaseAccount, RemoteDbHost
from app.services import mariadb
from app.services.shell import shell

logger = logging.getLogger("spanel.remote_mysql")

CONNECT_TIMEOUT = 8
QUERY_TIMEOUT = 30
# OPTIMIZE TABLE rebuilds the table; on a large one that is minutes.
LONG_QUERY_TIMEOUT = 900
DUMP_TIMEOUT = 6 * 3600
MAX_CA_CERT_BYTES = 64 * 1024
MAX_CLIENT_HOSTS = 16
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
# phpMyAdmin server 1 is the local MariaDB; remote hosts follow in id order.
PHPMYADMIN_FIRST_REMOTE_INDEX = 2
PHPMYADMIN_MAX_INDEX = 99

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*$"
)
_WILDCARD_HOST_RE = re.compile(r"^[A-Za-z0-9.:%_-]{1,255}$")
_ADMIN_USER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


class RemoteDatabaseError(RuntimeError):
    """A remote host could not do what was asked; the message says which and why."""


# --- validation (pure) --------------------------------------------------------

def normalize_server_host(value: str) -> str:
    """The address of a remote MySQL server: an IP address or a DNS name."""
    raw = (value or "").strip().strip("[]")
    if not raw:
        raise ValueError("Enter the IP address or hostname of the database server.")
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        if not _HOSTNAME_RE.match(raw):
            raise ValueError(f"'{value}' is not a valid IP address or hostname.") from None
        if raw.lower() in {"localhost", "localhost.localdomain"}:
            raise ValueError("The local MariaDB is already available; a remote host must be another server.")
        return raw.lower()
    if ip.is_loopback or ip.is_unspecified:
        raise ValueError("The local MariaDB is already available; a remote host must be another server.")
    return str(ip)


def validate_client_host(value: str) -> str:
    """One MySQL account host part a customer account may connect from.

    Accepts an IP address, an IPv4 address/netmask pair, a hostname, or a
    wildcard pattern over those (10.0.0.%). Refuses anything that matches every
    address, which is what this setting exists to prevent.
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Empty client host")
    if not set(raw) - set("%_.:"):
        raise ValueError(f"'{raw}' would accept connections from any address; name this server's address instead.")
    if "/" in raw:
        address, _, netmask = raw.partition("/")
        try:
            ipaddress.IPv4Address(address)
            mask = ipaddress.IPv4Address(netmask)
        except ValueError:
            raise ValueError(f"'{raw}' is not an IPv4 address/netmask pair (10.0.0.0/255.255.255.0).") from None
        if int(mask) == 0:
            raise ValueError(f"'{raw}' would accept connections from any address.")
        return raw
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        pass
    if "%" in raw or "_" in raw:
        if not _WILDCARD_HOST_RE.match(raw):
            raise ValueError(f"'{raw}' is not a valid client host pattern.")
        return raw.lower()
    if _HOSTNAME_RE.match(raw):
        return raw.lower()
    raise ValueError(f"'{raw}' is not a valid client host.")


def parse_client_hosts(raw: str) -> list[str]:
    """Comma/space separated client hosts, validated and de-duplicated."""
    hosts: list[str] = []
    for part in re.split(r"[\s,;]+", raw or ""):
        if not part:
            continue
        host = validate_client_host(part)
        if host not in hosts:
            hosts.append(host)
    if len(hosts) > MAX_CLIENT_HOSTS:
        raise ValueError(f"At most {MAX_CLIENT_HOSTS} client hosts.")
    return hosts


def validate_ca_cert(pem: str) -> str:
    """A PEM CA bundle the TLS stack accepts, or ''."""
    text = (pem or "").strip()
    if not text:
        return ""
    if len(text.encode("utf-8")) > MAX_CA_CERT_BYTES:
        raise ValueError("The CA certificate is too large (64 KB at most).")
    if "-----BEGIN CERTIFICATE-----" not in text or "PRIVATE KEY" in text:
        raise ValueError("Paste the CA certificate in PEM form (-----BEGIN CERTIFICATE-----), not a key.")
    try:
        ssl.create_default_context(cadata=text)
    except (ssl.SSLError, ValueError) as exc:
        raise ValueError(f"The CA certificate could not be read: {exc}") from exc
    return text + "\n"


# --- connection ---------------------------------------------------------------

def host_label(host: RemoteDbHost) -> str:
    return f"Remote MySQL {host.name} ({host.host}:{host.port})"


def admin_password(host: RemoteDbHost) -> str:
    stored = host.admin_password or ""
    if not stored:
        return ""
    try:
        return decrypt(stored)
    except RuntimeError as exc:
        raise RemoteDatabaseError(
            f"{host_label(host)}: the stored admin password cannot be decrypted; enter it again."
        ) from exc


def tls_context(host: RemoteDbHost) -> ssl.SSLContext:
    pem = (host.ca_cert or "").strip()
    try:
        ctx = ssl.create_default_context(cadata=pem) if pem else ssl.create_default_context()
    except (ssl.SSLError, ValueError) as exc:
        raise RemoteDatabaseError(f"{host_label(host)}: the CA certificate is not valid ({exc}).") from exc
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def describe_error(host: RemoteDbHost, exc: BaseException) -> RemoteDatabaseError:
    """Turn a driver/socket failure into one sentence an admin can act on."""
    args = getattr(exc, "args", ()) or ()
    code = args[0] if args and isinstance(args[0], int) else None
    detail = str(args[1]) if len(args) > 1 else str(exc)
    upper = detail.upper()
    if "CERTIFICATE_VERIFY_FAILED" in upper or "HOSTNAME MISMATCH" in upper or "SSL" in upper or isinstance(exc, ssl.SSLError):
        reason = (
            f"TLS verification failed ({detail}). Store the server's CA certificate on the host, "
            "and make sure the certificate names the address used here."
        )
    elif code == 1045:
        reason = f"access denied for admin user '{host.admin_user}' - check the user, password and its allowed host"
    elif code in (2003, 2002, 2013) or isinstance(exc, (socket.timeout, TimeoutError, ConnectionError)):
        reason = f"cannot reach the server ({detail})"
    elif code is not None:
        reason = f"MySQL error {code}: {detail}"
    else:
        reason = detail or exc.__class__.__name__
    return RemoteDatabaseError(f"{host_label(host)}: {reason}")


def connect(host: RemoteDbHost, *, read_timeout: int = QUERY_TIMEOUT, connect_timeout: int = CONNECT_TIMEOUT):
    """An authenticated PyMySQL connection to a remote host, as its admin user."""
    import pymysql

    kwargs: dict[str, Any] = {
        "host": host.host,
        "port": int(host.port or 3306),
        "user": host.admin_user or "root",
        "password": admin_password(host),
        "connect_timeout": connect_timeout,
        "read_timeout": read_timeout,
        "write_timeout": read_timeout,
        "charset": "utf8mb4",
        "autocommit": True,
    }
    if host.use_tls:
        kwargs["ssl"] = tls_context(host)
    try:
        return pymysql.connect(**kwargs)
    except RemoteDatabaseError:
        raise
    except Exception as exc:  # noqa: BLE001 - pymysql.err.*, OSError, ssl errors
        raise describe_error(host, exc) from exc


@contextmanager
def admin_cursor(host: RemoteDbHost, *, read_timeout: int = QUERY_TIMEOUT) -> Iterator[Any]:
    conn = connect(host, read_timeout=read_timeout)
    try:
        with conn.cursor() as cur:
            yield cur
    except RemoteDatabaseError:
        raise
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise describe_error(host, exc) from exc
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def detect_source_address(server_host: str, port: int = 3306) -> Optional[str]:
    """The local address the kernel picks to reach server_host.

    A UDP connect sends nothing; it only asks the routing table, which is
    exactly the address the remote server will see a TCP connection come from
    (barring NAT, which is what allowed_client_hosts is for).
    """
    try:
        infos = socket.getaddrinfo(server_host, int(port or 3306), proto=socket.IPPROTO_UDP)
    except OSError:
        return None
    for family, _type, _proto, _canon, sockaddr in infos:
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.connect(sockaddr)
                address = sock.getsockname()[0]
        except OSError:
            continue
        try:
            return str(ipaddress.ip_address(address.split("%", 1)[0]))
        except ValueError:
            continue
    return None


def client_hosts_for(host: RemoteDbHost) -> list[str]:
    """The account host parts a new customer account on this host gets."""
    configured = parse_client_hosts(host.allowed_client_hosts or "")
    if configured:
        return configured
    detected = detect_source_address(host.host, host.port)
    if detected:
        return [detected]
    raise RemoteDatabaseError(
        f"{host_label(host)}: cannot tell which address this server reaches it from; "
        "set 'Allowed client hosts' on the remote host."
    )


# --- SQL rendering (pure) -----------------------------------------------------

def _qi(identifier: str) -> str:
    return f"`{mariadb._validate_identifier(identifier)}`"


def render_create_statements(
    db_name: str, db_user: str, db_password: str, client_hosts: Iterable[str], *, reset_password: bool = False
) -> list[tuple[str, tuple]]:
    """CREATE DATABASE plus one account per client host, as (sql, params)."""
    hosts = [validate_client_host(h) for h in client_hosts]
    if not hosts:
        raise ValueError("No client host to create the database account for")
    statements: list[tuple[str, tuple]] = [
        (f"CREATE DATABASE IF NOT EXISTS {_qi(db_name)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci", ()),
    ]
    for client in hosts:
        statements.append(("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (db_user, client, db_password)))
        if reset_password:
            statements.append(("ALTER USER %s@%s IDENTIFIED BY %s", (db_user, client, db_password)))
        statements.append((f"GRANT ALL PRIVILEGES ON {_qi(db_name)}.* TO %s@%s", (db_user, client)))
    statements.append(("FLUSH PRIVILEGES", ()))
    return statements


def render_write_lock_statements(db_name: str, db_user: str, client_hosts: Iterable[str], locked: bool) -> list[tuple[str, tuple]]:
    statements: list[tuple[str, tuple]] = []
    for client in client_hosts:
        if locked:
            statements.append((f"REVOKE ALL PRIVILEGES ON {_qi(db_name)}.* FROM %s@%s", (db_user, client)))
            statements.append((
                f"GRANT SELECT, DELETE, DROP, REFERENCES, SHOW VIEW, TRIGGER ON {_qi(db_name)}.* TO %s@%s",
                (db_user, client),
            ))
        else:
            statements.append((f"GRANT ALL PRIVILEGES ON {_qi(db_name)}.* TO %s@%s", (db_user, client)))
    statements.append(("FLUSH PRIVILEGES", ()))
    return statements


def render_account_lock_statements(db_user: str, client_hosts: Iterable[str], locked: bool) -> list[tuple[str, tuple]]:
    clause = "ACCOUNT LOCK" if locked else "ACCOUNT UNLOCK"
    return [(f"ALTER USER %s@%s {clause}", (db_user, client)) for client in client_hosts]


def _option_value(value: str) -> str:
    """A value for a MySQL option file, quoted so no character can end it early."""
    escaped = (
        str(value).replace("\\", "\\\\").replace('"', '\\"')
        .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    )
    return f'"{escaped}"'


def render_client_defaults(
    host: RemoteDbHost,
    password: str,
    ca_path: Optional[str],
    *,
    user: Optional[str] = None,
) -> str:
    """The [client] option file mysqldump / mysql read for a remote host.

    The password goes in a 0600 file rather than argv or the environment, the
    same reason the local path pipes SQL through stdin.
    """
    eff_user = (user or "").strip() or (host.admin_user or "root")
    lines = [
        "[client]",
        f"host={normalize_server_host(host.host)}",
        f"port={int(host.port or 3306)}",
        f"user={_option_value(eff_user)}",
        f"password={_option_value(password)}",
    ]
    if host.use_tls:
        lines.append(f"ssl-ca={ca_path or SYSTEM_CA_BUNDLE}")
        lines.append("ssl-verify-server-cert")
    return "\n".join(lines) + "\n"


@contextmanager
def client_defaults_file(
    host: RemoteDbHost,
    *,
    db_user: Optional[str] = None,
    db_password: Optional[str] = None,
) -> Iterator[str]:
    workdir = Path(tempfile.mkdtemp(prefix="spanel-rdb-"))
    try:
        ca_path = None
        if host.use_tls and (host.ca_cert or "").strip():
            ca_file = workdir / "ca.pem"
            ca_file.write_text(host.ca_cert, encoding="utf-8")
            ca_path = str(ca_file)
        eff_user = (db_user or "").strip()
        if eff_user and db_password is not None:
            safe_user = mariadb._validate_identifier(eff_user)
            mariadb._reject_reserved_user(safe_user)
            eff_pass = db_password
        else:
            safe_user = None
            eff_pass = admin_password(host)
        cnf = workdir / "client.cnf"
        fd = os.open(str(cnf), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(render_client_defaults(host, eff_pass, ca_path, user=safe_user))
        yield str(cnf)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --- the backend --------------------------------------------------------------

class RemoteMySQLBackend:
    """The operations app.services.mariadb performs locally, on a remote host."""

    is_remote = True

    def __init__(self, host: RemoteDbHost):
        self.host = host

    @property
    def label(self) -> str:
        return host_label(self.host)

    def _execute(self, cur, statements: list[tuple[str, tuple]], *, tolerate: tuple[int, ...] = ()) -> None:
        for sql, params in statements:
            try:
                cur.execute(sql, params or None)
            except Exception as exc:  # noqa: BLE001
                code = exc.args[0] if getattr(exc, "args", None) and isinstance(exc.args[0], int) else None
                if code in tolerate:
                    continue
                raise

    @staticmethod
    def _account_hosts(cur, db_user: str) -> list[str]:
        """Every host part this account exists for on the server - not just
        today's client_hosts, which may have changed since it was created."""
        cur.execute("SELECT Host FROM mysql.user WHERE User = %s", (db_user,))
        hosts = []
        for row in cur.fetchall() or []:
            value = row[0]
            hosts.append(value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value))
        return hosts

    @staticmethod
    def _checked_user(db_user: str) -> str:
        safe = mariadb._validate_identifier(db_user)
        mariadb._reject_reserved_user(safe)
        return safe

    def user_exists(self, db_user: str) -> bool:
        safe = mariadb._validate_identifier(db_user)
        with admin_cursor(self.host) as cur:
            return bool(self._account_hosts(cur, safe))

    def create_database_credentials(
        self, db_name: str, db_user: str, db_password: str, *, password_hash: str | None = None,
        allow_existing_user: bool = False,
    ) -> dict[str, str]:
        db_name = mariadb._validate_identifier(db_name)
        db_user = self._checked_user(db_user)
        if password_hash:
            # Only a DirectAdmin import carries a hash, and imports go local.
            raise ValueError("A password hash can only be restored onto the local MariaDB.")
        clients = client_hosts_for(self.host)
        with admin_cursor(self.host) as cur:
            if not allow_existing_user and self._account_hosts(cur, db_user):
                raise ValueError(
                    f"Account '{db_user}' already exists on {self.label}. Choose another database user name."
                )
            self._execute(cur, render_create_statements(
                db_name, db_user, db_password, clients, reset_password=allow_existing_user,
            ))
        try:
            self.enforce_resource_limits(db_user)
        except RemoteDatabaseError:
            logger.warning("Could not set resource limits for %s on %s", db_user, self.label, exc_info=True)
        return {"db_name": db_name, "db_user": db_user, "db_password": db_password}

    def drop_database(self, db_name: str, db_user: str) -> None:
        db_name = mariadb._validate_identifier(db_name)
        db_user = self._checked_user(db_user)
        with admin_cursor(self.host) as cur:
            statements: list[tuple[str, tuple]] = [(f"DROP DATABASE IF EXISTS {_qi(db_name)}", ())]
            statements += [("DROP USER IF EXISTS %s@%s", (db_user, client)) for client in self._account_hosts(cur, db_user)]
            statements.append(("FLUSH PRIVILEGES", ()))
            self._execute(cur, statements)

    def change_password(self, db_user: str, db_password: str) -> None:
        db_user = self._checked_user(db_user)
        with admin_cursor(self.host) as cur:
            clients = self._account_hosts(cur, db_user)
            if not clients:
                raise RemoteDatabaseError(f"{self.label}: account '{db_user}' does not exist there.")
            statements = [("ALTER USER %s@%s IDENTIFIED BY %s", (db_user, client, db_password)) for client in clients]
            statements.append(("FLUSH PRIVILEGES", ()))
            self._execute(cur, statements)

    def set_write_lock(self, db_name: str, db_user: str, locked: bool) -> None:
        db_name = mariadb._validate_identifier(db_name)
        db_user = self._checked_user(db_user)
        with admin_cursor(self.host) as cur:
            clients = self._account_hosts(cur, db_user)
            # 1141: no grant to revoke - already locked, or never granted.
            self._execute(cur, render_write_lock_statements(db_name, db_user, clients, locked), tolerate=(1141, 1147))

    def set_account_locked(self, db_user: str, locked: bool) -> None:
        """ACCOUNT LOCK / UNLOCK (MariaDB >= 10.4.2, MySQL >= 5.7.6), and on
        lock end the sessions already open, which ACCOUNT LOCK leaves alone."""
        db_user = self._checked_user(db_user)
        with admin_cursor(self.host) as cur:
            clients = self._account_hosts(cur, db_user)
            self._execute(cur, render_account_lock_statements(db_user, clients, locked))
            if locked:
                cur.execute("SELECT ID FROM information_schema.PROCESSLIST WHERE USER = %s", (db_user,))
                for row in cur.fetchall() or []:
                    # 1094: the session ended on its own in between.
                    self._execute(cur, [("KILL CONNECTION %s", (int(row[0]),))], tolerate=(1094,))

    def enforce_resource_limits(self, db_user: str, max_user_connections: int = 25, max_statement_time: int = 15) -> None:
        db_user = self._checked_user(db_user)
        conns = max(5, min(500, int(max_user_connections or 25)))
        stmt_sec = max(2, min(300, int(max_statement_time or 15)))
        with admin_cursor(self.host) as cur:
            for client in self._account_hosts(cur, db_user):
                try:
                    cur.execute(
                        f"ALTER USER %s@%s WITH MAX_USER_CONNECTIONS {conns} MAX_STATEMENT_TIME {stmt_sec}",
                        (db_user, client),
                    )
                except Exception:  # noqa: BLE001 - MySQL has no MAX_STATEMENT_TIME
                    cur.execute(f"ALTER USER %s@%s WITH MAX_USER_CONNECTIONS {conns}", (db_user, client))

    def tables_breakdown(self, db_name: str) -> list[dict]:
        db_name = mariadb._validate_identifier(db_name)
        with admin_cursor(self.host) as cur:
            cur.execute(
                "SELECT TABLE_NAME, IFNULL(TABLE_ROWS, 0), IFNULL(DATA_LENGTH, 0), IFNULL(INDEX_LENGTH, 0), "
                "IFNULL(DATA_FREE, 0), IFNULL(ENGINE, 'InnoDB') FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s ORDER BY (IFNULL(DATA_LENGTH, 0) + IFNULL(INDEX_LENGTH, 0)) DESC LIMIT 40",
                (db_name,),
            )
            rows = cur.fetchall() or []
        items = []
        for name, table_rows, data_b, index_b, free_b, engine in rows:
            items.append({
                "table_name": str(name),
                "rows": int(table_rows or 0),
                "data_bytes": int(data_b or 0),
                "index_bytes": int(index_b or 0),
                "free_bytes": int(free_b or 0),
                "total_bytes": int(data_b or 0) + int(index_b or 0),
                "engine": str(engine or "InnoDB"),
            })
        return items

    def optimize(self, db_name: str) -> dict:
        db_name = mariadb._validate_identifier(db_name)
        # Table names are case sensitive on a Linux server, so only the ones
        # that are already plain identifiers are named back to it.
        tables = [t["table_name"] for t in self.tables_breakdown(db_name)[:30]
                  if t["table_name"] and mariadb.IDENTIFIER_CHARS.issuperset(t["table_name"])]
        if tables:
            with admin_cursor(self.host, read_timeout=LONG_QUERY_TIMEOUT) as cur:
                for table in tables:
                    cur.execute(f"OPTIMIZE TABLE {_qi(db_name)}.{_qi(table)}")
                    cur.fetchall()
        return {"optimized_tables": len(tables), "db_name": db_name}

    def database_sizes(self, db_names: Iterable[str]) -> dict[str, int]:
        names = [mariadb._validate_identifier(n) for n in db_names]
        if not names:
            return {}
        placeholders = ", ".join(["%s"] * len(names))
        with admin_cursor(self.host) as cur:
            cur.execute(
                "SELECT TABLE_SCHEMA, IFNULL(SUM(DATA_LENGTH + INDEX_LENGTH), 0) FROM information_schema.TABLES "
                f"WHERE TABLE_SCHEMA IN ({placeholders}) GROUP BY TABLE_SCHEMA",
                tuple(names),
            )
            rows = cur.fetchall() or []
        sizes = {name: 0 for name in names}
        sizes.update({str(schema): int(total or 0) for schema, total in rows})
        return sizes

    def user_statistics(self, db_users: Iterable[str]) -> dict[str, dict]:
        """USER_STATISTICS for our accounts; {} where the server has none
        (MySQL, or MariaDB without userstat=1)."""
        users = [u for u in db_users if u and u.lower() not in mariadb.RESERVED_DB_USERS]
        if not users:
            return {}
        placeholders = ", ".join(["%s"] * len(users))
        try:
            with admin_cursor(self.host, read_timeout=5) as cur:
                cur.execute(
                    "SELECT USER, IFNULL(CONCURRENT_CONNECTIONS, 0), IFNULL(BUSY_TIME, 0), IFNULL(CPU_TIME, 0), "
                    "IFNULL(ROWS_READ, 0), IFNULL(ROWS_SENT, 0), IFNULL(SELECT_COMMANDS, 0), IFNULL(UPDATE_COMMANDS, 0), "
                    "IFNULL(OTHER_COMMANDS, 0), IFNULL(DENIED_CONNECTIONS, 0) "
                    f"FROM information_schema.USER_STATISTICS WHERE USER IN ({placeholders})",
                    tuple(users),
                )
                rows = cur.fetchall() or []
        except RemoteDatabaseError:
            return {}
        stats: dict[str, dict] = {}
        for row in rows:
            stats[str(row[0])] = {
                "concurrent_connections": int(float(row[1] or 0)),
                "busy_time": float(row[2] or 0.0),
                "cpu_time": float(row[3] or 0.0),
                "rows_read": int(float(row[4] or 0)),
                "rows_sent": int(float(row[5] or 0)),
                "select_commands": int(float(row[6] or 0)),
                "update_commands": int(float(row[7] or 0)),
                "other_commands": int(float(row[8] or 0)),
                "denied_connections": int(float(row[9] or 0)),
            }
        return stats

    def snipe_slow_queries(self, db_users: Iterable[str], max_time_sec: int = 3) -> list[dict]:
        users = sorted({mariadb._validate_identifier(u) for u in db_users if u and u.lower() not in mariadb.RESERVED_DB_USERS})
        if not users:
            return []
        sec = max(1, min(60, int(max_time_sec or 3)))
        placeholders = ", ".join(["%s"] * len(users))
        killed: list[dict] = []
        with admin_cursor(self.host) as cur:
            cur.execute(
                "SELECT ID, USER, IFNULL(DB, ''), IFNULL(TIME, 0), IFNULL(STATE, ''), LEFT(IFNULL(INFO, ''), 160) "
                f"FROM information_schema.PROCESSLIST WHERE COMMAND = 'Query' AND TIME >= %s AND USER IN ({placeholders})",
                (sec, *users),
            )
            for qid, quser, qdb, qtime, qstate, qinfo in cur.fetchall() or []:
                info = " ".join(str(qinfo or "").split())
                if not info.upper().startswith(("SELECT", "WITH")):
                    continue
                self._execute(cur, [("KILL QUERY %s", (int(qid),))], tolerate=(1094,))
                killed.append({
                    "query_id": int(qid), "db_user": str(quser), "db_name": str(qdb),
                    "time_sec": int(float(qtime or 0)), "state": str(qstate or ""), "query": info,
                })
        return killed

    def export_database(self, db_name: str, output_file: str):
        safe_name = mariadb._validate_identifier(db_name)
        with client_defaults_file(self.host) as cnf:
            result = shell.run(
                ["mysqldump", f"--defaults-file={cnf}", "--single-transaction", safe_name, "--result-file", output_file],
                check=False,
                sensitive=True,
                timeout=DUMP_TIMEOUT,
            )
        if result.returncode != 0:
            raise RemoteDatabaseError(f"{self.label}: dumping '{safe_name}' failed: {(result.stderr or '').strip()[:500]}")
        return result

    def import_database(
        self,
        db_name: str,
        input_file: str,
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
    ):
        safe_name = mariadb._validate_identifier(db_name)
        sql_path = Path(input_file).resolve()
        if not sql_path.is_file():
            raise FileNotFoundError("SQL file not found")
        if settings.command_dry_run:
            return shell.run(["mysql", safe_name], sensitive=True)
        eff_user = (db_user or "").strip()
        eff_pass = db_password
        if not (eff_user and eff_pass):
            ctx_creds = mariadb._IMPORT_CREDS_CTX.get()
            if ctx_creds:
                eff_user, eff_pass = (ctx_creds[0] or "").strip(), ctx_creds[1]
        with (
            client_defaults_file(
                self.host,
                db_user=eff_user or None,
                db_password=eff_pass if eff_user else None,
            ) as cnf,
            mariadb._strip_sql_definers(sql_path) as clean_sql_path,
            clean_sql_path.open("rb") as source,
        ):
            try:
                completed = subprocess.run(
                    ["mysql", f"--defaults-file={cnf}", safe_name],
                    stdin=source, capture_output=True, check=False, timeout=DUMP_TIMEOUT,
                )
            except subprocess.TimeoutExpired as exc:
                raise RemoteDatabaseError(f"{self.label}: importing into '{safe_name}' timed out") from exc
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()[:500]
            raise RemoteDatabaseError(f"{self.label}: importing into '{safe_name}' failed: {stderr}")
        return completed

    def import_database_as_tenant(
        self,
        db_name: str,
        input_file: str,
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
    ):
        safe_name = mariadb._reject_reserved_database(db_name)
        eff_user, eff_pass = mariadb._resolve_tenant_import_credentials(
            safe_name, db_user=db_user, db_password=db_password
        )
        with mariadb.tenant_import_credentials(eff_user, eff_pass):
            return self.import_database(db_name, input_file)


# --- host records -------------------------------------------------------------

def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value else None


def get_host(db: Session, host_id: int) -> RemoteDbHost:
    host = db.query(RemoteDbHost).filter(RemoteDbHost.id == int(host_id)).first()
    if host is None:
        raise LookupError("Remote database host not found.")
    return host


def phpmyadmin_server_index(db: Session, host: Optional[RemoteDbHost]) -> int:
    """phpMyAdmin's $cfg['Servers'] index for a host; 1 is the local MariaDB."""
    if host is None:
        return 1
    ids = [row[0] for row in db.query(RemoteDbHost.id).order_by(RemoteDbHost.id.asc()).all()]
    try:
        index = PHPMYADMIN_FIRST_REMOTE_INDEX + ids.index(host.id)
    except ValueError:
        return 1
    return index if index <= PHPMYADMIN_MAX_INDEX else 1


def serialize_host(db: Session, host: RemoteDbHost) -> dict[str, Any]:
    """A host for the admin page. The admin password never leaves the panel,
    not even partly masked: has_password is all the form needs."""
    return {
        "id": host.id,
        "name": host.name,
        "host": host.host,
        "port": int(host.port or 3306),
        "admin_user": host.admin_user,
        "has_password": bool(host.admin_password),
        "use_tls": bool(host.use_tls),
        "ca_cert": host.ca_cert or "",
        "has_ca_cert": bool((host.ca_cert or "").strip()),
        "allowed_client_hosts": host.allowed_client_hosts or "",
        "enabled": bool(host.enabled),
        "status": host.status or "unknown",
        "latency_ms": host.latency_ms,
        "server_version": host.server_version or "",
        "last_checked_at": _iso(host.last_checked_at),
        "last_error": host.last_error or "",
        "created_at": _iso(host.created_at),
        "database_count": db.query(DatabaseAccount).filter(DatabaseAccount.db_host_id == host.id).count(),
        "phpmyadmin_server": phpmyadmin_server_index(db, host),
    }


def list_hosts(db: Session) -> list[dict[str, Any]]:
    return [serialize_host(db, h) for h in db.query(RemoteDbHost).order_by(RemoteDbHost.id.asc()).all()]


def host_choices(db: Session) -> list[dict[str, Any]]:
    """What a customer may pick from when creating a database: no credentials."""
    rows = db.query(RemoteDbHost).filter(RemoteDbHost.enabled.is_(True)).order_by(RemoteDbHost.id.asc()).all()
    return [{"id": h.id, "name": h.name, "address": f"{h.host}:{h.port}", "status": h.status or "unknown"} for h in rows]


def _apply_payload(host: RemoteDbHost, payload: dict[str, Any], *, creating: bool) -> None:
    if creating or payload.get("name") is not None:
        name = str(payload.get("name") or "").strip()[:80]
        if not name:
            raise ValueError("Enter a name for the database host.")
        host.name = name
    if creating or payload.get("host") is not None:
        host.host = normalize_server_host(str(payload.get("host") or ""))
    if creating or payload.get("port") is not None:
        port = int(payload.get("port") or 3306)
        if not 1 <= port <= 65535:
            raise ValueError("The MySQL port must be between 1 and 65535.")
        host.port = port
    if creating or payload.get("admin_user") is not None:
        admin_user = str(payload.get("admin_user") or "root").strip()
        if not _ADMIN_USER_RE.match(admin_user):
            raise ValueError("The admin user may only contain letters, digits and _ . @ -")
        host.admin_user = admin_user
    password = payload.get("admin_password")
    if password:
        password = str(password)
        host.admin_password = password if is_encrypted(password) else encrypt(password)
    elif creating:
        host.admin_password = ""
    if creating or payload.get("use_tls") is not None:
        host.use_tls = bool(payload.get("use_tls", False))
    if creating or payload.get("ca_cert") is not None:
        host.ca_cert = validate_ca_cert(str(payload.get("ca_cert") or ""))
    if creating or payload.get("allowed_client_hosts") is not None:
        host.allowed_client_hosts = ", ".join(parse_client_hosts(str(payload.get("allowed_client_hosts") or "")))
    if creating or payload.get("enabled") is not None:
        host.enabled = bool(payload.get("enabled", True))


def _ensure_unique(db: Session, host: RemoteDbHost) -> None:
    clash = db.query(RemoteDbHost).filter(
        RemoteDbHost.host == host.host, RemoteDbHost.port == host.port, RemoteDbHost.id != (host.id or 0)
    ).first()
    if clash:
        raise ValueError(f"Database host {host.host}:{host.port} is already in the list.")


def add_host(db: Session, payload: dict[str, Any]) -> RemoteDbHost:
    host = RemoteDbHost(status="unknown", server_version="", last_error="", created_at=datetime.utcnow())
    _apply_payload(host, payload, creating=True)
    _ensure_unique(db, host)
    db.add(host)
    db.commit()
    db.refresh(host)
    return host


def update_host(db: Session, host_id: int, payload: dict[str, Any]) -> RemoteDbHost:
    host = get_host(db, host_id)
    moving = (
        (payload.get("host") is not None and normalize_server_host(str(payload["host"])) != host.host)
        or (payload.get("port") is not None and int(payload["port"]) != int(host.port))
    )
    if moving and db.query(DatabaseAccount).filter(DatabaseAccount.db_host_id == host.id).count():
        # Pointing the record at another server would orphan every database on
        # this one while the panel kept believing they were there.
        raise ValueError("This host has databases on it; its address and port cannot change. Add a new host instead.")
    _apply_payload(host, payload, creating=False)
    _ensure_unique(db, host)
    db.commit()
    db.refresh(host)
    return host


def _probe_banner(server_host: str, port: int, timeout: float = 4.0) -> tuple[int, str]:
    """Read the server greeting without logging in, for a host with no password yet."""
    started = time.perf_counter()
    with socket.create_connection((server_host, int(port)), timeout=timeout) as sock:
        sock.settimeout(timeout)
        raw = sock.recv(256)
    latency_ms = max(1, int(round((time.perf_counter() - started) * 1000)))
    version = "MySQL / MariaDB"
    if len(raw) > 5 and raw[4] == 10:
        end = raw.find(b"\x00", 5)
        if end != -1:
            version = raw[5:end].decode("ascii", errors="replace").strip() or version
    return latency_ms, version


def test_host(db: Session, host_id: int) -> RemoteDbHost:
    """Log in as the admin user (or read the greeting when there is no
    password yet) and record the outcome on the host."""
    host = get_host(db, host_id)
    started = time.perf_counter()
    try:
        if host.admin_password:
            with admin_cursor(host, read_timeout=8) as cur:
                cur.execute("SELECT VERSION()")
                row = cur.fetchone()
            version = str(row[0]) if row and row[0] else "MySQL / MariaDB"
            latency = max(1, int(round((time.perf_counter() - started) * 1000)))
        else:
            latency, version = _probe_banner(host.host, host.port)
        host.status, host.latency_ms, host.server_version, host.last_error = "online", latency, version[:128], ""
    except Exception as exc:  # noqa: BLE001
        message = str(exc) if isinstance(exc, RemoteDatabaseError) else str(describe_error(host, exc))
        host.status, host.latency_ms, host.last_error = "offline", None, message[:2000]
    host.last_checked_at = datetime.utcnow()
    db.commit()
    db.refresh(host)
    return host


def delete_host(db: Session, host_id: int) -> str:
    host = get_host(db, host_id)
    count = db.query(DatabaseAccount).filter(DatabaseAccount.db_host_id == host.id).count()
    if count:
        raise ValueError(f"{count} database(s) still live on this host. Delete or move them first.")
    label = f"{host.name} ({host.host}:{host.port})"
    db.delete(host)
    db.commit()
    return label


def resolve_restore_host(db: Session, descriptor: Any) -> tuple[Optional[RemoteDbHost], str]:
    """The host a database from a backup goes back to.

    The backup names the host by address and port (ids differ between panels).
    A host still configured here gets the database back; otherwise it is
    restored on the local MariaDB and the returned note says so.
    """
    if not isinstance(descriptor, dict) or not descriptor.get("host"):
        return None, ""
    try:
        address = normalize_server_host(str(descriptor.get("host")))
        port = int(descriptor.get("port") or 3306)
    except (TypeError, ValueError):
        return None, f"backup names an invalid database host {descriptor.get('host')!r}; restored on the local MariaDB"
    host = db.query(RemoteDbHost).filter(RemoteDbHost.host == address, RemoteDbHost.port == port).first()
    if host is None:
        return None, f"remote host {address}:{port} is not configured here; restored on the local MariaDB"
    return host, ""


def backup_descriptor(account: DatabaseAccount) -> Optional[dict[str, Any]]:
    """What a backup manifest records about where a database lived."""
    host = getattr(account, "db_host", None)
    if host is None:
        return None
    return {"host": host.host, "port": int(host.port or 3306), "name": host.name}


# --- phpMyAdmin ---------------------------------------------------------------

def phpmyadmin_server_specs(hosts: Iterable[RemoteDbHost]) -> list[str]:
    """Helper arguments, one per remote host: INDEX,HOST,PORT,TLS,CA."""
    specs = []
    for position, host in enumerate(sorted(hosts, key=lambda h: h.id)):
        index = PHPMYADMIN_FIRST_REMOTE_INDEX + position
        if index > PHPMYADMIN_MAX_INDEX:
            break
        has_ca = 1 if host.use_tls and (host.ca_cert or "").strip() else 0
        specs.append(f"{index},{normalize_server_host(host.host)},{int(host.port or 3306)},{1 if host.use_tls else 0},{has_ca}")
    return specs


def sync_phpmyadmin_servers(db: Session) -> bool:
    """Regenerate phpMyAdmin's remote server list. Never raises: phpMyAdmin is
    a convenience on top of a database that works without it."""
    hosts = db.query(RemoteDbHost).order_by(RemoteDbHost.id.asc()).all()
    try:
        specs = phpmyadmin_server_specs(hosts)
        for spec in specs:
            index, _host, _port, _tls, has_ca = spec.split(",")
            if has_ca == "1":
                host = hosts[int(index) - PHPMYADMIN_FIRST_REMOTE_INDEX]
                shell.privileged(
                    "phpmyadmin-remote-ca", helper_args=[index], input=host.ca_cert, check=True,
                    fallback=["true"], timeout=20,
                )
        shell.privileged("phpmyadmin-remote-servers", helper_args=specs, check=True, fallback=["true"], timeout=20)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Could not update phpMyAdmin's remote server list", exc_info=True)
        return False
