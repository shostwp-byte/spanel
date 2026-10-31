"""Behavioral matrix tests for unified package-first count quotas (P2-5)."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("SPANEL_SECRET_KEY", "test-secret-key-for-quota-unified-matrix-32b")


def _make_sqlite_session():
    from app.core.database import Base
    import app.models.entities  # noqa: F401

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, Session()


def _ok_proc(*_args, **_kwargs):
    return subprocess.CompletedProcess(args=["stub"], returncode=0, stdout="", stderr="")


def test_effective_limit_contract_matrix():
    from app.services import quota

    with pytest.raises(ValueError):
        quota.effective_limit(None, "nonexistent_limit")

    admin = SimpleNamespace(id=1, role="admin", package=None, website_limit=0)
    for field in quota.QUOTA_FIELDS:
        assert quota.effective_limit(admin, field) == -1
        assert quota.effective_limit(None, field) == -1

    for field in quota.QUOTA_FIELDS:
        for val in (-1, 0, 2):
            pkg_kwargs = {f: 99 for f in quota.QUOTA_FIELDS}
            pkg_kwargs[field] = val
            pkg = SimpleNamespace(**pkg_kwargs)
            # Even when denormalized user columns hold stale values (5/99/0),
            # package attribute is authoritative.
            user_kwargs = {f: 5 for f in quota.QUOTA_FIELDS}
            user_kwargs.update(id=10, role="end_user", package=pkg)
            packaged_user = SimpleNamespace(**user_kwargs)
            eff = quota.effective_limit(packaged_user, field)
            assert eff == val, f"{field} with package={val} returned {eff}"

            # Custom user without package reads user column directly.
            custom_kwargs = {f: None for f in quota.QUOTA_FIELDS}
            custom_kwargs[field] = val
            custom_kwargs.update(id=11, role="end_user", package=None)
            custom_user = SimpleNamespace(**custom_kwargs)
            assert quota.effective_limit(custom_user, field) == val

        # None on un-packaged user normalizes to -1 (Unlimited)
        unset_user = SimpleNamespace(id=12, role="end_user", package=None)
        assert quota.effective_limit(unset_user, field) == -1

    # Predicates across -1, 0, 2
    assert quota.is_quota_unlimited(-1) is True
    assert quota.is_quota_unlimited(0) is False
    assert quota.is_quota_unlimited(2) is False

    assert quota.is_quota_disabled(0) is True
    assert quota.is_quota_disabled(-1) is False
    assert quota.is_quota_disabled(2) is False

    assert quota.is_quota_reached(0, -1) is False
    assert quota.is_quota_reached(100, -1) is False
    assert quota.is_quota_reached(0, 0) is True
    assert quota.is_quota_reached(0, 2) is False
    assert quota.is_quota_reached(1, 2) is False
    assert quota.is_quota_reached(2, 2) is True
    assert quota.is_quota_reached(1, 2, adding=2) is True


def test_website_and_subdomain_matrix_create_assign_transfer(tmp_path):
    from app.api import websites as websites_api
    from app.models.entities import User, UserPackage, Website
    from app.schemas.schemas import WebsiteCreate, WebsiteUpdate

    engine, db = _make_sqlite_session()
    try:
        admin = User(
            username="admin_q",
            email="admin@quota.test",
            hashed_password="x",
            role="admin",
            website_limit=0,
        )
        db.add(admin)
        db.flush()

        def fake_site_root(username: str, domain: str) -> str:
            p = tmp_path / username / domain
            (p / "public_html").mkdir(parents=True, exist_ok=True)
            return str(p)

        fake_req = MagicMock()
        fake_req.headers = {}
        fake_req.client = SimpleNamespace(host="127.0.0.1")

        with (
            patch.object(websites_api, "reconcile_websites_from_disk"),
            patch.object(websites_api, "_ensure_default_waf_file"),
            patch.object(websites_api, "_write_placeholder_page"),
            patch.object(websites_api, "_sync_http_flood_zones"),
            patch.object(websites_api, "_rewrite_website_vhost"),
            patch("app.services.mail.webmail_host_taken", return_value=False),
            patch("app.services.storage_quota.enforce_user_storage_quota"),
            patch("app.services.storage_quota.website_storage_used_bytes", return_value=0),
            patch("app.services.site_users.linux_user_for_panel_username", side_effect=lambda u: u),
            patch("app.services.site_users.site_root_for_panel_user", side_effect=fake_site_root),
            patch("app.services.site_users.ensure_site_runtime", side_effect=lambda _d, _r, _p, u: u),
            patch("app.services.site_users.fix_site_path", return_value=_ok_proc()),
            patch("app.services.site_users.move_site_runtime", return_value=_ok_proc()),
            patch("app.services.nginx.write_vhost", return_value=_ok_proc()),
            patch("app.services.waf.sync_website_rules", return_value=_ok_proc()),
            patch("app.services.dns.zone_for_new_website"),
            patch.object(websites_api, "log_action"),
        ):
            # --- Matrix over website_limit in (-1, 0, 2) ---
            for idx, w_lim in enumerate((-1, 0, 2)):
                pkg = UserPackage(
                    name=f"WebPkg_{idx}_{w_lim}",
                    website_limit=w_lim,
                    subdomain_limit=2,
                    database_limit=5,
                    sftp_accounts_limit=3,
                    mail_accounts_limit=5,
                )
                db.add(pkg)
                db.flush()
                u_create = User(
                    username=f"wc_{idx}",
                    email=f"wc_{idx}@quota.test",
                    hashed_password="x",
                    role="end_user",
                    package_id=pkg.id,
                    website_limit=99,  # stale denormalized value must be ignored
                )
                u_assign = User(
                    username=f"wa_{idx}",
                    email=f"wa_{idx}@quota.test",
                    hashed_password="x",
                    role="end_user",
                    package_id=pkg.id,
                    website_limit=-1 if w_lim == -1 else 99,
                )
                u_transfer = User(
                    username=f"wt_{idx}",
                    email=f"wt_{idx}@quota.test",
                    hashed_password="x",
                    role="end_user",
                    package_id=pkg.id,
                    website_limit=99,
                )
                db.add_all([u_create, u_assign, u_transfer])
                db.commit()
                db.refresh(u_create)
                db.refresh(u_assign)
                db.refresh(u_transfer)

                if w_lim == 0:
                    with pytest.raises(HTTPException) as exc:
                        websites_api.create_website(
                            WebsiteCreate(domain=f"c0-{idx}.test", install_wordpress=False, app_type="php"),
                            fake_req,
                            db,
                            u_create,
                        )
                    assert exc.value.status_code == 403

                    seed = Website(
                        domain=f"seed-a0-{idx}.test",
                        owner_id=admin.id,
                        root_path=fake_site_root("admin_q", f"seed-a0-{idx}.test"),
                        linux_user="admin_q",
                        php_version="8.3",
                        app_type="php",
                    )
                    db.add(seed)
                    db.commit()
                    with pytest.raises(HTTPException) as exc_a:
                        websites_api.update_website(
                            seed.id, WebsiteUpdate(owner_id=u_assign.id), db, admin
                        )
                    assert exc_a.value.status_code == 403

                    with pytest.raises(HTTPException) as exc_t:
                        websites_api.transfer_website_owner(
                            seed.id,
                            websites_api.WebsiteTransferOwnerPayload(target_owner_id=u_transfer.id),
                            fake_req,
                            db,
                            admin,
                        )
                    assert exc_t.value.status_code == 403

                elif w_lim == 2:
                    for n in (1, 2):
                        s = websites_api.create_website(
                            WebsiteCreate(domain=f"c2-{idx}-{n}.test", install_wordpress=False, app_type="php"),
                            fake_req,
                            db,
                            u_create,
                        )
                        assert s.owner_id == u_create.id
                    with pytest.raises(HTTPException) as exc3:
                        websites_api.create_website(
                            WebsiteCreate(domain=f"c2-{idx}-3.test", install_wordpress=False, app_type="php"),
                            fake_req,
                            db,
                            u_create,
                        )
                    assert exc3.value.status_code == 403

                    # Assign (PATCH) allows 2, blocks 3rd
                    for n in (1, 2, 3):
                        seed = Website(
                            domain=f"a2-{idx}-{n}.test",
                            owner_id=admin.id,
                            root_path=fake_site_root("admin_q", f"a2-{idx}-{n}.test"),
                            linux_user="admin_q",
                            php_version="8.3",
                            app_type="php",
                        )
                        db.add(seed)
                        db.commit()
                        if n <= 2:
                            res = websites_api.update_website(
                                seed.id, WebsiteUpdate(owner_id=u_assign.id), db, admin
                            )
                            assert res.owner_id == u_assign.id
                        else:
                            with pytest.raises(HTTPException) as exc_a3:
                                websites_api.update_website(
                                    seed.id, WebsiteUpdate(owner_id=u_assign.id), db, admin
                                )
                            assert exc_a3.value.status_code == 403

                    # Transfer allows 2, blocks 3rd
                    for n in (1, 2, 3):
                        seed = Website(
                            domain=f"t2-{idx}-{n}.test",
                            owner_id=admin.id,
                            root_path=fake_site_root("admin_q", f"t2-{idx}-{n}.test"),
                            linux_user="admin_q",
                            php_version="8.3",
                            app_type="php",
                        )
                        db.add(seed)
                        db.commit()
                        if n <= 2:
                            res = websites_api.transfer_website_owner(
                                seed.id,
                                websites_api.WebsiteTransferOwnerPayload(target_owner_id=u_transfer.id),
                                fake_req,
                                db,
                                admin,
                            )
                            assert res.owner_id == u_transfer.id
                        else:
                            with pytest.raises(HTTPException) as exc_t3:
                                websites_api.transfer_website_owner(
                                    seed.id,
                                    websites_api.WebsiteTransferOwnerPayload(target_owner_id=u_transfer.id),
                                    fake_req,
                                    db,
                                    admin,
                                )
                            assert exc_t3.value.status_code == 403

                else:  # w_lim == -1 (Unlimited)
                    for n in (1, 2, 3):
                        s = websites_api.create_website(
                            WebsiteCreate(domain=f"cu-{idx}-{n}.test", install_wordpress=False, app_type="php"),
                            fake_req,
                            db,
                            u_create,
                        )
                        assert s.owner_id == u_create.id

                        # Regression guard: PATCH /websites/{id} previously rejected website_limit == -1!
                        seed_a = Website(
                            domain=f"au-{idx}-{n}.test",
                            owner_id=admin.id,
                            root_path=fake_site_root("admin_q", f"au-{idx}-{n}.test"),
                            linux_user="admin_q",
                            php_version="8.3",
                            app_type="php",
                        )
                        seed_t = Website(
                            domain=f"tu-{idx}-{n}.test",
                            owner_id=admin.id,
                            root_path=fake_site_root("admin_q", f"tu-{idx}-{n}.test"),
                            linux_user="admin_q",
                            php_version="8.3",
                            app_type="php",
                        )
                        db.add_all([seed_a, seed_t])
                        db.commit()
                        assert (
                            websites_api.update_website(
                                seed_a.id, WebsiteUpdate(owner_id=u_assign.id), db, admin
                            ).owner_id
                            == u_assign.id
                        )
                        assert (
                            websites_api.transfer_website_owner(
                                seed_t.id,
                                websites_api.WebsiteTransferOwnerPayload(target_owner_id=u_transfer.id),
                                fake_req,
                                db,
                                admin,
                            ).owner_id
                            == u_transfer.id
                        )

            # --- Matrix over subdomain_limit in (-1, 0, 2) ---
            for idx, s_lim in enumerate((-1, 0, 2)):
                pkg_s = UserPackage(
                    name=f"SubPkg_{idx}_{s_lim}",
                    website_limit=2,
                    subdomain_limit=s_lim,
                    database_limit=5,
                )
                db.add(pkg_s)
                db.flush()
                u_sub = User(
                    username=f"subu_{idx}",
                    email=f"subu_{idx}@quota.test",
                    hashed_password="x",
                    role="end_user",
                    package_id=pkg_s.id,
                    website_limit=2,
                )
                db.add(u_sub)
                db.commit()
                db.refresh(u_sub)

                root_domain = f"parent-{idx}.test"
                websites_api.create_website(
                    WebsiteCreate(domain=root_domain, install_wordpress=False, app_type="php"),
                    fake_req,
                    db,
                    u_sub,
                )

                if s_lim == 0:
                    with pytest.raises(HTTPException) as exc_s0:
                        websites_api.create_website(
                            WebsiteCreate(domain=f"s1.{root_domain}", install_wordpress=False, app_type="php"),
                            fake_req,
                            db,
                            u_sub,
                        )
                    assert exc_s0.value.status_code == 403

                    seed_sub = Website(
                        domain=f"tr.{root_domain}",
                        owner_id=admin.id,
                        root_path=fake_site_root("admin_q", f"tr.{root_domain}"),
                        linux_user="admin_q",
                        php_version="8.3",
                        app_type="php",
                    )
                    db.add(seed_sub)
                    db.commit()
                    with pytest.raises(HTTPException) as exc_st0:
                        websites_api.transfer_website_owner(
                            seed_sub.id,
                            websites_api.WebsiteTransferOwnerPayload(target_owner_id=u_sub.id),
                            fake_req,
                            db,
                            admin,
                        )
                    assert exc_st0.value.status_code == 403
                elif s_lim == 2:
                    for n in (1, 2):
                        websites_api.create_website(
                            WebsiteCreate(domain=f"s{n}.{root_domain}", install_wordpress=False, app_type="php"),
                            fake_req,
                            db,
                            u_sub,
                        )
                    with pytest.raises(HTTPException) as exc_s3:
                        websites_api.create_website(
                            WebsiteCreate(domain=f"s3.{root_domain}", install_wordpress=False, app_type="php"),
                            fake_req,
                            db,
                            u_sub,
                        )
                    assert exc_s3.value.status_code == 403
                else:  # s_lim == -1
                    for n in (1, 2, 3):
                        sub = websites_api.create_website(
                            WebsiteCreate(domain=f"su{n}.{root_domain}", install_wordpress=False, app_type="php"),
                            fake_req,
                            db,
                            u_sub,
                        )
                        assert sub.owner_id == u_sub.id
    finally:
        db.close()
        engine.dispose()


def test_sftp_database_and_mail_limits_matrix(tmp_path):
    from app.api import databases as db_api
    from app.api import sftp_accounts as sftp_api
    from app.models.entities import DatabaseAccount, MailDomain, User, UserPackage, Website
    from app.services import mail as mail_svc
    from app.services.mail import MailError

    engine, db = _make_sqlite_session()
    try:
        admin = User(username="adm_sdm", email="adm@sdm.test", hashed_password="x", role="admin")
        db.add(admin)
        db.flush()
        assert sftp_api.account_limits(db=db, current_user=admin) == {
            "limit": None,
            "used": 0,
            "unlimited": True,
        }

        fake_req = MagicMock()
        fake_req.headers = {}
        fake_req.client = SimpleNamespace(host="127.0.0.1")

        for idx, lim in enumerate((-1, 0, 2)):
            pkg = UserPackage(
                name=f"SdmPkg_{idx}_{lim}",
                website_limit=5,
                subdomain_limit=5,
                database_limit=lim,
                sftp_accounts_limit=lim,
                mail_accounts_limit=lim,
            )
            db.add(pkg)
            db.flush()
            u = User(
                username=f"sdmu_{idx}",
                email=f"sdmu_{idx}@sdm.test",
                hashed_password="x",
                role="end_user",
                package_id=pkg.id,
                # Stale defaults on user row must NOT override package limits:
                database_limit=5,
                sftp_accounts_limit=3,
                mail_accounts_limit=10,
            )
            db.add(u)
            db.flush()
            site = Website(
                domain=f"sdm-{idx}.test",
                owner_id=u.id,
                root_path=str(tmp_path / f"sdm-{idx}.test"),
                linux_user=u.username,
                php_version="8.3",
                app_type="php",
            )
            mdom = MailDomain(owner_id=u.id, domain=f"sdm-{idx}.test")
            db.add_all([site, mdom])
            db.commit()
            db.refresh(u)

            # 1) SFTP limits endpoint + create_account enforcement
            sftp_status = sftp_api.account_limits(db=db, current_user=u)
            if lim == -1:
                assert sftp_status == {"limit": -1, "used": 0, "unlimited": True}
            elif lim == 0:
                assert sftp_status == {"limit": 0, "used": 0, "unlimited": False}
            else:
                assert sftp_status == {"limit": 2, "used": 0, "unlimited": False}

            with (
                patch("app.services.sftp_accounts.ensure_account", return_value="/chroot"),
                patch("app.services.sftp_accounts.set_password"),
                patch.object(sftp_api, "_sftp_host", return_value="panel.sdm.test"),
                patch.object(sftp_api, "log_action"),
            ):
                if lim == 0:
                    with pytest.raises(HTTPException) as exc_sftp0:
                        sftp_api.create_account(
                            sftp_api.SftpAccountCreate(website_id=site.id, label="dev1", password="StrongPassword123!"),
                            fake_req,
                            db,
                            u,
                        )
                    assert exc_sftp0.value.status_code == 403
                elif lim == 2:
                    for n in (1, 2):
                        sftp_api.create_account(
                            sftp_api.SftpAccountCreate(website_id=site.id, label=f"dev{n}", password="StrongPassword123!"),
                            fake_req,
                            db,
                            u,
                        )
                    with pytest.raises(HTTPException) as exc_sftp3:
                        sftp_api.create_account(
                            sftp_api.SftpAccountCreate(website_id=site.id, label="dev3", password="StrongPassword123!"),
                            fake_req,
                            db,
                            u,
                        )
                    assert exc_sftp3.value.status_code == 409
                else:
                    for n in (1, 2, 3):
                        sftp_api.create_account(
                            sftp_api.SftpAccountCreate(website_id=site.id, label=f"dev{n}", password="StrongPassword123!"),
                            fake_req,
                            db,
                            u,
                        )

            # 2) Database quota enforcement
            if lim == 0:
                with pytest.raises(HTTPException) as exc_db0:
                    db_api.enforce_user_database_limit(db, u)
                assert exc_db0.value.status_code == 403
            elif lim == 2:
                for n in (1, 2):
                    db_api.enforce_user_database_limit(db, u)
                    db.add(
                        DatabaseAccount(
                            owner_id=u.id,
                            website_id=site.id,
                            db_name=f"db_{idx}_{n}",
                            db_user=f"dbu_{idx}_{n}",
                            db_password="enc",
                        )
                    )
                    db.commit()
                with pytest.raises(HTTPException) as exc_db3:
                    db_api.enforce_user_database_limit(db, u)
                assert exc_db3.value.status_code == 403
            else:
                for n in (1, 2, 3):
                    db_api.enforce_user_database_limit(db, u)
                    db.add(
                        DatabaseAccount(
                            owner_id=u.id,
                            website_id=site.id,
                            db_name=f"dbu_{idx}_{n}",
                            db_user=f"dbuu_{idx}_{n}",
                            db_password="enc",
                        )
                    )
                    db.commit()

            # 3) Mailbox overview + create_account enforcement
            with (
                patch.object(mail_svc, "_require_active"),
                patch.object(mail_svc, "active", return_value=True),
                patch.object(mail_svc, "sync"),
                patch.object(mail_svc, "hash_password", return_value="{BLF-CRYPT}$2y$05$hash"),
            ):
                ov = mail_svc.overview(db, u)
                if lim == -1:
                    assert ov["mailbox_limit"] is None
                    assert ov["at_limit"] is False
                    for n in (1, 2, 3):
                        mail_svc.create_account(db, u, mdom.id, f"box{n}", "MailPass123!@#")
                elif lim == 0:
                    assert ov["mailbox_limit"] == 0
                    assert ov["at_limit"] is True
                    with pytest.raises(MailError) as exc_m0:
                        mail_svc.create_account(db, u, mdom.id, "box1", "MailPass123!@#")
                    assert exc_m0.value.status == 403
                else:
                    assert ov["mailbox_limit"] == 2
                    assert ov["at_limit"] is False
                    for n in (1, 2):
                        mail_svc.create_account(db, u, mdom.id, f"box{n}", "MailPass123!@#")
                    assert mail_svc.overview(db, u)["at_limit"] is True
                    with pytest.raises(MailError) as exc_m3:
                        mail_svc.create_account(db, u, mdom.id, "box3", "MailPass123!@#")
                    assert exc_m3.value.status == 403
    finally:
        db.close()
        engine.dispose()


def test_auth_me_user_out_and_application_quota_sync():
    from app.api import auth as auth_api
    from app.api import laravel_manager as lm_api
    from app.api import resources as res_api
    from app.api import site_apps as sa_api
    from app.api import users as users_api
    from app.api import wordpress_manager as wm_api
    from app.models.entities import User, UserPackage

    engine, db = _make_sqlite_session()
    try:
        pkg_zero_web = UserPackage(
            name="ZeroWebButSub20",
            website_limit=0,
            subdomain_limit=20,
            database_limit=0,
            sftp_accounts_limit=0,
            mail_accounts_limit=0,
        )
        pkg_unlim = UserPackage(
            name="UnlimAll",
            website_limit=-1,
            subdomain_limit=-1,
            database_limit=-1,
            sftp_accounts_limit=-1,
            mail_accounts_limit=-1,
        )
        db.add_all([pkg_zero_web, pkg_unlim])
        db.flush()

        u_zero = User(
            username="uzero",
            email="uzero@quota.test",
            hashed_password="x",
            role="end_user",
            package_id=pkg_zero_web.id,
            website_limit=5,
            database_limit=5,
            sftp_accounts_limit=3,
            mail_accounts_limit=10,
        )
        u_unlim = User(
            username="uunlim",
            email="uunlim@quota.test",
            hashed_password="x",
            role="end_user",
            package_id=pkg_unlim.id,
            website_limit=1,
            database_limit=1,
            sftp_accounts_limit=1,
            mail_accounts_limit=1,
        )
        admin = User(
            username="uadmin",
            email="uadmin@quota.test",
            hashed_password="x",
            role="admin",
            website_limit=0,
        )
        db.add_all([u_zero, u_unlim, admin])
        db.commit()
        for obj in (u_zero, u_unlim, admin):
            db.refresh(obj)

        req = MagicMock()
        req.state = SimpleNamespace(jwt_payload={})
        resp = MagicMock()

        with patch("app.services.storage_quota.storage_usage_summary", return_value={}):
            me_zero = auth_api.session_status(req, resp, db, u_zero)["user"]
            assert (
                me_zero["website_limit"],
                me_zero["subdomain_limit"],
                me_zero["database_limit"],
                me_zero["sftp_accounts_limit"],
                me_zero["mail_accounts_limit"],
            ) == (0, 20, 0, 0, 0)

            out_zero = users_api._user_out(u_zero, db, cached_usage=True)
            assert (
                out_zero["website_limit"],
                out_zero["subdomain_limit"],
                out_zero["database_limit"],
                out_zero["sftp_accounts_limit"],
                out_zero["mail_accounts_limit"],
            ) == (0, 20, 0, 0, 0)

            me_unlim = auth_api.session_status(req, resp, db, u_unlim)["user"]
            assert (
                me_unlim["website_limit"],
                me_unlim["subdomain_limit"],
                me_unlim["database_limit"],
                me_unlim["sftp_accounts_limit"],
                me_unlim["mail_accounts_limit"],
            ) == (-1, -1, -1, -1, -1)

            me_admin = auth_api.session_status(req, resp, db, admin)["user"]
            assert (
                me_admin["website_limit"],
                me_admin["subdomain_limit"],
                me_admin["database_limit"],
                me_admin["sftp_accounts_limit"],
                me_admin["mail_accounts_limit"],
            ) == (-1, -1, -1, -1, -1)

        res_row = res_api._build_user_resource_row(
            u_zero, dbs=[], sites=[], managers={}, primary_admin=admin
        )
        assert res_row["website_limit"] == 0
        assert res_row["database_limit"] == 0

        # When website_limit == 0, Application modules must treat max_total_websites as 0
        # even if subdomain_limit == 20.
        sa_q = sa_api._website_quota_status(db, u_zero, [])
        assert sa_q["max_total_websites"] == 0
        assert sa_q["website_quota_reached"] is True

        wp_q, _ = wm_api._evaluate_user_app_quota(db, u_zero, [], {"instances": {}}, set())
        assert wp_q["max_total_websites"] == 0
        assert wp_q["can_install"] is False

        lv_q, _ = lm_api._evaluate_user_app_quota(db, u_zero, [], {"instances": {}}, set())
        assert lv_q["max_total_websites"] == 0
        assert lv_q["can_install"] is False
    finally:
        db.close()
        engine.dispose()


def test_alembic_unify_package_quota_limits_migration():
    from alembic import command
    from alembic.config import Config
    from app.core.config import settings

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "legacy_upgrade.db"
        db_url = f"sqlite:///{db_file.as_posix()}"
        prev_url = settings.database_url
        settings.database_url = db_url
        try:
            cfg = Config(str(BACKEND_DIR / "alembic.ini"))
            cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
            cfg.set_main_option("sqlalchemy.url", db_url)

            # 1. Migrate up to mail_upgrade (immediately prior to unify_package_quota_limits)
            command.upgrade(cfg, "mail_upgrade")

            eng = create_engine(db_url)
            try:
                with eng.begin() as conn:
                    conn.execute(sa.text("PRAGMA foreign_keys = ON"))
                    conn.execute(
                        sa.text(
                            "INSERT INTO user_packages "
                            "(id, name, website_limit, storage_limit_mb, subdomain_limit, "
                            "database_limit, sftp_accounts_limit, mail_accounts_limit, created_at) "
                            "VALUES "
                            "(901, 'LegacySubZero', 2, 1024, 0, 0, 0, 0, '2026-01-01 00:00:00'), "
                            "(902, 'ExplicitSubFive', 1, 2048, 5, 3, 2, 4, '2026-01-01 00:00:00')"
                        )
                    )
                    conn.execute(
                        sa.text(
                            "INSERT INTO users "
                            "(id, username, email, hashed_password, role, is_active, package_id, "
                            "website_limit, storage_limit_mb, database_limit, sftp_accounts_limit, "
                            "mail_accounts_limit, created_at) "
                            "VALUES "
                            "(910, 'packaged_stale', 'p@t.test', 'x', 'end_user', 1, 901, 99, 1024, 5, 3, 10, '2026-01-01 00:00:00'), "
                            "(911, 'custom_legacy', 'c@t.test', 'x', 'end_user', 1, NULL, 0, 1024, 0, 0, 0, '2026-01-01 00:00:00'), "
                            "(912, 'admin_legacy', 'a@t.test', 'x', 'admin', 1, NULL, 0, 1024, 5, 3, 10, '2026-01-01 00:00:00')"
                        )
                    )

                # 2. Upgrade to head (unify_package_quota_limits)
                command.upgrade(cfg, "head")

                with eng.connect() as conn:
                    # Seeded default packages from 0021_user_packages also had subdomain_limit=0 -> -1
                    seeded_sub = conn.execute(
                        sa.text("SELECT subdomain_limit FROM user_packages WHERE id = 1")
                    ).scalar_one_or_none()
                    if seeded_sub is not None:
                        assert seeded_sub == -1

                    pkg1 = conn.execute(
                        sa.text(
                            "SELECT website_limit, subdomain_limit, database_limit, "
                            "sftp_accounts_limit, mail_accounts_limit "
                            "FROM user_packages WHERE id = 901"
                        )
                    ).mappings().one()
                    assert dict(pkg1) == {
                        "website_limit": 2,
                        "subdomain_limit": -1,  # converted 0 -> -1
                        "database_limit": 0,    # 0 preserved as disabled!
                        "sftp_accounts_limit": 0,
                        "mail_accounts_limit": 0,
                    }

                    pkg2 = conn.execute(
                        sa.text("SELECT subdomain_limit FROM user_packages WHERE id = 902")
                    ).scalar_one()
                    assert pkg2 == 5

                    u10 = conn.execute(
                        sa.text(
                            "SELECT package_id, website_limit, database_limit, "
                            "sftp_accounts_limit, mail_accounts_limit "
                            "FROM users WHERE id = 910"
                        )
                    ).mappings().one()
                    assert dict(u10) == {
                        "package_id": 901,
                        "website_limit": 2,
                        "database_limit": 0,
                        "sftp_accounts_limit": 0,
                        "mail_accounts_limit": 0,
                    }

                    u11 = conn.execute(
                        sa.text(
                            "SELECT package_id, website_limit, database_limit, "
                            "sftp_accounts_limit, mail_accounts_limit "
                            "FROM users WHERE id = 911"
                        )
                    ).mappings().one()
                    assert dict(u11) == {
                        "package_id": None,
                        "website_limit": -1,  # converted 0 -> -1 for un-packaged end_user
                        "database_limit": 0,  # 0 preserved as disabled!
                        "sftp_accounts_limit": 0,
                        "mail_accounts_limit": 0,
                    }

                    u12_web = conn.execute(
                        sa.text("SELECT website_limit FROM users WHERE id = 912")
                    ).scalar_one()
                    assert u12_web == 0  # admin row left untouched
            finally:
                eng.dispose()
        finally:
            settings.database_url = prev_url
