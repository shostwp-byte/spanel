"""clamd's stock limits, and why the panel raises them.

Two settings decide whether an upload is really examined. StreamMaxLength is a
hard refusal - an INSTREAM body over it is rejected mid-send, which the panel
sees as a broken pipe. MaxFileSize is not a refusal at all: clamd answers OK on
a larger file without reading it, so "clean" and "never looked" come back
identical. Padding a payload past the stock 25 MB walked it through.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


@pytest.fixture(scope="module")
def helper_source():
    return HELPER_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tuning_body(helper_source):
    body = helper_source[helper_source.index("tune_clamd_limits() {"):]
    return body[:body.index("install_clamav_engine() {")]


def test_clamd_limits_are_raised_to_something_a_customer_actually_uploads(helper_source):
    """Plugin bundles, theme archives, site backups - not mail attachments."""
    assert 'CLAMD_MAX_FILE_SIZE="256M"' in helper_source
    assert 'CLAMD_MAX_SCAN_SIZE="512M"' in helper_source


def test_streammaxlength_is_not_left_below_maxfilesize(tuning_body):
    """Raising MaxFileSize alone would move the wall, not remove it.

    The panel scans by INSTREAM, so StreamMaxLength is the hard gate: clamd
    rejects a larger body mid-send. MaxFileSize decides whether what does get
    through is read at all.
    """
    assert '"StreamMaxLength ${CLAMD_MAX_FILE_SIZE}"' in tuning_body


def test_maxscansize_leaves_room_for_an_archive_to_expand(tuning_body):
    assert '"MaxScanSize ${CLAMD_MAX_SCAN_SIZE}"' in tuning_body


def test_tuning_is_idempotent_enough_not_to_restart_clamd_for_nothing(tuning_body):
    assert "continue" in tuning_body and "changed=0" in tuning_body
    assert re.search(r'if \[\[ "\$changed" -eq 1 \]\]', tuning_body), (
        "clamd should only be restarted when the file actually changed"
    )


def test_a_missing_clamd_conf_is_not_an_error(tuning_body):
    """clamd is optional; the panel runs without it."""
    assert 'nothing to tune' in tuning_body
    assert 'return 0' in tuning_body


def test_an_existing_setting_is_replaced_not_duplicated(tuning_body):
    """Two MaxFileSize lines and clamd reads whichever it likes."""
    assert "sed -i -E" in tuning_body
    assert "grep -qE" in tuning_body


def test_installing_the_daemon_tunes_it(helper_source):
    """The limits live in clamd.conf, so they matter when clamd does.

    Installing the engine alone no longer touches them: maldet passes its own
    --max-filesize on the clamscan command line and never reads clamd.conf.
    """
    start = helper_source.index("install_clamav_daemon() {")
    body = helper_source[start:helper_source.index("\n}\n", start)]
    assert "tune_clamd_limits" in body


def test_the_engine_install_does_not_tune_a_daemon_it_did_not_install(helper_source):
    start = helper_source.index("install_clamav_engine() {")
    body = helper_source[start:helper_source.index("\n}\n", start)]
    assert "tune_clamd_limits" not in body


def test_the_tuning_verb_exists_and_takes_no_arguments(helper_source):
    assert "clamav-tune)" in helper_source
    body = helper_source[helper_source.index("clamav-tune)"):]
    body = body[:body.index(";;")]
    assert "usage: clamav-tune" in body


@pytest.mark.parametrize("script", [INSTALL_SCRIPT, UPDATE_SCRIPT], ids=["install", "update"])
def test_both_installers_apply_the_tuning(script):
    """The servers that need this are the ones already running."""
    text = script.read_text(encoding="utf-8")
    assert "spanel-helper clamav-tune" in text, (
        f"{script.name} never raises clamd's limits on an existing install"
    )


# --- the mistake this nearly shipped with ----------------------------------

@pytest.mark.parametrize("script", [INSTALL_SCRIPT, UPDATE_SCRIPT], ids=["install", "update"])
def test_the_installers_never_call_the_helper_as_root(script):
    """The helper refuses anything that is not sudo from 'spanel'.

    The first version of the clamd tuning called it directly from the
    installer, which runs as root. The helper printed its refusal, `|| true`
    swallowed it, and the update reported success with clamd untouched - the
    same silent failure the memory guard had, one mechanism further on.

    A systemd unit is the one legitimate exception: those set
    Environment=SUDO_USER=spanel, so the guard is satisfied.
    """
    offenders = []
    for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if "spanel-helper" not in stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("ExecStart="):
            continue
        # A line that only talks about the helper. "…/spanel-helper and
        # /etc/sudoers.d/spanel" otherwise reads as the verb "and".
        if stripped.split(" ", 1)[0] in {"log", "echo", "printf"}:
            continue
        # Installing, chmod-ing or sed-ing the file is not invoking it.
        if re.search(r"/usr/local/sbin/spanel-helper\s+[a-z][a-z0-9-]*", stripped) is None:
            continue
        if "sudo -u spanel" in stripped:
            continue
        offenders.append(f"{script.name}:{number}: {stripped}")

    assert not offenders, (
        "the helper refuses a direct root call; these would fail silently:\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("script", [INSTALL_SCRIPT, UPDATE_SCRIPT], ids=["install", "update"])
def test_a_failed_tuning_says_so_instead_of_disappearing(script):
    """`|| true` is how both of these bugs stayed invisible."""
    text = script.read_text(encoding="utf-8")
    index = text.index("spanel-helper clamav-tune")
    tail = text[index:index + 300]
    assert "warning" in tail, "a refusal has to reach the operator"
    assert "clamav-tune >/dev/null || true" not in tail
