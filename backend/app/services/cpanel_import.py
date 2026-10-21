"""Import cPanel backup archives (cpmove-*.tar.gz, backup-*.tar.gz) into SPanel.

Supports full cPanel account backups and cpmove archives:
- Discovers account metadata from ``cp/<user>`` and ``userdata/main`` + ``userdata/<domain>``
- Restores main domain, addon domains, subdomains, and parked domains (aliases)
- Unpacks nested ``homedir.tar`` / ``homedir.tar.gz`` when present
- Restores MariaDB databases from ``mysql/*.sql`` and recovers password hashes from ``mysql.sql``
- Auto-updates ``wp-config.php``, ``.env``, and ``configuration.php`` when credentials change
- Restores email accounts, password hashes (``$6$`` SHA512-CRYPT), quotas, forwarders (``va/<domain>``),
  and Maildir messages from ``homedir/mail/<domain>/<local>``
- Restores SSL certificates from ``ssl/certs`` & ``ssl/keys`` (or issues Let's Encrypt when DNS matches)
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from passlib.hash import sha512_crypt

from app.core.database import SessionLocal
from app.models.entities import DatabaseAccount, User, Website
from app.services import da_import, mariadb, nginx, site_users, waf

logger = logging.getLogger("spanel.cpanel_import")

SYSTEM_SQL_STEMS = {"mysql", "roundcube", "horde", "information_schema", "performance_schema", "phpmyadmin", "cphulkd"}


def _log(msg: str) -> None:
    logger.info(msg)
    print(msg, flush=True)


def _find_cpanel_root(extracted: Path) -> Path:
    """Locate the cpmove-<user> or backup-<date>_<user> directory inside extracted tree."""
    markers = ("cp", "userdata", "homedir", "mysql", "va", "ssl")
    if any((extracted / m).exists() for m in markers):
        return extracted

    candidates: list[tuple[int, Path]] = []
    for child in extracted.iterdir():
        if not child.is_dir():
            continue
        score = sum(2 for m in markers if (child / m).exists())
        if child.name.startswith(("cpmove-", "backup-")):
            score += 3
        if (child / "homedir.tar").is_file() or (child / "homedir.tar.gz").is_file():
            score += 2
        if score > 0:
            candidates.append((score, child))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    for sub in extracted.rglob("userdata"):
        if sub.is_dir():
            return sub.parent
    for sub in extracted.rglob("homedir"):
        if sub.is_dir():
            return sub.parent
    return extracted


def _ensure_homedir_extracted(root: Path) -> Path:
    """Extract nested homedir.tar / homedir.tar.gz if older cPanel backup packed it separately."""
    homedir = root / "homedir"
    for tar_name in ("homedir.tar", "homedir.tar.gz", "homedir.tgz"):
        nested = root / tar_name
        if nested.is_file() and (not homedir.is_dir() or not any(homedir.iterdir())):
            _log(f"  Extracting nested {tar_name} into homedir/ ...")
            homedir.mkdir(parents=True, exist_ok=True)
            try:
                da_import._safe_extract_tar(nested, homedir)
            except Exception as exc:
                _log(f"  WARNING: failed to extract {tar_name}: {exc}")
    return homedir


def _strip_cpanel_archive_username(archive_name: str) -> str:
    stem = da_import._strip_archive_suffix(archive_name)
    if stem.startswith("cpmove-"):
        return stem[len("cpmove-"):]
    # backup-10.21.2026_12-00-00_username
    m = re.match(r"^backup-[0-9._-]+_([A-Za-z0-9_.-]+)$", stem)
    if m:
        return m.group(1)
    return da_import._archive_username(archive_name)


def _discover_cpanel_username_and_email(root: Path, archive_name: str) -> tuple[str, str, Optional[str]]:
    """Return (normalized_username, email, main_domain_from_cp)."""
    raw_user = ""
    email = ""
    main_domain = None

    cp_dir = root / "cp"
    if cp_dir.is_dir():
        for item in sorted(cp_dir.iterdir()):
            if item.is_file() and not item.name.startswith("."):
                if not raw_user:
                    raw_user = item.name
                kv = da_import._read_key_values(item)
                raw_user = kv.get("user") or raw_user
                email = kv.get("contactemail") or kv.get("contactemail2") or email
                dns_dom = da_import._normalize_domain(kv.get("dns") or "")
                if dns_dom:
                    main_domain = dns_dom
                break

    if not raw_user:
        raw_user = _strip_cpanel_archive_username(archive_name)

    return da_import._normalize_username(raw_user, archive_name), email, main_domain


def _parse_userdata_file(path: Path) -> dict[str, str]:
    """Read key-value pairs from a cPanel userdata file (YAML or JSON)."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return {str(k).strip().lower(): str(v).strip() for k, v in data.items() if not isinstance(v, (dict, list))}
        except Exception:
            pass
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("---"):
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            k_clean = k.strip().lower()
            v_clean = v.strip().strip("'\"")
            if k_clean and v_clean and not raw.startswith((" ", "\t")):
                out[k_clean] = v_clean
    return out


