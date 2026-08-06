"""Focused Phase 2 tests for durable chat contracts and migration safety."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pms_case_workflow.chat_models import (
    ChatAccessMode,
    ChatMessageRole,
    ChatParticipant,
    ChatRecord,
    ChatStatus,
    ChatType,
)
from pms_case_workflow.chat_repository import PostgresChatStore
from pms_common.security import AuthorizationContext, Classification, UserRole

MIGRATION = Path("db/migrations/versions/20260806_0016_persistent_chat_workspace.py")


class _Result:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row

    def mappings(self) -> _Result:
        return self

    def one(self) -> dict[str, Any]:
        return self._row or {"next_sequence": 1}

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row


class _Connection:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.params: list[dict[str, Any] | None] = []

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        self.sql.append(str(statement))
        self.params.append(params)
        if "COALESCE(MAX(sequence_number)" in str(statement):
            return _Result({"next_sequence": 1})
        return _Result()


def _context() -> AuthorizationContext:
    return AuthorizationContext(
        subject="local.owner",
        roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.INTERNAL,
    )


def test_phase2_migration_declares_complete_persistence_model() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in (
        '"chat"',
        '"chat_participant"',
        '"chat_message"',
        '"chat_message_citation"',
        '"chat_attachment"',
        '"chat_memory"',
    ):
        assert f'op.create_table(\n        {table}' in source
    for required in (
        "uq_chat_message_chat_sequence",
        "uq_chat_attachment_checksum",
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "SECURITY DEFINER",
        "SET search_path = {SCHEMA}, pg_catalog",
        "REVOKE ALL ON FUNCTION",
        "GRANT USAGE ON SCHEMA",
        "GRANT SELECT, INSERT, UPDATE",
    ):
        assert required in source
    assert "public.admin_users.admin_id" in source
    assert "pms_extract_2010_2023" not in source
    assert "DROP SCHEMA public" not in source


def test_create_chat_initializes_owner_and_memory_in_one_connection() -> None:
    connection = _Connection()
    store = PostgresChatStore(connection, _context())
    now = datetime.now(UTC)
    chat = ChatRecord(
        chat_id="chat-1",
        owner_subject="local.owner",
        title="Lease review",
        chat_type=ChatType.PERSONAL,
        status=ChatStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    owner = ChatParticipant(
        chat_id="chat-1",
        participant_subject="local.owner",
        participant_role="Data Entry Operator",
        access_mode=ChatAccessMode.OWNER,
        added_by_subject="local.owner",
        added_at=now,
    )

    store.create_chat(chat, owner)

    inserts = [sql for sql in connection.sql if "INSERT INTO pms_chat" in sql]
    assert len(inserts) == 3
    assert any("pms_chat.chat_memory" in sql for sql in inserts)


def test_blank_message_is_rejected_before_database_write() -> None:
    connection = _Connection()
    store = PostgresChatStore(connection, _context())

    with pytest.raises(ValueError, match="cannot be empty"):
        store.append_message(
            "chat-1",
            sender_subject="local.owner",
            role=ChatMessageRole.USER,
            content="   ",
            created_at=datetime.now(UTC),
        )

    assert not any("INSERT INTO pms_chat.chat_message" in sql for sql in connection.sql)
