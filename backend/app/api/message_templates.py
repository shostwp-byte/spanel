from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import MessageTemplate, User
from app.schemas.schemas import MessageTemplateOut, MessageTemplateUpdate
from app.services.audit import log_action

router = APIRouter(prefix="/message-templates", tags=["message_templates"])

@router.get("", response_model=List[MessageTemplateOut])
def get_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    ensure_role(current_user.role, Role.reseller)
    return db.query(MessageTemplate).filter(MessageTemplate.reseller_id == current_user.id).all()


@router.put("/{template_type}", response_model=MessageTemplateOut)
def update_template(
    template_type: str,
    payload: MessageTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    ensure_role(current_user.role, Role.reseller)
    
    template = db.query(MessageTemplate).filter(
        MessageTemplate.reseller_id == current_user.id,
        MessageTemplate.template_type == template_type
    ).first()

    if template:
        template.subject = payload.subject
        template.body = payload.body
    else:
        template = MessageTemplate(
            reseller_id=current_user.id,
            template_type=template_type,
            subject=payload.subject,
            body=payload.body
        )
        db.add(template)
        
    db.commit()
    db.refresh(template)
    log_action(db, current_user.id, "update_message_template", template_type)
    return template
