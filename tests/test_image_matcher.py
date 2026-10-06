import pytest
from src.images.inventory import DiscoveredImage, ImageInventory
from src.images.matcher import (
    DeterministicImageMatcher,
    MatchStatus,
)
from src.models.enums import FieldSource
from src.models.product import Product
from src.models.variant import Variant


@pytest.fixture
def sample_catalog_with_variants() -> list[Product]:
    """Create a sample product catalog with multiple variants sharing a supplier SKU."""
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        product_name="Mahotsav Soft Chinon Silk Saree",
        product_group="Mahots-K",
    )
    v1 = Variant(
        variant_code="Mahots-K-S3207",
        supplier_sku="AM-17090",
        variant_name="Emerald Green",
        color="Emerald Green",
        image_sku="Mahots-K-S3207",
        primary_image_url="https://source.cdn.com/Amra/Mahotsav/Mahots-K-S3207.png",
    )
    v2 = Variant(
        variant_code="Mahots-K-S3208",
        supplier_sku="AM-17090",
        variant_name="Coral Red",
        color="Coral Red",
        image_sku="Mahots-K-S3208",
        primary_image_url="https://source.cdn.com/Amra/Mahotsav/Mahots-K-S3208.png",
    )
    prod.add_variant(v1)
    prod.add_variant(v2)
    return [prod]


def test_exact_image_sku_matching(sample_catalog_with_variants):
    """Verify Priority 1: Exact image_sku / variant_code matching."""
    images = [
        DiscoveredImage.create(
            source_type="DIRECTORY",
            source_identity="/fake/dir",
            relative_path="Mahots-K-S3207.png",
            original_filename="Mahots-K-S3207.png",
            file_extension=".png",
        ),
        DiscoveredImage.create(
            source_type="DIRECTORY",
            source_identity="/fake/dir",
            relative_path="Mahots-K-S3208.png",
            original_filename="Mahots-K-S3208.png",
            file_extension=".png",
        ),
    ]
    inventory = ImageInventory(source_path="/fake/dir", images=images)
    report = DeterministicImageMatcher.match(sample_catalog_with_variants, inventory)

    assert report.total_images == 2
    assert report.matched_count == 2
    assert report.unmatched_count == 0
    assert report.ambiguous_count == 0

    findings = report.matched_findings()
    assert findings[0].matched_variant_code == "Mahots-K-S3207"
    assert findings[0].match_strategy in ("EXACT_VARIANT_CODE", "EXACT_IMAGE_SKU")
    assert findings[1].matched_variant_code == "Mahots-K-S3208"


def test_shared_supplier_sku_with_index_disambiguation():
    """
    Verify matching when filenames contain supplier SKU and numeric index
    (e.g. rajtex_1.jpg, rajtex_2.jpg matching variants of AM-1726I).
    """
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Rajtex Ajrakh Vol-35",
        product_name="Rajtex Ajrakh Printed Saree",
        product_group="Rajtex-A",
    )
    v1 = Variant(
        variant_code="Rajtex-A-035-A",
        supplier_sku="AM-1726I",
        variant_name="Design A",
    )
    v2 = Variant(
        variant_code="Rajtex-A-035-B",
        supplier_sku="AM-1726I",
        variant_name="Design B",
    )
    prod.add_variant(v1)
    prod.add_variant(v2)

    images = [
        DiscoveredImage.create(
            source_type="ZIP",
            source_identity="rajtex.zip",
            relative_path="images/AM-1726I_1.jpg",
            original_filename="AM-1726I_1.jpg",
            file_extension=".jpg",
        ),
        DiscoveredImage.create(
            source_type="ZIP",
            source_identity="rajtex.zip",
            relative_path="images/AM-1726I_2.jpg",
            original_filename="AM-1726I_2.jpg",
            file_extension=".jpg",
        ),
    ]
    inventory = ImageInventory(source_path="rajtex.zip", images=images)
    report = DeterministicImageMatcher.match([prod], inventory, attach_to_variants=True)

    assert report.matched_count == 2
    assert v1.images[0].original_filename == "AM-1726I_1.jpg"
    assert v1.images[0].is_primary is True
    assert v2.images[0].original_filename == "AM-1726I_2.jpg"
    assert v2.images[0].is_primary is True


