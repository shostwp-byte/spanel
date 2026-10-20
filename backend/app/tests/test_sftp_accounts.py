"""Per-website SFTP sub-accounts.

The feature hands out a real Linux login. Four things carry its safety, and
each one is asserted here rather than left to review:

  the namespace     A sub-account name always starts with sftp_, and a panel
                    user may never take a name in that space. That disjointness
                    is the whole proof that no sub-account verb can be aimed at
                    a panel user, or the reverse.

  the chroot root   /var/lib/spanel-sftp, owned by root. NOT under
                    /var/lib/spanel, which belongs to the panel account. A
                    chroot root the confined account can write is not a
                    boundary - the same lesson the frontend/node_modules
                    finding taught, one directory over.

  the shared uid    Sub-accounts share the site owner's uid so uploaded files
                    keep working ownership. That makes the chroot the only
                    boundary, so nothing may weaken it, and teardown must never
                    reach for `pkill -u` or `userdel -r`.

  the sweeps        A deleted user, a deleted website and a suspended account
                    all have to reach these credentials. Missing one leaves a
                    working password pointing at somebody's files.
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.entities import Base, SftpAccount, User
from app.services import sftp_accounts, site_users, teardown

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


def _scripts() -> list[tuple[str, str]]:
    return [
        (p.name, p.read_text(encoding="utf-8"))
        for p in (INSTALL_SCRIPT, UPDATE_SCRIPT)
    ]


# --- the namespace ----------------------------------------------------------

def test_a_generated_login_fits_linux_and_stays_in_its_namespace():
    """32 characters is a hard Linux limit; the prefix is our own rule."""
    name = sftp_accounts.linux_user_for("a-very-long-panel-username", 4242, "deploy bot")
    assert len(name) <= 32, f"{name!r} is {len(name)} characters"
    assert name.startswith("sftp_")
    assert sftp_accounts.SUB_USER_RE.fullmatch(name)


def test_the_generated_login_is_deterministic_and_collision_resistant():
    """Same inputs converge; different owners asking for "deploy" do not collide."""
    first = sftp_accounts.linux_user_for("alice", 7, "deploy")
    assert first == sftp_accounts.linux_user_for("alice", 7, "deploy")
    assert first != sftp_accounts.linux_user_for("bob", 7, "deploy")
    assert first != sftp_accounts.linux_user_for("alice", 8, "deploy")


def test_a_panel_user_may_not_take_a_name_in_the_sub_account_namespace():
    """If the two namespaces could overlap, the helper's prefix check proves nothing."""
    with pytest.raises(ValueError):
        site_users.validate_linux_user("sftp_alice_deploy_ab12cd")


def test_the_helper_refuses_the_prefix_for_panel_user_verbs():
    """The same rule on the root side, where it actually matters."""
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("require_linux_user() {", 1)[1].split("\n}", 1)[0]
    assert "sftp_*)" in body, (
        "require_linux_user must refuse the sftp_ prefix, or a panel-user verb "
        "could be aimed at a sub-account"
    )


def test_python_and_the_helper_agree_on_what_a_sub_account_name_is():
    """Two copies of one rule drift. A mismatch here is a real bug, not style."""
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r'\[\[ "\$1" =~ \^(sftp_\[a-z0-9_\]\{3,26\})\$ \]\]', helper)
    assert match, "require_sftp_sub_user's pattern is not where this test expects it"
    assert sftp_accounts.SUB_USER_RE.pattern == f"^{match.group(1)}$".replace("\\", "")


# --- the chroot root --------------------------------------------------------

def test_the_chroot_root_is_not_inside_the_panel_accounts_directory():
    """/var/lib/spanel is spanel-owned. A chroot root there is not a boundary.

    OpenSSH refuses such a chroot outright, and it is right to: the account
    being confined could rewrite the confinement.
    """
    path = sftp_accounts.chroot_path_for("sftp_alice_deploy_ab12cd")
    assert path.startswith("/var/lib/spanel-sftp/")
    assert not path.startswith("/var/lib/spanel/")


def test_both_installers_create_the_chroot_root_owned_by_root():
    for name, src in _scripts():
        assert "install -d -o root -g root -m 0755 /var/lib/spanel-sftp" in src, (
            f"{name} must create the SFTP chroot root owned by root"
        )


