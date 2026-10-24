"""What the Notifications addon can say, to whom, and in which words.

Only administrators hear anything: customers get no notifications
(operator, 2026-09-27). An administrator hears about the server - a service
that stopped, a disk filling up, the firewall off, a failed scheduled backup,
malware, certificates about to expire, a panel update - and about their own
account: a sign-in from an address it has not used before, a password or
two-factor change.

Each message exists in Vietnamese and English; the panel-wide setting picks
one. A message is a title and a few lines, plain text, so the same words work
in an email and in Telegram.
"""
from __future__ import annotations

from collections.abc import Callable

ADMIN = "admin"
USER = "user"

# key -> audience, the label a person sees in their settings, a one-line hint.
EVENTS: dict[str, dict] = {
    # --- the server, to administrators ---------------------------------------
    "service_down": {"audience": ADMIN, "label": "A service stopped or came back",
                     "hint": "nginx, PHP-FPM, MariaDB, Redis or the panel itself, checked every 5 minutes."},
    "disk_high": {"audience": ADMIN, "label": "The disk is nearly full",
                  "hint": "Once when usage passes the threshold, again only after it has dropped back."},
    "firewall_off": {"audience": ADMIN, "label": "The firewall is off",
                     "hint": "Switched off, or on but not filtering."},
    "cluster_node_down": {"audience": ADMIN, "label": "A cluster node went down or came back",
                          "hint": "Another SPanel server of the Multi Server cluster stopped answering, checked every minute."},
    "update_available": {"audience": ADMIN, "label": "A panel update is available",
                         "hint": "Once per new version."},
    "php_security_updated": {"audience": ADMIN, "label": "PHP security patches automatically applied",
                             "hint": "Whenever Smart PHP Guard safely patches PHP versions (Zero-Downtime & Anti-PHP2SHELL)."},
    "php_update_available": {"audience": ADMIN, "label": "PHP security updates available",
                             "hint": "When new minor/security releases exist for installed PHP branches."},
    "backup_failed_admin": {"audience": ADMIN, "label": "A scheduled backup failed",
                            "hint": "Any schedule on the server, with the error."},
    "malware_found_admin": {"audience": ADMIN, "label": "Malware found",
                            "hint": "Any scan on the server that finds something."},
    "ssl_expiring_admin": {"audience": ADMIN, "label": "Certificates about to expire",
                           "hint": "Every website's certificate, checked once a day."},
    # --- an administrator's own account ----------------------------------------
    "login_new_ip": {"audience": USER, "label": "Sign-in from a new address",
                     "hint": "Your account signed in from an IP it has not used before."},
    "security_change": {"audience": USER, "label": "Password or two-factor changed",
                        "hint": "So a change you did not make does not go unnoticed."},
}

LANGUAGES = ("vi", "en")
MAX_LISTED = 8


def _listed(items: list[str], more_vi: str, more_en: str, lang: str) -> list[str]:
    shown = [f"- {item}" for item in items[:MAX_LISTED]]
    if len(items) > MAX_LISTED:
        rest = len(items) - MAX_LISTED
        shown.append((more_vi if lang == "vi" else more_en).format(n=rest))
    return shown


def _service_down(p: dict, lang: str) -> tuple[str, list[str]]:
    names = ", ".join(p.get("services") or [])
    if p.get("recovered"):
        return (
            (f"Dịch vụ đã chạy lại: {names}", ["Dịch vụ hoạt động bình thường trở lại."])
            if lang == "vi" else
            (f"Service running again: {names}", ["The service is back to normal."])
        )
    return (
        (f"Dịch vụ đã dừng: {names}", [
            "Dịch vụ không chạy trong hai lần kiểm tra liên tiếp.",
            "Vào trang Dịch vụ trong panel để khởi động lại và xem log.",
        ]) if lang == "vi" else
        (f"Service stopped: {names}", [
            "The service was not running at two checks in a row.",
            "Open Services in the panel to start it again and read its log.",
        ])
    )


def _disk_high(p: dict, lang: str) -> tuple[str, list[str]]:
    return (
        (f"Ổ đĩa đã dùng {p['percent']}%", [
            f"Đã dùng {p['used']} trên {p['total']}, còn trống {p['free']}.",
            "Khi đầy, MariaDB, backup và việc tải file lên đều sẽ lỗi.",
        ]) if lang == "vi" else
        (f"Disk {p['percent']}% full", [
            f"{p['used']} of {p['total']} used, {p['free']} free.",
            "When it is full, MariaDB, backups and uploads all start failing.",
        ])
    )


