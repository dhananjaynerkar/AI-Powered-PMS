"""Persist an ingestion job identifier for chat PDF processing.

The attachment row remains the authoritative job state.  The identifier lets
the API return a stable polling handle without introducing a second queue or
changing any public business schema.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0017"
down_revision: str | None = "20260806_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_attachment",
        sa.Column("ingestion_job_id", sa.Text(), nullable=True),
        schema="pms_chat",
    )
    op.create_unique_constraint(
        "uq_chat_attachment_ingestion_job",
        "chat_attachment",
        ["ingestion_job_id"],
        schema="pms_chat",
    )
    op.create_index(
        "ix_chat_attachment_ingestion_job",
        "chat_attachment",
        ["ingestion_job_id"],
        schema="pms_chat",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_attachment_ingestion_job",
        table_name="chat_attachment",
        schema="pms_chat",
    )
    op.drop_constraint(
        "uq_chat_attachment_ingestion_job",
        "chat_attachment",
        schema="pms_chat",
        type_="unique",
    )
    op.drop_column("chat_attachment", "ingestion_job_id", schema="pms_chat")
