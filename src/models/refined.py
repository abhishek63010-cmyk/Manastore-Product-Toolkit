from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field


class RefinedVariantRecord(BaseModel):
    """
    Enriched, audit-grade record for a single variant.
    Combines:
    1. Product & Variant core identifiers
    2. Original unmutated supplier data
    3. Approved image assignment identity & review status
    4. Provisional OCR candidates (clearly demarcated)
    5. Verified Blob storage URLs
    6. Review flags and validation issues
    """
    # Core Domain Identifiers
    product_id: str
    catalog_name: str
    product_name: str
    product_group: Optional[str] = None
    brand_name: Optional[str] = None
    product_type: str = "Saree"
    category_slug: Optional[str] = None
    variant_code: str
    variant_name: str

    # Original Raw Supplier Values (preserved unchanged)
    supplier_sku: Optional[str] = None  # Actual supplier SKU (NOT internal variant code)
    original_color: Optional[str] = None
    original_size: Optional[str] = None
    supplier_price: Optional[float] = None
    supplier_cost: Optional[float] = None
    gst_percent: Optional[float] = None
    selling_price: Optional[float] = None  # None unless manually set; never invented
    stock_quantity: Optional[int] = None
    description: Optional[str] = None

    # Approved Image Assignment & Identity
    has_approved_image: bool = False
    image_id: Optional[str] = None
    image_filename: Optional[str] = None
    image_path: Optional[str] = None
    approval_status: str = "UNASSIGNED"  # APPROVED, PENDING, REJECTED, STALE_HASH_MISMATCH, UNASSIGNED
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    assignment_hash: Optional[str] = None

    # Canonical Image-Enriched Fields (Populated ONLY after approval and analysis)
    image_sku: Optional[str] = None  # Exact image SKU from user manifest / image OCR
    color: Optional[str] = None      # Provisional canonical color derived from image pixels

    # Image Pixel Color Analysis Provenance
    image_color_status: str = "NOT_ANALYZED"  # EXTRACTED, UNCERTAIN, BACKGROUND_ONLY, FAILED, NOT_ANALYZED
    image_color_confidence: Optional[float] = None
    image_color_hex: Optional[str] = None
    image_color_notes: Optional[str] = None

    # Provisional OCR Candidates (Evidence from approved images)
    ocr_status: str = "NOT_RUN"  # SUCCESS, NO_TEXT_FOUND, PROVIDER_UNAVAILABLE, FAILED, NOT_RUN
    ocr_provider: Optional[str] = None
    ocr_confidence: Optional[float] = None
    candidate_design_number: Optional[str] = None
    candidate_sku: Optional[str] = None
    candidate_color: Optional[str] = None
    candidate_fabric: Optional[str] = None
    candidate_rate: Optional[float] = None
    raw_ocr_text: Optional[str] = None

    # Blob Upload Status
    is_uploaded: bool = False
    blob_url: Optional[str] = None
    gallery_blob_urls: list[str] = Field(default_factory=list)

    # Review Requirements & Quality Gate
    requires_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)

    # Extensible Category Attributes (e.g. fabric, blouse_fabric, work, occasion)
    attributes: dict[str, Any] = Field(default_factory=dict)

    def to_flat_dict(self) -> dict[str, Any]:
        """Produce flat dictionary row for refined workbook serialization."""
        row: dict[str, Any] = {
            # Identity
            "catalog_name": self.catalog_name,
            "product_name": self.product_name,
            "variant_code": self.variant_code,
            "variant_name": self.variant_name,
            "product_group": self.product_group,
            "brand_name": self.brand_name,
            "product_type": self.product_type,
            "category_slug": self.category_slug,

            # Raw Supplier Facts (Untouched)
            "supplier_sku": self.supplier_sku,
            "supplier_price": self.supplier_price,
            "supplier_cost": self.supplier_cost,
            "gst_percent": self.gst_percent,
            "original_color": self.original_color,
            "original_size": self.original_size,
            "selling_price": self.selling_price,
            "stock_quantity": self.stock_quantity,

            # Approved Image Identity & Gate
            "has_approved_image": self.has_approved_image,
            "image_id": self.image_id,
            "image_filename": self.image_filename,
            "image_path": self.image_path,
            "approval_status": self.approval_status,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "assignment_hash": self.assignment_hash,

            # Enriched Canonical Attributes from Approved Images
            "image_sku": self.image_sku,
            "color": self.color,

            # Image Color Analysis Provenance
            "image_color_status": self.image_color_status,
            "image_color_confidence": self.image_color_confidence,
            "image_color_hex": self.image_color_hex,
            "image_color_notes": self.image_color_notes,

            # Provisional OCR Evidence
            "ocr_status": self.ocr_status,
            "ocr_provider": self.ocr_provider,
            "ocr_confidence": self.ocr_confidence,
            "candidate_design_number": self.candidate_design_number,
            "candidate_sku": self.candidate_sku,
            "candidate_color": self.candidate_color,
            "candidate_fabric": self.candidate_fabric,
            "candidate_rate": self.candidate_rate,
            "raw_ocr_text": self.raw_ocr_text,

            # Upload Linkage
            "is_uploaded": self.is_uploaded,
            "blob_url": self.blob_url,
            "gallery_count": len(self.gallery_blob_urls),

            # Human Review & Quality
            "requires_review": self.requires_review,
            "review_reasons": "; ".join(self.review_reasons) if self.review_reasons else "",
            "validation_errors": "; ".join(self.validation_errors) if self.validation_errors else "",
            "validation_warnings": "; ".join(self.validation_warnings) if self.validation_warnings else "",
        }

        # Include category attributes
        for k, v in self.attributes.items():
            if k not in row:
                row[k] = v

        return row


class RefinedExport(BaseModel):
    """Container for the complete refined dataset across all products and variants."""
    export_version: str = "1.0"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_variants: int = 0
    approved_image_variants: int = 0
    ocr_enriched_variants: int = 0
    uploaded_variants: int = 0
    variants_requiring_review: int = 0
    records: list[RefinedVariantRecord] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "total_variants": self.total_variants,
            "approved_image_variants": self.approved_image_variants,
            "ocr_enriched_variants": self.ocr_enriched_variants,
            "uploaded_variants": self.uploaded_variants,
            "variants_requiring_review": self.variants_requiring_review,
        }

    def to_flat_records(self) -> list[dict[str, Any]]:
        return [r.to_flat_dict() for r in self.records]

    def review_required_records(self) -> list[dict[str, Any]]:
        return [r.to_flat_dict() for r in self.records if r.requires_review]
