from contextlib import contextmanager
import contextvars
from datetime import datetime
import os
from pathlib import Path
import re
import secrets
import string
import subprocess
import tempfile
from typing import Dict, Iterator, Optional

from app.core.config import settings
from app.services.shell import shell

_IMPORT_CREDS_CTX: contextvars.ContextVar[Optional[tuple[str, str]]] = contextvars.ContextVar(
    "spanel_mariadb_import_creds", default=None
)


@contextmanager
def tenant_import_credentials(db_user: Optional[str], db_password: Optional[str]) -> Iterator[None]:
    """Scope a database import to a tenant's own MySQL account instead of the panel's global ~/.my.cnf."""
    if db_user and db_password:
        token = _IMPORT_CREDS_CTX.set((db_user.strip(), db_password))
        try:
            yield
        finally:
            _IMPORT_CREDS_CTX.reset(token)
    else:
        yield


@contextmanager
def _tenant_client_defaults_file(db_user: str, db_password: str) -> Iterator[Path]:
    safe_user = _validate_identifier(db_user)
    _reject_reserved_user(safe_user)
    escaped_pw = (db_password or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    fd, raw_path = tempfile.mkstemp(prefix="spanel-mysql-import-", suffix=".cnf")
    path = Path(raw_path)
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f'[client]\nhost=localhost\nuser="{safe_user}"\npassword="{escaped_pw}"\n')
        yield path
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


IDENTIFIER_CHARS = set(string.ascii_lowercase + string.digits + "_")


def random_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#%^*_+-"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def safe_db_identifier(domain: str, prefix: str) -> str:
    clean = "".join(ch if ch.isalnum() else "_" for ch in domain.lower())[:38]
    return f"{prefix}_{clean}"[:63]


def _validate_identifier(value: str) -> str:
    if not value or len(value) > 64 or any(ch not in IDENTIFIER_CHARS for ch in value):
        raise ValueError("Invalid database identifier")
    return value


# MariaDB's own accounts, plus the one the panel authenticates as. Creating a
# database must never touch any of them, on any code path - not even a restore.
RESERVED_DB_USERS = frozenset({
    "root",
    "mysql",
    "mariadb.sys",
    "debian-sys-maint",
    "spanel",
})

RESERVED_DATABASES = frozenset({
    "mysql",
    "information_schema",
    "performance_schema",
    "sys",
    "spanel",
    "spanel_mail",
    "phpmyadmin",
    "roundcube",
    "roundcubemail",
    "pdns",
    "powerdns",
    "test",
})


def _reject_reserved_user(db_user: str) -> None:
    if db_user.strip().lower() in RESERVED_DB_USERS:
        raise ValueError(f"'{db_user}' is a reserved MariaDB account and cannot be used for a website database")


def _reject_reserved_database(db_name: str) -> str:
    safe = _validate_identifier(db_name)
    if safe.strip().lower() in RESERVED_DATABASES:
        raise ValueError(f"'{db_name}' is a reserved MariaDB database and cannot be used for a website database")
    return safe


def user_exists(db_user: str) -> bool:
    """Whether this MariaDB account already exists.

    Asked of MariaDB rather than of spanel's own table: the whole point is to
    notice accounts spanel does not know about, which is exactly what the
    table cannot tell us.
    """
    safe = _validate_identifier(db_user)
    result = _run_sql(
        f"SELECT 1 FROM mysql.user WHERE user = {_quote_sql_string(safe)} LIMIT 1;\n",
        check=False,
    )
    return "1" in (result.stdout or "")


def _quote_sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return f"`{_validate_identifier(value)}`"


_NATIVE_PASSWORD_HASH_RE = re.compile(r"^\*[0-9A-Fa-f]{40}$")


