# db/repositories/documento.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from datetime import datetime
from uuid import UUID
from database.models import DocumentoUsuario, DocumentoSistema


class DocumentoRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── Documentos do Usuário ──────────────────────────────────────────

    async def criar_documento_usuario(self, documento: DocumentoUsuario) -> DocumentoUsuario:
        self.session.add(documento)
        await self.session.commit()
        await self.session.refresh(documento)
        return documento

    async def buscar_documento_usuario_por_id(self, documento_id: UUID) -> DocumentoUsuario | None:
        return await self.session.get(DocumentoUsuario, documento_id)

    async def listar_documentos_por_usuario(self, usuario_id: UUID) -> list[DocumentoUsuario]:
        result = await self.session.execute(
            select(DocumentoUsuario).where(DocumentoUsuario.usuario_id == usuario_id)
        )
        return result.scalars().all()

    async def deletar_documento_usuario(self, documento_id: UUID) -> None:
        doc = await self.buscar_documento_usuario_por_id(documento_id)
        if doc:
            await self.session.delete(doc)
            await self.session.commit()


    async def listar_documentos_por_periodo(
        self,
        usuario_id: str,
        data_inicio: datetime | None = None,
        data_fim: datetime | None = None
    ) -> list[DocumentoUsuario]:

        stmt = select(DocumentoUsuario).where(
            DocumentoUsuario.usuario_id == usuario_id
        )

        if data_inicio:
            stmt = stmt.where(DocumentoUsuario.criado_em >= data_inicio)

        if data_fim:
            stmt = stmt.where(DocumentoUsuario.criado_em <= data_fim)

        result = await self.session.execute(stmt)
        return result.scalars().all()
    

    # ── Documentos do Sistema ──────────────────────────────────────────

    async def criar_documento_sistema(self, documento: DocumentoSistema) -> DocumentoSistema:
        self.session.add(documento)
        await self.session.commit()
        await self.session.refresh(documento)
        return documento

    async def buscar_documento_sistema_por_id(self, documento_id: UUID) -> DocumentoSistema | None:
        return await self.session.get(DocumentoSistema, documento_id)

    async def listar_documentos_sistema(self) -> list[DocumentoSistema]:
        result = await self.session.execute(select(DocumentoSistema))
        return result.scalars().all()

    async def deletar_documento_sistema(self, documento_id: UUID) -> None:
        doc = await self.buscar_documento_sistema_por_id(documento_id)
        if doc:
            await self.session.delete(doc)
            await self.session.commit()

    async def listar_documentos_sistema_por_periodo(
        self,
        data_inicio: datetime | None = None,
        data_fim: datetime | None = None,
    ) -> list[DocumentoSistema]:
        stmt = select(DocumentoSistema)

        if data_inicio:
            stmt = stmt.where(DocumentoSistema.criado_em >= data_inicio)
        if data_fim:
            stmt = stmt.where(DocumentoSistema.criado_em <= data_fim)

        result = await self.session.execute(stmt)
        return result.scalars().all()