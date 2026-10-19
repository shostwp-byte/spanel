from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
import logging

from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import NotificationLog, User
from app.services.audit import log_action
from app.services import notifications

logger = logging.getLogger("spanel.message_all")

router = APIRouter(prefix="/message-all", tags=["messages"])

class MessageAllPayload(BaseModel):
    target: str
    priority: str
    subject: str
    message: str

def _send_bulk_messages(target: str, subject: str, message: str, admin_id: int):
    bg_db = SessionLocal()
    try:
        query = bg_db.query(User)
        
        if target == "resellers":
            query = query.filter(User.role == Role.reseller)
        elif target == "admin":
            query = query.filter(User.role == Role.admin)
        # else all_users: no filter
        
        users = query.all()
        config = notifications.load_config()
        smtp_ok = notifications.smtp_ready(config)
        count = 0
        for u in users:
            if not u.email:
                continue
            try:
                if smtp_ok:
                    notifications.send_email(config, u.email, subject, message)
                bg_db.add(
                    NotificationLog(
                        user_id=u.id,
                        event="message_all",
                        channel="email",
                        status="sent",
                        title=subject[:255],
                        detail=message[:2000],
                    )
                )
                count += 1
            except Exception as e:
                logger.error(f"Failed to send bulk message to {u.email}: {e}")
                
        log_action(
            bg_db,
            admin_id,
            "message_all",
            target,
            f"Broadcasted message '{subject}' to {count} users (target={target})",
        )
        bg_db.commit()
    except Exception as e:
        bg_db.rollback()
        logger.error(f"Bulk message broadcast failed: {e}")
    finally:
        bg_db.close()

@router.post("")
def broadcast_message(
    payload: MessageAllPayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    ensure_role(current_user.role, Role.admin)
    
    if not payload.subject or not payload.message:
        raise HTTPException(status_code=400, detail="Subject and message are required")
        
    background_tasks.add_task(
        _send_bulk_messages, 
        payload.target, 
        payload.subject, 
        payload.message, 
        current_user.id
    )
    
    return {"success": True, "message": "Message queued for delivery"}