def test_ambiguous_matching_when_sku_shared_without_disambiguator():
    """
    Verify that if multiple variants share a supplier SKU and the image filename
    has NO disambiguating info, it is marked AMBIGUOUS rather than guessing.
    """
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Test Collection",
        product_name="Test Product",
    )
    v1 = Variant(
        variant_code="TEST-V1",
        supplier_sku="AM-SHARED-99",
        variant_name="Red Option",
    )
    v2 = Variant(
        variant_code="TEST-V2",
        supplier_sku="AM-SHARED-99",
        variant_name="Blue Option",
    )
    prod.add_variant(v1)
    prod.add_variant(v2)

    images = [
        DiscoveredImage.create(
            source_type="DIRECTORY",
            source_identity="/dir",
            relative_path="AM-SHARED-99.jpg",
            original_filename="AM-SHARED-99.jpg",
            file_extension=".jpg",
        ),
    ]
    inventory = ImageInventory(source_path="/dir", images=images)
    report = DeterministicImageMatcher.match([prod], inventory)

    assert report.matched_count == 0
    assert report.ambiguous_count == 1
    ambiguous = report.ambiguous_findings()[0]
    assert ambiguous.status == MatchStatus.AMBIGUOUS
    assert set(ambiguous.candidate_variant_codes) == {"TEST-V1", "TEST-V2"}
    assert "multiple variants share this SKU" in ambiguous.reason


def test_unmatched_image_handling(sample_catalog_with_variants):
    """Verify completely unassociated images are flagged UNMATCHED."""
    images = [
        DiscoveredImage.create(
            source_type="DIRECTORY",
            source_identity="/dir",
            relative_path="unrelated_banner_photo.jpg",
            original_filename="unrelated_banner_photo.jpg",
            file_extension=".jpg",
        ),
    ]
    inventory = ImageInventory(source_path="/dir", images=images)
    report = DeterministicImageMatcher.match(sample_catalog_with_variants, inventory)

    assert report.unmatched_count == 1
    unmatched = report.unmatched_findings()[0]
    assert unmatched.status == MatchStatus.UNMATCHED
    assert unmatched.reason == "No reliable variant identity found"


def test_cross_product_isolation():
    """
    CRITICAL REQUIREMENT:
    Verify that an image for Product A (Variant 'Green') never matches Product B (Variant 'Green').
    """
    prod_a = Product(
        supplier_name="Supplier A",
        catalog_name="Silk Sarees 2026",
        product_name="Pure Silk Saree",
        product_group="SILK-A",
    )
    v_a = Variant(
        variant_code="SILK-A-GREEN",
        supplier_sku="SKU-A",
        variant_name="Bottle Green",
        color="Green",
    )
    prod_a.add_variant(v_a)

    prod_b = Product(
        supplier_name="Supplier B",
        catalog_name="Cotton Kurtis 2026",
        product_name="Cotton Daily Kurti",
        product_group="KURTI-B",
    )
    v_b = Variant(
        variant_code="KURTI-B-GREEN",
        supplier_sku="SKU-B",
        variant_name="Bottle Green",
        color="Green",
    )
    prod_b.add_variant(v_b)

    images = [
        DiscoveredImage.create(
            source_type="DIRECTORY",
            source_identity="/dir",
            relative_path="SILK-A/Bottle-Green.jpg",
            original_filename="Bottle-Green.jpg",
            file_extension=".jpg",
        ),
    ]
    inventory = ImageInventory(source_path="/dir", images=images)
    report = DeterministicImageMatcher.match([prod_a, prod_b], inventory)

    assert report.matched_count == 1
    matched = report.matched_findings()[0]
    # MUST match SILK-A-GREEN, NEVER KURTI-B-GREEN
    assert matched.matched_product_id == prod_a.product_id
    assert matched.matched_variant_code == "SILK-A-GREEN"


