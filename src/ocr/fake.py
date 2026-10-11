from pathlib import Path
from typing import Any, Optional, Union

from src.ocr.base import BaseOCRExtractor
from src.ocr.models import BoundingBox, OCRResult, OCRStatus
from src.ocr.parser import ConservativeCandidateParser

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


class FakeOCRExtractor(BaseOCRExtractor):
    """
    Deterministic fake OCR extractor for unit and pipeline tests.
    
    Guarantees:
    - 0ms execution without network, API keys, ML frameworks, or system binaries.
    - Fully configurable responses via exact or substring filename mapping.
    - Safe simulation of edge cases (no text, unsupported format, unavailable engine, errors).
    """

    provider_name: str = "fake_ocr"

    def __init__(
        self,
        mock_data: Optional[dict[str, str]] = None,
        default_text: str = "",
        confidence: float = 0.95,
        available: bool = True,
        simulate_failure: bool = False,
        simulate_no_text: bool = False,
        simulate_unsupported: bool = False,
    ):
        self.mock_data: dict[str, str] = mock_data or {}
        self.default_text = default_text
        self.default_confidence = confidence
        self._available = available
        self.simulate_failure = simulate_failure
        self.simulate_no_text = simulate_no_text
        self.simulate_unsupported = simulate_unsupported

    def is_available(self) -> bool:
        return self._available

    def register_mock_text(self, filename_or_key: str, text: str) -> None:
        """Register specific mock text for a given filename or key."""
        self.mock_data[filename_or_key] = text

    def extract_from_file(
        self,
        file_path: Union[str, Path],
        batch_id: Optional[str] = None,
    ) -> OCRResult:
        path = Path(file_path)
        filename = path.name

        if not path.exists():
            return OCRResult(
                status=OCRStatus.FAILED,
                source_identity=str(path),
                source_filename=filename,
                provider=self.provider_name,
                batch_id=batch_id,
                error_message=f"File not found: {path}",
            )

        data = path.read_bytes()
        return self._process_extraction(
            source_identity=str(path.resolve()),
            filename=filename,
            data=data,
            batch_id=batch_id,
        )

    def extract_from_bytes(
        self,
        data: bytes,
        filename: str,
        batch_id: Optional[str] = None,
    ) -> OCRResult:
        return self._process_extraction(
            source_identity=filename,
            filename=filename,
            data=data,
            batch_id=batch_id,
        )

    def _process_extraction(
        self,
        source_identity: str,
        filename: str,
        data: bytes,
        batch_id: Optional[str] = None,
    ) -> OCRResult:
        # 1. Provider availability check
        if not self.is_available():
            return OCRResult(
                status=OCRStatus.PROVIDER_UNAVAILABLE,
                source_identity=source_identity,
                source_filename=filename,
                provider=self.provider_name,
                batch_id=batch_id,
                error_message="Fake OCR provider simulated as unavailable",
            )

        # 2. Simulated failure
        if self.simulate_failure:
            return OCRResult(
                status=OCRStatus.FAILED,
                source_identity=source_identity,
                source_filename=filename,
                provider=self.provider_name,
                batch_id=batch_id,
                error_message="Simulated OCR extraction failure",
            )

        # 3. Format validation
        ext = Path(filename).suffix.lower()
        if self.simulate_unsupported or ext not in SUPPORTED_IMAGE_EXTENSIONS:
            return OCRResult(
                status=OCRStatus.UNSUPPORTED_FORMAT,
                source_identity=source_identity,
                source_filename=filename,
                provider=self.provider_name,
                batch_id=batch_id,
                error_message=f"Unsupported image format '{ext}' for OCR processing",
            )

        # 4. Simulated no-text
        if self.simulate_no_text:
            return OCRResult(
                status=OCRStatus.NO_TEXT_DETECTED,
                source_identity=source_identity,
                source_filename=filename,
                provider=self.provider_name,
                raw_text="",
                confidence=1.0,
                batch_id=batch_id,
            )

        # 5. Text resolution from mock data
        text = self.default_text
        for key, val in self.mock_data.items():
            if key.lower() in filename.lower() or key == filename:
                text = val
                break

        if not text or not text.strip():
            return OCRResult(
                status=OCRStatus.NO_TEXT_DETECTED,
                source_identity=source_identity,
                source_filename=filename,
                provider=self.provider_name,
                raw_text="",
                confidence=self.default_confidence,
                batch_id=batch_id,
            )

        # 6. Parse candidate fields
        candidates = ConservativeCandidateParser.parse(text)

        # 7. Synthesize realistic mock bounding boxes for lines
        bboxes = []
        for line_idx, line in enumerate(text.splitlines()):
            if line.strip():
                bboxes.append(
                    BoundingBox(
                        x=10.0,
                        y=10.0 + (line_idx * 30.0),
                        width=250.0,
                        height=25.0,
                        confidence=self.default_confidence,
                        text=line.strip(),
                    )
                )

        return OCRResult(
            status=OCRStatus.SUCCESS,
            source_identity=source_identity,
            source_filename=filename,
            provider=self.provider_name,
            raw_text=text,
            confidence=self.default_confidence,
            bounding_boxes=bboxes,
            candidate_fields=candidates,
            batch_id=batch_id,
        )
