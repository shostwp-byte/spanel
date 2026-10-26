import datetime as dt
import re
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

connect_args = {"check_same_thread": False, "timeout": 15} if settings.database_url.startswith("sqlite") else {}
kwargs = {'connect_args': connect_args}
if not settings.database_url.startswith('sqlite'):
    kwargs['pool_recycle'] = 3600
engine = create_engine(settings.database_url, **kwargs)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=15000;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA temp_store=MEMORY;")
        except Exception:
            pass
        finally:
            cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _alembic_config():
    """Build an Alembic Config bound to the application's database URL.

    Imported lazily so non-migration code paths don't pay the alembic import
    cost.
    """
    from alembic.config import Config

    here = Path(__file__).resolve().parents[2]  # backend/
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


def _has_legacy_schema() -> bool:
    """Return True if the DB already contains spanel tables but has never
    been stamped by Alembic. This is the case for servers that ran the old
    apply_simple_migrations() bootstrap before we adopted Alembic."""
    inspector = inspect(engine)
    return inspector.has_table("users") and not inspector.has_table("alembic_version")


def run_migrations() -> None:
    """Bring the schema up to date with the latest Alembic revision.

    - Fresh install (empty DB): runs every migration from 0001 onwards.
    - Legacy install (tables exist, alembic_version absent): stamps the DB
      to '0001_initial' (matching the schema as it existed before Alembic),
      then upgrades to head. No DDL is replayed for tables that already
      exist with the correct columns.
    - Already-managed install: upgrades from current revision to head.
    """
    from alembic import command

    cfg = _alembic_config()
    if _has_legacy_schema():
        command.stamp(cfg, "0001_initial")
    command.upgrade(cfg, "head")
    import app.models.entities  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _ensure_resource_limit_columns()
    try:
        from app.api.websites import reconcile_websites_from_disk
        with SessionLocal() as db:
            reconcile_websites_from_disk(db)
    except Exception:
        pass


