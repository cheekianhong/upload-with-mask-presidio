"""Local PII analysis and irreversible visual redaction helpers."""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import pymupdf as fitz
import spacy
import pytesseract
from PIL import Image, ImageDraw, ImageOps
from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
from pytesseract import Output


MAX_PDF_PAGES = 300
OCR_SCALE = 3.0
SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


@dataclass(frozen=True)
class Detection:
    id: str
    page: int
    entity: str
    text: str
    score: float
    boxes: list[list[float]]
    source: str


def _clean_rgb(image: Image.Image) -> Image.Image:
    """Apply orientation and copy pixels into a fresh image with no source metadata."""
    oriented = ImageOps.exif_transpose(image).convert("RGB")
    clean = Image.new("RGB", oriented.size)
    clean.paste(oriented)
    return clean


def load_analyzer() -> AnalyzerEngine:
    """Load the English Presidio analyzer without downloading models at runtime."""
    if not spacy.util.is_package("en_core_web_sm"):
        raise RuntimeError(
            "The local English language model is missing. In the app folder, run: "
            "uv run python -m spacy download en_core_web_sm"
        )
    config = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
    }
    engine = NlpEngineProvider(nlp_configuration=config).create_engine()
    return AnalyzerEngine(nlp_engine=engine, supported_languages=["en"])


