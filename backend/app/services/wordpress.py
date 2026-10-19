import re
import secrets as _secrets
from pathlib import Path
from typing import Dict

from app.core.config import settings
from app.services import site_users
from app.services.shell import shell


# Strict whitelists for values fed to WP-CLI to prevent flag injection.
WP_USER_RE = re.compile(r"^[A-Za-z0-9._@-]{3,60}$")
WP_TITLE_RE = re.compile(r"^[\w\s.,'\-:!()&]{1,150}$", re.UNICODE)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{3,255}$")


WP_SALT_KEYS = (
    "AUTH_KEY", "SECURE_AUTH_KEY", "LOGGED_IN_KEY", "NONCE_KEY",
    "AUTH_SALT", "SECURE_AUTH_SALT", "LOGGED_IN_SALT", "NONCE_SALT",
)


def _generate_wp_salts() -> str:
    lines = []
    for key in WP_SALT_KEYS:
        salt = _secrets.token_urlsafe(48).replace("'", "")
        lines.append(f"define('{key}', '{salt}');")
    return "\n".join(lines)


def _render_wp_config(db_name: str, db_user: str, db_password: str) -> str:
    """Render wp-config.php directly so the DB password never appears in argv.

    PHP single-quoted strings only need ' and \\ escaping.
    """
    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    return (
        "<?php\n"
        f"define('DB_NAME', '{esc(db_name)}');\n"
        f"define('DB_USER', '{esc(db_user)}');\n"
        f"define('DB_PASSWORD', '{esc(db_password)}');\n"
        "define('DB_HOST', 'localhost');\n"
        "define('DB_CHARSET', 'utf8mb4');\n"
        "define('DB_COLLATE', '');\n"
        "\n"
        "$table_prefix = 'wp_';\n"
        "\n"
        f"{_generate_wp_salts()}\n"
        "\n"
        "define('WP_DEBUG', false);\n"
        "if ( ! defined('ABSPATH') ) {\n"
        "    define('ABSPATH', __DIR__ . '/');\n"
        "}\n"
        "require_once ABSPATH . 'wp-settings.php';\n"
    )


def _safe_value(value: str, pattern: re.Pattern, label: str) -> str:
    value = (value or "").strip()
    if value.startswith("-") or "\x00" in value or not pattern.fullmatch(value):
        raise ValueError(f"Invalid {label}")
    return value


def site_root(domain: str) -> str:
    return site_users.site_root_for_domain(domain)


def install_wordpress(
    domain: str,
    db: Dict[str, str],
    title: str,
    admin_user: str,
    admin_password: str,
    admin_email: str,
    php_version: str,
    linux_user: str | None = None,
    root_path: str | None = None,
) -> str:
    safe_user = _safe_value(admin_user, WP_USER_RE, "WordPress admin username")
    safe_title = _safe_value(title, WP_TITLE_RE, "WordPress site title")
    safe_email = _safe_value(admin_email, EMAIL_RE, "WordPress admin email")
    if not isinstance(admin_password, str) or len(admin_password) < 10 or "\x00" in admin_password:
        raise ValueError("WordPress admin password must be at least 10 characters")

    root = Path(root_path or site_root(domain))
    public = site_users.document_root(root)
    linux_user = linux_user or site_users.ensure_site_runtime(domain, str(root), php_version)
    wp_path = f"--path={public}"

    # WP-CLI runs as this website's isolated Linux user through the helper.
    shell.privileged(
        "wp-site",
        helper_args=[linux_user, *_wp_php_flag(php_version), "core", "download", wp_path],
        fallback=["wp", "core", "download", wp_path],
    )

    # Render wp-config.php directly to avoid leaking the DB password through
    # argv (which would be visible to other local users via /proc/<pid>/cmdline
    # or `ps auxww` while wp config create runs).
    config_path = public / "wp-config.php"
    config_content = _render_wp_config(db["db_name"], db["db_user"], db["db_password"])
    if not settings.command_dry_run:
        shell.privileged(
            "site-file-write",
            helper_args=[linux_user, str(root), "public_html/wp-config.php", "0640"],
            fallback=[
                "sh",
                "-c",
                'cat >"$1" && chmod "$2" "$1"',
                "sh",
                str(config_path),
                "0640",
            ],
            input=config_content,
            sensitive=True,
        )
    shell.privileged(
        "site-path-fix",
        helper_args=[str(public), linux_user],
        check=False,
        fallback=["chown", "-R", f"{linux_user}:{linux_user}", str(public)],
    )

    install_args = [
        "core", "install", wp_path,
        f"--url=https://{domain}",
        f"--title={safe_title}",
        f"--admin_user={safe_user}",
        f"--admin_email={safe_email}",
        "--prompt=admin_password",
        "--skip-email",
        "--allow-root",
    ]
    # The site's PHP, not the default alternative: `wp core install` writes to
    # the database, so a CLI without mysqli fails here just as `core update` did.
    shell.privileged(
        "wp-site",
        helper_args=[linux_user, *_wp_php_flag(php_version), *install_args],
        fallback=["wp", *install_args],
        input=admin_password + "\n",
        sensitive=True,
    )

    fix_permissions(str(root), linux_user)
    if not settings.command_dry_run:
        shell.privileged(
            "site-file-write",
            helper_args=[linux_user, str(root), "public_html/wp-config.php", "0640"],
            fallback=[
                "sh",
                "-c",
                'chmod "$2" "$1"',
                "sh",
                str(config_path),
                "0640",
            ],
            input=config_content,
            sensitive=True,
        )
    return str(root)


