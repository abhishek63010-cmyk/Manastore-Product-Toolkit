from pathlib import Path
import json
import pytest

from src.adapters.amra import AmraAdapter
from src.extractors.tabular import TabularExtractor
from src.images.inventory import DiscoveredImage, ImageInventory
from src.images.manifest import ApprovalStatus, ImageApprovalManifest, ImageManifestEntry
from src.images.matcher import DeterministicImageMatcher, MatchStatus
from src.images.uploader import ImageUploadService
from src.models.product import Product
from src.models.variant import Variant
from src.ocr.models import CandidateField, OCRResult, OCRStatus
from src.pipeline.config import PipelineConfig
from src.pipeline.orchestrator import PipelineOrchestrator
from src.uploaders.fake import FakeBlobUploader


@pytest.fixture
def test_dir(tmp_path: Path) -> Path:
    return tmp_path


def test_conservative_spelling_normalization():
    """Verify 'Amra' and 'Amrah' equate conservatively without fuzzy false positives."""
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Amrah Wholesale Aab Minaxi Vol 1",
        product_name="Aab Minaxi Art Silk Saree",
    )
    var = Variant(
        variant_code="AMRAH-WHOLESALE-AAB-MINAXI-VOL-1-V01",
        variant_name="Standard",
    )
    prod.add_variant(var)

    # Discovered image has 'Amra' instead of 'Amrah'
    img = DiscoveredImage.create(
        source_type="DIRECTORY",
        source_identity="/images",
        relative_path="Amra wholesale Aab Minaxi Vol 1.jpeg",
        original_filename="Amra wholesale Aab Minaxi Vol 1.jpeg",
        file_extension=".jpeg",
    )
    inventory = ImageInventory(source_path="/images", images=[img])

    report = DeterministicImageMatcher.match([prod], inventory)
    assert report.total_images == 1
    assert report.matched_count == 1
    assert report.ambiguous_count == 0
    assert report.unmatched_count == 0

    finding = report.matched_findings()[0]
    assert finding.status == MatchStatus.MATCHED
    assert finding.matched_variant_code == "AMRAH-WHOLESALE-AAB-MINAXI-VOL-1-V01"
    assert finding.match_strategy == "SCOPED_SINGLE_VARIANT_PRODUCT"


def test_multi_variant_without_tokens_marked_ambiguous():
    """
    CRITICAL RULE:
    Multi-variant catalogs with ambiguous photos (e.g. Rajtex Noorani with 6 WhatsApp photos)
    MUST be marked AMBIGUOUS with manual review required. Never guess color/variant code.
    """
    prod = Product(
        supplier_name="RAJTEX",
        catalog_name="RAJTEX NOORANI",
        product_name="Munga Handloom Saree",
    )
    for i in range(1, 7):
        prod.add_variant(Variant(
            variant_code=f"RAJTEX-NOORANI-V{i:02d}",
            variant_name=f"Design {i}",
        ))

    # WhatsApp image inside Rajtex Noorani folder without specific design token
    img = DiscoveredImage.create(
        source_type="DIRECTORY",
        source_identity="/catalog",
        relative_path="Rajtex Noorani/WhatsApp Image 2026-10-08 at 2.55.35 PM.jpeg",
        original_filename="WhatsApp Image 2026-10-08 at 2.55.35 PM.jpeg",
        file_extension=".jpeg",
    )
    inventory = ImageInventory(source_path="/catalog", images=[img])

    report = DeterministicImageMatcher.match([prod], inventory)
    assert report.total_images == 1
    assert report.matched_count == 0
    assert report.ambiguous_count == 1

    finding = report.ambiguous_findings()[0]
    assert finding.status == MatchStatus.AMBIGUOUS
    assert finding.match_strategy == "SCOPED_MULTI_VARIANT_AMBIGUOUS"
    assert len(finding.candidate_variant_codes) == 6
    assert "RAJTEX-NOORANI-V01" in finding.candidate_variant_codes
    assert "RAJTEX-NOORANI-V06" in finding.candidate_variant_codes


