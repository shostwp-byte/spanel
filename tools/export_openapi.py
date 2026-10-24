#!/usr/bin/env python3
"""Export the billing-facing OpenAPI document to docs/openapi.json.

    PYTHONPATH=backend python tools/export_openapi.py          # write
    PYTHONPATH=backend python tools/export_openapi.py --check  # exit 1 if stale

Only the provisioning API (/api/provisioning/v1) is exported: it is what
WHMCS and other billing systems integrate against. The panel serves the full
document at /openapi.json; the Swagger UI at /docs is blocked by the panel's
Content-Security-Policy, so load this file into any OpenAPI viewer instead.
tests/test_api_docs.py fails when the committed file is out of date.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "docs" / "openapi.json"

if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))


def build_document() -> dict:
    from fastapi import FastAPI
    from fastapi.openapi.utils import get_openapi

    from app.api import provisioning
    from app.core.version import APP_VERSION

    app = FastAPI()
    app.include_router(provisioning.router, prefix="/api")
    document = get_openapi(
        title="SPanel Provisioning API",
        version=APP_VERSION,
        description=(
            "Billing-system API for creating and managing hosting accounts. "
            "Authenticate with `Authorization: Bearer <token>`; write calls accept "
            "an `Idempotency-Key` header. See docs/API_AND_WHMCS.md."
        ),
        routes=app.routes,
    )
    document.setdefault("components", {})["securitySchemes"] = {
        "bearerAuth": {"type": "http", "scheme": "bearer", "description": "Provisioning API token (bp_...)"},
    }
    document["security"] = [{"bearerAuth": []}]
    return document


def render() -> str:
    return json.dumps(build_document(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print(f"{OUTPUT} is out of date; run tools/export_openapi.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT} is up to date")
        return 0
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