def test_deterministic_multi_image_ordering():
    """
    Verify:
    1. Multi-image natural sorting (image1, image2, image10)
    2. Primary image is always sort_index 0 (is_primary=True)
    3. Gallery images have sort_index > 0 (is_primary=False)
    4. Repeated runs produce identical ordering
    """
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Sethnic Vantasa",
        product_name="Sethnic Vantasa Saree",
    )
    v = Variant(
        variant_code="SETHNI-V-108001",
        supplier_sku="AM-17066",
        variant_name="Royal Blue",
        image_sku="SETHNI-V-108001",
    )
    prod.add_variant(v)

    # Discovered in shuffled order
    images = [
        DiscoveredImage.create(
            source_type="DIRECTORY",
            source_identity="/dir",
            relative_path="SETHNI-V-108001-10.png",
            original_filename="SETHNI-V-108001-10.png",
            file_extension=".png",
        ),
        DiscoveredImage.create(
            source_type="DIRECTORY",
            source_identity="/dir",
            relative_path="SETHNI-V-108001.png",
            original_filename="SETHNI-V-108001.png",
            file_extension=".png",
        ),
        DiscoveredImage.create(
            source_type="DIRECTORY",
            source_identity="/dir",
            relative_path="SETHNI-V-108001-2.png",
            original_filename="SETHNI-V-108001-2.png",
            file_extension=".png",
        ),
        DiscoveredImage.create(
            source_type="DIRECTORY",
            source_identity="/dir",
            relative_path="SETHNI-V-108001-1.png",
            original_filename="SETHNI-V-108001-1.png",
            file_extension=".png",
        ),
    ]
    inventory = ImageInventory(source_path="/dir", images=images)

    # Run 1
    report1 = DeterministicImageMatcher.match([prod], inventory, attach_to_variants=True)
    assets1 = [img.original_filename for img in v.images]

    # Run 2
    report2 = DeterministicImageMatcher.match([prod], inventory, attach_to_variants=True)
    assets2 = [img.original_filename for img in v.images]

    # Assert deterministic repeatability
    assert assets1 == assets2

    # Assert natural ordering: SETHNI-V-108001.png (main) -> -1.png -> -2.png -> -10.png
    assert assets1 == [
        "SETHNI-V-108001.png",
        "SETHNI-V-108001-1.png",
        "SETHNI-V-108001-2.png",
        "SETHNI-V-108001-10.png",
    ]

    # Check primary / sort_index flags
    assert v.images[0].is_primary is True
    assert v.images[0].sort_index == 0

    assert v.images[1].is_primary is False
    assert v.images[1].sort_index == 1

    assert v.images[2].is_primary is False
    assert v.images[2].sort_index == 2

    assert v.images[3].is_primary is False
    assert v.images[3].sort_index == 3


def test_provenance_and_source_url_isolation():
    """
    Verify:
    1. ImageAsset provenance tracks matching method and source file
    2. Remote source URL is preserved without being confused with Blob URL
    """
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        product_name="Mahotsav Soft Chinon Silk Saree",
    )
    v = Variant(
        variant_code="Mahots-K-S3207",
        supplier_sku="AM-17090",
        variant_name="Emerald Green",
        primary_image_url="https://source.example.com/Amra/Mahots-K-S3207.png",
    )
    prod.add_variant(v)

    images = [
        DiscoveredImage.create(
            source_type="ZIP",
            source_identity="Mahotsav_Kriti.zip",
            relative_path="Mahots-K-S3207.png",
            original_filename="Mahots-K-S3207.png",
            file_extension=".png",
        ),
    ]
    inventory = ImageInventory(source_path="Mahotsav_Kriti.zip", images=images)
    DeterministicImageMatcher.match([prod], inventory, attach_to_variants=True)

    assert len(v.images) == 1
    asset = v.images[0]
    assert asset.blob_url is None  # NOT set because Blob upload hasn't happened yet!
    assert v.primary_image_url == "https://source.example.com/Amra/Mahots-K-S3207.png"  # Source URL intact
    assert asset.source_metadata.field_provenance["variant_code"].source_type == FieldSource.SOURCE
    assert asset.source_metadata.source_file == "Mahotsav_Kriti.zip"