def test_ocr_candidate_assists_multi_variant_matching():
    """Verify provisional OCR candidates assist variant identification without fabricating truth."""
    prod = Product(
        supplier_name="RAJTEX",
        catalog_name="RAJTEX NOORANI",
        product_name="Munga Handloom Saree",
    )
    for i in range(1, 4):
        prod.add_variant(Variant(
            variant_code=f"RAJTEX-NOORANI-V{i:02d}",
            variant_name=f"Design {i}",
            image_sku=f"10800{i}",
        ))

    img = DiscoveredImage.create(
        source_type="DIRECTORY",
        source_identity="/catalog",
        relative_path="Rajtex Noorani/photo_1.jpeg",
        original_filename="photo_1.jpeg",
        file_extension=".jpeg",
    )
    inventory = ImageInventory(source_path="/catalog", images=[img])

    # OCR extracted design number matching image_sku 108002
    ocr_res = OCRResult(
        provider="fake",
        source_filename="photo_1.jpeg",
        source_identity="photo_1.jpeg",
        status=OCRStatus.SUCCESS,
        extracted_text="Design No: 108002",
        confidence=0.92,
        candidate_fields=[
            CandidateField(
                field_name="design_number",
                candidate_value="108002",
                raw_text="Design No: 108002",
                confidence=0.92,
            )
        ],
    )

    report = DeterministicImageMatcher.match(
        products=[prod],
        inventory=inventory,
        ocr_results=[ocr_res],
    )
    assert report.matched_count == 1
    assert report.ambiguous_count == 0
    finding = report.matched_findings()[0]
    assert finding.matched_variant_code == "RAJTEX-NOORANI-V02"
    assert finding.match_strategy == "OCR_CANDIDATE_VARIANT_MATCH"


def test_image_approval_manifest_lifecycle_and_tamper_detection(tmp_path: Path):
    """
    Verify ImageApprovalManifest:
    - Defaults all entries to PENDING
    - Computes assignment hash
    - Invalidation on reassignment
    - Rejection of tampered records
    """
    img = DiscoveredImage.create(
        source_type="DIRECTORY",
        source_identity="/images",
        relative_path="sample.jpg",
        original_filename="sample.jpg",
        file_extension=".jpg",
    )
    finding = DeterministicImageMatcher.match(
        products=[],
        inventory=ImageInventory(source_path="/images", images=[img]),
    ).findings[0]

    manifest = ImageApprovalManifest.create_from_match_report(
        match_report=DeterministicImageMatcher.match(
            products=[],
            inventory=ImageInventory(source_path="/images", images=[img]),
        )
    )

    entry = manifest.entries[0]
    assert entry.approval_status == ApprovalStatus.PENDING
    assert entry.is_valid_approval() is False  # Pending is not approved!

    # Assign and approve
    manifest.reassign(entry.image_id, "VAR-TEST-01", reviewer="QA_LEAD")
    assert entry.approval_status == ApprovalStatus.PENDING  # Reassignment resets to pending!
    assert entry.is_valid_approval() is False

    # Explicit approval
    manifest.approve(entry.image_id, approved_by="QA_LEAD")
    assert entry.approval_status == ApprovalStatus.APPROVED
    assert entry.is_valid_approval() is True

    # Tampering test: simulate changing proposed_variant_code without recomputing hash
    entry.proposed_variant_code = "TAMPERED-VAR-CODE"
    assert entry.is_valid_approval() is False  # Hash mismatch detected!