def _auth_clause(db_password: str, password_hash: str | None) -> str:
    """The IDENTIFIED ... clause for CREATE/ALTER USER.

    When a mysql_native_password hash is given (DirectAdmin keeps the original
    in its <db>.conf), the user is recreated with that exact hash so the
    imported site's existing config keeps working untouched. The hash is a
    fixed shape (``*`` + 40 hex) and is validated before it reaches any SQL.
    """
    if password_hash:
        if not _NATIVE_PASSWORD_HASH_RE.match(password_hash):
            raise ValueError("Invalid mysql_native_password hash")
        return f"IDENTIFIED VIA mysql_native_password USING '{password_hash}'"
    return f"IDENTIFIED BY {_quote_sql_string(db_password)}"


def _mysql_args(extra: list = None) -> list:
    args = ["mysql"]
    home_cnf = Path.home() / ".my.cnf"
    if home_cnf.exists():
        args.append(f"--defaults-file={home_cnf}")
    if extra:
        args.extend(extra)
    return args


def _run_sql(sql: str, *, check: bool = True):
    """Pipe SQL through stdin so secrets never appear in argv/ps output."""
    return shell.run(_mysql_args(), check=check, input=sql, sensitive=True)


def create_database(seed: str, prefix: str = "wp", db_name: str | None = None, if_not_exists: bool = True) -> Dict[str, str]:
    db_name = _reject_reserved_database(db_name or safe_db_identifier(seed, prefix))
    db_user = _validate_identifier(safe_db_identifier(db_name, "u"))
    _reject_reserved_user(db_user)
    db_password = random_password()
    create_clause = "CREATE DATABASE IF NOT EXISTS" if if_not_exists else "CREATE DATABASE"
    sql = (
        f"{create_clause} {_quote_identifier(db_name)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        f"CREATE USER IF NOT EXISTS {_quote_sql_string(db_user)}@'localhost' IDENTIFIED BY {_quote_sql_string(db_password)};\n"
        f"ALTER USER {_quote_sql_string(db_user)}@'localhost' IDENTIFIED BY {_quote_sql_string(db_password)};\n"
        f"GRANT ALL PRIVILEGES ON {_quote_identifier(db_name)}.* TO {_quote_sql_string(db_user)}@'localhost';\n"
        "FLUSH PRIVILEGES;\n"
    )
    _run_sql(sql)
    enforce_db_user_resource_limits(db_user)
    return {"db_name": db_name, "db_user": db_user, "db_password": db_password}


def create_database_credentials(
    db_name: str, db_user: str, db_password: str, *, password_hash: str | None = None,
    allow_existing_user: bool = False,
) -> Dict[str, str]:
    """Create a database and the account that owns it.

    `CREATE USER IF NOT EXISTS` followed by an unconditional `ALTER USER` used
    to mean "create it, or take it over". Since the panel authenticates to
    MariaDB with ALL PRIVILEGES ON *.*, any panel user who asked for
    db_user=root got root's password reset to a value of their choosing - and
    the API handed it back in the response. On a stock Ubuntu box root is
    `IDENTIFIED VIA mysql_native_password USING 'invalid' OR unix_socket`, so
    the ALTER also drops the socket clause and locks the system's own root out
    of MariaDB.

    Creating now refuses an account that already exists. `allow_existing_user`
    is for the restore paths - a backup or a DirectAdmin import legitimately
    recreates the account that archive already owned - and even those cannot
    touch a reserved account.
    """
    db_name = _reject_reserved_database(db_name)
    db_user = _validate_identifier(db_user)
    _reject_reserved_user(db_user)

    if not allow_existing_user and user_exists(db_user):
        raise ValueError(
            f"MariaDB account '{db_user}' already exists. Choose another database user name."
        )

    auth = _auth_clause(db_password, password_hash)
    statements = [
        f"CREATE DATABASE IF NOT EXISTS {_quote_identifier(db_name)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
        f"CREATE USER IF NOT EXISTS {_quote_sql_string(db_user)}@'localhost' {auth};",
    ]
    if allow_existing_user:
        # Only a restore sets the password of an account that was already there.
        statements.append(f"ALTER USER {_quote_sql_string(db_user)}@'localhost' {auth};")
    statements += [
        f"GRANT ALL PRIVILEGES ON {_quote_identifier(db_name)}.* TO {_quote_sql_string(db_user)}@'localhost';",
        "FLUSH PRIVILEGES;",
    ]
    _run_sql("\n".join(statements) + "\n")
    enforce_db_user_resource_limits(db_user)
    return {"db_name": db_name, "db_user": db_user, "db_password": db_password}


