"""spanel_mailtool: what spanel-helper's Email commands render and check.

Installed root-owned at /usr/local/lib/spanel/spanel_mailtool.py (install.sh,
update.sh) and run by the helper as `python3 -I <this file> <command>`, or
imported by the helper's own inline Python. Everything here that turns panel
input into a file another program reads - a Sieve script, an nginx server
block, Exim's SNI maps - checks that input again first: the panel's checks are
for its messages, these are the boundary.

Pure functions apart from the command line at the bottom, so tests import this
file directly (tests/test_mail_*.py). Nothing at import time needs root, Linux
or a mail server.
"""

from __future__ import annotations

import json
import re
import sys

DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
LOCAL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
DATE_RE = re.compile(r"^(?:19|20|21)[0-9]{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])$")

VACATION_SUBJECT_MAX = 255
VACATION_BODY_MAX = 8000
VACATION_DAYS = (1, 30)


class ToolError(ValueError):
    """Input the helper refuses; the message ends up after "spanel-helper: "."""


def _address(value) -> tuple[str, str]:
    address = str(value or "")
    local, _, domain = address.partition("@")
    if not LOCAL_RE.fullmatch(local) or ".." in local or not DOMAIN_RE.fullmatch(domain):
        raise ToolError(f"invalid mailbox address: {address!r}")
    return local, domain


def _no_controls(text: str, multiline: bool) -> bool:
    for char in text:
        code = ord(char)
        if char == "\n" and multiline:
            continue
        if char == "\t":
            continue
        if code < 32 or code == 127 or 0x80 <= code < 0xA0 or char in "  ":
            return False
    return True


def _date(value, what: str) -> str:
    text = str(value or "")
    if text and not DATE_RE.fullmatch(text):
        raise ToolError(f"invalid {what} date for an autoresponder: {text!r}")
    return text


# --- autoresponders: a Sieve vacation in the mailbox ------------------------------------

def sieve_quote(text: str) -> str:
    """A Sieve quoted string (RFC 5228 2.4.2): backslash and double quote are
    the only characters escaped; a line break may stand in it as itself."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def check_vacation(item) -> dict:
    """One autoresponder as mail-sync receives it, checked. Raises ToolError."""
    if not isinstance(item, dict):
        raise ToolError("an autoresponder must be an object")
    local, domain = _address(item.get("address"))
    subject = str(item.get("subject") or "")
    body = str(item.get("body") or "").replace("\r\n", "\n")
    if not subject.strip() or len(subject) > VACATION_SUBJECT_MAX or not _no_controls(subject, multiline=False):
        raise ToolError(f"invalid autoresponder subject for {local}@{domain}")
    if not body.strip() or len(body) > VACATION_BODY_MAX or not _no_controls(body, multiline=True):
        raise ToolError(f"invalid autoresponder message for {local}@{domain}")
    start = _date(item.get("start"), "start")
    end = _date(item.get("end"), "end")
    if start and end and end < start:
        raise ToolError(f"the autoresponder of {local}@{domain} ends before it starts")
    days = item.get("days", 1)
    if not isinstance(days, int) or isinstance(days, bool) or not VACATION_DAYS[0] <= days <= VACATION_DAYS[1]:
        raise ToolError(f"invalid reply interval for {local}@{domain}")
    return {"address": f"{local}@{domain}", "subject": subject, "body": body, "start": start, "end": end,
            "days": days}


def render_vacation(item) -> str:
    """The Sieve script that answers mail for one mailbox (Pigeonhole's vacation
    extension). Dovecot runs it before the mailbox's own scripts as
    ~/spanel-vacation.sieve. Vacation itself never answers mailing lists, bulk
    or automatic mail and remembers whom it answered; mail the spam filter
    marked is not answered either."""
    item = check_vacation(item)
    tests = ['not header :contains "X-Spam-Status" "Yes"']
    if item["start"]:
        tests.append(f'currentdate :value "ge" "date" {sieve_quote(item["start"])}')
    if item["end"]:
        tests.append(f'currentdate :value "le" "date" {sieve_quote(item["end"])}')
    condition = tests[0] if len(tests) == 1 else "allof (" + ",\n        ".join(tests) + ")"
    lines = [
        f"# Managed by SPanel (Email addon): the autoresponder of {item['address']}.",
        "# Change it in the panel; this file is rewritten on every change.",
        'require ["vacation", "date", "relational"];',
        f"if {condition} {{",
        f"  vacation :days {item['days']} :addresses [{sieve_quote(item['address'])}]",
        f"    :subject {sieve_quote(item['subject'])}",
        f"    {sieve_quote(item['body'])};",
        "}",
    ]
    return "\n".join(lines) + "\n"


def vacation_scripts(items) -> dict[str, str]:
    """{address: script} for mail-sync's list; one autoresponder an address."""
    if not isinstance(items, list) or len(items) > 100000:
        raise ToolError("autoresponders must be a list")
    scripts: dict[str, str] = {}
    for item in items:
        script = render_vacation(item)
        address = check_vacation(item)["address"]
        if address in scripts:
            raise ToolError(f"two autoresponders for {address}")
        scripts[address] = script
    return scripts