def file_kind(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in SUPPORTED_IMAGES:
        return "image"
    raise ValueError("Choose a PDF, JPEG, PNG, TIFF, WebP, or BMP file.")


def _rect_list(rect: Iterable[float]) -> list[float]:
    return [round(float(part), 2) for part in rect]


def _text_and_tokens(
    words: Iterable[tuple[str, tuple[float, float, float, float]]],
) -> tuple[str, list[tuple[int, int, tuple[float, float, float, float]]]]:
    pieces: list[str] = []
    tokens: list[tuple[int, int, tuple[float, float, float, float]]] = []
    cursor = 0
    for text, rect in words:
        text = text.strip()
        if not text:
            continue
        if pieces:
            pieces.append(" ")
            cursor += 1
        start = cursor
        pieces.append(text)
        cursor += len(text)
        tokens.append((start, cursor, rect))
    return "".join(pieces), tokens


def _ocr_words(image: Image.Image) -> list[tuple[str, tuple[float, float, float, float]]]:
    try:
        data = pytesseract.image_to_data(
            image, lang="eng", output_type=Output.DICT, config="--psm 3"
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Tesseract OCR is not installed or not on PATH. On macOS, install it "
            "with `brew install tesseract`, then restart this app."
        ) from exc
    except pytesseract.TesseractError as exc:
        raise RuntimeError(f"Tesseract could not read this page: {exc}") from exc

    words: list[tuple[str, tuple[float, float, float, float]]] = []
    for index, raw_text in enumerate(data["text"]):
        text = str(raw_text).strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1
        if confidence < 0:
            continue
        left = float(data["left"][index])
        top = float(data["top"][index])
        right = left + float(data["width"][index])
        bottom = top + float(data["height"][index])
        words.append((text, (left, top, right, bottom)))
    return words


def _boxes_for_span(
    start: int,
    end: int,
    tokens: list[tuple[int, int, tuple[float, float, float, float]]],
    pad: float,
    bounds: tuple[float, float],
) -> list[list[float]]:
    width, height = bounds
    boxes: list[list[float]] = []
    for token_start, token_end, rect in tokens:
        if token_start >= end or token_end <= start:
            continue
        x0, y0, x1, y1 = rect
        boxes.append(
            [
                max(0.0, x0 - pad),
                max(0.0, y0 - pad),
                min(width, x1 + pad),
                min(height, y1 + pad),
            ]
        )
    return boxes


def _analyze_text(
    analyzer: AnalyzerEngine,
    text: str,
    tokens: list[tuple[int, int, tuple[float, float, float, float]]],
    page: int,
    bounds: tuple[float, float],
    manual_terms: list[str],
    min_score: float,
    pad: float,
    source: str,
) -> list[Detection]:
    if not text.strip():
        return []

    found: list[tuple[int, int, str, float, str]] = []
    for result in analyzer.analyze(
        text=text,
        language="en",
        score_threshold=min_score,
    ):
        found.append(
            (int(result.start), int(result.end), str(result.entity_type), float(result.score), "presidio")
        )

    for term in manual_terms:
        term = term.strip()
        if len(term) < 2:
            continue
        for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            found.append((match.start(), match.end(), "MANUAL_REDACTION", 1.0, "manual"))

    found.sort(key=lambda item: (item[0], item[1], item[2]))
    detections: list[Detection] = []
    for index, (start, end, entity, score, detection_source) in enumerate(found):
        snippet = text[start:end].strip()
        boxes = _boxes_for_span(start, end, tokens, pad, bounds)
        if not snippet or not boxes:
            continue
        boxes = [_rect_list(box) for box in boxes]
        detections.append(
            Detection(
                id=f"p{page}-d{index}-{start}-{end}",
                page=page,
                entity=entity,
                text=snippet,
                score=score,
                boxes=boxes,
                source=detection_source,
            )
        )
    return detections


def _image_words(image: Image.Image) -> tuple[str, list[tuple[int, int, tuple[float, float, float, float]]]]:
    image = _clean_rgb(image)
    words = _ocr_words(image)
    text, pixel_tokens = _text_and_tokens(words)
    return text, pixel_tokens


def _pdf_text_words(page: fitz.Page) -> list[tuple[str, tuple[float, float, float, float]]]:
    words = page.get_text("words", sort=True)
    return [
        (str(word[4]), (float(word[0]), float(word[1]), float(word[2]), float(word[3])))
        for word in words
        if len(word) >= 5 and str(word[4]).strip()
    ]


def _ocr_pdf_page(
    page: fitz.Page,
) -> tuple[str, list[tuple[int, int, tuple[float, float, float, float]]]]:
    pix = page.get_pixmap(matrix=fitz.Matrix(OCR_SCALE, OCR_SCALE), alpha=False)
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    words = _ocr_words(image)
    text, pixel_tokens = _text_and_tokens(words)
    x_scale = pix.width / page.rect.width
    y_scale = pix.height / page.rect.height
    page_tokens = []
    for start, end, rect in pixel_tokens:
        display_rect = fitz.Rect(
            rect[0] / x_scale,
            rect[1] / y_scale,
            rect[2] / x_scale,
            rect[3] / y_scale,
        )
        # OCR runs on the rotated page image, while PyMuPDF redaction coordinates
        # are relative to the unrotated page.
        unrotated_rect = display_rect * page.derotation_matrix
        page_tokens.append((start, end, tuple(unrotated_rect)))
    return text, page_tokens


def _preview_image(
    image: Image.Image,
    detections: list[Detection],
    page: int,
    bounds: tuple[float, float],
    rotation_matrix: fitz.Matrix | None = None,
) -> bytes:
    image = _clean_rgb(image)
    original_width, original_height = image.size
    max_side = 1600
    preview_scale = min(1.0, max_side / max(original_width, original_height))
    if preview_scale < 1.0:
        image = image.resize(
            (max(1, round(original_width * preview_scale)), max(1, round(original_height * preview_scale))),
            Image.Resampling.LANCZOS,
        )
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    bounds_width, bounds_height = bounds
    sx = image.width / bounds_width
    sy = image.height / bounds_height
    for detection in detections:
        if detection.page != page:
            continue
        for x0, y0, x1, y1 in detection.boxes:
            rect = fitz.Rect(x0, y0, x1, y1)
            if rotation_matrix is not None:
                rect = rect * rotation_matrix
            x0, y0, x1, y1 = rect
            box = (round(x0 * sx), round(y0 * sy), round(x1 * sx), round(y1 * sy))
            draw.rectangle(box, fill=(199, 77, 54, 55), outline=(199, 77, 54, 245), width=3)
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def analyze_file(
    data: bytes,
    filename: str,
    analyzer: AnalyzerEngine,
    manual_terms: list[str] | None = None,
    min_score: float = 0.4,
    ocr_every_pdf_page: bool = False,
) -> dict[str, Any]:
    """Return mapped detections and review previews for a PDF or raster image."""
    kind = file_kind(filename)
    manual_terms = manual_terms or []
    detections: list[Detection] = []
    previews: list[bytes] = []

    if kind == "image":
        try:
            image = _clean_rgb(Image.open(io.BytesIO(data)))
        except Exception as exc:
            raise ValueError(f"This image could not be opened: {exc}") from exc
        text, tokens = _image_words(image)
        detections = _analyze_text(
            analyzer,
            text,
            tokens,
            page=0,
            bounds=(float(image.width), float(image.height)),
            manual_terms=manual_terms,
            min_score=min_score,
            pad=3.0,
            source="ocr",
        )
        previews = [_preview_image(image, detections, 0, (image.width, image.height))]
        return {
            "kind": kind,
            "page_count": 1,
            "ocr_pages": 1,
            "detections": [asdict(detection) for detection in detections],
            "previews": previews,
            "dimensions": [(image.width, image.height)],
            "search_pages": [
                {
                    "text": text,
                    "tokens": tokens,
                    "bounds": (float(image.width), float(image.height)),
                    "pad": 3.0,
                }
            ],
        }

    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"This PDF could not be opened: {exc}") from exc
    if document.needs_pass:
        document.close()
        raise ValueError("Password-protected PDFs are not supported. Remove the password locally first.")
    if len(document) > MAX_PDF_PAGES:
        document.close()
        raise ValueError(f"This PDF has more than {MAX_PDF_PAGES} pages. Split it locally and try a smaller file.")
    if len(document) == 0:
        document.close()
        raise ValueError("This PDF has no pages.")

    dimensions: list[tuple[float, float]] = []
    search_pages: list[dict[str, Any]] = []
    ocr_pages = 0
    try:
        for page_index, page in enumerate(document):
            dimensions.append((float(page.rect.width), float(page.rect.height)))
            unrotated_bounds = (float(page.cropbox.width), float(page.cropbox.height))
            use_ocr = ocr_every_pdf_page
            native_words = _pdf_text_words(page)
            if not native_words:
                use_ocr = True
            if use_ocr:
                ocr_pages += 1
                text, tokens = _ocr_pdf_page(page)
                source = "ocr"
                pad = 1.5
            else:
                text, tokens = _text_and_tokens(native_words)
                source = "pdf text"
                pad = 1.5
            search_pages.append(
                {
                    "text": text,
                    "tokens": tokens,
                    "bounds": unrotated_bounds,
                    "pad": pad,
                }
            )
            detections.extend(
                _analyze_text(
                    analyzer,
                    text,
                    tokens,
                    page=page_index,
                    bounds=unrotated_bounds,
                    manual_terms=manual_terms,
                    min_score=min_score,
                    pad=pad,
                    source=source,
                )
            )
            preview_pix = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            preview = Image.open(io.BytesIO(preview_pix.tobytes("png"))).convert("RGB")
            previews.append(
                _preview_image(
                    preview,
                    detections,
                    page_index,
                    (page.rect.width, page.rect.height),
                    rotation_matrix=page.rotation_matrix,
                )
            )
    finally:
        document.close()

    return {
        "kind": kind,
        "page_count": len(dimensions),
        "ocr_pages": ocr_pages,
        "detections": [asdict(detection) for detection in detections],
        "previews": previews,
        "dimensions": dimensions,
        "search_pages": search_pages,
    }


