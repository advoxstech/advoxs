from datetime import datetime

from fastapi import APIRouter

router_health = APIRouter()


@router_health.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