# --- DKIM: keys by selector -------------------------------------------------------------------

SELECTOR_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
LEGACY_SELECTOR = "spanel"


def check_selector(value) -> str:
    selector = str(value or "")
    if not SELECTOR_RE.fullmatch(selector):
        raise ToolError(f"invalid DKIM selector: {selector!r}")
    return selector


def dkim_key_name(domain: str, selector: str) -> str:
    """A key's file under /etc/exim4/spanel/dkim, less ".pem". The "spanel"
    key is <domain>.pem, where every domain's key was before rotation; any
    other selector has a directory, so no domain name can be read as another
    domain's selector."""
    if not DOMAIN_RE.fullmatch(str(domain or "")):
        raise ToolError(f"invalid domain: {domain!r}")
    selector = check_selector(selector)
    return domain if selector == LEGACY_SELECTOR else f"{selector}/{domain}"


def dkim_maps(selectors: dict) -> tuple[list[str], list[str]]:
    """Exim's dkim_domains (domain -> key file) and dkim_selectors (domain ->
    selector) lines: the two always change together, so a switch of keys is
    one mail-sync."""
    domains, chosen = [], []
    for domain in sorted(selectors):
        selector = check_selector(selectors[domain])
        domains.append(f"{domain}: {dkim_key_name(domain, selector)}")
        chosen.append(f"{domain}: {selector}")
    return domains, chosen


# --- sending limits and the hold ------------------------------------------------------------------
# With auto-hold on, a mailbox or website account over its hourly limit does
# not get its mail refused: the message is frozen in the queue (logged as
# "SPanel hold"), for an administrator to release or delete from the panel's
# queue page. Past twice the limit it is refused all the same, so a runaway
# script cannot fill the spool.

