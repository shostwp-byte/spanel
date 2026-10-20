"""A backup archive is untrusted input, not a privileged instruction.

RESTORABLE_BACKUP_KINDS deliberately accepts archives produced by a *different*
panel, so the manifest is written by whoever handed the operator the .tar.gz.
Restore used to read two things straight out of it: the account's role, and the
owner of an existing domain.

That meant a file could say "I am an admin, here is my password hash" and the
panel would create exactly that - and panel admin is the principal the root
sudo helper obeys. A second field could move a live domain onto the archive's
user and have the vhost rewritten to serve their files.

The import path for DirectAdmin (da_import._ensure_panel_user_record) always
did this correctly: hardcode end_user, generate the password locally. These
tests hold restore to the same rule.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKUP_SERVICE = PROJECT_ROOT / "backend" / "app" / "services" / "backup.py"
DA_IMPORT = PROJECT_ROOT / "backend" / "app" / "services" / "da_import.py"


def _source() -> str:
    return BACKUP_SERVICE.read_text(encoding="utf-8")


def _restore_body() -> str:
    """The body of restore_user_backup, where the manifest is trusted or not."""
    src = _source()
    start = src.index("def restore_user_backup(")
    return src[start : src.index("def _restore_applications(", start)]


def test_the_manifest_cannot_choose_the_role():
    body = _restore_body()
    assert "role=Role.end_user.value" in body, "restore must hardcode the created role"
    assert "normalize_role" not in body, (
        "normalize_role on a manifest value accepts 'admin' and 'super_admin' "
        "(the latter aliases to admin), which is how a file minted a panel admin"
    )


def test_the_manifest_cannot_choose_the_password():
    body = _restore_body()
    assert "hashed_password=hash_password(generated_password)" in body
    assert 'user_info.get("hashed_password")' not in body, (
        "copying the manifest's hash lets the archive's author log in as the "
        "account it just created"
    )


def test_restore_refuses_a_domain_owned_by_someone_else():
    body = _restore_body()
    assert "if website.owner_id != user.id:" in body, (
        "the primary-domain branch must refuse a live domain that belongs to "
        "another user, the way the alias loop already does"
    )
    # The alias guard was always there; it is what makes the primary-domain
    # omission an oversight rather than a design choice. Keep both.
    assert "_hostname_conflicts(db, alias_domain" in body


def test_the_generated_password_is_reported_to_the_operator():
    """The archive no longer sets it, so restore has to hand it back."""
    body = _restore_body()
    assert '"generated_password": generated_password' in body


def test_directadmin_import_still_hardcodes_end_user():
    """The in-repo precedent this fix is modelled on; if it drifts, say so."""
    src = DA_IMPORT.read_text(encoding="utf-8")
    assert 'role="end_user"' in src or "role='end_user'" in src
