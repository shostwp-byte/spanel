"""Import Plesk backup archives (.tar, .tar.gz, .tgz, .tar.zst, .zip) into SPanel.

Supports Plesk Backup Manager archives and Plesk domain/subscription dumps:
- Parses XML dump descriptors (*_info_*.xml, backup_info_*.xml, dump.xml)
- Discovers domains from ``domains/<domain>/``, ``vhosts/<domain>/``, ``sites/<domain>/``, and XML manifests
- Unpacks nested Plesk ``backup_user-data_*`` / ``backup_httpdocs_*`` archives when present
- Restores MariaDB databases and auto-updates ``wp-config.php``, ``.env``, ``configuration.php``
- Restores email accounts and Maildir folders into SPanel's mail service
- Restores SSL certificates or issues Let's Encrypt when DNS matches
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
import secrets
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Optional

from passlib.hash import sha512_crypt

from app.core.database import SessionLocal
from app.models.entities import DatabaseAccount, User, Website
from app.services import da_import, nginx, site_users, waf

logger = logging.getLogger("spanel.plesk_import")


def _log(msg: str) -> None:
    logger.info(msg)
    print(msg, flush=True)


def _safe_extract_archive(archive: Path, destination: Path) -> None:
    """Safely extract .zip or .tar* archives into destination."""
    destination.mkdir(parents=True, exist_ok=True)
    dest_resolved = destination.resolve()
    if archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zf:
            for member in zf.infolist():
                target = (destination / member.filename).resolve()
                try:
                    target.relative_to(dest_resolved)
                except ValueError:
                    raise RuntimeError(f"Unsafe path in {archive.name}: {member.filename}")
            zf.extractall(destination)
        return
    da_import._safe_extract_tar(archive, destination)


def _discover_plesk_metadata(extracted: Path, archive_name: str) -> dict:
    """Inspect extracted Plesk archive and return normalized metadata."""
    raw_user = ""
    email = ""
    domains_map: dict[str, Optional[Path]] = {}
    aliases_by_domain: dict[str, list[tuple[str, str]]] = {}
    emails_by_domain: dict[str, list[dict]] = {}

    # 1. Inspect XML dump descriptors if present
    for xml_file in sorted(extracted.rglob("*.xml")):
        try:
            if xml_file.stat().st_size > 20 * 1024 * 1024:
                continue
            tree = ET.parse(xml_file)
            root_el = tree.getroot()
        except Exception:
            continue

        for el in root_el.iter():
            tag = el.tag.split("}")[-1].lower()
            if tag in {"client", "customer", "user", "subscription", "webspace"}:
                login = (el.attrib.get("login") or el.attrib.get("name") or "").strip().lower()
                if login and not raw_user and re.match(r"^[a-z0-9][a-z0-9._-]{1,30}$", login):
                    raw_user = login
                mail_attr = (el.attrib.get("email") or el.attrib.get("mail") or "").strip()
                if mail_attr and "@" in mail_attr and not email:
                    email = mail_attr
            elif tag == "domain":
                dname = da_import._normalize_domain(el.attrib.get("name") or el.attrib.get("ascii-name") or "")
                if dname:
                    domains_map.setdefault(dname, None)
            elif tag in {"domain-alias", "alias"}:
                aname = da_import._normalize_domain(el.attrib.get("name") or el.attrib.get("ascii-name") or "")
                parent_dom = da_import._normalize_domain(el.attrib.get("domain") or "")
                if aname and parent_dom and aname != parent_dom:
                    aliases_by_domain.setdefault(parent_dom, []).append((aname, "alias"))

    # 2. Discover domains under vhosts/<domain>, domains/<domain>, or sites/<domain>
    for container_name in ("vhosts", "domains", "sites"):
        for container in sorted(extracted.rglob(container_name)):
            if not container.is_dir():
                continue
            for dom_dir in sorted(container.iterdir()):
                if not dom_dir.is_dir():
                    continue
                dname = da_import._normalize_domain(dom_dir.name)
                if not dname:
                    continue
                docroot_src = domains_map.get(dname)
                if docroot_src is None:
                    for sub_cand in ("httpdocs", "public_html", "htdocs", "www"):
                        cand_dir = dom_dir / sub_cand
                        if cand_dir.is_dir():
                            docroot_src = cand_dir
                            break
                if docroot_src is None:
                    # Check nested Plesk user-data / httpdocs tarballs inside dom_dir
                    for item in sorted(dom_dir.iterdir()):
                        if item.is_file() and da_import._is_archive(item) and any(
                            k in item.name.lower() for k in ("user-data", "httpdocs", "vhost", "web")
                        ):
                            stage_doc = dom_dir / "_spanel_httpdocs"
                            try:
                                _safe_extract_archive(item, stage_doc)
                                if (stage_doc / "httpdocs").is_dir():
                                    docroot_src = stage_doc / "httpdocs"
                                elif (stage_doc / "public_html").is_dir():
                                    docroot_src = stage_doc / "public_html"
                                else:
                                    docroot_src = stage_doc
                                break
                            except Exception as exc:
                                _log(f"  WARNING: failed to extract Plesk nested archive {item.name}: {exc}")
                if docroot_src is None:
                    docroot_src = dom_dir
                domains_map[dname] = docroot_src

                # Discover subdomains inside dom_dir/subdomains/<sub>
                subs_dir = dom_dir / "subdomains"
                if subs_dir.is_dir():
                    for sdir in sorted(subs_dir.iterdir()):
                        if not sdir.is_dir():
                            continue
                        sub_fqdn = da_import._normalize_domain(f"{sdir.name}.{dname}")
                        if not sub_fqdn:
                            continue
                        sub_doc = sdir / "httpdocs" if (sdir / "httpdocs").is_dir() else sdir
                        domains_map.setdefault(sub_fqdn, sub_doc)

                # Discover mailboxes under dom_dir/mailnames/<local> or dom_dir/mail/<local>
                for mfolder in ("mailnames", "mail"):
                    mdir = dom_dir / mfolder
                    if not mdir.is_dir():
                        continue
                    boxes: dict[str, dict] = {}
                    for local_dir in sorted(mdir.iterdir()):
                        if not local_dir.is_dir() or local_dir.name.startswith("."):
                            continue
                        local = local_dir.name.strip().lower()
                        if not re.match(r"^[a-z0-9._+-]+$", local):
                            continue
                        maildir = local_dir / "Maildir" if (local_dir / "Maildir").is_dir() else local_dir
                        boxes[local] = {
                            "local_part": local,
                            "domain": dname,
                            "address": f"{local}@{dname}",
                            "password_hash": "",
                            "quota_mb": 1024,
                            "maildir": maildir,
                        }
                    if boxes:
                        emails_by_domain[dname] = list(boxes.values())

    # 3. Discover SQL dumps anywhere in the extracted tree
    sql_files: dict[str, Path] = {}
    for path in sorted(extracted.rglob("*")):
        if not path.is_file():
            continue
        if any(path.name.lower().endswith(s) for s in da_import.SQL_SUFFIXES):
            part_names = {p.lower() for p in path.parts}
            if "httpdocs" in part_names or "public_html" in part_names:
                continue
            base = da_import._sql_base_name(path).lower()
            if base in {"mysql", "psa", "apsc", "roundcubemail", "horde", "phpmyadmin", "information_schema"}:
                continue
            sql_files.setdefault(base, path)

    username = da_import._normalize_username(
        raw_user or (next(iter(domains_map.keys())).split(".")[0] if domains_map else da_import._archive_username(archive_name)),
        archive_name,
    )
    return {
        "username": username,
        "email": email,
        "domains": list(domains_map.items()),
        "aliases_by_domain": aliases_by_domain,
        "emails_by_domain": emails_by_domain,
        "sql_files": sql_files,
    }


def _import_plesk_emails(
    db,
    user: User,
    emails_by_domain: dict[str, list[dict]],
    credentials: list[str],
    item_summary: dict,
) -> list[str]:
    """Import discovered Plesk mailboxes and Maildir data into SPanel mail."""
    from app.services import mail as mail_service

    if not emails_by_domain:
        return []

    mail_section: dict = {"domains": [], "mailboxes": [], "forwarders": []}
    maildir_lookup: dict[tuple[str, str], Optional[Path]] = {}

    for domain, boxes in emails_by_domain.items():
        mail_section["domains"].append({"domain": domain, "catch_all": "", "relay": ""})
        for box in boxes:
            local = box["local_part"]
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


def scan_plesk_backup(archive_path: str) -> dict:
    """Scan a Plesk backup archive (.tar, .tar.gz, .zip) to preview users, domains, DBs, emails."""
    path = da_import.resolve_backup_path(archive_path)
    if not path.exists():
        raise FileNotFoundError(f"Backup not found: {path.name}")
    if not da_import._is_archive(path):
        raise ValueError("Not a supported archive format")

    result: dict = {
        "filename": path.name,
        "size": path.stat().st_size,
        "archive_path": str(path),
        "panel_type": "plesk",
        "users": [],
        "errors": [],
    }

    da_import.STAGE_BASE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="spanel-plesk-scan-", dir=str(da_import.STAGE_BASE)) as tmp:
        extracted = Path(tmp) / "extracted"
        try:
            _safe_extract_archive(path, extracted)
        except Exception as exc:
            result["errors"].append(f"Extraction failed: {exc}")
            return result

        meta = _discover_plesk_metadata(extracted, path.name)
        targets = meta["domains"]
        sql_files = meta["sql_files"]
        emails_by_domain = meta["emails_by_domain"]
        aliases_by_domain = meta["aliases_by_domain"]
        all_mailboxes = [box["address"] for boxes in emails_by_domain.values() for box in boxes]

        user_entry: dict = {
            "username": meta["username"],
            "email": meta["email"],
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
                "email_count": len(emails_by_domain.get(domain, [])),
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


def import_plesk_backup(
    archive_path: str,
    force: bool = False,
    *,
    import_websites: bool = True,
    import_databases: bool = True,
    import_emails: bool = True,
) -> dict:
    """Import a Plesk backup archive into SPanel."""
    path = da_import.resolve_backup_path(archive_path)
    if not path.exists():
        raise FileNotFoundError(f"Backup not found: {path.name}")
    if not da_import._is_archive(path):
        raise ValueError("Not a supported archive format")

    stamp = da_import._utc_stamp()
    da_import.STAGE_BASE.mkdir(parents=True, exist_ok=True)
    stage_dir = da_import.STAGE_BASE / f"plesk-{stamp}-{secrets.token_hex(4)}"
    stage_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    credentials: list[str] = [
        "# SPanel Plesk import credentials",
        f"# Web import: {stamp}",
        f"# Archive: {path.name}",
        "# Keep this file private. It contains generated panel, database, and mailbox passwords.",
    ]

    try:
        extracted = stage_dir / "extracted"
        _log(f"Extracting Plesk archive {path.name} ...")
        _safe_extract_archive(path, extracted)

        meta = _discover_plesk_metadata(extracted, path.name)
        username = meta["username"]
        email = meta["email"]
        targets = meta["domains"]
        all_domains = [d for d, _ in targets]
        sql_files = meta["sql_files"]
        aliases_by_domain = meta["aliases_by_domain"]
        emails_by_domain = meta["emails_by_domain"]

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
            item_summary["warnings"].append("No domains or databases found in Plesk backup")
            summary.append(item_summary)
            return {"summary": summary, "credentials": credentials, "errors": ["No domains found in Plesk backup"]}

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

                    _log(f"Importing Plesk domain {domain} for user {username} ...")
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
                            item_summary["warnings"].append(f"Aliases for {domain} not applied to Nginx: {exc}")

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
                            db_name, db_user, db_password, reused = da_import._create_panel_database(
                                db, user, website,
                                app_config.get("DB_NAME") or matched_key,
                                app_config.get("DB_USER"),
                                matched_sql, credentials,
                                app_config=app_config,
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
                    _log(f"Importing Plesk database {key} for user {username} ...")
                    db_name, db_user, _db_password, _reused = da_import._create_panel_database(
                        db, user, None, key, key, sql_path, credentials,
                    )
                    item_summary["databases"].append({
                        "domain": None, "source": str(sql_path), "db_name": db_name, "db_user": db_user,
                    })

            if import_emails:
                _import_plesk_emails(db, user, emails_by_domain, credentials, item_summary)

            for website in websites:
                da_import._enable_ssl_when_dns_matches(db, website, item_summary)

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
    """Entry point used by Unified Migration to restore a Plesk backup."""
    return import_plesk_backup(
        archive_path,
        force=force,
        import_websites=import_websites,
        import_databases=import_databases,
        import_emails=import_emails,
    )
