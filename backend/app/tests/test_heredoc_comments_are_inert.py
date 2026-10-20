"""Prose written into a generated file must not run as a command.

The helper builds config files with heredocs, and most of them are unquoted on
purpose - they interpolate a port, a path, a user name. Unquoted means the
shell expands the body, and that expansion does not stop at the parts you
meant it to reach.

A comment written in the habit of markdown was enough:

    # calls the unit ssh.service. The `+` is a disjunction in fail2ban, so the

The backticks are command substitution. Root ran `+`, and the helper printed
`line 1135: +: command not found` in the middle of installing a firewall
addon. It was harmless because `+` is not a command. `$(rm ...)` in the same
position would not have been.

So: a comment line inside an unquoted heredoc may not contain command
substitution. Substitution on a real config line stays allowed - that is what
unquoted heredocs are for, and the unattended-upgrades block uses it
deliberately.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = sorted(
    [PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh",
     PROJECT_ROOT / "installer" / "install.sh",
     PROJECT_ROOT / "installer" / "update.sh"]
)

# An opening heredoc whose delimiter is NOT quoted: <<NAME or <<-NAME, but not
# <<'NAME'. Only these expand their body.
UNQUOTED = re.compile(r"<<-?(?!')([A-Za-z_][A-Za-z0-9_]*)")
SUBSTITUTION = re.compile(r"`|\$\(")


def _unquoted_heredoc_bodies(text):
    """Yield (delimiter, line number, line) for every line inside one."""
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = UNQUOTED.search(lines[index])
        if not match:
            index += 1
            continue
        delimiter = match.group(1)
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].strip() != delimiter:
            yield delimiter, cursor + 1, lines[cursor]
            cursor += 1
        index = cursor + 1


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_a_comment_in_a_generated_file_never_runs(script):
    offenders = [
        f"{script.name}:{number}: {line.strip()[:88]}"
        for _, number, line in _unquoted_heredoc_bodies(script.read_text(encoding="utf-8"))
        if line.lstrip().startswith("#") and SUBSTITUTION.search(line)
    ]
    assert not offenders, (
        "backticks and $( inside an unquoted heredoc are command substitution, "
        "and root runs the result - even in a comment:\n  " + "\n  ".join(offenders)
    )


def test_the_scanner_would_have_caught_the_real_one():
    """Guard the guard: the regex has to match the line that got through."""
    sample = (
        'cat >"$FILE" <<EOF\n'
        "# calls the unit ssh.service. The `+` is a disjunction, so the\n"
        "journalmatch = _SYSTEMD_UNIT=${unit}\n"
        "EOF\n"
    )
    hits = [line for _, _, line in _unquoted_heredoc_bodies(sample)
            if line.lstrip().startswith("#") and SUBSTITUTION.search(line)]
    assert len(hits) == 1


def test_a_quoted_heredoc_is_left_alone():
    """<<'EOF' expands nothing, so prose in it is prose."""
    sample = (
        "cat >\"$FILE\" <<'EOF'\n"
        "# a `backtick` here is inert\n"
        "EOF\n"
    )
    assert list(_unquoted_heredoc_bodies(sample)) == []


def test_substitution_on_a_real_config_line_is_still_allowed():
    """Unquoted heredocs exist to do this; only comments are the rule."""
    sample = (
        'cat >"$FILE" <<APT\n'
        'Automatic-Reboot "$([[ "$reboot" == on ]] && echo true || echo false)";\n'
        "APT\n"
    )
    offenders = [line for _, _, line in _unquoted_heredoc_bodies(sample)
                 if line.lstrip().startswith("#") and SUBSTITUTION.search(line)]
    assert offenders == []
