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

    VARIANT_COUNT_ALIASES = [
        "total_designs",
        "total_design",
        "total_pieces",
        "total_piece",
        "full_catalogue",
        "full_catalog",
        "piece_count",
        "no_of_designs",
        "designs_count",
        "variant_count",
    ]

    @staticmethod
    def parse_variant_count(val: Any) -> Optional[int]:
        """
        Parse expected variant/design count from heterogeneous supplier formats:
        - Parenthesized: '₹8490 (6 Pcs)' -> 6
        - Plain quantity: '6 Pcs', '6 Designs', '1 Pc' -> 6, 1
        - Sets/Packs: 'Pack of 4', 'Set of 6' -> 4, 6
        - Pure numeric: 6, 6.0, '6' -> 6
        """
        if val is None:
            return None
        if isinstance(val, int):
            return val if val > 0 else None
        if isinstance(val, float):
            return int(val) if val.is_integer() and val > 0 else None
        s = str(val).strip()
        if not s or s.lower() in ("nan", "none", "null"):
            return None

        # 1. Parenthesized piece count (e.g., '₹8490 (6 Pcs)', 'Total (8 Pcs)')
        m_paren = re.search(r"\(\s*(\d+)\s*(?:pcs?|pieces?|designs?|items?)\b", s, re.IGNORECASE)
        if m_paren:
            return int(m_paren.group(1))

        # 2. Standalone piece count (e.g., '6 Pcs', '1 Pc', '8 Designs')
        m_pcs = re.search(r"\b(\d+)\s*(?:pcs?|pieces?|designs?|items?)\b", s, re.IGNORECASE)
        if m_pcs:
            return int(m_pcs.group(1))

        # 3. Pack or set format (e.g., 'Pack of 4', 'Set of 6')
        m_pack = re.search(r"\b(?:pack|set)\s+of\s+(\d+)\b", s, re.IGNORECASE)
        if m_pack:
            return int(m_pack.group(1))

        # 4. Pure integer string (e.g., '6')
        if s.isdigit():
            return int(s)

        return None

    @classmethod
    def find_variant_count(cls, record: RawRecord) -> tuple[Optional[str], Any, Optional[int]]:
        """
        Search for variant count field in record data.
        Returns (matched_key, raw_value, parsed_count).
        """
        for alias in cls.VARIANT_COUNT_ALIASES:
            k, v = cls.find_field_value(record, [alias])
            if k is not None:
                parsed = cls.parse_variant_count(v)
                if parsed is not None:
                    return k, v, parsed
        return None, None, None

    @classmethod
    def build_derived_placeholder_variant(
        cls,
        record: RawRecord,
        parent: Product,
        variant_idx: int,
        total_expected: int,
        source_field: Optional[str] = None,
        source_raw_value: Optional[Any] = None,
        source_file: str = "",
        supplier_sku: Optional[str] = None,
        unmapped_keys: Optional[set[str]] = None,
    ) -> Any:
        """
        Build a deterministic placeholder variant when a catalogue row specifies a variant count
        without individual variant rows / color specifications.
        """
        from src.models.variant import Variant

        var_prov = ProvenanceRecord(
            source_file=source_file,
            supplier_id=cls.adapter_name,
        )

        # 1. Deterministic variant code
        raw_prefix = parent.product_group or supplier_sku or parent.catalog_name
        clean_prefix = re.sub(r"[^\w\-]+", "-", str(raw_prefix).strip()).strip("-").upper()
        if not clean_prefix:
            clean_prefix = f"PROD-{variant_idx:02d}"

        variant_code = f"{clean_prefix}-V{variant_idx:02d}"
        cls.record_provenance(
            var_prov,
            "variant_code",
            FieldSource.DERIVED,
            raw_key=source_field,
            raw_value=str(source_raw_value) if source_raw_value is not None else None,
            notes=f"Derived placeholder variant {variant_idx}/{total_expected} from {source_field or 'catalog'} count",
        )

        # 2. Variant Name (placeholder title - NO invented colors!)
        variant_name = f"Design {variant_idx:02d}"
        cls.record_provenance(
            var_prov,
            "variant_name",
            FieldSource.AUTO_FILLED,
            notes=f"Placeholder design title {variant_idx}/{total_expected}; pending supplier color/design specification",
        )

        # 3. Supplier SKU (None if absent - DO NOT invent!)
        if supplier_sku:
            cls.record_provenance(var_prov, "supplier_sku", FieldSource.SOURCE, notes="Supplier SKU from catalog row")
        else:
            cls.record_provenance(var_prov, "supplier_sku", FieldSource.MANUAL_REVIEW, notes="Supplier SKU absent in catalog row")

        # 4. Supplier cost & selling price
        supplier_cost = parent.supplier_price
        if supplier_cost is not None:
            cls.record_provenance(var_prov, "supplier_cost", FieldSource.DERIVED, notes="Copied from parent catalog wholesale price")

        cls.record_provenance(
            var_prov,
            "selling_price",
            FieldSource.MANUAL_REVIEW,
            notes="Selling price unset; requires pricing configuration",
        )

        # 5. Extract size if present in parent attributes
        size = parent.get_attribute("size")
        if size:
            cls.record_provenance(var_prov, "size", FieldSource.DERIVED, notes="Inherited from catalog size attribute")

        # 6. Unmapped raw attributes for this record
        var_raw_attributes = {}
        if unmapped_keys is not None:
            for orig_k, orig_v in record.data.items():
                if cls.normalize_key(orig_k) not in unmapped_keys and orig_v is not None:
                    var_raw_attributes[orig_k] = orig_v

        return Variant(
            variant_code=variant_code,
            supplier_sku=supplier_sku,
            variant_name=variant_name,
            color=None,
            size=size,
            supplier_cost=supplier_cost,
            selling_price=None,
            stock_quantity=None,
            publish=False,
            raw_attributes=var_raw_attributes,
            source_metadata=var_prov,
        )