def test_strict_upload_gate_blocks_pending_and_ambiguous(tmp_path: Path):
    """
    Verify strict upload gate:
    1. Unapproved/pending images are blocked from upload.
    2. Explicitly approved images in manifest ARE uploaded.
    """
    prod = Product(
        supplier_name="Brand",
        catalog_name="Catalog",
        product_name="Dress",
    )
    v1 = Variant(variant_code="DRESS-V01", variant_name="Red")
    prod.add_variant(v1)

    img_file = tmp_path / "DRESS-V01.png"
    img_file.write_bytes(b"image-data-bytes")

    inventory = ImageInventory.discover(tmp_path)
    match_report = DeterministicImageMatcher.match([prod], inventory, attach_to_variants=True)
    manifest = ImageApprovalManifest.create_from_match_report(match_report)

    fake_uploader = FakeBlobUploader(base_url="https://blob.manastore.internal")

    # 1. Attempt upload with require_approval=True while manifest is PENDING
    summary_blocked = ImageUploadService.upload_matched_images(
        products=[prod],
        match_report=match_report,
        uploader=fake_uploader,
        approval_manifest=manifest,
        require_approval=True,
    )
    assert summary_blocked.successful_uploads == 0
    assert summary_blocked.skipped_count >= 1
    assert v1.primary_image_url is None

    # 2. Approve in manifest and re-attempt
    manifest.approve(manifest.entries[0].image_id, approved_by="HumanReviewer")
    assert manifest.entries[0].is_valid_approval() is True

    summary_approved = ImageUploadService.upload_matched_images(
        products=[prod],
        match_report=match_report,
        uploader=fake_uploader,
        approval_manifest=manifest,
        require_approval=True,
    )
    assert summary_approved.successful_uploads == 1
    assert v1.primary_image_url.startswith("https://blob.manastore.internal")


def test_benchmark_three_real_catalogues(tmp_path: Path):
    """
    Run full benchmark pipeline on real supplier data:
    MANA_Raw_Supplier_Data.xlsx with 8 images (2 single-variant, 6 WhatsApp multi-variant).
    Validates:
    - 3 products created
    - Rajtex Noorani: 6 variants, 6 ambiguous images
    - Aab Minaxi Vol 1: 1 variant, 1 matched image
    - Aab Maroon Velly Vol 1: 1 variant, 1 matched image
    - Human-review manifest emitted with 2 matched, 6 ambiguous, 0 unmatched
    - Manifest serialized to JSON
    """
    benchmark_dir = Path("input/raw-catalog-001")
    excel_path = benchmark_dir / "MANA_Raw_Supplier_Data.xlsx"
    assert excel_path.exists(), "Benchmark excel must exist"

    out_dir = tmp_path / "benchmark_out"

    config = PipelineConfig(
        input_path=excel_path,
        output_dir=out_dir,
        images_path=benchmark_dir,
        supplier="amra",
        export_formats=["json", "xlsx"],
        generate_report=True,
    )

    result = PipelineOrchestrator.run(config)
    assert result.success is True
    assert result.metrics.total_products == 3
    assert result.metrics.total_variants == 8  # 6 Noorani + 1 Minaxi + 1 Maroon Velly
    assert result.metrics.total_images_discovered == 8
    assert result.metrics.total_images_matched == 2
    assert result.metrics.total_images_ambiguous == 6

    # Verify manifest artifact
    manifest_file = out_dir / "image_approval_manifest.json"
    assert manifest_file.exists()
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert len(manifest_data["entries"]) == 8

    # Check status distribution
    statuses = [e["match_status"] for e in manifest_data["entries"]]
    assert statuses.count("MATCHED") == 2
    assert statuses.count("AMBIGUOUS") == 6
    assert statuses.count("UNMATCHED") == 0

    # Check Noorani entries have all 6 candidate variants
    noorani_entries = [e for e in manifest_data["entries"] if "WhatsApp" in e["original_filename"]]
    assert len(noorani_entries) == 6
    for ne in noorani_entries:
        assert ne["match_status"] == "AMBIGUOUS"
        assert len(ne["candidate_variant_codes"]) == 6
