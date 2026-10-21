import base64
import ipaddress
import json
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User, UserPackage
from app.schemas.schemas import UserPackageCreate, UserPackageOut, UserPackageUpdate
from app.services.audit import log_action

router = APIRouter(prefix="/packages", tags=["packages"])


class PackageBatchImportIn(BaseModel):
    packages: List[UserPackageCreate] = Field(default_factory=list)
    conflict_mode: str = Field(default="overwrite")  # overwrite | skip | rename
    source_panel: str = Field(default="directadmin")


class RemotePanelFetchIn(BaseModel):
    panel_type: str = Field(default="directadmin")  # directadmin | cpanel | plesk
    host_url: str
    username: str = Field(default="admin")
    secret: str
    auth_type: str = Field(default="password")  # password | token
    is_reseller_package: bool = Field(default=False)
    verify_ssl: bool = Field(default=False)


def _package_by_id(db: Session, package_id: int) -> UserPackage:
    package = db.query(UserPackage).filter(UserPackage.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    return package


def _ensure_unique_name(db: Session, name: str, package_id: int | None = None) -> None:
    query = db.query(UserPackage).filter(UserPackage.name == name)
    if package_id is not None:
        query = query.filter(UserPackage.id != package_id)
    if query.first():
        raise HTTPException(status_code=409, detail="Package name already exists")


def _parse_int_quota(val: Any, default: int = 0, bytes_to_mb: bool = False) -> int:
    if val is None:
        return default
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, (int, float)):
        n = int(val)
        if n < 0:
            return 0
        if bytes_to_mb and n >= 1048576:
            return max(1, round(n / 1048576))
        return n
    s = str(val).strip().lower()
    if not s or s in {"unlimited", "unlim", "inf", "infinite", "∞", "-1", "none", "null", "all"}:
        return 0
    m = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*([kmgt]b?)?$", s)
    if m:
        num = float(m.group(1))
        unit = (m.group(2) or "").rstrip("b")
        if num < 0:
            return 0
        if unit == "g":
            return int(round(num * 1024))
        if unit == "t":
            return int(round(num * 1024 * 1024))
        if unit == "k":
            return max(1, int(round(num / 1024))) if num > 0 else 0
        n = int(round(num))
        if bytes_to_mb and n >= 1048576:
            return max(1, round(n / 1048576))
        return n
    digits = re.sub(r"[^0-9-]", "", s)
    if not digits or digits == "-":
        return default
    n = int(digits)
    if n < 0:
        return 0
    if bytes_to_mb and n >= 1048576:
        return max(1, round(n / 1048576))
    return n


def _parse_bool_flag(val: Any, default: bool = True) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in {"on", "yes", "y", "true", "1", "enabled", "allow", "two", "three"}:
        return True
    if s in {"off", "no", "n", "false", "0", "disabled", "none", "deny"}:
        return False
    return default


def _default_package_dict(name: str, is_reseller: bool = False) -> dict:
    return {
        "name": name.strip() or "Imported Package",
        "is_reseller_package": bool(is_reseller),
        "reseller_user_limit": 25 if is_reseller else 0,
        "reseller_overselling_enabled": False,
        "storage_limit_mb": 5120,
        "bandwidth_limit_mb": 102400,
        "website_limit": 5,
        "subdomain_limit": 20,
        "alias_limit": 10,
        "domain_pointers_limit": 10,
        "inode_limit": 250000,
        "sftp_accounts_limit": 5,
        "ftp_accounts_limit": 5,
        "backup_retention_days": 14,
        "ips_limit": 1 if is_reseller else 0,
        "database_limit": 5,
        "mysql_storage_limit_mb": 2048,
        "mysql_max_db_size_mb": 1024,
        "mail_accounts_limit": 10,
        "email_forwarders_limit": 20,
        "autoresponders_limit": 10,
        "mailing_lists_limit": 0,
        "node_apps_limit": 3,
        "node_app_memory_mb": 512,
        "cpu_quota_percent": 100,
        "tasks_max": 80,
        "memory_high_mb": 768,
        "memory_max_mb": 1024,
        "io_read_mbps": 100,
        "io_write_mbps": 100,
        "iops_read_max": 1000,
        "iops_write_max": 1000,
        "php_access": True,
        "wordpress_enabled": True,
        "waf_enabled": True,
        "ssl_access": True,
        "terminal_enabled": False,
        "ssh_access": False,
        "ssh_access_users": False,
        "redis_access": True,
        "cron_jobs": True,
        "dns_control": True,
        "git_access": True,
        "spamassassin": True,
        "catch_all_email": False,
        "login_keys": True,
        "system_info": False,
        "cgi_access": False,
        "anonymous_ftp": False,
        "personal_dns_mode": "none",
        "share_server_ip": True,
    }


def _map_da_dict_to_package(name: str, raw: dict, is_reseller: bool = False) -> dict:
    kv = {str(k).strip().lower(): v for k, v in raw.items()}
    pkg = _default_package_dict(name, is_reseller=is_reseller)
    if "quota" in kv:
        pkg["storage_limit_mb"] = _parse_int_quota(kv.get("quota"), 5120)
    if "bandwidth" in kv:
        pkg["bandwidth_limit_mb"] = _parse_int_quota(kv.get("bandwidth"), 102400)
    if "vdomains" in kv:
        pkg["website_limit"] = _parse_int_quota(kv.get("vdomains"), 5)
    if "nsubdomains" in kv:
        pkg["subdomain_limit"] = _parse_int_quota(kv.get("nsubdomains"), 20)
    if "domainptr" in kv:
        ptr = _parse_int_quota(kv.get("domainptr"), 10)
        pkg["alias_limit"] = ptr
        pkg["domain_pointers_limit"] = ptr
    if "inode" in kv:
        pkg["inode_limit"] = _parse_int_quota(kv.get("inode"), 250000)
    if "mysql" in kv:
        pkg["database_limit"] = _parse_int_quota(kv.get("mysql"), 5)
    if "nemails" in kv:
        pkg["mail_accounts_limit"] = _parse_int_quota(kv.get("nemails"), 10)
    if "nemailf" in kv:
        pkg["email_forwarders_limit"] = _parse_int_quota(kv.get("nemailf"), 20)
    if "nemailr" in kv:
        pkg["autoresponders_limit"] = _parse_int_quota(kv.get("nemailr"), 10)
    if "nemailml" in kv:
        pkg["mailing_lists_limit"] = _parse_int_quota(kv.get("nemailml"), 0)
    if "ftp" in kv:
        ftp_val = _parse_int_quota(kv.get("ftp"), 5)
        pkg["sftp_accounts_limit"] = ftp_val
        pkg["ftp_accounts_limit"] = ftp_val
    if "nusers" in kv:
        pkg["reseller_user_limit"] = _parse_int_quota(kv.get("nusers"), 25)
        pkg["is_reseller_package"] = True
    if "ips" in kv:
        pkg["ips_limit"] = _parse_int_quota(kv.get("ips"), 0)
    if "oversell" in kv:
        pkg["reseller_overselling_enabled"] = _parse_bool_flag(kv.get("oversell"), False)
    if "php" in kv:
        pkg["php_access"] = _parse_bool_flag(kv.get("php"), True)
    if "ssl" in kv:
        pkg["ssl_access"] = _parse_bool_flag(kv.get("ssl"), True)
    if "ssh" in kv:
        ssh_flag = _parse_bool_flag(kv.get("ssh"), False)
        pkg["terminal_enabled"] = ssh_flag
        pkg["ssh_access"] = ssh_flag
    if "userssh" in kv:
        pkg["ssh_access_users"] = _parse_bool_flag(kv.get("userssh"), False)
    if "cron" in kv:
        pkg["cron_jobs"] = _parse_bool_flag(kv.get("cron"), True)
    if "dnscontrol" in kv:
        pkg["dns_control"] = _parse_bool_flag(kv.get("dnscontrol"), True)
    if "git" in kv:
        pkg["git_access"] = _parse_bool_flag(kv.get("git"), True)
    if "redis" in kv:
        pkg["redis_access"] = _parse_bool_flag(kv.get("redis"), True)
    if "spam" in kv:
        pkg["spamassassin"] = _parse_bool_flag(kv.get("spam"), True)
    if "catchall" in kv:
        pkg["catch_all_email"] = _parse_bool_flag(kv.get("catchall"), False)
    if "login_keys" in kv:
        pkg["login_keys"] = _parse_bool_flag(kv.get("login_keys"), True)
    if "sysinfo" in kv:
        pkg["system_info"] = _parse_bool_flag(kv.get("sysinfo"), False)
    if "cgi" in kv:
        pkg["cgi_access"] = _parse_bool_flag(kv.get("cgi"), False)
    if "aftp" in kv:
        pkg["anonymous_ftp"] = _parse_bool_flag(kv.get("aftp"), False)
    if "serverip" in kv:
        pkg["share_server_ip"] = _parse_bool_flag(kv.get("serverip"), True)
    if "dns" in kv:
        dns_mode = str(kv.get("dns", "OFF")).strip().upper()
        if dns_mode in {"THREE", "3", "3_IPS"}:
            pkg["personal_dns_mode"] = "3_ips"
        elif dns_mode in {"ON", "TWO", "2", "2_IPS", "1", "TRUE", "YES"}:
            pkg["personal_dns_mode"] = "2_ips"
        else:
            pkg["personal_dns_mode"] = "none"
    if "cpu_quota" in kv or "lve_cpu" in kv:
        pkg["cpu_quota_percent"] = _parse_int_quota(kv.get("cpu_quota") or kv.get("lve_cpu"), 100)
    if "mem_max" in kv or "lve_pmem" in kv:
        mem = _parse_int_quota(kv.get("mem_max") or kv.get("lve_pmem"), 1024)
        pkg["memory_max_mb"] = mem
        pkg["memory_high_mb"] = max(256, int(mem * 0.75)) if mem > 0 else 0
    if "tasks_max" in kv or "lve_nproc" in kv or "lve_ep" in kv:
        pkg["tasks_max"] = _parse_int_quota(kv.get("tasks_max") or kv.get("lve_nproc") or kv.get("lve_ep"), 80)
    return pkg