def _parse_userdata_main(path: Path) -> dict:
    """Parse cPanel's userdata/main (YAML or JSON) for main_domain, addon_domains, sub_domains, parked_domains."""
    result: dict = {
        "main_domain": "",
        "addon_domains": {},
        "sub_domains": [],
        "parked_domains": [],
    }
    if not path.is_file():
        return result
    text = path.read_text(encoding="utf-8", errors="ignore")
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                result["main_domain"] = str(data.get("main_domain") or "").strip().lower()
                if isinstance(data.get("addon_domains"), dict):
                    result["addon_domains"] = {
                        str(k).strip().lower(): str(v).strip().lower()
                        for k, v in data["addon_domains"].items()
                    }
                if isinstance(data.get("sub_domains"), list):
                    result["sub_domains"] = [str(x).strip().lower() for x in data["sub_domains"]]
                if isinstance(data.get("parked_domains"), list):
                    result["parked_domains"] = [str(x).strip().lower() for x in data["parked_domains"]]
                return result
        except Exception:
            pass

    section = None
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#") or raw.strip() == "---":
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0:
            if line.startswith("main_domain:"):
                result["main_domain"] = line.partition(":")[2].strip().strip("'\"").lower()
                section = None
            elif line.startswith("addon_domains:"):
                section = "addon"
            elif line.startswith("sub_domains:"):
                section = "sub"
            elif line.startswith("parked_domains:"):
                section = "parked"
            else:
                section = None
        elif section == "addon" and ":" in line:
            k, _, v = line.partition(":")
            dom = da_import._normalize_domain(k.strip().strip("'\""))
            if dom:
                result["addon_domains"][dom] = v.strip().strip("'\"").lower()
        elif section == "sub" and line.startswith("-"):
            sub = da_import._normalize_domain(line.lstrip("-").strip().strip("'\""))
            if sub:
                result["sub_domains"].append(sub)
        elif section == "parked" and line.startswith("-"):
            parked = da_import._normalize_domain(line.lstrip("-").strip().strip("'\""))
            if parked:
                result["parked_domains"].append(parked)
    return result


def _resolve_cpanel_docroot(homedir: Path, docroot_str: str, domain: str, is_main: bool) -> Optional[Path]:
    """Map a cPanel documentroot string (e.g. /home/user/public_html/sub) to the extracted homedir."""
    if docroot_str:
        parts = [p for p in docroot_str.replace("\\", "/").split("/") if p]
        # Strip /home/<user>/ prefix if present
        if len(parts) >= 2 and parts[0] in {"home", "home2", "home3", "var", "usr"}:
            rel_parts = parts[2:]
        else:
            rel_parts = parts
        if rel_parts:
            candidate = homedir.joinpath(*rel_parts)
            if candidate.is_dir():
                return candidate

    candidates = []
    if is_main:
        candidates.append(homedir / "public_html")
        candidates.append(homedir / "www")
    candidates.extend([
        homedir / domain,
        homedir / "public_html" / domain,
        homedir / "domains" / domain / "public_html",
    ])
    for cand in candidates:
        if cand.is_dir():
            return cand
    return (homedir / "public_html") if (is_main and (homedir / "public_html").is_dir()) else None