def _ensure_resource_limit_columns() -> None:
    """Ensure Cgroups v2 resource limit & MySQL quota columns exist on existing DBs."""
    table_columns = {
        "user_packages": [
            ("cpu_quota_percent", "INTEGER NOT NULL DEFAULT 100"),
            ("io_read_mbps", "INTEGER NOT NULL DEFAULT 0"),
            ("io_write_mbps", "INTEGER NOT NULL DEFAULT 0"),
            ("iops_read_max", "INTEGER NOT NULL DEFAULT 0"),
            ("iops_write_max", "INTEGER NOT NULL DEFAULT 0"),
            ("memory_high_mb", "INTEGER NOT NULL DEFAULT 1024"),
            ("memory_max_mb", "INTEGER NOT NULL DEFAULT 2048"),
            ("tasks_max", "INTEGER NOT NULL DEFAULT 512"),
            ("mysql_storage_limit_mb", "INTEGER NOT NULL DEFAULT 0"),
            ("mysql_max_db_size_mb", "INTEGER NOT NULL DEFAULT 0"),
            ("mysql_max_queries_per_hour", "INTEGER NOT NULL DEFAULT 0"),
            ("mysql_max_updates_per_hour", "INTEGER NOT NULL DEFAULT 0"),
            ("mysql_max_connections_per_hour", "INTEGER NOT NULL DEFAULT 0"),
        ],
        "users": [
            ("bandwidth_limit_mb", "INTEGER NOT NULL DEFAULT 0"),
            ("inode_limit", "INTEGER NOT NULL DEFAULT 0"),
            ("database_limit", "INTEGER NOT NULL DEFAULT 5"),
            ("cpu_quota_percent", "INTEGER NOT NULL DEFAULT 100"),
            ("io_read_mbps", "INTEGER NOT NULL DEFAULT 0"),
            ("io_write_mbps", "INTEGER NOT NULL DEFAULT 0"),
            ("iops_read_max", "INTEGER NOT NULL DEFAULT 0"),
            ("iops_write_max", "INTEGER NOT NULL DEFAULT 0"),
            ("memory_high_mb", "INTEGER NOT NULL DEFAULT 1024"),
            ("memory_max_mb", "INTEGER NOT NULL DEFAULT 2048"),
            ("tasks_max", "INTEGER NOT NULL DEFAULT 512"),
            ("mysql_storage_limit_mb", "INTEGER NOT NULL DEFAULT 0"),
            ("mysql_max_db_size_mb", "INTEGER NOT NULL DEFAULT 0"),
            ("mysql_max_queries_per_hour", "INTEGER NOT NULL DEFAULT 0"),
            ("mysql_max_updates_per_hour", "INTEGER NOT NULL DEFAULT 0"),
            ("mysql_max_connections_per_hour", "INTEGER NOT NULL DEFAULT 0"),
        ],
        "database_accounts": [
            ("size_bytes", "INTEGER NOT NULL DEFAULT 0"),
            ("write_locked", "BOOLEAN NOT NULL DEFAULT 0"),
            ("write_locked_reason", "VARCHAR(255) DEFAULT NULL"),
        ],
        "sftp_backup_targets": [
            ("owner_id", "INTEGER DEFAULT NULL"),
        ],
        "backup_schedules": [
            ("backup_mode", "VARCHAR(16) NOT NULL DEFAULT 'full'"),
            ("full_every_n", "INTEGER NOT NULL DEFAULT 7"),
        ],
        "audit_logs": [
            ("prev_hash", "VARCHAR(64) DEFAULT ''"),
            ("entry_hash", "VARCHAR(64) DEFAULT ''"),
        ],
    }
    try:
        inspector = inspect(engine)
        with engine.begin() as conn:
            for table_name, cols in table_columns.items():
                if not inspector.has_table(table_name):
                    continue
                existing = {c["name"] for c in inspector.get_columns(table_name)}
                for col_name, col_ddl in cols:
                    if col_name not in existing:
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_ddl}"))
    except Exception:
        pass


DB_BACKUP_DIR = Path("/var/lib/spanel/db-backups")
# What run_sqlite_hot_backup names its files; the helper's panel-db-restore
# accepts exactly this.
DB_BACKUP_NAME_RE = re.compile(r"^spanel-[0-9]{8}T[0-9]{6}Z-[a-z0-9_-]{1,24}\.db$")


def check_sqlite_backup(name: str) -> dict:
    """Read-only integrity_check of one hot backup, before a restore is asked
    of the helper (which checks again as root before it stops anything)."""
    if not DB_BACKUP_NAME_RE.fullmatch(name or ""):
        raise ValueError("Not a database backup name")
    path = DB_BACKUP_DIR / name
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("Database backup not found")
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=15)
        try:
            result = str(conn.execute("PRAGMA integrity_check;").fetchone()[0])
            tables = int(conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0])
            revision = ""
            try:
                revision = ",".join(r[0] for r in conn.execute("SELECT version_num FROM alembic_version"))
            except sqlite3.Error:
                pass
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"ok": False, "integrity": str(exc)[:200], "tables": 0, "revision": ""}
    return {"ok": result.lower() == "ok" and tables > 0, "integrity": result, "tables": tables, "revision": revision}


def _resolve_sqlite_path() -> Path | None:
    if not settings.database_url.startswith("sqlite:///"):
        return None
    raw = settings.database_url[len("sqlite:///"):]
    return Path(raw).resolve()


