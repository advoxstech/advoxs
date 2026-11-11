"""Carrega a base central da Advoxs e os pacotes por especialidade.

Lê a pasta BASE-PUBLICA baixada do Drive (precisa da subpasta PACOTES), lê cada
documento distinto uma única vez no api_rag e grava a composição do pacote de
cada especialidade. Pode ser rodado de novo: documentos que não mudaram são
pulados, os que mudaram são lidos de novo.

Uso (dentro de apps/api, com o ambiente do api):

    uv run python scripts/load_public_base.py /caminho/BASE-PUBLICA
    uv run python scripts/load_public_base.py /caminho/BASE-PUBLICA --dry-run
    uv run python scripts/load_public_base.py /caminho/BASE-PUBLICA --provision

--provision entrega os pacotes aos agentes que já têm especialidade (os agentes
padrão dos escritórios existentes). Sem ela, cada escritório recebe o pacote ao
escolher a especialidade ou ao usar "Restaurar pacote".
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from app.clients.rag import RagApiError, insert_user_document  # noqa: E402
from app.core.db import SystemSessionLocal  # noqa: E402
from app.core.knowledge_tree import PUBLIC_TENANT_ID, SPECIALTIES  # noqa: E402
from app.core.queue import close_arq_pool, get_arq_pool  # noqa: E402
from app.models import Agent, PublicDocument, SpecialtyPackageDocument  # noqa: E402
from app.services.packages import enqueue_copies, sync_agent_package  # noqa: E402
from app.services.public_base import PublicFile, scan_packages  # noqa: E402

# Um código grande leva minutos para ser lido (mesmo prazo da fila de ingestão).
READ_TIMEOUT_SECONDS = 1500


async def _load_document(item: PublicFile) -> str:
    """Grava/atualiza o documento e o lê no api_rag. Devolve o que aconteceu."""
    async with SystemSessionLocal() as session:
        document = await session.scalar(
            select(PublicDocument).where(PublicDocument.filename == item.filename)
        )
        if (
            document is not None
            and document.status == "ready"
            and document.content_sha256 == item.content_sha256
        ):
            document.category = item.category
            document.code, document.title = item.code, item.title
            await session.commit()
            return "sem mudança"
        if document is None:
            document = PublicDocument(filename=item.filename)
            session.add(document)
        document.code, document.title = item.code, item.title
        document.category = item.category
        document.mime_type = item.mime_type
        document.size_bytes = item.size_bytes
        document.content_sha256 = item.content_sha256
        document.status = "processing"
        document.error_message = None
        document.updated_at = datetime.now(UTC)
        await session.commit()
        document_id = document.id

    try:
        partial_pages = await insert_user_document(
            tenant_id=PUBLIC_TENANT_ID,
            conversation_id="kb",
            doc_id=str(document_id),
            filename=item.filename,
            file_bytes=item.path.read_bytes(),
            timeout=READ_TIMEOUT_SECONDS,
        )
        status, error = "ready", None
    except RagApiError as exc:
        partial_pages, status, error = [], "error", str(exc)[:500]

    async with SystemSessionLocal() as session:
        document = await session.get(PublicDocument, document_id)
        document.status = status
        document.error_message = error
        document.partial_pages = partial_pages or None
        await session.commit()
    return "lido" if status == "ready" else f"ERRO: {error}"


async def _save_packages(items: list[PublicFile]) -> None:
    """A composição de cada pacote passa a ser exatamente a da pasta."""
    async with SystemSessionLocal() as session:
        ids = dict(
            (
                await session.execute(
                    select(PublicDocument.filename, PublicDocument.id).where(
                        PublicDocument.status == "ready"
                    )
                )
            ).all()
        )
        await session.execute(delete(SpecialtyPackageDocument))
        for item in items:
            document_id = ids.get(item.filename)
            if document_id is None:
                continue
            for specialty in sorted(item.specialties):
                session.add(
                    SpecialtyPackageDocument(specialty=specialty, public_document_id=document_id)
                )
        await session.commit()


async def _provision() -> None:
    arq = await get_arq_pool()
    try:
        async with SystemSessionLocal() as session:
            agents = (
                await session.scalars(select(Agent).where(Agent.specialty.is_not(None)))
            ).all()
            total = 0
            for agent in agents:
                to_copy = await sync_agent_package(session, agent)
                await session.commit()
                await enqueue_copies(arq, agent.tenant_id, to_copy)
                total += len(to_copy)
        print(f"{len(agents)} agente(s) com especialidade; {total} cópia(s) enfileirada(s).")
    finally:
        await close_arq_pool()


async def run(args: argparse.Namespace) -> int:
    items = scan_packages(args.pasta)
    by_specialty = {s: sum(s in item.specialties for item in items) for s in SPECIALTIES}
    megabytes = sum(item.size_bytes for item in items) / 1_048_576
    print(f"{len(items)} documentos distintos ({megabytes:.0f} MB).")
    for number, specialty in enumerate(SPECIALTIES, start=1):
        print(f"  {number:02d} {specialty}: {by_specialty[specialty]} documentos")
    if args.dry_run:
        return 0

    failures = 0
    for index, item in enumerate(items, start=1):
        outcome = await _load_document(item)
        failures += outcome.startswith("ERRO")
        print(f"[{index}/{len(items)}] {item.filename}: {outcome}", flush=True)

    await _save_packages(items)
    print("Pacotes gravados.")
    if args.provision:
        await _provision()
    if failures:
        print(f"{failures} documento(s) com erro — rode de novo para tentar outra vez.")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pasta", type=Path, help="pasta BASE-PUBLICA (com a subpasta PACOTES)")
    parser.add_argument("--dry-run", action="store_true", help="só mostra o que seria carregado")
    parser.add_argument(
        "--provision",
        action="store_true",
        help="entrega os pacotes aos agentes que já têm especialidade",
    )
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
