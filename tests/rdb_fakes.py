"""Shared fakes for the remote MySQL tests: an in-memory panel database and a
fake MySQL server that PyMySQL's connect() is patched to return."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402


def make_session():
    from app.core.database import Base
    import app.models.entities  # noqa: F401

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)()


def make_user(db, username="alice", role="end_user"):
    from app.models.entities import User

    user = User(username=username, email=f"{username}@example.test", hashed_password="x", role=role)
    db.add(user)
    db.commit()
    return user


def make_host(db, **overrides):
    from app.core.secrets import encrypt
    from app.models.entities import RemoteDbHost

    values = dict(
        name="db1", host="10.0.0.5", port=3306, admin_user="root", admin_password=encrypt("adminpw"),
        use_tls=False, ca_cert="", allowed_client_hosts="10.0.0.10", enabled=True,
    )
    values.update(overrides)
    host = RemoteDbHost(**values)
    db.add(host)
    db.commit()
    return host


class FakeMySQLServer:
    """Just enough of a MySQL server for the statements RemoteMySQLBackend sends."""

    def __init__(self, version="10.11.6-MariaDB"):
        self.version = version
        self.accounts: dict[str, list[str]] = {}
        self.locked: set[tuple[str, str]] = set()
        self.processlist: list[tuple] = []
        self.sizes: dict[str, int] = {}
        self.tables: list[tuple] = []
        self.executed: list[tuple[str, tuple]] = []
        self.connect_kwargs: list[dict] = []
        self.fail_on: dict[str, Exception] = {}

    def connect(self, **kwargs):
        self.connect_kwargs.append(kwargs)
        return FakeConnection(self)

    def statements(self, prefix=""):
        return [(sql, params) for sql, params in self.executed if sql.startswith(prefix)]


class FakeConnection:
    def __init__(self, server):
        self.server = server
        self.closed = False

    def cursor(self):
        return FakeCursor(self.server)

    def close(self):
        self.closed = True


class FakeCursor:
    def __init__(self, server):
        self.server = server
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        params = tuple(params or ())
        self.server.executed.append((sql, params))
        for prefix, exc in self.server.fail_on.items():
            if sql.startswith(prefix):
                raise exc
        s = self.server
        self._rows = []
        if sql.startswith("SELECT Host FROM mysql.user"):
            self._rows = [(h,) for h in s.accounts.get(params[0], [])]
        elif sql.startswith("CREATE USER IF NOT EXISTS"):
            hosts = s.accounts.setdefault(params[0], [])
            if params[1] not in hosts:
                hosts.append(params[1])
        elif sql.startswith("DROP USER IF EXISTS"):
            hosts = s.accounts.get(params[0], [])
            if params[1] in hosts:
                hosts.remove(params[1])
            if not hosts:
                s.accounts.pop(params[0], None)
        elif sql.startswith("ALTER USER") and "ACCOUNT LOCK" in sql:
            s.locked.add((params[0], params[1]))
        elif sql.startswith("ALTER USER") and "ACCOUNT UNLOCK" in sql:
            s.locked.discard((params[0], params[1]))
        elif sql.startswith("SELECT ID FROM information_schema.PROCESSLIST"):
            self._rows = [(row[0],) for row in s.processlist if row[1] == params[0]]
        elif sql.startswith("SELECT ID, USER"):
            self._rows = [row for row in s.processlist if row[1] in params[1:] and row[3] >= params[0]]
        elif sql.startswith("SELECT TABLE_SCHEMA"):
            self._rows = [(name, s.sizes[name]) for name in params if name in s.sizes]
        elif sql.startswith("SELECT TABLE_NAME"):
            self._rows = list(s.tables)
        elif sql.startswith("SELECT VERSION()"):
            self._rows = [(s.version,)]
        return len(self._rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None
