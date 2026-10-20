"""Rewriting every vhost on a server without testing the config N times.

nginx re-parses everything on each `nginx -t`, ModSecurity rules included.
With CRS enabled and 22 sites that is 22 parses of 18,503 rules in one update,
which is what ran a swapless 8 GB server out of memory and took nginx down
partway through. These pin the batching, and - more importantly - that
batching did not cost the rollback that made the per-site path safe.
"""

import pytest

from app.services import nginx


class FakeResult:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_privileged(verb, *a, **k):
        seen.append(verb)
        if verb == "nginx-test":
            return FakeResult(fake_privileged.test_rc, fake_privileged.test_err)
        return FakeResult(0)

    fake_privileged.test_rc = 0
    fake_privileged.test_err = ""
    monkeypatch.setattr(nginx.shell, "privileged", fake_privileged)
    return seen, fake_privileged


def _write(tmp_path, name, text, old=None):
    target = tmp_path / name
    target.write_text(text, encoding="utf-8")
    nginx._test_and_reload(target, old)
    return target


def test_each_write_tests_and_reloads_on_its_own_by_default(tmp_path, calls):
    seen, _ = calls

    for i in range(3):
        _write(tmp_path, f"s{i}.conf", "new")

    assert seen.count("nginx-test") == 3
    assert seen.count("nginx-reload") == 3


def test_a_batch_tests_and_reloads_once(tmp_path, calls):
    seen, _ = calls

    with nginx.deferred_reload():
        for i in range(22):
            _write(tmp_path, f"s{i}.conf", "new")
        assert "nginx-test" not in seen, "nothing may be tested inside the batch"

    assert seen.count("nginx-test") == 1
    assert seen.count("nginx-reload") == 1


def test_a_failing_batch_puts_every_file_back_and_never_reloads(tmp_path, calls):
    seen, fake = calls
    for i in range(3):
        (tmp_path / f"s{i}.conf").write_text(f"old {i}", encoding="utf-8")
    fake.test_rc = 1
    fake.test_err = "invalid value"

    with pytest.raises(RuntimeError, match="invalid value"):
        with nginx.deferred_reload():
            for i in range(3):
                _write(tmp_path, f"s{i}.conf", "new", old=f"old {i}")

    for i in range(3):
        assert (tmp_path / f"s{i}.conf").read_text(encoding="utf-8") == f"old {i}"
    assert "nginx-reload" not in seen, "a config that fails the test must not be loaded"


def test_a_file_that_did_not_exist_before_is_removed_again(tmp_path, calls):
    seen, fake = calls
    fake.test_rc = 1

    with pytest.raises(RuntimeError):
        with nginx.deferred_reload():
            _write(tmp_path, "new-site.conf", "new", old=None)

    assert not (tmp_path / "new-site.conf").exists()


def test_the_body_raising_also_rolls_back(tmp_path, calls):
    seen, _ = calls
    (tmp_path / "s.conf").write_text("old", encoding="utf-8")

    with pytest.raises(ValueError):
        with nginx.deferred_reload():
            _write(tmp_path, "s.conf", "new", old="old")
            raise ValueError("render failed halfway")

    assert (tmp_path / "s.conf").read_text(encoding="utf-8") == "old"
    assert "nginx-test" not in seen and "nginx-reload" not in seen


def test_nesting_leaves_the_reload_to_the_outermost_block(tmp_path, calls):
    seen, _ = calls

    with nginx.deferred_reload():
        _write(tmp_path, "a.conf", "new")
        with nginx.deferred_reload():
            _write(tmp_path, "b.conf", "new")
        assert "nginx-test" not in seen

    assert seen.count("nginx-test") == 1


def test_the_batch_flag_is_cleared_even_when_it_fails(tmp_path, calls):
    """Otherwise every later write in the process silently stops reloading."""
    seen, fake = calls
    fake.test_rc = 1

    with pytest.raises(RuntimeError):
        with nginx.deferred_reload():
            _write(tmp_path, "s.conf", "new", old=None)

    assert nginx._DEFERRED_WRITES is None
    fake.test_rc = 0
    _write(tmp_path, "after.conf", "new")
    assert seen.count("nginx-reload") == 1


def test_the_updater_actually_uses_it():
    """The fix is only real if update.sh's per-site loop is inside the block."""
    from pathlib import Path

    update = (Path(__file__).resolve().parents[3] / "installer" / "update.sh").read_text(
        encoding="utf-8", errors="replace"
    )

    assert "with nginx.deferred_reload():" in update
    block = update.split("with nginx.deferred_reload():", 1)[1]
    assert "nginx.rewrite_vhost(" in block.split("\nPY\n", 1)[0]
