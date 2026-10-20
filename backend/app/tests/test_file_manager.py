import io
import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from app.models.entities import Website
from app.services import file_manager


def _website(root):
    return Website(domain="example.test", owner_id=1, root_path=str(root), linux_user=None)


def _linux_website(root):
    return Website(domain="example.test", owner_id=1, root_path=str(root), linux_user="siteuser")


def test_upload_file_with_linux_user_uses_install_helper_and_normalizes(tmp_path, monkeypatch):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(file_manager.shell, "privileged", fake_privileged)
    monkeypatch.setattr(file_manager, "_clear_fastcgi_cache", lambda: None)

    target = file_manager.upload_file(
        _linux_website(root),
        "public_html",
        "index.php",
        io.BytesIO(b"<?php echo 'ok';\n"),
        allow_executable=True,
    )

    assert target == str(public / "index.php")
    assert calls[0][0] == "site-file-install"
    assert calls[0][1][:3] == ["siteuser", str(root.resolve()), "public_html/index.php"]
    assert not Path(calls[0][1][3]).exists()

    # One privileged call, not three. site-file-install renames the staged file
    # into place, fixes ownership and clears the FastCGI cache itself; it used
    # to be followed by site-path-fix and fastcgi-cache-clear, each paying for
    # its own sudo and helper start-up - about 0.2s apiece on a loaded VPS,
    # which was most of what a small upload cost.
    assert len(calls) == 1, f"expected one privileged call, got {[c[0] for c in calls]}"


def test_copy_entries_with_linux_user_runs_as_site_user_then_normalizes(tmp_path, monkeypatch):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    source = public / "index.php"
    source.write_text("hello", encoding="utf-8")
    destination = root / "copies"
    destination.mkdir()
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(file_manager.shell, "privileged", fake_privileged)
    monkeypatch.setattr(file_manager, "_clear_fastcgi_cache", lambda: None)

    copied = file_manager.copy_entries(
        _linux_website(root),
        ["public_html/index.php"],
        "copies",
        allow_executable=True,
    )

    assert copied == [str(destination / "index.php")]
    assert calls[0] == (
        "terminal-exec",
        ["siteuser", str(root.resolve()), "cp", "-R", "--", "public_html/index.php", "copies/index.php"],
        {"fallback": ["cp", "-R", "--", str(source), str(destination / "index.php")]},
    )
    assert calls[1] == (
        "site-path-fix",
        [str(destination / "index.php"), "siteuser"],
        {"check": True, "fallback": ["chown", "-R", "siteuser:siteuser", str(destination / "index.php")]},
    )


def test_write_text_file_with_linux_user_normalizes_written_file(tmp_path, monkeypatch):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    target = public / "index.php"
    target.write_text("old", encoding="utf-8")
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(file_manager.shell, "privileged", fake_privileged)
    monkeypatch.setattr(file_manager, "_clear_fastcgi_cache", lambda: None)

    written = file_manager.write_text_file(
        _linux_website(root),
        "public_html/index.php",
        "new",
        allow_executable=True,
    )

    assert written == str(target)
    assert calls == [
        (
            "site-file-write",
            ["siteuser", str(root.resolve()), "public_html/index.php"],
            {"input": "new", "fallback": ["tee", str(target)]},
        ),
        (
            "site-path-fix",
            [str(target), "siteuser"],
            {"check": True, "fallback": ["chown", "-R", "siteuser:siteuser", str(target)]},
        ),
    ]


def test_delete_entries_with_linux_user_binds_helper_to_site_user_and_root(tmp_path, monkeypatch):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    target = public / "cache"
    target.mkdir()
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(file_manager.shell, "privileged", fake_privileged)
    monkeypatch.setattr(file_manager, "_clear_fastcgi_cache", lambda: None)

    deleted = file_manager.delete_entries(_linux_website(root), ["public_html/cache"], allow_executable=True)

    assert deleted == [str(target)]
    assert calls == [
        (
            "rm-site",
            ["siteuser", str(root.resolve()), str(target)],
            {"fallback": ["rm", "-rf", "--", str(target)]},
        )
    ]


