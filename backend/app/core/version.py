from pathlib import Path

APP_VERSION_FALLBACK = "1.0.6"


def _read_app_version() -> str:
    version_file = Path(__file__).resolve().parents[3] / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip() or APP_VERSION_FALLBACK
    except OSError:
        return APP_VERSION_FALLBACK


APP_VERSION = _read_app_version()
