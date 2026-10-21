from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
kwargs = {'connect_args': connect_args}
if not settings.database_url.startswith('sqlite'):
    kwargs['pool_recycle'] = 3600
engine = create_engine(settings.database_url, **kwargs)
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
