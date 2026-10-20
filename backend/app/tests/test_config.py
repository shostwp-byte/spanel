"""The panel has to start even when .env has drifted."""

from app.core.config import Settings


def test_an_unknown_env_key_does_not_stop_the_panel_starting():
    """A stale key in .env must be ignored, not fatal.

    .env is machine-written: the installer creates it and every update rewrites
    it, so a key introduced by a newer version is left behind when that version
    is rolled back. With pydantic-settings' default extra="forbid" that leftover
    makes Settings() raise, and the panel cannot start at all.

    This is not hypothetical. A production box carried WEB_SERVER=nginx from a
    branch that was later cancelled; once main no longer declared the field,
    every start raised ValidationError. It stayed invisible for two weeks
    because the already-running process had its config loaded - the box looked
    healthy while being one restart from down.
    """
    settings = Settings(
        _env_file=None,
        WEB_SERVER="nginx",           # the real one
        SOME_KEY_FROM_THE_FUTURE="x",
    )

    # Started, and the known settings still work.
    assert settings.app_name == "spanel"
    assert settings.panel_port == 2222
    # The unknown keys are simply not there.
    assert not hasattr(settings, "web_server")
