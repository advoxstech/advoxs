"""Listagem pública dos pacotes de créditos à venda (cadastro self-service)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import CreditPackage
from app.schemas.signup import CreditPackageOut

router = APIRouter(prefix="/credit-packages", tags=["signup"])


@router.get("")
async def list_credit_packages(
    session: AsyncSession = Depends(get_session),
) -> list[CreditPackageOut]:
    result = await session.execute(
        select(CreditPackage)
        .where(CreditPackage.active.is_(True))
        .order_by(CreditPackage.price_brl)
    )
    return [CreditPackageOut.model_validate(p) for p in result.scalars().all()]