def _firewall_off(p: dict, lang: str) -> tuple[str, list[str]]:
    return (
        ("Tường lửa đang tắt", ["Server không được tường lửa bảo vệ. Vào trang Tường lửa để bật lại."])
        if lang == "vi" else
        ("The firewall is off", ["The server is not protected by its firewall. Open Firewall to turn it back on."])
    )


def _cluster_node_down(p: dict, lang: str) -> tuple[str, list[str]]:
    name = p.get("node") or ""
    if p.get("recovered"):
        return (
            (f"Máy chủ cụm đã trực tuyến lại: {name}", [f"{name} ({p.get('url', '')}) phản hồi bình thường trở lại."])
            if lang == "vi" else
            (f"Cluster node back online: {name}", [f"{name} ({p.get('url', '')}) is answering again."])
        )
    return (
        (f"Máy chủ cụm không phản hồi: {name}", [
            f"{name} ({p.get('url', '')}) không phản hồi {p.get('failures', 2)} lần kiểm tra liên tiếp.",
            f"Lỗi: {p.get('error', '')}",
            "Đồng bộ DNS tới máy chủ này được giữ lại và sẽ tự gửi lại khi nó trực tuyến.",
        ]) if lang == "vi" else
        (f"Cluster node not answering: {name}", [
            f"{name} ({p.get('url', '')}) did not answer {p.get('failures', 2)} checks in a row.",
            f"Error: {p.get('error', '')}",
            "DNS changes for it are kept and sent again once it is back.",
        ])
    )


def _update_available(p: dict, lang: str) -> tuple[str, list[str]]:
    return (
        (f"Có bản cập nhật spanel {p['version']}", [
            f"Đang chạy {p['current']}. Cập nhật bằng lệnh: spanel-update --release",
        ]) if lang == "vi" else
        (f"spanel {p['version']} is available", [
            f"This server runs {p['current']}. Update with: spanel-update --release",
        ])
    )


def _backup_failed(p: dict, lang: str) -> tuple[str, list[str]]:
    errors = p.get("errors") or []
    if lang == "vi":
        return (f"Backup theo lịch bị lỗi ({p.get('when', '')})",
                ["Lần chạy backup theo lịch không hoàn tất:", *_listed(errors, "...và {n} lỗi khác", "", lang)])
    return (f"Scheduled backup failed ({p.get('when', '')})",
            ["The scheduled backup did not complete:", *_listed(errors, "", "...and {n} more", lang)])


def _malware_found(p: dict, lang: str) -> tuple[str, list[str]]:
    lines = [f"{t['path']} ({t['signature']})" for t in p.get("threats") or []]
    count = p.get("count", len(lines))
    if lang == "vi":
        return (f"Phát hiện {count} file nghi nhiễm mã độc",
                [*_listed(lines, "...và {n} file khác", "", lang),
                 "Xem chi tiết trong trang Quét mã độc. File chưa bị xoá hay cách ly."])
    return (f"{count} suspicious file(s) found",
            [*_listed(lines, "", "...and {n} more", lang),
             "See the Malware scanner page. Nothing was deleted or quarantined."])


def _ssl_expiring(p: dict, lang: str) -> tuple[str, list[str]]:
    lines = [f"{s['domain']}: {s['days']} ngày ({s['expires']})" if lang == "vi"
             else f"{s['domain']}: {s['days']} day(s) ({s['expires']})" for s in p.get("sites") or []]
    if lang == "vi":
        return (f"{len(lines)} chứng chỉ SSL sắp hết hạn",
                [*_listed(lines, "...và {n} website khác", "", lang),
                 "Chứng chỉ Let's Encrypt lẽ ra đã tự gia hạn; hãy mở trang SSL để gia hạn lại."])
    return (f"{len(lines)} SSL certificate(s) about to expire",
            [*_listed(lines, "", "...and {n} more", lang),
             "Let's Encrypt certificates should have renewed on their own; open SSL to renew them."])


def _login_new_ip(p: dict, lang: str) -> tuple[str, list[str]]:
    agent = p.get("agent") or ""
    if lang == "vi":
        return ("Đăng nhập từ địa chỉ mới", [
            f"Tài khoản {p['username']} vừa đăng nhập từ {p['ip']} lúc {p['when']}.",
            *( [f"Trình duyệt: {agent}"] if agent else [] ),
            "Nếu không phải bạn, hãy đổi mật khẩu và bật đăng nhập hai lớp ngay.",
        ])
    return ("Sign-in from a new address", [
        f"Account {p['username']} signed in from {p['ip']} at {p['when']}.",
        *( [f"Browser: {agent}"] if agent else [] ),
        "If this was not you, change your password and turn on two-factor sign-in now.",
    ])


