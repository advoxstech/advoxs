"""Real PostgreSQL tests: first message routing, concurrency and tenant isolation."""

import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import get_current_tenant, get_tenant_session
from app.main import app
from app.models import Base


@pytest.fixture
async def db():
    url = os.getenv("CONTACTS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires a disposable PostgreSQL via CONTACTS_TEST_DATABASE_URL")
    schema = "contacts_test_" + uuid.uuid4().hex
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as c:
        await c.execute(text(f'CREATE SCHEMA "{schema}"'))
        await c.run_sync(Base.metadata.create_all)
        # This association is maintained by SQL migrations, not ORM metadata.
        await c.execute(
            text("CREATE TABLE agent_lawyers (tenant_id uuid, agent_id uuid, user_id uuid)")
        )
        await c.execute(
            text("ALTER TABLE conversations DROP COLUMN contact_id, DROP COLUMN human_only")
        )
        await c.execute(text("DROP TABLE contact_tag_links, contacts, contact_tags"))
        sql = Path(__file__).parents[2] / "alembic/versions/0048_contacts_tags.sql"
        for statement in sql.read_text().split("\n-- statement\n"):
            await c.execute(text(statement))
    try:
        yield engine
    finally:
        async with engine.begin() as c:
            await c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


async def seed(db):
    ids = {k: uuid.uuid4() for k in ("tenant", "other", "contact", "tag", "chat", "message", "job")}
    async with db.begin() as c:
        await c.execute(
            text(
                "INSERT INTO tenants(id,name,email_contato) VALUES "
                "(:tenant,'Office','one@example.com'),(:other,'Other','two@example.com')"
            ),
            ids,
        )
    return ids


async def add_contact(c, ids):
    await c.execute(
        text(
            "INSERT INTO contacts(id,tenant_id,phone_number,human_only) "
            "VALUES(:contact,:tenant,'557996028480',true)"
        ),
        ids,
    )


async def add_chat(c, ids):
    await c.execute(
        text(
            "INSERT INTO conversations(id,tenant_id,contact_phone_number) "
            "VALUES(:chat,:tenant,'+55 (79) 9602-8480')"
        ),
        ids,
    )


async def test_first_message_and_isolation(db):
    ids = await seed(db)
    async with db.begin() as c:
        await add_contact(c, ids)
        await add_chat(c, ids)
        row = (
            await c.execute(
                text("SELECT state,human_only,contact_id FROM conversations WHERE id=:chat"), ids
            )
        ).one()
        assert (row.state, row.human_only, row.contact_id) == ("human", True, ids["contact"])
        # Same phone in another office must not inherit the rule.
        await c.execute(
            text(
                "INSERT INTO conversations(tenant_id,contact_phone_number) "
                "VALUES(:other,'+55 (79) 9602-8480')"
            ),
            ids,
        )
        assert (
            await c.execute(text("SELECT state FROM conversations WHERE tenant_id=:other"), ids)
        ).scalar_one() == "agent"
        # A playground/test conversation must never inherit contact routing.
        await c.execute(
            text(
                "INSERT INTO conversations(tenant_id,contact_phone_number,is_test) "
                "VALUES(:tenant,'557996028480',true)"
            ),
            ids,
        )
        assert (
            await c.execute(text("SELECT state FROM conversations WHERE is_test"))
        ).scalar_one() == "agent"


