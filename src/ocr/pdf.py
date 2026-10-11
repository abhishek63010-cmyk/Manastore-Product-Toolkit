from pathlib import Path
import re
from typing import Optional, Union
import zlib

from src.ocr.base import BasePDFExtractor
from src.ocr.models import OCRResult, OCRStatus
from src.ocr.parser import ConservativeCandidateParser


class PDFTextExtractor(BasePDFExtractor):
    """
    Document text extractor for PDF catalogues and brochures.
    
    Principles:
    - Never fabricates text for scanned raster pages.
    - Accurately distinguishes text-bearing digital PDFs from scanned image PDFs.
    - Emits OCR_REQUIRED status when scanned pages lack an embedded text layer.
    """

    provider_name: str = "pdf_text_extractor"

    def is_available(self) -> bool:
        """PDF extractor has standard-library native support + optional pypdf."""
        return True

    def extract_pdf(
        self,
        pdf_path: Union[str, Path],
        batch_id: Optional[str] = None,
    ) -> list[OCRResult]:
        path = Path(pdf_path)
        filename = path.name

        if not path.exists():
            return [
                OCRResult(
                    status=OCRStatus.FAILED,
                    source_identity=str(path),
                    source_filename=filename,
                    provider=self.provider_name,
                    batch_id=batch_id,
                    error_message=f"PDF file not found: {path}",
                )
            ]

        if path.suffix.lower() != ".pdf":
            return [
                OCRResult(
                    status=OCRStatus.UNSUPPORTED_FORMAT,
                    source_identity=str(path),
                    source_filename=filename,
                    provider=self.provider_name,
                    batch_id=batch_id,
                    error_message=f"Unsupported document format '{path.suffix.lower()}'. Expected '.pdf'",
                )
            ]

        data = path.read_bytes()
        return self.extract_pdf_bytes(data, filename, source_identity=str(path.resolve()), batch_id=batch_id)

    def extract_pdf_bytes(
        self,
        data: bytes,
        filename: str,
        source_identity: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> list[OCRResult]:
        src_id = source_identity or filename

        # 1. Try external libraries if installed (pypdf or pypdf2)
        external_results = self._try_external_pdf_libraries(data, filename, src_id, batch_id)
        if external_results is not None:
            return external_results

        # 2. Native pure-Python PDF stream inspector
        return self._extract_native_pdf_streams(data, filename, src_id, batch_id)

    def _try_external_pdf_libraries(
        self,
        data: bytes,
        filename: str,
        source_identity: str,
        batch_id: Optional[str],
    ) -> Optional[list[OCRResult]]:
        try:
            import io
            import pypdf

            reader = pypdf.PdfReader(io.BytesIO(data))
            results: list[OCRResult] = []

            for idx, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                cleaned = text.strip()

                if cleaned:
                    candidates = ConservativeCandidateParser.parse(cleaned)
                    results.append(
                        OCRResult(
                            status=OCRStatus.SUCCESS,
                            source_identity=source_identity,
                            source_filename=filename,
                            page_number=idx,
                            provider=self.provider_name,
                            raw_text=cleaned,
                            confidence=1.0,
                            candidate_fields=candidates,
                            batch_id=batch_id,
                        )
                    )
                else:
                    results.append(
                        OCRResult(
                            status=OCRStatus.OCR_REQUIRED,
                            source_identity=source_identity,
                            source_filename=filename,
                            page_number=idx,
                            provider=self.provider_name,
                            raw_text="",
                            batch_id=batch_id,
                            error_message="Page contains scanned raster images without an embedded text layer. OCR required.",
                        )
                    )
            return results
        except ImportError:
            return None
        except Exception as e:
            return [
                OCRResult(
                    status=OCRStatus.FAILED,
                    source_identity=source_identity,
                    source_filename=filename,
                    provider=self.provider_name,
                    batch_id=batch_id,
                    error_message=f"PDF parsing error: {type(e).__name__}",
                )
            ]

    def _extract_native_pdf_streams(
        self,
        data: bytes,
        filename: str,
        source_identity: str,
        batch_id: Optional[str],
    ) -> list[OCRResult]:
        """
        Lightweight native PDF parser using stream decompression and regex operators.
        Distinguishes text streams (BT ... ET) from purely scanned/image streams.
        """
        extracted_text_chunks: list[str] = []
        has_images = bool(b"/Image" in data or b"/XObject" in data)

        # Find all streams: stream ... endstream
        stream_matches = re.finditer(b"stream[\r\n]+(.*?)[\r\n]+endstream", data, re.DOTALL)
        for sm in stream_matches:
            raw_stream = sm.group(1)
            decompressed = None

            # Attempt FlateDecode decompression
            try:
                decompressed = zlib.decompress(raw_stream)
            except Exception:
                decompressed = raw_stream

            if decompressed and (b"BT" in decompressed and b"ET" in decompressed):
                # Extract text within parentheses: (Sample Text) Tj / TJ
                text_matches = re.findall(rb"\(([^)]+)\)\s*(?:Tj|TJ|'|\")", decompressed)
                if text_matches:
                    for tm in text_matches:
                        try:
                            decoded = tm.decode("utf-8", errors="ignore").strip()
                            if decoded and len(decoded) > 1:
                                extracted_text_chunks.append(decoded)
                        except Exception:
                            continue

        combined_text = "\n".join(extracted_text_chunks).strip()

        if combined_text:
            candidates = ConservativeCandidateParser.parse(combined_text)
            return [
                OCRResult(
                    status=OCRStatus.SUCCESS,
                    source_identity=source_identity,
                    source_filename=filename,
                    page_number=1,
                    provider=self.provider_name,
                    raw_text=combined_text,
                    confidence=0.9,
                    candidate_fields=candidates,
                    batch_id=batch_id,
                )
            ]

        # No text found in streams
        if has_images:
            return [
                OCRResult(
                    status=OCRStatus.OCR_REQUIRED,
                    source_identity=source_identity,
                    source_filename=filename,
                    page_number=1,
                    provider=self.provider_name,
                    raw_text="",
                    batch_id=batch_id,
                    error_message=(
                        "PDF appears to be a scanned document or raster catalogue with no embedded text layer. "
                        "OCR provider required."
                    ),
                )
            ]

        return [
            OCRResult(
                status=OCRStatus.NO_TEXT_DETECTED,
                source_identity=source_identity,
                source_filename=filename,
                page_number=1,
                provider=self.provider_name,
                raw_text="",
                confidence=1.0,
                batch_id=batch_id,
            )
        ]
