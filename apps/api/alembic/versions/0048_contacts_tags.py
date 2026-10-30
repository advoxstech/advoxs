"""Contacts, labels and persistent human-only routing.

Revision ID: 0048
Revises: 0047
"""

from pathlib import Path

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split("\n-- statement\n"):
        op.execute(statement)


def downgrade():
    op.execute("DROP TRIGGER contact_delivery_guard ON conversations")
    op.execute("DROP TRIGGER contact_routing ON conversations")
    op.execute("DROP FUNCTION contact_cancel_deliveries()")
    op.execute("DROP FUNCTION contact_route_conversation()")
    op.execute("DROP TRIGGER contact_sync ON contacts")
    op.execute("DROP TRIGGER contact_lock ON contacts")
    op.execute("DROP FUNCTION contact_sync_conversations()")
    op.execute("DROP FUNCTION contact_lock_phone()")
    op.execute("ALTER TABLE conversations DROP COLUMN contact_id, DROP COLUMN human_only")
    op.execute("DROP TABLE contact_tag_links, contact_tags, contacts")
    op.execute("DROP FUNCTION contact_phone_key(text)")
