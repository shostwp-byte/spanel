from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


DEFAULT_SECRET_KEY = "change-this-secret-key"


class Settings(BaseSettings):
    app_name: str = "spanel"
    app_env: str = "development"
    secret_key: str = DEFAULT_SECRET_KEY
    webhook_secret_key: str = ""
    access_token_expire_minutes: int = 120  # was 720; shorter window if a token is stolen
    # A session is silently re-issued while it is in use (see the slide-session
    # middleware), so this is really an *idle* timeout: leave the panel alone
    # this long and the next visit asks for the password again.
    remember_me_expire_minutes: int = 60 * 24 * 30  # opt-in "remember me" at login
    database_url: str = "sqlite:///./spanel.db"
    command_dry_run: bool = True
    allowed_origins: str = Field(default="")
    backup_root: str = "/var/backups/spanel"
    nginx_sites_available: str = "/etc/nginx/conf.d"
    default_php_version: str = "8.4"
    ssl_email: str = ""
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_backend: str = "redis"
    panel_url: str = ""
    panel_domain: str = ""
    panel_port: int = 2222
    panel_ssl_cert: str = ""
    panel_ssl_key: str = ""
    # Where that certificate came from: selfsigned | letsencrypt | domain.
    # Blank on a panel installed before the panel had a certificate at all.
    panel_ssl_mode: str = ""
    frontend_dist: str = "/opt/spanel/frontend/dist"
    totp_issuer: str = "spanel"
    github_token: str = ""
    geoip_country_db: str = ""
    geoip_dbip_country_url: str = "https://download.db-ip.com/free/dbip-country-lite-{year}-{month}.csv.gz"
    geoip_dbip_cache_dir: str = "/var/lib/spanel/geoip"
    # Malware scanning is OPTIONAL and OFF by default. It only becomes active
    # after an admin enables it in the panel, which triggers an on-demand
    # install of clamav-daemon. Leaving this False keeps spanel lightweight.
    malware_scan_enabled: bool = False
    clamav_socket_path: str = "/run/clamav/clamd.sock"
    # When True, uploaded files are scanned in memory before being accepted.
    malware_scan_on_upload: bool = True
    # When true, ``app.core.secrets.decrypt`` refuses to read legacy plaintext
    # values (the deprecated migration grace path). Production should leave
    # this True so any unmigrated row surfaces as a hard error instead of
    # silently leaking through. Set STRICT_DECRYPT=false during a one-shot
    # migration window only.
    strict_decrypt: bool = True

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, value: str, info):
        app_env = (info.data.get("app_env") or "development").lower()
        if app_env == "production" and (value == DEFAULT_SECRET_KEY or len(value) < 32):
            raise ValueError("SECRET_KEY must be changed to a strong random value in production")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, value: str, info):
        app_env = (info.data.get("app_env") or "development").lower()
        normalized = [o.strip() for o in (value or "").split(",") if o.strip()]
        if app_env == "production":
            if "*" in normalized:
                raise ValueError("ALLOWED_ORIGINS cannot be '*' in production with credentials enabled")
            for origin in normalized:
                if not origin.startswith(("http://", "https://")):
                    raise ValueError(f"ALLOWED_ORIGINS entry must include scheme: {origin}")
        return value

    @field_validator("rate_limit_backend")
    @classmethod
    def validate_rate_limit_backend(cls, value: str, info):
        backend = (value or "memory").strip().lower()
        if backend not in {"memory", "redis"}:
            raise ValueError("RATE_LIMIT_BACKEND must be 'memory' or 'redis'")
        app_env = (info.data.get("app_env") or "development").lower()
        if app_env == "production" and backend != "redis":
            raise ValueError("RATE_LIMIT_BACKEND=redis is required in production")
        return backend

    @property
    def cors_origins(self) -> list[str]:
        origins = []
        for origin in self.allowed_origins.split(","):
            origin = origin.strip().rstrip("/")
            if not origin or origin == "*":
                continue
            origins.append(origin)
        return origins

    class Config:
        env_file = ".env"
        # Unknown keys in .env are ignored, not fatal.
        #
        # pydantic-settings defaults to extra="forbid", which means one stale
        # key stops the panel from starting at all. That is the wrong trade for
        # a control panel: .env is written by the installer and rewritten by
        # every update, so a key that a newer version added stays behind when
        # that version is rolled back. Seen for real on a production box, where
        # a leftover WEB_SERVER=nginx from a since-cancelled branch left the
        # panel unable to start - and silently, because the running process had
        # already imported its config. The box looked healthy for two weeks
        # while being one restart away from being down.
        #
        # Ignoring an unknown key costs a typo going unnoticed. Forbidding one
        # costs the panel. The panel matters more.
        extra = "ignore"


settings = Settings()
