"""Unit and behavioral regression tests for the End-User Node.js Manager module (v1.0.9)."""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    import pytest
except ImportError:
    class _RaisesContext:
        def __init__(self, expected_exc):
            self.expected_exc = expected_exc
            self.value = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                raise AssertionError(f"Expected {self.expected_exc.__name__} to be raised")
            if issubclass(exc_type, self.expected_exc):
                self.value = exc_val
                return True
            return False

    pytest = SimpleNamespace(raises=lambda exc: _RaisesContext(exc))

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.api import nodejs_manager as nm
from app.core.database import Base
from app.models.entities import DatabaseAccount, User, Website
from app.services import site_apps

HELPER_PATH = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
APP_JSX_PATH = REPO_ROOT / "frontend" / "src" / "App.jsx"
NODEJS_PAGE_PATH = REPO_ROOT / "frontend" / "src" / "pages" / "NodejsManager.jsx"


def _make_request(headers: Optional[Dict[str, str]] = None):
    return SimpleNamespace(headers=headers or {})


def _setup_test_db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    SessionLocal = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return eng, SessionLocal


def test_rbac_access_role_enforcement():
    admin_user = SimpleNamespace(id=1, username="admin", role="admin")
    reseller_user = SimpleNamespace(id=2, username="reseller1", role="reseller")
    end_user = SimpleNamespace(id=3, username="alice", role="end_user")

    # Admin without X-Access-Role: user must be rejected with 403
    with pytest.raises(HTTPException) as exc_info:
        nm._require_end_user_nodejs_access(admin_user, _make_request({}))
    assert exc_info.value.status_code == 403

    # Reseller without X-Access-Role: user must be rejected with 403
    with pytest.raises(HTTPException) as exc_info:
        nm._require_end_user_nodejs_access(reseller_user, _make_request({"x-access-role": "reseller"}))
    assert exc_info.value.status_code == 403

    # Admin with X-Access-Role: user succeeds
    nm._require_end_user_nodejs_access(admin_user, _make_request({"x-access-role": "user"}))

    # End user directly succeeds
    nm._require_end_user_nodejs_access(end_user, _make_request({}))


def test_menu_order_nodejs_before_laravel():
    app_jsx = APP_JSX_PATH.read_text(encoding="utf-8")
    nodejs_idx = app_jsx.find("['nodejs_manager', 'Node.js', NodejsIcon]")
    laravel_idx = app_jsx.find("['laravel_manager', 'Laravel', LaravelIcon]")
    wordpress_idx = app_jsx.find("['wordpress_manager', 'WordPress Manager', WordPressIcon]")
    assert nodejs_idx != -1, "Node.js Manager menu entry missing from App.jsx"
    assert laravel_idx != -1, "Laravel Manager menu entry missing from App.jsx"
    assert wordpress_idx != -1, "WordPress Manager menu entry missing from App.jsx"
    assert nodejs_idx < laravel_idx < wordpress_idx, (
        "Node.js must be positioned before Laravel in the End User Application menu"
    )
    assert NODEJS_PAGE_PATH.is_file(), "NodejsManager.jsx page component must exist"


def test_helper_verbs_present_in_spanel_helper():
    helper_text = HELPER_PATH.read_text(encoding="utf-8")
    for verb in (
        "nodejs-worker-sync)",
        "nodejs-worker-status)",
        "nodejs-worker-logs)",
        "nodejs-worker-delete)",
        "--node-version=*)",
        "delete_nodejs_worker_units_for_user",
    ):
        assert verb in helper_text, f"Expected `{verb}` in spanel-helper.sh"


