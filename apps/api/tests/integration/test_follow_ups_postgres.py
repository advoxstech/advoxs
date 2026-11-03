"""Real PostgreSQL checks for migration 0049: constraints, cascade and tenant isolation."""

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import Base

VERSIONS = Path(__file__).parents[2] / "alembic/versions"


async def _apply(conn, name: str) -> None:
    for statement in (VERSIONS / name).read_text().split("\n-- statement\n"):
        await conn.execute(text(statement))


@pytest.fixture
async def db():
    url = os.getenv("CONTACTS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires a disposable PostgreSQL via CONTACTS_TEST_DATABASE_URL")
    schema = "follow_ups_test_" + uuid.uuid4().hex
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as c:
        await c.execute(text(f'CREATE SCHEMA "{schema}"'))
        await c.run_sync(Base.metadata.create_all)
        # Tables owned by migrations 0048/0049 are rebuilt from their SQL files.
        await c.execute(text("DROP TABLE follow_up_recipients, follow_up_batches"))
        await c.execute(
            text("ALTER TABLE conversations DROP COLUMN contact_id, DROP COLUMN human_only")
        )
        await c.execute(text("DROP TABLE contact_tag_links, contacts, contact_tags"))
        await _apply(c, "0048_contacts_tags.sql")
        await _apply(c, "0049_follow_ups.sql")
    try:
        yield engine
    finally:
        async with engine.begin() as c:
            await c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


async def _seed(db) -> dict:
    ids = {k: uuid.uuid4() for k in ("tenant", "other", "batch")}
    async with db.begin() as c:
        await c.execute(
            text(
                "INSERT INTO tenants(id,name,email_contato) VALUES "
                "(:tenant,'Office','one@example.com'),(:other,'Other','two@example.com')"
            ),
            ids,
        )
        await c.execute(
            text(
                "INSERT INTO follow_up_batches(id,tenant_id,provider,text) "
                "VALUES(:batch,:tenant,'zapi','Oi {{nome}}')"
            ),
            ids,
        )
    return ids


async def test_lote_exige_conteudo_do_provedor(db) -> None:
    ids = await _seed(db)

    with pytest.raises(IntegrityError):
        async with db.begin() as c:
            await c.execute(
                text("INSERT INTO follow_up_batches(tenant_id,provider) VALUES(:tenant,'meta')"),
                ids,
            )


async def test_numero_repetido_no_lote_e_recusado(db) -> None:
    ids = await _seed(db)
    insert = text(
        "INSERT INTO follow_up_recipients(tenant_id,batch_id,phone) "
        "VALUES(:tenant,:batch,'5579999990000')"
    )
    async with db.begin() as c:
        await c.execute(insert, ids)

    with pytest.raises(IntegrityError):
        async with db.begin() as c:
            await c.execute(insert, ids)


async def test_destinatario_nao_aponta_lote_de_outro_escritorio(db) -> None:
    ids = await _seed(db)

    with pytest.raises(IntegrityError):
        async with db.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO follow_up_recipients(tenant_id,batch_id,phone) "
                    "VALUES(:other,:batch,'5579999990000')"
                ),
                ids,
            )


async def test_excluir_lote_remove_destinatarios(db) -> None:
    ids = await _seed(db)
    async with db.begin() as c:
        await c.execute(
            text(
                "INSERT INTO follow_up_recipients(tenant_id,batch_id,phone) "
                "VALUES(:tenant,:batch,'5579999990000')"
            ),
            ids,
        )
        await c.execute(text("DELETE FROM follow_up_batches WHERE id=:batch"), ids)
        remaining = await c.scalar(text("SELECT count(*) FROM follow_up_recipients"))

    assert remaining == 0


async def test_rls_isola_lotes_por_escritorio(db) -> None:
    ids = await _seed(db)
    role = f"follow_ups_reader_{uuid.uuid4().hex}"
    async with db.begin() as c:
        await c.execute(text(f'CREATE ROLE "{role}"'))
        schema = await c.scalar(text("SELECT current_schema()"))
        await c.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
        await c.execute(text(f'GRANT SELECT ON follow_up_batches TO "{role}"'))
    try:
        async with db.begin() as c:
            await c.execute(text(f'SET LOCAL ROLE "{role}"'))
            await c.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(ids["other"])}
            )
            hidden = await c.scalar(text("SELECT count(*) FROM follow_up_batches"))
            await c.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(ids["tenant"])}
            )
            visible = await c.scalar(text("SELECT count(*) FROM follow_up_batches"))
    finally:
        async with db.begin() as c:
            await c.execute(text(f'DROP OWNED BY "{role}"'))
            await c.execute(text(f'DROP ROLE "{role}"'))

    assert (hidden, visible) == (0, 1)
