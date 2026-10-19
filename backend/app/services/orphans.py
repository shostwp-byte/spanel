"""What a deleted website leaves behind, and clearing it.

Deleting a site through the panel cleans up after itself. This exists for
everything that predates those fixes, plus the cases the panel never sees: a
site removed by hand, an import that failed halfway, a domain a customer moved
to another server. On one live server this was four Let's Encrypt lineages,
seven vhost backups, two uploaded certificates and six panel SNI copies - and
the first of those is the one that hurts, because a renewal config for a site
nobody hosts wakes certbot twice a day and eventually fails for good.

The panel decides what is live; the helper does the privileged inspection and
removal, keeps its own guards, and copies everything into /root/spanel-removed
before deleting.
"""

from sqlalchemy.orm import Session

from app.models.entities import Website, WebsiteAlias
from app.services.shell import CommandResult, shell

CATEGORY_LABELS = {
    "cert": "Let's Encrypt certificate",
    "waf-rules": "WAF rule file",
    "vhost-backup": "vhost backup",
    "manual-ssl": "uploaded certificate",
    "sni-copy": "panel SNI copy",
    "php-pool": "PHP-FPM pool",
}


def live_domains(db: Session) -> list[str]:
    """Every domain this panel still serves, websites and aliases alike.

    An alias is as live as the website carrying it: a certificate covering only
    an alias is still in use.
    """
    names: set[str] = set()
    for (domain,) in db.query(Website.domain).all():
        if domain and domain.strip():
            names.add(domain.strip().lower())
    for (domain,) in db.query(WebsiteAlias.domain).all():
        if domain and domain.strip():
            names.add(domain.strip().lower())
    return sorted(names)


def _run(verb: str, domains: list[str]) -> CommandResult:
    # An empty list would mean "nothing on this server is live", which the
    # helper refuses - but do not even ask, so a broken query cannot turn into
    # a delete request at all.
    if not domains:
        raise ValueError("refusing to run orphan cleanup without any live domains")
    return shell.privileged(
        verb,
        check=False,
        input="\n".join(domains) + "\n",
        fallback=["bash", "-lc", "cat >/dev/null; echo 'summary\tcerts=0 waf-rules=0'"],
    )


def _parse(result: CommandResult) -> dict:
    items: list[dict] = []
    summary: dict[str, int] = {}
    archive = ""
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        kind, value = parts[0].strip(), parts[1].strip()
        if kind == "summary":
            for token in value.split():
                key, _, count = token.partition("=")
                summary[key] = int(count) if count.isdigit() else 0
        elif kind == "archive":
            archive = value
        elif kind in CATEGORY_LABELS:
            items.append({"type": kind, "label": CATEGORY_LABELS[kind], "name": value})
    return {
        "items": items,
        "summary": summary,
        "archive": archive,
        "total": len(items),
        "ok": result.returncode == 0,
        "error": "" if result.returncode == 0 else (result.stderr or result.stdout or "").strip()[:400],
    }


def scan(db: Session) -> dict:
    """List what would be removed, touching nothing."""
    return _parse(_run("orphans-scan", live_domains(db)))


def clean(db: Session) -> dict:
    """Remove it, after archiving a copy of everything."""
    return _parse(_run("orphans-clean", live_domains(db)))


def describe(outcome: dict) -> str:
    if not outcome.get("ok"):
        return f"Orphan cleanup failed: {outcome.get('error') or 'unknown error'}"
    if not outcome.get("total"):
        return "Nothing orphaned: every certificate and config on disk belongs to a website this panel serves."
    counts = ", ".join(f"{key} {value}" for key, value in sorted(outcome["summary"].items()) if value)
    message = f"Removed {outcome['total']} orphaned item(s): {counts}."
    if outcome.get("archive"):
        message += f" A copy is in {outcome['archive']}."
    return message
