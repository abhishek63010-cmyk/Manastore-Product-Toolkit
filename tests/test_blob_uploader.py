import pytest
from src.uploaders.base import BaseBlobUploader, UploadStatus
from src.uploaders.fake import FakeBlobUploader
from src.uploaders.vercel_blob import VercelBlobUploader


def test_deterministic_destination_key_generation():
    """Verify deterministic destination key format with sanitized segments."""
    key = BaseBlobUploader.generate_destination_key(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        variant_code="Mahots-K-S3207",
        filename="Mahots-K-S3207.png",
        prefix="products",
    )
    assert key == "products/Amra-Wholesale/Mahotsav-Kriti/Mahots-K-S3207/Mahots-K-S3207.png"


def test_same_inputs_produce_same_key():
    """Verify idempotency in destination key generation."""
    key1 = BaseBlobUploader.generate_destination_key(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        variant_code="Mahots-K-S3207",
        filename="photo.jpg",
    )
    key2 = BaseBlobUploader.generate_destination_key(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        variant_code="Mahots-K-S3207",
        filename="photo.jpg",
    )
    assert key1 == key2


def test_different_variant_codes_produce_different_keys():
    """
    CRITICAL REQUIREMENT:
    Even if two variants share the exact same catalog and filename,
    their destination keys must differ based on variant_code.
    """
    key_var1 = BaseBlobUploader.generate_destination_key(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        variant_code="Mahots-K-S3207",
        filename="main.png",
    )
    key_var2 = BaseBlobUploader.generate_destination_key(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        variant_code="Mahots-K-S3208",
        filename="main.png",
    )
    assert key_var1 != key_var2
    assert "Mahots-K-S3207" in key_var1
    assert "Mahots-K-S3208" in key_var2


def test_shared_supplier_sku_no_key_collision():
    """
    Verify variants sharing supplier_sku (e.g. AM-17090) do not collide
    because destination keys use variant_code.
    """
    key1 = BaseBlobUploader.generate_destination_key(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        variant_code="Mahots-K-S3207",
        filename="shot_1.jpg",
    )
    key2 = BaseBlobUploader.generate_destination_key(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        variant_code="Mahots-K-S3208",
        filename="shot_1.jpg",
    )
    assert key1 != key2


def test_unsafe_filenames_sanitized():
    """Verify unsafe special characters, spaces, and path separators in filenames are sanitized."""
    key = BaseBlobUploader.generate_destination_key(
        supplier_name="Vendor & Co. / Surat",
        catalog_name="Summer #1 [2026]",
        variant_code="VAR 01 (Special)",
        filename="My Image (1) & copy.PNG",
        prefix="assets/test",
    )
    assert key == "assets/test/Vendor-Co-Surat/Summer-1-2026/VAR-01-Special/My-Image-1-copy.png"


def test_content_type_detection():
    """Verify correct MIME content type detection across supported image formats."""
    assert BaseBlobUploader.determine_content_type("pic.jpg") == "image/jpeg"
    assert BaseBlobUploader.determine_content_type("pic.jpeg") == "image/jpeg"
    assert BaseBlobUploader.determine_content_type("pic.png") == "image/png"
    assert BaseBlobUploader.determine_content_type("pic.webp") == "image/webp"


def test_fake_uploader_basic_and_idempotency():
    """Verify FakeBlobUploader upload, deterministic URL, and idempotent re-upload."""
    uploader = FakeBlobUploader(base_url="https://blob.example.com")
    payload = b"fake-png-binary-data"
    key = "products/Amra/Cat/V1/main.png"

    # 1. First upload
    res1 = uploader.upload_bytes(data=payload, destination_key=key, content_type="image/png")
    assert res1.status == UploadStatus.UPLOADED
    assert res1.blob_url == "https://blob.example.com/products/Amra/Cat/V1/main.png"
    assert res1.is_idempotent_reuse is False
    assert uploader.get_payload(key) == payload

    # 2. Idempotent re-upload of exact same data
    res2 = uploader.upload_bytes(data=payload, destination_key=key, content_type="image/png")
    assert res2.status == UploadStatus.UPLOADED
    assert res2.blob_url == res1.blob_url
    assert res2.is_idempotent_reuse is True


def test_fake_uploader_collision_detection():
    """Verify that uploading different data to an existing key triggers COLLISION."""
    uploader = FakeBlobUploader()
    key = "products/Amra/Cat/V1/main.png"

    res1 = uploader.upload_bytes(data=b"original-image", destination_key=key)
    assert res1.status == UploadStatus.UPLOADED

    # Attempt to upload DIFFERENT data at same key
    res2 = uploader.upload_bytes(data=b"different-image-content", destination_key=key)
    assert res2.status == UploadStatus.COLLISION
    assert res2.blob_url is None
    assert "already contains different content" in res2.error_message


def test_fake_uploader_simulated_failures():
    """Verify failure simulation handling."""
    uploader = FakeBlobUploader(simulate_failure=True)
    res = uploader.upload_bytes(data=b"data", destination_key="some/key.jpg")
    assert res.status == UploadStatus.FAILED
    assert "Simulated upload network/service failure" in res.error_message

    uploader_cred = FakeBlobUploader(simulate_missing_credentials=True)
    res_cred = uploader_cred.upload_bytes(data=b"data", destination_key="some/key.jpg")
    assert res_cred.status == UploadStatus.FAILED
    assert "Missing blob storage credentials" in res_cred.error_message


def test_vercel_uploader_missing_token_safe_handling():
    """Verify VercelBlobUploader gracefully fails without token and does NOT leak credentials."""
    uploader = VercelBlobUploader(token=None)
    assert uploader.has_credentials is False

    res = uploader.upload_bytes(data=b"pixels", destination_key="products/test.png")
    assert res.status == UploadStatus.FAILED
    assert "token not configured" in res.error_message
    assert res.blob_url is None
