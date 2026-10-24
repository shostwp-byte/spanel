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
        raise ToolError(f"unknown spanel_mailtool command: {command}")
    except ToolError as exc:
        print(f"spanel-helper: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
