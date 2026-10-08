from enum import Enum


class FieldSource(str, Enum):
    """Source classification for product and variant fields."""
    SOURCE = "SOURCE"              # Extracted directly from supplier raw input
    DERIVED = "DERIVED"            # Calculated or transformed deterministically
    AUTO_FILLED = "AUTO_FILLED"    # Default/fallback value supplied automatically
    MANUAL_REVIEW = "MANUAL_REVIEW"# Flagged or provided via human inspection/editing


class ValidationSeverity(str, Enum):
    """Severity levels for pipeline validation issues."""
    ERROR = "ERROR"    # Critical problem that blocks pipeline progress or export
    WARNING = "WARNING"# Non-blocking issue requiring attention or review
    INFO = "INFO"      # Informational finding or audit note


class ReviewStatus(str, Enum):
    """Review lifecycle status for catalog/variant records."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