def drop_database(db_name: str, db_user: str):
    safe_db = _reject_reserved_database(db_name)
    _reject_reserved_user(_validate_identifier(db_user))
    sql = (
        f"DROP DATABASE IF EXISTS {_quote_identifier(safe_db)};\n"
        f"DROP USER IF EXISTS {_quote_sql_string(_validate_identifier(db_user))}@'localhost';\n"
        "FLUSH PRIVILEGES;\n"
    )
    return _run_sql(sql)


def change_database_password(db_user: str, db_password: str):
    _reject_reserved_user(_validate_identifier(db_user))
    sql = (
        f"ALTER USER {_quote_sql_string(_validate_identifier(db_user))}@'localhost' "
        f"IDENTIFIED BY {_quote_sql_string(db_password)};\n"
        "FLUSH PRIVILEGES;\n"
    )
    return _run_sql(sql)


def export_database(db_name: str, output_file: str):
    args = ["mysqldump"]
    home_cnf = Path.home() / ".my.cnf"
    if home_cnf.exists():
        args.append(f"--defaults-file={home_cnf}")
    args.extend([_reject_reserved_database(db_name), "--result-file", output_file])
    return shell.run(args, sensitive=True)


_SQL_DEFINER_RE = re.compile(
    rb"DEFINER\s*=\s*(?:`[^`]+`|'[^']+'|\"[^\"]+\"|[A-Za-z0-9_.$]+)\s*@\s*(?:`[^`]+`|'[^']+'|\"[^\"]+\"|[A-Za-z0-9_.%$-]+)\s*",
    re.IGNORECASE,
)


@contextmanager
def _strip_sql_definers(sql_path: Path) -> Iterator[Path]:
    """Copy sql_path line-by-line to a 0600 temporary file with DEFINER clauses removed."""
    fd, raw_tmp = tempfile.mkstemp(prefix="spanel-sql-import-", suffix=".sql")
    tmp_path = Path(raw_tmp)
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "wb") as dst, sql_path.open("rb") as src:
            for raw_line in src:
                cleaned = _SQL_DEFINER_RE.sub(b"", raw_line)
                if cleaned != raw_line:
                    cleaned = re.sub(rb"/\*![0-9]{5}\s*\*/\s*", b"", cleaned)
                dst.write(cleaned)
        yield tmp_path
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _resolve_tenant_import_credentials(
    safe_name: str,
    db_user: Optional[str] = None,
    db_password: Optional[str] = None,
) -> tuple[str, str]:
    eff_user = (db_user or "").strip()
    eff_pass = db_password
    if not (eff_user and eff_pass):
        ctx_creds = _IMPORT_CREDS_CTX.get()
        if ctx_creds:
            eff_user, eff_pass = (ctx_creds[0] or "").strip(), ctx_creds[1]
    if not (eff_user and eff_pass):
        acc = None
        try:
            from app.core.database import SessionLocal
            from app.models.entities import DatabaseAccount

            with SessionLocal() as session:
                acc = session.query(DatabaseAccount).filter(DatabaseAccount.db_name == safe_name).first()
                if acc is not None:
                    session.expunge(acc)
        except Exception:
            acc = None
        if acc and acc.db_user and acc.db_password:
            from app.core.secrets import decrypt

            eff_user = acc.db_user.strip()
            eff_pass = decrypt(acc.db_password)
    if not (eff_user and eff_pass):
        raise RuntimeError(
            f"Database import refused: missing tenant database credentials for '{safe_name}'"
        )
    safe_user = _validate_identifier(eff_user)
    _reject_reserved_user(safe_user)
    return safe_user, eff_pass