_SECURITY_KINDS = {
    "password": ("Mật khẩu tài khoản đã được đổi", "Your password was changed"),
    "2fa_off": ("Đăng nhập hai lớp đã bị tắt", "Two-factor sign-in was turned off"),
    "2fa_reset": ("Quản trị viên đã đặt lại đăng nhập hai lớp", "An administrator reset your two-factor sign-in"),
}


def _security_change(p: dict, lang: str) -> tuple[str, list[str]]:
    vi, en = _SECURITY_KINDS.get(p.get("kind"), _SECURITY_KINDS["password"])
    if lang == "vi":
        return (vi, [f"Tài khoản {p['username']}, lúc {p['when']}.",
                     "Nếu không phải bạn làm, hãy đổi mật khẩu ngay và kiểm tra lại các tài khoản quản trị."])
    return (en, [f"Account {p['username']}, at {p['when']}.",
                 "If this was not you, change the password now and check the administrator accounts."])


def _php_security_updated(p: dict, lang: str) -> tuple[str, list[str]]:
    items = p.get("items") or []
    trigger = p.get("trigger") or "auto"
    if lang == "vi":
        mode_label = "Tự động (Smart PHP Auto-Patch)" if trigger == "auto" else "Thủ công (Quản trị viên)"
        return (
            f"Smart PHP Auto-Patch: Đã cập nhật bản vá bảo mật PHP ({len(items)} nhánh)",
            [
                f"Chế độ kích hoạt: {mode_label}.",
                *_listed(items, "...và {n} nhánh PHP khác", "", lang),
                "Dịch vụ PHP-FPM đã được kiểm tra cú pháp (php-fpm -t) và reload an toàn, không làm gián đoạn hoạt động của máy chủ.",
            ],
        )
    mode_en = "Automatic (Smart PHP Auto-Patch)" if trigger == "auto" else "Manual (Administrator)"
    return (
        f"Smart PHP Auto-Patch: PHP security patches applied ({len(items)} branch(es))",
        [
            f"Trigger: {mode_en}.",
            *_listed(items, "", "...and {n} more", lang),
            "PHP-FPM syntax was verified (php-fpm -t) and reloaded gracefully with zero downtime.",
        ],
    )


def _php_update_available(p: dict, lang: str) -> tuple[str, list[str]]:
    items = p.get("items") or []
    if lang == "vi":
        return (
            f"Smart PHP Auto-Patch: Phát hiện {len(items)} bản cập nhật bảo mật PHP mới",
            [
                "Các nhánh PHP dưới đây có bản vá bảo mật mới:",
                *_listed(items, "...và {n} nhánh khác", "", lang),
                "Mở trang Cập nhật hệ thống (Updates) hoặc CustomBuild để cập nhật an toàn.",
            ],
        )
    return (
        f"Smart PHP Auto-Patch: {len(items)} PHP security update(s) available",
        [
            "The following installed PHP branches have newer security/minor releases:",
            *_listed(items, "", "...and {n} more", lang),
            "Open System Updates or CustomBuild to apply safe patching.",
        ],
    )


_RENDER: dict[str, Callable[[dict, str], tuple[str, list[str]]]] = {
    "service_down": _service_down,
    "disk_high": _disk_high,
    "firewall_off": _firewall_off,
    "cluster_node_down": _cluster_node_down,
    "update_available": _update_available,
    "php_security_updated": _php_security_updated,
    "php_update_available": _php_update_available,
    "backup_failed_admin": _backup_failed,
    "malware_found_admin": _malware_found,
    "ssl_expiring_admin": _ssl_expiring,
    "login_new_ip": _login_new_ip,
    "security_change": _security_change,
}


def render(event: str, params: dict, lang: str = "vi") -> tuple[str, str]:
    """(title, body) for one event in one language."""
    lang = lang if lang in LANGUAGES else "vi"
    title, lines = _RENDER[event](params, lang)
    return title, "\n".join(line for line in lines if line is not None)


def events_for(role_is_admin: bool) -> list[str]:
    """The events a person can receive: all of them for an administrator,
    none for anybody else."""
    return list(EVENTS) if role_is_admin else []
