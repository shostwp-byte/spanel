import hashlib
import platform
import socket
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.core.version import APP_VERSION
from app.models.entities import DatabaseAccount, User, Website
from app.services import panel_settings, server_network

router = APIRouter(prefix="/licensing", tags=["licensing"])


def _read_os_pretty_name() -> str:
    os_release = Path("/etc/os-release")
    try:
        if os_release.is_file():
            for line in os_release.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return f"{platform.system()} {platform.release()}"


def _read_uptime_seconds() -> int:
    uptime_file = Path("/proc/uptime")
    try:
        if uptime_file.is_file():
            raw = uptime_file.read_text(encoding="utf-8").split()[0]
            return max(0, int(float(raw)))
    except Exception:
        pass
    return 0


def _compute_node_fingerprint(hostname: str) -> str:
    seed = hostname
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            if candidate.is_file():
                val = candidate.read_text(encoding="utf-8").strip()
                if val:
                    seed = f"{val}:{hostname}"
                    break
        except Exception:
            pass
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest().upper()
    return f"SPN-{digest[:4]}-{digest[4:8]}-{digest[8:12]}"


@router.get("/status")
def get_licensing_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    p_settings = panel_settings.current_settings()
    try:
        sys_host = socket.gethostname() or "spanel-node"
    except Exception:
        sys_host = "spanel-node"
    hostname = p_settings.get("panel_hostname") or sys_host

    total_users = db.query(User).count()
    resellers_count = db.query(User).filter(User.role == "reseller").count()
    websites_count = db.query(Website).count()
    databases_count = db.query(DatabaseAccount).count()

    return {
        "edition": "SPanel Open-Source Enterprise Edition",
        "license_model": "100% Free Forever (Open-Source on GitHub — 0đ)",
        "license_type": "Open-Source Community & Enterprise",
        "status": "active_unlimited",
        "price_label": "0đ / Miễn phí trọn đời",
        "github_repo": "https://github.com/shostwp-byte/spanel",
        "github_releases": "https://github.com/shostwp-byte/spanel/releases",
        "github_issues": "https://github.com/shostwp-byte/spanel/issues",
        "install_command": "curl -fsSL https://raw.githubusercontent.com/shostwp-byte/spanel/1.0.7/install.sh | sudo bash",
        "node_identity": {
            "node_id": _compute_node_fingerprint(sys_host),
            "hostname": hostname,
            "system_hostname": sys_host,
            "panel_version": APP_VERSION,
            "os_name": _read_os_pretty_name(),
            "kernel": platform.release(),
            "architecture": platform.machine() or "x86_64",
            "python_version": platform.python_version(),
            "ipv4_addresses": server_network.ipv4_addresses(),
            "ipv6_addresses": server_network.ipv6_addresses(),
            "uptime_seconds": _read_uptime_seconds(),
        },
        "usage": {
            "users_count": total_users,
            "resellers_count": resellers_count,
            "websites_count": websites_count,
            "databases_count": databases_count,
            "account_limit": "Unlimited (Không giới hạn)",
            "domain_limit": "Unlimited (Không giới hạn)",
        },
        "unlocked_capabilities": [
            {
                "key": "unlimited_accounts",
                "title": "Unlimited Users, Resellers & Domains",
                "desc": "Không giới hạn số lượng tài khoản Hosting, đại lý Reseller, tên miền, Subdomain và cơ sở dữ liệu.",
                "status": "Unlocked 100%",
            },
            {
                "key": "smart_php_autopatch",
                "title": "Smart PHP Auto-Patch (Zero-Downtime)",
                "desc": "Tự động phát hiện và vá lỗ hổng bảo mật cho toàn bộ các nhánh PHP (5.6 – 8.5) an toàn không gián đoạn.",
                "status": "Unlocked 100%",
            },
            {
                "key": "multi_server_cluster",
                "title": "Multi-Server Fleet & DNS Cluster",
                "desc": "Liên kết không giới hạn máy chủ SPanel, đồng bộ DNS tự động đa chiều và kiểm tra trùng tên miền liên máy chủ.",
                "status": "Unlocked 100%",
            },
            {
                "key": "kernel_isolation",
                "title": "Cgroups v2 & Mount Namespace Isolation",
                "desc": "Cô lập cứng tài nguyên CPU, RAM, Disk I/O, Process theo từng User và bảo vệ tiến trình cấp Kernel.",
                "status": "Unlocked 100%",
            },
            {
                "key": "billing_automation",
                "title": "WHMCS, HostBill, Blesta & Perfex CRM API",
                "desc": "Tích hợp tự động hóa khởi tạo dịch vụ 2 chiều, SSO 1-Click và Outbound Webhook có Replay.",
                "status": "Unlocked 100%",
            },
            {
                "key": "security_suite",
                "title": "Enterprise WAF, Malware & Brute-Force Guard",
                "desc": "Tích hợp sẵn ModSecurity OWASP CRS, ClamAV + Maldet, Fail2ban, IPSet Blocklist và WebAuthn Passkeys.",
                "status": "Unlocked 100%",
            },
            {
                "key": "disaster_recovery",
                "title": "Multi-Cloud Disaster Recovery & Migration",
                "desc": "Sao lưu Full & Incremental ra Local, Amazon S3, SFTP/FTP và di chuyển tự động từ cPanel / DirectAdmin.",
                "status": "Unlocked 100%",
            },
            {
                "key": "ai_mcp_engine",
                "title": "Built-in AI Assistant & Model Context Protocol (MCP)",
                "desc": "Trợ lý quản trị máy chủ bằng ngôn ngữ tự nhiên và kết nối trực tiếp với Claude / Cursor qua chuẩn MCP.",
                "status": "Unlocked 100%",
            },
        ],
    }
