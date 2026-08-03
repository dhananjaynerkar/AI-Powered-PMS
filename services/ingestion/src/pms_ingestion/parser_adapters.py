"""Lazy local adapters for the approved Phase 06 PDF parser stack."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
from base64 import b64encode
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pms_common.settings import Settings

from pms_ingestion.parsing import (
    BlockKind,
    BoundingBox,
    ParsedBlock,
    ParsedPage,
    ParserAdapter,
    ParserError,
    ParserOutput,
    ParserUnavailable,
    TransientParserError,
    VerificationEvidence,
)


def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _kind(value: object) -> BlockKind:
    normalized = str(value or "").strip().lower().replace(" ", "_")
    aliases = {
        "heading": BlockKind.HEADING,
        "title": BlockKind.HEADING,
        "section_header": BlockKind.HEADING,
        "paragraph": BlockKind.PARAGRAPH,
        "text": BlockKind.PARAGRAPH,
        "text_block": BlockKind.PARAGRAPH,
        "table": BlockKind.TABLE,
        "list_item": BlockKind.LIST_ITEM,
        "caption": BlockKind.CAPTION,
        "image": BlockKind.IMAGE,
        "picture": BlockKind.IMAGE,
        "formula": BlockKind.FORMULA,
    }
    return aliases.get(normalized, BlockKind.OTHER)


def _bbox(value: object) -> BoundingBox | None:
    if isinstance(value, list | tuple) and len(value) >= 4:
        return BoundingBox(
            left=float(value[0]),
            bottom=float(value[1]),
            right=float(value[2]),
            top=float(value[3]),
        )
    if isinstance(value, dict):
        lookup = {str(key).lower().replace(" ", "_"): item for key, item in value.items()}
        keys = ("left", "bottom", "right", "top")
        if all(key in lookup for key in keys):
            return BoundingBox(
                left=float(lookup["left"]),
                bottom=float(lookup["bottom"]),
                right=float(lookup["right"]),
                top=float(lookup["top"]),
            )
        alternate = ("l", "b", "r", "t")
        if all(key in lookup for key in alternate):
            return BoundingBox(
                left=float(lookup["l"]),
                bottom=float(lookup["b"]),
                right=float(lookup["r"]),
                top=float(lookup["t"]),
            )
    return None


def _text_from_node(node: dict[str, object]) -> str:
    direct = node.get("content") or node.get("text")
    if isinstance(direct, str):
        return direct
    collected: list[str] = []
    for key in ("kids", "children", "list items", "rows", "cells"):
        items = node.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    text = _text_from_node(item)
                    if text:
                        collected.append(text)
    return "\n".join(collected)


def _walk_nodes(value: object) -> Iterator[dict[str, object]]:
    if isinstance(value, dict):
        if "type" in value:
            yield value
        for child in value.values():
            yield from _walk_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_nodes(child)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _json_safe_payload(value: object) -> object:
    """Represent parser byte fields losslessly inside the persisted JSON payload."""

    if isinstance(value, bytes | bytearray | memoryview):
        return {
            "encoding": "base64",
            "data": b64encode(bytes(value)).decode("ascii"),
        }
    if isinstance(value, dict):
        return {str(key): _json_safe_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_payload(item) for item in value]
    return value


def _normalize_opendataloader(payload: dict[str, object], markdown: str) -> ParserOutput:
    page_count = _integer(payload.get("number of pages")) or 0
    page_blocks: dict[int, list[ParsedBlock]] = {
        page_number: [] for page_number in range(1, page_count + 1)
    }
    for index, node in enumerate(_walk_nodes(payload.get("kids", []))):
        raw_page = node.get("page number")
        if not isinstance(raw_page, int) or raw_page < 1:
            continue
        block_kind = _kind(node.get("type"))
        text = _text_from_node(node)
        if not text and block_kind not in {BlockKind.IMAGE, BlockKind.TABLE}:
            continue
        page_blocks.setdefault(raw_page, []).append(
            ParsedBlock(
                block_id=f"odl-{node.get('id', index)}",
                page_number=raw_page,
                reading_order=len(page_blocks[raw_page]),
                kind=block_kind,
                text=text,
                bounding_box=_bbox(node.get("bounding box")),
                heading_level=(
                    _integer(node.get("heading level"))
                    if block_kind is BlockKind.HEADING
                    else None
                ),
                table_rows=(
                    _integer(node.get("number of rows"))
                    if block_kind is BlockKind.TABLE
                    else None
                ),
                table_columns=(
                    _integer(node.get("number of columns"))
                    if block_kind is BlockKind.TABLE
                    else None
                ),
                table_has_header=(
                    bool(node.get("rows")) if block_kind is BlockKind.TABLE else None
                ),
                source_element_id=(
                    str(node["id"]) if node.get("id") is not None else None
                ),
            )
        )
    pages = tuple(
        ParsedPage(page_number=number, blocks=tuple(page_blocks.get(number, [])))
        for number in range(1, page_count + 1)
    )
    return ParserOutput(
        parser="opendataloader",
        parser_version=_package_version("opendataloader-pdf"),
        mode="deterministic",
        page_count=page_count,
        pages=pages,
        markdown=markdown,
        raw_payload=payload,
    )


class OpenDataLoaderAdapter:
    """Run OpenDataLoader in deterministic, local, content-safe mode."""

    name = "opendataloader"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(self, content: bytes, filename: str) -> ParserOutput:
        if not self._settings.opendataloader_enabled:
            raise ParserUnavailable("OpenDataLoader is disabled")
        try:
            module = importlib.import_module("opendataloader_pdf")
        except ImportError as error:
            raise ParserUnavailable("opendataloader-pdf is not installed") from error

        work_root = Path("tmp") / "pdfs"
        work_root.mkdir(parents=True, exist_ok=True)
        try:
            with TemporaryDirectory(prefix="odl-", dir=work_root) as directory:
                root = Path(directory)
                source = root / Path(filename).name
                output = root / "output"
                output.mkdir()
                source.write_bytes(content)
                module.convert(
                    input_path=str(source),
                    output_dir=str(output),
                    format=self._settings.pdf_output_formats,
                    quiet=True,
                    sanitize=self._settings.opendataloader_sanitize,
                    use_struct_tree=self._settings.pdf_use_struct_tree,
                    image_output=self._settings.pdf_image_output,
                    threads=str(self._settings.pdf_threads),
                )
                json_files = sorted(output.rglob("*.json"))
                if not json_files:
                    raise ParserError("OpenDataLoader produced no JSON output")
                payload = json.loads(json_files[0].read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ParserError("OpenDataLoader JSON root is not an object")
                markdown_files = sorted(output.rglob("*.md"))
                markdown = (
                    markdown_files[0].read_text(encoding="utf-8")
                    if markdown_files
                    else ""
                )
                return _normalize_opendataloader(payload, markdown)
        except ParserError:
            raise
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
            raise TransientParserError("OpenDataLoader conversion failed") from error


def _docling_provenance(
    item: dict[str, object],
    page_heights: dict[int, float],
) -> tuple[int, BoundingBox | None]:
    provenance = item.get("prov")
    if not isinstance(provenance, list) or not provenance:
        return 1, None
    first = provenance[0]
    if not isinstance(first, dict):
        return 1, None
    page_number = int(first.get("page_no", 1) or 1)
    raw_bbox = first.get("bbox")
    parsed = _bbox(raw_bbox)
    origin = ""
    if isinstance(raw_bbox, dict):
        origin = str(raw_bbox.get("coord_origin", "")).lower()
    if parsed is not None and "top" in origin:
        height = page_heights.get(page_number)
        if height is not None:
            parsed = BoundingBox(
                left=parsed.left,
                bottom=height - parsed.top,
                right=parsed.right,
                top=height - parsed.bottom,
            )
    return page_number, parsed


def _docling_table(
    node: dict[str, object],
) -> tuple[str, int | None, int | None, bool | None]:
    data = node.get("data")
    if not isinstance(data, dict):
        return "", None, None, None
    rows = _integer(data.get("num_rows"))
    columns = _integer(data.get("num_cols"))
    raw_cells = data.get("table_cells")
    if not isinstance(raw_cells, list):
        return "", rows, columns, None
    cells = [cell for cell in raw_cells if isinstance(cell, dict)]
    ordered = sorted(
        cells,
        key=lambda cell: (
            int(cell.get("start_row_offset_idx", 0) or 0),
            int(cell.get("start_col_offset_idx", 0) or 0),
        ),
    )
    text = "\n".join(
        str(cell.get("text", "")).strip()
        for cell in ordered
        if str(cell.get("text", "")).strip()
    )
    has_header = any(
        cell.get("column_header") is True or cell.get("row_header") is True
        for cell in cells
    )
    return text, rows, columns, has_header


def _normalize_docling(
    payload: dict[str, object],
    markdown: str,
    parser_version: str,
) -> ParserOutput:
    pages_value = payload.get("pages")
    page_sizes: dict[int, tuple[float | None, float | None]] = {}
    if isinstance(pages_value, dict):
        for key, value in pages_value.items():
            if not isinstance(value, dict):
                continue
            page_number = int(str(value.get("page_no", key)))
            size = value.get("size")
            width = height = None
            if isinstance(size, dict):
                if size.get("width") is not None:
                    width = float(size["width"])
                if size.get("height") is not None:
                    height = float(size["height"])
            page_sizes[page_number] = (width, height)

    page_heights = {
        page_number: size[1]
        for page_number, size in page_sizes.items()
        if size[1] is not None
    }
    nodes: list[dict[str, object]] = []
    for key in ("texts", "tables"):
        value = payload.get(key)
        if isinstance(value, list):
            nodes.extend(item for item in value if isinstance(item, dict))

    blocks_by_page: dict[int, list[ParsedBlock]] = {}
    for index, node in enumerate(nodes):
        kind = _kind(node.get("label") or node.get("type"))
        table_rows = table_columns = None
        table_has_header = None
        if kind is BlockKind.TABLE:
            text, table_rows, table_columns, table_has_header = _docling_table(node)
        else:
            text = _text_from_node(node)
        if not text.strip() and kind is not BlockKind.TABLE:
            continue
        page_number, bounding_box = _docling_provenance(node, page_heights)
        blocks_by_page.setdefault(page_number, []).append(
            ParsedBlock(
                block_id=f"docling-{index}",
                page_number=page_number,
                reading_order=len(blocks_by_page[page_number]),
                kind=kind,
                text=text,
                bounding_box=bounding_box,
                heading_level=(
                    _integer(node.get("level"))
                    if kind is BlockKind.HEADING
                    else None
                ),
                table_rows=table_rows,
                table_columns=table_columns,
                table_has_header=table_has_header,
                source_element_id=(
                    str(node["self_ref"]) if node.get("self_ref") else None
                ),
            )
        )

    page_count = max(len(page_sizes), max(blocks_by_page, default=0))
    pages = tuple(
        ParsedPage(
            page_number=number,
            width=page_sizes.get(number, (None, None))[0],
            height=page_sizes.get(number, (None, None))[1],
            blocks=tuple(blocks_by_page.get(number, [])),
        )
        for number in range(1, page_count + 1)
    )
    safe_payload = _json_safe_payload(payload)
    if not isinstance(safe_payload, dict):
        raise ParserError("Docling JSON-safe output root is not an object")
    return ParserOutput(
        parser="docling",
        parser_version=parser_version,
        mode="fallback",
        page_count=page_count,
        pages=pages,
        markdown=markdown,
        raw_payload=safe_payload,
    )


class DoclingAdapter:
    """Use Docling as the first local structural fallback."""

    name = "docling"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(self, content: bytes, filename: str) -> ParserOutput:
        if not self._settings.docling_enabled:
            raise ParserUnavailable("Docling is disabled")
        try:
            converter_module = importlib.import_module("docling.document_converter")
            base_module = importlib.import_module("docling.datamodel.base_models")
        except ImportError as error:
            raise ParserUnavailable("docling is not installed") from error
        try:
            stream = base_module.DocumentStream(name=filename, stream=BytesIO(content))
            result = converter_module.DocumentConverter().convert(stream)
            document = result.document
            payload = document.export_to_dict()
            markdown = document.export_to_markdown()
            if not isinstance(payload, dict):
                raise ParserError("Docling output root is not an object")
            return _normalize_docling(
                payload,
                markdown,
                _package_version("docling"),
            )
        except ParserError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise TransientParserError("Docling conversion failed") from error


def _pymupdf_module() -> Any:
    for name in ("pymupdf", "fitz"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise ParserUnavailable("PyMuPDF is not installed")


def _pdf_bbox_from_top_left(
    raw: list[float] | tuple[float, ...],
    page_height: float,
) -> BoundingBox:
    left, top, right, bottom = (float(value) for value in raw[:4])
    return BoundingBox(
        left=left,
        bottom=page_height - bottom,
        right=right,
        top=page_height - top,
    )


class PyMuPDFAdapter:
    """Extract digital text and coordinates as a deterministic fallback."""

    name = "pymupdf"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(self, content: bytes, filename: str) -> ParserOutput:
        del filename
        if not self._settings.pymupdf_enabled:
            raise ParserUnavailable("PyMuPDF is disabled")
        module = _pymupdf_module()
        try:
            document = module.open(stream=content, filetype="pdf")
            pages: list[ParsedPage] = []
            raw_pages: list[dict[str, object]] = []
            for page_index, page in enumerate(document):
                page_number = page_index + 1
                height = float(page.rect.height)
                raw = page.get_text("dict")
                raw_pages.append(raw)
                blocks: list[ParsedBlock] = []
                for raw_block in raw.get("blocks", []):
                    if not isinstance(raw_block, dict) or raw_block.get("type") != 0:
                        continue
                    lines = raw_block.get("lines", [])
                    text_parts: list[str] = []
                    max_size = 0.0
                    if isinstance(lines, list):
                        for line in lines:
                            if not isinstance(line, dict):
                                continue
                            spans = line.get("spans", [])
                            if isinstance(spans, list):
                                for span in spans:
                                    if isinstance(span, dict):
                                        text_parts.append(str(span.get("text", "")))
                                        max_size = max(
                                            max_size,
                                            float(span.get("size", 0) or 0),
                                        )
                    text = " ".join(part for part in text_parts if part).strip()
                    if not text:
                        continue
                    raw_bbox = raw_block.get("bbox")
                    bounding_box = (
                        _pdf_bbox_from_top_left(raw_bbox, height)
                        if isinstance(raw_bbox, list | tuple) and len(raw_bbox) >= 4
                        else None
                    )
                    is_heading = max_size >= 14
                    blocks.append(
                        ParsedBlock(
                            block_id=f"pymupdf-{page_number}-{len(blocks)}",
                            page_number=page_number,
                            reading_order=len(blocks),
                            kind=(
                                BlockKind.HEADING if is_heading else BlockKind.PARAGRAPH
                            ),
                            text=text,
                            bounding_box=bounding_box,
                            heading_level=1 if is_heading else None,
                        )
                    )
                pages.append(
                    ParsedPage(
                        page_number=page_number,
                        width=float(page.rect.width),
                        height=height,
                        blocks=tuple(blocks),
                    )
                )
            document.close()
            return ParserOutput(
                parser=self.name,
                parser_version=_package_version("pymupdf"),
                mode="verification-fallback",
                page_count=len(pages),
                pages=tuple(pages),
                markdown="\n\n".join(
                    block.text for page in pages for block in page.blocks
                ),
                raw_payload={"pages": _json_safe_payload(raw_pages)},
            )
        except ParserUnavailable:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise ParserError("PyMuPDF extraction failed") from error


def _is_structured_table(table: object) -> bool:
    """Accept dense multi-column candidates, not prose line-wrap regions."""

    if not isinstance(table, list):
        return False
    rows = [row for row in table if isinstance(row, list | tuple)]
    if len(rows) < 2:
        return False
    column_count = max((len(row) for row in rows), default=0)
    if column_count < 2:
        return False
    normalized = [
        [str(cell or "").strip() for cell in row]
        + [""] * (column_count - len(row))
        for row in rows
    ]
    non_empty = sum(bool(cell) for row in normalized for cell in row)
    if non_empty / (len(normalized) * column_count) < 0.60:
        return False
    populated_columns = {
        column
        for column in range(column_count)
        if sum(bool(row[column]) for row in normalized) >= 2
    }
    populated_rows = sum(
        sum(bool(cell) for cell in row) >= 2 for row in normalized
    )
    return len(populated_columns) >= 2 and populated_rows >= 2


class LocalPdfVerifier:
    """Verify OpenDataLoader output without treating prose as a table."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pymupdf = PyMuPDFAdapter(settings)

    def verify(self, content: bytes, filename: str) -> VerificationEvidence:
        output = self._pymupdf.parse(content, filename)
        table_count = 0
        if self._settings.pdfplumber_enabled:
            try:
                pdfplumber = importlib.import_module("pdfplumber")
            except ImportError:
                pdfplumber = None
            if pdfplumber is not None:
                try:
                    with pdfplumber.open(BytesIO(content)) as document:
                        table_count = sum(
                            sum(
                                _is_structured_table(table)
                                for table in (page.extract_tables() or [])
                            )
                            for page in document.pages
                        )
                except (OSError, RuntimeError, ValueError) as error:
                    raise ParserError("pdfplumber table verification failed") from error
        return VerificationEvidence(
            provider="pymupdf+pdfplumber",
            provider_version=(
                f"pymupdf={_package_version('pymupdf')};"
                f"pdfplumber={_package_version('pdfplumber')}"
            ),
            page_count=output.page_count,
            text=output.text,
            table_count=table_count,
        )