def find_text_matches(
    search_pages: list[dict[str, Any]], terms: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Find case-insensitive whole-word or phrase matches on extracted page text."""
    detections: list[dict[str, Any]] = []
    missing_terms: list[str] = []
    for term_index, term in enumerate(terms):
        normalized_term = " ".join(term.split())
        if not normalized_term:
            continue
        leading_boundary = r"(?<!\w)" if normalized_term[0].isalnum() or normalized_term[0] == "_" else ""
        trailing_boundary = r"(?!\w)" if normalized_term[-1].isalnum() or normalized_term[-1] == "_" else ""
        pattern = re.compile(
            f"{leading_boundary}{re.escape(normalized_term)}{trailing_boundary}",
            flags=re.IGNORECASE,
        )
        term_matches = 0
        for page_index, page in enumerate(search_pages):
            page_text = str(page["text"])
            tokens = page["tokens"]
            bounds = tuple(page["bounds"])
            pad = float(page["pad"])
            for match_index, match in enumerate(pattern.finditer(page_text)):
                rects = _boxes_for_span(
                    match.start(), match.end(), tokens, pad=pad, bounds=bounds
                )
                if not rects:
                    continue
                detections.append(
                    {
                        "id": f"search-t{term_index}-p{page_index}-m{match_index}",
                        "page": page_index,
                        "entity": f"SEARCH TERM: {normalized_term}",
                        "text": match.group(0),
                        "score": 1.0,
                        "boxes": [_rect_list(rect) for rect in rects],
                        "source": "search",
                    }
                )
                term_matches += 1
        if not term_matches:
            missing_terms.append(normalized_term)
    return detections, missing_terms


def render_previews_for_pages(
    data: bytes,
    filename: str,
    detections: list[dict[str, Any]],
    page_indices: set[int],
) -> dict[int, bytes]:
    """Render updated page previews for search results without rerunning OCR or analysis."""
    if not page_indices:
        return {}
    kind = file_kind(filename)
    if kind == "image":
        if 0 not in page_indices:
            return {}
        try:
            image = _clean_rgb(Image.open(io.BytesIO(data)))
        except Exception as exc:
            raise ValueError(f"This image could not be opened: {exc}") from exc
        return {
            0: _preview_image(
                image, detections, 0, (float(image.width), float(image.height))
            )
        }

    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"This PDF could not be opened: {exc}") from exc
    try:
        if document.needs_pass:
            raise ValueError("Password-protected PDFs are not supported.")
        previews: dict[int, bytes] = {}
        for page_index in sorted(page_indices):
            if page_index < 0 or page_index >= len(document):
                continue
            page = document[page_index]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            previews[page_index] = _preview_image(
                image,
                detections,
                page_index,
                (page.rect.width, page.rect.height),
                rotation_matrix=page.rotation_matrix,
            )
        return previews
    finally:
        document.close()


def _selected_by_page(detections: list[dict[str, Any]], selected_ids: set[str]) -> dict[int, list[list[float]]]:
    boxes_by_page: dict[int, list[list[float]]] = {}
    for detection in detections:
        if detection["id"] not in selected_ids:
            continue
        boxes_by_page.setdefault(int(detection["page"]), []).extend(detection["boxes"])
    return boxes_by_page


def create_redacted_output(
    data: bytes,
    filename: str,
    detections: list[dict[str, Any]],
    selected_ids: set[str],
    pdf_scale: float = 2.2,
) -> tuple[bytes, str, str]:
    """Create a flattened PDF or metadata-free PNG with selected boxes painted black."""
    if not selected_ids:
        raise ValueError("Select at least one detection or add a manual phrase before creating a copy.")
    kind = file_kind(filename)
    boxes_by_page = _selected_by_page(detections, selected_ids)

    if kind == "image":
        try:
            image = _clean_rgb(Image.open(io.BytesIO(data)))
        except Exception as exc:
            raise ValueError(f"This image could not be opened: {exc}") from exc
        draw = ImageDraw.Draw(image)
        for x0, y0, x1, y1 in boxes_by_page.get(0, []):
            draw.rectangle((round(x0), round(y0), round(x1), round(y1)), fill=(0, 0, 0))
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue(), f"{Path(filename).stem}_masked.png", "image/png"

    try:
        source = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"This PDF could not be opened: {exc}") from exc
    if source.needs_pass:
        source.close()
        raise ValueError("Password-protected PDFs are not supported.")
    if len(source) > MAX_PDF_PAGES:
        source.close()
        raise ValueError(f"This PDF has more than {MAX_PDF_PAGES} pages.")

    try:
        for page_index, page in enumerate(source):
            for box in boxes_by_page.get(page_index, []):
                page.add_redact_annot(fitz.Rect(box), fill=(0, 0, 0), cross_out=False)
            page.apply_redactions(images=2, graphics=1, text=0)

        # Rebuild from rendered pages so the download contains no source text layer,
        # annotations, forms, bookmarks, or source metadata behind the visible page.
        clean = fitz.open()
        try:
            for page in source:
                pix = page.get_pixmap(matrix=fitz.Matrix(pdf_scale, pdf_scale), alpha=False)
                clean_page = clean.new_page(width=page.rect.width, height=page.rect.height)
                clean_page.insert_image(clean_page.rect, stream=pix.tobytes("png"), keep_proportion=False)
            clean.set_metadata(
                {
                    "title": "",
                    "author": "",
                    "subject": "",
                    "keywords": "",
                    "creator": "Local File Masking App",
                    "producer": "",
                    "creationDate": "",
                    "modDate": "",
                    "trapped": "",
                }
            )
            clean.set_toc([])
            clean.del_xml_metadata()
            output = clean.tobytes(garbage=4, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)
        finally:
            clean.close()
    finally:
        source.close()
    return output, f"{Path(filename).stem}_masked.pdf", "application/pdf"