def test_both_installers_ship_the_sub_account_sshd_block():
    for name, src in _scripts():
        assert "Match Group spanel-sftp-site" in src, f"{name} is missing the Match block"
        assert "ChrootDirectory /var/lib/spanel-sftp/%u" in src, (
            f"{name} must chroot sub-accounts into the root-owned tree"
        )
        assert "ForceCommand internal-sftp" in src


def test_the_sub_account_block_comes_after_the_panel_user_block():
    """sshd applies every matching block in order and lets later ones win."""
    for name, src in _scripts():
        first = src.index("Match Group spanel-sftp\n")
        second = src.index("Match Group spanel-sftp-site")
        assert first < second, f"{name} has the blocks in an order that inverts the chroot"


def test_the_sub_account_gets_no_shell_and_no_forwarding():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("ensure_sftp_account() {", 1)[1].split("\n}", 1)[0]
    assert "/usr/sbin/nologin" in body
    for name, src in _scripts():
        block = src.split("Match Group spanel-sftp-site", 1)[1].split("# END", 1)[0]
        for directive in ("PermitTTY no", "AllowTcpForwarding no", "PermitTunnel no", "X11Forwarding no"):
            assert directive in block, f"{name}: sub-accounts must set {directive}"


# --- the shared uid ---------------------------------------------------------

def test_the_sub_account_shares_the_site_owners_uid():
    """So uploads land with ownership PHP-FPM and WordPress can still use."""
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("ensure_sftp_account() {", 1)[1].split("\nset_sftp_account_password", 1)[0]
    assert 'useradd -o -u "$owner_uid"' in body, (
        "without -o -u the sub-account writes files the site owner cannot manage"
    )


def test_teardown_never_kills_by_uid_or_removes_the_shared_home():
    """Both would destroy the site owner, who shares the uid.

    `pkill -u` matches the uid, so it would kill the customer's PHP workers and
    cron jobs. `userdel -r` would delete a home directory that is not this
    account's to delete.
    """
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("delete_sftp_account() {", 1)[1].split("\nsite_php_pool_glob", 1)[0]
    assert "pkill -u" not in body, "pkill -u would kill the site owner's processes"
    assert "userdel -r" not in body, (
        "userdel -r would aim a recursive delete at the recorded home, which is "
        "'/' - the chroot root"
    )
    assert 'userdel -f "$sub"' in body, (
        "userdel decides 'in use' by scanning processes owned by the UID, and this "
        "account shares the site owner's UID. Without -f it exits 8 and leaves the "
        "passwd entry behind for any customer who has a PHP worker running - which "
        "is all of them. Found by deleting a real account on a live server."
    )


def test_sessions_are_matched_by_login_name_not_uid():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("sftp_account_kill_sessions() {", 1)[1].split("\n}", 1)[0]
    assert "pkill -f" in body and "sshd:" in body, (
        "with a shared uid the only way to reach just this account's sessions "
        "is sshd's process title, which carries the login name"
    )


# --- password handling ------------------------------------------------------

@pytest.mark.parametrize("bad", ["short", "has:a:colon:in:it", "line\nbreak", "carriage\rreturn"])
def test_passwords_that_would_confuse_chpasswd_are_refused(bad):
    """chpasswd reads `user:password` per line.

    A ':' or a newline in the value would let a password describe a second
    account, so both are refused before the value ever reaches the helper.
    """
    with pytest.raises(ValueError):
        sftp_accounts.validate_password(bad)


def test_a_generated_password_always_passes_the_rule_it_is_checked_against():
    for _ in range(50):
        assert sftp_accounts.validate_password(sftp_accounts.generate_password())


def test_the_helper_enforces_the_same_password_rule_itself():
    """The API is not the boundary; the root helper is."""
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("set_sftp_account_password() {", 1)[1].split("\n}", 1)[0]
    assert "${#password} -ge 12" in body and "${#password} -le 72" in body
    assert "*:*" in body


# --- the sweeps -------------------------------------------------------------

@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _user(db, username="tenant"):
    user = User(username=username, email=f"{username}@x.test",
                hashed_password="x", role="end_user")
    db.add(user)
    db.flush()
    return user


def _account(db, owner_id, website_id=1, label="deploy"):
    account = SftpAccount(
        owner_id=owner_id,
        website_id=website_id,
        label=label,
        linux_user=sftp_accounts.linux_user_for(f"u{owner_id}", website_id, label),
        chroot_path="/var/lib/spanel-sftp/x",
    )
    db.add(account)
    db.flush()
    return account


