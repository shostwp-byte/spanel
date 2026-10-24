"""Run individual spanel-helper.sh functions under bash with stubbed commands.

The helper is one script whose body dispatches on argv, so it cannot be
sourced. These tests lift the functions they need out of it by name and run
them against a scratch tree, with systemctl, id and friends replaced by
recorders - the same logic the server runs, minus the side effects.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
BASH = shutil.which("bash")


def helper_text() -> str:
    return HELPER.read_text(encoding="utf-8")


def helper_functions(*names: str) -> str:
    text = helper_text()
    out = []
    for name in names:
        match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", text, re.M | re.S)
        assert match, f"function {name} not found in spanel-helper.sh"
        out.append(match.group(0))
    return "\n".join(out)


def helper_assignment(name: str) -> str:
    match = re.search(rf"^{re.escape(name)}=.*$", helper_text(), re.M)
    assert match, f"{name}= not found in spanel-helper.sh"
    return match.group(0)


def run_bash(tmp_path: Path, script: str, *, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    # Script in a file, bytes not text: a -c argument and text mode both
    # rewrite backslashes / line endings on their way to bash on Windows.
    script_file = tmp_path / f"run-{abs(hash(script))}.sh"
    script_file.write_bytes(script.encode("utf-8"))
    import os

    run_env = dict(os.environ)
    run_env.update(env or {})
    proc = subprocess.run([BASH, script_file.as_posix()], capture_output=True, env=run_env)
    result = subprocess.CompletedProcess(
        proc.args, proc.returncode,
        proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"),
    )
    if check and result.returncode != 0:
        raise AssertionError(f"bash exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result
