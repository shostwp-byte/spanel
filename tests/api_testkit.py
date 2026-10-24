"""Shared fixtures for the provisioning API / webhook / WHMCS tests.

Not a test module itself (no ``test_`` prefix): the tests/test_api_*.py files
import it. It builds a small FastAPI app with only the routers under test, so
importing app.main (which starts the panel's daemons) is never needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
import app.models.entities as entities  # noqa: E402,F401

_CLEAN_TABLES = (
    "provisioning_idempotency_keys",
    "webhook_outbox",
    "webhook_endpoints",
    "provisioning_accounts",
    "database_accounts",
    "websites",
    "api_tokens",
    "audit_logs",
    "users",
    "user_packages",
    "reseller_packages",
)


def reset_db() -> None:
    Base.metadata.create_all(bind=engine)
    from sqlalchemy import inspect, text

    existing = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        for table in _CLEAN_TABLES:
            if table in existing:
                conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("PRAGMA foreign_keys=ON"))
    from app.core import rate_limit

    with rate_limit._LOCK:
        rate_limit._BUCKETS.clear()


def build_app(current_user_id: int | None = None) -> FastAPI:
    from app.api import provisioning, resellers
    from app.api.deps import get_current_user

    app = FastAPI()
    app.include_router(provisioning.router, prefix="/api")
    app.include_router(resellers.router, prefix="/api")
    if current_user_id is not None:
        def _current_user():
            with SessionLocal() as db:
                user = db.get(entities.User, current_user_id)
                db.expunge(user)
                return user

        app.dependency_overrides[get_current_user] = _current_user
    return app


def client(current_user_id: int | None = None) -> TestClient:
    return TestClient(build_app(current_user_id))


def make_user(db, username: str, role: str = "admin", **extra) -> entities.User:
    user = entities.User(
        username=username,
        email=f"{username}@example.test",
        hashed_password="x",
        role=role,
        **extra,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # Detached with its columns loaded, so later commits do not expire it.
    db.expunge(user)
    return user


def make_package(db, name: str = "Basic", **extra) -> entities.UserPackage:
    package = entities.UserPackage(name=name, slug=name.lower(), **extra)
    db.add(package)
    db.commit()
    db.refresh(package)
    db.expunge(package)
    return package


def make_token(db, scopes: str = "provisioning:read,provisioning:write", allowed_ips: str = "", **extra) -> str:
    from app.services.provisioning import create_api_token

    raw, token = create_api_token(db, "test", scopes, allowed_ips, **extra)
    return raw


def bearer(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}