def get_sqlite_health() -> dict:
    db_path = _resolve_sqlite_path()
    info = {
        "engine": "sqlite" if settings.database_url.startswith("sqlite") else "other",
        "db_path": str(db_path) if db_path else settings.database_url,
        "journal_mode": "unknown",
        "synchronous": "unknown",
        "busy_timeout": 0,
        "foreign_keys": 0,
        "size_bytes": 0,
        "wal_size_bytes": 0,
        "shm_size_bytes": 0,
        "integrity_ok": True,
        "integrity_detail": "ok",
        "backup_dir": str(DB_BACKUP_DIR),
        "last_backup_at": None,
        "backup_count": 0,
        "backups": [],
    }
    if not settings.database_url.startswith("sqlite"):
        return info

    try:
        with engine.connect() as conn:
            jm = conn.execute(text("PRAGMA journal_mode;")).scalar()
            sync = conn.execute(text("PRAGMA synchronous;")).scalar()
            bt = conn.execute(text("PRAGMA busy_timeout;")).scalar()
            fk = conn.execute(text("PRAGMA foreign_keys;")).scalar()
            integ = conn.execute(text("PRAGMA quick_check;")).scalar()
            info["journal_mode"] = str(jm or "").upper()
            sync_map = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}
            info["synchronous"] = sync_map.get(sync, str(sync))
            info["busy_timeout"] = int(bt or 0)
            info["foreign_keys"] = int(fk or 0)
            info["integrity_detail"] = str(integ or "ok")
            info["integrity_ok"] = str(integ or "").lower() == "ok"
    except Exception as exc:
        info["integrity_ok"] = False
        info["integrity_detail"] = str(exc)[:200]

    if db_path and db_path.exists():
        try:
            info["size_bytes"] = db_path.stat().st_size
            wal_path = Path(str(db_path) + "-wal")
            shm_path = Path(str(db_path) + "-shm")
            if wal_path.exists():
                info["wal_size_bytes"] = wal_path.stat().st_size
            if shm_path.exists():
                info["shm_size_bytes"] = shm_path.stat().st_size
        except Exception:
            pass

    if DB_BACKUP_DIR.exists():
        try:
            files = sorted(DB_BACKUP_DIR.glob("spanel-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
            info["backup_count"] = len(files)
            if files:
                latest_mtime = dt.datetime.fromtimestamp(files[0].stat().st_mtime, tz=dt.timezone.utc)
                info["last_backup_at"] = latest_mtime.strftime("%Y-%m-%dT%H:%M:%SZ")
            info["backups"] = [
                {
                    "filename": f.name,
                    "size_bytes": f.stat().st_size,
                    "created_at": dt.datetime.fromtimestamp(f.stat().st_mtime, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                for f in files[:10]
            ]
        except Exception:
            pass
    return info


def run_sqlite_hot_backup(reason: str = "manual", keep: int = 14) -> dict:
    db_path = _resolve_sqlite_path()
    if not db_path or not db_path.exists():
        return {"ok": False, "detail": "SQLite database file not found"}

    safe_reason = "".join(ch for ch in (reason or "manual").lower() if ch.isalnum() or ch in ("-", "_"))[:24] or "manual"
    now_str = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    DB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target_path = DB_BACKUP_DIR / f"spanel-{now_str}-{safe_reason}.db"

    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(PASSIVE);"))
    except Exception:
        pass

    try:
        src_conn = sqlite3.connect(str(db_path), timeout=15)
        dst_conn = sqlite3.connect(str(target_path), timeout=15)
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
            cur = dst_conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            check_res = cur.fetchone()
            integrity = str(check_res[0] if check_res else "ok")
            cur.close()
        finally:
            dst_conn.close()
            src_conn.close()
    except Exception:
        try:
            shutil.copy2(db_path, target_path)
            integrity = "copied"
        except Exception as exc:
            return {"ok": False, "detail": f"Hot backup failed: {exc}"}

    # Rotate older backups
    try:
        files = sorted(DB_BACKUP_DIR.glob("spanel-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old_file in files[max(1, keep):]:
            try:
                old_file.unlink()
            except Exception:
                pass
    except Exception:
        pass

    return {
        "ok": integrity.lower() in ("ok", "copied"),
        "filename": target_path.name,
        "path": str(target_path),
        "size_bytes": target_path.stat().st_size if target_path.exists() else 0,
        "integrity": integrity,
        "created_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