async def test_existing_conversation_cancels_pending_and_requires_explicit_return(db):
    ids = await seed(db)
    async with db.begin() as c:
        await add_chat(c, ids)
        await c.execute(
            text(
                "INSERT INTO messages(id,tenant_id,conversation_id,"
                "sender_type,content,delivery_status) "
                "VALUES(:message,:tenant,:chat,'agent','Resposta','pending')"
            ),
            ids,
        )
        await c.execute(
            text(
                "INSERT INTO outbound_message_jobs(id,tenant_id,conversation_id,message_id,status) "
                "VALUES(:job,:tenant,:chat,:message,'pending')"
            ),
            ids,
        )
        await add_contact(c, ids)
        assert (
            await c.execute(text("SELECT status FROM outbound_message_jobs"))
        ).scalar_one() == "cancelled"
        assert (
            await c.execute(text("SELECT delivery_status FROM messages"))
        ).scalar_one() == "cancelled"
        await c.execute(text("UPDATE conversations SET state='agent' WHERE id=:chat"), ids)
        assert (await c.execute(text("SELECT state FROM conversations"))).scalar_one() == "human"
        await c.execute(text("UPDATE contacts SET human_only=false WHERE id=:contact"), ids)
        assert (await c.execute(text("SELECT state FROM conversations"))).scalar_one() == "human"
        await c.execute(text("UPDATE conversations SET state='agent' WHERE id=:chat"), ids)
        assert (await c.execute(text("SELECT state FROM conversations"))).scalar_one() == "agent"


async def test_delete_chat_preserves_rule_and_remove_contact_preserves_history(db):
    ids = await seed(db)
    async with db.begin() as c:
        await add_contact(c, ids)
        await add_chat(c, ids)
        await c.execute(text("DELETE FROM conversations WHERE id=:chat"), ids)
        await add_chat(c, ids)
        assert (await c.execute(text("SELECT human_only FROM conversations"))).scalar_one() is True
        await c.execute(text("DELETE FROM contacts WHERE id=:contact"), ids)
        row = (await c.execute(text("SELECT state,human_only,contact_id FROM conversations"))).one()
        assert tuple(row) == ("human", False, None)


async def test_tags_cannot_link_other_office_and_rls(db):
    ids = await seed(db)
    role = "contacts_reader_" + uuid.uuid4().hex
    async with db.begin() as c:
        await add_contact(c, ids)
        await c.execute(
            text("INSERT INTO contact_tags(id,tenant_id,name) VALUES(:tag,:other,'Private')"), ids
        )
    with pytest.raises(IntegrityError):
        async with db.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO contact_tag_links(tenant_id,contact_id,tag_id) "
                    "VALUES(:tenant,:contact,:tag)"
                ),
                ids,
            )
    try:
        async with db.begin() as c:
            schema = (await c.execute(text("SELECT current_schema()"))).scalar_one()
            await c.execute(text(f'CREATE ROLE "{role}"'))
            await c.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
            await c.execute(text(f'GRANT SELECT ON contacts,contact_tags TO "{role}"'))
        async with db.begin() as c:
            await c.execute(text(f'SET LOCAL ROLE "{role}"'))
            await c.execute(
                text("SELECT set_config('app.tenant_id',:tid,true)"), {"tid": str(ids["other"])}
            )
            assert (await c.execute(text("SELECT count(*) FROM contacts"))).scalar_one() == 0
            assert (await c.execute(text("SELECT count(*) FROM contact_tags"))).scalar_one() == 1
    finally:
        async with db.begin() as c:
            await c.execute(text(f'DROP OWNED BY "{role}"'))
            await c.execute(text(f'DROP ROLE "{role}"'))


async def test_first_message_waits_for_contact_registration(db):
    ids = await seed(db)
    started = asyncio.Event()
    async with db.begin() as c:
        await add_contact(c, ids)

        async def receive():
            async with db.begin() as other:
                started.set()
                await add_chat(other, ids)

        task = asyncio.create_task(receive())
        await started.wait()
        await asyncio.sleep(0.1)
        assert not task.done()
    await asyncio.wait_for(task, 5)
    async with db.connect() as c:
        assert (await c.execute(text("SELECT state FROM conversations"))).scalar_one() == "human"


async def test_registration_waits_for_first_message_and_updates_it(db):
    ids = await seed(db)
    started = asyncio.Event()
    async with db.begin() as c:
        await add_chat(c, ids)

        async def register():
            async with db.begin() as other:
                started.set()
                await add_contact(other, ids)

        task = asyncio.create_task(register())
        await started.wait()
        await asyncio.sleep(0.1)
        assert not task.done()
    await asyncio.wait_for(task, 5)
    async with db.connect() as c:
        assert (await c.execute(text("SELECT state FROM conversations"))).scalar_one() == "human"


