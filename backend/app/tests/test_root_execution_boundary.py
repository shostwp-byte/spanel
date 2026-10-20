"""Root must never execute a file the panel account can write.

installer/files/spanel-sudoers hands `spanel` a NOPASSWD wildcard on
spanel-helper, and the helper's whole design is that it validates the argv it
receives. That boundary is worth nothing if root separately reads bytes the
same account owns.

installer/update.sh runs as root and does, with the cwd set to backend/:
    source .venv/bin/activate      - runs the file in the root shell
    .venv/bin/pip install ...      - runs a binary from the venv
    .venv/bin/python -m py_compile - `python -m` puts the cwd on sys.path

while both installers used to `chown -R spanel:spanel $APP_DIR/backend`, venv
included. Ownership alone is enough: an owner can always chmod. And the same
account can fire the root execution itself, because the `updates-panel-run`
helper verb takes no arguments and has no guard beyond SUDO_USER=spanel.

rsync carries --filter='protect /.venv', so anything planted there survives an
update; and the recreate guard only fires when .venv/bin/uvicorn is missing,
which on a healthy install it never is.
"""

from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"

# `chown -R <anything> <...>/backend` with nothing excluding the venv.
BARE_BACKEND_CHOWN = re.compile(
    r"""chown\s+-R\s+spanel:spanel\s+["']?\$\{?APP_DIR\}?/backend["']?\s*$""",
    re.MULTILINE,
)


def _scripts() -> list[tuple[str, str]]:
    return [
        (p.name, p.read_text(encoding="utf-8"))
        for p in (INSTALL_SCRIPT, UPDATE_SCRIPT)
    ]


def test_no_installer_hands_the_virtualenv_to_the_panel_account():
    for name, src in _scripts():
        hit = BARE_BACKEND_CHOWN.search(src)
        assert hit is None, (
            f"{name} recursively chowns backend/ to spanel without excluding "
            f".venv: {hit.group(0).strip() if hit else ''!r}. Root sources and "
            "executes files from that venv."
        )


def test_both_installers_prune_the_venv_from_the_backend_chown():
    for name, src in _scripts():
        assert '-path "$APP_DIR/backend/.venv" -prune' in src or (
            '-path "$dir/.venv" -prune' in src
        ), f"{name} must prune .venv when chowning backend/"


def test_both_installers_reclaim_a_venv_an_older_build_gave_away():
    """Existing servers already have a spanel-owned venv; the fix has to undo it."""
    for name, src in _scripts():
        assert "chown -R root:root" in src and ".venv" in src, (
            f"{name} must chown the venv back to root, or upgraded servers keep "
            "the weakness forever"
        )


def test_the_update_verb_that_fires_root_execution_is_still_argument_free():
    """Not a regression guard - a tripwire.

    This finding's severity rests on `spanel` being able to trigger the root
    updater itself with no argv to validate. If someone later gives the verb
    arguments, the reasoning in the audit record needs revisiting.
    """
    helper = (PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh").read_text(
        encoding="utf-8"
    )
    assert "updates-panel-run)" in helper


# --- the same rule, one directory over -------------------------------------
#
# frontend/ was left out of the venv fix. Both installers chowned it whole to
# spanel, and then root ran `npm run build` in it: package.json's "build" is
# `vite build`, npm resolves that to node_modules/.bin/vite, and Node loads the
# vite/rolldown/lightningcss graph - two compiled .node addons among it - into
# the uid-0 process. Measured on a live v1.0.141 server:
#
#   spanel:spanel  frontend/node_modules/vite/bin/vite.js   (test -w: writable)
#   root:root      backend/.venv/bin/python                 (test -w: refused)
#
# The exploitable sink is update.sh, not install.sh: install.sh builds at :560
# before the chown at :732, so that tree is root-owned throughout. update.sh
# calls ensure_panel_runtime_ownership at :1214 and :1364, both before the
# build, so root executes a tree it has already given away - and rsync's
# protect filter keeps node_modules across updates.

# `chown -R spanel:spanel <...>/frontend` anywhere, in any trailing form.
BARE_FRONTEND_CHOWN = re.compile(
    r"""chown\s+-R\s+spanel:spanel\s+["']?\$\{?APP_DIR\}?/frontend["']?""",
)


def test_no_installer_hands_node_modules_to_the_panel_account():
    for name, src in _scripts():
        hit = BARE_FRONTEND_CHOWN.search(src)
        assert hit is None, (
            f"{name} recursively chowns frontend/ to spanel without excluding "
            f"node_modules: {hit.group(0).strip() if hit else ''!r}. Root runs "
            "`npm run build` there, which executes node_modules/.bin/vite."
        )


def test_both_installers_prune_node_modules_from_the_frontend_chown():
    for name, src in _scripts():
        assert '-path "$APP_DIR/frontend/node_modules" -prune' in src or (
            '-path "$dir/node_modules" -prune' in src
        ), f"{name} must prune node_modules when chowning frontend/"


def test_both_installers_reclaim_node_modules_an_older_build_gave_away():
    """Every server already deployed has a spanel-owned node_modules.

    Pruning alone only protects fresh installs. Without the reclaim, v1.0.141
    and everything before it keeps the weakness for good, because the rsync
    protect filter means node_modules is never re-created either.
    """
    for name, src in _scripts():
        assert re.search(r"chown -R root:root .*node_modules", src), (
            f"{name} must chown node_modules back to root, or upgraded servers "
            "keep the weakness forever"
        )


def test_the_frontend_build_still_runs_as_root():
    """A tripwire, not a guard.

    The fix above is the right one *because* the build stays root: downgrading
    it with runuser would leave dist/ owned by spanel, moving the problem to the
    bundle nginx serves to an authenticated admin. If someone changes that, the
    reasoning here has to be revisited rather than silently inverted.
    """
    src = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"^\s*VITE_API_URL=/api npm run build", src, re.MULTILINE), (
        "the frontend build is no longer a bare root `npm run build`; re-check "
        "whether node_modules still needs to be root-owned"
    )
