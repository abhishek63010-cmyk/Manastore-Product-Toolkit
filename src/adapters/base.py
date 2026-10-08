import re
from abc import ABC, abstractmethod
from typing import Any, Optional
from src.extractors.raw_record import RawRecord, RawTabularData
from src.models.enums import FieldSource
from src.models.product import Product
from src.models.provenance import ProvenanceRecord


class BaseSupplierAdapter(ABC):
    """
    Abstract base class for supplier data normalization adapters.
    
    Responsibilities:
    - Determine if this adapter can process a given raw tabular dataset.
    - Normalize supplier-specific field aliases to canonical domain models.
    - Preserve all unmapped raw attributes without data loss.
    - Record field-level provenance (SOURCE, DERIVED, AUTO_FILLED, MANUAL_REVIEW).
    - Support 1 Product -> N Variants grouping.
    - NEVER invent or calculate customer-facing selling_price.
    """

    adapter_name: str = "base_adapter"

    @abstractmethod
    def can_handle(self, raw_data: RawTabularData) -> bool:
        """Evaluate if this adapter is suitable for the provided dataset."""
        pass

    @abstractmethod
    def transform(self, raw_data: RawTabularData) -> list[Product]:
        """Transform raw tabular records into canonical Product domain models with child Variants."""
        pass

    # ==========================================
    # Helper utilities for normalization
    # ==========================================

    @staticmethod
    def normalize_key(key: str) -> str:
        """Standardize a header key by lowercasing, stripping, and normalizing delimiters."""
        cleaned = re.sub(r"[\s\-\/\.]+", "_", str(key).strip().lower())
        return cleaned.strip("_")

    @classmethod
    def find_field_value(
        cls,
        record: RawRecord,
        aliases: list[str],
    ) -> tuple[Optional[str], Any]:
        """
        Search a RawRecord for matching aliases.
        Returns: (matched_original_key, raw_value) or (None, None) if not found.
        """
        normalized_record = {
            cls.normalize_key(k): (k, v)
            for k, v in record.data.items()
        }

        for alias in aliases:
            norm_alias = cls.normalize_key(alias)
            if norm_alias in normalized_record:
                orig_key, val = normalized_record[norm_alias]
                if val is not None and str(val).strip() != "":
                    return orig_key, val
        return None, None

    @staticmethod
    def clean_str(val: Any) -> Optional[str]:
        """Sanitize string values; returns None for blank or NaN strings."""
        if val is None:
            return None
        s = str(val).strip()
        if not s or s.lower() in ("nan", "none", "null"):
            return None
        return s

    @staticmethod
    def clean_float(val: Any) -> Optional[float]:
        """Extract numeric float value handling currency symbols, commas, and percentages."""
        if val is None:
            return None
        s = str(val).strip()
        if not s or s.lower() in ("nan", "none", "null"):
            return None

        # Check if negative
        is_negative = s.strip().startswith("-")
        cleaned = re.sub(r"[^\d\.]+", "", s)
        try:
            val_flt = float(cleaned)
            return -val_flt if is_negative else val_flt
        except (ValueError, TypeError):
            return None


    @staticmethod
    def clean_int(val: Any) -> Optional[int]:
        """Extract integer value safely."""
        flt = BaseSupplierAdapter.clean_float(val)
        if flt is not None:
            return int(flt)
        return None

    @staticmethod
    def record_provenance(
        target_provenance: ProvenanceRecord,
        field_name: str,
        source_type: FieldSource,
        raw_key: Optional[str] = None,
        raw_value: Optional[Any] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Helper to attach field-level provenance details."""
        target_provenance.track_field(
            field_name=field_name,
            source_type=source_type,
            raw_key=raw_key,
            raw_value=raw_value,
            notes=notes,
        )