@pytest.fixture
async def api(db):
    ids = await seed(db)
    ctx = SimpleNamespace(tenant_id=ids["tenant"], role="admin", user_id=uuid.uuid4(), name="Admin")
    factory = async_sessionmaker(db, expire_on_commit=False)

    async def session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_current_tenant] = lambda: ctx
    app.dependency_overrides[get_tenant_session] = session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/"
        ) as client:
            yield client, ctx, ids
    finally:
        app.dependency_overrides.clear()


async def test_contacts_crud_permissions_and_tenant_isolation(api):
    client, ctx, ids = api
    tag = await client.post("contact-tags", json={"name": "Suporte", "color": "#2563EB"})
    assert tag.status_code == 201, tag.text
    tag_id = tag.json()["id"]
    body = {
        "name": "Escritório cliente",
        "phone_number": "+55 (79) 9602-8480",
        "human_only": True,
        "tag_ids": [tag_id],
    }
    created = await client.post("contacts", json=body)
    assert created.status_code == 201, created.text
    contact_id = created.json()["id"]
    assert created.json()["phone_number"] == "557996028480"
    assert created.json()["tags"][0]["name"] == "Suporte"
    assert (await client.post("contacts", json=body)).status_code == 409
    ctx.tenant_id = ids["other"]
    assert (await client.get(f"contacts/{contact_id}")).status_code == 404
    assert (await client.put(f"contacts/{contact_id}", json=body)).status_code == 404
    assert (await client.delete(f"contact-tags/{tag_id}")).status_code == 404
    assert (await client.post("contacts", json=body)).status_code == 422
    assert (await client.get("contacts")).json() == []
    ctx.tenant_id = ids["tenant"]
    ctx.role = "lawyer"
    assert (await client.get("contacts")).status_code == 403
    assert (await client.post("contacts", json=body)).status_code == 403
    assert (
        await client.put(f"contact-tags/{tag_id}", json={"name": "Renomeada"})
    ).status_code == 403
    assert (await client.get("contact-tags")).status_code == 200
    ctx.role = "admin"
    body.update(human_only=False, tag_ids=[])
    assert (await client.put(f"contacts/{contact_id}", json=body)).json()["tags"] == []
    assert (await client.delete(f"contacts/{contact_id}")).status_code == 204
    assert (await client.delete(f"contact-tags/{tag_id}")).status_code == 204


async def test_manual_rule_blocks_return_and_attachment_respects_scope(api, db, monkeypatch):
    from app.api.v1 import conversations as routes

    client, ctx, ids = api
    async with db.begin() as c:
        await add_contact(c, ids)
        await add_chat(c, ids)
        await c.execute(
            text(
                "INSERT INTO messages(id,tenant_id,conversation_id,"
                "sender_type,content,media_url,media_type) "
                "VALUES(:message,:tenant,:chat,'contact','Documento','12345','application/pdf')"
            ),
            ids,
        )
        await c.execute(
            text(
                "INSERT INTO whatsapp_numbers(tenant_id,provider,phone_number_id,waba_id,"
                "access_token_encrypted,display_phone_number) "
                "VALUES(:tenant,'meta','phone','waba','enc','5511999999999')"
            ),
            ids,
        )
    assert (
        await client.patch(f"conversations/{ids['chat']}", json={"state": "agent"})
    ).status_code == 409
    download = AsyncMock(return_value=b"test-pdf")
    monkeypatch.setattr(routes, "download_contact_media", download)
    monkeypatch.setattr(routes, "decrypt_access_token", lambda value: "token")
    path = f"conversations/{ids['chat']}/messages/{ids['message']}/attachment"
    result = await client.get(path)
    assert result.status_code == 200
    assert result.content == b"test-pdf"
    assert "attachment" in result.headers["content-disposition"]
    ctx.tenant_id = ids["other"]
    assert (await client.get(path)).status_code == 404
    ctx.tenant_id = ids["tenant"]
    ctx.role = "lawyer"
    assert (await client.get(path)).status_code == 404
    download.assert_awaited_once()