def import_database_as_tenant(
    db_name: str,
    input_file: str,
    db_user: Optional[str] = None,
    db_password: Optional[str] = None,
):
    safe_name = _reject_reserved_database(db_name)
    sql_path = Path(input_file).resolve()
    if not sql_path.exists() or not sql_path.is_file():
        raise FileNotFoundError("SQL file not found")
    if settings.command_dry_run:
        return shell.run(["mysql", safe_name], sensitive=True)

    eff_user, eff_pass = _resolve_tenant_import_credentials(
        safe_name, db_user=db_user, db_password=db_password
    )
    with _tenant_client_defaults_file(eff_user, eff_pass) as cnf_path, _strip_sql_definers(sql_path) as clean_sql_path:
        with clean_sql_path.open("rb") as source:
            args = ["mysql", f"--defaults-file={cnf_path}", "--binary-mode", safe_name]
            completed = subprocess.run(args, stdin=source, capture_output=True, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"Database import failed: {stderr.strip()}")
    return completed


def import_database_as_panel(db_name: str, input_file: str):
    """Import a SQL dump using the panel's global MariaDB credentials.

    Restricted to ephemeral panel-owned databases (such as backup_verify's
    throwaway spanel_verify_* databases) that have no tenant DatabaseAccount.
    """
    if import_database is not _default_import_database:
        return import_database(db_name, input_file)
    safe_name = _reject_reserved_database(db_name)
    sql_path = Path(input_file).resolve()
    if not sql_path.exists() or not sql_path.is_file():
        raise FileNotFoundError("SQL file not found")
    if settings.command_dry_run:
        return shell.run(["mysql", safe_name], sensitive=True)

    args = _mysql_args(["--binary-mode", safe_name])
    with _strip_sql_definers(sql_path) as clean_sql_path, clean_sql_path.open("rb") as source:
        completed = subprocess.run(args, stdin=source, capture_output=True, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"Database import failed: {stderr.strip()}")
    return completed


def import_database(
    db_name: str,
    input_file: str,
    *,
    db_user: Optional[str] = None,
    db_password: Optional[str] = None,
):
    return import_database_as_tenant(
        db_name,
        input_file,
        db_user=db_user,
        db_password=db_password,
    )


_default_import_database = import_database