def _map_cpanel_dict_to_package(name: str, raw: dict, is_reseller: bool = False) -> dict:
    kv = {str(k).strip().upper(): v for k, v in raw.items()}
    pkg = _default_package_dict(name, is_reseller=is_reseller)
    if "QUOTA" in kv:
        pkg["storage_limit_mb"] = _parse_int_quota(kv.get("QUOTA"), 5120)
    if "BWLIMIT" in kv:
        pkg["bandwidth_limit_mb"] = _parse_int_quota(kv.get("BWLIMIT"), 102400)
    if "MAXADDON" in kv:
        addon_raw = str(kv.get("MAXADDON", "")).strip().lower()
        if addon_raw in {"unlimited", "unlim", "∞", "-1", "all"}:
            pkg["website_limit"] = 0
        else:
            addons = _parse_int_quota(addon_raw, 0)
            pkg["website_limit"] = 1 + addons
    if "MAXSUB" in kv:
        pkg["subdomain_limit"] = _parse_int_quota(kv.get("MAXSUB"), 20)
    if "MAXPARK" in kv:
        park = _parse_int_quota(kv.get("MAXPARK"), 10)
        pkg["alias_limit"] = park
        pkg["domain_pointers_limit"] = park
    if "MAXSQL" in kv:
        pkg["database_limit"] = _parse_int_quota(kv.get("MAXSQL"), 5)
    if "MAXPOP" in kv:
        pkg["mail_accounts_limit"] = _parse_int_quota(kv.get("MAXPOP"), 10)
    if "MAXFTP" in kv:
        ftp_val = _parse_int_quota(kv.get("MAXFTP"), 5)
        pkg["sftp_accounts_limit"] = ftp_val
        pkg["ftp_accounts_limit"] = ftp_val
    if "MAXLST" in kv:
        pkg["mailing_lists_limit"] = _parse_int_quota(kv.get("MAXLST"), 0)
    if "HASSHELL" in kv:
        sh = _parse_bool_flag(kv.get("HASSHELL"), False)
        pkg["terminal_enabled"] = sh
        pkg["ssh_access"] = sh
    if "CGI" in kv:
        pkg["cgi_access"] = _parse_bool_flag(kv.get("CGI"), False)
    if "IP" in kv:
        has_ip = _parse_bool_flag(kv.get("IP"), False)
        pkg["ips_limit"] = 1 if has_ip else 0
    if "MAXACCT" in kv or "ACCT_LIMIT" in kv:
        pkg["reseller_user_limit"] = _parse_int_quota(kv.get("MAXACCT") or kv.get("ACCT_LIMIT"), 25)
        pkg["is_reseller_package"] = True
    if "OVERSELL" in kv:
        pkg["reseller_overselling_enabled"] = _parse_bool_flag(kv.get("OVERSELL"), False)
    return pkg


def _map_plesk_dict_to_package(name: str, raw: dict, is_reseller: bool = False) -> dict:
    kv = {str(k).strip().lower(): v for k, v in raw.items()}
    pkg = _default_package_dict(name, is_reseller=is_reseller)
    if "disk_space" in kv:
        pkg["storage_limit_mb"] = _parse_int_quota(kv.get("disk_space"), 5120, bytes_to_mb=True)
    if "max_traffic" in kv or "traffic" in kv:
        pkg["bandwidth_limit_mb"] = _parse_int_quota(kv.get("max_traffic") or kv.get("traffic"), 102400, bytes_to_mb=True)
    if "max_dom" in kv or "max_site" in kv:
        pkg["website_limit"] = _parse_int_quota(kv.get("max_dom") if "max_dom" in kv else kv.get("max_site"), 5)
    if "max_subdom" in kv:
        pkg["subdomain_limit"] = _parse_int_quota(kv.get("max_subdom"), 20)
    if "max_dom_aliases" in kv:
        alias_val = _parse_int_quota(kv.get("max_dom_aliases"), 10)
        pkg["alias_limit"] = alias_val
        pkg["domain_pointers_limit"] = alias_val
    if "max_db" in kv or "max_mysql_db" in kv:
        pkg["database_limit"] = _parse_int_quota(kv.get("max_db") if "max_db" in kv else kv.get("max_mysql_db"), 5)
    if "max_box" in kv:
        pkg["mail_accounts_limit"] = _parse_int_quota(kv.get("max_box"), 10)
    if "max_subftp_users" in kv or "max_wu" in kv:
        ftp_val = _parse_int_quota(kv.get("max_subftp_users") if "max_subftp_users" in kv else kv.get("max_wu"), 5)
        pkg["sftp_accounts_limit"] = ftp_val
        pkg["ftp_accounts_limit"] = ftp_val
    if "max_maillists" in kv:
        pkg["mailing_lists_limit"] = _parse_int_quota(kv.get("max_maillists"), 0)
    if "max_cl" in kv:
        pkg["reseller_user_limit"] = _parse_int_quota(kv.get("max_cl"), 25)
        pkg["is_reseller_package"] = True
    if "oversell" in kv:
        pkg["reseller_overselling_enabled"] = _parse_bool_flag(kv.get("oversell"), False)
    if "allow_sh_access" in kv or "shell" in kv:
        sh_raw = str(kv.get("allow_sh_access") or kv.get("shell") or "").strip().lower()
        sh_flag = sh_raw not in {"false", "off", "no", "0", "none", "/bin/false", "/sbin/nologin", " usr/sbin/nologin"}
        pkg["terminal_enabled"] = sh_flag
        pkg["ssh_access"] = sh_flag
    if "php" in kv or "ManagePhp" in raw:
        pkg["php_access"] = _parse_bool_flag(kv.get("php", True), True)
    if "ssl" in kv:
        pkg["ssl_access"] = _parse_bool_flag(kv.get("ssl", True), True)
    if "manage_crontab" in kv:
        pkg["cron_jobs"] = _parse_bool_flag(kv.get("manage_crontab", True), True)
    if "manage_dns" in kv:
        pkg["dns_control"] = _parse_bool_flag(kv.get("manage_dns", True), True)
    if "manage_spamfilter" in kv:
        pkg["spamassassin"] = _parse_bool_flag(kv.get("manage_spamfilter", True), True)
    if "git" in kv:
        pkg["git_access"] = _parse_bool_flag(kv.get("git", True), True)
    return pkg