def _limit(value, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100000:
        raise ToolError(f"invalid {what}")
    return value


def render_rate_acls(auth_rate: int, local_rate: int, domain_rate: int, hold: bool) -> dict[str, str]:
    """Exim ACL statements for the sending limits: "rcpt" goes in
    acl_check_rcpt (signed-in mailboxes), "data" at the top of acl_check_data
    (where a held message is frozen) and "not_smtp" in acl_check_not_smtp
    (PHP's sendmail, per Linux user)."""
    auth_rate = _limit(auth_rate, "mailbox sending limit")
    local_rate = _limit(local_rate, "website sending limit")
    domain_rate = _limit(domain_rate, "domain sending limit")
    rcpt, data, not_smtp = "", "", ""
    if auth_rate and hold:
        rcpt += (
            f"\n  deny    message = Sending limit reached: at most {auth_rate} recipients an hour for each mailbox\n"
            "          authenticated = *\n"
            f"          ratelimit = {2 * auth_rate} / 1h / per_rcpt / readonly / $authenticated_id\n"
            "  warn    authenticated = *\n"
            f"          ratelimit = {auth_rate} / 1h / per_rcpt / $authenticated_id\n"
            f"          set acl_m_hold = $authenticated_id is over its limit of {auth_rate} recipients an hour\n"
        )
        data = (
            "  # Over its sending limit: kept in the queue for an administrator.\n"
            "  warn    condition = ${if def:acl_m_hold}\n"
            "          control = freeze/no_tell\n"
            "          logwrite = SPanel hold: $acl_m_hold\n"
        )
    elif auth_rate:
        rcpt += (
            f"\n  deny    message = Sending limit reached: at most {auth_rate} recipients an hour for each mailbox\n"
            "          authenticated = *\n"
            f"          ratelimit = {auth_rate} / 1h / per_rcpt / $authenticated_id\n"
        )
    if domain_rate:
        rcpt += (
            "\n  deny    message = Domain sending limit reached for ${lc:$sender_address_domain}\n"
            "          authenticated = *\n"
            "          ratelimit = ${lookup{${lc:$sender_address_domain}}lsearch*{/etc/exim4/spanel_ratelimits}"
            f"{{$value}}{{{domain_rate}}}}} / 1h / per_rcpt / dom:${{lc:$sender_address_domain}}\n"
        )
    if local_rate and hold:
        not_smtp = (
            f"  deny    message = Sending limit reached: at most {local_rate} messages an hour\n"
            "          !condition = ${if eq{$sender_ident}{root}}\n"
            f"          ratelimit = {2 * local_rate} / 1h / per_mail / readonly / $sender_ident\n"
            "  warn    !condition = ${if eq{$sender_ident}{root}}\n"
            f"          ratelimit = {local_rate} / 1h / per_mail / $sender_ident\n"
            "          control = freeze/no_tell\n"
            f"          logwrite = SPanel hold: $sender_ident is over its limit of {local_rate} messages an hour\n"
        )
    elif local_rate:
        not_smtp = (
            f"  deny    message = Sending limit reached: at most {local_rate} messages an hour\n"
            "          !condition = ${if eq{$sender_ident}{root}}\n"
            f"          ratelimit = {local_rate} / 1h / per_mail / $sender_ident\n"
        )
    return {"rcpt": rcpt, "data": data, "not_smtp": not_smtp}


# --- mail hosts: mail.<domain> and friends, with their own certificate --------------------
# One Let's Encrypt certificate per mail domain, named after mail.<domain>,
# covers the hosts the panel turned on. nginx serves them on 80/443 (the ACME
# challenge, then whatever the host is for); Exim and Dovecot present the
# certificate to a client that asks for mail.<domain> by SNI.

MAIL_HOST_LABELS = ("mail", "mta-sts", "autoconfig", "autodiscover")
ACME_ROOT = "/var/www/spanel-acme"
LE_LIVE = "/etc/letsencrypt/live"
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")


def check_mail_hosts(data) -> dict:
    """What mail-hosts-apply receives, checked: the domain, the hosts to
    serve (labels of MAIL_HOST_LABELS, "mail" always among them) and the
    address Let's Encrypt writes to, if any."""
    if not isinstance(data, dict):
        raise ToolError("mail-hosts-apply expects a JSON object")
    domain = str(data.get("domain") or "")
    if not DOMAIN_RE.fullmatch(domain) or len(domain) > 200:
        raise ToolError(f"invalid domain: {domain!r}")
    labels = data.get("hosts")
    if not isinstance(labels, list) or not labels:
        raise ToolError("name the mail hosts to serve")
    clean = []
    for label in labels:
        if label not in MAIL_HOST_LABELS:
            raise ToolError(f"not a mail host: {label!r}")
        if label not in clean:
            clean.append(label)
    if "mail" not in clean:
        raise ToolError("mail.<domain> is always one of the mail hosts")
    email = str(data.get("email") or "")
    if email and not EMAIL_RE.fullmatch(email):
        raise ToolError(f"invalid email: {email!r}")
    ordered = [label for label in MAIL_HOST_LABELS if label in clean]
    mta_sts = data.get("mta_sts")
    if mta_sts is not None:
        if not isinstance(mta_sts, dict) or mta_sts.get("mode") not in MTA_STS_MODES:
            raise ToolError("the MTA-STS mode is testing, enforce or none")
        max_age = mta_sts.get("max_age", MTA_STS_MAX_AGE)
        if not isinstance(max_age, int) or isinstance(max_age, bool) or not 86400 <= max_age <= 31557600:
            raise ToolError("the MTA-STS max_age is 86400 to 31557600 seconds")
        # The policy names the MX the panel publishes, never anything sent in.
        mta_sts = {"mode": mta_sts["mode"], "mx": [f"mail.{domain}"], "max_age": max_age}
    return {"domain": domain, "hosts": ordered, "names": [f"{label}.{domain}" for label in ordered],
            "cert_name": f"mail.{domain}", "email": email, "mta_sts": mta_sts}


# --- what the mail hosts serve: MTA-STS, Thunderbird autoconfig, Outlook autodiscover ----------

MTA_STS_MODES = ("testing", "enforce", "none")
MTA_STS_MAX_AGE = 604800
MAIL_PORTS = {"imap": 993, "pop3": 995, "smtps": 465, "submission": 587}


def render_mta_sts_policy(mode: str, mx: list[str], max_age: int = MTA_STS_MAX_AGE) -> str:
    """/.well-known/mta-sts.txt (RFC 8461 3.2): CRLF-separated key: value lines."""
    if mode not in MTA_STS_MODES:
        raise ToolError("the MTA-STS mode is testing, enforce or none")
    if not mx or any(not DOMAIN_RE.fullmatch(host) for host in mx):
        raise ToolError("an MTA-STS policy names at least one MX host")
    if not isinstance(max_age, int) or not 86400 <= max_age <= 31557600:
        raise ToolError("the MTA-STS max_age is 86400 to 31557600 seconds")
    lines = ["version: STSv1", f"mode: {mode}", *[f"mx: {host}" for host in mx], f"max_age: {max_age}"]
    return "".join(line + "\r\n" for line in lines)


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_autoconfig(domain: str, host: str) -> str:
    """Thunderbird's autoconfig document (config-v1.1.xml): IMAP, POP3 and
    SMTP on host, signed in with the full address."""
    for name in (domain, host):
        if not DOMAIN_RE.fullmatch(name or ""):
            raise ToolError(f"invalid name: {name!r}")
    d, h = _xml(domain), _xml(host)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<clientConfig version="1.1">
  <emailProvider id="{d}">
    <domain>{d}</domain>
    <displayName>{d}</displayName>
    <displayShortName>{d}</displayShortName>
    <incomingServer type="imap">
      <hostname>{h}</hostname>
      <port>{MAIL_PORTS['imap']}</port>
      <socketType>SSL</socketType>
      <username>%EMAILADDRESS%</username>
      <authentication>password-cleartext</authentication>
    </incomingServer>
    <incomingServer type="pop3">
      <hostname>{h}</hostname>
      <port>{MAIL_PORTS['pop3']}</port>
      <socketType>SSL</socketType>
      <username>%EMAILADDRESS%</username>
      <authentication>password-cleartext</authentication>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>{h}</hostname>
      <port>{MAIL_PORTS['smtps']}</port>
      <socketType>SSL</socketType>
      <username>%EMAILADDRESS%</username>
      <authentication>password-cleartext</authentication>
    </outgoingServer>
    <outgoingServer type="smtp">
      <hostname>{h}</hostname>
      <port>{MAIL_PORTS['submission']}</port>
      <socketType>STARTTLS</socketType>
      <username>%EMAILADDRESS%</username>
      <authentication>password-cleartext</authentication>
    </outgoingServer>
  </emailProvider>
</clientConfig>
"""


def render_autodiscover(domain: str, host: str) -> str:
    """Outlook's POX autodiscover answer: IMAP and SMTP on host over SSL.
    The same for every address of the domain (nginx serves it as a file)."""
    for name in (domain, host):
        if not DOMAIN_RE.fullmatch(name or ""):
            raise ToolError(f"invalid name: {name!r}")
    h = _xml(host)

    def protocol(kind: str, port: int, extra: str = "") -> str:
        return (f"      <Protocol>\n        <Type>{kind}</Type>\n        <Server>{h}</Server>\n        <Port>{port}</Port>\n"
                f"        <DomainRequired>off</DomainRequired>\n        <SPA>off</SPA>\n        <SSL>on</SSL>\n"
                f"        <AuthRequired>on</AuthRequired>\n{extra}      </Protocol>\n")

    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<Autodiscover xmlns="http://schemas.microsoft.com/exchange/autodiscover/responseschema/2006">\n'
            '  <Response xmlns="http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a">\n'
            '    <Account>\n'
            '      <AccountType>email</AccountType>\n'
            '      <Action>settings</Action>\n'
            + protocol("IMAP", MAIL_PORTS["imap"])
            + protocol("SMTP", MAIL_PORTS["smtps"], "        <UsePOPAuth>on</UsePOPAuth>\n        <SMTPLast>off</SMTPLast>\n")
            + '    </Account>\n  </Response>\n</Autodiscover>\n')


def mailhost_documents(item: dict) -> dict[str, str]:
    """The files a domain's mail hosts serve, by file name: only those of the
    hosts that are on (and the MTA-STS policy only when there is one)."""
    host = f"mail.{item['domain']}"
    files = {}
    if "mta-sts" in item["hosts"] and item.get("mta_sts"):
        policy = item["mta_sts"]
        files["mta-sts.txt"] = render_mta_sts_policy(policy["mode"], policy["mx"], policy["max_age"])
    if "autoconfig" in item["hosts"]:
        files["config-v1.1.xml"] = render_autoconfig(item["domain"], host)
    if "autodiscover" in item["hosts"]:
        files["autodiscover.xml"] = render_autodiscover(item["domain"], host)
    return files


def write_mailhost_documents(docs_dir: str, item: dict) -> list[str]:
    """Put the documents in docs_dir (root's) and take out those no longer served."""
    import os

    files = mailhost_documents(item)
    for name in ("mta-sts.txt", "config-v1.1.xml", "autodiscover.xml"):
        path = os.path.join(docs_dir, name)
        if name in files:
            with open(path + ".new", "w", encoding="utf-8", newline="") as handle:
                handle.write(files[name])
            os.chmod(path + ".new", 0o644)
            os.replace(path + ".new", path)
        else:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    return sorted(files)


def render_mailhosts_nginx(domain: str, names: list[str], cert_name: str, docs_dir: str, with_tls: bool) -> str:
    """The server blocks of a domain's mail hosts. Without a certificate yet
    only port 80 and the ACME challenge; with one, HTTPS on 443 as well."""
    for name in names + [cert_name]:
        if not DOMAIN_RE.fullmatch(name) or not (name == domain or name.endswith("." + domain)):
            raise ToolError(f"invalid mail host: {name!r}")
    if not re.fullmatch(r"/[A-Za-z0-9/_.-]+", docs_dir) or ".." in docs_dir:
        raise ToolError(f"invalid documents directory: {docs_dir!r}")
    hosts = " ".join(names)
    lines = [
        f"# Managed by SPanel (Email addon): the mail hosts of {domain}.",
        "server {",
        "    listen 80;",
        f"    server_name {hosts};",
        "    location ^~ /.well-known/acme-challenge/ {",
        f"        root {ACME_ROOT};",
        "        default_type text/plain;",
        "        try_files $uri =404;",
        "    }",
        "    location / { return 301 https://$host$request_uri; }" if with_tls else "    location / { return 404; }",
        "}",
    ]
    if with_tls:
        lines += [
            "server {",
            "    listen 443 ssl;",
            f"    server_name {hosts};",
            f"    ssl_certificate {LE_LIVE}/{cert_name}/fullchain.pem;",
            f"    ssl_certificate_key {LE_LIVE}/{cert_name}/privkey.pem;",
            "    ssl_protocols TLSv1.2 TLSv1.3;",
            f"    root {docs_dir};",
            "    default_type text/plain;",
            "    add_header X-Content-Type-Options nosniff always;",
            *_mailhost_locations(),
            "    location / { return 404; }",
            "}",
        ]
    return "\n".join(lines) + "\n"


def _mailhost_locations() -> list[str]:
    """What the mail hosts serve besides the challenge. Outlook POSTs to
    autodiscover, which a static file answers with 405: that is turned into
    a GET of the same file (error_page ... =200 $uri)."""
    return [
        "    location = /.well-known/mta-sts.txt { try_files /mta-sts.txt =404; }",
        "    location = /mail/config-v1.1.xml { default_type application/xml; try_files /config-v1.1.xml =404; }",
        "    location = /.well-known/autoconfig/mail/config-v1.1.xml {",
        "        default_type application/xml;",
        "        try_files /config-v1.1.xml =404;",
        "    }",
        "    location ~* ^/autodiscover/autodiscover\\.xml$ {",
        "        default_type application/xml;",
        "        error_page 405 =200 $uri;",
        "        try_files /autodiscover.xml =404;",
        "    }",
    ]


def render_sni(entries: dict, tls_dir: str) -> tuple[list[str], list[str], str]:
    """Exim's tls_certs and tls_keys maps and Dovecot's local_name blocks for
    the hosts that have a certificate of their own: {host: certificate name}.
    The files are the copies spanel-mail-tls keeps in <tls_dir>/<name>/."""
    certs, keys, dovecot = [], [], [
        "# Managed by SPanel (Email addon): the certificate each mail host's name is",
        "# answered with (SNI). Written by spanel-mail-tls from /etc/exim4/spanel/tls_hosts.",
    ]
    for host in sorted(entries):
        name = entries[host]
        for value in (host, name):
            if not DOMAIN_RE.fullmatch(str(value or "")):
                raise ToolError(f"invalid TLS host: {value!r}")
        cert, key = f"{tls_dir}/{name}/fullchain.pem", f"{tls_dir}/{name}/privkey.pem"
        certs.append(f"{host}: {cert}")
        keys.append(f"{host}: {key}")
        dovecot += [f"local_name {host} {{", f"  ssl_cert = <{cert}", f"  ssl_key = <{key}", "}"]
    return certs, keys, "\n".join(dovecot) + "\n"


def parse_tls_hosts(text: str) -> dict:
    """/etc/exim4/spanel/tls_hosts: "<host>: <certificate name>" a line."""
    entries = {}
    for line in (text or "").splitlines():
        host, _, name = line.partition(":")
        host, name = host.strip(), name.strip()
        if host and DOMAIN_RE.fullmatch(host) and DOMAIN_RE.fullmatch(name):
            entries[host] = name
    return entries


def sni_apply(registry: str, le_live: str, tls_dir: str, exim_dir: str, dovecot_sni: str, *, owned: bool = True) -> bool:
    """Copy each registered certificate where Exim (as Debian-exim) can read
    it, and write the SNI maps and Dovecot's local_name blocks. A certificate
    whose files are missing or whose key does not match is left out rather
    than break TLS. Returns whether anything changed (Dovecot then needs a
    reload; Exim reads the files per connection)."""
    import filecmp
    import os
    import shutil

    gid = None
    if owned:
        import grp

        gid = grp.getgrnam("Debian-exim").gr_gid
    try:
        with open(registry, encoding="utf-8") as handle:
            entries = parse_tls_hosts(handle.read())
    except OSError:
        entries = {}
    changed = False
    usable = {}
    for host, name in entries.items():
        source = os.path.join(le_live, name)
        cert, key = os.path.join(source, "fullchain.pem"), os.path.join(source, "privkey.pem")
        if not (os.path.isfile(cert) and os.path.isfile(key)):
            continue
        target = os.path.join(tls_dir, name)
        os.makedirs(target, mode=0o750, exist_ok=True)
        if owned:
            os.chown(target, 0, gid)
        for src, dst in ((cert, os.path.join(target, "fullchain.pem")), (key, os.path.join(target, "privkey.pem"))):
            if os.path.isfile(dst) and filecmp.cmp(src, dst, shallow=False):
                continue
            shutil.copyfile(src, dst + ".new")
            if owned:
                os.chown(dst + ".new", 0, gid)
            os.chmod(dst + ".new", 0o640)
            os.replace(dst + ".new", dst)
            changed = True
        usable[host] = name
    certs, keys, dovecot = render_sni(usable, tls_dir)
    for path, text, mode in ((os.path.join(exim_dir, "tls_certs"), "".join(line + "\n" for line in certs), 0o640),
                             (os.path.join(exim_dir, "tls_keys"), "".join(line + "\n" for line in keys), 0o640),
                             (dovecot_sni, dovecot, 0o644)):
        try:
            with open(path, encoding="utf-8") as handle:
                if handle.read() == text:
                    continue
        except OSError:
            pass
        with open(path + ".new", "w", encoding="utf-8") as handle:
            handle.write(text)
        if owned and path != dovecot_sni:
            os.chown(path + ".new", 0, gid)
        os.chmod(path + ".new", mode)
        os.replace(path + ".new", path)
        changed = True
    return changed


# --- spam training: learning from what people move in and out of Junk ----------------------
# Dovecot's IMAPSieve runs learn-spam.sieve when a message is moved into Junk
# and learn-ham.sieve when one is moved out (not to Trash); each pipes the
# message to a program in the Sieve bin directory, which runs as the
# mailbox's owner and only queues it here (learn_enqueue). The root service
# spanel-mail-learn hands the queue to Rspamd's controller with the key no
# mailbox owner may read (learn_process).

LEARN_SPOOL = "/var/spool/spanel-learn"
LEARN_MAX_BYTES = 20 * 1024 * 1024
LEARN_BATCH = 500
LEARN_KINDS = ("spam", "ham")
RSPAMD_CONTROLLER = "http://127.0.0.1:11334"


def learn_enqueue(kind: str, source, spool: str = LEARN_SPOOL) -> str:
    """Write one message (from source, a binary stream) to the spool as
    <kind>.<random>, through a temporary name so the processor never reads
    half a message. Returns the file name."""
    import os
    import secrets

    if kind not in LEARN_KINDS:
        raise ToolError(f"invalid learn kind: {kind!r}")
    token = secrets.token_hex(12)
    tmp = os.path.join(spool, f"tmp.{token}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    written = 0
    try:
        while written <= LEARN_MAX_BYTES:
            chunk = source.read(65536)
            if not chunk:
                break
            os.write(fd, chunk)
            written += len(chunk)
    finally:
        os.close(fd)
    if written == 0 or written > LEARN_MAX_BYTES:
        os.unlink(tmp)
        return ""
    final = os.path.join(spool, f"{kind}.{token}")
    os.rename(tmp, final)
    return os.path.basename(final)


def learn_process(spool: str, send, *, now: float | None = None, batch: int = LEARN_BATCH) -> dict:
    """Hand queued messages to Rspamd: send(kind, data) -> bool (True when
    Rspamd learnt it or had already). Only plain files named spam.* or ham.*
    are read, never through a link; anything else, and temporary files left
    for an hour, is removed. Returns counts."""
    import os
    import stat
    import time

    now = time.time() if now is None else now
    counts = {"spam": 0, "ham": 0, "failed": 0, "removed": 0}
    try:
        names = sorted(os.listdir(spool))
    except OSError:
        return counts
    for name in names[:batch * 2]:
        path = os.path.join(spool, name)
        kind = name.split(".", 1)[0]
        if kind == "tmp":
            try:
                if now - os.lstat(path).st_mtime > 3600:
                    _remove_entry(path)
                    counts["removed"] += 1
            except OSError:
                pass
            continue
        if kind not in LEARN_KINDS:
            _remove_entry(path)
            counts["removed"] += 1
            continue
        if counts["spam"] + counts["ham"] + counts["failed"] >= batch:
            continue
        data = None
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                         | getattr(os, "O_BINARY", 0))
            try:
                info = os.fstat(fd)
                if stat.S_ISREG(info.st_mode) and 0 < info.st_size <= LEARN_MAX_BYTES:
                    chunks, size = [], 0
                    while size < info.st_size:
                        chunk = os.read(fd, min(1 << 20, info.st_size - size))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                    data = b"".join(chunks)
            finally:
                os.close(fd)
        except OSError:
            data = None
        if data and send(kind, data):
            counts[kind] += 1
        else:
            counts["failed"] += 1
        _remove_entry(path)
    return counts


def _remove_entry(path: str) -> None:
    """Remove a spool entry, whatever a mailbox owner made it: a link is
    removed, not followed, and a directory with whatever is in it (rmtree
    works through directory descriptors and never follows a link)."""
    import os
    import shutil
    import stat

    try:
        if stat.S_ISDIR(os.lstat(path).st_mode):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.unlink(path)
    except OSError:
        pass


def rspamd_learn_sender(key: str, controller: str = RSPAMD_CONTROLLER):
    """send() for learn_process: POST to /learnspam or /learnham. "Already
    learnt" counts as learnt."""
    import urllib.error
    import urllib.request

    def send(kind: str, data: bytes) -> bool:
        request = urllib.request.Request(f"{controller}/learn{kind}", data=data, method="POST",
                                         headers={"Password": key})
        try:
            with urllib.request.urlopen(request, timeout=30):  # noqa: S310 - fixed loopback URL
                return True
        except urllib.error.HTTPError as exc:
            return exc.code == 208
        except (urllib.error.URLError, OSError):
            return False

    return send


# --- quota warnings ------------------------------------------------------------------------------
# Dovecot's quota plugin runs the quota-warning service as a mailbox crosses
# 80% and 95% of its quota; the service (as user dovecot) mails the mailbox.

QUOTA_LEVELS = (80, 95)
QUOTA_STAMP_DIR = "/var/lib/spanel-mail/quota-warnings"


def check_quota_warning(percent, address, known: set[str] | None = None) -> tuple[int, str]:
    try:
        level = int(percent)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"invalid quota level: {percent!r}") from exc
    if level not in QUOTA_LEVELS:
        raise ToolError(f"invalid quota level: {percent!r}")
    local, domain = _address(str(address or "").lower())
    full = f"{local}@{domain}"
    if known is not None and full not in known:
        raise ToolError(f"{full} is not a mailbox here")
    return level, full


def render_quota_warning(level: int, address: str, date: str = "") -> bytes:
    """The message a mailbox gets at 80% or 95% of its quota."""
    level, address = check_quota_warning(level, address)
    domain = address.split("@", 1)[1]
    urgent = level >= 95
    subject = f"Your mailbox is {level}% full" + (" - mail will soon be refused" if urgent else "")
    body = (
        f"The mailbox {address} is using {level}% of its space.\n\n"
        + ("When it is full, new mail to it is refused and the senders are told so.\n\n" if urgent else "")
        + "Delete mail you no longer need (and empty Trash and Junk), or ask your provider for more space.\n\n"
        "-- \nThe mail server\n"
    )
    headers = [
        f"From: Mail server <postmaster@{domain}>",
        f"To: <{address}>",
        f"Subject: {subject}",
        "Auto-Submitted: auto-generated",
        "Precedence: bulk",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
        "Content-Transfer-Encoding: 8bit",
    ]
    if date:
        headers.insert(0, f"Date: {date}")
    return ("\n".join(headers) + "\n\n" + body).encode()


def quota_warning_due(stamp_dir: str, level: int, address: str, now: float, every: int = 86400) -> bool:
    """Once a day per mailbox and level, however often Dovecot asks (or
    anyone else on the machine does: the service's socket is open to every
    mailbox owner). Records the warning when it is due."""
    import os

    path = os.path.join(stamp_dir, f"{address}.{level}")
    try:
        if now - os.stat(path).st_mtime < every:
            return False
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="ascii") as handle:
        handle.write(str(int(now)))
    os.utime(path, (now, now))
    return True


