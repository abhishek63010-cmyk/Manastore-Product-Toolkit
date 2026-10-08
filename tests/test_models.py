import pytest
from pydantic import ValidationError
from src.models import (
    ImageAsset,
    Product,
    ReviewStatus,
    Variant,
)


def test_product_creation_minimal():
    """Verify minimal Product creation with only required fields."""
    product = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        product_name="Mahotsav Soft Chinon Silk Party Wear Saree",
    )
    assert product.supplier_name == "Amra Wholesale"
    assert product.catalog_name == "Mahotsav Kriti"
    assert product.product_name == "Mahotsav Soft Chinon Silk Party Wear Saree"
    assert product.product_type == "Saree"
    assert product.variants == []
    assert product.attributes == {}
    assert product.raw_attributes == {}
    assert product.product_id is not None


def test_product_creation_full():
    """Verify full Product creation with attributes, pricing, and metadata."""
    product = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Rajtex Ajrakh Vol-35",
        product_name="Rajtex Ajrakh Printed Modal Satin Saree",
        product_group="Rajtex-A",
        brand_name="Rajtex",
        product_type="Saree",
        category_slug="sarees",
        description="Traditional Ajrakh block printed saree in premium modal satin.",
        supplier_price=780.0,
        gst_percent=5.0,
        moq=1,
        dispatch_location="Surat, India",
        attributes={
            "fabric": "Modal Satin",
            "blouse_fabric": "Modal Satin",
            "work": "Ajrakh Block Printed Work",
            "occasion": "Casual Wear, Traditional Wear",
            "saree_length": "5.5 m",
            "blouse_length": "0.8 m",
            "stitch_type": "Unstitched",
        },
        raw_attributes={
            "source_upload_date": "2026-09-28",
            "catalogue_price_per_piece": 580,
            "design_count": 6,
        },
    )
    assert product.supplier_price == 780.0
    assert product.gst_percent == 5.0
    assert product.get_attribute("fabric") == "Modal Satin"
    assert product.get_attribute("FABRIC") == "Modal Satin"
    assert product.get_attribute("saree_length") == "5.5 m"
    assert product.raw_attributes["design_count"] == 6


def test_variant_creation():
    """Verify Variant creation with required and optional fields."""
    variant = Variant(
        variant_code="Mahots-K-S3207",
        supplier_sku="AM-17090",
        variant_name="Emerald Green",
        color="Emerald Green",
        supplier_cost=855.0,
        selling_price=999.0,
        image_sku="Mahots-K-S3207",
        primary_image_url="https://blob.example.com/Mahots-K-S3207.png",
        publish=False,
    )
    assert variant.variant_code == "Mahots-K-S3207"
    assert variant.supplier_sku == "AM-17090"
    assert variant.variant_name == "Emerald Green"
    assert variant.selling_price == 999.0
    assert variant.publish is False
    assert variant.review_status == ReviewStatus.PENDING


def test_multiple_variants_sharing_one_supplier_sku():
    """
    CRITICAL REQUIREMENT:
    Verify that multiple variants can intentionally share the same supplier_sku.
    """
    product = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        product_name="Mahotsav Soft Chinon Silk Party Wear Saree",
    )

    variant_1 = Variant(
        variant_code="Mahots-K-S3207",
        supplier_sku="AM-17090",
        variant_name="Emerald Green",
        selling_price=999.0,
    )
    variant_2 = Variant(
        variant_code="Mahots-K-S3208",
        supplier_sku="AM-17090",  # Shared supplier_sku
        variant_name="Coral Red",
        selling_price=999.0,
    )
    variant_3 = Variant(
        variant_code="Mahots-K-S3209",
        supplier_sku="AM-17090",  # Shared supplier_sku
        variant_name="Blush Pink",
        selling_price=999.0,
    )

    product.add_variant(variant_1)
    product.add_variant(variant_2)
    product.add_variant(variant_3)

    assert len(product.variants) == 3
    # All share the same supplier_sku
    assert {v.supplier_sku for v in product.variants} == {"AM-17090"}
    # But each has a unique variant_code
    assert len({v.variant_code for v in product.variants}) == 3


