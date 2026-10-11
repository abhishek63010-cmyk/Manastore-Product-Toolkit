import io
import shutil
from pathlib import Path
from typing import Any, Optional, Union

from src.ocr.base import BaseOCRExtractor
from src.ocr.models import BoundingBox, OCRResult, OCRStatus
from src.ocr.parser import ConservativeCandidateParser

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


class TesseractOCRExtractor(BaseOCRExtractor):
    """
    Real OCR extractor using Tesseract OCR (via pytesseract wrapper).
    
    Safety & Configuration:
    - Gracefully detects missing binary and missing Python dependencies.
    - Zero crashes or import errors when Tesseract is not installed.
    - Never leaks environment variables or credentials in error messages.
    """

    provider_name: str = "tesseract_ocr"

    def __init__(self, tesseract_cmd: Optional[str] = None):
        self.tesseract_cmd = tesseract_cmd
        self._checked_availability: Optional[bool] = None

    def is_available(self) -> bool:
        """Check if pytesseract library and tesseract binary are available."""
        if self._checked_availability is not None:
            return self._checked_availability

        try:
            import pytesseract
            from PIL import Image

            # Check explicit command or system PATH
            cmd = self.tesseract_cmd or getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
            if shutil.which(cmd) or Path(cmd).is_file():
                self._checked_availability = True
            else:
                self._checked_availability = False
        except ImportError:
            self._checked_availability = False

        return self._checked_availability

    def extract_from_file(
        self,
        file_path: Union[str, Path],
        batch_id: Optional[str] = None,
    ) -> OCRResult:
        path = Path(file_path)
        filename = path.name

        if not self.is_available():
            return self._unavailable_result(str(path), filename, batch_id)

        if not path.exists():
            return OCRResult(
                status=OCRStatus.FAILED,
                source_identity=str(path),
                source_filename=filename,
                provider=self.provider_name,
                batch_id=batch_id,
                error_message=f"File not found: {path}",
            )

        ext = path.suffix.lower()
        if ext not in SUPPORTED_IMAGE_EXTENSIONS:
            return OCRResult(
                status=OCRStatus.UNSUPPORTED_FORMAT,
                source_identity=str(path),
                source_filename=filename,
                provider=self.provider_name,
                batch_id=batch_id,
                error_message=f"Unsupported image format '{ext}' for Tesseract OCR",
            )

        try:
            from PIL import Image
            with Image.open(path) as img:
                return self._run_tesseract(img, str(path), filename, batch_id)
        except Exception as e:
            return OCRResult(
                status=OCRStatus.FAILED,
                source_identity=str(path),
                source_filename=filename,
                provider=self.provider_name,
                batch_id=batch_id,
                error_message=f"Image reading error: {type(e).__name__}",
            )

    def extract_from_bytes(
        self,
        data: bytes,
        filename: str,
        batch_id: Optional[str] = None,
    ) -> OCRResult:
        if not self.is_available():
            return self._unavailable_result(filename, filename, batch_id)

        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_IMAGE_EXTENSIONS:
            return OCRResult(
                status=OCRStatus.UNSUPPORTED_FORMAT,
                source_identity=filename,
                source_filename=filename,
                provider=self.provider_name,
                batch_id=batch_id,
                error_message=f"Unsupported image format '{ext}' for Tesseract OCR",
            )

        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as img:
                return self._run_tesseract(img, filename, filename, batch_id)
        except Exception as e:
            return OCRResult(
                status=OCRStatus.FAILED,
                source_identity=filename,
                source_filename=filename,
                provider=self.provider_name,
                batch_id=batch_id,
                error_message=f"Image decoding error: {type(e).__name__}",
            )

    def _run_tesseract(
        self,
        img: Any,
        source_identity: str,
        filename: str,
        batch_id: Optional[str],
    ) -> OCRResult:
        import pytesseract

        if self.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd

        # 1. Extract text
        raw_text = pytesseract.image_to_string(img)
        cleaned_text = raw_text.strip()

        if not cleaned_text:
            return OCRResult(
                status=OCRStatus.NO_TEXT_DETECTED,
                source_identity=source_identity,
                source_filename=filename,
                provider=self.provider_name,
                raw_text="",
                confidence=1.0,
                batch_id=batch_id,
            )

        # 2. Extract bounding boxes & confidence via image_to_data
        bboxes: list[BoundingBox] = []
        confidences: list[float] = []

        try:
            data_dict = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            n_boxes = len(data_dict.get("text", []))
            for i in range(n_boxes):
                box_text = data_dict["text"][i].strip()
                raw_conf = data_dict["conf"][i]
                if box_text and raw_conf != "-1":
                    conf_val = float(raw_conf) / 100.0
                    confidences.append(conf_val)
                    bboxes.append(
                        BoundingBox(
                            x=float(data_dict["left"][i]),
                            y=float(data_dict["top"][i]),
                            width=float(data_dict["width"][i]),
                            height=float(data_dict["height"][i]),
                            confidence=conf_val,
                            text=box_text,
                        )
                    )
        except Exception:
            # Non-blocking if bounding box parse fails
            pass

        avg_conf = sum(confidences) / len(confidences) if confidences else 0.8
        candidates = ConservativeCandidateParser.parse(cleaned_text)

        return OCRResult(
            status=OCRStatus.SUCCESS,
            source_identity=source_identity,
            source_filename=filename,
            provider=self.provider_name,
            raw_text=cleaned_text,
            confidence=round(avg_conf, 2),
            bounding_boxes=bboxes,
            candidate_fields=candidates,
            batch_id=batch_id,
        )

    def _unavailable_result(
        self,
        source_identity: str,
        filename: str,
        batch_id: Optional[str],
    ) -> OCRResult:
        return OCRResult(
            status=OCRStatus.PROVIDER_UNAVAILABLE,
            source_identity=source_identity,
            source_filename=filename,
            provider=self.provider_name,
            batch_id=batch_id,
            error_message=(
                "Tesseract OCR is not installed or not found in system PATH. "
                "Setup instructions: "
                "1. Windows: Install Tesseract via https://github.com/UB-Mannheim/tesseract/wiki "
                "2. Linux: 'sudo apt install tesseract-ocr' "
                "3. Python: 'pip install pytesseract'"
            ),
        )
