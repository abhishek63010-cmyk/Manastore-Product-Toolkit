from typing import Any, Optional
from pydantic import BaseModel, Field

from src.models.product import Product
from src.models.validation import ValidationReport


class CanonicalImage(BaseModel):
    """Canonical representation of an image asset."""
    image_id: str
    original_filename: str
    sort_index: int = 0
    is_primary: bool = False
    blob_url: Optional[str] = None
    source_url: Optional[str] = None


class CanonicalVariant(BaseModel):
    """Canonical MANA Store variant record."""
    variant_code: str = Field(..., description="Unique internal variant code")
    supplier_sku: str = Field(..., description="Supplier SKU (may repeat across variants)")
    variant_name: str = Field(..., description="Canonical variant title (NOT 'varient_name')")
    image_sku: Optional[str] = Field(default=None, description="Canonical image SKU (NOT 'image-sku')")
    color: Optional[str] = None
    size: Optional[str] = None
    supplier_cost: Optional[float] = None
    selling_price: Optional[float] = Field(default=None, description="Retail price (NOT 'selling_proce')")
    stock_quantity: Optional[int] = None
    publish: bool = False
    review_status: str = "PENDING"
    primary_image_url: Optional[str] = None
    gallery_image_urls: list[str] = Field(default_factory=list)
    images: list[CanonicalImage] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalProduct(BaseModel):
    """Canonical MANA Store product/catalog grouping."""
    product_id: str
    supplier_name: str
    catalog_name: str = Field(..., description="Canonical catalog title (NOT 'catalogue_name')")
    product_name: str
    product_group: Optional[str] = None
    brand_name: Optional[str] = None
    product_type: str = "Saree"
    category_slug: Optional[str] = None
    description: Optional[str] = None
    supplier_price: Optional[float] = None
    gst_percent: Optional[float] = None
    moq: Optional[int] = 1
    dispatch_location: Optional[str] = None
    variants: list[CanonicalVariant] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalExport(BaseModel):
    """
    Standardized, clean export document representing products, variants, and images.
    
    Guarantees:
    - Zero legacy typos ('varient_name', 'selling_proce', 'image-sku', 'catalogue_name' never appear).
    - Preserves category-agnostic attributes and pricing boundaries.
    - Deterministic dictionary and flat tabular serialization.
    """
    export_version: str = "1.0"
    total_products: int = 0
    total_variants: int = 0
    products: list[CanonicalProduct] = Field(default_factory=list)
    validation_summary: Optional[dict[str, Any]] = None

    @classmethod
    def from_products(
        cls,
        products: list[Product],
        validation_report: Optional[ValidationReport] = None,
    ) -> "CanonicalExport":
        """Convert domain models into the canonical export schema."""
        canonical_products: list[CanonicalProduct] = []

        for prod in products:
            canonical_variants: list[CanonicalVariant] = []

            for var in prod.variants:
                # Convert ImageAsset list
                canonical_images = [
                    CanonicalImage(
                        image_id=img.image_id,
                        original_filename=img.original_filename,
                        sort_index=img.sort_index,
                        is_primary=img.is_primary,
                        blob_url=img.blob_url,
                        source_url=img.source_url,
                    )
                    for img in var.images
                ]

                # Gallery URLs: strictly exclude primary_image_url
                clean_gallery = [
                    url for url in var.gallery_image_urls
                    if url and url != var.primary_image_url
                ]

                c_var = CanonicalVariant(
                    variant_code=var.variant_code,
                    supplier_sku=var.supplier_sku,
                    variant_name=var.variant_name,
                    image_sku=var.image_sku,
                    color=var.color,
                    size=var.size,
                    supplier_cost=var.supplier_cost,
                    selling_price=var.selling_price,
                    stock_quantity=var.stock_quantity,
                    publish=var.publish,
                    review_status=var.review_status.value if hasattr(var.review_status, "value") else str(var.review_status),
                    primary_image_url=var.primary_image_url,
                    gallery_image_urls=clean_gallery,
                    images=canonical_images,
                    attributes=var.attributes,
                    source_metadata=var.source_metadata.model_dump(),
                )
                canonical_variants.append(c_var)

            c_prod = CanonicalProduct(
                product_id=prod.product_id,
                supplier_name=prod.supplier_name,
                catalog_name=prod.catalog_name,
                product_name=prod.product_name,
                product_group=prod.product_group,
                brand_name=prod.brand_name,
                product_type=prod.product_type,
                category_slug=prod.category_slug,
                description=prod.description,
                supplier_price=prod.supplier_price,
                gst_percent=prod.gst_percent,
                moq=prod.moq or 1,
                dispatch_location=prod.dispatch_location,
                variants=canonical_variants,
                attributes=prod.attributes,
                source_metadata=prod.source_metadata.model_dump(),
            )
            canonical_products.append(c_prod)

        val_summary = validation_report.summary() if validation_report else None

        return cls(
            total_products=len(canonical_products),
            total_variants=sum(len(p.variants) for p in canonical_products),
            products=canonical_products,
            validation_summary=val_summary,
        )

    def to_flat_records(self) -> list[dict[str, Any]]:
        """
        Flatten canonical products into tabular row records for downstream CSV/Excel export.
        Emits strictly clean canonical column names.
        """
        rows: list[dict[str, Any]] = []

        for prod in self.products:
            for var in prod.variants:
                row: dict[str, Any] = {
                    "supplier_name": prod.supplier_name,
                    "catalog_name": prod.catalog_name,
                    "product_group": prod.product_group,
                    "product_name": prod.product_name,
                    "variant_name": var.variant_name,
                    "variant_code": var.variant_code,
                    "supplier_sku": var.supplier_sku,
                    "image_sku": var.image_sku,
                    "product_type": prod.product_type,
                    "category_slug": prod.category_slug,
                    "brand_name": prod.brand_name,
                    "color": var.color,
                    "size": var.size,
                    "description": prod.description,
                    "supplier_price": prod.supplier_price,
                    "supplier_cost": var.supplier_cost,
                    "gst_percent": prod.gst_percent,
                    "selling_price": var.selling_price,
                    "stock_quantity": var.stock_quantity,
                    "publish": var.publish,
                    "review_status": var.review_status,
                    "primary_image_url": var.primary_image_url,
                    "image_url": var.primary_image_url,  # Synonymous with primary_image_url
                }

                # Add gallery images as image1_url, image2_url, etc.
                for g_idx, g_url in enumerate(var.gallery_image_urls, start=1):
                    row[f"image{g_idx}_url"] = g_url

                # Merge category attributes into flat row
                for attr_k, attr_v in prod.attributes.items():
                    if attr_k not in row:
                        row[attr_k] = attr_v

                for v_attr_k, v_attr_v in var.attributes.items():
                    if v_attr_k not in row:
                        row[v_attr_k] = v_attr_v

                rows.append(row)

        return rows
