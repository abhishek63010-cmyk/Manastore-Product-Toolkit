from typing import Callable, Optional

from src.ocr.base import BaseOCRExtractor, BasePDFExtractor
from src.ocr.fake import FakeOCRExtractor
from src.ocr.pdf import PDFTextExtractor
from src.ocr.tesseract import TesseractOCRExtractor


class OCRRegistry:
    """
    Registry for OCR and document text extraction providers.
    """

    _providers: dict[str, Callable[[], BaseOCRExtractor]] = {}
    _pdf_providers: dict[str, Callable[[], BasePDFExtractor]] = {}

    @classmethod
    def register(cls, name: str, factory: Callable[[], BaseOCRExtractor]) -> None:
        cls._providers[name.lower()] = factory

    @classmethod
    def register_pdf(cls, name: str, factory: Callable[[], BasePDFExtractor]) -> None:
        cls._pdf_providers[name.lower()] = factory

    @classmethod
    def list_providers(cls) -> list[str]:
        return sorted(cls._providers.keys())

    @classmethod
    def get_extractor(cls, provider_name: Optional[str] = None) -> BaseOCRExtractor:
        """
        Resolve an OCR extractor.
        If provider_name is omitted:
        - Tries 'tesseract' if available.
        - Otherwise returns 'fake' (deterministic fallback).
        """
        if provider_name:
            name_clean = provider_name.lower().strip()
            if name_clean in cls._providers:
                return cls._providers[name_clean]()
            raise ValueError(f"Unknown OCR provider '{provider_name}'. Available: {cls.list_providers()}")

        # Auto-selection: try real provider if available, otherwise fake
        tesseract = TesseractOCRExtractor()
        if tesseract.is_available():
            return tesseract
        return FakeOCRExtractor()

    @classmethod
    def get_pdf_extractor(cls, provider_name: Optional[str] = None) -> BasePDFExtractor:
        """Resolve a PDF text extractor."""
        if provider_name:
            name_clean = provider_name.lower().strip()
            if name_clean in cls._pdf_providers:
                return cls._pdf_providers[name_clean]()
            raise ValueError(f"Unknown PDF extractor '{provider_name}'.")

        return PDFTextExtractor()


# Register built-in providers
OCRRegistry.register("fake", lambda: FakeOCRExtractor())
OCRRegistry.register("tesseract", lambda: TesseractOCRExtractor())
OCRRegistry.register_pdf("native", lambda: PDFTextExtractor())