def _discover_cpanel_domains_and_sources(
    root: Path, homedir: Path, fallback_main_domain: Optional[str]
) -> tuple[list[tuple[str, Optional[Path]]], dict[str, list[tuple[str, str]]]]:
    """Return ([(domain, source_path), ...], {domain: [(alias, mode), ...]})."""
    userdata_dir = root / "userdata"
    main_info = _parse_userdata_main(userdata_dir / "main") if userdata_dir.is_dir() else {
        "main_domain": "", "addon_domains": {}, "sub_domains": [], "parked_domains": [],
    }
    if not main_info["main_domain"] and (userdata_dir / "main.cache").is_file():
        main_info = _parse_userdata_main(userdata_dir / "main.cache")

    main_domain = da_import._normalize_domain(main_info.get("main_domain") or "") or fallback_main_domain

    # Map domain -> relative docroot from userdata/<domain_or_sub>
    domain_docroots: dict[str, str] = {}
    sub_to_addon: dict[str, str] = {}
    for addon_dom, sub_dom in (main_info.get("addon_domains") or {}).items():
        norm_addon = da_import._normalize_domain(addon_dom)
        if norm_addon:
            sub_to_addon[sub_dom.lower()] = norm_addon
            domain_docroots.setdefault(norm_addon, "")

    if userdata_dir.is_dir():
        for entry in sorted(userdata_dir.iterdir()):
            if not entry.is_file() or entry.name in {"main", "main.cache"} or entry.name.endswith((".cache", "_SSL")):
                continue
            kv = _parse_userdata_file(entry)
            servername = (kv.get("servername") or entry.name).strip().lower()
            docroot = kv.get("documentroot") or ""
            target_dom = sub_to_addon.get(servername) or da_import._normalize_domain(servername)
            if target_dom:
                domain_docroots[target_dom] = docroot

    # Fallback: read root/addons, root/sds, root/pds
    addons_file = root / "addons"
    if addons_file.is_file():
        for raw in addons_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if "=" in line and not line.startswith("#"):
                dom_part, _, rest = line.partition("=")
                norm_d = da_import._normalize_domain(dom_part.strip())
                if norm_d:
                    domain_docroots.setdefault(norm_d, "")

    ordered_domains: list[str] = []
    if main_domain:
        ordered_domains.append(main_domain)
    for addon_dom in (main_info.get("addon_domains") or {}):
        norm_d = da_import._normalize_domain(addon_dom)
        if norm_d and norm_d not in ordered_domains:
            ordered_domains.append(norm_d)
    for dom in domain_docroots:
        if dom not in ordered_domains and dom not in sub_to_addon:
            ordered_domains.append(dom)
    for sub_dom in (main_info.get("sub_domains") or []):
        if sub_dom.lower() in sub_to_addon:
            continue
        norm_sub = da_import._normalize_domain(sub_dom)
        if norm_sub and norm_sub not in ordered_domains:
            ordered_domains.append(norm_sub)

    # Also check cp/<user> DNS1=..., DNS2=... if still empty
    if not ordered_domains and (root / "cp").is_dir():
        for cp_file in (root / "cp").iterdir():
            if cp_file.is_file():
                kv = da_import._read_key_values(cp_file)
                for k, v in kv.items():
                    if k.startswith("dns"):
                        d = da_import._normalize_domain(v)
                        if d and d not in ordered_domains:
                            ordered_domains.append(d)

    targets: list[tuple[str, Optional[Path]]] = []
    # To prevent copying addon/subdomain subfolders located inside main public_html twice,
    # relocate nested docroots into a staging sibling directory first.
    holding = root / "_spanel_cpanel_subroots"
    main_public = homedir / "public_html"

    # First resolve non-main domains that might live inside public_html/<folder>
    non_main_sources: dict[str, Optional[Path]] = {}
    for dom in ordered_domains:
        if dom == main_domain:
            continue
        docroot_str = domain_docroots.get(dom, "")
        src = _resolve_cpanel_docroot(homedir, docroot_str, dom, is_main=False)
        if src and main_public.is_dir() and src != main_public:
            try:
                src.relative_to(main_public)
                dest = holding / dom
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.move(str(src), str(dest))
                src = dest
            except ValueError:
                pass
        non_main_sources[dom] = src

    for dom in ordered_domains:
        if dom == main_domain:
            src = _resolve_cpanel_docroot(homedir, domain_docroots.get(dom, ""), dom, is_main=True)
            targets.append((dom, src))
        else:
            targets.append((dom, non_main_sources.get(dom)))

    # Parked domains become aliases on main_domain
    aliases_by_domain: dict[str, list[tuple[str, str]]] = {}
    parked_list = list(main_info.get("parked_domains") or [])
    pds_file = root / "pds"
    if pds_file.is_file():
        for raw in pds_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            p = da_import._normalize_domain(raw.strip())
            if p and p not in parked_list:
                parked_list.append(p)
    if main_domain and parked_list:
        aliases_by_domain[main_domain] = [
            (p, "alias") for p in parked_list if p != main_domain and p not in ordered_domains
        ]

    return targets, aliases_by_domain


def _discover_cpanel_sql_files(root: Path) -> dict[str, Path]:
    """Find user SQL dumps inside mysql/ or mysql-dumps/ in a cPanel backup."""
    result: dict[str, Path] = {}
    for folder_name in ("mysql", "mysql-dumps"):
        mdir = root / folder_name
        if not mdir.is_dir():
            continue
        for item in sorted(mdir.iterdir()):
            if not item.is_file():
                continue
            if not any(item.name.lower().endswith(s) for s in da_import.SQL_SUFFIXES):
                continue
            base = da_import._sql_base_name(item).lower()
            if base in SYSTEM_SQL_STEMS:
                continue
            result.setdefault(base, item)
    return result


