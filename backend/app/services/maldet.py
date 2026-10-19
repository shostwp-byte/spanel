"""Linux Malware Detect (LMD / maldet) integration.

Layered on top of the ClamAV scanner. LMD brings site-root-targeted scans,
a cheap daily "recent files" scan, self-updating rfxn signatures, named
malware families in the hit list, and (Level 2) an inotify real-time monitor.

Every function degrades gracefully: when maldet is not installed each call
returns an empty / disabled result rather than raising, so the panel works
unchanged on a server that never enabled the scanner.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.shell import shell

MALDET_BIN = Path("/usr/local/sbin/maldet")
MALDET_HOME = Path("/usr/local/maldetect")
JOBS_DIR = Path("/var/lib/spanel/malware-scan-jobs")

# maldet's plain-text report lists hits as "<signature> : <path>".
_HIT_LINE_RE = re.compile(r"^\s*(?P<sig>\S.*?)\s*:\s*(?P<path>/\S.*?)\s*$")
_KV_RE = re.compile(r"^([a-z_]+)=(.*)$")


def installed() -> bool:
    return MALDET_BIN.exists()


def _kv(text: str) -> dict:
    out: dict = {}
    for line in (text or "").splitlines():
        m = _KV_RE.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def status() -> dict:
    """installed / monitor running / signature version, from the helper."""
    if not installed():
        return {"installed": False, "monitor_running": False, "sig_version": "", "sig_updated_at": ""}
    result = shell.privileged("maldet-status", check=False, fallback=["bash", "-lc", "echo installed=1"])
    kv = _kv(result.stdout)
    return {
        "installed": kv.get("installed") == "1",
        "monitor_running": kv.get("monitor") == "1",
        "sig_version": kv.get("sig_version", "") if kv.get("sig_version") != "unknown" else "",
        "sig_updated_at": kv.get("sig_updated", ""),
    }


def install() -> str:
    """Download + install LMD via the helper. Raises on failure."""
    result = shell.privileged(
        "maldet-install",
        check=False,
        timeout=600,
        fallback=["bash", "-lc", "echo dry-run-maldet-install"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "maldet install failed").strip())
    return (result.stdout or "LMD installed").strip()


def update_signatures() -> str:
    result = shell.privileged(
        "maldet-update-sigs",
        check=False,
        timeout=300,
        fallback=["bash", "-lc", "echo dry-run-maldet-update"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "signature update failed").strip())
    return (result.stdout or "signatures updated").strip()


def scan(job_id: str, targets: list[str], *, recent_days: int | None = None, timeout: int = 6 * 60 * 60) -> dict:
    """Run a maldet scan through the helper.

    ``recent_days`` set -> ``maldet -r <target> <days>`` (first target only);
    otherwise ``maldet -a <targets>``. Returns ``{"scanid": str, "exit": int}``.
    """
    mode = "recent" if recent_days is not None else "all"
    days = str(int(recent_days)) if recent_days is not None else "0"
    args = [job_id, mode, days, *targets]
    result = shell.privileged(
        "maldet-scan",
        helper_args=args,
        check=False,
        timeout=timeout,
        fallback=["bash", "-lc", "echo scanid=none; echo exit=0"],
    )
    kv = _kv(result.stdout)
    scanid = kv.get("scanid", "")
    if scanid in ("", "none"):
        scanid = ""
    try:
        exit_code = int(kv.get("exit", result.returncode))
    except (TypeError, ValueError):
        exit_code = result.returncode
    return {"scanid": scanid, "exit": exit_code, "raw": result.stdout or ""}


def progress(job_id: str) -> dict:
    """How far a running scan has got: {"stage", "total", "scanned"}.

    stage is none / starting / listing / scanning / results. The helper reads
    the scanner's position in maldet's file list; only root can see it.
    """
    result = shell.privileged(
        "maldet-progress",
        helper_args=[job_id],
        check=False,
        fallback=["bash", "-lc", "echo stage=none"],
    )
    kv = _kv(result.stdout)

    def number(key: str) -> int:
        try:
            return max(0, int(kv.get(key, 0)))
        except (TypeError, ValueError):
            return 0

    return {"stage": kv.get("stage", "none"), "total": number("total"), "scanned": number("scanned")}


def _report_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.maldet.report"


def parse_report_text(text: str) -> tuple[int, list[dict]]:
    """(total_files, threats[]) from a maldet plain-text scan report."""
    total_files = 0
    threats: list[dict] = []
    in_hits = False
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        low = line.lower()
        if "total files" in low:
            digits = re.findall(r"\d+", line)
            if digits:
                total_files = int(digits[-1])
        if "file hit list" in low or low.startswith("hits :"):
            in_hits = True
            continue
        if in_hits:
            if not line.strip() or set(line.strip()) <= {"=", "-"}:
                if threats:
                    in_hits = False
                continue
            m = _HIT_LINE_RE.match(line)
            if m and m.group("path").startswith("/"):
                threats.append({"path": m.group("path"), "signature": m.group("sig"), "domain": ""})
    return total_files, threats


def read_job_report(job_id: str) -> tuple[int, list[dict]]:
    try:
        return parse_report_text(_report_path(job_id).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0, []


def monitor_status() -> dict:
    if not installed():
        return {"running": False, "max_watches": 0}
    result = shell.privileged(
        "maldet-monitor", helper_args=["status"], check=False,
        fallback=["bash", "-lc", "echo running=0; echo watches=0"],
    )
    kv = _kv(result.stdout)
    try:
        watches = int(kv.get("watches", 0))
    except (TypeError, ValueError):
        watches = 0
    return {"running": kv.get("running") == "1", "max_watches": watches}


def monitor_start() -> None:
    result = shell.privileged(
        "maldet-monitor", helper_args=["start"], check=False, timeout=120,
        fallback=["bash", "-lc", "echo monitor started"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "could not start the monitor").strip())


def monitor_stop() -> None:
    shell.privileged(
        "maldet-monitor", helper_args=["stop"], check=False, timeout=60,
        fallback=["bash", "-lc", "echo monitor stopped"],
    )
