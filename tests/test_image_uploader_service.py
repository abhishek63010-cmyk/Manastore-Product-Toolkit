from pathlib import Path
from zipfile import ZipFile
import pytest
from src.images.inventory import DiscoveredImage, ImageInventory
from src.images.matcher import DeterministicImageMatcher
from src.images.uploader import ImageUploadService
from src.models.enums import FieldSource
from src.models.product import Product
from src.models.variant import Variant
from src.uploaders.fake import FakeBlobUploader


@pytest.fixture
def staged_product_and_images(tmp_path):
    """Create test product, variant, and local test image files."""
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        product_name="Mahotsav Soft Chinon Silk Saree",
        product_group="Mahots-K",
    )
    v = Variant(
        variant_code="Mahots-K-S3207",
        supplier_sku="AM-17090",
        variant_name="Emerald Green",
        image_sku="Mahots-K-S3207",
        primary_image_url="https://source.cdn.com/raw_amra/Mahots-K-S3207.png",  # Remote source URL
    )
    prod.add_variant(v)

    # Create dummy images on disk
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    img_hero = img_dir / "Mahots-K-S3207.png"
    img_gallery1 = img_dir / "Mahots-K-S3207-1.png"
    img_gallery2 = img_dir / "Mahots-K-S3207-2.png"
    img_unrelated = img_dir / "unrelated_banner.jpg"

    img_hero.write_bytes(b"hero-image-bytes")
    img_gallery1.write_bytes(b"gallery-1-bytes")
    img_gallery2.write_bytes(b"gallery-2-bytes")
    img_unrelated.write_bytes(b"banner-bytes")

    return prod, v, img_dir


def test_upload_matched_images_full_pipeline(staged_product_and_images):
    """
    Verify complete upload and mapping pipeline:
    1. Matcher matches valid images
    2. Uploader uploads only MATCHED images
    3. Populates ImageAsset.blob_url
    4. Sets variant.primary_image_url (index 0)
    5. Sets variant.gallery_image_urls in exact natural order (index 1, 2)
    6. Preserves original primary_image_url as source metadata
    """
    prod, variant, img_dir = staged_product_and_images
    inventory = ImageInventory.discover(img_dir)
    match_report = DeterministicImageMatcher.match([prod], inventory, attach_to_variants=True)

    assert match_report.matched_count == 3
    assert match_report.unmatched_count == 1

    fake_uploader = FakeBlobUploader(base_url="https://ladg3qktuldto6fi.public.blob.vercel-storage.com")
    upload_summary = ImageUploadService.upload_matched_images(
        products=[prod],
        match_report=match_report,
        uploader=fake_uploader,
        prefix="Amra",
    )

    # 1. Verify upload metrics
    assert upload_summary.total_attempted == 3
    assert upload_summary.successful_uploads == 3
    assert upload_summary.skipped_count == 1  # Unrelated banner skipped
    assert upload_summary.failed_count == 0

    # 2. Verify Primary Image mapping (index 0)
    assert len(variant.images) == 3
    hero_asset = variant.images[0]
    assert hero_asset.is_primary is True
    assert hero_asset.sort_index == 0
    assert hero_asset.blob_url == "https://ladg3qktuldto6fi.public.blob.vercel-storage.com/Amra/Amra-Wholesale/Mahotsav-Kriti/Mahots-K-S3207/Mahots-K-S3207.png"
    assert variant.primary_image_url == hero_asset.blob_url

    # 3. Verify Gallery Images mapping (index 1, 2)
    assert len(variant.gallery_image_urls) == 2
    assert variant.gallery_image_urls[0] == "https://ladg3qktuldto6fi.public.blob.vercel-storage.com/Amra/Amra-Wholesale/Mahotsav-Kriti/Mahots-K-S3207/Mahots-K-S3207-1.png"
    assert variant.gallery_image_urls[1] == "https://ladg3qktuldto6fi.public.blob.vercel-storage.com/Amra/Amra-Wholesale/Mahotsav-Kriti/Mahots-K-S3207/Mahots-K-S3207-2.png"

    # 4. Verify Provenance tracking
    assert hero_asset.source_metadata.field_provenance["blob_url"].source_type == FieldSource.DERIVED
    assert "fake_blob_uploader" in hero_asset.source_metadata.field_provenance["blob_url"].notes


