import pytest
from src.models.enums import ValidationSeverity
from src.models.product import Product
from src.models.variant import Variant
from src.validators.generic import GenericProductValidator


def test_valid_generic_product_passes():
    """Verify clean validation for a properly formed generic product and variants."""
    prod = Product(
        supplier_name="Weave365",
        catalog_name="Aura Collection",
        product_name="Aura Straight Kurti",
        product_type="Kurti",
        category_slug="kurtis",
        supplier_price=450.0,
        gst_percent=5.0,
        attributes={"fabric": "Rayon", "sleeve": "3/4"},
    )
    v1 = Variant(
        variant_code="WV-01",
        supplier_sku="SKU-101",
        variant_name="Maroon M",
        selling_price=899.0,
        stock_quantity=10,
        primary_image_url="https://blob.example.com/WV-01.jpg",
    )
    v2 = Variant(
        variant_code="WV-02",
        supplier_sku="SKU-101",  # Shared supplier SKU
        variant_name="Maroon L",
        selling_price=899.0,
        stock_quantity=0,  # Zero stock is valid (OUT_OF_STOCK)
        primary_image_url="https://blob.example.com/WV-02.jpg",
    )
    prod.add_variant(v1)
    prod.add_variant(v2)

    report = GenericProductValidator.validate([prod])
    assert report.is_valid is True
    assert len(report.errors()) == 0
    assert report.total_products == 1
    assert report.total_variants == 2


def test_amra_shared_supplier_sku_passes_without_errors():
    """
    CRITICAL REQUIREMENT:
    Amra-style multiple variants sharing supplier_sku (e.g. AM-17090)
    MUST pass validation without ERROR.
    """
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        product_name="Mahotsav Soft Chinon Silk Saree",
        product_group="Mahots-K",
    )
    v1 = Variant(variant_code="Mahots-K-S3207", supplier_sku="AM-17090", variant_name="Emerald Green")
    v2 = Variant(variant_code="Mahots-K-S3208", supplier_sku="AM-17090", variant_name="Coral Red")
    v3 = Variant(variant_code="Mahots-K-S3209", supplier_sku="AM-17090", variant_name="Blush Pink")
    prod.add_variant(v1)
    prod.add_variant(v2)
    prod.add_variant(v3)

    report = GenericProductValidator.validate([prod])
    assert report.is_valid is True
    assert len(report.errors()) == 0

    # Ensure shared supplier SKU is noted as INFO, never ERROR
    info_codes = [i.code for i in report.infos()]
    assert "SHARED_SUPPLIER_SKU" in info_codes


def test_duplicate_variant_code_fails():
    """Verify duplicate variant_code is flagged as an ERROR."""
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        product_name="Mahotsav Saree",
    )
    v1 = Variant(variant_code="DUP-01", supplier_sku="AM-100", variant_name="Green")
    v2 = Variant(variant_code="DUP-02", supplier_sku="AM-100", variant_name="Red")
    prod.add_variant(v1)
    prod.add_variant(v2)

    # Force duplicate code by modifying existing list directly
    prod.variants[1].variant_code = "DUP-01"

    report = GenericProductValidator.validate([prod])
    assert report.is_valid is False
    assert any(e.code == "DUPLICATE_VARIANT_CODE" for e in report.errors())


def test_missing_selling_price_emits_warning_never_auto_calculated():
    """
    Verify missing selling_price produces a WARNING and is NEVER auto-calculated
    or copied from supplier_cost.
    """
    prod = Product(
        supplier_name="Vendor A",
        catalog_name="Cat A",
        product_name="Prod A",
        supplier_price=500.0,
    )
    v = Variant(
        variant_code="V-01",
        supplier_sku="SKU-1",
        variant_name="Option 1",
        supplier_cost=500.0,
        selling_price=None,  # Missing selling price
    )
    prod.add_variant(v)

    report = GenericProductValidator.validate([prod])
    assert report.is_valid is True  # WARNING does not make report invalid
    assert any(w.code == "MISSING_SELLING_PRICE" for w in report.warnings())
    # Ensure domain model was NOT mutated
    assert v.selling_price is None


def test_invalid_negative_stock_and_prices_fail():
    """Verify negative stock, cost, or selling price triggers validation error."""
    # 1. Pydantic schema validation rejects negative numbers directly
    with pytest.raises(Exception):
        Variant(
            variant_code="V-01",
            supplier_sku="SKU-1",
            variant_name="Option 1",
            stock_quantity=-5,
        )

    with pytest.raises(Exception):
        Variant(
            variant_code="V-01",
            supplier_sku="SKU-1",
            variant_name="Option 1",
            selling_price=-200.0,
        )

    # 2. Validator catches product-level invalid bounds
    prod = Product(
        supplier_name="Vendor A",
        catalog_name="Cat A",
        product_name="Prod A",
    )
    v = Variant(
        variant_code="V-01",
        supplier_sku="SKU-1",
        variant_name="Option 1",
    )
    prod.add_variant(v)

    # Mutate to invalid values to test validator quality gate
    prod.supplier_price = -50.0
    prod.gst_percent = 120.0
    v.stock_quantity = -1

    report = GenericProductValidator.validate([prod])
    assert report.is_valid is False
    error_codes = [e.code for e in report.errors()]
    assert "INVALID_SUPPLIER_PRICE" in error_codes
    assert "INVALID_GST_PERCENT" in error_codes
    assert "INVALID_STOCK_QUANTITY" in error_codes


def test_duplicate_primary_image_in_gallery_fails():
    """Verify primary image duplicated in gallery image URLs triggers ERROR."""
    prod = Product(
        supplier_name="Vendor A",
        catalog_name="Cat A",
        product_name="Prod A",
    )
    v = Variant(
        variant_code="V-01",
        supplier_sku="SKU-1",
        variant_name="Option 1",
        primary_image_url="https://blob.com/hero.jpg",
        gallery_image_urls=["https://blob.com/hero.jpg", "https://blob.com/angle2.jpg"],
    )
    prod.add_variant(v)

    report = GenericProductValidator.validate([prod])
    assert report.is_valid is False
    assert any(e.code == "PRIMARY_IMAGE_IN_GALLERY" for e in report.errors())


def test_category_neutral_validation_non_saree():
    """Verify validation passes on non-saree apparel (Kurtis) without demanding saree fields."""
    prod = Product(
        supplier_name="Weave365",
        catalog_name="Summer Kurtis",
        product_name="Embroidered Rayon Kurti",
        product_type="Kurti",
        category_slug="kurtis",
        supplier_price=380.0,
        attributes={"sleeve": "3/4", "neck": "Round", "fabric": "Rayon"},
    )
    v = Variant(
        variant_code="KUR-01",
        supplier_sku="WV-KUR",
        variant_name="Teal S",
        selling_price=699.0,
        stock_quantity=15,
        primary_image_url="https://blob.com/kurti_teal.jpg",
    )
    prod.add_variant(v)

    report = GenericProductValidator.validate([prod])
    assert report.is_valid is True
    assert len(report.errors()) == 0