def _discover_cpanel_db_hashes(root: Path) -> dict[str, str]:
    """Parse mysql.sql in cPanel backup to extract *40-hex mysql_native_password hashes per user."""
    hashes: dict[str, str] = {}
    mysql_sql = root / "mysql.sql"
    if not mysql_sql.is_file():
        mysql_sql = root / "mysql" / "mysql.sql"
    if not mysql_sql.is_file():
        return hashes
    try:
        text = mysql_sql.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(
            r"['\"`]([A-Za-z0-9_]+)['\"`]\s*@\s*['\"`][^'\"`]+['\"`][^\n;]*?(\*[0-9A-Fa-f]{40})",
            text,
            re.IGNORECASE,
        ):
            db_user = m.group(1).strip().lower()
            pw_hash = m.group(2).strip()
            hashes.setdefault(db_user, pw_hash)
    except OSError:
        pass
    return hashes


def _discover_cpanel_emails(root: Path, homedir: Path, domains: list[str]) -> dict[str, list[dict]]:
    """Discover email accounts, password hashes ($6$), quotas, and Maildir folders from cPanel backup."""
    found: dict[str, list[dict]] = {}
    etc_dir = homedir / "etc"
    mail_dir = homedir / "mail"

    all_candidate_domains = list(domains)
    if etc_dir.is_dir():
        for d in sorted(etc_dir.iterdir()):
            if d.is_dir():
                norm_d = da_import._normalize_domain(d.name)
                if norm_d and norm_d not in all_candidate_domains:
                    all_candidate_domains.append(norm_d)

    for domain in all_candidate_domains:
        mailboxes: dict[str, dict] = {}
        quota_map: dict[str, int] = {}

        quota_file = etc_dir / domain / "quota"
        if quota_file.is_file():
            for raw in quota_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if ":" in line and not line.startswith("#"):
                    local, _, q_raw = line.partition(":")
                    try:
                        q_bytes = int(q_raw.strip())
                        quota_map[local.strip().lower()] = max(0, q_bytes // (1024 * 1024)) if q_bytes > 0 else 1024
                    except ValueError:
                        pass

        for fname in ("shadow", "passwd"):
            f_path = etc_dir / domain / fname
            if not f_path.is_file():
                continue
            for raw in f_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                parts = line.split(":")
                local = parts[0].strip().lower()
                secret = parts[1].strip() if len(parts) > 1 else ""
                if not local:
                    continue
                if local not in mailboxes:
                    mailboxes[local] = {
                        "local_part": local,
                        "domain": domain,
                        "address": f"{local}@{domain}",
                        "password_hash": secret if secret.startswith("$") else "",
                        "quota_mb": quota_map.get(local, 1024),
                        "maildir": None,
                    }
                elif secret.startswith("$") and not mailboxes[local]["password_hash"]:
                    mailboxes[local]["password_hash"] = secret

        dom_mail_dir = mail_dir / domain
        if dom_mail_dir.is_dir():
            for entry in sorted(dom_mail_dir.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                local = entry.name.strip().lower()
                if local not in mailboxes:
                    mailboxes[local] = {
                        "local_part": local,
                        "domain": domain,
                        "address": f"{local}@{domain}",
                        "password_hash": "",
                        "quota_mb": quota_map.get(local, 1024),
                        "maildir": entry,
                    }
                else:
                    mailboxes[local]["maildir"] = entry

        if mailboxes:
            found[domain] = list(mailboxes.values())

    return found


def _import_cpanel_emails(
    db,
    user: User,
    root: Path,
    homedir: Path,
    domains: list[str],
    credentials: list[str],
    item_summary: dict,
) -> list[str]:
    """Import discovered cPanel mailboxes, forwarders, and Maildir data into SPanel mail."""
    from app.services import mail as mail_service

    discovered = _discover_cpanel_emails(root, homedir, domains)
    if not discovered:
        return []

    mail_section: dict = {"domains": [], "mailboxes": [], "forwarders": []}
    maildir_lookup: dict[tuple[str, str], Optional[Path]] = {}

    for domain, boxes in discovered.items():
        mail_section["domains"].append({"domain": domain, "catch_all": "", "relay": ""})
        for box in boxes:
            local = box["local_part"]
            raw_hash = str(box.get("password_hash") or "").strip()
            if raw_hash.startswith("{SHA512-CRYPT}$6$"):
                final_hash = raw_hash
            elif raw_hash.startswith("$6$"):
                final_hash = f"{{SHA512-CRYPT}}{raw_hash}"
            else:
                gen_pass = secrets.token_urlsafe(14)
                final_hash = "{SHA512-CRYPT}" + sha512_crypt.using(rounds=5000).hash(gen_pass)
                credentials.append(f"mailbox address={local}@{domain} password={gen_pass}")

            if not mail_service.SHA512_RE.fullmatch(final_hash):
                gen_pass = secrets.token_urlsafe(14)
                final_hash = "{SHA512-CRYPT}" + sha512_crypt.using(rounds=5000).hash(gen_pass)
                credentials.append(f"mailbox address={local}@{domain} password={gen_pass}")

            mail_section["mailboxes"].append({
                "local_part": local,
                "domain": domain,
                "password_hash": final_hash,
                "quota_mb": int(box.get("quota_mb") or 1024),
                "enabled": True,
            })
            maildir_lookup[(domain, local)] = box.get("maildir")

        # Forwarders from va/<domain>
        va_file = root / "va" / domain
        if va_file.is_file():
            fwd_map: dict[str, list[str]] = {}
            for raw in va_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                lhs, _, rhs = line.partition(":")
                lhs = lhs.strip().lower()
                if "@" in lhs:
                    local_part, _, lhs_dom = lhs.partition("@")
                    if lhs_dom != domain:
                        continue
                else:
                    local_part = lhs
                for dest in rhs.split(","):
                    dest_clean = dest.strip()
                    if "@" in dest_clean and not dest_clean.startswith("|") and ":" not in dest_clean:
                        fwd_map.setdefault(local_part, []).append(dest_clean)
            for local_part, dests in fwd_map.items():
                if local_part and dests:
                    mail_section["forwarders"].append({
                        "local_part": local_part,
                        "domain": domain,
                        "destinations": dests,
                    })

    def stage_mail(domain: str, local: str) -> Optional[Path]:
        mdir = maildir_lookup.get((domain, local))
        if mdir and mdir.is_dir():
            return mdir
        return None

    try:
        restored = mail_service.restore_manifest(db, user, mail_section, stage_mail)
        mail_service.sync_quietly(db)
        item_summary.setdefault("mailboxes", []).extend(restored)
        return restored
    except Exception as exc:
        item_summary["warnings"].append(f"Email import warning: {exc}")
        return []


def _import_cpanel_certificate(db, website: Website, root: Path, homedir: Path, item_summary: dict) -> bool:
    """Import SSL certificate for domain from cPanel backup ssl/ directory if valid."""
    from app.services import ssl as ssl_service

    ssl_roots = [root / "ssl", homedir / "ssl"]
    cert_bytes = b""
    key_bytes = b""
    ca_bytes = b""

    domain_prefix = website.domain.replace(".", "_")
    for sroot in ssl_roots:
        certs_dir = sroot / "certs"
        keys_dir = sroot / "keys"
        if not certs_dir.is_dir() or not keys_dir.is_dir():
            continue
        matched_cert = None
        for cfile in sorted(certs_dir.iterdir()):
            if cfile.is_file() and (
                cfile.name.startswith(website.domain) or cfile.name.startswith(domain_prefix)
            ) and cfile.suffix in {".crt", ".cert", ".pem"}:
                matched_cert = cfile
                break
        if not matched_cert:
            continue
        # Find matching key
        matched_key = None
        for kfile in sorted(keys_dir.iterdir()):
            if kfile.is_file() and kfile.suffix in {".key", ".pem"}:
                if kfile.stem == matched_cert.stem or kfile.name.startswith(website.domain) or kfile.name.startswith(domain_prefix):
                    matched_key = kfile
                    break
        if matched_cert and matched_key:
            try:
                cert_bytes = matched_cert.read_bytes()
                key_bytes = matched_key.read_bytes()
                break
            except OSError:
                continue

    if not cert_bytes.strip() or not key_bytes.strip():
        return False

    aliases = [alias.domain for alias in (website.aliases or [])]
    try:
        written = ssl_service.install_manual_ssl(website.domain, cert_bytes, key_bytes, ca_bytes, aliases=aliases)
    except (ValueError, RuntimeError) as exc:
        _log(f"  {website.domain}: no usable cPanel SSL certificate ({exc})")
        return False

    website.ssl_enabled = True
    website.ssl_mode = "manual"
    website.ssl_cert_path = written["cert"]
    website.ssl_key_path = written["key"]
    website.ssl_ca_path = written["ca"]
    website.ssl_updated_at = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    db.commit()
    try:
        nginx.rewrite_vhost(
            website.domain,
            website.root_path,
            app_type=website.app_type or "wordpress",
            php_version=website.php_version,
            custom_directives=website.nginx_custom or "",
            waf_enabled=bool(website.waf_enabled),
            document_root=getattr(website, "document_root", "public_html") or "public_html",
            rewrite_mode=getattr(website, "nginx_rewrite_mode", "none") or "none",
            ssl_cert_path=written["cert"],
            ssl_key_path=written["key"],
            ssl_ca_path=written["ca"],
            preserve_existing_ssl=False,
            aliases=[a.domain for a in (website.aliases or []) if a.mode == "alias"],
            redirects=[a.domain for a in (website.aliases or []) if a.mode == "redirect"],
        )
    except Exception as exc:
        _log(f"  {website.domain}: cPanel SSL installed but vhost rewrite failed ({exc})")
        return False

    item_summary.setdefault("ssl_imported_domains", []).append(website.domain)
    return True


def scan_cpanel_backup(archive_path: str) -> dict:
    """Scan a cPanel backup archive (cpmove-*.tar.gz / backup-*.tar.gz) to preview users, domains, DBs, emails."""
    path = da_import.resolve_backup_path(archive_path)
    if not path.exists():
        raise FileNotFoundError(f"Backup not found: {path.name}")
    if not da_import._is_archive(path):
        raise ValueError("Not a supported archive format")

    result: dict = {
        "filename": path.name,
        "size": path.stat().st_size,
        "archive_path": str(path),
        "panel_type": "cpanel",
        "users": [],
        "errors": [],
    }

    da_import.STAGE_BASE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="spanel-cp-scan-", dir=str(da_import.STAGE_BASE)) as tmp:
        extracted = Path(tmp) / "extracted"
        try:
            da_import._safe_extract_tar(path, extracted)
        except Exception as exc:
            result["errors"].append(f"Extraction failed: {exc}")
            return result

        root = _find_cpanel_root(extracted)
        homedir = _ensure_homedir_extracted(root)
        username, email, fallback_main = _discover_cpanel_username_and_email(root, path.name)
        targets, aliases_by_domain = _discover_cpanel_domains_and_sources(root, homedir, fallback_main)
        sql_files = _discover_cpanel_sql_files(root)
        domain_names = [d for d, _ in targets]
        discovered_emails = _discover_cpanel_emails(root, homedir, domain_names)
        all_mailboxes = [box["address"] for boxes in discovered_emails.values() for box in boxes]

        user_entry: dict = {
            "username": username,
            "email": email,
            "domains": [],
            "databases": [],
            "mailboxes": all_mailboxes,
        }

        total_sites = len(targets)
        for domain, source in targets:
            app_type = da_import._detect_app_type(source)
            app_config = da_import._parse_app_db_config(source) if source else {}
            matched_key, matched_sql = da_import._matched_sql_for_config(app_config, sql_files, total_sites == 1)

            user_entry["domains"].append({
                "domain": domain,
                "app_type": app_type,
                "has_files": source is not None,
                "db_name": app_config.get("DB_NAME") or matched_key or "",
                "db_user": app_config.get("DB_USER") or "",
                "has_sql_dump": matched_sql is not None,
                "email_count": len(discovered_emails.get(domain, [])),
                "aliases": [
                    {"domain": name, "mode": mode}
                    for name, mode in aliases_by_domain.get(domain, [])
                ],
            })

        assigned_keys = {d["db_name"].lower() for d in user_entry["domains"] if d["db_name"]}
        for key, sql_path in sql_files.items():
            if key not in assigned_keys:
                user_entry["databases"].append({
                    "db_name": key,
                    "sql_file": str(sql_path),
                    "has_sql_dump": True,
                })

        result["users"].append(user_entry)

    return result


def import_cpanel_backup(
    archive_path: str,
    force: bool = False,
    *,
    import_websites: bool = True,
    import_databases: bool = True,
    import_emails: bool = True,
) -> dict:
    """Import a cPanel backup archive into SPanel."""
    path = da_import.resolve_backup_path(archive_path)
    if not path.exists():
        raise FileNotFoundError(f"Backup not found: {path.name}")
    if not da_import._is_archive(path):
        raise ValueError("Not a supported archive format")

    stamp = da_import._utc_stamp()
    da_import.STAGE_BASE.mkdir(parents=True, exist_ok=True)
    stage_dir = da_import.STAGE_BASE / f"cp-{stamp}-{secrets.token_hex(4)}"
    stage_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    credentials: list[str] = [
        "# SPanel cPanel import credentials",
        f"# Web import: {stamp}",
        f"# Archive: {path.name}",
        "# Keep this file private. It contains generated panel, database, and mailbox passwords.",
    ]

    try:
        extracted = stage_dir / "extracted"
        _log(f"Extracting cPanel archive {path.name} ...")
        da_import._safe_extract_tar(path, extracted)

        root = _find_cpanel_root(extracted)
        homedir = _ensure_homedir_extracted(root)
        username, email, fallback_main = _discover_cpanel_username_and_email(root, path.name)
        targets, aliases_by_domain = _discover_cpanel_domains_and_sources(root, homedir, fallback_main)
        all_domains = [d for d, _ in targets]
        sql_files = _discover_cpanel_sql_files(root)
        db_hashes = _discover_cpanel_db_hashes(root)

        item_summary: dict = {
            "archive": path.name,
            "username": username,
            "domains": all_domains,
            "imported_domains": [],
            "databases": [],
            "mailboxes": [],
            "aliases": [],
            "ssl_enabled_domains": [],
            "ssl_imported_domains": [],
            "warnings": [],
        }

        if not all_domains and not sql_files:
            item_summary["warnings"].append("No domains or databases found in cPanel backup")
            summary.append(item_summary)
            return {"summary": summary, "credentials": credentials, "errors": ["No domains found in cPanel backup"]}

        db = SessionLocal()
        try:
            if not force:
                conflicts = []
                if db.query(User).filter(User.username == username).first():
                    conflicts.append(f"panel user '{username}'")
                if import_websites:
                    for domain in all_domains:
                        if db.query(Website).filter(Website.domain == domain).first():
                            conflicts.append(f"website '{domain}'")
                if conflicts:
                    message = (
                        "Already exists: " + ", ".join(conflicts)
                        + ". Enable 'Replace existing users/websites' to overwrite."
                    )
                    item_summary["warnings"].append(message)
                    summary.append(item_summary)
                    return {"summary": summary, "credentials": credentials, "errors": [message]}

            if force:
                if import_websites:
                    for domain in all_domains:
                        da_import._delete_existing_domain(db, domain)
                da_import._delete_existing_user(db, username)

            existing_user = db.query(User).filter(User.username == username).first()
            if existing_user:
                user = existing_user
            else:
                user, _created = da_import._ensure_panel_user_record(db, username, email, all_domains, credentials)

            imported_sql_keys: set[str] = set()
            websites = []

            if import_websites:
                for domain, source in targets:
                    app_type = da_import._detect_app_type(source)
                    app_config = da_import._parse_app_db_config(source) if source else {}

                    _log(f"Importing cPanel domain {domain} for user {username} ...")
                    root_path = site_users.site_root_for_panel_user(user.username, domain)
                    linux_user = site_users.ensure_site_runtime(
                        domain, root_path,
                        da_import.DEFAULT_PHP_VERSION if app_type in {"wordpress", "php"} else None,
                        user.username,
                    )
                    if source is not None:
                        staged = stage_dir / "payload" / domain
                        da_import._copy_site_files(source, staged / "public_html")
                        site_users.import_site_files(root_path, linux_user, str(staged))

                    selected_rules = [rule["id"] for rule in waf.DEFAULT_RULES]
                    waf_enabled = True
                    waf_result = waf.sync_site_rules(domain, selected_rules, "", crs_mode="off")
                    if waf_result.returncode != 0:
                        waf_enabled = False

                    php_socket = site_users.site_php_fpm_socket(
                        linux_user, root_path,
                        da_import.DEFAULT_PHP_VERSION if app_type in {"wordpress", "php"} else None,
                    )
                    rewrite_mode = "front_controller" if app_type == "wordpress" else "none"
                    pointers = aliases_by_domain.get(domain, [])
                    try:
                        nginx.write_vhost(
                            domain, root_path,
                            app_type=app_type,
                            php_version=da_import.DEFAULT_PHP_VERSION if app_type in {"wordpress", "php"} else None,
                            php_fpm_socket_override=php_socket,
                            waf_enabled=waf_enabled,
                            rewrite_mode=rewrite_mode,
                        )
                    except RuntimeError:
                        if not waf_enabled:
                            raise
                        waf_enabled = False
                        nginx.write_vhost(
                            domain, root_path,
                            app_type=app_type,
                            php_version=da_import.DEFAULT_PHP_VERSION if app_type in {"wordpress", "php"} else None,
                            php_fpm_socket_override=php_socket,
                            waf_enabled=False,
                            rewrite_mode=rewrite_mode,
                        )

                    website = db.query(Website).filter(Website.domain == domain).first()
                    if website is None:
                        website = Website(
                            domain=domain,
                            owner_id=user.id,
                            root_path=root_path,
                            linux_user=linux_user,
                            php_version=da_import.DEFAULT_PHP_VERSION,
                            app_type=app_type,
                            status="active",
                            waf_enabled=waf_enabled,
                            nginx_rewrite_mode=rewrite_mode,
                        )
                        db.add(website)
                    else:
                        website.owner_id = user.id
                        website.root_path = root_path
                        website.linux_user = linux_user
                        website.php_version = da_import.DEFAULT_PHP_VERSION
                        website.app_type = app_type
                        website.waf_enabled = waf_enabled
                        website.nginx_rewrite_mode = rewrite_mode
                    db.commit()
                    db.refresh(website)

                    applied_pointers = da_import._sync_website_aliases(db, website, pointers, item_summary)
                    if applied_pointers:
                        alias_domains = [name for name, mode in applied_pointers if mode == "alias"]
                        redirect_domains = [name for name, mode in applied_pointers if mode == "redirect"]
                        try:
                            nginx.rewrite_vhost(
                                domain, root_path,
                                app_type=app_type,
                                php_version=da_import.DEFAULT_PHP_VERSION if app_type in {"wordpress", "php"} else None,
                                php_fpm_socket_override=php_socket,
                                waf_enabled=waf_enabled,
                                rewrite_mode=rewrite_mode,
                                aliases=alias_domains,
                                redirects=redirect_domains,
                            )
                            item_summary.setdefault("aliases", []).extend(
                                f"{name} ({mode})" for name, mode in applied_pointers
                            )
                        except RuntimeError as exc:
                            item_summary["warnings"].append(f"Parked domains for {domain} not applied to Nginx: {exc}")

                    site_users.fix_site_permissions(root_path, linux_user)
                    websites.append(website)
                    item_summary["imported_domains"].append(domain)

                    if import_databases:
                        public = site_users.document_root(website.root_path)
                        app_config = app_config or da_import._parse_app_db_config(public)
                        matched_key, matched_sql = da_import._matched_sql_for_config(
                            app_config, sql_files, len(targets) == 1
                        )
                        if matched_sql:
                            cfg_user = (app_config.get("DB_USER") or matched_key).lower()
                            cp_creds = {"password": "", "password_hash": db_hashes.get(cfg_user, "")}
                            db_name, db_user, db_password, reused = da_import._create_panel_database(
                                db, user, website,
                                app_config.get("DB_NAME") or matched_key,
                                app_config.get("DB_USER"),
                                matched_sql, credentials,
                                app_config=app_config, da_credentials=cp_creds,
                            )
                            imported_sql_keys.add(matched_key)
                            if not reused:
                                da_import._update_app_db_config(public, db_name, db_user, db_password)
                            site_users.fix_site_permissions(website.root_path, website.linux_user)
                            item_summary["databases"].append({
                                "domain": domain, "source": str(matched_sql), "db_name": db_name,
                            })

            if import_databases:
                for key, sql_path in sql_files.items():
                    if key in imported_sql_keys:
                        continue
                    if db.query(DatabaseAccount).filter(DatabaseAccount.db_name == key).first() and not force:
                        continue
                    _log(f"Importing cPanel database {key} for user {username} ...")
                    cp_creds = {"password": "", "password_hash": db_hashes.get(key.lower(), "")}
                    db_name, db_user, _db_password, _reused = da_import._create_panel_database(
                        db, user, None, key, key, sql_path, credentials,
                        da_credentials=cp_creds,
                    )
                    item_summary["databases"].append({
                        "domain": None, "source": str(sql_path), "db_name": db_name, "db_user": db_user,
                    })

            if import_emails:
                _import_cpanel_emails(db, user, root, homedir, all_domains, credentials, item_summary)

            for website in websites:
                da_import._enable_ssl_when_dns_matches(db, website, item_summary)
                if not website.ssl_enabled:
                    _import_cpanel_certificate(db, website, root, homedir, item_summary)

            summary.append(item_summary)
        finally:
            db.close()
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)

    from app.services import dns

    with SessionLocal() as dns_db:
        dns.sync_quietly(dns_db)

    errors = [w for item in summary for w in item.get("warnings", []) if "failed" in w.lower() or "error" in w.lower()]
    return {
        "summary": summary,
        "credentials": credentials,
        "errors": errors,
    }


def restore_to_user(
    archive_path: str,
    user_id: int,
    import_websites: bool = True,
    import_databases: bool = True,
    import_emails: bool = True,
    force: bool = True,
) -> dict:
    """Entry point used by Unified Migration to restore a cPanel backup."""
    return import_cpanel_backup(
        archive_path,
        force=force,
        import_websites=import_websites,
        import_databases=import_databases,
        import_emails=import_emails,
    )
