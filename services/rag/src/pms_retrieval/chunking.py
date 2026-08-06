"""Structure-aware parent/child chunking over Phase 06 canonical JSON."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pms_common.security import AuthorizationContext, Classification
from pms_common.settings import Settings
from pms_common.text import remove_nul_characters
from pms_ingestion.parsing import (
    BlockKind,
    CanonicalBlock,
    CanonicalDocument,
)

from pms_retrieval.models import ChunkCitation, ChunkKind, DocumentChunk

CHUNKING_VERSION = "1.0"
_PROVISO = re.compile(r"^\s*(?:provided\s+that|proviso\b)", re.IGNORECASE)
_SECTION = re.compile(r"\bsection\s+([0-9A-Za-z.-]+)", re.IGNORECASE)
_CLAUSE = re.compile(r"\bclause\s+([0-9A-Za-z().-]+)", re.IGNORECASE)


class ChunkingError(RuntimeError):
    """Base error for deterministic chunk generation."""


class ChunkingReviewRequired(ChunkingError):
    """Raised when a protected semantic element cannot fit safely."""


class Tokenizer(Protocol):
    """Embedding-tokenizer boundary required before chunks are persisted."""

    def count(self, text: str) -> int: ...

    def split(self, text: str, maximum: int, overlap: int) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    kind: BlockKind
    heading_path: tuple[str, ...]
    blocks: tuple[CanonicalBlock, ...]


class StructureAwareChunker:
    """Create stable parent/child chunks without splitting protected elements."""

    def __init__(
        self,
        settings: Settings,
        tokenizer: Tokenizer,
        *,
        chunking_version: str = CHUNKING_VERSION,
    ) -> None:
        self._settings = settings
        self._tokenizer = tokenizer
        self._version = chunking_version

    def chunk(
        self,
        canonical: CanonicalDocument,
        context: AuthorizationContext,
        *,
        classification: str,
    ) -> tuple[DocumentChunk, ...]:
        provisional = (
            canonical.quality.decision == "CONDITIONAL_PASS"
            and self._settings.pdf_provisional_indexing_enabled
            and self._settings.pdf_evidence_mode == "AUTHORITATIVE_AND_PROVISIONAL"
        )
        if (canonical.quality.review_required and not provisional) or not canonical.quality.passed:
            raise ChunkingReviewRequired(
                "only quality-passed canonical documents may be chunked"
            )
        units = self._semantic_units(canonical)
        if not units:
            raise ChunkingReviewRequired("canonical document contains no chunkable text")
        parent_groups = self._group_units(
            units,
            maximum=self._settings.parent_chunk_max_tokens,
            target=self._settings.parent_chunk_max_tokens,
            overlap=0,
        )
        chunks: list[DocumentChunk] = []
        child_ordinal = 0
        for parent_ordinal, parent_units in enumerate(parent_groups):
            parent = self._build_chunk(
                canonical,
                context,
                classification=classification,
                kind=ChunkKind.PARENT,
                ordinal=parent_ordinal,
                parent_chunk_id=None,
                units=parent_units,
            )
            if parent.token_count > self._settings.parent_chunk_max_tokens:
                raise ChunkingError("parent token limit was not enforced")
            chunks.append(parent)
            child_groups = self._group_units(
                parent_units,
                maximum=self._settings.chunk_max_tokens,
                target=self._settings.chunk_target_tokens,
                overlap=self._settings.chunk_overlap_tokens,
            )
            for child_units in child_groups:
                child = self._build_chunk(
                    canonical,
                    context,
                    classification=classification,
                    kind=ChunkKind.CHILD,
                    ordinal=child_ordinal,
                    parent_chunk_id=parent.chunk_id,
                    units=child_units,
                )
                if child.token_count > self._settings.chunk_max_tokens:
                    raise ChunkingError("child token limit was not enforced")
                chunks.append(child)
                child_ordinal += 1
        return tuple(chunks)

    def _semantic_units(self, canonical: CanonicalDocument) -> tuple[_Unit, ...]:
        headings: list[str] = []
        units: list[_Unit] = []
        for page in canonical.pages:
            for block in page.blocks:
                text = (remove_nul_characters(block.text) or "").strip()
                if not text:
                    continue
                if block.kind is BlockKind.HEADING:
                    level = block.heading_level or 1
                    headings = headings[: level - 1]
                    headings.append(text)
                    continue
                unit = _Unit(
                    text=text,
                    kind=block.kind,
                    heading_path=tuple(headings),
                    blocks=(block,),
                )
                if (
                    self._settings.preserve_legal_provisos
                    and _PROVISO.match(text)
                    and units
                ):
                    prior = units.pop()
                    unit = _Unit(
                        text=f"{prior.text}\n\n{text}",
                        kind=prior.kind,
                        heading_path=prior.heading_path,
                        blocks=(*prior.blocks, block),
                    )
                units.extend(self._fit_unit(unit))
        return tuple(units)

    def _fit_unit(self, unit: _Unit) -> tuple[_Unit, ...]:
        token_count = self._tokenizer.count(self._unit_text(unit))
        if token_count <= self._settings.chunk_max_tokens:
            return (unit,)
        if unit.kind in {BlockKind.TABLE, BlockKind.FORMULA}:
            raise ChunkingReviewRequired(
                f"{unit.kind.value} exceeds the child token limit and cannot be split"
            )
        if len(unit.blocks) > 1 and self._settings.preserve_legal_provisos:
            raise ChunkingReviewRequired(
                "a clause/proviso unit exceeds the child token limit"
            )
        heading_tokens = self._tokenizer.count("\n".join(unit.heading_path))
        available = self._settings.chunk_max_tokens - heading_tokens
        if available <= 0:
            raise ChunkingReviewRequired("heading path exceeds the child token limit")
        fragments = self._tokenizer.split(
            unit.text,
            available,
            min(self._settings.chunk_overlap_tokens, max(available - 1, 0)),
        )
        if not fragments:
            raise ChunkingReviewRequired("tokenizer could not split an oversized unit")
        return tuple(
            _Unit(
                text=fragment,
                kind=unit.kind,
                heading_path=unit.heading_path,
                blocks=unit.blocks,
            )
            for fragment in fragments
        )

    def _group_units(
        self,
        units: Sequence[_Unit],
        *,
        maximum: int,
        target: int,
        overlap: int,
    ) -> tuple[tuple[_Unit, ...], ...]:
        groups: list[tuple[_Unit, ...]] = []
        current: list[_Unit] = []
        for unit in units:
            proposed = (*current, unit)
            proposed_count = self._tokenizer.count(self._group_text(proposed))
            heading_changed = bool(
                current and unit.heading_path != current[-1].heading_path
            )
            if current and (proposed_count > target or heading_changed):
                groups.append(tuple(current))
                current = (
                    [] if heading_changed else self._overlap_units(current, overlap)
                )
                while current and self._tokenizer.count(
                    self._group_text((*current, unit))
                ) > maximum:
                    current.pop(0)
            current.append(unit)
            if self._tokenizer.count(self._group_text(current)) > maximum:
                raise ChunkingReviewRequired("a semantic group exceeds its token limit")
        if current:
            groups.append(tuple(current))
        return tuple(groups)

    def _overlap_units(self, units: Sequence[_Unit], overlap: int) -> list[_Unit]:
        if overlap <= 0:
            return []
        selected: list[_Unit] = []
        for unit in reversed(units):
            proposed = [unit, *selected]
            if self._tokenizer.count(self._group_text(proposed)) > overlap:
                break
            selected = proposed
        return selected

    @staticmethod
    def _unit_text(unit: _Unit) -> str:
        if not unit.heading_path:
            return unit.text
        return "\n".join((*unit.heading_path, "", unit.text))

    def _group_text(self, units: Sequence[_Unit]) -> str:
        if not units:
            return ""
        heading = units[0].heading_path
        body = "\n\n".join(unit.text for unit in units)
        return "\n".join((*heading, "", body)).strip() if heading else body

    def _build_chunk(
        self,
        canonical: CanonicalDocument,
        context: AuthorizationContext,
        *,
        classification: str,
        kind: ChunkKind,
        ordinal: int,
        parent_chunk_id: str | None,
        units: Sequence[_Unit],
    ) -> DocumentChunk:
        text = remove_nul_characters(self._group_text(units)) or ""
        token_count = self._tokenizer.count(text)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunk_id = hashlib.sha256(
            (
                f"{canonical.document_version_id}:{self._version}:"
                f"{kind.value}:{ordinal}:{content_hash}"
            ).encode()
        ).hexdigest()
        blocks = tuple(block for unit in units for block in unit.blocks)
        page_numbers = tuple(sorted({block.page_number for block in blocks}))
        citations = tuple(
            ChunkCitation(
                block_id=block.block_id,
                page_number=block.page_number,
                bounding_box=block.bounding_box,
            )
            for block in blocks
        )
        language_codes = tuple(dict.fromkeys(block.language_code for block in blocks))
        languages = tuple(
            dict.fromkeys(
                language
                for block in blocks
                for language in block.languages
            )
        )
        script_codes = tuple(
            dict.fromkeys(
                script
                for block in blocks
                for script in block.script_code.split("+")
                if script
            )
        )
        confidence_values = [
            block.ocr_confidence
            for block in blocks
            if block.ocr_confidence is not None
        ]
        section_match = _SECTION.search(text)
        clause_match = _CLAUSE.search(text)

        return DocumentChunk(
            chunk_id=chunk_id,
            canonical_document_id=canonical.canonical_document_id,
            document_version_id=canonical.document_version_id,
            parent_chunk_id=parent_chunk_id,
            chunk_kind=kind,
            ordinal=ordinal,
            text=text,
            token_count=token_count,
            content_hash=content_hash,
            heading_path=units[0].heading_path,
            page_numbers=page_numbers,
            citations=citations,
            section_number=section_match.group(1) if section_match else None,
            clause_number=clause_match.group(1) if clause_match else None,
            language_code=(
                language_codes[0] if len(language_codes) == 1 else "mixed"
            ),
            languages=languages or ("und",),
            script_code="+".join(script_codes) if script_codes else "Zyyy",
            translation_group_id=None,
            authoritative_language=None,
            publication_date=None,
            effective_from=None,
            effective_to=None,
            document_status="canonicalized",
            port_id=context.unit_id,
            department_id=context.department_id,
            security_classification=Classification(classification),
            review_status="accepted",
            ocr_confidence=(
                sum(confidence_values) / len(confidence_values)
                if confidence_values
                else None
            ),
            parser_name=canonical.parser,
            parser_version=canonical.parser_version,
            chunking_version=self._version,
        )
