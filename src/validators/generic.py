import re
from typing import Any, Optional
from src.models.enums import ValidationSeverity
from src.models.product import Product
from src.models.validation import ValidationIssue, ValidationReport
from src.models.variant import Variant


class GenericProductValidator:
    """
    Supplier- and category-agnostic validator for canonical Product and Variant hierarchies.
    
    Principles:
    - Does NOT hardcode category-specific mandatory fields (e.g. saree_fabric, blouse_fabric).
    - Supports multi-variant shared supplier SKUs without flagging errors.
    - Strictly checks variant_code uniqueness within products.
    - Enforces numeric non-negativity (stock_quantity >= 0, prices >= 0).
    - Checks image URL deduplication (primary image not in gallery).
    - Flags unpriced products as WARNING (never silently copies cost to price).
    - Distinguishes ERROR (blocking), WARNING (action required), and INFO.
    """

    @classmethod
    def validate(
        cls,
        products: list[Product],
        require_blob_urls: bool = False,
    ) -> ValidationReport:
        report = ValidationReport(
            total_products=len(products),
            total_variants=sum(len(p.variants) for p in products),
        )

        global_variant_codes: dict[str, str] = {}  # code.lower() -> product_id

        for p_idx, prod in enumerate(products, start=1):
            cls._validate_product(prod, p_idx, report, global_variant_codes, require_blob_urls)

        return report

    @classmethod
    def validate_products(
        cls,
        products: list[Product],
        require_blob_urls: bool = False,
    ) -> ValidationReport:
        """Alias for validate()."""
        return cls.validate(products=products, require_blob_urls=require_blob_urls)


    @classmethod
    def _validate_product(
        cls,
        prod: Product,
        p_idx: int,
        report: ValidationReport,
        global_variant_codes: dict[str, str],
        require_blob_urls: bool,
    ) -> None:
        # 1. Mandatory Product Fields
        if not prod.product_name or not prod.product_name.strip():
            report.add_issue(
                severity=ValidationSeverity.ERROR,
                code="MISSING_PRODUCT_NAME",
                message=f"Product at index {p_idx} is missing a product_name",
                field="product_name",
                source_row=p_idx,
            )

        if not prod.catalog_name or not prod.catalog_name.strip():
            report.add_issue(
                severity=ValidationSeverity.ERROR,
                code="MISSING_CATALOG_NAME",
                message=f"Product at index {p_idx} is missing a catalog_name",
                field="catalog_name",
                source_row=p_idx,
            )

        if not prod.supplier_name or not prod.supplier_name.strip():
            report.add_issue(
                severity=ValidationSeverity.ERROR,
                code="MISSING_SUPPLIER_NAME",
                message=f"Product '{prod.catalog_name}' is missing a supplier_name",
                field="supplier_name",
            )

        # 2. Product Pricing & GST Bounds
        if prod.supplier_price is not None and prod.supplier_price < 0:
            report.add_issue(
                severity=ValidationSeverity.ERROR,
                code="INVALID_SUPPLIER_PRICE",
                message=f"Product '{prod.catalog_name}' has negative supplier_price ({prod.supplier_price})",
                field="supplier_price",
            )

        if prod.gst_percent is not None and not (0 <= prod.gst_percent <= 100):
            report.add_issue(
                severity=ValidationSeverity.ERROR,
                code="INVALID_GST_PERCENT",
                message=f"Product '{prod.catalog_name}' has invalid GST percent ({prod.gst_percent}%)",
                field="gst_percent",
            )

        # 3. Category Slug Validation
        if prod.category_slug:
            slug = prod.category_slug.strip()
            if re.search(r"[^\w\-]+", slug) or slug != slug.lower():
                report.add_issue(
                    severity=ValidationSeverity.ERROR,
                    code="INVALID_CATEGORY_SLUG",
                    message=f"Category slug '{prod.category_slug}' contains invalid uppercase or special characters",
                    field="category_slug",
                )

        # 4. Product Variants Presence
        if not prod.variants:
            report.add_issue(
                severity=ValidationSeverity.ERROR,
                code="NO_VARIANTS",
                message=f"Product '{prod.catalog_name}' has no child variants",
                field="variants",
            )
            return

        # 5. Check shared supplier SKUs in product
        supplier_skus = [v.supplier_sku for v in prod.variants if v.supplier_sku]
        if len(set(supplier_skus)) < len(supplier_skus):
            # Shared supplier SKU across variants is VALID in multi-variant catalogs -> emit INFO
            report.add_issue(
                severity=ValidationSeverity.INFO,
                code="SHARED_SUPPLIER_SKU",
                message=(
                    f"Product '{prod.catalog_name}' has multiple variants sharing supplier SKU(s): "
                    f"{set(s for s in supplier_skus if supplier_skus.count(s) > 1)}"
                ),
                field="supplier_sku",
            )

        # 6. Validate Individual Variants
        product_variant_codes: set[str] = set()

        for v_idx, variant in enumerate(prod.variants, start=1):
            cls._validate_variant(
                prod=prod,
                variant=variant,
                v_idx=v_idx,
                report=report,
                product_variant_codes=product_variant_codes,
                global_variant_codes=global_variant_codes,
                require_blob_urls=require_blob_urls,
            )

    @classmethod
    def _validate_variant(
        cls,
        prod: Product,
        variant: Variant,
        v_idx: int,
        report: ValidationReport,
        product_variant_codes: set[str],
        global_variant_codes: dict[str, str],
        require_blob_urls: bool,
    ) -> None:
        v_code = variant.variant_code.strip() if variant.variant_code else ""
        v_sku = variant.supplier_sku.strip() if variant.supplier_sku else ""

        # Variant Code Required
        if not v_code:
            report.add_issue(
                severity=ValidationSeverity.ERROR,
                code="MISSING_VARIANT_CODE",
                message=f"Variant #{v_idx} in product '{prod.catalog_name}' is missing variant_code",
                field="variant_code",
                supplier_sku=v_sku,
            )
        else:
            # Variant Code Uniqueness within product & batch
            code_cf = v_code.casefold()
            if code_cf in product_variant_codes or code_cf in global_variant_codes:
                report.add_issue(
                    severity=ValidationSeverity.ERROR,
                    code="DUPLICATE_VARIANT_CODE",
                    message=f"Variant code '{v_code}' is duplicated",
                    field="variant_code",
                    variant_code=v_code,
                    supplier_sku=v_sku,
                )
            else:
                product_variant_codes.add(code_cf)
                global_variant_codes[code_cf] = prod.product_id


        # Variant Name Required
        if not variant.variant_name or not variant.variant_name.strip():
            report.add_issue(
                severity=ValidationSeverity.ERROR,
                code="MISSING_VARIANT_NAME",
                message=f"Variant '{v_code}' is missing variant_name",
                field="variant_name",
                variant_code=v_code,
                supplier_sku=v_sku,
            )

        # Stock Non-Negativity
        if variant.stock_quantity is not None:
            if variant.stock_quantity < 0:
                report.add_issue(
                    severity=ValidationSeverity.ERROR,
                    code="INVALID_STOCK_QUANTITY",
                    message=f"Variant '{v_code}' has negative stock quantity ({variant.stock_quantity})",
                    field="stock_quantity",
                    variant_code=v_code,
                    supplier_sku=v_sku,
                )
        else:
            report.add_issue(
                severity=ValidationSeverity.WARNING,
                code="MISSING_STOCK_QUANTITY",
                message=f"Variant '{v_code}' has no stock quantity defined (defaults to 0)",
                field="stock_quantity",
                variant_code=v_code,
                supplier_sku=v_sku,
            )

        # Pricing Rules
        if variant.supplier_cost is not None and variant.supplier_cost < 0:
            report.add_issue(
                severity=ValidationSeverity.ERROR,
                code="INVALID_SUPPLIER_COST",
                message=f"Variant '{v_code}' has negative supplier_cost ({variant.supplier_cost})",
                field="supplier_cost",
                variant_code=v_code,
                supplier_sku=v_sku,
            )

        if variant.selling_price is not None:
            if variant.selling_price < 0:
                report.add_issue(
                    severity=ValidationSeverity.ERROR,
                    code="INVALID_SELLING_PRICE",
                    message=f"Variant '{v_code}' has negative selling_price ({variant.selling_price})",
                    field="selling_price",
                    variant_code=v_code,
                    supplier_sku=v_sku,
                )
        else:
            report.add_issue(
                severity=ValidationSeverity.WARNING,
                code="MISSING_SELLING_PRICE",
                message=f"Variant '{v_code}' is missing selling_price (requires commercial review)",
                field="selling_price",
                variant_code=v_code,
                supplier_sku=v_sku,
            )

        # Publish Safety
        if variant.publish and variant.selling_price is None:
            report.add_issue(
                severity=ValidationSeverity.WARNING,
                code="PUBLISHED_WITHOUT_SELLING_PRICE",
                message=f"Variant '{v_code}' is marked publish=True but has no selling_price",
                field="publish",
                variant_code=v_code,
                supplier_sku=v_sku,
            )

        # Image Validation Rules
        cls._validate_images(variant, report, require_blob_urls)

    @classmethod
    def _validate_images(
        cls,
        variant: Variant,
        report: ValidationReport,
        require_blob_urls: bool,
    ) -> None:
        v_code = variant.variant_code
        v_sku = variant.supplier_sku

        # Check primary image URL
        if require_blob_urls and not variant.primary_image_url:
            report.add_issue(
                severity=ValidationSeverity.WARNING,
                code="MISSING_PRIMARY_BLOB_URL",
                message=f"Variant '{v_code}' has no uploaded primary_image_url",
                field="primary_image_url",
                variant_code=v_code,
                supplier_sku=v_sku,
            )

        # Duplicate image URL check (primary in gallery)
        if variant.primary_image_url and variant.gallery_image_urls:
            if variant.primary_image_url in variant.gallery_image_urls:
                report.add_issue(
                    severity=ValidationSeverity.ERROR,
                    code="PRIMARY_IMAGE_IN_GALLERY",
                    message=f"Variant '{v_code}' primary_image_url is duplicated in gallery_image_urls",
                    field="gallery_image_urls",
                    variant_code=v_code,
                    supplier_sku=v_sku,
                )

        # Duplicate gallery image URLs
        if variant.gallery_image_urls:
            seen_gallery = set()
            for g_url in variant.gallery_image_urls:
                if g_url in seen_gallery:
                    report.add_issue(
                        severity=ValidationSeverity.ERROR,
                        code="DUPLICATE_GALLERY_IMAGE",
                        message=f"Variant '{v_code}' has duplicate gallery image URL: {g_url}",
                        field="gallery_image_urls",
                        variant_code=v_code,
                        supplier_sku=v_sku,
                    )
                else:
                    seen_gallery.add(g_url)
