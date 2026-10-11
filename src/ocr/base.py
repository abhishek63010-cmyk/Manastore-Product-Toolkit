from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union

from src.ocr.models import OCRResult


class BaseOCRExtractor(ABC):
    """
    Abstract interface for pluggable OCR extractors.
    
    Principles:
    - Decoupled from specific OCR engines (Tesseract, cloud APIs, test doubles).
    - Guarantees structured OCRResult return values without leaking secrets in errors.
    - Preserves source file identity, page numbering, and batch ID.
    - Never mutates or overwrites source assets.
    """

    provider_name: str = "base_ocr_extractor"

    @abstractmethod
    def is_available(self) -> bool:
        """Check whether the underlying OCR engine, binary, or API is available."""
        pass

    @abstractmethod
    def extract_from_file(
        self,
        file_path: Union[str, Path],
        batch_id: Optional[str] = None,
    ) -> OCRResult:
        """Extract text and candidate fields from a local image file."""
        pass

    @abstractmethod
    def extract_from_bytes(
        self,
        data: bytes,
        filename: str,
        batch_id: Optional[str] = None,
    ) -> OCRResult:
        """Extract text and candidate fields from raw binary image data."""
        pass


class BasePDFExtractor(ABC):
    """
    Abstract interface for document / PDF text extractors.
    """

    provider_name: str = "base_pdf_extractor"

    @abstractmethod
    def is_available(self) -> bool:
        """Check whether PDF extraction capability is available."""
        pass

    @abstractmethod
    def extract_pdf(
        self,
        pdf_path: Union[str, Path],
        batch_id: Optional[str] = None,
    ) -> list[OCRResult]:
        """Extract text across pages from a local PDF document."""
        pass
