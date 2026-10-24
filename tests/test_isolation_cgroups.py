"""Which tenant processes the classify scan still moves, and the IO limits it writes.

Real cgroup membership (a worker forked by spanel-php8.3-fpm@alice really
being in user-1001.slice) can only be checked on a server: see the report.
"""
import pytest

from _isolation_bash import BASH, helper_functions, run_bash

needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")


def _run(tmp_path, body: str) -> list[str]:
    script = "\n".join([
        "set -euo pipefail",
        helper_functions("render_io_max_line", "tenant_process_needs_scope"),
        body,
    ])
    return run_bash(tmp_path, script).stdout.splitlines()


@needs_bash
def test_processes_inside_the_account_slice_are_left_where_systemd_put_them(tmp_path):
    out = _run(tmp_path, r'''
check() { if tenant_process_needs_scope "$1" 1001; then echo move; else echo stay; fi; }
check "0::/user.slice/user-1001.slice/spanel-php8.3-fpm@alice.service"
check "0::/user.slice/user-1001.slice/session-4.scope"
check "0::/user.slice/user-1001.slice/spanel.scope"
check "0::/system.slice/php8.3-fpm.service"
check "0::/system.slice/cron.service"
check "0::/user.slice/user-1002.slice/session-9.scope"
check "0::/system.slice/spanel-app-7.service"
''')
    assert out == ["stay", "stay", "stay", "move", "move", "move", "stay"]


@needs_bash
def test_io_max_line_converts_mb_per_second_and_keeps_zero_unlimited(tmp_path):
    out = _run(tmp_path, r'''
render_io_max_line 8:0 100 50 1000 0
render_io_max_line 259:0 0 0 0 0
render_io_max_line 8:0 junk 1 "" 2
if render_io_max_line sda 1 1 1 1; then echo bad; else echo refused; fi
''')
    assert out == [
        "8:0 rbps=104857600 wbps=52428800 riops=1000 wiops=max",
        "259:0 rbps=max wbps=max riops=max wiops=max",
        "8:0 rbps=max wbps=1048576 riops=max wiops=2",
        "refused",
    ]
