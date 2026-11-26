"""Regression tests for Round 21 independent audit findings (R1..R4)."""
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


# --------------------------------------------------------------------------- R1


def test_r1_failed_owner_change_and_transfer_rollback_persists_consistent_db_state(tmp_path, monkeypatch):
    """Round 21 R1:
    When reverse move fails during owner change (update_website) or ownership
    transfer (transfer_website_owner), DB MUST commit owner_id + root_path +
    linux_user + NodejsInstance.owner_id aligned with the moved files on disk,
    verified from a brand-new SQLAlchemy session (never relying on uncommitted
    in-memory state). When reverse move succeeds, DB MUST remain on old owner.
    """
    from app.api import nodejs_manager as nm
    from app.api import websites as websites_api
    from app.core.permissions import Role
    from app.models.entities import NodejsInstance, User, Website
    from app.schemas.schemas import WebsiteUpdate

    monkeypatch.setattr(nm, "_deploy_locks_dir", lambda: tmp_path)
    SessionLocal = _make_session()
    with SessionLocal() as db:
        admin = User(username="r1admin", email="r1a@example.com", hashed_password="x", role=Role.admin)
        alice = User(username="r1alice", email="r1al@example.com", hashed_password="x", role=Role.end_user)
        bob = User(username="r1bob", email="r1bo@example.com", hashed_password="x", role=Role.end_user)
        db.add_all([admin, alice, bob])
        db.commit()
        db.refresh(admin)
        db.refresh(alice)
        db.refresh(bob)

        # 1. update_website: reverse move fails -> new owner/path + NodejsInstance.owner_id persisted
        site1 = Website(
            domain="r1-upd.example.com",
            root_path="/home/r1alice/r1-upd.example.com",
            linux_user="r1alice",
            owner_id=alice.id,
            app_type="nodejs",
        )
        db.add(site1)
        db.commit()
        db.refresh(site1)
        node1 = NodejsInstance(website_id=site1.id, owner_id=alice.id, port=3101)
        db.add(node1)
        db.commit()

        moves = {"n": 0}

        def _move_fail_on_2(old_p, new_p, user, php_v=None):
            moves["n"] += 1
            if moves["n"] == 2:
                raise RuntimeError("reverse move failed")

        with patch.object(websites_api.storage_quota, "enforce_user_storage_quota"), \
             patch.object(websites_api.storage_quota, "website_storage_used_bytes", return_value=0), \
             patch.object(websites_api.site_users, "move_site_runtime", side_effect=_move_fail_on_2), \
             patch.object(websites_api.waf, "sync_website_rules", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")), \
             patch.object(websites_api, "_rewrite_website_vhost", side_effect=RuntimeError("vhost fail")):
            with pytest.raises(HTTPException):
                websites_api.update_website(site1.id, WebsiteUpdate(owner_id=bob.id), db=db, current_user=admin)

    # Read from brand-new session (old session closed without calling commit externally)
    with SessionLocal() as verify_db:
        s1 = verify_db.query(Website).filter(Website.id == site1.id).first()
        n1 = verify_db.query(NodejsInstance).filter(NodejsInstance.website_id == site1.id).first()
        assert s1.owner_id == bob.id
        assert s1.linux_user == "r1bob"
        assert s1.root_path.replace("\\", "/").endswith("/r1bob/r1-upd.example.com")
        assert n1.owner_id == bob.id

    # 2. transfer_website_owner: reverse move fails -> persisted to target_owner with no cross-tenant split
    with SessionLocal() as db:
        site2 = Website(
            domain="r1-tr.example.com",
            root_path="/home/r1alice/r1-tr.example.com",
            linux_user="r1alice",
            owner_id=alice.id,
            app_type="nodejs",
        )
        db.add(site2)
        db.commit()
        db.refresh(site2)
        node2 = NodejsInstance(website_id=site2.id, owner_id=alice.id, port=3102)
        db.add(node2)
        db.commit()

        tr_moves = {"n": 0}

        def _tr_move_fail_on_2(old_p, new_p, user, php_v=None):
            tr_moves["n"] += 1
            if tr_moves["n"] == 2:
                raise RuntimeError("transfer reverse move failed")

        with patch.object(websites_api.storage_quota, "enforce_user_storage_quota"), \
             patch.object(websites_api.storage_quota, "website_storage_used_bytes", return_value=0), \
             patch.object(websites_api.site_users, "move_site_runtime", side_effect=_tr_move_fail_on_2), \
             patch.object(websites_api, "_ensure_default_waf_file"), \
             patch.object(websites_api, "_rewrite_website_vhost", side_effect=RuntimeError("vhost fail")):
            with pytest.raises(HTTPException) as exc_tr:
                websites_api.transfer_website_owner(
                    site2.id,
                    websites_api.WebsiteTransferOwnerPayload(target_owner_id=bob.id),
                    SimpleNamespace(),
                    db=db,
                    current_user=admin,
                )
        assert "transfer reverse move failed" in exc_tr.value.detail

    with SessionLocal() as verify_db:
        s2 = verify_db.query(Website).filter(Website.id == site2.id).first()
        n2 = verify_db.query(NodejsInstance).filter(NodejsInstance.website_id == site2.id).first()
        assert s2.owner_id == bob.id
        assert s2.linux_user == "r1bob"
        assert s2.root_path.replace("\\", "/").endswith("/r1bob/r1-tr.example.com")
        assert n2.owner_id == bob.id

    # 3. Happy rollback (reverse move succeeds) -> fresh session still sees old owner/path
    with SessionLocal() as db:
        site3 = Website(
            domain="r1-ok.example.com",
            root_path="/home/r1alice/r1-ok.example.com",
            linux_user="r1alice",
            owner_id=alice.id,
            app_type="php",
            php_version="8.4",
        )
        db.add(site3)
        db.commit()
        db.refresh(site3)
        site3_id = site3.id

        with patch.object(websites_api.storage_quota, "enforce_user_storage_quota"), \
             patch.object(websites_api.storage_quota, "website_storage_used_bytes", return_value=0), \
             patch.object(websites_api.site_users, "move_site_runtime"), \
             patch.object(websites_api.waf, "sync_website_rules", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")), \
             patch.object(websites_api, "_ensure_default_waf_file"), \
             patch.object(websites_api, "_rewrite_website_vhost", side_effect=[RuntimeError("forward fail"), None]), \
             patch.object(websites_api, "_resync_site_workers", return_value=[]):
            with pytest.raises(HTTPException):
                websites_api.update_website(site3_id, WebsiteUpdate(owner_id=bob.id), db=db, current_user=admin)

    with SessionLocal() as verify_db:
        s3 = verify_db.query(Website).filter(Website.id == site3_id).first()
        assert s3.owner_id == alice.id
        assert s3.linux_user == "r1alice"
        assert s3.root_path == "/home/r1alice/r1-ok.example.com"


# --------------------------------------------------------------------------- R2


def test_r2_delete_user_acquires_all_site_locks_before_deleting_anything(tmp_path, monkeypatch):
    """Round 21 R2:
    delete_user must acquire site_operation_lock for every website owned by the
    user BEFORE deleting anything.
    1) If the 2nd site has an active Node.js lock, delete_user returns 409,
       deletes NEITHER site nor the user, and releases the 1st site's lock.
    2) When idle, all owned site locks stay held throughout _delete_owned_website
       (blocking concurrent deploys) and are released upon completion.
    """
    from app.api import nodejs_manager as nm
    from app.api import users as users_api
    from app.core.permissions import Role
    from app.models.entities import User, Website

    monkeypatch.setattr(nm, "_deploy_locks_dir", lambda: tmp_path)
    SessionLocal = _make_session()
    with SessionLocal() as db:
        admin = User(username="r2admin", email="r2a@example.com", hashed_password="x", role=Role.admin)
        victim = User(username="r2victim", email="r2v@example.com", hashed_password="x", role=Role.end_user)
        db.add_all([admin, victim])
        db.commit()
        db.refresh(admin)
        db.refresh(victim)

        site1 = Website(
            domain="r2-one.example.com",
            root_path="/home/r2victim/r2-one.example.com",
            linux_user="r2victim",
            owner_id=victim.id,
            app_type="nodejs",
        )
        site2 = Website(
            domain="r2-two.example.com",
            root_path="/home/r2victim/r2-two.example.com",
            linux_user="r2victim",
            owner_id=victim.id,
            app_type="nodejs",
        )
        db.add_all([site1, site2])
        db.commit()
        db.refresh(site1)
        db.refresh(site2)
        s1_id, s2_id, v_id = site1.id, site2.id, victim.id

        # 1. site2 is busy -> delete_user fails 409 BEFORE touching site1 or victim
        deleted_sites: list[str] = []
        with nm.site_operation_lock(s2_id, "npm:install"):
            with patch.object(users_api, "enforce_rate_limit"), \
                 patch.object(users_api, "_delete_owned_website", side_effect=lambda sdb, w: deleted_sites.append(w.domain)):
                with pytest.raises(HTTPException) as exc_busy:
                    users_api.delete_user(v_id, SimpleNamespace(), db=db, current_user=admin)
            assert exc_busy.value.status_code == 409
            assert deleted_sites == []
            # site1's lock acquired earlier in the loop MUST have been released cleanly
            assert nm._get_site_operation_status(s1_id)["active"] is False

        # Verify both sites and user still exist in DB
        assert db.query(Website).filter(Website.owner_id == v_id).count() == 2
        assert db.query(User).filter(User.id == v_id).first() is not None

        # 2. When idle, locks for BOTH site1 & site2 are held mid-deletion
        mid_flight_locked = {"s1": False, "s2": False}

        def _check_locks_during_delete(sdb, w):
            for key, sid in (("s1", s1_id), ("s2", s2_id)):
                with pytest.raises(HTTPException) as exc_c:
                    nm._acquire_deploy_lock(sid, operation="git-deploy")
                assert exc_c.value.status_code == 409
                mid_flight_locked[key] = True
            sdb.delete(w)

        with patch.object(users_api, "enforce_rate_limit"), \
             patch.object(users_api, "_delete_owned_website", side_effect=_check_locks_during_delete), \
             patch.object(users_api.teardown, "purge_owned_resources", return_value={"databases": [], "applications": []}), \
             patch.object(users_api.mail, "delete_for_owner"), \
             patch.object(users_api.mail, "sync_quietly"), \
             patch.object(users_api.site_users, "delete_panel_user"), \
             patch.object(users_api, "log_action"):
            res = users_api.delete_user(v_id, SimpleNamespace(), db=db, current_user=admin)
            assert res["ok"] is True

        assert mid_flight_locked == {"s1": True, "s2": True}
        assert nm._get_site_operation_status(s1_id)["active"] is False
        assert nm._get_site_operation_status(s2_id)["active"] is False