def test_delete_file_allows_laravel_storage_symlink_leaf(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows symlink permissions vary by developer environment")
    root = tmp_path / "site"
    public = root / "public_html"
    storage = root / "storage" / "app" / "public"
    public.mkdir(parents=True)
    storage.mkdir(parents=True)
    link = public / "storage"
    link.symlink_to(storage, target_is_directory=True)
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(file_manager.shell, "privileged", fake_privileged)
    monkeypatch.setattr(file_manager, "_clear_fastcgi_cache", lambda: None)

    deleted = file_manager.delete_file(_linux_website(root), "public_html/storage", allow_executable=True)

    assert deleted == str(link)
    assert calls == [
        (
            "rm-site",
            ["siteuser", str(root.resolve()), str(link)],
            {"fallback": ["rm", "-f", "--", str(link)]},
        )
    ]


def test_delete_entries_allows_laravel_storage_symlink_inside_deleted_tree(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows symlink permissions vary by developer environment")
    root = tmp_path / "site"
    public = root / "public_html"
    storage = root / "storage" / "app" / "public"
    public.mkdir(parents=True)
    storage.mkdir(parents=True)
    (public / "storage").symlink_to(storage, target_is_directory=True)
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(file_manager.shell, "privileged", fake_privileged)
    monkeypatch.setattr(file_manager, "_clear_fastcgi_cache", lambda: None)

    deleted = file_manager.delete_entries(_linux_website(root), ["public_html"], allow_executable=True)

    assert deleted == [str(public)]
    assert calls[0][1] == ["siteuser", str(root.resolve()), str(public)]


def test_tar_validation_allows_more_than_legacy_file_limit(tmp_path):
    archive_path = tmp_path / "many.tar.gz"
    destination = tmp_path / "public_html"
    destination.mkdir()

    with tarfile.open(archive_path, "w:gz") as archive:
        for index in range(10005):
            info = tarfile.TarInfo(f"many/file-{index}.txt")
            info.size = 0
            archive.addfile(info, io.BytesIO())

    with tarfile.open(archive_path, "r:gz") as archive:
        assert file_manager._tar_uncompressed_size(archive, destination, archive_path, allow_executable=True) == 0


def test_extract_tar_reopens_after_validation(tmp_path):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    archive_path = public / "site.tar.gz"
    content = b"hello from archive"

    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("app/index.php")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))

    file_manager.extract_archive(
        _website(root),
        "public_html/site.tar.gz",
        "public_html",
        allow_executable=True,
    )

    assert (public / "app" / "index.php").read_bytes() == content


def test_extract_tar_does_not_overwrite_source_archive(tmp_path):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    archive_path = public / "site.tar.gz"

    with tarfile.open(archive_path, "w:gz") as archive:
        payload = b"this would truncate the source archive without the guard"
        info = tarfile.TarInfo("site.tar.gz")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    original = archive_path.read_bytes()

    file_manager.extract_archive(
        _website(root),
        "public_html/site.tar.gz",
        "public_html",
        allow_executable=True,
    )

    assert archive_path.read_bytes() == original
    with tarfile.open(archive_path, "r:gz") as archive:
        assert archive.getnames() == ["site.tar.gz"]


def test_extract_zip_normalizes_backslashes_and_overwrites_existing_files(tmp_path):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    archive_path = public / "crm_update.zip"
    existing = public / "app" / "config.php"
    existing.parent.mkdir()
    existing.write_text("old", encoding="utf-8")

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("app\\config.php", b"new")
        archive.writestr("app\\cache\\", b"")
        archive.writestr("crm_update.zip", b"do not replace the source archive")

    original = archive_path.read_bytes()

    file_manager.extract_archive(
        _website(root),
        "public_html/crm_update.zip",
        "public_html",
        allow_executable=True,
    )

    assert existing.read_text(encoding="utf-8") == "new"
    assert (public / "app" / "cache").is_dir()
    assert archive_path.read_bytes() == original


def test_chmod_entry_updates_mode(tmp_path):
    if os.name == "nt":
        pytest.skip("Windows does not expose POSIX chmod semantics")
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    target = public / ".env"
    target.write_text("APP_ENV=local\n", encoding="utf-8")

    file_manager.chmod_entry(_website(root), "public_html/.env", "600")

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert file_manager.list_files(_website(root), "public_html")[0]["mode"] == "600"


def test_chmod_entry_accepts_read_only_file_mode(tmp_path):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    target = public / "index.php"
    target.write_text("<?php echo 'ok';\n", encoding="utf-8")

    file_manager.chmod_entry(_website(root), "public_html/index.php", "444")

    assert stat.S_IMODE(target.stat().st_mode) == 0o444


def _chmod_helper_calls(monkeypatch):
    """Capture the privileged chmod so the assertions do not need POSIX modes."""
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(file_manager.shell, "privileged", fake_privileged)
    return calls


