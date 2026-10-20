"""Tests for the DirectAdmin backup import service."""

import tarfile
from pathlib import Path

import pytest

from app.services import da_import

HELPER_SCRIPT = Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh"


def _make_da_backup(tmp_path, name="user.admin.demo.tar", pointers=None):
    """Build a minimal DirectAdmin-shaped account backup."""
    src = tmp_path / f"src-{name}"
    backup = src / "backup"
    backup.mkdir(parents=True)
    (backup / "user.conf").write_text("username=demo\nemail=demo@example.com\n", encoding="utf-8")
    (backup / "domains.list").write_text("example.com\n", encoding="utf-8")
    if pointers is not None:
        (backup / "example.com.pointers").write_text(pointers, encoding="utf-8")

    public = src / "domains" / "example.com" / "public_html"
    public.mkdir(parents=True)
    (public / "index.php").write_text("<?php echo 'hi';", encoding="utf-8")
    (public / "wp-config.php").write_text(
        "<?php define('DB_NAME', 'demo_wp'); define('DB_USER', 'demo_wp');", encoding="utf-8"
    )
    (src / "demo_wp.sql").write_text("CREATE TABLE t (id int);\n", encoding="utf-8")

    archive = tmp_path / name
    with tarfile.open(archive, "w") as handle:
        handle.add(src, arcname=".")
    return archive


class TestSafeExtract:
    def test_extracts_seekable_archive(self, tmp_path):
        archive = _make_da_backup(tmp_path)
        dest = tmp_path / "out"

        da_import._safe_extract_tar(archive, dest)

        assert (dest / "backup" / "user.conf").is_file()
        assert (dest / "domains" / "example.com" / "public_html" / "index.php").is_file()

    def test_extracts_non_seekable_stream(self, tmp_path, monkeypatch):
        """The .tar.zst path pipes through zstd, so the tar stream is read once.

        Regression test: validating members in a separate pass consumed the
        stream and extractall then raised StreamError / wrote nothing.
        """
        archive = _make_da_backup(tmp_path, name="user.admin.demo.tar.zst")
        plain = _make_da_backup(tmp_path, name="plain.tar")
        dest = tmp_path / "out-stream"

        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.stdout = open(plain, "rb")
                self.stderr = None
                self.returncode = 0

            def wait(self):
                return 0

        monkeypatch.setattr(da_import.shutil, "which", lambda name: "/usr/bin/zstdcat")
        monkeypatch.setattr(da_import.subprocess, "Popen", FakePopen)

        da_import._safe_extract_tar(archive, dest)

        assert (dest / "backup" / "domains.list").is_file()
        assert (dest / "domains" / "example.com" / "public_html" / "index.php").is_file()

    def test_rejects_path_escaping_destination(self, tmp_path):
        archive = tmp_path / "evil.tar"
        payload = tmp_path / "payload.txt"
        payload.write_text("x", encoding="utf-8")
        with tarfile.open(archive, "w") as handle:
            handle.add(payload, arcname="../escaped.txt")

        with pytest.raises(RuntimeError, match="Unsafe path"):
            da_import._safe_extract_tar(archive, tmp_path / "out")


class TestUploadName:
    def test_strips_directory_components(self):
        assert da_import.safe_upload_name("backup.tar.gz") == "backup.tar.gz"
        assert da_import.safe_upload_name("/etc/cron.d/../backup.tar.gz") == "backup.tar.gz"
        assert da_import.safe_upload_name("..\\..\\backup.tar.gz") == "backup.tar.gz"

    def test_rejects_traversal_and_bad_types(self):
        for name in ("../../etc/cron.d/evil", "..", "", ".hidden.tar.gz", "shell.php", "notes.txt"):
            with pytest.raises(ValueError):
                da_import.safe_upload_name(name)


class TestResolveBackupPath:
    def test_accepts_paths_inside_backup_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(da_import, "DA_BACKUP_DIR", tmp_path)
        target = tmp_path / "user.demo.tar.gz"
        target.write_text("x", encoding="utf-8")

        assert da_import.resolve_backup_path(str(target)) == target.resolve()
        assert da_import.resolve_backup_path("user.demo.tar.gz") == target.resolve()

    def test_rejects_paths_outside_backup_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(da_import, "DA_BACKUP_DIR", tmp_path / "da")
        (tmp_path / "da").mkdir()

        for value in ("../../etc/passwd", str(tmp_path / "elsewhere.tar.gz"), ""):
            with pytest.raises(ValueError):
                da_import.resolve_backup_path(value)