def dump_database_file(db_name: str, output_dir: Path) -> Path:
    safe_name = _reject_reserved_database(db_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{safe_name}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.sql"
    export_database(safe_name, str(target))
    if settings.command_dry_run and not target.exists():
        target.write_text(f"-- DRY RUN database dump for {safe_name}\n", encoding="utf-8")
    return target



def set_database_write_lock(db_name: str, db_user: str, locked: bool) -> None:
    """Toggle write privileges (INSERT, UPDATE, CREATE, ALTER, INDEX) for a database user.

    When locked=True, the database enters Read-Only + Cleanup mode:
    SELECT, DELETE, DROP, and SHOW VIEW remain granted so the website stays online
    and the customer can still DELETE/TRUNCATE/DROP bloated tables via phpMyAdmin.
    """
    safe_db = _reject_reserved_database(db_name)
    safe_user = _validate_identifier(db_user)
    _reject_reserved_user(safe_user)
    q_db = _quote_identifier(safe_db)
    q_user = f"{_quote_sql_string(safe_user)}@'localhost'"

    if locked:
        sql = (
            f"REVOKE ALL PRIVILEGES ON {q_db}.* FROM {q_user};\n"
            f"GRANT SELECT, DELETE, DROP, REFERENCES, SHOW VIEW, TRIGGER ON {q_db}.* TO {q_user};\n"
            "FLUSH PRIVILEGES;\n"
        )
    else:
        sql = (
            f"GRANT ALL PRIVILEGES ON {q_db}.* TO {q_user};\n"
            "FLUSH PRIVILEGES;\n"
        )
    _run_sql(sql, check=False)


def account_lock_sql(db_user: str, locked: bool, client_host: str = "localhost") -> str:
    """ALTER USER ... ACCOUNT LOCK/UNLOCK (MariaDB >= 10.4.2, MySQL >= 5.7.6)."""
    safe_user = _validate_identifier(db_user)
    _reject_reserved_user(safe_user)
    clause = "ACCOUNT LOCK" if locked else "ACCOUNT UNLOCK"
    return f"ALTER USER {_quote_sql_string(safe_user)}@{_quote_sql_string(client_host)} {clause};\n"


def set_account_locked(db_user: str, locked: bool) -> None:
    """Lock or unlock a customer's MariaDB account for suspend/unsuspend.

    ACCOUNT LOCK only refuses new logins, so on lock the sessions the account
    already has open are killed too - otherwise a suspended site's persistent
    connections keep working until they happen to reconnect.
    """
    safe_user = _validate_identifier(db_user)
    result = _run_sql(account_lock_sql(safe_user, locked), check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not {'lock' if locked else 'unlock'} MariaDB account '{safe_user}': {(result.stderr or '').strip()}"
        )
    if not locked or settings.command_dry_run:
        return
    res = _run_sql(
        f"SELECT ID FROM information_schema.PROCESSLIST WHERE USER = {_quote_sql_string(safe_user)};\n",
        check=False,
    )
    kills = [f"KILL CONNECTION {line.strip()};" for line in (res.stdout or "").splitlines() if line.strip().isdigit()]
    if kills:
        _run_sql("\n".join(kills) + "\n", check=False)


def get_database_tables_breakdown(db_name: str) -> list[dict]:
    """Return top tables by size (data + index bytes) inside a specific database."""
    safe_db = _reject_reserved_database(db_name)
    if settings.command_dry_run:
        return []
    sql = (
        "SELECT TABLE_NAME, IFNULL(TABLE_ROWS, 0), IFNULL(DATA_LENGTH, 0), "
        "IFNULL(INDEX_LENGTH, 0), IFNULL(DATA_FREE, 0), IFNULL(ENGINE, 'InnoDB') "
        f"FROM information_schema.TABLES WHERE TABLE_SCHEMA = {_quote_sql_string(safe_db)} "
        "ORDER BY (IFNULL(DATA_LENGTH, 0) + IFNULL(INDEX_LENGTH, 0)) DESC LIMIT 40;\n"
    )
    res = _run_sql(sql, check=False)
    items = []
    for idx, line in enumerate((res.stdout or "").splitlines()):
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        if idx == 0 and parts[0] == "TABLE_NAME":
            continue
        try:
            data_b = int(parts[2] or 0)
            index_b = int(parts[3] or 0)
            free_b = int(parts[4] or 0)
            items.append({
                "table_name": parts[0],
                "rows": int(parts[1] or 0),
                "data_bytes": data_b,
                "index_bytes": index_b,
                "free_bytes": free_b,
                "total_bytes": data_b + index_b,
                "engine": parts[5] or "InnoDB",
            })
        except ValueError:
            continue
    return items


def optimize_database(db_name: str) -> dict:
    """Run OPTIMIZE TABLE on tables in db_name to reclaim fragmented InnoDB/MyISAM space."""
    safe_db = _reject_reserved_database(db_name)
    tables = get_database_tables_breakdown(safe_db)
    if not tables:
        return {"optimized_tables": 0, "db_name": safe_db}
    stmts = [f"OPTIMIZE TABLE {_quote_identifier(safe_db)}.{_quote_identifier(t['table_name'])};" for t in tables[:30] if IDENTIFIER_CHARS.issuperset(t['table_name'].lower())]
    if stmts:
        _run_sql("\n".join(stmts) + "\n", check=False)
    return {"optimized_tables": len(stmts), "db_name": safe_db}



def enforce_db_user_resource_limits(
    db_user: str,
    max_user_connections: int = 25,
    max_statement_time: int = 15,
) -> None:
    """Enforce MariaDB per-user MAX_USER_CONNECTIONS and query timeout so one attacked site
    can never exhaust global max_connections or lock up MariaDB for other users."""
    safe_user = _validate_identifier(db_user)
    _reject_reserved_user(safe_user)
    if settings.command_dry_run:
        return
    q_user = f"{_quote_sql_string(safe_user)}@'localhost'"
    conns = max(5, min(500, int(max_user_connections or 25)))
    stmt_sec = max(2, min(300, int(max_statement_time or 15)))
    res = _run_sql(
        f"ALTER USER {q_user} WITH MAX_USER_CONNECTIONS {conns} MAX_STATEMENT_TIME {stmt_sec};\n",
        check=False,
    )
    if res.returncode != 0:
        _run_sql(
            f"ALTER USER {q_user} WITH MAX_USER_CONNECTIONS {conns};\n",
            check=False,
        )



MAX_HOURLY_LIMIT = 1_000_000_000


def _hourly_value(value) -> int:
    """0 (or less, or junk) is "no limit", which is also MariaDB's 0."""
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_HOURLY_LIMIT, number))