@pytest.mark.parametrize(
    ("mode", "applied"),
    [("755", "00755"), ("700", "00700"), ("777", "00777"), ("444", "00444"), ("000", "00000")],
)
def test_chmod_entry_allows_any_ordinary_file_mode(tmp_path, monkeypatch, mode, applied):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    target = public / "deploy.sh"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    calls = _chmod_helper_calls(monkeypatch)

    file_manager.chmod_entry(_linux_website(root), "public_html/deploy.sh", mode)

    assert calls[0][1][-1] == applied


@pytest.mark.parametrize(
    ("mode", "applied"),
    [("777", "00777"), ("070", "00070"), ("2750", "02750"), ("750", "00750")],
)
def test_chmod_entry_allows_any_ordinary_directory_mode(tmp_path, monkeypatch, mode, applied):
    """A three digit mode must clear setgid instead of inheriting chmod's preserve rule."""
    root = tmp_path / "site"
    public = root / "public_html"
    (public / "uploads").mkdir(parents=True)
    calls = _chmod_helper_calls(monkeypatch)

    file_manager.chmod_entry(_linux_website(root), "public_html/uploads", mode)

    assert calls[0][1][-1] == applied


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("4755", "setuid"),
        ("1755", "sticky"),
        ("2644", "only be set on a folder"),
    ],
)
def test_chmod_entry_rejects_special_bits(tmp_path, monkeypatch, mode, message):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    target = public / "index.php"
    target.write_text("<?php echo 'ok';\n", encoding="utf-8")
    _chmod_helper_calls(monkeypatch)

    with pytest.raises(ValueError, match=message):
        file_manager.chmod_entry(_linux_website(root), "public_html/index.php", mode)


@pytest.mark.parametrize("mode", ["", "64", "88888", "9", "64x"])
def test_chmod_entry_rejects_malformed_modes(tmp_path, monkeypatch, mode):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    (public / "index.php").write_text("<?php echo 'ok';\n", encoding="utf-8")
    _chmod_helper_calls(monkeypatch)

    with pytest.raises(ValueError, match="octal"):
        file_manager.chmod_entry(_linux_website(root), "public_html/index.php", mode)


def test_chmod_entry_uses_the_root_helper_for_site_users(tmp_path, monkeypatch):
    """Running chmod as the site user would clear setgid on site folders."""
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(file_manager.shell, "privileged", fake_privileged)

    file_manager.chmod_entry(_linux_website(root), "public_html", "2750")

    assert calls == [("site-chmod", ["siteuser", str(root.resolve()), str(public.resolve()), "02750"])]


def test_write_text_file_clears_fastcgi_cache(tmp_path, monkeypatch):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    target = public / "index.php"
    target.write_text("old", encoding="utf-8")
    cleared = []
    monkeypatch.setattr(file_manager, "_clear_fastcgi_cache", lambda: cleared.append(True))

    file_manager.write_text_file(
        _website(root),
        "public_html/index.php",
        "new",
        allow_executable=True,
    )

    assert target.read_text(encoding="utf-8") == "new"
    assert cleared == [True]


def test_copy_entries_copies_files_and_folders(tmp_path):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    (public / "index.php").write_text("hello", encoding="utf-8")
    (public / "assets").mkdir()
    (public / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    destination = root / "copies"
    destination.mkdir()

    copied = file_manager.copy_entries(
        _website(root),
        ["public_html/index.php", "public_html/assets"],
        "copies",
        allow_executable=True,
    )

    assert len(copied) == 2
    assert (destination / "index.php").read_text(encoding="utf-8") == "hello"
    assert (destination / "assets" / "app.css").read_text(encoding="utf-8") == "body{}"
    assert (public / "index.php").exists()


def test_move_entries_moves_files_and_folders(tmp_path):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    (public / "index.php").write_text("hello", encoding="utf-8")
    (public / "assets").mkdir()
    (public / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    destination = root / "moved"
    destination.mkdir()

    moved = file_manager.move_entries(
        _website(root),
        ["public_html/index.php", "public_html/assets"],
        "moved",
        allow_executable=True,
    )

    assert len(moved) == 2
    assert (destination / "index.php").read_text(encoding="utf-8") == "hello"
    assert (destination / "assets" / "app.css").read_text(encoding="utf-8") == "body{}"
    assert not (public / "index.php").exists()
    assert not (public / "assets").exists()


def test_move_entries_rejects_folder_into_itself(tmp_path):
    root = tmp_path / "site"
    public = root / "public_html"
    nested = public / "assets" / "nested"
    nested.mkdir(parents=True)

    with pytest.raises(ValueError, match="folder into itself"):
        file_manager.move_entries(
            _website(root),
            ["public_html/assets"],
            "public_html/assets/nested",
            allow_executable=True,
        )
