"""Transaction-bounded persistence orchestration for Phase 06 parsing."""

from __future__ import annotations

from dataclasses import dataclass

from pms_common.security import AuthorizationContext
from pms_common.settings import Settings
from sqlalchemy import Engine

from pms_ingestion.factory import create_document_service
from pms_ingestion.models import DocumentStatus, ObjectKind
from pms_ingestion.parser_adapters import (
    LocalPdfVerifier,
    default_parser_adapters,
)
from pms_ingestion.parsing import CanonicalDocument, ParsingEngine, ParsingOutcome
from pms_ingestion.storage import MinioObjectStore, ObjectStore

PIPELINE_PRODUCER = "pms-parser-pipeline"
PIPELINE_VERSION = "1.0"


class ParsingServiceError(RuntimeError):
    """Raised when a configured parse cannot complete safely."""


@dataclass(frozen=True, slots=True)
class PersistedParsingResult:
    """CLI-facing result without exposing credentials or raw document text."""

    canonical_document_id: str
    parser: str
    parser_mode: str
    page_count: int
    quality_passed: bool
    review_required: bool
    fallback_used: bool
    idempotent: bool
    raw_object_keys: tuple[str, ...]
    canonical_object_key: str | None
    issue_codes: tuple[str, ...]
    final_status: str


def default_parsing_engine(settings: Settings) -> ParsingEngine:
    """Compose local adapters without importing optional runtimes at startup."""

    verifier = (
        LocalPdfVerifier(settings)
        if settings.pymupdf_enabled
        else None
    )
    return ParsingEngine(
        settings,
        default_parser_adapters(settings),
        verifier,
    )


class DocumentParsingCoordinator:
    """Keep database transactions short while parser execution runs outside them."""

    def __init__(
        self,
        engine: Engine,
        context: AuthorizationContext,
        settings: Settings,
        *,
        parsing_engine: ParsingEngine | None = None,
        object_store: ObjectStore | None = None,
    ) -> None:
        self._engine = engine
        self._context = context
        self._settings = settings
        self._parsing_engine = parsing_engine or default_parsing_engine(settings)
        self._object_store = object_store or MinioObjectStore(settings)

    def parse(self, document_id: str, *, force: bool = False) -> PersistedParsingResult:
        with self._engine.begin() as connection:
            service = create_document_service(
                connection,
                self._context,
                self._settings,
                object_store=self._object_store,
            )
            if not force:
                existing = service.retrieve_derived(
                    document_id=document_id,
                    object_kind=ObjectKind.CANONICAL_JSON,
                    producer=PIPELINE_PRODUCER,
                    producer_version=PIPELINE_VERSION,
                )
                if existing is not None:
                    canonical = CanonicalDocument.model_validate_json(existing.content)
                    return self._persisted_result(
                        document_id=document_id,
                        canonical=canonical,
                        outcome=None,
                        raw_object_keys=(),
                        canonical_object_key=existing.artifact.object_key,
                        idempotent=True,
                    )
            service.transition_status(document_id, DocumentStatus.PARSING)
            retrieved = service.retrieve(document_id)

        try:
            outcome = self._parsing_engine.run(retrieved.document, retrieved.content)
        except Exception as error:
            with self._engine.begin() as connection:
                service = create_document_service(
                    connection,
                    self._context,
                    self._settings,
                    object_store=self._object_store,
                )
                service.transition_status(document_id, DocumentStatus.FAILED)
            raise ParsingServiceError("document parsing failed") from error

        raw_object_keys: list[str] = []
        canonical_object_key: str | None = None
        with self._engine.begin() as connection:
            service = create_document_service(
                connection,
                self._context,
                self._settings,
                object_store=self._object_store,
            )
            for raw_output in outcome.raw_outputs:
                raw_content = raw_output.model_dump_json(
                    exclude_none=True,
                ).encode("utf-8")
                existing_raw = service.retrieve_derived(
                    document_id=document_id,
                    object_kind=ObjectKind.RAW_PARSER,
                    producer=raw_output.parser,
                    producer_version=raw_output.parser_version,
                )
                stored = (
                    existing_raw.artifact
                    if existing_raw is not None
                    and existing_raw.content == raw_content
                    else service.store_derived(
                        document_id=document_id,
                        content=raw_content,
                        mime_type="application/json",
                        object_kind=ObjectKind.RAW_PARSER,
                        producer=raw_output.parser,
                        producer_version=raw_output.parser_version,
                    )
                )
                raw_object_keys.append(stored.object_key)

            if outcome.canonical is None:
                service.transition_status(
                    document_id,
                    DocumentStatus(outcome.final_status),
                )
            else:
                service.transition_status(document_id, DocumentStatus.PARSED)
                stored = service.store_derived(
                    document_id=document_id,
                    content=outcome.canonical.model_dump_json().encode("utf-8"),
                    mime_type="application/json",
                    object_kind=ObjectKind.CANONICAL_JSON,
                    producer=PIPELINE_PRODUCER,
                    producer_version=PIPELINE_VERSION,
                )
                canonical_object_key = stored.object_key
                service.transition_status(
                    document_id,
                    DocumentStatus(outcome.final_status),
                )
        return self._persisted_result(
            document_id=document_id,
            canonical=outcome.canonical,
            outcome=outcome,
            raw_object_keys=tuple(raw_object_keys),
            canonical_object_key=canonical_object_key,
            idempotent=False,
        )

    @staticmethod
    def _persisted_result(
        *,
        document_id: str,
        canonical: CanonicalDocument | None,
        outcome: ParsingOutcome | None,
        raw_object_keys: tuple[str, ...],
        canonical_object_key: str | None,
        idempotent: bool,
    ) -> PersistedParsingResult:
        if canonical is not None:
            quality = canonical.quality
            return PersistedParsingResult(
                canonical_document_id=canonical.canonical_document_id,
                parser=canonical.parser,
                parser_mode=canonical.parser_mode,
                page_count=len(canonical.pages),
                quality_passed=quality.passed,
                review_required=quality.review_required,
                fallback_used=outcome.fallback_used if outcome else False,
                idempotent=idempotent,
                raw_object_keys=raw_object_keys,
                canonical_object_key=canonical_object_key,
                issue_codes=tuple(issue.code for issue in quality.issues),
                final_status=(
                    outcome.final_status
                    if outcome
                    else (
                        "review_required"
                        if quality.review_required
                        else "canonicalized"
                    )
                ),
            )
        if outcome is None or outcome.quality is None:
            return PersistedParsingResult(
                canonical_document_id=document_id,
                parser="none",
                parser_mode="none",
                page_count=0,
                quality_passed=False,
                review_required=True,
                fallback_used=bool(outcome and outcome.fallback_used),
                idempotent=idempotent,
                raw_object_keys=raw_object_keys,
                canonical_object_key=None,
                issue_codes=("NO_PARSER_OUTPUT",),
                final_status=outcome.final_status if outcome else "review_required",
            )
        return PersistedParsingResult(
            canonical_document_id=document_id,
            parser=outcome.selected_parser or "none",
            parser_mode="none",
            page_count=outcome.quality.metrics.page_count,
            quality_passed=False,
            review_required=True,
            fallback_used=outcome.fallback_used,
            idempotent=idempotent,
            raw_object_keys=raw_object_keys,
            canonical_object_key=None,
            issue_codes=tuple(issue.code for issue in outcome.quality.issues),
            final_status=outcome.final_status,
        )
