"""Phase 9 API contracts for database-authorized DO/NO/HOD chat sharing."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pms_api.app import create_app, get_authorization_context
from pms_api.staff_directory import StaffDirectoryError, StaffRecipient
from pms_case_workflow.chat_models import (
    ChatHandoffAction,
    ChatHandoffEvent,
    ChatParticipant,
    ChatRecord,
    ChatStatus,
    ChatType,
)
from pms_case_workflow.chat_repository import ChatStore
from pms_common.security import AuthorizationContext, Classification, UserRole


class MemoryHandoffStore(ChatStore):
    def __init__(self) -> None:
        self.chats: dict[str, ChatRecord] = {}
        self.participants: dict[str, list[ChatParticipant]] = {}
        self.events: dict[str, list[ChatHandoffEvent]] = {}

    def create_chat(self, chat: ChatRecord, owner: ChatParticipant) -> None:
        self.chats[chat.chat_id] = replace(chat, current_owner_subject=chat.owner_subject)
        self.participants[chat.chat_id] = [owner]
        self.events[chat.chat_id] = []

    def get_chat(self, chat_id: str) -> ChatRecord | None:
        return self.chats.get(chat_id)

    def list_chats(self, *, include_archived: bool = False) -> tuple[ChatRecord, ...]:
        return tuple(
            c
            for c in self.chats.values()
            if include_archived or c.status is not ChatStatus.ARCHIVED
        )

    def update_chat(
        self, chat_id: str, *, title: str | None = None, status: ChatStatus | None = None
    ) -> ChatRecord:
        current = self.chats[chat_id]
        updated = replace(current, title=title or current.title, status=status or current.status)
        self.chats[chat_id] = updated
        return updated

    def archive_chat(self, chat_id: str) -> ChatRecord:
        return self.update_chat(chat_id, status=ChatStatus.ARCHIVED)

    def list_participants(self, chat_id: str) -> tuple[ChatParticipant, ...]:
        return tuple(self.participants.get(chat_id, ()))

    def list_handoff_events(self, chat_id: str) -> tuple[ChatHandoffEvent, ...]:
        return tuple(self.events.get(chat_id, ()))

    def apply_handoff(
        self, event: ChatHandoffEvent, *, recipient: ChatParticipant, case_id: str | None = None
    ) -> ChatRecord:
        current = self.chats[event.chat_id]
        if (current.current_owner_subject or current.owner_subject) != event.actor_subject:
            raise PermissionError("not current owner")
        if current.chat_type is ChatType.PERSONAL:
            if case_id is None:
                raise ValueError("a case_id is required")
            current = replace(current, chat_type=ChatType.SHARED_CASE, case_id=case_id)
        updated = replace(
            current, current_owner_subject=event.recipient_subject, updated_at=event.created_at
        )
        self.chats[event.chat_id] = updated
        self.participants[event.chat_id].append(recipient)
        self.events[event.chat_id].append(event)
        return updated

    def list_messages(self, chat_id: str):
        return ()

    def get_message_by_idempotency(self, chat_id: str, idempotency_key: str):
        return None

    def list_citations(self, message_id: str):
        return ()

    def list_attachments(self, chat_id: str):
        return ()

    def add_participant(self, participant: ChatParticipant) -> None:
        self.participants.setdefault(participant.chat_id, []).append(participant)

    def append_message(self, *args, **kwargs):
        raise NotImplementedError

    def update_message_status(self, *args, **kwargs) -> None:
        return None

    def add_citations(self, citations) -> None:
        return None

    def add_attachment(self, attachment) -> None:
        return None

    def remove_attachment(self, attachment_id: str) -> None:
        return None

    def update_attachment(self, *args, **kwargs) -> None:
        return None

    def upsert_memory(self, memory) -> None:
        return None

    def get_memory(self, chat_id: str):
        return None

    def try_acquire_generation_lock(self, key: str) -> bool:
        return True


class Directory:
    def require_recipient(
        self, context: AuthorizationContext, *, role: UserRole, subject: str
    ) -> StaffRecipient:
        if subject != "local.no":
            raise StaffDirectoryError("recipient unavailable")
        return StaffRecipient("local.no", "Nodal Officer", "no", "Nodal", role)

    def recipients(self, context: AuthorizationContext, *, role: UserRole):
        return (self.require_recipient(context, role=role, subject="local.no"),)


def _context(
    request: Request, role: UserRole = UserRole.DATA_ENTRY_OPERATOR
) -> AuthorizationContext:
    del request
    return AuthorizationContext(
        subject="local.do" if role is UserRole.DATA_ENTRY_OPERATOR else "local.no",
        roles=frozenset({role}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.INTERNAL,
    )


def _app(store: MemoryHandoffStore):
    @contextmanager
    def provider(context: AuthorizationContext) -> Iterator[ChatStore]:
        del context
        yield store

    app = create_app(
        chat_service_provider=lambda context: provider(context), staff_directory=Directory()
    )
    app.dependency_overrides[get_authorization_context] = _context
    return app


def test_private_chat_requires_explicit_case_confirmation() -> None:
    store = MemoryHandoffStore()
    app = _app(store)

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/api/v1/assistant/chats", json={})
            response = await client.post(
                f"/api/v1/assistant/chats/{created.json()['chat_id']}/handoff",
                json={
                    "action": "SHARE",
                    "recipient_subject": "local.no",
                    "recipient_role": "Nodal/Regional Officer",
                    "remarks": "Review",
                },
            )
            assert response.status_code == 409

    asyncio.run(scenario())


def test_handoff_retains_chat_id_and_records_event() -> None:
    store = MemoryHandoffStore()
    app = _app(store)

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/assistant/chats", json={"chat_type": "SHARED_CASE", "case_id": "case-1"}
            )
            chat_id = created.json()["chat_id"]
            response = await client.post(
                f"/api/v1/assistant/chats/{chat_id}/handoff",
                json={
                    "action": "SUBMIT_TO_NO",
                    "recipient_subject": "local.no",
                    "remarks": "Please review",
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["chat_id"] == chat_id
            assert body["current_owner_subject"] == "local.no"
            assert body["handoff_events"][0]["action"] == ChatHandoffAction.SUBMIT_TO_NO.value
            assert body["participants"][-1]["participant_subject"] == "local.no"

    asyncio.run(scenario())


def test_ineligible_recipient_is_rejected() -> None:
    store = MemoryHandoffStore()
    app = _app(store)

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/assistant/chats", json={"chat_type": "SHARED_CASE", "case_id": "case-1"}
            )
            response = await client.post(
                f"/api/v1/assistant/chats/{created.json()['chat_id']}/handoff",
                json={
                    "action": "SUBMIT_TO_NO",
                    "recipient_subject": "local.disabled",
                    "remarks": "Review",
                },
            )
            assert response.status_code == 403

    asyncio.run(scenario())


def test_migration_is_scoped_and_rls_protected() -> None:
    source = Path(
        "db/migrations/versions/20260807_0018_chat_handoff_events.py"
    ).read_text(encoding="utf-8")
    assert "pms_chat" in source
    assert "public.admin_users" not in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "GRANT SELECT, INSERT ON" in source
    assert "chat_handoff_event" in source
    assert "DROP SCHEMA public" not in source
    assert source.index("op.create_table(") < source.index(
        "ALTER TABLE {SCHEMA}.chat_handoff_event ENABLE ROW LEVEL SECURITY"
    )
    assert source.count("CREATE POLICY chat_participant_insert") == 2
    assert source.count("CREATE POLICY chat_participant_update") == 2
