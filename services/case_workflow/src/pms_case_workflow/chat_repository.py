"""Parameterized PostgreSQL persistence for personal and shared chats.

The store is deliberately transaction-scoped: callers create it with a
SQLAlchemy ``Connection`` obtained from ``Engine.begin()``.  Chat creation,
participant initialization and memory initialization therefore commit or roll
back together, while PostgreSQL RLS remains the final authorization boundary.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from pms_common.security import AuthorizationContext, Classification, apply_postgres_session_context
from sqlalchemy import Connection, text

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
    ChatType,
)


class ChatStore(Protocol):
    """Persistence operations used by the chat service/API layer."""

    def create_chat(self, chat: ChatRecord, owner: ChatParticipant) -> None: ...

    def get_chat(self, chat_id: str) -> ChatRecord | None: ...

    def list_chats(self, *, include_archived: bool = False) -> tuple[ChatRecord, ...]: ...

    def update_chat(
        self,
        chat_id: str,
        *,
        title: str | None = None,
        status: ChatStatus | None = None,
    ) -> ChatRecord: ...

    def archive_chat(self, chat_id: str) -> ChatRecord: ...

    def list_messages(self, chat_id: str) -> tuple[ChatMessage, ...]: ...

    def list_citations(self, message_id: str) -> tuple[ChatCitation, ...]: ...

    def list_attachments(self, chat_id: str) -> tuple[ChatAttachment, ...]: ...

    def add_participant(self, participant: ChatParticipant) -> None: ...

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
    ) -> ChatMessage: ...

    def update_message_status(
        self,
        message_id: str,
        *,
        status: ChatMessageStatus,
        completed_at: datetime | None = None,
        failure_reason: str | None = None,
    ) -> None: ...

    def add_citations(self, citations: Iterable[ChatCitation]) -> None: ...

    def add_attachment(self, attachment: ChatAttachment) -> None: ...

    def update_attachment(
        self,
        attachment_id: str,
        *,
        status: ChatIngestionStatus,
        canonical_document_id: str | None = None,
        ready_at: datetime | None = None,
        failure_reason: str | None = None,
        review_reason: str | None = None,
    ) -> None: ...

    def upsert_memory(self, memory: ChatMemory) -> None: ...

    def get_memory(self, chat_id: str) -> ChatMemory | None: ...

    def try_acquire_generation_lock(self, key: str) -> bool: ...


def _chat_from_row(row: Mapping[Any, Any]) -> ChatRecord:
    return ChatRecord(
        chat_id=str(row["chat_id"]),
        owner_subject=str(row["owner_subject"]),
        title=str(row["title"]),
        chat_type=ChatType(str(row["chat_type"])),
        status=ChatStatus(str(row["status"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_message_at=row["last_message_at"],
        owner_admin_id=row["owner_admin_id"],
        case_id=row["case_id"],
    )


def _message_from_row(row: Mapping[Any, Any]) -> ChatMessage:
    return ChatMessage(
        message_id=str(row["message_id"]),
        chat_id=str(row["chat_id"]),
        sequence_number=int(row["sequence_number"]),
        sender_subject=str(row["sender_subject"]),
        message_role=ChatMessageRole(str(row["message_role"])),
        content=str(row["content"]),
        message_status=ChatMessageStatus(str(row["message_status"])),
        created_at=row["created_at"],
        sender_admin_id=row["sender_admin_id"],
        model_name=row["model_name"],
        route=row["route"],
        review_required=bool(row["review_required"]),
        completed_at=row["completed_at"],
        failure_reason=row["failure_reason"],
    )


class PostgresChatStore:
    """RLS-scoped chat repository; one instance belongs to one transaction."""

    def __init__(self, connection: Connection, context: AuthorizationContext) -> None:
        self._connection = connection
        self._context = context
        apply_postgres_session_context(connection, context)

    def create_chat(self, chat: ChatRecord, owner: ChatParticipant) -> None:
        if owner.chat_id != chat.chat_id or owner.participant_subject != chat.owner_subject:
            raise ValueError("chat owner participant does not match chat owner")
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.chat "
                "(chat_id, owner_subject, owner_admin_id, title, chat_type, status, case_id, "
                "created_at, updated_at, last_message_at) VALUES "
                "(:chat_id, :owner_subject, :owner_admin_id, :title, :chat_type, :status, "
                ":case_id, :created_at, :updated_at, :last_message_at)"
            ),
            {
                "chat_id": chat.chat_id,
                "owner_subject": chat.owner_subject,
                "owner_admin_id": chat.owner_admin_id,
                "title": chat.title,
                "chat_type": chat.chat_type.value,
                "status": chat.status.value,
                "case_id": chat.case_id,
                "created_at": chat.created_at,
                "updated_at": chat.updated_at,
                "last_message_at": chat.last_message_at,
            },
        )
        self.add_participant(owner)
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.chat_memory "
                "(chat_id, summary, last_summarized_sequence, summary_version, updated_at) "
                "VALUES (:chat_id, '', 0, 1, :updated_at)"
            ),
            {"chat_id": chat.chat_id, "updated_at": chat.updated_at},
        )

    def get_chat(self, chat_id: str) -> ChatRecord | None:
        row = self._connection.execute(
            text(
                "SELECT chat_id, owner_subject, owner_admin_id, title, chat_type, status, "
                "case_id, created_at, updated_at, last_message_at "
                "FROM pms_chat.chat WHERE chat_id = :chat_id"
            ),
            {"chat_id": chat_id},
        ).mappings().one_or_none()
        return _chat_from_row(row) if row is not None else None

    def list_chats(self, *, include_archived: bool = False) -> tuple[ChatRecord, ...]:
        status_filter = "" if include_archived else "WHERE status <> :archived_status"
        rows = self._connection.execute(
            text(
                "SELECT chat_id, owner_subject, owner_admin_id, title, chat_type, status, "
                "case_id, created_at, updated_at, last_message_at "
                f"FROM pms_chat.chat {status_filter} ORDER BY updated_at DESC, chat_id"
            ),
            {} if include_archived else {"archived_status": ChatStatus.ARCHIVED.value},
        ).mappings()
        return tuple(_chat_from_row(row) for row in rows)

    def update_chat(
        self,
        chat_id: str,
        *,
        title: str | None = None,
        status: ChatStatus | None = None,
    ) -> ChatRecord:
        if title is None and status is None:
            current = self.get_chat(chat_id)
            if current is None:
                raise LookupError("chat is unavailable")
            return current
        self._connection.execute(
            text(
                "UPDATE pms_chat.chat SET title = COALESCE(:title, title), "
                "status = COALESCE(:status, status), updated_at = :updated_at "
                "WHERE chat_id = :chat_id"
            ),
            {
                "chat_id": chat_id,
                "title": title,
                "status": status.value if status is not None else None,
                "updated_at": datetime.now(UTC),
            },
        )
        current = self.get_chat(chat_id)
        if current is None:
            raise LookupError("chat is unavailable")
        return current

    def archive_chat(self, chat_id: str) -> ChatRecord:
        return self.update_chat(chat_id, status=ChatStatus.ARCHIVED)

    def list_messages(self, chat_id: str) -> tuple[ChatMessage, ...]:
        rows = self._connection.execute(
            text(
                "SELECT message_id, chat_id, sequence_number, sender_subject, sender_admin_id, "
                "message_role, content, message_status, model_name, route, review_required, "
                "created_at, completed_at, failure_reason FROM pms_chat.chat_message "
                "WHERE chat_id = :chat_id ORDER BY sequence_number"
            ),
            {"chat_id": chat_id},
        ).mappings()
        return tuple(_message_from_row(row) for row in rows)

    def list_citations(self, message_id: str) -> tuple[ChatCitation, ...]:
        rows = self._connection.execute(
            text(
                "SELECT citation_id, message_id, source_id, canonical_document_id, "
                "document_version_id, page_number, block_id, section_number, clause_number, "
                "bounding_box, created_at FROM pms_chat.chat_message_citation "
                "WHERE message_id = :message_id ORDER BY page_number, citation_id"
            ),
            {"message_id": message_id},
        ).mappings()
        return tuple(
            ChatCitation(
                citation_id=str(row["citation_id"]),
                message_id=str(row["message_id"]),
                source_id=str(row["source_id"]),
                canonical_document_id=str(row["canonical_document_id"]),
                document_version_id=row["document_version_id"],
                page_number=int(row["page_number"]),
                block_id=row["block_id"],
                section_number=row["section_number"],
                clause_number=row["clause_number"],
                bounding_box=row["bounding_box"],
                created_at=row["created_at"],
            )
            for row in rows
        )

    def list_attachments(self, chat_id: str) -> tuple[ChatAttachment, ...]:
        rows = self._connection.execute(
            text(
                "SELECT attachment_id, chat_id, uploaded_by_subject, uploaded_by_admin_id, "
                "canonical_document_id, original_filename, checksum_sha256, mime_type, size_bytes, "
                "ingestion_status, classification, created_at, ready_at, failure_reason, "
                "review_reason "
                "FROM pms_chat.chat_attachment WHERE chat_id = :chat_id "
                "ORDER BY created_at, attachment_id"
            ),
            {"chat_id": chat_id},
        ).mappings()
        return tuple(
            ChatAttachment(
                attachment_id=str(row["attachment_id"]),
                chat_id=str(row["chat_id"]),
                uploaded_by_subject=str(row["uploaded_by_subject"]),
                uploaded_by_admin_id=row["uploaded_by_admin_id"],
                canonical_document_id=row["canonical_document_id"],
                original_filename=str(row["original_filename"]),
                checksum_sha256=str(row["checksum_sha256"]),
                mime_type=str(row["mime_type"]),
                size_bytes=int(row["size_bytes"]),
                ingestion_status=ChatIngestionStatus(str(row["ingestion_status"])),
                classification=Classification(str(row["classification"])),
                created_at=row["created_at"],
                ready_at=row["ready_at"],
                failure_reason=row["failure_reason"],
                review_reason=row["review_reason"],
            )
            for row in rows
        )

    def add_participant(self, participant: ChatParticipant) -> None:
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.chat_participant "
                "(chat_id, participant_subject, participant_admin_id, participant_role, "
                "access_mode, added_by_subject, added_at, removed_at) VALUES "
                "(:chat_id, :subject, :admin_id, :role, :access_mode, :added_by, :added_at, "
                ":removed_at) ON CONFLICT (chat_id, participant_subject) DO UPDATE SET "
                "participant_admin_id = EXCLUDED.participant_admin_id, "
                "participant_role = EXCLUDED.participant_role, "
                "access_mode = EXCLUDED.access_mode, added_by_subject = EXCLUDED.added_by_subject, "
                "added_at = EXCLUDED.added_at, removed_at = EXCLUDED.removed_at"
            ),
            {
                "chat_id": participant.chat_id,
                "subject": participant.participant_subject,
                "admin_id": participant.participant_admin_id,
                "role": participant.participant_role,
                "access_mode": participant.access_mode.value,
                "added_by": participant.added_by_subject,
                "added_at": participant.added_at,
                "removed_at": participant.removed_at,
            },
        )

    def _next_sequence(self, chat_id: str) -> int:
        # Updating the parent row takes a row lock, making MAX()+1 safe for
        # concurrent messages in the same chat within the surrounding transaction.
        self._connection.execute(
            text(
                "UPDATE pms_chat.chat SET updated_at = updated_at "
                "WHERE chat_id = :chat_id"
            ),
            {"chat_id": chat_id},
        )
        row = self._connection.execute(
            text(
                "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_sequence "
                "FROM pms_chat.chat_message WHERE chat_id = :chat_id"
            ),
            {"chat_id": chat_id},
        ).mappings().one()
        return int(row["next_sequence"])

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
    ) -> ChatMessage:
        if not content.strip():
            raise ValueError("chat message content cannot be empty")
        message = ChatMessage(
            message_id=str(uuid4()),
            chat_id=chat_id,
            sequence_number=self._next_sequence(chat_id),
            sender_subject=sender_subject,
            sender_admin_id=sender_admin_id,
            message_role=role,
            content=content,
            message_status=status,
            model_name=model_name,
            route=route,
            review_required=review_required,
            created_at=created_at,
        )
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.chat_message "
                "(message_id, chat_id, sequence_number, sender_subject, sender_admin_id, "
                "message_role, content, message_status, model_name, route, review_required, "
                "created_at) VALUES (:message_id, :chat_id, :sequence_number, :sender, "
                ":sender_admin_id, :role, :content, :status, :model, :route, :review_required, "
                ":created_at)"
            ),
            {
                "message_id": message.message_id,
                "chat_id": message.chat_id,
                "sequence_number": message.sequence_number,
                "sender": message.sender_subject,
                "sender_admin_id": message.sender_admin_id,
                "role": message.message_role.value,
                "content": message.content,
                "status": message.message_status.value,
                "model": message.model_name,
                "route": message.route,
                "review_required": message.review_required,
                "created_at": message.created_at,
            },
        )
        self._connection.execute(
            text(
                "UPDATE pms_chat.chat SET updated_at = :updated_at, "
                "last_message_at = :last_message_at "
                "WHERE chat_id = :chat_id"
            ),
            {"chat_id": chat_id, "updated_at": created_at, "last_message_at": created_at},
        )
        return message

    def update_message_status(
        self,
        message_id: str,
        *,
        status: ChatMessageStatus,
        completed_at: datetime | None = None,
        failure_reason: str | None = None,
    ) -> None:
        self._connection.execute(
            text(
                "UPDATE pms_chat.chat_message SET message_status = :status, "
                "completed_at = :completed_at, failure_reason = :failure_reason "
                "WHERE message_id = :message_id"
            ),
            {
                "message_id": message_id,
                "status": status.value,
                "completed_at": completed_at,
                "failure_reason": failure_reason,
            },
        )

    def add_citations(self, citations: Iterable[ChatCitation]) -> None:
        for citation in citations:
            self._connection.execute(
                text(
                    "INSERT INTO pms_chat.chat_message_citation "
                    "(citation_id, message_id, source_id, canonical_document_id, "
                    "document_version_id, page_number, block_id, section_number, clause_number, "
                    "bounding_box, created_at) VALUES (:citation_id, :message_id, :source_id, "
                    ":document_id, :version_id, :page_number, :block_id, :section_number, "
                    ":clause_number, CAST(:bounding_box AS jsonb), :created_at) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "citation_id": citation.citation_id,
                    "message_id": citation.message_id,
                    "source_id": citation.source_id,
                    "document_id": citation.canonical_document_id,
                    "version_id": citation.document_version_id,
                    "page_number": citation.page_number,
                    "block_id": citation.block_id,
                    "section_number": citation.section_number,
                    "clause_number": citation.clause_number,
                    "bounding_box": _json_value(citation.bounding_box),
                    "created_at": citation.created_at,
                },
            )

    def add_attachment(self, attachment: ChatAttachment) -> None:
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.chat_attachment "
                "(attachment_id, chat_id, uploaded_by_subject, uploaded_by_admin_id, "
                "canonical_document_id, original_filename, checksum_sha256, mime_type, size_bytes, "
                "ingestion_status, classification, created_at, ready_at, failure_reason, "
                "review_reason) "
                "VALUES (:attachment_id, :chat_id, :uploaded_by, :uploaded_by_admin_id, "
                ":document_id, :filename, :checksum, :mime_type, :size_bytes, :status, "
                ":classification, :created_at, :ready_at, :failure_reason, :review_reason) "
                "ON CONFLICT (chat_id, checksum_sha256) DO NOTHING"
            ),
            {
                "attachment_id": attachment.attachment_id,
                "chat_id": attachment.chat_id,
                "uploaded_by": attachment.uploaded_by_subject,
                "uploaded_by_admin_id": attachment.uploaded_by_admin_id,
                "document_id": attachment.canonical_document_id,
                "filename": attachment.original_filename,
                "checksum": attachment.checksum_sha256,
                "mime_type": attachment.mime_type,
                "size_bytes": attachment.size_bytes,
                "status": attachment.ingestion_status.value,
                "classification": attachment.classification.value,
                "created_at": attachment.created_at,
                "ready_at": attachment.ready_at,
                "failure_reason": attachment.failure_reason,
                "review_reason": attachment.review_reason,
            },
        )

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
        self._connection.execute(
            text(
                "UPDATE pms_chat.chat_attachment SET ingestion_status = :status, "
                "canonical_document_id = COALESCE(:document_id, canonical_document_id), "
                "ready_at = :ready_at, failure_reason = :failure_reason, "
                "review_reason = :review_reason "
                "WHERE attachment_id = :attachment_id"
            ),
            {
                "attachment_id": attachment_id,
                "status": status.value,
                "document_id": canonical_document_id,
                "ready_at": ready_at,
                "failure_reason": failure_reason,
                "review_reason": review_reason,
            },
        )

    def upsert_memory(self, memory: ChatMemory) -> None:
        self._connection.execute(
            text(
                "INSERT INTO pms_chat.chat_memory "
                "(chat_id, summary, last_summarized_sequence, summary_version, updated_at) "
                "VALUES (:chat_id, :summary, :last_sequence, :version, :updated_at) "
                "ON CONFLICT (chat_id) DO UPDATE SET summary = EXCLUDED.summary, "
                "last_summarized_sequence = EXCLUDED.last_summarized_sequence, "
                "summary_version = EXCLUDED.summary_version, updated_at = EXCLUDED.updated_at"
            ),
            {
                "chat_id": memory.chat_id,
                "summary": memory.summary,
                "last_sequence": memory.last_summarized_sequence,
                "version": memory.summary_version,
                "updated_at": memory.updated_at,
            },
        )

    def get_memory(self, chat_id: str) -> ChatMemory | None:
        row = self._connection.execute(
            text(
                "SELECT chat_id, summary, last_summarized_sequence, summary_version, updated_at "
                "FROM pms_chat.chat_memory WHERE chat_id = :chat_id"
            ),
            {"chat_id": chat_id},
        ).mappings().one_or_none()
        if row is None:
            return None
        return ChatMemory(
            chat_id=str(row["chat_id"]),
            summary=str(row["summary"]),
            last_summarized_sequence=int(row["last_summarized_sequence"]),
            summary_version=int(row["summary_version"]),
            updated_at=row["updated_at"],
        )

    def try_acquire_generation_lock(self, key: str) -> bool:
        """Acquire a transaction-scoped lock held until the request ends."""

        result = self._connection.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        ).scalar_one()
        return bool(result)


def _json_value(value: Mapping[str, Any] | None) -> str:
    """Serialize optional JSONB data without allowing SQL string interpolation."""

    import json

    return json.dumps(value, sort_keys=True) if value is not None else "null"
