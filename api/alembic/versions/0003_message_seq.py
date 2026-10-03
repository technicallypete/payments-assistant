"""messages.seq: insertion order. Timestamps tie (one turn writes tool rows and the reply at
the same instant), so transcripts are ordered by this identity column instead.

Identity columns need no separate sequence grant: INSERT on the table is enough for api and bot.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN seq bigint GENERATED ALWAYS AS IDENTITY")
    op.execute("CREATE INDEX messages_conversation_seq_idx ON messages (conversation_id, seq)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS messages_conversation_seq_idx")
    op.execute("ALTER TABLE messages DROP COLUMN seq")
