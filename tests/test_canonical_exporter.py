import pytest
from src.exporters.canonical import (
    CanonicalExport,
    CanonicalProduct,
    CanonicalVariant,
)
from src.models.product import Product
from src.models.variant import Variant
from src.validators.generic import GenericProductValidator


def test_canonical_export_from_products():
    """Verify CanonicalExport creation, serialization, and clean schema naming."""
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        product_name="Mahotsav Soft Chinon Silk Saree",
        product_group="Mahots-K",
        brand_name="Mahotsav",
        product_type="Saree",
        category_slug="sarees",
        supplier_price=855.0,
        gst_percent=5.0,
        attributes={"fabric": "Soft Chinon Silk", "work": "Floral Prints"},
    )
    v1 = Variant(
        variant_code="Mahots-K-S3207",
        supplier_sku="AM-17090",
        variant_name="Emerald Green",
        image_sku="Mahots-K-S3207",
        color="Emerald Green",
        supplier_cost=855.0,
        selling_price=999.0,
        stock_quantity=5,
        publish=False,
        primary_image_url="https://blob.com/Mahots-K-S3207.png",
        gallery_image_urls=["https://blob.com/Mahots-K-S3207-1.png"],
    )
    v2 = Variant(
        variant_code="Mahots-K-S3208",
        supplier_sku="AM-17090",  # Shared SKU
        variant_name="Coral Red",
        image_sku="Mahots-K-S3208",
        color="Coral Red",
        supplier_cost=855.0,
        selling_price=999.0,
        stock_quantity=0,
        publish=False,
        primary_image_url="https://blob.com/Mahots-K-S3208.png",
    )
    prod.add_variant(v1)
    prod.add_variant(v2)

    val_report = GenericProductValidator.validate([prod])
    export_doc = CanonicalExport.from_products([prod], validation_report=val_report)

    assert export_doc.total_products == 1
    assert export_doc.total_variants == 2
    assert export_doc.validation_summary["is_valid"] is True

    # Check Product level
    c_prod = export_doc.products[0]
    assert c_prod.catalog_name == "Mahotsav Kriti"
    assert c_prod.product_name == "Mahotsav Soft Chinon Silk Saree"
    assert c_prod.attributes["fabric"] == "Soft Chinon Silk"

    # Check Variant level
    c_v1 = c_prod.variants[0]
    assert c_v1.variant_code == "Mahots-K-S3207"
    assert c_v1.supplier_sku == "AM-17090"
    assert c_v1.variant_name == "Emerald Green"
    assert c_v1.image_sku == "Mahots-K-S3207"
    assert c_v1.selling_price == 999.0
    assert c_v1.primary_image_url == "https://blob.com/Mahots-K-S3207.png"
    assert c_v1.gallery_image_urls == ["https://blob.com/Mahots-K-S3207-1.png"]


def test_legacy_typo_fields_never_appear_in_canonical_output():
    """
    CRITICAL REQUIREMENT:
    Ensure legacy misspelled fields ('varient_name', 'selling_proce', 'image-sku', 'catalogue_name')
    never appear in canonical model fields or flattened records.
    """
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Rajtex Ajrakh Vol-35",
        product_name="Rajtex Ajrakh Printed Saree",
    )
    v = Variant(
        variant_code="Rajtex-A-035-A",
        supplier_sku="AM-1726I",
        variant_name="Design A",
        image_sku="Rajtex-A-035-A",
        selling_price=899.0,
    )
    prod.add_variant(v)

    export_doc = CanonicalExport.from_products([prod])
    doc_dict = export_doc.model_dump()
    flat_rows = export_doc.to_flat_records()

    # 1. Inspect dictionary serialization
    dict_str = str(doc_dict).lower()
    assert "varient_name" not in dict_str
    assert "selling_proce" not in dict_str
    assert "'image-sku'" not in dict_str
    assert "catalogue_name" not in dict_str

    # 2. Inspect flat tabular columns
    assert len(flat_rows) == 1
    row_keys = list(flat_rows[0].keys())

    # Correct canonical names MUST be present
    assert "catalog_name" in row_keys
    assert "variant_name" in row_keys
    assert "variant_code" in row_keys
    assert "selling_price" in row_keys
    assert "image_sku" in row_keys

    # Misspelled names MUST NOT be present
    assert "catalogue_name" not in row_keys
    assert "varient_name" not in row_keys
    assert "selling_proce" not in row_keys
    assert "image-sku" not in row_keys


def test_gallery_deduplication_and_ordering():
    """Verify primary image is never duplicated in gallery and ordering is deterministic."""
    prod = Product(
        supplier_name="Vendor A",
        catalog_name="Cat A",
        product_name="Prod A",
    )
    v = Variant(
        variant_code="VAR-1",
        supplier_sku="SKU-1",
        variant_name="Option 1",
        primary_image_url="https://blob.com/hero.jpg",
        gallery_image_urls=[
            "https://blob.com/hero.jpg",  # Accidental duplicate of primary
            "https://blob.com/view1.jpg",
            "https://blob.com/view2.jpg",
        ],
    )
    prod.add_variant(v)

    export_doc = CanonicalExport.from_products([prod])
    c_var = export_doc.products[0].variants[0]

    assert c_var.primary_image_url == "https://blob.com/hero.jpg"
    assert c_var.gallery_image_urls == [
        "https://blob.com/view1.jpg",
        "https://blob.com/view2.jpg",
    ]
    assert "https://blob.com/hero.jpg" not in c_var.gallery_image_urls


def test_flat_records_preserves_category_attributes():
    """Verify category attributes are flattened into the tabular row without data loss."""
    prod = Product(
        supplier_name="Weave365",
        catalog_name="Rayon Kurtis",
        product_name="Aura Kurti",
        attributes={"sleeve": "3/4 Sleeve", "neck": "Round Neck", "fabric": "100% Rayon"},
    )
    v = Variant(
        variant_code="WV-K-01",
        supplier_sku="WV-100",
        variant_name="Mustard L",
        color="Mustard Yellow",
        size="L",
    )
    prod.add_variant(v)

    export_doc = CanonicalExport.from_products([prod])
    flat_rows = export_doc.to_flat_records()

    assert len(flat_rows) == 1
    row = flat_rows[0]
    assert row["sleeve"] == "3/4 Sleeve"
    assert row["neck"] == "Round Neck"
    assert row["fabric"] == "100% Rayon"
    assert row["color"] == "Mustard Yellow"
    assert row["size"] == "L"
