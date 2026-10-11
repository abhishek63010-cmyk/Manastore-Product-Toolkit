from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

from src.models.enums import ReviewStatus


class OCRStatus(str, Enum):
    """Lifecycle status for an OCR text extraction attempt."""
    SUCCESS = "SUCCESS"                        # Text successfully extracted
    NO_TEXT_DETECTED = "NO_TEXT_DETECTED"      # Processed cleanly, but no text or glyphs found
    OCR_REQUIRED = "OCR_REQUIRED"              # Document has raster pages with no embedded text layer
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"  # File extension or MIME type not supported by extractor
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE" # Underlying OCR engine or binary is not installed
    FAILED = "FAILED"                          # Processing failed with an error


class BoundingBox(BaseModel):
    """Spatial bounding box coordinates for a recognized text element."""
    x: float = Field(..., description="Top-left horizontal coordinate")
    y: float = Field(..., description="Top-left vertical coordinate")
    width: float = Field(..., description="Bounding box width")
    height: float = Field(..., description="Bounding box height")
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Recognition confidence score between 0.0 and 1.0"
    )
    text: Optional[str] = Field(default=None, description="Recognized word or phrase in box")


class CandidateField(BaseModel):
    """
    Structured candidate attribute derived from raw OCR text.
    
    IMPORTANT:
    Candidates are provisional extractions requiring review; they are never
    assumed to be confirmed, authoritative product facts.
    """
    field_name: str = Field(..., description="Target domain field name (e.g. 'design_number', 'sku', 'fabric')")
    candidate_value: Any = Field(..., description="Extracted and normalized candidate value")
    raw_text: str = Field(..., description="Exact matched substring from source document")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Heuristic or model confidence score")
    review_status: ReviewStatus = Field(
        default=ReviewStatus.PENDING,
        description="Review lifecycle status (defaults to PENDING for manual/review gate)"
    )
    extraction_rule: str = Field(
        default="UNKNOWN",
        description="Identifier of the parser rule or regex pattern that produced this candidate"
    )


class OCRResult(BaseModel):
    """
    Structured outcome of an OCR or document text extraction run.
    """
    status: OCRStatus
    source_identity: str = Field(..., description="Absolute path, URI, or unique identifier of source asset")
    source_filename: str = Field(..., description="Original filename of the processed document/image")
    page_number: Optional[int] = Field(default=None, description="1-indexed page number if multi-page document")
    provider: str = Field(..., description="Identifier of the OCR extractor provider")
    raw_text: str = Field(default="", description="Full raw unedited text extracted from document")
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Aggregate confidence score for text extraction"
    )
    bounding_boxes: list[BoundingBox] = Field(default_factory=list, description="Spatial text locations")
    candidate_fields: list[CandidateField] = Field(
        default_factory=list,
        description="Structured candidate attributes derived from raw text"
    )
    batch_id: Optional[str] = Field(default=None, description="Associated pipeline batch run identifier")
    error_message: Optional[str] = Field(
        default=None,
        description="Sanitized diagnostic error or missing dependency message"
    )

    @property
    def is_successful(self) -> bool:
        """True if text extraction succeeded."""
        return self.status == OCRStatus.SUCCESS

    def get_candidate(self, field_name: str) -> Optional[CandidateField]:
        """Retrieve first candidate field matching the given field name."""
        for c in self.candidate_fields:
            if c.field_name.lower() == field_name.lower():
                return c
        return None

    def get_candidate_value(self, field_name: str) -> Optional[Any]:
        """Convenience method to retrieve candidate value if present."""
        cand = self.get_candidate(field_name)
        return cand.candidate_value if cand else None

    def summary(self) -> dict[str, Any]:
        """Return a structured summary dictionary."""
        return {
            "status": self.status.value,
            "source_filename": self.source_filename,
            "provider": self.provider,
            "page_number": self.page_number,
            "text_length": len(self.raw_text),
            "confidence": self.confidence,
            "candidates_count": len(self.candidate_fields),
            "candidate_fields": {c.field_name: c.candidate_value for c in self.candidate_fields},
            "error_message": self.error_message,
        }