def test_deleting_a_user_takes_their_sftp_logins_with_them(db, monkeypatch):
    removed = []
    monkeypatch.setattr(teardown.sftp_accounts, "delete_account", removed.append)

    user = _user(db)
    account = _account(db, user.id)

    assert teardown.purge_owner_sftp_accounts(db, user.id) == [account.linux_user]
    assert removed == [account.linux_user], "the Linux user and chroot must go too"
    assert db.query(SftpAccount).filter_by(owner_id=user.id).count() == 0


def test_a_failing_removal_does_not_strand_the_account(db, monkeypatch):
    """The chroot may already be unmounted, or the Linux user already gone."""
    def boom(linux_user):
        raise RuntimeError("no such user")

    monkeypatch.setattr(teardown.sftp_accounts, "delete_account", boom)
    user = _user(db)
    _account(db, user.id)

    assert teardown.purge_owner_sftp_accounts(db, user.id) != []
    assert db.query(SftpAccount).filter_by(owner_id=user.id).count() == 0


def test_deleting_a_website_takes_only_that_websites_logins(db, monkeypatch):
    monkeypatch.setattr(teardown.sftp_accounts, "delete_account", lambda u: None)
    user = _user(db)
    kept = _account(db, user.id, website_id=2, label="other")
    _account(db, user.id, website_id=1, label="deploy")

    teardown.purge_website_sftp_accounts(db, 1)

    remaining = db.query(SftpAccount).all()
    assert [a.linux_user for a in remaining] == [kept.linux_user]


def test_suspending_an_account_locks_its_sftp_logins(db, monkeypatch):
    """Locking the site's Linux user does NOT reach these.

    A sub-account shares the site user's uid but has its own name, and
    `usermod -L` works on names - so a suspension that skipped this step would
    leave the customer a working write credential for the sites it just
    disabled.
    """
    calls = []
    monkeypatch.setattr(teardown.sftp_accounts, "set_locked",
                        lambda user, locked: calls.append((user, locked)))
    user = _user(db)
    account = _account(db, user.id)

    teardown.set_owner_sftp_accounts_locked(db, user.id, True)
    assert calls == [(account.linux_user, True)]
    assert db.query(SftpAccount).filter_by(id=account.id).first().is_active is False

    teardown.set_owner_sftp_accounts_locked(db, user.id, False)
    assert calls[-1] == (account.linux_user, False)
    assert db.query(SftpAccount).filter_by(id=account.id).first().is_active is True


def test_the_three_lifecycle_paths_all_reach_sftp_accounts():
    """They drifted once for databases and applications. Not again."""
    api = PROJECT_ROOT / "backend" / "app" / "api"
    users_py = (api / "users.py").read_text(encoding="utf-8")
    websites_py = (api / "websites.py").read_text(encoding="utf-8")
    teardown_py = (PROJECT_ROOT / "backend" / "app" / "services" / "teardown.py").read_text(encoding="utf-8")

    assert "purge_owner_sftp_accounts(db, owner_id)" in teardown_py, (
        "the shared user sweep must include SFTP accounts"
    )
    assert "teardown.purge_website_sftp_accounts(db, website.id)" in websites_py
    assert "teardown.set_owner_sftp_accounts_locked(db, user.id, True)" in users_py
    assert "teardown.set_owner_sftp_accounts_locked(db, user.id, False)" in users_py


# --- scope ------------------------------------------------------------------

def test_a_sub_account_is_scoped_to_a_website_root_not_a_subdirectory():
    """apps/<name> resolves to depth 2 and a nested path is deeper than 1."""
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("ensure_sftp_account() {", 1)[1].split("\nset_sftp_account_password", 1)[0]
    assert 'managed_root_depth "$relative"' in body
    assert '"$relative" != */*' in body, (
        "without this an application root or a nested directory would be accepted"
    )


@pytest.mark.parametrize("bad", ["", "a", "x" * 33, "has/slash", "has.dot", "bad;semi"])
def test_labels_that_are_not_labels_are_refused(bad):
    with pytest.raises(ValueError):
        sftp_accounts.validate_label(bad)


# --- a gate needs a key -----------------------------------------------------
#
# sftp_accounts_limit shipped as a column on user_packages and users, read by
# the API to refuse requests, and settable by nothing at all: it was missing
# from UserPackageCreate, UserPackageUpdate, UserUpdate, the packages endpoints
# and _apply_package_limits. It defaults to 0, so every account was refused and
# no administrator could change that. Reported from real use: "log in as a user
# and you cannot create an account".
#
# These tests are written against every limit rather than just this one,
# because the bug is the shape, not the column.

