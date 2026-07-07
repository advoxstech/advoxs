from fastapi import APIRouter
from datetime import datetime

router_health = APIRouter()

@router_health.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat()
    }