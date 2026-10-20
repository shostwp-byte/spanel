"""The Services page describes the machine, not a customer's slice of it.

system-info gives the kernel and hostname, resource-usage gives the whole
box's CPU and memory, and list/action say which system units are installed and
running. None of that belongs to one hosting customer; all of it helps somebody
sizing up the host.

All four were open to end users. The page was visible to them too, but hiding a
page in the frontend is not access control - a session cookie and curl reach
the same endpoints.
"""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICES_API = PROJECT_ROOT / "backend" / "app" / "api" / "services.py"
APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"


def _role_checks() -> dict[str, list[str]]:
    """Every ensure_role in the module, by the function it guards."""
    tree = ast.parse(SERVICES_API.read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        roles = []
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            if getattr(call.func, "id", None) != "ensure_role":
                continue
            roles += [ast.unparse(a) for a in call.args[1:]]
        found[node.name] = roles
    return found


def test_every_services_endpoint_requires_an_admin():
    checks = _role_checks()
    assert checks, "no ensure_role calls found; this test is reading the wrong file"
    for name, roles in checks.items():
        assert roles, f"{name} has no role check at all"
        for role in roles:
            assert role == "Role.admin", (
                f"{name} accepts {role}; this endpoint describes the server, "
                "which is not a customer's to read"
            )


def test_reading_a_unit_status_is_not_a_customer_operation():
    """`action: status` used to be end-user reachable while start/stop was not.

    A status read still says which units exist and whether they are running,
    which is the same disclosure as listing them.
    """
    src = SERVICES_API.read_text(encoding="utf-8")
    body = src.split("def run_service_action(", 1)[1].split("\n@router", 1)[0]
    assert "Role.end_user" not in body
    assert "minimum_role" not in body, (
        "the role no longer varies by action; a single admin check is clearer"
    )


def test_the_page_is_hidden_and_refuses_rather_than_erroring():
    """Hidden in the nav, and answering for itself when reached by URL."""
    src = APP_JSX.read_text(encoding="utf-8")
    assert "isAdmin && ['services', 'Services', Activity," in src, (
        "the nav entry is still offered to end users"
    )
    assert "isAdmin ? renderServices() : renderAdminOnly()" in src, (
        "the page renders for end users and fires requests that will be refused"
    )
    assert "function renderAdminOnly()" in src


def test_the_dashboard_services_card_is_admin_only():
    """The card sits in the dashboard's admin branch, and the summary endpoint
    leaves the services out for a customer."""
    src = APP_JSX.read_text(encoding="utf-8")
    index = src.index("cards.push({ key: 'services'")
    before = src[:index]
    assert before.rfind("if (isAdmin) {") > before.rfind("} else {"), "services card is not inside the isAdmin branch"
    api = (PROJECT_ROOT / "backend" / "app" / "api" / "dashboard.py").read_text(encoding="utf-8")
    assert '    if admin:\n' in api and 'summary["services"] = _safe(_services)' in api.split("    if admin:\n", 1)[1]
