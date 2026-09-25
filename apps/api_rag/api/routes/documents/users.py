import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.security import verify_api_key
from clients.qdrant import QdrantClient
from database.repositories.documento import DocumentoRepository
from database.session import get_session
from safe_logging import safe_error, safe_identifier
from services.documents.main import DocumentoService

router_doc_users = APIRouter(prefix="/documents/users")


def get_qdrant():
    return QdrantClient()


async def get_repo(session: AsyncSession = Depends(get_session)):
    return DocumentoRepository(session=session)


@router_doc_users.get("/{document_id}/content", dependencies=[Depends(verify_api_key)])
async def document_content(
    document_id: UUID,
    tenant_id: UUID,
    repo: DocumentoRepository = Depends(get_repo),
):
    """Internal download, restricted to a tenant's office knowledge base."""
    doc = await repo.buscar_documento_usuario_por_id(document_id)
    root_dir = os.getenv("UPLOAD_DIR_USER")
    if (
        doc is None
        or str(doc.tenant_id) != str(tenant_id)
        or doc.conversation_id != "kb"
        or not root_dir
    ):
        raise HTTPException(404, "Documento indisponível")
    base = Path(root_dir).resolve()
    root = (base / str(tenant_id) / "kb").resolve()
    path = (Path(doc.path_base) / doc.path_doc / doc.nome).resolve()
    if not root.is_relative_to(base) or not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Documento indisponível")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


async def get_service(
    repo: DocumentoRepository = Depends(get_repo),
    qdrant: QdrantClient = Depends(get_qdrant),
) -> DocumentoService:
    return DocumentoService(repo=repo, qdrant=qdrant)


# ── Documentos do Usuário (contato de um escritório) ────────────────────


@router_doc_users.post("/insert")
async def inserir_documento(
    tenant_id: str = Form(...),
    conversation_id: str = Form(...),
    doc_id: str | None = Form(default=None),
    file: UploadFile = File(...),
    service: DocumentoService = Depends(get_service),
    security: str = Depends(verify_api_key),
):
    try:
        files = [file]
        logger.info(
            "Recebendo arquivos | total={} | tenant_ref={}",
            len(files),
            safe_identifier(tenant_id),
        )
        await service.inserir_documento_usuario(files, tenant_id, conversation_id, doc_id=doc_id)
        return {"mensagem": "Documentos inseridos com sucesso"}
    except ValueError as exc:
        logger.warning("Erro de validação ao inserir documento | error_type={}", safe_error(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Erro ao inserir documento | error_type={}", safe_error(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router_doc_users.delete("/delete")
async def deletar_documentos(
    tenant_id: str = Query(...),
    docs_ids: list[str] = Query(...),
    service: DocumentoService = Depends(get_service),
    security: str = Depends(verify_api_key),
):
    logger.info(
        "Deletando documentos | total={} | tenant_ref={}",
        len(docs_ids),
        safe_identifier(tenant_id),
    )
    try:
        await service.deletar_documento_usuario(tenant_id, docs_ids)
        logger.info(f"Documentos | total={len(docs_ids)} deletados com sucesso")
        return {"mensagem": "Documentos deletados com sucesso"}
    except ValueError as exc:
        logger.warning("Erro de validação ao deletar documentos | error_type={}", safe_error(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Erro ao deletar documentos | error_type={}", safe_error(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