def hourly_limits_sql(
    db_user: str,
    max_queries_per_hour: int = 0,
    max_updates_per_hour: int = 0,
    max_connections_per_hour: int = 0,
) -> str:
    """ALTER USER setting the three per-hour resource limits of one account.

    All three are always set, so lowering a package to "no limit" lifts a
    limit set earlier instead of leaving it behind. MAX_USER_CONNECTIONS and
    MAX_STATEMENT_TIME (enforce_db_user_resource_limits) are left as they are.
    """
    safe_user = _validate_identifier(db_user)
    _reject_reserved_user(safe_user)
    return (
        f"ALTER USER {_quote_sql_string(safe_user)}@'localhost' WITH"
        f" MAX_QUERIES_PER_HOUR {_hourly_value(max_queries_per_hour)}"
        f" MAX_UPDATES_PER_HOUR {_hourly_value(max_updates_per_hour)}"
        f" MAX_CONNECTIONS_PER_HOUR {_hourly_value(max_connections_per_hour)};\n"
    )


def owner_hourly_limits(owner) -> tuple[int, int, int]:
    """The hourly limits a panel user's package gave them."""
    return (
        _hourly_value(getattr(owner, "mysql_max_queries_per_hour", 0)),
        _hourly_value(getattr(owner, "mysql_max_updates_per_hour", 0)),
        _hourly_value(getattr(owner, "mysql_max_connections_per_hour", 0)),
    )


def enforce_db_user_hourly_limits(
    db_user: str,
    max_queries_per_hour: int = 0,
    max_updates_per_hour: int = 0,
    max_connections_per_hour: int = 0,
) -> bool:
    sql = hourly_limits_sql(db_user, max_queries_per_hour, max_updates_per_hour, max_connections_per_hour)
    if settings.command_dry_run:
        return True
    return _run_sql(sql, check=False).returncode == 0


def ensure_mysql_governor_settings() -> None:
    """Enable MariaDB userstat=1 (per-user CPU/row statistics) and InnoDB Buffer Pool scan resistance
    (innodb_old_blocks_time=1000, innodb_old_blocks_pct=37) so a scraped site cannot evict other users' hot RAM cache."""
    if settings.command_dry_run:
        return
    try:
        shell.privileged(
            "mysql-governor-ensure",
            helper_args=[],
            check=False,
            timeout=10,
            fallback=["true"],
        )
    except Exception:
        pass
    _run_sql(
        "SET GLOBAL userstat = 1; SET GLOBAL innodb_old_blocks_pct = 37; SET GLOBAL innodb_old_blocks_time = 1000;\n",
        check=False,
    )