def test_duplicate_variant_code_raises_error():
    """Verify that duplicate variant_code is rejected within a Product."""
    product = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        product_name="Mahotsav Soft Chinon Silk Party Wear Saree",
    )

    variant_1 = Variant(
        variant_code="Mahots-K-S3207",
        supplier_sku="AM-17090",
        variant_name="Emerald Green",
    )
    variant_duplicate = Variant(
        variant_code="Mahots-K-S3207",
        supplier_sku="AM-17090",
        variant_name="Emerald Green Duplicate",
    )

    product.add_variant(variant_1)
    with pytest.raises(ValueError, match="Duplicate variant_code"):
        product.add_variant(variant_duplicate)


def test_image_asset_creation():
    """Verify ImageAsset fields, deterministic sorting, and primary flag."""
    image_hero = ImageAsset(
        original_filename="Mahots-K-S3207.png",
        supplier_sku="AM-17090",
        variant_code="Mahots-K-S3207",
        source_identity="Mahotsav_Kriti.zip",
        sort_index=0,
        is_primary=True,
        blob_url="https://blob.example.com/Mahots-K-S3207.png",
    )
    image_gallery = ImageAsset(
        original_filename="Mahots-K-S3207-1.png",
        supplier_sku="AM-17090",
        variant_code="Mahots-K-S3207",
        source_identity="Mahotsav_Kriti.zip",
        sort_index=1,
        is_primary=False,
        blob_url="https://blob.example.com/Mahots-K-S3207-1.png",
    )

    assert image_hero.is_primary is True
    assert image_hero.sort_index == 0
    assert image_gallery.is_primary is False
    assert image_gallery.sort_index == 1
    assert image_hero.image_id != image_gallery.image_id


def test_extensible_attributes_non_saree_categories():
    """
    Verify that models gracefully accommodate diverse product categories
    (e.g., Kurti, Jewellery, Home Products) without rigid global schema coupling.
    """
    kurti_product = Product(
        supplier_name="Weave365",
        catalog_name="Aura Rayon Kurtis",
        product_name="Aura Embroidered Straight Kurti",
        product_type="Kurti",
        category_slug="kurtis",
        attributes={
            "fabric": "100% Rayon",
            "sleeve_length": "3/4 Sleeves",
            "neck_style": "Round Neck",
            "pattern": "Embroidered",
        },
    )
    assert kurti_product.get_attribute("fabric") == "100% Rayon"
    assert kurti_product.get_attribute("sleeve_length") == "3/4 Sleeves"
    assert kurti_product.get_attribute("blouse_fabric") is None

    jewellery_product = Product(
        supplier_name="Shree Gems",
        catalog_name="Kundan Choker Set",
        product_name="Bridal Kundan Necklace Set with Earrings",
        product_type="Jewellery",
        category_slug="jewellery",
        attributes={
            "material": "Brass Alloy",
            "plating": "Gold Plated",
            "stone_type": "Kundan & Pearls",
            "closure": "Drawstring",
        },
    )
    assert jewellery_product.get_attribute("material") == "Brass Alloy"
    assert jewellery_product.get_attribute("fabric") is None


def test_optional_fields_absent_without_failure():
    """Verify that omitting optional fields uses clean defaults without errors."""
    product = Product(
        supplier_name="Test Supplier",
        catalog_name="Test Catalog",
        product_name="Test Product",
    )
    assert product.brand_name is None
    assert product.category_slug is None
    assert product.description is None
    assert product.supplier_price is None
    assert product.gst_percent is None
    assert product.moq is None
    assert product.dispatch_location is None

    variant = Variant(
        variant_code="V-001",
        supplier_sku="SKU-001",
        variant_name="Default",
    )
    assert variant.color is None
    assert variant.size is None
    assert variant.supplier_cost is None
    assert variant.selling_price is None
    assert variant.stock_quantity is None
    assert variant.image_sku is None
    assert variant.primary_image_url is None
    assert variant.gallery_image_urls == []


def test_blank_string_validation():
    """Verify that blank whitespace strings in mandatory fields raise validation errors."""
    with pytest.raises(ValidationError):
        Product(
            supplier_name="   ",
            catalog_name="Valid Catalog",
            product_name="Valid Product",
        )

    with pytest.raises(ValidationError):
        Variant(
            variant_code="",
            supplier_sku="SKU-1",
            variant_name="Variant 1",
        )
