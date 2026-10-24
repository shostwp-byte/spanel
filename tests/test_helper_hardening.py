"""The root helper must not let the caller's working directory reach Python."""
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "installer" / "files" / "spanel-helper.sh"


def test_helper_leaves_the_callers_directory_before_running_python():
    """sudo keeps the API's working directory (the spanel-owned backend/), and
    `python3 - <<PY` searches it first, so a planted json.py would run as root."""
    text = HELPER.read_text(encoding="utf-8")
    lines = text.splitlines()
    first_python = next(i for i, line in enumerate(lines) if "python3" in line and not line.lstrip().startswith("#"))
    header = lines[:first_python]
    assert "cd /" in header
    assert "export PYTHONSAFEPATH" in header
    assert header.index("cd /") > next(i for i, line in enumerate(header) if line.startswith("set -euo pipefail"))
