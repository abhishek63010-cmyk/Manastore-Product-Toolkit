import uuid
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator
from src.models.provenance import ProvenanceRecord
from src.models.variant import Variant


class Product(BaseModel):
    """
    Represents the customer-facing product / supplier catalog grouping.
    
    Category-agnostic: Attributes such as fabric, work, occasion, etc., are stored in 
    the extensible `attributes` mapping rather than hardcoding category-specific fields into the schema.
    Unmapped supplier facts survive in `raw_attributes`.
    """
    product_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Internal unique UUID for the product / catalog entity"
    )
    supplier_name: str = Field(
        ...,
        min_length=1,
        description="Supplier / vendor name (e.g., 'Amra Wholesale', 'Weave365')"
    )
    catalog_name: str = Field(
        ...,
        min_length=1,
        description="Supplier catalog or collection title (canonical name, replaces 'catalogue_name')"
    )
    product_name: str = Field(
        ...,
        min_length=1,
        description="Customer-facing refined product title"
    )
    product_group: Optional[str] = Field(
        default=None,
        description="Catalog grouping or family slug (e.g., 'Mahots-K', 'Rajtex-A')"
    )
    brand_name: Optional[str] = Field(
        default=None,
        description="Brand / manufacturer name (e.g., 'Mahotsav', 'Rajtex', 'Bunawat')"
    )
    product_type: str = Field(
        default="Saree",
        description="High-level product classification (e.g., 'Saree', 'Kurti', 'Jewellery')"
    )
    category_slug: Optional[str] = Field(
        default=None,
        description="Canonical category routing slug (e.g., 'sarees', 'kurtis', 'accessories')"
    )
    description: Optional[str] = Field(
        default=None,
        description="Refined customer-facing marketing and product description"
    )
    supplier_price: Optional[float] = Field(
        default=None,
        ge=0,
        description="Base catalog-level supplier wholesale price per piece"
    )
    gst_percent: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="Applicable GST percentage rate (e.g., 5.0 for 5%)"
    )
    moq: Optional[int] = Field(
        default=None,
        ge=1,
        description="Minimum order quantity specified by the supplier"
    )
    dispatch_location: Optional[str] = Field(
        default=None,
        description="Supplier fulfillment / dispatch location (e.g., 'Surat, India')"
    )
    variants: list[Variant] = Field(
        default_factory=list,
        description="Associated child design / color / size variants"
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Extensible category attributes (e.g. fabric, blouse_fabric, work, occasion, "
            "saree_length, blouse_length, stitch_type). Not hardcoded as rigid top-level columns."
        )
    )
    raw_attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Preserved raw unmapped supplier facts, metadata, and custom fields"
    )
    source_metadata: ProvenanceRecord = Field(
        default_factory=ProvenanceRecord,
        description="Source provenance, extraction timestamp, and file origin details"
    )

    @field_validator("supplier_name", "catalog_name", "product_name")
    @classmethod
    def strip_whitespace(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty or blank whitespace")
        return cleaned

    def add_variant(self, variant: Variant) -> None:
        """Add a variant while verifying variant_code uniqueness within this product."""
        existing_codes = {v.variant_code.casefold() for v in self.variants}
        if variant.variant_code.casefold() in existing_codes:
            raise ValueError(
                f"Duplicate variant_code '{variant.variant_code}' in product '{self.catalog_name}'"
            )
        self.variants.append(variant)

    def get_attribute(self, key: str, default: Any = None) -> Any:
        """Helper to get an attribute value case-insensitively."""
        key_cf = key.casefold()
        for k, v in self.attributes.items():
            if k.casefold() == key_cf:
                return v
        return default