class PaddleOCRAdapter:
    """Use the local Devanagari PaddleOCR model for scanned-page fallback."""

    name = "paddleocr"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(self, content: bytes, filename: str) -> ParserOutput:
        del filename
        if not self._settings.paddleocr_enabled or not self._settings.ocr_enabled:
            raise ParserUnavailable("PaddleOCR is disabled")
        try:
            paddle_module = importlib.import_module("paddleocr")
        except ImportError as error:
            raise ParserUnavailable("paddleocr is not installed") from error
        pymupdf = _pymupdf_module()
        work_root = Path("tmp") / "pdfs"
        work_root.mkdir(parents=True, exist_ok=True)
        try:
            with TemporaryDirectory(prefix="paddle-", dir=work_root) as directory:
                document = pymupdf.open(stream=content, filetype="pdf")
                ocr = paddle_module.PaddleOCR(
                    text_detection_model_name="PP-OCRv5_mobile_det",
                    text_recognition_model_name="devanagari_PP-OCRv5_mobile_rec",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
                pages: list[ParsedPage] = []
                raw_pages: list[dict[str, object]] = []
                for page_index, page in enumerate(document):
                    page_number = page_index + 1
                    image_path = Path(directory) / f"page-{page_number}.png"
                    page.get_pixmap(dpi=self._settings.ocr_render_dpi).save(image_path)
                    results = list(ocr.predict(str(image_path)))
                    blocks: list[ParsedBlock] = []
                    serialized_results: list[dict[str, object]] = []
                    for result in results:
                        payload = getattr(result, "json", result)
                        if callable(payload):
                            payload = payload()
                        if not isinstance(payload, dict):
                            continue
                        serialized_results.append(payload)
                        data = payload.get("res", payload)
                        if not isinstance(data, dict):
                            continue
                        texts = data.get("rec_texts", [])
                        scores = data.get("rec_scores", [])
                        boxes = data.get("rec_boxes", [])
                        if not isinstance(texts, list):
                            continue
                        for index, text in enumerate(texts):
                            raw_box = boxes[index] if index < len(boxes) else None
                            bounding_box = None
                            if (
                                isinstance(raw_box, list | tuple)
                                and len(raw_box) >= 4
                            ):
                                scale = 72 / self._settings.ocr_render_dpi
                                left, top, right, bottom = (
                                    float(value) * scale for value in raw_box[:4]
                                )
                                bounding_box = BoundingBox(
                                    left=left,
                                    bottom=float(page.rect.height) - bottom,
                                    right=right,
                                    top=float(page.rect.height) - top,
                                )
                            score = (
                                float(scores[index])
                                if isinstance(scores, list) and index < len(scores)
                                else None
                            )
                            blocks.append(
                                ParsedBlock(
                                    block_id=f"paddle-{page_number}-{len(blocks)}",
                                    page_number=page_number,
                                    reading_order=len(blocks),
                                    kind=BlockKind.PARAGRAPH,
                                    text=str(text),
                                    bounding_box=bounding_box,
                                    ocr_confidence=score,
                                )
                            )
                    raw_pages.append({"page_number": page_number, "results": serialized_results})
                    pages.append(
                        ParsedPage(
                            page_number=page_number,
                            width=float(page.rect.width),
                            height=float(page.rect.height),
                            blocks=tuple(blocks),
                        )
                    )
                document.close()
                return ParserOutput(
                    parser=self.name,
                    parser_version=_package_version("paddleocr"),
                    mode="ocr-fallback",
                    page_count=len(pages),
                    pages=tuple(pages),
                    markdown="\n\n".join(
                        block.text for page in pages for block in page.blocks
                    ),
                    raw_payload={"pages": raw_pages},
                )
        except ParserUnavailable:
            raise
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            raise TransientParserError("PaddleOCR conversion failed") from error


def default_parser_adapters(settings: Settings) -> tuple[ParserAdapter, ...]:
    """Return the single production extraction adapter.

    PyMuPDF/pdfplumber remain an independent verifier only; they are not parser
    fallbacks. The optional adapter classes remain available to isolated Phase 06
    evaluation tests and callers that explicitly construct a custom engine.
    """

    return (OpenDataLoaderAdapter(settings),)
