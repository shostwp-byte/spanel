"""Regression tests for Round 20 independent audit findings (N1..N7)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))


def _make_session():
    from app.core.database import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


# --------------------------------------------------------------------------- N1


def test_n1_live_lock_overrides_completed_status_file_and_blocks_website_mutations(tmp_path, monkeypatch):
    """Round 20 N1:
    1) Even when a prior background deploy left a status=succeeded file, a live
       per-site lock (e.g. sync npm:install) MUST be detected as active=True by
       _get_site_operation_status and ensure_no_active_nodejs_operation.
    2) _rename_single_website, delete_website, transfer_website_owner, and
       update_website(owner_id=...) MUST hold the per-site lock across their
       entire mutation (no TOCTOU window where git-deploy can sneak in).
    """
    from app.api import nodejs_manager as nm
    from app.api import websites as websites_api
    from app.core.permissions import Role
    from app.models.entities import User, Website
    from app.schemas.schemas import WebsiteUpdate

    monkeypatch.setattr(nm, "_deploy_locks_dir", lambda: tmp_path)
    SessionLocal = _make_session()
    with SessionLocal() as db:
        admin = User(username="n1admin", email="n1a@example.com", hashed_password="x", role=Role.admin)
        alice = User(username="n1alice", email="n1al@example.com", hashed_password="x", role=Role.end_user)
        bob = User(username="n1bob", email="n1bo@example.com", hashed_password="x", role=Role.end_user)
        db.add_all([admin, alice, bob])
        db.commit()
        db.refresh(admin)
        db.refresh(alice)
        db.refresh(bob)

        site = Website(
            domain="n1-site.example.com",
            root_path="/home/n1alice/domains/n1-site.example.com",
            linux_user="n1alice",
            owner_id=alice.id,
            app_type="nodejs",
        )
        db.add(site)
        db.commit()
        db.refresh(site)

        # Simulate a prior background git-deploy that finished cleanly
        nm._write_site_operation_status(
            site.id,
            operation="git-deploy",
            status="succeeded",
            phase="completed",
            message="done",
        )
        assert nm._get_site_operation_status(site.id)["active"] is False

        # Now a synchronous endpoint holds the lock without writing a status file
        with nm.site_operation_lock(site.id, "npm:install"):
            st = nm._get_site_operation_status(site.id)
            assert st["active"] is True
            assert st["operation"] == "npm:install"

            with pytest.raises(HTTPException) as exc_chk:
                nm.ensure_no_active_nodejs_operation(site.id)
            assert exc_chk.value.status_code == 409

            with pytest.raises(HTTPException) as exc_ren:
                websites_api._rename_single_website(db, site, "n1-renamed.example.com", alice, alice.id)
            assert exc_ren.value.status_code == 409

            with pytest.raises(HTTPException) as exc_del:
                websites_api.delete_website(site.id, SimpleNamespace(), True, True, db=db, current_user=admin)
            assert exc_del.value.status_code == 409

            with pytest.raises(HTTPException) as exc_tr:
                websites_api.transfer_website_owner(
                    site.id,
                    websites_api.WebsiteTransferOwnerPayload(target_owner_id=bob.id),
                    SimpleNamespace(),
                    db=db,
                    current_user=admin,
                )
            assert exc_tr.value.status_code == 409

            with pytest.raises(HTTPException) as exc_upd:
                websites_api.update_website(
                    site.id,
                    WebsiteUpdate(owner_id=bob.id),
                    db=db,
                    current_user=admin,
                )
            assert exc_upd.value.status_code == 409

        # Verify the lock is actually HELD during mid-flight website rename (no TOCTOU)
        concurrent_blocked = {"hit": False}

        def _mid_flight_move(old_p, new_p, user, php_v=None):
            with pytest.raises(HTTPException) as exc_conc:
                nm._acquire_deploy_lock(site.id, operation="git-deploy")
            assert exc_conc.value.status_code == 409
            concurrent_blocked["hit"] = True
            raise RuntimeError("abort after verifying lock held")

        with patch.object(websites_api.site_users, "move_site_runtime", side_effect=_mid_flight_move):
            with pytest.raises(HTTPException):
                websites_api._rename_single_website(db, site, "n1-renamed.example.com", alice, alice.id)
        assert concurrent_blocked["hit"] is True
        # Lock must be released after rename exits
        assert nm._get_site_operation_status(site.id)["active"] is False

        # Verify the lock is actually HELD during mid-flight delete_website
        delete_blocked = {"hit": False}

        def _mid_flight_vhost_del(domain):
            with pytest.raises(HTTPException) as exc_conc:
                nm._acquire_deploy_lock(site.id, operation="git-deploy")
            assert exc_conc.value.status_code == 409
            delete_blocked["hit"] = True

        with patch.object(websites_api.nginx, "delete_wordpress_vhost", side_effect=_mid_flight_vhost_del), \
             patch.object(websites_api.ssl, "release_site_certificates", return_value=""), \
             patch.object(websites_api.waf, "remove_site_rules"), \
             patch.object(websites_api.site_users, "delete_site_runtime"), \
             patch.object(websites_api.teardown, "purge_website_sftp_accounts"), \
             patch.object(websites_api.storage_quota, "forget_user_storage"), \
             patch.object(websites_api, "log_action"):
            res = websites_api.delete_website(site.id, SimpleNamespace(), True, False, db=db, current_user=admin)
            assert res["ok"] is True
        assert delete_blocked["hit"] is True
        assert nm._get_site_operation_status(site.id)["active"] is False
