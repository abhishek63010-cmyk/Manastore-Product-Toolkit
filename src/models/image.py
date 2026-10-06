import uuid
from typing import Any, Optional
from pydantic import BaseModel, Field
from src.models.provenance import ProvenanceRecord


class ImageAsset(BaseModel):
    """
    Represents an image file associated with a product catalog or specific variant.
    Supports deterministic sorting and slot assignment (index 0 = primary).
    """
    image_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique internal identifier for the image asset"
    )
    local_path: Optional[str] = Field(
        default=None,
        description="Local filesystem path to the original or staged image file"
    )
    supplier_sku: Optional[str] = Field(
        default=None,
        description="Catalog-level supplier SKU this image is associated with"
    )
    variant_code: Optional[str] = Field(
        default=None,
        description="Specific variant code this image is mapped to"
    )
    original_filename: str = Field(
        ...,
        description="Original filename of the image as provided by supplier / zip"
    )
    source_identity: Optional[str] = Field(
        default=None,
        description="Source archive, folder, or batch identifier (e.g. zip filename)"
    )
    sort_index: int = Field(
        default=0,
        ge=0,
        description="Deterministic sort order index for multiple images (0-indexed)"
    )
    is_primary: bool = Field(
        default=False,
        description="True if this is designated as the primary / hero image (index 0)"
    )
    blob_url: Optional[str] = Field(
        default=None,
        description="Hosted cloud Blob URL (e.g., Vercel Blob, S3, Azure) once uploaded"
    )
    source_url: Optional[str] = Field(
        default=None,
        description="Original supplier-provided remote image URL prior to cloud upload"
    )
    source_metadata: ProvenanceRecord = Field(
        default_factory=ProvenanceRecord,
        description="Provenance metadata for the image asset"
    )
    extra_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Preserved additional image attributes (e.g., file size, resolution, format)"
    )
