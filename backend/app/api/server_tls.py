import os
import subprocess
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from app.models.entities import User
from app.api.auth import get_current_user
from app.core.permissions import Role, ensure_role
from app.core.config import settings
from app.services.panel_settings import current_settings

router = APIRouter(prefix="/api/server-tls", tags=["server-tls"])

def get_cert_details(cert_path: str) -> dict:
    if not os.path.exists(cert_path):
        return {}
    try:
        # Use openssl to parse subject, issuer, dates
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-subject", "-issuer", "-startdate", "-enddate", "-serial"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return {}
        lines = result.stdout.splitlines()
        info = {}
        for line in lines:
            # e.g., subject=CN = example.com
            if line.startswith("subject="):
                info["subject"] = line.split("=", 1)[1].strip()
            elif line.startswith("issuer="):
                info["issuer"] = line.split("=", 1)[1].strip()
            elif line.startswith("notBefore="):
                info["issued_on"] = line.split("=", 1)[1].strip()
            elif line.startswith("notAfter="):
                info["expires_in"] = line.split("=", 1)[1].strip()
            elif line.startswith("serial="):
                info["serial_number"] = line.split("=", 1)[1].strip()
                
        # Also get pubkey type
        pub_res = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-text"],
            capture_output=True, text=True
        )
        if pub_res.returncode == 0:
            for l in pub_res.stdout.splitlines():
                if "Public Key Algorithm:" in l:
                    info["key_type"] = l.split(":", 1)[1].strip()
                if "DNS:" in l:
                    # Very rough extraction of SANs
                    sans = [p.strip().replace("DNS:", "") for p in l.split(",") if "DNS:" in p]
                    info["domains"] = ", ".join(sans)
        return info
    except Exception:
        return {}

@router.get("")
def get_server_tls(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    cert_path = "/etc/spanel/panel-fullchain.pem"
    key_path = "/etc/spanel/panel-privkey.pem"
    if settings.panel_ssl_cert and os.path.exists(settings.panel_ssl_cert):
        cert_path = settings.panel_ssl_cert
        key_path = settings.panel_ssl_key
        
    key_valid = os.path.exists(key_path)
    cert_valid = os.path.exists(cert_path)
    
    cs = current_settings()
    panel_https = cs.get("ssl_enabled", False)
    
    main_cert = get_cert_details(cert_path) if cert_valid else {}
    
    return {
        "key_valid": key_valid,
        "cert_valid": cert_valid,
        "panel_https": panel_https,
        "auto_cert": cert_valid, # Enabled if there is a cert
        "main_cert": main_cert,
        "chain_certs": []
    }

@router.get("/export-ca")
def export_ca(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    cert_path = "/etc/spanel/panel-fullchain.pem"
    if settings.panel_ssl_cert and os.path.exists(settings.panel_ssl_cert):
        cert_path = settings.panel_ssl_cert
    if not os.path.exists(cert_path):
        raise HTTPException(status_code=404, detail="Server certificate not found")
    return FileResponse(
        cert_path,
        media_type="application/x-pem-file",
        filename="spanel-ca.pem",
    )