class TestDomainPointers:
    def test_reads_mode_from_pointer_file(self, tmp_path):
        root = tmp_path / "extracted"
        (root / "backup").mkdir(parents=True)
        (root / "backup" / "example.com.pointers").write_text(
            "alias.com=alias\nold-name.com=redirect\n# comment\n\nexample.com=alias\n",
            encoding="utf-8",
        )

        assert da_import._discover_domain_pointers(root, "example.com") == [
            ("alias.com", "alias"),
            ("old-name.com", "redirect"),
        ]

    def test_bare_domain_lines_default_to_alias(self, tmp_path):
        root = tmp_path / "extracted"
        (root / "domains" / "example.com").mkdir(parents=True)
        (root / "domains" / "example.com" / "domain.pointers").write_text(
            "alias.com\n", encoding="utf-8"
        )

        assert da_import._discover_domain_pointers(root, "example.com") == [("alias.com", "alias")]

    def test_missing_pointer_file_is_empty(self, tmp_path):
        assert da_import._discover_domain_pointers(tmp_path, "example.com") == []


class TestScan:
    def test_scan_reports_domains_databases_and_aliases(self, tmp_path, monkeypatch):
        monkeypatch.setattr(da_import, "DA_BACKUP_DIR", tmp_path)
        monkeypatch.setattr(da_import, "STAGE_BASE", tmp_path / "stage")
        archive = _make_da_backup(tmp_path, name="user.admin.demo.tar", pointers="alias.com=alias\n")

        result = da_import.scan_da_backup(str(archive))

        assert result["errors"] == []
        assert len(result["users"]) == 1
        user = result["users"][0]
        assert user["username"] == "demo"
        domain = user["domains"][0]
        assert domain["domain"] == "example.com"
        assert domain["app_type"] == "wordpress"
        assert domain["db_name"] == "demo_wp"
        assert domain["has_sql_dump"] is True
        assert domain["aliases"] == [{"domain": "alias.com", "mode": "alias"}]

    def test_scan_rejects_path_outside_backup_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(da_import, "DA_BACKUP_DIR", tmp_path / "da")
        (tmp_path / "da").mkdir()
        outside = tmp_path / "outside.tar.gz"
        outside.write_text("x", encoding="utf-8")

        with pytest.raises(ValueError):
            da_import.scan_da_backup(str(outside))


class TestAppConfigDiscovery:
    def test_wp_config_one_level_above_docroot_is_found(self, tmp_path):
        site = tmp_path / "domains" / "example.com"
        public = site / "public_html"
        public.mkdir(parents=True)
        (public / "index.php").write_text("<?php", encoding="utf-8")
        # WordPress allows wp-config.php in the parent of the document root.
        (site / "wp-config.php").write_text(
            "<?php define('DB_NAME','wp_live'); define('DB_USER','wp_user');"
            " define('DB_PASSWORD','s3cret');",
            encoding="utf-8",
        )

        assert da_import._detect_app_type(public) == "wordpress"
        values, matched_dir = da_import._locate_app_db_config(public)
        assert values["DB_NAME"] == "wp_live"
        assert values["DB_PASSWORD"] == "s3cret"
        assert matched_dir == site

    def test_config_in_subfolder_is_found(self, tmp_path):
        public = tmp_path / "public_html"
        app = public / "app"
        app.mkdir(parents=True)
        (app / ".env").write_text("DB_DATABASE=laravel\nDB_PASSWORD=pw\n", encoding="utf-8")

        values, matched_dir = da_import._locate_app_db_config(public)
        assert values["DB_NAME"] == "laravel"
        assert matched_dir == app

    def test_junk_dirs_are_skipped(self, tmp_path):
        public = tmp_path / "public_html"
        (public / "wp-content" / "cache").mkdir(parents=True)
        (public / "wp-content" / "cache" / "wp-config.php").write_text(
            "<?php define('DB_NAME','stale');", encoding="utf-8"
        )
        assert public / "wp-content" not in da_import._candidate_config_dirs(public)