def fix_permissions(root_path: str, linux_user: str | None = None):
    return site_users.fix_site_permissions(root_path, linux_user)


def _wp_php_flag(php_version: str | None) -> list[str]:
    """The --php-version the helper wants, or nothing if we do not know one.

    WP-CLI must run under the site's PHP. Left to the default `php`, a site on
    one version gets updated by another version's CLI, which may not have the
    extensions WordPress needs - mysqli in particular.
    """
    version = (php_version or "").strip()
    return [f"--php-version={version}"] if version else []


def _site_user_for_path(path: str, linux_user: str | None) -> str:
    """The Linux user WP-CLI must run as for a site at this path.

    Never falls back to www-data. That account is a supplementary member of
    every site's group (spanel-helper.sh:3596) and of spanel-sites (:3513),
    while wp-config.php, .env and .my.cnf are 0640 group-readable - so WP-CLI
    running as www-data could read every tenant's database credentials, and
    WP-CLI loads the target install's own plugins, which is tenant-authored
    PHP. Panel-created sites always set linux_user; the fallback existed for
    legacy and imported rows, and derives the owner from the path instead.
    """
    if linux_user:
        return site_users.validate_linux_user(linux_user)
    try:
        parts = Path(path).resolve().relative_to(site_users.HOME_ROOT.resolve()).parts
    except ValueError as exc:
        raise ValueError(f"Cannot determine the site owner for {path}") from exc
    if not parts:
        raise ValueError(f"Cannot determine the site owner for {path}")
    return site_users.validate_linux_user(parts[0])


def wp_update(path: str, action: str, linux_user: str | None = None, php_version: str | None = None):
    if action == "core":
        args = ["core", "update", f"--path={path}", "--allow-root"]
    elif action == "plugins":
        args = ["plugin", "update", "--all", f"--path={path}", "--allow-root"]
    elif action == "themes":
        args = ["theme", "update", "--all", f"--path={path}", "--allow-root"]
    else:
        raise ValueError("Unsupported WordPress action")
    site_user = _site_user_for_path(path, linux_user)
    return shell.privileged(
        "wp-site",
        helper_args=[site_user, *_wp_php_flag(php_version), *args],
        fallback=["wp", *args],
    )


def reset_admin_password(path: str, user: str, password: str, linux_user: str | None = None, php_version: str | None = None):
    safe_user = _safe_value(user, WP_USER_RE, "WordPress username")
    if not isinstance(password, str) or len(password) < 10 or "\x00" in password:
        raise ValueError("Password must be at least 10 characters")
    args = ["user", "update", safe_user, "--user_pass=/dev/stdin", f"--path={path}", "--allow-root"]
    site_user = _site_user_for_path(path, linux_user)
    return shell.privileged(
        "wp-site",
        helper_args=[site_user, *_wp_php_flag(php_version), *args],
        fallback=["wp", *args],
        input=password,
        sensitive=True,
    )


def delete_wordpress(root_path: str):
    target = Path(root_path).resolve()
    if not site_users.is_managed_site_path(target):
        raise ValueError("Refusing to delete path outside managed site roots")
    linux_user = target.relative_to(site_users.HOME_ROOT.resolve()).parts[0]
    return shell.privileged(
        "rm-site",
        helper_args=[linux_user, str(target), str(target)],
        fallback=["rm", "-rf", str(target)],
    )
