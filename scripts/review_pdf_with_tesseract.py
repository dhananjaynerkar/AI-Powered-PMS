"""Create an opt-in OCR review artifact without entering the production RAG path.

OpenDataLoader remains the only production parser. This utility preserves the
source PDF and adds a transparent Tesseract text layer for human comparison.
It writes a sidecar JSON report; it does not register, canonicalize, chunk or
embed the OCR result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a human-review OCR overlay; never production RAG input."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--language",
        default="eng",
        help="Tesseract language data, e.g. eng+hin+mar",
    )
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--tesseract-cmd", default="tesseract")
    parser.add_argument("--tessdata-dir", type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    source = arguments.input.resolve()
    output = arguments.output.resolve()
    if not source.is_file():
        raise SystemExit(f"input PDF not found: {source}")
    if source == output:
        raise SystemExit("output must be different from input")
    if arguments.dpi < 72 or arguments.dpi > 600:
        raise SystemExit("--dpi must be between 72 and 600")

    try:
        import fitz
        import pytesseract
        from PIL import Image
        from pytesseract import Output
    except ImportError as error:
        raise SystemExit(
            "OCR review dependencies are optional and not installed; install "
            "PyMuPDF, Pillow and pytesseract before running this utility."
        ) from error

    pytesseract.pytesseract.tesseract_cmd = arguments.tesseract_cmd
    if arguments.tessdata_dir:
        pytesseract.pytesseract.tessdata_dir_config = (
            f'--tessdata-dir "{arguments.tessdata_dir.resolve()}"'
        )

    source_document = fitz.open(source)
    if source_document.is_encrypted:
        raise SystemExit("encrypted PDFs are not supported by this review utility")
    review_document = fitz.open()
    review_document.insert_pdf(source_document)
    pages: list[dict[str, Any]] = []
    for page_number, page in enumerate(source_document, start=1):
        pixmap = page.get_pixmap(dpi=arguments.dpi, colorspace=fitz.csRGB, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        config = f"--oem 1 --psm 3 --dpi {arguments.dpi} -c preserve_interword_spaces=1"
        data = pytesseract.image_to_data(
            image,
            lang=arguments.language,
            config=config,
            output_type=Output.DICT,
        )
        token_indexes = [
            index for index, value in enumerate(data["text"]) if value.strip()
        ]
        confidence = [
            float(data["conf"][index])
            for index in token_indexes
            if float(data["conf"][index]) >= 0
        ]
        overlay_bytes = pytesseract.image_to_pdf_or_hocr(
            image,
            extension="pdf",
            lang=arguments.language,
            config=config + " -c textonly_pdf=1",
        )
        overlay = fitz.open(stream=overlay_bytes, filetype="pdf")
        review_document[page_number - 1].show_pdf_page(
            review_document[page_number - 1].rect,
            overlay,
            0,
            overlay=True,
            keep_proportion=False,
        )
        overlay.close()
        pages.append(
            {
                "page": page_number,
                "tokens": len(token_indexes),
                "characters": len(" ".join(data["text"][index] for index in token_indexes)),
                "mean_confidence": round(statistics.mean(confidence), 2) if confidence else 0.0,
                "low_confidence_tokens": sum(value < 50 for value in confidence),
            }
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    review_document.set_metadata(
        {
            **source_document.metadata,
            "producer": "PyMuPDF transparent Tesseract OCR review overlay",
            "subject": "Human-review artifact; not production canonical JSON",
        }
    )
    review_document.save(output, garbage=4, deflate=True, clean=True)
    review_document.close()
    source_document.close()
    report = {
        "source": str(source),
        "source_sha256": _sha256(source),
        "review_output": str(output),
        "review_output_sha256": _sha256(output),
        "language": arguments.language,
        "dpi": arguments.dpi,
        "pages": pages,
        "production_status": "not_registered",
        "next_action": "compare overlay with source and obtain reviewer approval",
    }
    report_path = output.with_suffix(output.suffix + ".review.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "report": str(report_path), "pages": len(pages)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
