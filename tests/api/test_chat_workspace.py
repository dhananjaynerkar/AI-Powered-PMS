"""API contracts for durable chat creation, listing, rename and archive."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from typing import cast

from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pms_api.app import RagServiceProvider, create_app, get_authorization_context
from pms_case_workflow.chat_models import (
    ChatAttachment,
    ChatCitation,
    ChatIngestionStatus,
    ChatMemory,
    ChatMessage,
    ChatMessageRole,
    ChatMessageStatus,
    ChatParticipant,
    ChatRecord,
    ChatStatus,
)
from pms_case_workflow.chat_repository import ChatStore
from pms_common.security import AuthorizationContext, Classification, UserRole
from pms_retrieval.models import GroundedAnswer


class MemoryChats(ChatStore):
    def __init__(self) -> None:
        self.chats: dict[str, ChatRecord] = {}
        self.messages: list[ChatMessage] = []

    def create_chat(self, chat: ChatRecord, owner: ChatParticipant) -> None:
        del owner
        self.chats[chat.chat_id] = chat

    def get_chat(self, chat_id: str) -> ChatRecord | None:
        return self.chats.get(chat_id)

    def list_chats(self, *, include_archived: bool = False) -> tuple[ChatRecord, ...]:
        return tuple(
            chat
            for chat in self.chats.values()
            if include_archived or chat.status is not ChatStatus.ARCHIVED
        )

    def update_chat(
        self, chat_id: str, *, title: str | None = None, status: ChatStatus | None = None
    ) -> ChatRecord:
        current = self.chats[chat_id]
        updated = replace(
            current,
            title=title if title is not None else current.title,
            status=status if status is not None else current.status,
            updated_at=datetime.now(UTC),
        )
        self.chats[chat_id] = updated
        return updated

    def archive_chat(self, chat_id: str) -> ChatRecord:
        return self.update_chat(chat_id, status=ChatStatus.ARCHIVED)

    def list_messages(self, chat_id: str) -> tuple[ChatMessage, ...]:
        return tuple(message for message in self.messages if message.chat_id == chat_id)

    def get_message_by_idempotency(
        self, chat_id: str, idempotency_key: str
    ) -> ChatMessage | None:
        return next(
            (
                message
                for message in self.messages
                if message.chat_id == chat_id and message.idempotency_key == idempotency_key
            ),
            None,
        )

    def list_citations(self, message_id: str) -> tuple[ChatCitation, ...]:
        del message_id
        return ()

    def list_attachments(self, chat_id: str) -> tuple[ChatAttachment, ...]:
        del chat_id
        return ()

    def add_participant(self, participant: ChatParticipant) -> None:
        del participant

    def append_message(
        self,
        chat_id: str,
        *,
        sender_subject: str,
        role: ChatMessageRole,
        content: str,
        status: ChatMessageStatus = ChatMessageStatus.PENDING,
        sender_admin_id: int | None = None,
        model_name: str | None = None,
        route: str | None = None,
        review_required: bool = False,
        created_at: datetime,
        message_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ChatMessage:
        if idempotency_key is not None:
            existing = self.get_message_by_idempotency(chat_id, idempotency_key)
            if existing is not None:
                return existing
        message = ChatMessage(
            message_id=message_id or f"message-{len(self.messages) + 1}",
            chat_id=chat_id,
            sequence_number=len(self.messages) + 1,
            sender_subject=sender_subject,
            message_role=role,
            content=content,
            message_status=status,
            sender_admin_id=sender_admin_id,
            model_name=model_name,
            route=route,
            review_required=review_required,
            created_at=created_at,
            idempotency_key=idempotency_key,
        )
        self.messages.append(message)
        return message

    def update_message_status(
        self,
        message_id: str,
        *,
        status: ChatMessageStatus,
        completed_at: datetime | None = None,
        failure_reason: str | None = None,
    ) -> None:
        del message_id, status, completed_at, failure_reason

    def add_citations(self, citations: Iterable[ChatCitation]) -> None:
        del citations

    def add_attachment(self, attachment: ChatAttachment) -> None:
        del attachment

    def update_attachment(
        self,
        attachment_id: str,
        *,
        status: ChatIngestionStatus,
        canonical_document_id: str | None = None,
        ready_at: datetime | None = None,
        failure_reason: str | None = None,
        review_reason: str | None = None,
    ) -> None:
        del attachment_id, status, canonical_document_id, ready_at, failure_reason, review_reason

    def upsert_memory(self, memory: ChatMemory) -> None:
        del memory

    def get_memory(self, chat_id: str) -> ChatMemory | None:
        del chat_id
        return None

    def try_acquire_generation_lock(self, key: str) -> bool:
        del key
        return True


def _context(request: Request) -> AuthorizationContext:
    del request
    return AuthorizationContext(
        subject="do-user",
        roles=frozenset({UserRole.DATA_ENTRY_OPERATOR}),
        tenant_id=None,
        department_id="estate",
        unit_id="land",
        classification=Classification.INTERNAL,
    )


def test_chat_crud_is_persistent_and_soft_deletes() -> None:
    store = MemoryChats()

    @contextmanager
    def provider(context: AuthorizationContext) -> Iterator[ChatStore]:
        del context
        yield store

    app = create_app(chat_service_provider=lambda context: provider(context))
    app.dependency_overrides[get_authorization_context] = _context

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": "Bearer test"}
            created = await client.post("/api/v1/assistant/chats", headers=headers, json={})
            assert created.status_code == 201
            chat_id = created.json()["chat_id"]
            assert created.json()["title"] == "New Chat"
            renamed = await client.patch(
                f"/api/v1/assistant/chats/{chat_id}",
                headers=headers,
                json={"first_question": "Which clause addresses renewal?"},
            )
            assert renamed.status_code == 200
            assert "renewal" in renamed.json()["title"].lower()
            assert (await client.get("/api/v1/assistant/chats", headers=headers)).json()[0][
                "chat_id"
            ] == chat_id
            assert (
                await client.delete(f"/api/v1/assistant/chats/{chat_id}", headers=headers)
            ).status_code == 204
            assert (await client.get("/api/v1/assistant/chats", headers=headers)).json() == []
            assert (
                await client.get("/api/v1/assistant/chats?include_archived=true", headers=headers)
            ).json()[0]["status"] == "ARCHIVED"

    asyncio.run(scenario())


def test_streaming_policy_requests_are_locked_per_subject() -> None:
    started = Event()

    class Rag:
        def ask(self, question: str, **kwargs: object) -> GroundedAnswer:
            del question, kwargs
            started.set()
            time.sleep(0.15)
            return GroundedAnswer(answer="grounded", confidence="HIGH", review_required=False)

    @contextmanager
    def rag_provider(context: AuthorizationContext) -> Iterator[Rag]:
        del context
        yield Rag()

    app = create_app(rag_service_provider=cast(RagServiceProvider, rag_provider))
    app.dependency_overrides[get_authorization_context] = _context

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": "Bearer test", "Accept": "text/event-stream"}
            first = asyncio.create_task(
                client.post("/api/v1/policy/query", headers=headers, json={"question": "one"})
            )
            await asyncio.to_thread(started.wait, 2)
            second = await client.post(
                "/api/v1/policy/query", headers=headers, json={"question": "two"}
            )
            assert second.status_code == 409
            assert (await first).status_code == 200

    asyncio.run(scenario())


def test_streaming_persists_one_idempotent_assistant_message() -> None:
    store = MemoryChats()

    class Rag:
        def ask(self, question: str, **kwargs: object) -> GroundedAnswer:
            callback = kwargs.get("on_token")
            if callable(callback):
                callback("grounded ")
                callback("answer")
            return GroundedAnswer(
                answer="grounded answer",
                confidence="HIGH",
                review_required=False,
                model="configured-model",
            )

    @contextmanager
    def chat_provider(context: AuthorizationContext) -> Iterator[ChatStore]:
        del context
        yield store

    @contextmanager
    def rag_provider(context: AuthorizationContext) -> Iterator[Rag]:
        del context
        yield Rag()

    app = create_app(
        chat_service_provider=lambda context: chat_provider(context),
        rag_service_provider=cast(RagServiceProvider, rag_provider),
    )
    app.dependency_overrides[get_authorization_context] = _context

    async def scenario() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": "Bearer test"}
            created = await client.post("/api/v1/assistant/chats", headers=headers, json={})
            chat_id = created.json()["chat_id"]
            stream_headers = {**headers, "Accept": "text/event-stream"}
            body = {
                "question": "What is the policy?",
                "chat_id": chat_id,
                "idempotency_key": "submission-1",
            }
            first = await client.post(
                "/api/v1/policy/query", headers=stream_headers, json=body
            )
            assert first.status_code == 200
            assert "event: accepted" in first.text
            assert 'event: token\ndata: {"delta": "grounded "}' in first.text
            assert "event: final" in first.text
            assert len(store.messages) == 2
            assert (
                sum(
                    message.message_role is ChatMessageRole.ASSISTANT
                    for message in store.messages
                )
                == 1
            )

            replay = await client.post(
                "/api/v1/policy/query", headers=stream_headers, json=body
            )
            assert replay.status_code == 200
            assert "grounded answer" in replay.text
            assert len(store.messages) == 2

    asyncio.run(scenario())