def test_full_nodejs_lifecycle_install_process_npm_env_security_remove():
    eng, SessionLocal = _setup_test_db()
    with tempfile.TemporaryDirectory() as tmpdir, patch.object(nm, "engine", eng):
        nm.invalidate_nodejs_inspect_cache()
        db = SessionLocal()
        try:
            user = User(
                username="nodeuser",
                email="nodeuser@example.com",
                hashed_password="x",
                role="end_user",
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            site_root = Path(tmpdir) / "domains" / "nodeapp.example.com"
            pub_dir = site_root / "public_html"
            pub_dir.mkdir(parents=True, exist_ok=True)

            site = Website(
                domain="nodeapp.example.com",
                root_path=str(site_root),
                document_root="public_html",
                linux_user="nodeuser",
                php_version="8.3",
                app_type="php",
                ssl_enabled=True,
                owner_id=user.id,
            )
            db.add(site)
            db.commit()
            db.refresh(site)

            # 1. Install Node.js app from Express template with database creation
            with patch("app.services.mariadb.create_database_credentials") as mock_db_create, patch(
                "app.api.websites._rewrite_website_vhost"
            ) as mock_vhost:
                install_res = nm.install_nodejs_instance(
                    nm.NodejsInstallRequest(
                        website_id=site.id,
                        app_name="My Express API",
                        install_source="template",
                        template="express-api",
                        node_major="22",
                        start_mode="node",
                        start_arg="server.js",
                        memory_mb=512,
                        create_database=True,
                        db_name="nd_test_db",
                        db_user="u_nd_test",
                        db_password="StrongPassword123!",
                    ),
                    db=db,
                    current_user=user,
                )

            assert install_res["instance"]["domain"] == "nodeapp.example.com"
            assert install_res["instance"]["framework"] == "Express.js"
            assert install_res["instance"]["node_major"] == "22"
            assert install_res["instance"]["status"] == "running"
            assert 21000 <= install_res["instance"]["port"] <= 21999
            assert install_res["credentials"]["db_created"] is True
            assert (pub_dir / "package.json").is_file()
            assert (pub_dir / "server.js").is_file()
            assert (pub_dir / ".env").is_file()
            mock_db_create.assert_called_once()

            # Verify site_apps port integration
            allocated_port = install_res["instance"]["port"]
            assert nm.get_nodejs_port_for_website(site.id) == allocated_port
            assert allocated_port in nm.reserved_nodejs_ports()
            assert site_apps.app_port_for_website(site) == allocated_port

            # 2. List instances (verify fast cache hit works)
            list_res = nm.list_nodejs_instances(refresh=False, db=db, current_user=user)
            assert len(list_res["instances"]) == 1
            assert list_res["instances"][0]["port"] == allocated_port

            # 3. Process control: stop -> start -> configure
            stop_res = nm.manage_nodejs_process(
                site.id,
                nm.ProcessControlRequest(action="stop"),
                db=db,
                current_user=user,
            )
            assert stop_res["instance"]["status"] == "stopped"

            cfg_res = nm.manage_nodejs_process(
                site.id,
                nm.ProcessControlRequest(
                    action="start",
                    node_major="20",
                    start_mode="npm",
                    start_arg="start",
                    memory_mb=1024,
                ),
                db=db,
                current_user=user,
            )
            assert cfg_res["instance"]["status"] == "running"
            assert cfg_res["instance"]["node_major"] == "20"
            assert cfg_res["instance"]["start_mode"] == "npm"
            assert cfg_res["instance"]["memory_mb"] == 1024

            # 4. NPM package management: add & remove package
            npm_add = nm.run_npm_action(
                site.id,
                nm.NpmActionRequest(action="add", package="cors@^2.8.5"),
                db=db,
                current_user=user,
            )
            pkg_names = [p["name"] for p in npm_add["instance"]["packages"]]
            assert "cors" in pkg_names

            npm_rem = nm.run_npm_action(
                site.id,
                nm.NpmActionRequest(action="remove", package="cors"),
                db=db,
                current_user=user,
            )
            pkg_names_after = [p["name"] for p in npm_rem["instance"]["packages"]]
            assert "cors" not in pkg_names_after

            # 5. .env get & update
            env_get = nm.get_nodejs_env(site.id, db=db, current_user=user)
            assert env_get["parsed"]["app_name"] == "My Express API"

            env_put = nm.update_nodejs_env(
                site.id,
                nm.EnvUpdateRequest(
                    mode="structured",
                    updates={"APP_NAME": "Updated Node API", "LOG_LEVEL": "debug"},
                ),
                db=db,
                current_user=user,
            )
            assert env_put["parsed"]["app_name"] == "Updated Node API"
            assert env_put["parsed"]["log_level"] == "debug"

            # 6. Security checks & 1-click apply_all
            sec_res = nm.update_nodejs_security(
                site.id,
                nm.SecurityActionRequest(apply_all=True, enabled=True),
                db=db,
                current_user=user,
            )
            assert sec_res["instance"]["security_score"] >= 85

            # 7. Logs read & clear
            logs_res = nm.get_nodejs_logs(site.id, kind="systemd", lines=50, db=db, current_user=user)
            assert "nodeapp.example.com" in logs_res["content"]
            nm.clear_nodejs_logs(site.id, db=db, current_user=user)

            # 8. Detach & Re-scan
            nm.detach_nodejs_instance(site.id, db=db, current_user=user)
            after_detach = nm.list_nodejs_instances(refresh=True, db=db, current_user=user)
            assert len(after_detach["instances"]) == 0

            scan_res = nm.scan_nodejs_instances(db=db, current_user=user)
            assert len(scan_res["instances"]) == 1

            # 9. Remove instance completely
            with patch("app.services.teardown.drop_database_record") as mock_drop_db, patch(
                "app.api.websites._rewrite_website_vhost"
            ):
                nm.remove_nodejs_instance(
                    site.id,
                    nm.RemoveNodejsRequest(delete_files=True, drop_database=True),
                    db=db,
                    current_user=user,
                )
            mock_drop_db.assert_called_once()
            after_remove = nm.list_nodejs_instances(refresh=True, db=db, current_user=user)
            assert len(after_remove["instances"]) == 0
        finally:
            db.close()