def _build_ssl_context(verify_ssl: bool = False) -> ssl.SSLContext:
    if verify_ssl:
        return ssl.create_default_context()
    ctx = ssl._create_unverified_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for cipher_str in ("DEFAULT:@SECLEVEL=0", "DEFAULT:@SECLEVEL=1", "ALL:@SECLEVEL=0"):
        try:
            ctx.set_ciphers(cipher_str)
            break
        except Exception:
            pass
    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    if hasattr(ssl, "TLSVersion") and hasattr(ctx, "minimum_version"):
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        except Exception:
            pass
    return ctx


def _validate_remote_url_candidates(raw_url: str, panel: str = "directadmin") -> list[str]:
    url = (raw_url or "").strip().strip("'\"")
    if not url:
        raise ValueError("Remote panel URL is required")
    explicit_scheme = None
    if url.lower().startswith("https://"):
        explicit_scheme = "https"
    elif url.lower().startswith("http://"):
        explicit_scheme = "http"
    else:
        url = "https://" + url

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Invalid remote panel URL")

    host = parsed.hostname.strip().lower()
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0", "169.254.169.254"}:
        raise ValueError("Remote panel URL cannot point to loopback or metadata addresses")

    try:
        port = parsed.port
    except ValueError:
        port = None

    if not port:
        if panel == "directadmin":
            port = 2222
        elif panel == "cpanel":
            port = 2087
        elif panel == "plesk":
            port = 8443

    try:
        infos = socket.getaddrinfo(host, port or 443)
        for info in infos:
            ip_str = info[4][0]
            ip_obj = ipaddress.ip_address(ip_str)
            if ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast:
                raise ValueError("Remote panel host resolves to a restricted address")
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve hostname '{host}': {exc}")

    host_fmt = f"[{host}]" if ":" in host and not host.startswith("[") else host
    netloc = f"{host_fmt}:{port}" if port else host_fmt
    primary_scheme = explicit_scheme or "https"
    alt_scheme = "http" if primary_scheme == "https" else "https"
    return [f"{primary_scheme}://{netloc}", f"{alt_scheme}://{netloc}"]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _extract_cookies_from_headers(hdrs: Any) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if not hdrs:
        return cookies
    raw_list: list[str] = []
    if hasattr(hdrs, "get_all"):
        raw_list = hdrs.get_all("Set-Cookie") or []
    elif hasattr(hdrs, "getheaders"):
        raw_list = hdrs.getheaders("Set-Cookie") or []
    for sc in raw_list:
        first_part = str(sc).split(";", 1)[0].strip()
        if "=" in first_part:
            k, v = first_part.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k and v and v.lower() not in {"deleted", "null", "none", '""', "''"}:
                cookies[k] = v
    return cookies