def dovecot_mailboxes(users_file: str = "/etc/dovecot/spanel/users") -> set[str]:
    try:
        with open(users_file, encoding="utf-8") as handle:
            return {line.split(":", 1)[0] for line in handle if ":" in line}
    except OSError:
        return set()


# --- command line -------------------------------------------------------------------------

def _read_json_stdin(limit: int = 8_000_000):
    raw = sys.stdin.read(limit + 1)
    if len(raw) > limit:
        raise ToolError("the input is too large")
    try:
        return json.loads(raw or "null")
    except ValueError as exc:
        raise ToolError("expects JSON on stdin") from exc


def main(argv: list[str]) -> int:
    if not argv:
        print("spanel-helper: spanel_mailtool needs a command", file=sys.stderr)
        return 2
    command, args = argv[0], argv[1:]
    try:
        if command == "render-vacation" and not args:
            sys.stdout.write(render_vacation(_read_json_stdin()))
            return 0
        if command == "check-mail-hosts" and not args:
            print(json.dumps(check_mail_hosts(_read_json_stdin(200_000))))
            return 0
        if command == "mail-hosts-fields" and not args:
            # For the shell: every value is a checked name, so one line splits safely.
            item = check_mail_hosts(_read_json_stdin(200_000))
            print(item["domain"], item["cert_name"], item["email"] or "-", ",".join(item["names"]))
            return 0
        if command == "mailhosts-nginx" and len(args) == 2 and args[1] in ("http", "https"):
            item = check_mail_hosts(_read_json_stdin(200_000))
            sys.stdout.write(render_mailhosts_nginx(item["domain"], item["names"], item["cert_name"], args[0],
                                                    args[1] == "https"))
            return 0
        if command == "mailhost-docs" and len(args) == 1:
            item = check_mail_hosts(_read_json_stdin(200_000))
            if args[0] != f"/var/www/spanel-mailhosts/{item['domain']}":
                raise ToolError("the documents go in /var/www/spanel-mailhosts/<domain>")
            print(" ".join(write_mailhost_documents(args[0], item)))
            return 0
        if command == "learn-enqueue" and len(args) == 1:
            # From Dovecot's sieve pipe, as the mailbox's owner: never fails the move.
            try:
                learn_enqueue(args[0], sys.stdin.buffer)
            except OSError as exc:
                print(f"spanel-helper: could not queue the message for learning: {exc}", file=sys.stderr)
            return 0
        if command == "learn-process" and len(args) == 2:
            with open(args[1], encoding="utf-8") as handle:
                key = handle.read().strip()
            counts = learn_process(args[0], rspamd_learn_sender(key))
            print(json.dumps(counts))
            return 0
        if command == "quota-warning" and len(args) == 2:
            import subprocess
            import time
            from email.utils import formatdate

            level, address = check_quota_warning(args[0], args[1], dovecot_mailboxes())
            if quota_warning_due(QUOTA_STAMP_DIR, level, address, time.time()):
                message = render_quota_warning(level, address, formatdate(localtime=True))
                subprocess.run(["/usr/sbin/sendmail", "-oi", "-f", f"postmaster@{address.split('@', 1)[1]}", "--", address],
                               input=message, check=False, timeout=60)
            return 0
        if command == "sni-apply" and len(args) == 5:
            print("changed" if sni_apply(*args) else "unchanged")
            return 0
        raise ToolError(f"unknown spanel_mailtool command: {command}")
    except ToolError as exc:
        print(f"spanel-helper: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