SCHEMAS_PY = PROJECT_ROOT / "backend" / "app" / "schemas" / "schemas.py"
PACKAGES_PY = PROJECT_ROOT / "backend" / "app" / "api" / "packages.py"
USERS_PY = PROJECT_ROOT / "backend" / "app" / "api" / "users.py"


def _package_limit_columns() -> list[str]:
    from app.models.entities import UserPackage
    skip = {"id", "name", "slug", "created_at", "users"}
    return [c.name for c in UserPackage.__table__.columns if c.name not in skip]


def test_every_package_limit_can_be_set_through_the_api():
    """A column the API reads but nothing can write is a gate with no key."""
    schemas = SCHEMAS_PY.read_text(encoding="utf-8")
    create = schemas.split("class UserPackageCreate(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    update = schemas.split("class UserPackageUpdate(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    missing = [c for c in _package_limit_columns()
               if f"{c}:" not in create or f"{c}:" not in update]
    assert not missing, (
        "these package limits cannot be set by any caller: " + ", ".join(missing)
    )


def test_every_package_limit_is_handled_by_the_packages_endpoints():
    src = PACKAGES_PY.read_text(encoding="utf-8")
    missing = [c for c in _package_limit_columns() if c not in src]
    assert not missing, (
        "the packages API never reads these fields off the payload: " + ", ".join(missing)
    )


def test_assigning_a_package_carries_its_sftp_limit_to_the_user():
    """_apply_package_limits is what makes a package mean anything.

    terminal_enabled sat unread on UserPackage for months for want of this
    line; sftp_accounts_limit was heading the same way.
    """
    src = USERS_PY.read_text(encoding="utf-8")
    body = src.split("def _apply_package_limits(", 1)[1].split("\ndef ", 1)[0]
    assert "user.sftp_accounts_limit = package.sftp_accounts_limit" in body


def test_an_admin_can_grant_the_limit_to_one_user():
    """Per user as well as per package, like website_limit and storage_limit_mb."""
    schemas = SCHEMAS_PY.read_text(encoding="utf-8")
    block = schemas.split("class UserUpdate(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    assert "sftp_accounts_limit:" in block
    users = USERS_PY.read_text(encoding="utf-8")
    assert "user.sftp_accounts_limit = payload.sftp_accounts_limit" in users


def test_the_refusal_says_where_to_change_it():
    """"Not included in your package" is true and useless on its own."""
    src = (PROJECT_ROOT / "backend" / "app" / "api" / "sftp_accounts.py").read_text(encoding="utf-8")
    block = src.split("does not include SFTP accounts", 1)[1].split(")", 1)[0]
    assert "administrator" in block.lower()


def test_the_feature_is_not_dead_on_arrival():
    """A limit of 0 everywhere is not caution, it is a feature that refuses.

    0032 shipped sftp_accounts_limit defaulting to 0 on every package and every
    user. Nothing could raise it either, so every customer got
    "your hosting package does not include SFTP accounts" and there was no
    control anywhere that changed that. Reported twice from real use.

    A sub-account grants no access the customer does not already have - it
    reaches one site as the uid that owns it, and the file manager reaches all
    of them. What it gates is the ability to delegate a narrower credential
    than the account password. That is not something to default off.
    """
    from app.models.entities import User, UserPackage
    for model in (User, UserPackage):
        default = model.__table__.columns["sftp_accounts_limit"].default
        value = default.arg if default is not None else None
        assert value and value > 0, (
            f"{model.__name__}.sftp_accounts_limit defaults to {value!r}; new "
            "accounts would be unable to use the feature at all"
        )


def test_existing_accounts_are_backfilled_rather_than_left_refusing():
    """Every account that exists today carries the shipped 0."""
    migration = (PROJECT_ROOT / "backend" / "alembic" / "versions"
                 / "0034_sftp_accounts_default_limit.py").read_text(encoding="utf-8")
    body = migration.split("def upgrade()", 1)[1].split("def downgrade", 1)[0]
    assert "UPDATE users SET sftp_accounts_limit" in body
    assert "UPDATE user_packages SET sftp_accounts_limit" in body
    # Only rows still at the shipped default, so an admin's deliberate 0 - and a
    # second run of the migration - are both left alone.
    assert body.count("WHERE sftp_accounts_limit = 0") == 2, (
        "the backfill must not overwrite a limit somebody chose"
    )