def _http_fetch(
    url: str,
    headers: dict,
    data: bytes | None = None,
    verify_ssl: bool = False,
    include_origin: bool = False,
    _depth: int = 0,
) -> str:
    if _depth > 4:
        raise RuntimeError("Too many redirects from remote panel")
    ctx = _build_ssl_context(verify_ssl=verify_ssl)
    parsed_u = urllib.parse.urlparse(url)
    origin_url = f"{parsed_u.scheme}://{parsed_u.netloc}" if parsed_u.scheme and parsed_u.netloc else ""
    req_headers: dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (compatible; SPanel-Package-Importer/1.0)",
        "Accept": "application/json, text/plain, */*",
    }
    if include_origin and origin_url:
        req_headers["Referer"] = f"{origin_url}/"
        req_headers["Origin"] = origin_url
    req_headers.update(headers or {})

    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        _NoRedirectHandler(),
    )
    req = urllib.request.Request(
        url,
        data=data,
        headers=req_headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with opener.open(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if "this web server is running in ssl mode" in body[:400].lower() and url.startswith("http://"):
                https_url = "https://" + url[len("http://"):]
                return _http_fetch(
                    https_url, headers, data=data, verify_ssl=verify_ssl, include_origin=include_origin, _depth=_depth + 1
                )
            return body
    except urllib.error.HTTPError as exc:
        body_preview = ""
        try:
            body_preview = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass

        if "this web server is running in ssl mode" in body_preview.lower() and url.startswith("http://"):
            https_url = "https://" + url[len("http://"):]
            return _http_fetch(
                https_url, headers, data=data, verify_ssl=verify_ssl, include_origin=include_origin, _depth=_depth + 1
            )

        if exc.code in (301, 302, 303, 307, 308):
            loc = str(exc.headers.get("Location") or "").strip()
            if loc:
                full_loc = urllib.parse.urljoin(url, loc)
                loc_parsed = urllib.parse.urlparse(full_loc)
                loc_lower = full_loc.lower()
                if "cmd_login" in loc_lower or "/login" in loc_parsed.path.lower():
                    raise RuntimeError(
                        "Authentication failed on remote panel: Invalid username, password, or Login Key."
                    )
                # If remote panel redirected IP -> force_hostname, try keeping the direct IP connection
                # with Host/Referer/Origin set to the redirected hostname first (bypasses Cloudflare/DNS issues),
                # then fall back to following full_loc with Authorization preserved.
                if (
                    loc_parsed.hostname
                    and parsed_u.hostname
                    and loc_parsed.hostname.lower() != parsed_u.hostname.lower()
                    and "Host" not in req_headers
                ):
                    new_scheme = loc_parsed.scheme or parsed_u.scheme
                    same_ip_url = urllib.parse.urlunparse(
                        (new_scheme, parsed_u.netloc, parsed_u.path, parsed_u.params, parsed_u.query, "")
                    )
                    host_hdrs = {
                        **headers,
                        "Host": loc_parsed.netloc,
                        "Referer": f"{new_scheme}://{loc_parsed.netloc}/",
                        "Origin": f"{new_scheme}://{loc_parsed.netloc}",
                    }
                    try:
                        return _http_fetch(
                            same_ip_url,
                            host_hdrs,
                            data=data,
                            verify_ssl=verify_ssl,
                            include_origin=include_origin,
                            _depth=_depth + 1,
                        )
                    except Exception:
                        pass
                return _http_fetch(
                    full_loc,
                    headers,
                    data=data,
                    verify_ssl=verify_ssl,
                    include_origin=include_origin,
                    _depth=_depth + 1,
                )

        if exc.code in (401, 403) or "not logged in" in body_preview.lower() or "unauthorized" in body_preview.lower():
            raise RuntimeError(
                "Authentication failed on remote panel: Invalid username, password, or Login Key."
            )
        raise RuntimeError(f"Remote panel returned HTTP {exc.code}: {body_preview or exc.reason}")
    except Exception as exc:
        err_str = str(exc)
        if url.startswith("https://") and any(
            k in err_str.lower() for k in ("wrong_version_number", "unknown_protocol", "ssl", "eof occurred")
        ):
            http_url = "http://" + url[len("https://"):]
            try:
                return _http_fetch(
                    http_url, headers, data=data, verify_ssl=False, include_origin=include_origin, _depth=_depth + 1
                )
            except Exception:
                pass
        raise RuntimeError(f"Failed to connect to remote panel ({parsed_u.netloc}): {exc}")


def _da_login_session_headers(
    base_url: str, user: str, secret: str, verify_ssl: bool = False, _host_override: str | None = None
) -> tuple[dict[str, str] | None, str | None]:
    """Authenticate against DirectAdmin via /CMD_LOGIN or /api/login and return explicit Cookie/Auth headers."""
    ctx = _build_ssl_context(verify_ssl=verify_ssl)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        _NoRedirectHandler(),
    )
    parsed_b = urllib.parse.urlparse(base_url)
    effective_host = _host_override or parsed_b.netloc
    effective_origin = f"{parsed_b.scheme}://{effective_host}" if parsed_b.scheme and effective_host else base_url
    base_headers: dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (compatible; SPanel-Package-Importer/1.0)",
        "Referer": f"{effective_origin}/CMD_LOGIN",
        "Origin": effective_origin,
    }
    if _host_override:
        base_headers["Host"] = _host_override

    # 1. Classic & Evolution DirectAdmin form login: POST /CMD_LOGIN (with and without json=yes)
    for extra_params in ({"referer": "/"}, {"referer": "/", "json": "yes"}):
        form_data = urllib.parse.urlencode({"username": user, "password": secret, **extra_params}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/CMD_LOGIN",
            data=form_data,
            headers={**base_headers, "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        cookies: dict[str, str] = {}
        body_lower = ""
        loc_raw = ""
        try:
            with opener.open(req, timeout=12) as resp:
                cookies.update(_extract_cookies_from_headers(resp.headers))
                body_lower = resp.read().decode("utf-8", errors="replace").lower()
        except urllib.error.HTTPError as exc:
            cookies.update(_extract_cookies_from_headers(exc.headers))
            loc_raw = str(exc.headers.get("Location") or "").strip()
            try:
                body_lower = exc.read().decode("utf-8", errors="replace").lower()
            except Exception:
                pass
            if exc.code in (301, 302, 303, 307, 308) and loc_raw and not _host_override:
                full_loc = urllib.parse.urljoin(base_url, loc_raw)
                loc_p = urllib.parse.urlparse(full_loc)
                if loc_p.hostname and parsed_b.hostname and loc_p.hostname.lower() != parsed_b.hostname.lower():
                    retry_hdrs, retry_err = _da_login_session_headers(
                        base_url, user, secret, verify_ssl=verify_ssl, _host_override=loc_p.netloc
                    )
                    if retry_hdrs or retry_err:
                        return retry_hdrs, retry_err
        except Exception:
            continue

        loc_lower = loc_raw.lower()
        if "twostep_auth" in body_lower or "twostep_auth" in loc_lower or "two-step" in body_lower:
            return None, "DirectAdmin account has Two-Factor Authentication (2FA) enabled. Please create a Login Key in DirectAdmin and use it as the password."

        sess_val = cookies.get("session") or cookies.get("DirectAdmin-Session") or ""
        if len(sess_val) >= 6 and "invalid" not in body_lower and "login" not in loc_lower:
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            ret_hdrs = {
                "Cookie": cookie_str,
                "Referer": f"{effective_origin}/",
                "Origin": effective_origin,
            }
            if _host_override:
                ret_hdrs["Host"] = _host_override
            return ret_hdrs, None

    # 2. Modern DirectAdmin JSON login: POST /api/login
    try:
        json_payload = json.dumps({"username": user, "password": secret}).encode("utf-8")
        api_hdrs = {
            "User-Agent": "Mozilla/5.0 (compatible; SPanel-Package-Importer/1.0)",
            "Referer": f"{effective_origin}/",
            "Origin": effective_origin,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if _host_override:
            api_hdrs["Host"] = _host_override
        req_api = urllib.request.Request(
            f"{base_url}/api/login",
            data=json_payload,
            headers=api_hdrs,
            method="POST",
        )
        cookies_api: dict[str, str] = {}
        raw_api = ""
        try:
            with opener.open(req_api, timeout=12) as resp:
                cookies_api.update(_extract_cookies_from_headers(resp.headers))
                raw_api = resp.read().decode("utf-8", errors="replace").strip()
        except urllib.error.HTTPError as exc:
            cookies_api.update(_extract_cookies_from_headers(exc.headers))

        sess_val = cookies_api.get("session") or cookies_api.get("DirectAdmin-Session") or ""
        if len(sess_val) >= 6:
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies_api.items())
            ret_hdrs = {
                "Cookie": cookie_str,
                "Referer": f"{effective_origin}/",
                "Origin": effective_origin,
            }
            if _host_override:
                ret_hdrs["Host"] = _host_override
            return ret_hdrs, None

        if raw_api.startswith("{"):
            parsed_login = json.loads(raw_api)
            if isinstance(parsed_login, dict):
                tok = str(parsed_login.get("token") or parsed_login.get("sessionID") or parsed_login.get("session") or "").strip()
                if len(tok) >= 6:
                    ret_hdrs = {
                        "Cookie": f"session={tok}",
                        "Authorization": f"Bearer {tok}",
                        "Referer": f"{effective_origin}/",
                        "Origin": effective_origin,
                    }
                    if _host_override:
                        ret_hdrs["Host"] = _host_override
                    return ret_hdrs, None
    except Exception:
        pass

    return None, None


def _da_parse_response_kv(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            j = json.loads(text)
            if isinstance(j, dict):
                if isinstance(j.get("package"), dict):
                    j = j["package"]
                elif isinstance(j.get("data"), dict):
                    j = j["data"]
                elif isinstance(j.get("settings"), dict):
                    j = j["settings"]
                flat_j: dict[str, Any] = {}
                for k, v in j.items():
                    if isinstance(v, dict):
                        if str(v.get("unlimited", "")).lower() in {"yes", "on", "true", "1"} or v.get("checked") is True:
                            flat_j[k] = "unlimited"
                        elif "value" in v:
                            flat_j[k] = v["value"]
                        elif "selected" in v:
                            flat_j[k] = v["selected"]
                        elif "text" in v:
                            flat_j[k] = v["text"]
                        else:
                            flat_j[k] = v
                    elif isinstance(v, list):
                        flat_j[k] = v[0] if len(v) == 1 else v
                    else:
                        flat_j[k] = v
                return flat_j
        except Exception:
            pass
    qs = urllib.parse.parse_qs(text, keep_blank_values=True)
    if qs and ("=" in text):
        return {k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in qs.items()}
    kv: dict[str, Any] = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = urllib.parse.unquote_plus(v.strip())
    return kv


def _da_fetch_cmd_packages(
    base_url: str,
    headers: dict[str, str],
    list_cmd: str,
    show_cmd: str,
    is_reseller_pkg: bool,
    verify_ssl: bool,
    include_origin: bool = False,
) -> tuple[list[dict] | None, str | None]:
    try:
        raw_list = _http_fetch(
            f"{base_url}/{list_cmd}", headers, verify_ssl=verify_ssl, include_origin=include_origin
        )
    except Exception as exc:
        return None, str(exc)

    stripped = (raw_list or "").strip()
    if not stripped:
        return None, "Empty response from DirectAdmin API."

    lower_all = stripped.lower()
    if (
        "<html" in lower_all
        or "<!doctype" in lower_all
        or "cmd_login" in lower_all
        or "you cannot execute that command" in lower_all
        or "not logged in" in lower_all
    ):
        if "you cannot execute that command" in lower_all:
            return None, "DirectAdmin account or Login Key does not have permission to view packages."
        return None, "Authentication failed on DirectAdmin: Invalid username, password, or Login Key."

    pkg_names: list[str] = []
    direct_items: list[dict] = []
    recognized_response = False
    parsed_json = None
    if stripped.startswith(("{", "[")):
        try:
            parsed_json = json.loads(stripped)
        except Exception:
            parsed_json = None

    if isinstance(parsed_json, list):
        recognized_response = True
        for entry in parsed_json:
            if isinstance(entry, (str, int)) and str(entry).strip():
                pkg_names.append(str(entry).strip())
            elif isinstance(entry, dict):
                p_name = str(entry.get("name") or entry.get("package") or "").strip()
                if p_name:
                    direct_items.append(_map_da_dict_to_package(p_name, entry, is_reseller=is_reseller_pkg))
    elif isinstance(parsed_json, dict):
        err_flag = parsed_json.get("error")
        if err_flag and str(err_flag) not in ("0", "false", "False"):
            err_msg = str(parsed_json.get("result") or parsed_json.get("text") or parsed_json.get("details") or parsed_json.get("message") or err_flag)
            return None, f"DirectAdmin: {err_msg}"
        if "message" in parsed_json and len(parsed_json) <= 3 and "list" not in parsed_json and "packages" not in parsed_json:
            return None, f"DirectAdmin: {parsed_json.get('message')}"

        lst = parsed_json.get("list") if "list" in parsed_json else parsed_json.get("packages")
        if isinstance(lst, list):
            recognized_response = True
            for x in lst:
                if isinstance(x, (str, int)) and str(x).strip():
                    pkg_names.append(str(x).strip())
                elif isinstance(x, dict):
                    p_name = str(x.get("name") or x.get("package") or "").strip()
                    if p_name:
                        direct_items.append(_map_da_dict_to_package(p_name, x, is_reseller=is_reseller_pkg))
        elif isinstance(lst, dict):
            recognized_response = True
            for k, v in lst.items():
                if isinstance(v, dict):
                    direct_items.append(_map_da_dict_to_package(str(k).strip(), v, is_reseller=is_reseller_pkg))
                elif isinstance(v, (str, int)) and str(v).strip():
                    pkg_names.append(str(v).strip())
        else:
            for k, v in parsed_json.items():
                if k in ("error", "text", "details", "result", "columns", "info"):
                    continue
                if isinstance(v, dict):
                    if "package" in v or "name" in v:
                        raw_p = v.get("package") or v.get("name")
                        if isinstance(raw_p, dict):
                            raw_p = raw_p.get("text") or raw_p.get("value") or ""
                        p_str = str(raw_p or "").strip()
                        if p_str:
                            recognized_response = True
                            pkg_names.append(p_str)
                    elif any(qk in v for qk in ("quota", "bandwidth", "vdomains", "nsubdomains")):
                        recognized_response = True
                        direct_items.append(_map_da_dict_to_package(str(k).strip(), v, is_reseller=is_reseller_pkg))
                elif (k.isdigit() or k.startswith("list")) and isinstance(v, (str, int)) and str(v).strip():
                    recognized_response = True
                    pkg_names.append(str(v).strip())
    else:
        parsed_qs = urllib.parse.parse_qs(stripped, keep_blank_values=True)
        err_vals = parsed_qs.get("error")
        if err_vals and str(err_vals[0]).strip() not in ("0", "", "false", "False"):
            text_part = (parsed_qs.get("text") or [""])[0]
            detail_part = (parsed_qs.get("details") or [""])[0]
            err_msg = f"{text_part}: {detail_part}".strip(": ") or "DirectAdmin API returned error=1"
            return None, err_msg
        if "list[]" in parsed_qs or "list" in parsed_qs:
            recognized_response = True
            raw_items = parsed_qs.get("list[]") or parsed_qs.get("list") or []
            pkg_names = [str(x).strip() for x in raw_items if str(x).strip()]
        else:
            for k, v in parsed_qs.items():
                if (k.isdigit() or k.startswith("list[")) and v:
                    recognized_response = True
                    for item in v:
                        if str(item).strip():
                            pkg_names.append(str(item).strip())

    if not recognized_response and not direct_items and not pkg_names:
        return None, "Unrecognized response from DirectAdmin package endpoint."

    if direct_items and not pkg_names:
        return direct_items, None

    seen: set[str] = set()
    unique_names: list[str] = []
    for name in pkg_names:
        if name and name not in seen:
            seen.add(name)
            unique_names.append(name)

    items: list[dict] = list(direct_items)
    for pkg_name in unique_names[:60]:
        q_name = urllib.parse.quote(pkg_name)
        sep = "&" if "?" in show_cmd else "?"
        try:
            detail_raw = _http_fetch(
                f"{base_url}/{show_cmd}{sep}package={q_name}",
                headers,
                verify_ssl=verify_ssl,
                include_origin=include_origin,
            )
            kv = _da_parse_response_kv(detail_raw)
            if str(kv.get("error", "0")) not in ("0", "", "false", "False"):
                continue
            flat_kv = {k: (v[0] if isinstance(v, list) and v else v) for k, v in kv.items()}
            items.append(_map_da_dict_to_package(pkg_name, flat_kv, is_reseller=is_reseller_pkg))
        except Exception:
            items.append(_default_package_dict(pkg_name, is_reseller=is_reseller_pkg))

    return items, None


@router.get("", response_model=List[UserPackageOut])
def list_packages(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    query = db.query(UserPackage)
    if current_user.role == Role.reseller:
        query = query.filter((UserPackage.owner_id == None) | (UserPackage.owner_id == current_user.id))
    return query.order_by(UserPackage.id.asc()).all()


def _reseller_can_grant_terminal(reseller: User) -> bool:
    if bool(getattr(reseller, "terminal_enabled", False)):
        return True
    if reseller.reseller_package is not None and bool(
        getattr(reseller.reseller_package, "ssh_access_users", False)
    ):
        return True
    if reseller.package is not None and bool(
        getattr(reseller.package, "ssh_access_users", False)
    ):
        return True
    return False


@router.post("/fetch-remote")
def fetch_remote_packages(
    payload: RemotePanelFetchIn,
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    is_reseller_pkg = bool(payload.is_reseller_package) if current_user.role == Role.admin else False
    panel = (payload.panel_type or "directadmin").strip().lower()
    user = (payload.username or ("root" if panel == "cpanel" else "admin")).strip()
    secret = (payload.secret or "").strip()
    if not secret:
        return {"ok": False, "error": "Password or API Token is required.", "panel_type": panel, "count": 0, "packages": []}

    try:
        base_candidates = _validate_remote_url_candidates(payload.host_url, panel=panel)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "panel_type": panel, "count": 0, "packages": []}

    basic_token = base64.b64encode(f"{user}:{secret}".encode("utf-8")).decode("ascii")
    results: list[dict] = []

    if panel == "directadmin":
        if user.lower() != "admin":
            cmd_pairs = [
                ("CMD_API_PACKAGES_USER", "CMD_API_SHOW_USER_PACKAGE"),
                ("CMD_PACKAGES_USER?json=yes", "CMD_SHOW_USER_PACKAGE?json=yes"),
                ("CMD_API_PACKAGES_RESELLER", "CMD_API_SHOW_RESELLER_PACKAGE"),
            ]
        elif is_reseller_pkg:
            cmd_pairs = [
                ("CMD_API_PACKAGES_RESELLER", "CMD_API_SHOW_RESELLER_PACKAGE"),
                ("CMD_PACKAGES_RESELLER?json=yes", "CMD_SHOW_RESELLER_PACKAGE?json=yes"),
                ("CMD_API_PACKAGES_USER", "CMD_API_SHOW_USER_PACKAGE"),
                ("CMD_PACKAGES_USER?json=yes", "CMD_SHOW_USER_PACKAGE?json=yes"),
            ]
        else:
            cmd_pairs = [
                ("CMD_API_PACKAGES_USER", "CMD_API_SHOW_USER_PACKAGE"),
                ("CMD_PACKAGES_USER?json=yes", "CMD_SHOW_USER_PACKAGE?json=yes"),
                ("CMD_API_PACKAGES_RESELLER", "CMD_API_SHOW_RESELLER_PACKAGE"),
                ("CMD_PACKAGES_RESELLER?json=yes", "CMD_SHOW_RESELLER_PACKAGE?json=yes"),
            ]

        auth_header_variants = [
            ({"Authorization": f"Basic {basic_token}"}, True),
        ]
        if payload.auth_type == "token":
            auth_header_variants.append(({"Authorization": f"Bearer {secret}"}, True))

        last_err: str | None = None
        any_authenticated_empty = False

        for base_url in base_candidates:
            # 1. Try Basic / Bearer Auth variants
            for hdrs, inc_origin in auth_header_variants:
                for list_cmd, show_cmd in cmd_pairs:
                    pkgs, err = _da_fetch_cmd_packages(
                        base_url,
                        hdrs,
                        list_cmd,
                        show_cmd,
                        is_reseller_pkg,
                        payload.verify_ssl,
                        include_origin=inc_origin,
                    )
                    if pkgs is not None:
                        if len(pkgs) > 0:
                            results = pkgs
                            break
                        any_authenticated_empty = True
                    elif err:
                        last_err = err
                        err_l = err.lower()
                        if "authentication failed" in err_l or "failed to connect" in err_l:
                            break
                if results or ("failed to connect" in (last_err or "").lower()):
                    break

            if results:
                break

            if "failed to connect" in (last_err or "").lower():
                continue

            # 2. Try Session Cookie Login (/CMD_LOGIN and /api/login)
            sess_headers, sess_err = _da_login_session_headers(
                base_url, user, secret, verify_ssl=payload.verify_ssl
            )
            if sess_err:
                last_err = sess_err
                break
            if sess_headers:
                for list_cmd, show_cmd in cmd_pairs:
                    pkgs_s, err_s = _da_fetch_cmd_packages(
                        base_url,
                        sess_headers,
                        list_cmd,
                        show_cmd,
                        is_reseller_pkg,
                        payload.verify_ssl,
                        include_origin=True,
                    )
                    if pkgs_s is not None:
                        if len(pkgs_s) > 0:
                            results = pkgs_s
                            break
                        any_authenticated_empty = True
                    elif err_s:
                        last_err = err_s
                        if "authentication failed" in err_s.lower():
                            break
                if results:
                    break

            # Server responded over HTTP/HTTPS on this scheme, no need to retry alternate scheme
            break

        if not results and not any_authenticated_empty:
            return {
                "ok": False,
                "error": last_err or "Authentication failed on DirectAdmin: Invalid username, password, or Login Key.",
                "panel_type": panel,
                "count": 0,
                "packages": [],
            }

    elif panel == "cpanel":
        auth_headers_list = (
            [{"Authorization": f"whm {user}:{secret}"}, {"Authorization": f"Basic {basic_token}"}]
            if payload.auth_type == "token"
            else [{"Authorization": f"Basic {basic_token}"}, {"Authorization": f"whm {user}:{secret}"}]
        )
        data = None
        last_cp_err = None
        for base_url in base_candidates:
            for hdrs in auth_headers_list:
                for endpoint in ("/json-api/listpkgs?api.version=1", "/json-api/listpkgs"):
                    try:
                        raw_json = _http_fetch(
                            f"{base_url}{endpoint}", hdrs, verify_ssl=payload.verify_ssl
                        )
                        stripped_cp = (raw_json or "").strip()
                        if stripped_cp[:300].lower().startswith(("<!doctype", "<html")) or "<html" in stripped_cp[:400].lower():
                            last_cp_err = "Authentication failed on WHM: Invalid username, password, or WHM API Token."
                            continue
                        parsed_cp = json.loads(stripped_cp)
                        if isinstance(parsed_cp, dict):
                            cpanel_res = parsed_cp.get("cpanelresult")
                            if isinstance(cpanel_res, dict) and cpanel_res.get("error"):
                                last_cp_err = str(cpanel_res.get("error"))
                                continue
                            meta = parsed_cp.get("metadata")
                            if isinstance(meta, dict) and str(meta.get("result", "1")) == "0":
                                last_cp_err = str(meta.get("reason") or "WHM API returned result=0")
                                continue
                            data = parsed_cp
                            break
                    except Exception as exc:
                        last_cp_err = str(exc)
                if data is not None:
                    break
            if data is not None:
                break

        if not isinstance(data, dict):
            return {
                "ok": False,
                "error": last_cp_err or "Authentication failed on WHM: Invalid username, password, or WHM API Token.",
                "panel_type": panel,
                "count": 0,
                "packages": [],
            }
        pkgs_raw = (
            (data.get("data") or {}).get("pkg")
            or data.get("pkg")
            or data.get("package")
            or data.get("packages")
            or []
        )
        if not isinstance(pkgs_raw, list):
            pkgs_raw = []
        for item in pkgs_raw[:100]:
            if not isinstance(item, dict):
                continue
            pkg_name = str(item.get("name") or item.get("PACKAGE") or "cpanel_pkg").strip()
            results.append(_map_cpanel_dict_to_package(pkg_name, item, is_reseller=is_reseller_pkg))

    elif panel == "plesk":
        rest_headers_list = (
            [
                {"Accept": "application/json", "X-API-Key": secret},
                {"Accept": "application/json", "Authorization": f"Basic {basic_token}"},
            ]
            if payload.auth_type == "token"
            else [
                {"Accept": "application/json", "Authorization": f"Basic {basic_token}"},
                {"Accept": "application/json", "X-API-Key": secret},
            ]
        )
        xml_headers_list = (
            [
                {"Content-Type": "text/xml", "HTTP_PRETTY_PRINT": "TRUE", "KEY": secret},
                {"Content-Type": "text/xml", "HTTP_PRETTY_PRINT": "TRUE", "HTTP_AUTH_LOGIN": user, "HTTP_AUTH_PASSWD": secret},
            ]
            if payload.auth_type == "token"
            else [
                {"Content-Type": "text/xml", "HTTP_PRETTY_PRINT": "TRUE", "HTTP_AUTH_LOGIN": user, "HTTP_AUTH_PASSWD": secret},
                {"Content-Type": "text/xml", "HTTP_PRETTY_PRINT": "TRUE", "KEY": secret},
            ]
        )
        last_plesk_err = None

        def _try_plesk_rest(b_url: str) -> bool:
            nonlocal last_plesk_err
            for r_hdrs in rest_headers_list:
                try:
                    raw_rest = _http_fetch(f"{b_url}/api/v2/serviceplans", r_hdrs, verify_ssl=payload.verify_ssl)
                    plans = json.loads(raw_rest)
                    if isinstance(plans, list):
                        for p in plans[:100]:
                            if not isinstance(p, dict):
                                continue
                            p_name = str(p.get("name") or "plesk_plan").strip()
                            flat: dict[str, Any] = {}
                            for lim in p.get("limits", []) if isinstance(p.get("limits"), list) else []:
                                if isinstance(lim, dict) and "name" in lim:
                                    flat[lim["name"]] = lim.get("value")
                            for perm in p.get("permissions", []) if isinstance(p.get("permissions"), list) else []:
                                if isinstance(perm, dict) and "name" in perm:
                                    flat[perm["name"]] = perm.get("value")
                            flat.update({k: v for k, v in p.items() if not isinstance(v, (list, dict))})
                            results.append(_map_plesk_dict_to_package(p_name, flat, is_reseller=is_reseller_pkg))
                        return True
                except Exception as exc:
                    last_plesk_err = str(exc)
            return False

        def _try_plesk_xml(b_url: str, xml_tag: str) -> bool:
            nonlocal last_plesk_err
            xml_body = (
                f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<{ "packet" }><{xml_tag}><get><filter/></get></{xml_tag}></{ "packet" }>'
            ).encode("utf-8")
            for x_hdrs in xml_headers_list:
                try:
                    raw_xml = _http_fetch(
                        f"{b_url}/enterprise/control/agent.php",
                        x_hdrs,
                        data=xml_body,
                        verify_ssl=payload.verify_ssl,
                    )
                    root = ET.fromstring(raw_xml)
                    sys_err = root.findtext(".//system/errtext")
                    if sys_err:
                        last_plesk_err = f"Plesk API: {sys_err}"
                        continue
                    for res_node in root.findall(f".//{xml_tag}/get/result"):
                        if res_node.findtext("status") == "error":
                            err_txt = res_node.findtext("errtext")
                            if err_txt:
                                last_plesk_err = f"Plesk API: {err_txt}"
                            continue
                        p_name = (res_node.findtext("name") or "plesk_plan").strip()
                        if not p_name:
                            continue
                        flat_xml: dict[str, Any] = {}
                        for lim_node in res_node.findall(".//limits/limit"):
                            ln = lim_node.findtext("name")
                            lv = lim_node.findtext("value")
                            if ln:
                                flat_xml[ln] = lv
                        for perm_node in res_node.findall(".//permissions/permission"):
                            pn = perm_node.findtext("name")
                            pv = perm_node.findtext("value")
                            if pn:
                                flat_xml[pn] = pv
                        results.append(_map_plesk_dict_to_package(p_name, flat_xml, is_reseller=is_reseller_pkg))
                    return True
                except Exception as exc:
                    last_plesk_err = str(exc)
            return False

        ok = False
        for base_url in base_candidates:
            if is_reseller_pkg:
                ok = _try_plesk_xml(base_url, "reseller-plan")
                if not results:
                    ok = _try_plesk_rest(base_url) or _try_plesk_xml(base_url, "service-plan") or ok
            else:
                ok = _try_plesk_rest(base_url)
                if not results:
                    ok = _try_plesk_xml(base_url, "service-plan") or ok
            if results or ok:
                break

        if not ok and not results:
            return {
                "ok": False,
                "error": last_plesk_err or "Authentication failed on Plesk: Invalid username, password, or API Key.",
                "panel_type": panel,
                "count": 0,
                "packages": [],
            }
    else:
        return {"ok": False, "error": f"Unsupported panel_type: {panel}", "panel_type": panel, "count": 0, "packages": []}

    return {"ok": True, "panel_type": panel, "count": len(results), "packages": results}


@router.post("/import")
def batch_import_packages(
    payload: PackageBatchImportIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    if not payload.packages:
        raise HTTPException(status_code=400, detail="No packages provided for import")

    conflict_mode = (payload.conflict_mode or "overwrite").strip().lower()
    if conflict_mode not in {"overwrite", "skip", "rename"}:
        conflict_mode = "overwrite"

    can_grant_term = True
    if current_user.role == Role.reseller.value:
        can_grant_term = _reseller_can_grant_terminal(current_user)

    created_count = 0
    updated_count = 0
    skipped_count = 0
    imported_names: list[str] = []

    for item in payload.packages[:100]:
        raw_name = (item.name or "").strip()
        if not raw_name:
            continue

        terminal_flag = bool(item.terminal_enabled or item.ssh_access)
        ssh_users_flag = bool(item.ssh_access_users)
        if current_user.role == Role.reseller.value and not can_grant_term:
            terminal_flag = False
            ssh_users_flag = False

        effective_alias = item.alias_limit if item.alias_limit is not None else item.domain_pointers_limit
        effective_pointers = item.domain_pointers_limit if item.domain_pointers_limit is not None else effective_alias
        effective_sftp = item.sftp_accounts_limit if item.sftp_accounts_limit is not None else item.ftp_accounts_limit
        effective_ftp = item.ftp_accounts_limit if item.ftp_accounts_limit is not None else effective_sftp
        is_reseller_pkg = bool(item.is_reseller_package) if current_user.role == Role.admin else False

        existing = db.query(UserPackage).filter(UserPackage.name == raw_name).first()
        target_name = raw_name

        if existing:
            if conflict_mode == "skip":
                skipped_count += 1
                continue
            elif conflict_mode == "rename":
                suffix = 2
                candidate = f"{raw_name}_imported"
                while db.query(UserPackage).filter(UserPackage.name == candidate).first():
                    candidate = f"{raw_name}_{suffix}"
                    suffix += 1
                target_name = candidate
                existing = None
            elif conflict_mode == "overwrite":
                if current_user.role == Role.reseller and existing.owner_id != current_user.id:
                    # Reseller cannot overwrite Admin/other Reseller package; auto-rename instead
                    suffix = 2
                    candidate = f"{raw_name}_reseller"
                    while db.query(UserPackage).filter(UserPackage.name == candidate).first():
                        candidate = f"{raw_name}_reseller_{suffix}"
                        suffix += 1
                    target_name = candidate
                    existing = None

        field_values = dict(
            website_limit=item.website_limit,
            storage_limit_mb=item.storage_limit_mb,
            database_limit=item.database_limit,
            alias_limit=effective_alias,
            backup_retention_days=max(1, int(item.backup_retention_days or 14)),
            terminal_enabled=terminal_flag,
            waf_enabled=item.waf_enabled,
            wordpress_enabled=item.wordpress_enabled,
            node_apps_limit=item.node_apps_limit,
            sftp_accounts_limit=effective_sftp,
            mail_accounts_limit=item.mail_accounts_limit,
            node_app_memory_mb=max(64, int(item.node_app_memory_mb or 512)),
            is_reseller_package=is_reseller_pkg,
            reseller_user_limit=item.reseller_user_limit if current_user.role == Role.admin else 0,
            reseller_overselling_enabled=item.reseller_overselling_enabled if current_user.role == Role.admin else False,
            bandwidth_limit_mb=item.bandwidth_limit_mb,
            inode_limit=item.inode_limit,
            subdomain_limit=item.subdomain_limit,
            email_forwarders_limit=item.email_forwarders_limit,
            mailing_lists_limit=item.mailing_lists_limit,
            autoresponders_limit=item.autoresponders_limit,
            domain_pointers_limit=effective_pointers,
            ftp_accounts_limit=effective_ftp,
            ips_limit=item.ips_limit,
            anonymous_ftp=item.anonymous_ftp,
            cgi_access=item.cgi_access,
            git_access=item.git_access,
            php_access=item.php_access,
            spamassassin=item.spamassassin,
            catch_all_email=item.catch_all_email,
            ssl_access=item.ssl_access,
            ssh_access=terminal_flag,
            ssh_access_users=ssh_users_flag,
            cron_jobs=item.cron_jobs,
            redis_access=item.redis_access,
            system_info=item.system_info,
            login_keys=item.login_keys,
            dns_control=item.dns_control,
            personal_dns_mode=item.personal_dns_mode,
            share_server_ip=item.share_server_ip,
            cpu_quota_percent=item.cpu_quota_percent,
            io_read_mbps=item.io_read_mbps,
            io_write_mbps=item.io_write_mbps,
            iops_read_max=item.iops_read_max,
            iops_write_max=item.iops_write_max,
            memory_high_mb=item.memory_high_mb,
            memory_max_mb=item.memory_max_mb,
            tasks_max=item.tasks_max,
            mysql_storage_limit_mb=item.mysql_storage_limit_mb,
            mysql_max_db_size_mb=item.mysql_max_db_size_mb,
        )

        if existing:
            for k, v in field_values.items():
                setattr(existing, k, v)
            updated_count += 1
            imported_names.append(existing.name)
        else:
            new_pkg = UserPackage(
                name=target_name,
                slug=item.slug,
                owner_id=current_user.id if current_user.role == Role.reseller else None,
                **field_values,
            )
            db.add(new_pkg)
            created_count += 1
            imported_names.append(target_name)

    db.commit()
    log_action(
        db,
        current_user.id,
        "import_packages",
        f"{payload.source_panel}: created={created_count}, updated={updated_count}, skipped={skipped_count}",
        request=request,
    )
    return {
        "ok": True,
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "names": imported_names,
    }


@router.post("", response_model=UserPackageOut)
def create_package(
    payload: UserPackageCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    terminal_flag = bool(payload.terminal_enabled or payload.ssh_access)
    if current_user.role == Role.reseller.value and (terminal_flag or payload.ssh_access_users):
        if not _reseller_can_grant_terminal(current_user):
            raise HTTPException(status_code=403, detail="You are not allowed to enable terminal access")
    _ensure_unique_name(db, payload.name)

    # Standardize paired fields so legacy & canonical columns stay in sync
    effective_alias_limit = payload.alias_limit if payload.alias_limit is not None else payload.domain_pointers_limit
    effective_pointers_limit = payload.domain_pointers_limit if payload.domain_pointers_limit is not None else effective_alias_limit
    effective_sftp_limit = payload.sftp_accounts_limit if payload.sftp_accounts_limit is not None else payload.ftp_accounts_limit
    effective_ftp_limit = payload.ftp_accounts_limit if payload.ftp_accounts_limit is not None else effective_sftp_limit

    package = UserPackage(
        name=payload.name,
        slug=payload.slug,
        owner_id=current_user.id if current_user.role == Role.reseller else None,
        website_limit=payload.website_limit,
        storage_limit_mb=payload.storage_limit_mb,
        database_limit=payload.database_limit,
        alias_limit=effective_alias_limit,
        backup_retention_days=payload.backup_retention_days,
        terminal_enabled=terminal_flag,
        waf_enabled=payload.waf_enabled,
        wordpress_enabled=payload.wordpress_enabled,
        node_apps_limit=payload.node_apps_limit,
        sftp_accounts_limit=effective_sftp_limit,
        mail_accounts_limit=payload.mail_accounts_limit,
        node_app_memory_mb=max(64, int(payload.node_app_memory_mb or 512)),

        is_reseller_package=payload.is_reseller_package if current_user.role == Role.admin else False,
        reseller_user_limit=payload.reseller_user_limit if current_user.role == Role.admin else 0,
        reseller_overselling_enabled=payload.reseller_overselling_enabled if current_user.role == Role.admin else False,

        bandwidth_limit_mb=payload.bandwidth_limit_mb,
        inode_limit=payload.inode_limit,
        subdomain_limit=payload.subdomain_limit,
        email_forwarders_limit=payload.email_forwarders_limit,
        mailing_lists_limit=payload.mailing_lists_limit,
        autoresponders_limit=payload.autoresponders_limit,
        domain_pointers_limit=effective_pointers_limit,
        ftp_accounts_limit=effective_ftp_limit,
        ips_limit=payload.ips_limit,
        anonymous_ftp=payload.anonymous_ftp,
        cgi_access=payload.cgi_access,
        git_access=payload.git_access,
        php_access=payload.php_access,
        spamassassin=payload.spamassassin,
        catch_all_email=payload.catch_all_email,
        ssl_access=payload.ssl_access,
        ssh_access=terminal_flag,
        ssh_access_users=payload.ssh_access_users,
        cron_jobs=payload.cron_jobs,
        redis_access=payload.redis_access,
        system_info=payload.system_info,
        login_keys=payload.login_keys,
        dns_control=payload.dns_control,
        personal_dns_mode=payload.personal_dns_mode,
        share_server_ip=payload.share_server_ip,
        cpu_quota_percent=payload.cpu_quota_percent,
        io_read_mbps=payload.io_read_mbps,
        io_write_mbps=payload.io_write_mbps,
        iops_read_max=payload.iops_read_max,
        iops_write_max=payload.iops_write_max,
        memory_high_mb=payload.memory_high_mb,
        memory_max_mb=payload.memory_max_mb,
        tasks_max=payload.tasks_max,
        mysql_storage_limit_mb=payload.mysql_storage_limit_mb,
        mysql_max_db_size_mb=payload.mysql_max_db_size_mb,
    )
    db.add(package)
    db.commit()
    db.refresh(package)
    log_action(db, current_user.id, "create_package", package.name, request=request)
    return package


@router.patch("/{package_id:int}", response_model=UserPackageOut)
def update_package(
    package_id: int,
    payload: UserPackageUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    package = _package_by_id(db, package_id)
    if current_user.role == Role.reseller and package.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot modify a package you do not own")
    if current_user.role == Role.reseller.value and (
        payload.terminal_enabled is True or payload.ssh_access is True or payload.ssh_access_users is True
    ):
        if not _reseller_can_grant_terminal(current_user):
            raise HTTPException(status_code=403, detail="You are not allowed to enable terminal access")

    if payload.name is not None and payload.name != package.name:
        _ensure_unique_name(db, payload.name, package_id=package.id)
        package.name = payload.name
    if payload.slug is not None:
        package.slug = payload.slug
    if payload.website_limit is not None:
        package.website_limit = payload.website_limit
    if payload.storage_limit_mb is not None:
        package.storage_limit_mb = payload.storage_limit_mb
    if payload.database_limit is not None:
        package.database_limit = payload.database_limit
    if payload.alias_limit is not None:
        package.alias_limit = payload.alias_limit
        if payload.domain_pointers_limit is None:
            package.domain_pointers_limit = payload.alias_limit
    if payload.domain_pointers_limit is not None:
        package.domain_pointers_limit = payload.domain_pointers_limit
        if payload.alias_limit is None:
            package.alias_limit = payload.domain_pointers_limit
    if payload.backup_retention_days is not None:
        package.backup_retention_days = payload.backup_retention_days
    if payload.terminal_enabled is not None or payload.ssh_access is not None:
        term_val = bool(
            payload.terminal_enabled
            if payload.terminal_enabled is not None
            else payload.ssh_access
        )
        package.terminal_enabled = term_val
        package.ssh_access = term_val
    if payload.waf_enabled is not None:
        package.waf_enabled = payload.waf_enabled
    if payload.wordpress_enabled is not None:
        package.wordpress_enabled = payload.wordpress_enabled
    if payload.node_apps_limit is not None:
        package.node_apps_limit = payload.node_apps_limit
    if payload.sftp_accounts_limit is not None:
        package.sftp_accounts_limit = payload.sftp_accounts_limit
        if payload.ftp_accounts_limit is None:
            package.ftp_accounts_limit = payload.sftp_accounts_limit
    if payload.ftp_accounts_limit is not None:
        package.ftp_accounts_limit = payload.ftp_accounts_limit
        if payload.sftp_accounts_limit is None:
            package.sftp_accounts_limit = payload.ftp_accounts_limit
    if payload.mail_accounts_limit is not None:
        package.mail_accounts_limit = payload.mail_accounts_limit
    if payload.node_app_memory_mb is not None:
        package.node_app_memory_mb = max(64, int(payload.node_app_memory_mb))
    if current_user.role == Role.admin:
        if payload.is_reseller_package is not None:
            package.is_reseller_package = payload.is_reseller_package
        if payload.reseller_user_limit is not None:
            package.reseller_user_limit = payload.reseller_user_limit
        if payload.reseller_overselling_enabled is not None:
            package.reseller_overselling_enabled = payload.reseller_overselling_enabled

    for field in [
        "bandwidth_limit_mb", "inode_limit", "subdomain_limit", "email_forwarders_limit",
        "mailing_lists_limit", "autoresponders_limit", "ips_limit",
        "anonymous_ftp", "cgi_access", "git_access", "php_access", "spamassassin",
        "catch_all_email", "ssl_access", "ssh_access_users", "cron_jobs",
        "redis_access", "system_info", "login_keys", "dns_control",
        "personal_dns_mode", "share_server_ip", "cpu_quota_percent",
        "io_read_mbps", "io_write_mbps", "iops_read_max", "iops_write_max",
        "memory_high_mb", "memory_max_mb", "tasks_max",
        "mysql_storage_limit_mb", "mysql_max_db_size_mb",
    ]:
        val = getattr(payload, field, None)
        if val is not None:
            setattr(package, field, val)

    affected_users = db.query(User).filter(User.package_id == package.id).all()
    for user in affected_users:
        user.website_limit = package.website_limit
        user.storage_limit_mb = package.storage_limit_mb
        user.database_limit = package.database_limit
        user.bandwidth_limit_mb = package.bandwidth_limit_mb
        user.inode_limit = package.inode_limit
        user.terminal_enabled = package.terminal_enabled
        user.sftp_accounts_limit = package.sftp_accounts_limit
        user.mail_accounts_limit = package.mail_accounts_limit
        user.cpu_quota_percent = package.cpu_quota_percent
        user.io_read_mbps = package.io_read_mbps
        user.io_write_mbps = package.io_write_mbps
        user.iops_read_max = package.iops_read_max
        user.iops_write_max = package.iops_write_max
        user.memory_high_mb = package.memory_high_mb
        user.memory_max_mb = package.memory_max_mb
        user.tasks_max = package.tasks_max
        user.mysql_storage_limit_mb = package.mysql_storage_limit_mb
        user.mysql_max_db_size_mb = package.mysql_max_db_size_mb
    db.commit()
    from app.services import resource_monitor
    for user in affected_users:
        resource_monitor.apply_user_cgroup_limits_async(user.id)
    db.refresh(package)
    log_action(db, current_user.id, "update_package", package.name, request=request)
    return package


@router.delete("/{package_id:int}")
def delete_package(
    package_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    package = _package_by_id(db, package_id)
    if current_user.role == Role.reseller and package.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot delete a package you do not own")
    if db.query(User).filter((User.package_id == package.id) | (User.reseller_package_id == package.id)).first():
        raise HTTPException(status_code=400, detail="Package is in use")
    name = package.name
    db.delete(package)
    db.commit()
    log_action(db, current_user.id, "delete_package", name, request=request)
    return {"ok": True}
