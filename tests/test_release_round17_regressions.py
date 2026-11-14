"""Regression tests for the round 17 review (main @ b147eb5)."""
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

from app.api import laravel_manager as lm
from app.api import nodejs_manager as nm
from app.api import websites
from app.core.database import Base
from app.models.entities import NodejsInstance, User, Website

HELPER = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
UPDATE_SH = REPO_ROOT / "installer" / "update.sh"


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)()


def _alice_site(db, domain="a.example.com"):
    alice = User(username="alice", email="alice@example.com", hashed_password="x", role="end_user")
    db.add(alice)
    db.commit()
    db.refresh(alice)
    site = Website(
        domain=domain,
        root_path=websites.site_users.site_root_for_panel_user("alice", domain),
        document_root="public_html",
        linux_user="alice",
        owner_id=alice.id,
        app_type="application",
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    return alice, site


def _ok():
    return SimpleNamespace(returncode=0, stdout="", stderr="")


# --------------------------------------------------------------------------- P1-2


def _rename_patches(move_calls, node_calls, laravel_calls, *, vhost_effect=None, laravel_state=None):
    return [
        patch.object(websites.site_users, "move_site_runtime", side_effect=lambda o, n, u, p=None: move_calls.append((o, n, u))),
        patch.object(websites, "_ensure_default_waf_file"),
        patch.object(websites.waf, "sync_website_rules", return_value=_ok()),
        patch.object(websites.waf, "remove_site_rules"),
        patch.object(websites.nginx, "delete_wordpress_vhost"),
        patch.object(websites, "_rewrite_website_vhost", side_effect=vhost_effect),
        patch.object(websites, "_refresh_placeholder_page_if_unchanged"),
        patch.object(websites.ssl, "release_site_certificates"),
        patch.object(websites.dns, "zone_for_new_website"),
        patch.object(nm, "_sync_nodejs_worker_unit", side_effect=lambda *a, **k: node_calls.append((a, k)) or {"status": "running"}),
        patch.object(lm, "_sync_laravel_worker_unit", side_effect=lambda *a, **k: laravel_calls.append((a, k)) or {"status": "running"}),
        patch.object(lm, "_load_toolkit_state", return_value=laravel_state or {"instances": {}}),
    ]


def _apply(patches):
    for p in patches:
        p.start()


def test_p1_2_rename_recreates_nodejs_and_laravel_worker_units_at_the_new_path():
    db = _session()
    alice, site = _alice_site(db)
    db.add(NodejsInstance(
        website_id=site.id, owner_id=alice.id, port=21010, detached=False,
        process_enabled=True, node_major="22", start_mode="node", start_arg="server.js", memory_mb=768,
    ))
    db.commit()
    move_calls, node_calls, laravel_calls = [], [], []
    state = {"instances": {str(site.id): {"subpath": "", "queue_worker": {"enabled": True, "workers": 2}}}}
    patches = _rename_patches(move_calls, node_calls, laravel_calls, laravel_state=state)
    _apply(patches)
    try:
        websites._rename_single_website(db, site, "b.example.com", alice, alice.id)
    finally:
        patch.stopall()

    new_root = websites.site_users.site_root_for_panel_user("alice", "b.example.com")
    assert move_calls and move_calls[0][1] == new_root
    assert len(node_calls) == 1
    args, kwargs = node_calls[0]
    assert args[0] == "alice"
    assert str(args[1]) == str(Path(new_root))
    assert kwargs["port"] == 21010 and kwargs["memory_mb"] == 768
    assert kwargs["enabled"] is True and kwargs["strict"] is False
    assert len(laravel_calls) == 1
    assert str(laravel_calls[0][0][1]) == str(Path(new_root))


def test_p1_2_rename_rollback_recreates_units_at_the_old_path():
    db = _session()
    alice, site = _alice_site(db)
    old_root = site.root_path
    db.add(NodejsInstance(website_id=site.id, owner_id=alice.id, port=21011, detached=False, process_enabled=True))
    db.commit()
    move_calls, node_calls, laravel_calls = [], [], []
    patches = _rename_patches(
        move_calls, node_calls, laravel_calls,
        vhost_effect=[HTTPException(status_code=500, detail="nginx failed"), None],
    )
    _apply(patches)
    try:
        with pytest.raises(HTTPException):
            websites._rename_single_website(db, site, "b.example.com", alice, alice.id)
    finally:
        patch.stopall()

    assert len(move_calls) == 2  # forward, then back
    assert len(node_calls) == 1
    assert str(node_calls[0][0][1]) == str(Path(old_root))
    assert site.domain == "a.example.com"


def test_p1_2_detached_nodejs_instance_is_left_alone():
    db = _session()
    alice, site = _alice_site(db)
    db.add(NodejsInstance(website_id=site.id, owner_id=alice.id, port=None, detached=True))
    db.commit()
    node_calls = []
    with patch.object(nm, "_sync_nodejs_worker_unit", side_effect=lambda *a, **k: node_calls.append(a)), \
         patch.object(lm, "_load_toolkit_state", return_value={"instances": {}}):
        assert websites._resync_site_workers(db, site) == []
    assert node_calls == []


def test_p1_2_failed_resync_is_reported_not_swallowed():
    db = _session()
    alice, site = _alice_site(db)
    db.add(NodejsInstance(website_id=site.id, owner_id=alice.id, port=21012, detached=False, process_enabled=True))
    db.commit()
    with patch.object(nm, "_sync_nodejs_worker_unit", return_value={"status": "error"}), \
         patch.object(lm, "_load_toolkit_state", side_effect=RuntimeError("state unreadable")):
        assert websites._resync_site_workers(db, site) == ["nodejs", "laravel-queue"]


def test_p1_2_no_reference_to_the_missing_sync_function():
    text = (REPO_ROOT / "backend" / "app" / "api" / "websites.py").read_text(encoding="utf-8")
    assert "_sync_systemd_unit" not in text


def test_p1_2_update_website_surfaces_worker_warnings_and_respects_rename_children():
    from fastapi import Response
    from app.schemas.schemas import WebsiteOut, WebsiteUpdate
    from app.services import mail as mail_service

    db = _session()
    alice, site = _alice_site(db, "a.example.com")
    child = Website(
        domain="shop.a.example.com",
        root_path=websites.site_users.site_root_for_panel_user("alice", "shop.a.example.com"),
        document_root="public_html",
        linux_user="alice",
        owner_id=alice.id,
        app_type="php",
    )
    db.add(child)
    db.add(NodejsInstance(website_id=site.id, owner_id=alice.id, port=21015, detached=False, process_enabled=True))
    db.commit()

    move_calls, node_calls, laravel_calls = [], [], []
    patches = _rename_patches(move_calls, node_calls, laravel_calls)
    # Override node worker sync to simulate a failure so warning propagation is tested
    patches[-3] = patch.object(nm, "_sync_nodejs_worker_unit", return_value={"status": "error"})
    patches.extend([
        patch.object(websites, "_hostname_conflicts", return_value=False),
        patch.object(mail_service, "webmail_host_taken", return_value=False),
    ])
    _apply(patches)
    try:
        resp = Response()
        out = websites.update_website(site.id, WebsiteUpdate(domain="b.example.com"), db, alice, response=resp)
        dto = WebsiteOut.model_validate(out)
        # Child site must NOT be renamed unless rename_children=True
        db.refresh(child)
        assert child.domain == "shop.a.example.com"
        assert dto.warnings and "nodejs" in dto.warnings[0]
        assert "nodejs" in (resp.headers.get("X-SPanel-Warning") or "")

        # Opting into rename_children=True renames matching child sites
        child_b = Website(
            domain="api.b.example.com",
            root_path=websites.site_users.site_root_for_panel_user("alice", "api.b.example.com"),
            document_root="public_html",
            linux_user="alice",
            owner_id=alice.id,
            app_type="php",
        )
        db.add(child_b)
        db.commit()
        websites.update_website(
            site.id,
            WebsiteUpdate(domain="c.example.com", rename_children=True),
            db,
            alice,
        )
        db.refresh(child_b)
        assert child_b.domain == "api.c.example.com"
    finally:
        patch.stopall()


def test_p1_2_owner_change_updates_nodejs_instance_owner_before_resync():
    from app.schemas.schemas import WebsiteUpdate

    db = _session()
    alice, site = _alice_site(db, "own.example.com")
    bob = User(username="bob", email="bob@example.com", hashed_password="x", role="end_user", website_limit=10)
    admin = User(username="admin", email="admin@example.com", hashed_password="x", role="admin")
    db.add_all([bob, admin])
    db.commit()
    db.refresh(bob)
    db.refresh(admin)
    inst_row = NodejsInstance(website_id=site.id, owner_id=alice.id, port=21016, detached=False, process_enabled=True)
    db.add(inst_row)
    db.commit()

    seen_owners = []

    def _capture_sync(linux_user, site_root, app_dir, **kwargs):
        seen_owners.append((linux_user, str(site_root), site.owner_id, inst_row.owner_id))
        return {"status": "running"}

    with patch.object(websites.site_users, "move_site_runtime"), \
         patch.object(websites.waf, "sync_website_rules", return_value=_ok()), \
         patch.object(websites, "_rewrite_website_vhost"), \
         patch.object(websites.storage_quota, "enforce_user_storage_quota"), \
         patch.object(websites.storage_quota, "website_storage_used_bytes", return_value=0), \
         patch.object(nm, "_sync_nodejs_worker_unit", side_effect=_capture_sync), \
         patch.object(lm, "_load_toolkit_state", return_value={"instances": {}}):
        websites.update_website(site.id, WebsiteUpdate(owner_id=bob.id), db, admin)

    db.refresh(inst_row)
    assert inst_row.owner_id == bob.id
    assert seen_owners == [("bob", str(Path(websites.site_users.site_root_for_panel_user("bob", "own.example.com"))), bob.id, bob.id)]