def test_ambiguous_and_unmatched_images_never_uploaded(tmp_path):
    """
    CRITICAL REQUIREMENT:
    Ambiguous and Unmatched images must NOT be uploaded or assigned to variants.
    """
    prod = Product(
        supplier_name="Vendor A",
        catalog_name="Collection A",
        product_name="Product A",
    )
    v1 = Variant(variant_code="V-1", supplier_sku="SKU-SHARED", variant_name="Red")
    v2 = Variant(variant_code="V-2", supplier_sku="SKU-SHARED", variant_name="Blue")
    prod.add_variant(v1)
    prod.add_variant(v2)

    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    (img_dir / "SKU-SHARED.jpg").write_bytes(b"shared-sku-data")  # Ambiguous
    (img_dir / "random_file.png").write_bytes(b"random-data")    # Unmatched

    inventory = ImageInventory.discover(img_dir)
    match_report = DeterministicImageMatcher.match([prod], inventory, attach_to_variants=True)

    assert match_report.matched_count == 0
    assert match_report.ambiguous_count == 1
    assert match_report.unmatched_count == 1

    fake_uploader = FakeBlobUploader()
    upload_summary = ImageUploadService.upload_matched_images(
        products=[prod],
        match_report=match_report,
        uploader=fake_uploader,
    )

    assert upload_summary.total_attempted == 0
    assert upload_summary.successful_uploads == 0
    assert upload_summary.skipped_count == 2
    assert len(fake_uploader.get_uploaded_keys()) == 0
    assert v1.images == []
    assert v2.images == []


def test_upload_from_zip_archive(tmp_path):
    """Verify streaming image upload directly from inside a ZIP archive without unzipping to disk."""
    zip_file = tmp_path / "supplier_images.zip"
    with ZipFile(zip_file, "w") as zf:
        zf.writestr("images/Rajtex-A-035-A.png", b"zip-image-a")
        zf.writestr("images/Rajtex-A-035-B.png", b"zip-image-b")

    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Rajtex Ajrakh Vol-35",
        product_name="Rajtex Ajrakh Printed Saree",
        product_group="Rajtex-A",
    )
    v1 = Variant(variant_code="Rajtex-A-035-A", supplier_sku="AM-1726I", variant_name="Design A", image_sku="Rajtex-A-035-A")
    v2 = Variant(variant_code="Rajtex-A-035-B", supplier_sku="AM-1726I", variant_name="Design B", image_sku="Rajtex-A-035-B")
    prod.add_variant(v1)
    prod.add_variant(v2)

    inventory = ImageInventory.discover(zip_file)
    match_report = DeterministicImageMatcher.match([prod], inventory, attach_to_variants=True)
    assert match_report.matched_count == 2

    fake_uploader = FakeBlobUploader()
    summary = ImageUploadService.upload_matched_images([prod], match_report, fake_uploader)

    assert summary.successful_uploads == 2
    assert v1.primary_image_url.endswith("Rajtex-A-035-A.png")
    assert v2.primary_image_url.endswith("Rajtex-A-035-B.png")


def test_upload_service_idempotency(staged_product_and_images):
    """Verify repeating the upload process reuses existing blob URLs idempotently."""
    prod, variant, img_dir = staged_product_and_images
    inventory = ImageInventory.discover(img_dir)
    match_report = DeterministicImageMatcher.match([prod], inventory, attach_to_variants=True)

    fake_uploader = FakeBlobUploader()

    # Pass 1
    summary1 = ImageUploadService.upload_matched_images([prod], match_report, fake_uploader)
    assert summary1.successful_uploads == 3
    assert all(not r.is_idempotent_reuse for r in summary1.results)

    # Pass 2: identical data
    summary2 = ImageUploadService.upload_matched_images([prod], match_report, fake_uploader)
    assert summary2.successful_uploads == 3
    assert all(r.is_idempotent_reuse for r in summary2.results)
    assert variant.primary_image_url == summary1.results[0].blob_url