class TestDaDbCredentials:
    def test_reads_nested_query_string_hash(self, tmp_path):
        root = tmp_path / "extracted"
        backup = root / "backup"
        backup.mkdir(parents=True)
        sql = backup / "nhs_data1402.sql"
        sql.write_text("CREATE TABLE t (id int);\n", encoding="utf-8")
        (backup / "nhs_data1402.conf").write_text(
            "accesshosts=0=localhost\n"
            "nhs_data1402=accesshosts=localhost&passwd=%2A03D98834235F3AD4CE858733AA33C455088FAEF1"
            "&plugin=mysql_native_password&select_priv=Y\n",
            encoding="utf-8",
        )

        creds = da_import._da_db_credentials(sql, root)
        assert creds["password_hash"] == "*03D98834235F3AD4CE858733AA33C455088FAEF1"
        assert creds["password"] == ""

    def test_reads_flat_clear_password(self, tmp_path):
        root = tmp_path / "extracted"
        backup = root / "backup"
        backup.mkdir(parents=True)
        sql = backup / "shop.sql"
        sql.write_text("-- dump\n", encoding="utf-8")
        (backup / "shop.conf").write_text("passwd=plain-text-pw\n", encoding="utf-8")

        creds = da_import._da_db_credentials(sql, root)
        assert creds == {"password": "plain-text-pw", "password_hash": ""}

    def test_missing_conf_returns_empty(self, tmp_path):
        root = tmp_path / "extracted"
        (root / "backup").mkdir(parents=True)
        sql = root / "backup" / "lonely.sql"
        sql.write_text("-- dump\n", encoding="utf-8")
        assert da_import._da_db_credentials(sql, root) == {"password": "", "password_hash": ""}

    def test_import_db_password_priority(self):
        # App config wins, then the DA hash, then a DA clear password.
        pw, h, reused = da_import._import_db_password(
            {"DB_PASSWORD": "from-wp"}, {"password_hash": "*" + "a" * 40}
        )
        assert (pw, h, reused) == ("from-wp", "", True)
        pw, h, reused = da_import._import_db_password({}, {"password_hash": "*" + "b" * 40})
        assert (pw, h, reused) == ("", "*" + "b" * 40, True)
        pw, h, reused = da_import._import_db_password({}, {"password": "da-clear"})
        assert (pw, h, reused) == ("da-clear", "", True)
        pw, h, reused = da_import._import_db_password({}, {})
        assert reused is False and pw and h == ""


class TestNestedArchivesAndSubdomains:
    def test_nested_domain_archive_is_unpacked(self, tmp_path):
        root = tmp_path / "extracted"
        backup = root / "backup"
        backup.mkdir(parents=True)
        # A per-domain archive shipped inside backup/ instead of domains/.
        inner = tmp_path / "inner"
        (inner / "public_html").mkdir(parents=True)
        (inner / "public_html" / "index.php").write_text("<?php", encoding="utf-8")
        nested = backup / "shop.example.com.tar.gz"
        with tarfile.open(nested, "w:gz") as handle:
            handle.add(inner, arcname=".")

        da_import._extract_nested_domain_archives(root)

        assert (root / "domains" / "shop.example.com" / "public_html" / "index.php").is_file()
        assert da_import._normalize_domain("shop.example.com") in da_import._discover_domains(root)

    def test_discover_subdomains_reads_the_list(self, tmp_path):
        root = tmp_path / "extracted"
        (root / "backup" / "example.com").mkdir(parents=True)
        (root / "backup" / "example.com" / "subdomain.list").write_text(
            "blog\nshop\n# note\n", encoding="utf-8"
        )
        assert da_import._discover_subdomains(root, "example.com") == ["blog", "shop"]

    def test_relocate_moves_subdomain_docroot(self, tmp_path):
        root = tmp_path / "extracted"
        (root / "backup" / "example.com").mkdir(parents=True)
        (root / "backup" / "example.com" / "subdomain.list").write_text("blog\n", encoding="utf-8")
        blog = root / "domains" / "example.com" / "public_html" / "blog"
        blog.mkdir(parents=True)
        (blog / "index.php").write_text("<?php", encoding="utf-8")

        sources = da_import._relocate_da_subdomain_sources(root, ["example.com"])

        assert "blog.example.com" in sources
        staged = sources["blog.example.com"]
        assert staged is not None and (staged / "index.php").is_file()
        # Moved, not copied: gone from the parent's public_html.
        assert not blog.exists()


class TestHelperVerb:
    def test_site_populate_is_defined_and_confined(self):
        helper = HELPER_SCRIPT.read_text(encoding="utf-8")
        assert "site-populate)" in helper
        # The staged source must live under the panel-owned import staging area.
        assert "staged source must be under /var/lib/spanel/import-stage" in helper
        # Runs the copy as root, then re-applies the site permission model.
        assert 'cp -a --no-preserve=ownership -- "$src/." "$root_target/"' in helper
        assert 'fix_site_tree "$root_target" "$user"' in helper
        # Drops anything a crafted backup could smuggle in.
        assert "-type l -o -type b -o -type c -o -type p -o -type s" in helper