async def test_tag_filter_is_before_pagination_and_preserves_lawyer_scope(api, db):
    client, ctx, ids = api
    tag = (await client.post("contact-tags", json={"name": "Suporte"})).json()
    for i in range(4):
        body = {"phone_number": f"551199999990{i}", "tag_ids": [tag["id"]] if i % 2 == 0 else []}
        assert (await client.post("contacts", json=body)).status_code == 201
        async with db.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO conversations(tenant_id,contact_phone_number,last_message_at) "
                    "VALUES(:tenant,:phone,now()+make_interval(secs=>:seconds))"
                ),
                {"tenant": ids["tenant"], "phone": body["phone_number"], "seconds": i},
            )
    response = await client.get(f"conversations?tag_id={tag['id']}&limit=1&offset=1")
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["contact_phone_number"] == "5511999999900"
    assert response.json()[0]["tags"][0]["name"] == "Suporte"
    assert len((await client.get("conversations?untagged=true")).json()) == 2
    ctx.role = "lawyer"
    assert (await client.get(f"conversations?tag_id={tag['id']}")).json() == []
    ctx.role = "admin"
    await client.delete(f"contact-tags/{tag['id']}")
    assert len((await client.get("conversations?untagged=true")).json()) == 4


@pytest.mark.parametrize("provider", ["meta", "zapi"])
async def test_real_inbound_persists_media_and_routes_without_automation(db, provider):
    from app.models import WhatsAppNumber
    from app.services.whatsapp_inbound import handle_meta_webhook, handle_zapi_webhook

    ids = await seed(db)
    async with db.begin() as c:
        await add_contact(c, ids)
    factory = async_sessionmaker(db, expire_on_commit=False)
    async with factory() as s:
        number = WhatsAppNumber(
            tenant_id=ids["tenant"],
            provider=provider,
            status="connected",
            display_phone_number="5511999999999",
        )
        if provider == "meta":
            number.phone_number_id = "phone-id"
            number.waba_id = "waba"
            number.access_token_encrypted = "encrypted"
        else:
            number.zapi_instance_id = "instance"
            number.zapi_instance_token_encrypted = "encrypted"
            number.zapi_webhook_secret = "secret"
        s.add(number)
        await s.commit()
        arq = AsyncMock()
        if provider == "meta":
            payload = {
                "entry": [
                    {
                        "changes": [
                            {
                                "field": "messages",
                                "value": {
                                    "metadata": {"phone_number_id": "phone-id"},
                                    "messages": [
                                        {
                                            "id": "wa-1",
                                            "from": "557996028480",
                                            "type": "document",
                                            "document": {
                                                "id": "12345",
                                                "mime_type": "application/pdf",
                                                "filename": "doc.pdf",
                                            },
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                ]
            }
            result = await handle_meta_webhook(payload, s, arq)
        else:
            payload = {
                "instanceId": "instance",
                "phone": "557996028480",
                "messageId": "wa-1",
                "fromMe": False,
                "isGroup": False,
                "document": {
                    "documentUrl": "https://storage.googleapis.com/example/doc.pdf",
                    "mimeType": "application/pdf",
                    "fileName": "doc.pdf",
                },
            }
            result = await handle_zapi_webhook(payload, "secret", s, arq)
        assert result == {"received": 1}
        row = (
            await s.execute(text("SELECT state,human_only,automation_status FROM conversations"))
        ).one()
        assert tuple(row) == ("human", True, "idle")
        assert (
            await s.execute(
                text(
                    "SELECT count(*) FROM messages WHERE sender_type='contact' "
                    "AND media_url IS NOT NULL"
                )
            )
        ).scalar_one() == 1
        arq.enqueue_job.assert_awaited_once()
