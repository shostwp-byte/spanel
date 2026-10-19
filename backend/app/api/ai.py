from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.api.deps import get_current_user
from app.models.entities import User

router = APIRouter(prefix="/ai", tags=["ai"])

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    reply: str

@router.post("/chat", response_model=ChatResponse)
def ai_chat(payload: ChatRequest, current_user: User = Depends(get_current_user)):
    # Mock AI response
    msg = payload.message.lower()
    
    reply = "I'm your AI Assistant. I can help you configure your server, install apps, and troubleshoot issues."
    
    if "laravel" in msg or "nodejs" in msg:
        reply = "To install Laravel or Node.js, you can head over to the **App Installer** tab in the sidebar. It's a 1-click process!"
    elif "slow" in msg or "optimize" in msg:
        reply = "If your server is slow, consider enabling **FastCGI Cache** for your WordPress sites, or checking the **Resource Stats** to see if any user is consuming too much bandwidth."
    elif "backup" in msg:
        reply = "You can schedule automatic backups in the **Backups** tab. SPanel supports local and S3-compatible remote storage."
    
    return {"reply": reply}
