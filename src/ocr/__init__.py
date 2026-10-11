"""
MANA Store Product Import Toolkit - OCR & Document Text Extraction Module.

Pluggable, testable OCR foundation supporting structured text extraction,
bounding boxes, conservative candidate field parsing, and PDF text inspection.
"""

from src.ocr.models import (
    BoundingBox,
    CandidateField,
    OCRResult,
    OCRStatus,
)
from src.ocr.base import BaseOCRExtractor, BasePDFExtractor
from src.ocr.parser import ConservativeCandidateParser
from src.ocr.fake import FakeOCRExtractor
from src.ocr.tesseract import TesseractOCRExtractor
from src.ocr.pdf import PDFTextExtractor
from src.ocr.registry import OCRRegistry

__all__ = [
    "BoundingBox",
    "CandidateField",
    "OCRResult",
    "OCRStatus",
    "BaseOCRExtractor",
    "BasePDFExtractor",
    "ConservativeCandidateParser",
    "FakeOCRExtractor",
    "TesseractOCRExtractor",
    "PDFTextExtractor",
    "OCRRegistry",
]
