"""The panel password and the SFTP password are two secrets now.

Before migration 0033 they were one. Every path that changed a panel password
also wrote it to the account's Linux user, and sshd offers password
authentication to that user on port 22 from anywhere. So:

  a brute force against SFTP yielded the panel password, and a panel password
  is root, because the panel account holds a NOPASSWD sudo wildcard on
  spanel-helper;

  and a leaked SFTP password - handed to a freelancer, pasted in a deploy
  script, stored by an FTP client - was a full panel compromise.

What the tests below are really guarding is one rule: **no password a user
typed into the panel may reach chpasswd.** Everything else here is the
consequence of that rule meeting a database full of accounts that were created
before it existed.
"""

import ast
from pathlib import Path

import pytest

from app.services import sftp_accounts, site_users

PROJECT_ROOT = Path(__file__).resolve().parents[3]
API = PROJECT_ROOT / "backend" / "app" / "api"
SERVICES = PROJECT_ROOT / "backend" / "app" / "services"
SCHEMAS = PROJECT_ROOT / "backend" / "app" / "schemas" / "schemas.py"

# Every module that can change or create a panel password.
PASSWORD_PATHS = [
    API / "users.py",
    API / "panel_settings.py",
    API / "provisioning.py",
    SERVICES / "da_import.py",
    SERVICES / "backup.py",
    PROJECT_ROOT / "backend" / "app" / "seed.py",
]


# --- the rule ---------------------------------------------------------------

def test_no_caller_hands_a_payload_password_to_the_linux_account():
    """The one rule, checked at every call site rather than argued about.

    set_panel_user_password writes straight to chpasswd. Its second argument
    must never be something the user typed: not payload.password, not the
    `password` local that gets hashed into the panel account.
    """
    offenders = []
    for path in PASSWORD_PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name not in {"set_panel_user_password", "ensure_panel_user"}:
                continue
            # The password argument is the second positional one.
            args = node.args
            if len(args) < 2:
                continue
            src = ast.unparse(args[1])
            if src in {"payload.password", "password", "payload.password or ''", '''payload.password or ""'''}:
                offenders.append(f"{path.name}:{node.lineno} -> {name}(..., {src})")
    assert not offenders, (
        "a panel password is being written to a Linux account:\n  "
        + "\n  ".join(offenders)
    )


def test_the_generated_password_is_the_only_thing_that_reaches_chpasswd():
    """Positively: every call site passes a generated value or nothing."""
    for path in PASSWORD_PATHS:
        src = path.read_text(encoding="utf-8")
        for line in src.split("\n"):
            if "ensure_panel_user(" not in line and "set_panel_user_password(" not in line:
                continue
            if line.lstrip().startswith("#") or "def " in line:
                continue
            assert "payload.password" not in line, f"{path.name}: {line.strip()}"


# --- the generated secret ---------------------------------------------------

def test_one_generator_serves_the_account_and_its_sub_accounts():
    """Two implementations would drift on strength or on the safe characters."""
    assert sftp_accounts.generate_password.__doc__
    src = Path(sftp_accounts.__file__).read_text(encoding="utf-8")
    assert "site_users.generate_login_password()" in src


def test_a_generated_password_never_trips_the_chpasswd_rule():
    """chpasswd reads `user:password` per line, so ':' would be a second account."""
    for _ in range(200):
        value = site_users.generate_login_password()
        assert len(value) >= 20
        assert not any(c in value for c in (":", "\r", "\n", "\x00"))


# --- the migration of accounts that already exist ---------------------------

def test_an_account_with_its_own_password_is_left_alone():
    assert site_users.retire_shared_login_password("someone", already_separate=True) is None


def test_a_legacy_account_has_its_shared_password_replaced(monkeypatch):
    """The important half.

    A legacy account's Linux password IS the old panel password. If a user
    rotates a compromised panel password and we leave the Linux side alone, the
    compromised value still opens SFTP - the rotation looks complete and is not.
    """
    captured = {}
    monkeypatch.setattr(site_users, "set_panel_user_password",
                        lambda username, password: captured.update(u=username, p=password))

    minted = site_users.retire_shared_login_password("someone", already_separate=False)

    assert minted is not None
    assert captured["u"] == "someone"
    assert captured["p"] == minted
    assert len(minted) >= 20


def test_the_migration_does_not_rotate_anyone(monkeypatch):
    """0033 adds a column and touches no password.

    Rotating every customer's SFTP password during an update would break live
    deploy scripts and FTP clients with no warning and no way to hand out the
    new secret.
    """
    migration = (PROJECT_ROOT / "backend" / "alembic" / "versions"
                 / "0033_sftp_password_split.py").read_text(encoding="utf-8")
    body = migration.split("def upgrade()", 1)[1].split("def downgrade", 1)[0]
    assert "add_column" in body
    for forbidden in ("chpasswd", "set_panel_user_password", "UPDATE users SET"):
        assert forbidden not in body, f"the migration must not {forbidden}"


# --- what the schemas now say -----------------------------------------------

def test_the_panel_password_no_longer_carries_a_linux_restriction():
    """Keeping it would be a lie: the value never reaches a Linux account."""
    src = SCHEMAS.read_text(encoding="utf-8")
    for cls in ("UserCreate", "UserPasswordUpdate", "AdminAccountUpdate"):
        block = src.split(f"class {cls}(BaseModel):", 1)[1].split("\nclass ", 1)[0]
        assert "_validate_linux_login_password" not in block, (
            f"{cls} still applies the Linux login rule to a panel password"
        )


