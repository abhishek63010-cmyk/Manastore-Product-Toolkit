import uuid
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator
from src.models.enums import ReviewStatus
from src.models.image import ImageAsset
from src.models.provenance import ProvenanceRecord


class Variant(BaseModel):
    """
    Represents an individual purchasable product variant (e.g., colorway, design, size option).
    
    IMPORTANT ARCHITECTURAL RULES:
    - `supplier_sku` is the supplier-assigned catalog/product SKU and IS NOT unique across variants;
      multiple variants from the same catalog can and do share the same `supplier_sku`.
    - `variant_code` is the unique SKU/code for this specific variant within the import batch.
    """
    variant_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Internal unique UUID for the variant record"
    )
    variant_code: str = Field(
        ...,
        min_length=1,
        description="Internal unique variant identifier/SKU (e.g. 'Mahots-K-S3207', 'Rajtex-A-035-A')"
    )
    supplier_sku: str = Field(
        ...,
        min_length=1,
        description="Supplier-assigned SKU (e.g. 'AM-17090'). May be shared across multiple variants"
    )
    variant_name: str = Field(
        ...,
        min_length=1,
        description="Human-readable title/name of the variant (e.g. 'Emerald Green', 'Design A')"
    )
    color: Optional[str] = Field(
        default=None,
        description="Colorway designation"
    )
    size: Optional[str] = Field(
        default=None,
        description="Size or dimension description"
    )
    supplier_cost: Optional[float] = Field(
        default=None,
        ge=0,
        description="Unit wholesale cost / supplier price per piece"
    )
    selling_price: Optional[float] = Field(
        default=None,
        ge=0,
        description="Customer-facing retail/selling price (canonical name, replaces 'selling_proce')"
    )
    stock_quantity: Optional[int] = Field(
        default=None,
        ge=0,
        description="Available inventory quantity / MOQ units"
    )
    image_sku: Optional[str] = Field(
        default=None,
        description="Image matching identifier (canonical name, replaces 'image-sku')"
    )
    primary_image_url: Optional[str] = Field(
        default=None,
        description="Main hero image Blob URL (Index 0)"
    )
    gallery_image_urls: list[str] = Field(
        default_factory=list,
        description="Additional view / angle Blob URLs (e.g., image1_url, image2_url, image3_url)"
    )
    images: list[ImageAsset] = Field(
        default_factory=list,
        description="Associated structured ImageAsset objects"
    )
    publish: bool = Field(
        default=False,
        description="Publish flag; default False until reviewed and approved"
    )
    review_status: ReviewStatus = Field(
        default=ReviewStatus.PENDING,
        description="Operational review lifecycle status"
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Normalized variant-level attributes (e.g., pattern, color_hex)"
    )
    raw_attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Preserved raw unmapped supplier facts specific to this variant"
    )
    source_metadata: ProvenanceRecord = Field(
        default_factory=ProvenanceRecord,
        description="Provenance and field tracking metadata for this variant"
    )

    @field_validator("variant_code", "supplier_sku", "variant_name")
    @classmethod
    def strip_whitespace(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty or blank whitespace")
        return cleaned
