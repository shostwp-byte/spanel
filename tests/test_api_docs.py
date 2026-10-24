"""docs/API_AND_WHMCS.md and docs/openapi.json must describe the API that
actually exists: the old document listed paths without /v1 that had never
been served."""
import json
import re
import sys

import api_testkit as kit

DOC = kit.REPO_ROOT / "docs" / "API_AND_WHMCS.md"
OPENAPI = kit.REPO_ROOT / "docs" / "openapi.json"
ENDPOINT_RE = re.compile(r"\b(GET|POST|PATCH|PUT|DELETE) (/api/[A-Za-z0-9_\-/{}.:]+)")


def _served() -> dict[str, set[str]]:
    spec = kit.build_app().openapi()
    return {path: {m.upper() for m in methods} for path, methods in spec["paths"].items()}


def _documented() -> set[tuple[str, str]]:
    found = set()
    for method, path in ENDPOINT_RE.findall(DOC.read_text(encoding="utf-8")):
        found.add((method, path.rstrip(".")))
    return found


def test_every_documented_endpoint_exists():
    served = _served()
    documented = _documented()
    assert len(documented) > 20
    missing = sorted(f"{m} {p}" for m, p in documented if m not in served.get(p, set()))
    assert not missing, f"documented but not served: {missing}"


def test_every_provisioning_endpoint_is_documented():
    documented = _documented()
    undocumented = sorted(
        f"{m} {p}"
        for p, methods in _served().items()
        if p.startswith("/api/provisioning/v1")
        for m in methods
        if (m, p) not in documented
    )
    assert not undocumented, f"served but not documented: {undocumented}"


def test_every_catalogue_event_is_documented():
    from app.services.webhooks import EVENT_CATALOGUE

    text = DOC.read_text(encoding="utf-8")
    missing = [name for name in EVENT_CATALOGUE if f"`{name}`" not in text]
    assert not missing


def test_committed_openapi_matches_the_code():
    sys.path.insert(0, str(kit.REPO_ROOT / "tools"))
    import export_openapi

    fresh = export_openapi.build_document()
    committed = json.loads(OPENAPI.read_text(encoding="utf-8"))
    # The version follows the VERSION file; a release bump alone must not
    # make the document stale.
    fresh["info"].pop("version", None)
    committed["info"].pop("version", None)
    assert committed == json.loads(json.dumps(fresh)), "run: PYTHONPATH=backend python tools/export_openapi.py"
    assert "/api/provisioning/v1/accounts/usage" in committed["paths"]
    assert committed["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
