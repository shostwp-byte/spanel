import logging
import json
import hmac
import hashlib
import urllib.request
import urllib.error
import threading
from typing import Optional

from sqlalchemy.orm import Session
from app.models.entities import WebhookEndpoint, User

logger = logging.getLogger("spanel.webhooks")

def _send_webhook(url: str, secret: str, payload: dict):
    headers = {"Content-Type": "application/json"}
    body = json.dumps(payload).encode("utf-8")
    
    if secret:
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-SPanel-Signature"] = signature
        
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            pass # 2xx status code
    except urllib.error.HTTPError as e:
        logger.warning(f"Webhook {url} returned status {e.code}")
    except Exception as e:
        logger.warning(f"Failed to send webhook to {url}: {e}")

def dispatch_webhook(db: Session, user_id: Optional[int], action: str, target: str, detail: str = ""):
    if not user_id:
        return
        
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return
        
    # Find endpoints for the user and their reseller (if applicable)
    endpoint_users = [user.id]
    if user.reseller_id:
        endpoint_users.append(user.reseller_id)
        
    endpoints = db.query(WebhookEndpoint).filter(
        WebhookEndpoint.user_id.in_(endpoint_users),
        WebhookEndpoint.is_active == True
    ).all()
    
    if not endpoints:
        return
        
    payload = {
        "event": action,
        "user_id": user.id,
        "username": user.username,
        "target": target,
        "detail": detail
    }
    
    for ep in endpoints:
        if ep.events != "*" and action not in ep.events.split(","):
            continue
            
        # Dispatch in background thread so API doesn't block
        threading.Thread(target=_send_webhook, args=(ep.url, ep.secret, payload), daemon=True).start()