def test_the_sftp_password_does_carry_it():
    src = SCHEMAS.read_text(encoding="utf-8")
    block = src.split("class SftpPasswordUpdate(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    assert "_validate_linux_login_password" in block


def test_the_error_message_no_longer_claims_the_passwords_are_synced():
    src = SCHEMAS.read_text(encoding="utf-8")
    assert "synced to the Linux/SFTP account" not in src, (
        "that sentence became false when the two were separated"
    )


@pytest.mark.parametrize("bad", ["has:a:colon", "line\nbreak", "carriage\rreturn"])
def test_an_sftp_password_that_would_confuse_chpasswd_is_refused(bad):
    from app.schemas.schemas import SftpPasswordUpdate
    with pytest.raises(ValueError):
        SftpPasswordUpdate(password=bad + "padding-to-twelve")


def test_a_panel_password_may_now_contain_a_colon():
    """The restriction existed only because the value was reused as a Linux one."""
    from app.schemas.schemas import UserPasswordUpdate
    assert UserPasswordUpdate(password="has:a:colon:ok").password == "has:a:colon:ok"


# --- the way out for accounts that are still sharing ------------------------

def test_there_is_an_endpoint_to_set_the_sftp_password():
    src = (API / "users.py").read_text(encoding="utf-8")
    assert '@router.post("/{user_id}/sftp-password")' in src
    block = src.split("def set_sftp_password(", 1)[1]
    # Generating is the default, and the only case where the value is certain
    # not to be a password the user already uses elsewhere.
    assert "payload.password or site_users.generate_login_password()" in block
    # Self-service needs the same step-up as any other credential change.
    assert "require_sensitive_action_step_up" in block
    # A password the user chose is never echoed back.
    assert '"password": sftp_password if generated else None' in block


def test_the_column_records_when_the_two_were_separated():
    from app.models.entities import User
    assert hasattr(User, "sftp_password_set_at")
    src = (PROJECT_ROOT / "backend" / "app" / "models" / "entities.py").read_text(encoding="utf-8")
    block = src.split("sftp_password_set_at", 1)[0]
    assert "NULL is load-bearing" in src, (
        "the meaning of NULL is the whole migration story and has to be written down"
    )


def test_every_creation_path_records_that_the_secrets_are_separate():
    """Otherwise NULL would claim an account shares a password when it does not."""
    for path in (API / "users.py", API / "provisioning.py",
                 SERVICES / "da_import.py", SERVICES / "backup.py",
                 PROJECT_ROOT / "backend" / "app" / "seed.py"):
        src = path.read_text(encoding="utf-8")
        assert "sftp_password_set_at=" in src, (
            f"{path.name} creates an account without saying whether its SFTP "
            "password is its own"
        )


# --- deleting a panel account actually deletes it ---------------------------
#
# Found while cleaning up after the end-to-end test above: the probe account's
# home was gone but its passwd entry and its group were still there, and the
# helper had reported success. Both leaks predate this change; SFTP just made
# them easy to hit, because an SFTP session is a process owned by that uid.

HELPER = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"


def _delete_panel_user_body() -> str:
    src = HELPER.read_text(encoding="utf-8")
    return src.split("delete_panel_user_runtime() {", 1)[1].split("\n}", 1)[0]


def test_deleting_a_panel_user_forces_it_through():
    """userdel races with the pkill on the line above it.

    It decides "in use" by scanning for processes owned by the uid, so anything
    still winding down - an SFTP session, a PHP worker - makes it exit 8, which
    `|| true` then reports as success. Measured on a live server.
    """
    body = _delete_panel_user_body()
    assert 'userdel -f "$user"' in body, (
        "without -f, deleting an account that had an SFTP session open leaves "
        "the passwd entry behind and says it succeeded"
    )
    assert "userdel -r" not in body, (
        "the home is removed explicitly below; -r would delete a path this "
        "function never checked"
    )


def test_the_group_is_emptied_before_it_is_deleted():
    """groupdel refuses a group that still has members.

    ensure_panel_user_home adds www-data to it so nginx can read the site, so
    groupdel has always refused, silently, leaving one orphan group per deleted
    account.
    """
    body = _delete_panel_user_body()
    assert "gpasswd -d www-data" in body, (
        "www-data has to leave the group before groupdel can remove it"
    )
    assert body.index("gpasswd -d www-data") < body.index('groupdel "$user"')


def test_the_installer_never_prints_the_sftp_password():
    """seed.py runs inside install.sh.

    Its stdout lands in terminal scrollback, CI logs and support tickets.
    CodeQL flagged the first version of this as clear-text logging of a
    credential (backend/app/seed.py, high severity) and was right to: the admin
    sets a password they can see from the panel instead, which is the only
    place it is ever readable.
    """
    src = (PROJECT_ROOT / "backend" / "app" / "seed.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "print":
            continue
        printed = " ".join(ast.unparse(a) for a in node.args)
        assert "sftp_password" not in printed, (
            f"seed.py line {node.lineno} prints the SFTP password: {printed}"
        )