def collect_user_statistics() -> dict[str, dict]:
    """Read cumulative per-DB-user CPU_TIME, BUSY_TIME, ROWS_READ, and query counts from information_schema.USER_STATISTICS."""
    if settings.command_dry_run:
        return {}
    sql = (
        "SELECT USER, IFNULL(CONCURRENT_CONNECTIONS, 0), IFNULL(BUSY_TIME, 0), IFNULL(CPU_TIME, 0), "
        "IFNULL(ROWS_READ, 0), IFNULL(ROWS_SENT, 0), IFNULL(SELECT_COMMANDS, 0), IFNULL(UPDATE_COMMANDS, 0), "
        "IFNULL(OTHER_COMMANDS, 0), IFNULL(DENIED_CONNECTIONS, 0) "
        "FROM information_schema.USER_STATISTICS;\n"
    )
    res = _run_sql(sql, check=False)
    stats: dict[str, dict] = {}
    for idx, line in enumerate((res.stdout or "").splitlines()):
        parts = line.strip().split("\t")
        if len(parts) < 10:
            continue
        if idx == 0 and parts[0].upper() == "USER":
            continue
        u_name = parts[0].strip()
        if not u_name or u_name.lower() in RESERVED_DB_USERS:
            continue
        try:
            stats[u_name] = {
                "concurrent_connections": int(float(parts[1] or 0)),
                "busy_time": float(parts[2] or 0.0),
                "cpu_time": float(parts[3] or 0.0),
                "rows_read": int(float(parts[4] or 0)),
                "rows_sent": int(float(parts[5] or 0)),
                "select_commands": int(float(parts[6] or 0)),
                "update_commands": int(float(parts[7] or 0)),
                "other_commands": int(float(parts[8] or 0)),
                "denied_connections": int(float(parts[9] or 0)),
            }
        except ValueError:
            continue
    return stats


def snipe_slow_scraping_queries(db_users: list[str] | set[str], max_time_sec: int = 3) -> list[dict]:
    """Identify and KILL slow/heavy SELECT queries belonging to specific db_users that are holding up MariaDB CPU/IO."""
    safe_users = {_validate_identifier(u) for u in db_users if u and u.lower() not in RESERVED_DB_USERS}
    if not safe_users or settings.command_dry_run:
        return []
    sec = max(1, min(60, int(max_time_sec or 3)))
    sql = (
        "SELECT ID, USER, IFNULL(DB, ''), IFNULL(TIME, 0), IFNULL(STATE, ''), "
        "LEFT(REPLACE(REPLACE(IFNULL(INFO, ''), '\n', ' '), '\t', ' '), 160) "
        f"FROM information_schema.PROCESSLIST WHERE COMMAND = 'Query' AND TIME >= {sec};\n"
    )
    res = _run_sql(sql, check=False)
    killed: list[dict] = []
    kill_stmts: list[str] = []
    for idx, line in enumerate((res.stdout or "").splitlines()):
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        if idx == 0 and parts[0].upper() == "ID":
            continue
        qid_str, quser, qdb, qtime_str, qstate, qinfo = parts[:6]
        if quser.strip() not in safe_users or not qid_str.strip().isdigit():
            continue
        qinfo_clean = qinfo.strip()
        if not qinfo_clean.upper().startswith(("SELECT", "WITH")):
            continue
        qid = int(qid_str.strip())
        kill_stmts.append(f"KILL QUERY {qid};")
        killed.append({
            "query_id": qid,
            "db_user": quser.strip(),
            "db_name": qdb.strip(),
            "time_sec": int(float(qtime_str or 0)),
            "state": qstate.strip(),
            "query": qinfo_clean,
        })
    if kill_stmts:
        _run_sql("\n".join(kill_stmts) + "\n", check=False)
    return killed