class TestUsernameDiscovery:
    def test_directadmin_name_key_is_used(self, tmp_path):
        root = tmp_path / "extracted"
        (root / "backup").mkdir(parents=True)
        (root / "backup" / "user.conf").write_text(
            "account=ON\ndomain=nganhaso.net\nname=nhs\nemail=legiang360@gmail.com\n",
            encoding="utf-8",
        )
        username, email = da_import._discover_username(root, "user.admin.nhs.tar.zst")
        assert username == "nhs"
        assert email == "legiang360@gmail.com"


class TestImportSslCoverage:
    def test_pointer_domains_are_requested_alongside_the_bare_domain(self, monkeypatch):
        # _sync_website_aliases() already persisted every pointer DirectAdmin
        # had for this site (both "alias" and "redirect" mode) by the time
        # this runs - nginx serves all of them, including a redirect domain
        # (it still needs a valid certificate: the browser completes TLS
        # before it ever sees the 301 that sends it elsewhere). Calling
        # issue_ssl(website.domain) alone, with nothing else, left every one
        # of them with no certificate coverage - reported live for both
        # www.<domain> and a redirect domain on a DirectAdmin-imported site.
        from types import SimpleNamespace

        website = SimpleNamespace(
            domain="example.com",
            aliases=[
                SimpleNamespace(domain="www.example.com", mode="alias"),
                SimpleNamespace(domain="old-example.com", mode="redirect"),
            ],
            ssl_enabled=False, ssl_mode="none", ssl_updated_at=None,
        )
        captured = {}

        def fake_issue_ssl(domain, aliases=None):
            captured["domain"] = domain
            captured["aliases"] = aliases
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(da_import, "_domain_ip_addresses", lambda domain: {"203.0.113.10"})
        monkeypatch.setattr(da_import, "_server_ip_addresses", lambda: {"203.0.113.10"})
        monkeypatch.setattr("app.services.ssl.issue_ssl", fake_issue_ssl)

        db = SimpleNamespace(commit=lambda: None, refresh=lambda obj: None)
        da_import._enable_ssl_when_dns_matches(db, website, {"warnings": [], "ssl_enabled_domains": []})

        assert captured["domain"] == "example.com"
        assert set(captured["aliases"]) == {"www.example.com", "old-example.com"}
        assert website.ssl_enabled is True
        assert website.ssl_mode == "letsencrypt"

    def test_a_pointer_with_no_working_dns_here_is_reported_not_silently_dropped(self, monkeypatch):
        # --allow-subset-of-names lets issue_ssl() succeed overall while
        # dropping a pointer whose DNS points elsewhere - the import used to
        # report "SSL installed for example.com" with no hint that one of its
        # pointers still had no certificate coverage at all.
        from types import SimpleNamespace

        redirect_alias = SimpleNamespace(domain="old-example.com", mode="redirect", ssl_enabled=False)
        website = SimpleNamespace(
            domain="example.com",
            aliases=[redirect_alias],
            ssl_enabled=False, ssl_mode="none", ssl_updated_at=None,
        )

        monkeypatch.setattr(da_import, "_domain_ip_addresses", lambda domain: {"203.0.113.10"})
        monkeypatch.setattr(da_import, "_server_ip_addresses", lambda: {"203.0.113.10"})
        monkeypatch.setattr(
            "app.services.ssl.issue_ssl",
            lambda domain, aliases=None: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
        # The certificate exists (example.com + www), but never got the
        # redirect pointer - its DNS points somewhere else entirely.
        monkeypatch.setattr("app.services.ssl.cert_info", lambda domain: {"sans": ["example.com", "www.example.com"]})

        db = SimpleNamespace(commit=lambda: None, refresh=lambda obj: None)
        item_summary = {"warnings": [], "ssl_enabled_domains": []}
        da_import._enable_ssl_when_dns_matches(db, website, item_summary)

        assert redirect_alias.ssl_enabled is False
        assert any("old-example.com" in w for w in item_summary["warnings"])
        # The bare domain still got SSL - a bad pointer must not undo that.
        assert website.ssl_enabled is True
        assert item_summary["ssl_enabled_domains"] == ["example.com"]
