import json
from pathlib import Path
import pandas as pd
import pytest

from src.enrichment.service import ProductEnrichmentService
from src.exporters.writers import OutputWriter
from src.images.inventory import DiscoveredImage, ImageInventory
from src.images.manifest import ApprovalStatus, ImageApprovalManifest, ImageManifestEntry
from src.images.matcher import DeterministicImageMatcher, MatchStatus
from src.images.uploader import ImageUploadService
from src.models.product import Product
from src.models.refined import RefinedExport, RefinedVariantRecord
from src.models.variant import Variant
from src.ocr.models import CandidateField, OCRResult, OCRStatus
from src.pipeline.config import PipelineConfig
from src.pipeline.orchestrator import PipelineOrchestrator
from src.uploaders.fake import FakeBlobUploader


@pytest.fixture
def sample_product_with_variant() -> tuple[Product, Variant]:
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Amrah Wholesale Aab Minaxi Vol 1",
        product_name="Aab Minaxi Art Silk Saree",
        product_group="AMRA-MINAXI",
        brand_name="Amrah Wholesale",
        supplier_price=419.0,
        gst_percent=5.0,
        description="Art Silk Jacquard Saree",
    )
    prod.attributes = {
        "fabric": "Art Silk",
        "blouse_fabric": "Art Silk",
        "work": "Jacquard Work",
        "size": "Free Size",
    }

    var = Variant(
        variant_code="AMRAH-AAB-MINAXI-V01",
        supplier_sku="AM-17090",
        variant_name="Emerald Green",
        color="Emerald Green",
        supplier_cost=419.0,
    )
    prod.add_variant(var)
    return prod, var


def test_approved_image_with_readable_design_information(sample_product_with_variant):
    """
    Verify:
    1. Only approved image assignment is enriched.
    2. OCR candidates are extracted and populated into candidate_* columns.
    3. Raw supplier data remains completely unmutated.
    """
    prod, var = sample_product_with_variant

    # Create approved manifest entry
    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_12345",
                image_path="Amra wholesale Aab Minaxi Vol 1.jpeg",
                original_filename="Amra wholesale Aab Minaxi Vol 1.jpeg",
                catalog_name="Amrah Wholesale Aab Minaxi Vol 1",
                proposed_variant_code="AMRAH-AAB-MINAXI-V01",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                approved_by="LeadReviewer",
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_12345", "AMRAH-AAB-MINAXI-V01"),
            )
        ]
    )

    # OCR result with design number, SKU, and color
    ocr_result = OCRResult(
        provider="fake",
        source_filename="Amra wholesale Aab Minaxi Vol 1.jpeg",
        source_identity="Amra wholesale Aab Minaxi Vol 1.jpeg",
        status=OCRStatus.SUCCESS,
        raw_text="Design: 108001 SKU: AM-17090 Color: Emerald Green Rate: 419",
        confidence=0.95,
        candidate_fields=[
            CandidateField(field_name="design_number", candidate_value="108001", raw_text="Design: 108001", confidence=0.95),
            CandidateField(field_name="sku", candidate_value="AM-17090", raw_text="SKU: AM-17090", confidence=0.95),
            CandidateField(field_name="color", candidate_value="Emerald Green", raw_text="Color: Emerald Green", confidence=0.95),
            CandidateField(field_name="rate", candidate_value="419.0", raw_text="Rate: 419", confidence=0.95),
        ],
    )

    refined = ProductEnrichmentService.enrich(
        products=[prod],
        approval_manifest=manifest,
        ocr_results=[ocr_result],
    )

    assert refined.total_variants == 1
    assert refined.approved_image_variants == 1
    record = refined.records[0]

    # Verify approved image metadata
    assert record.has_approved_image is True
    assert record.image_id == "img_12345"
    assert record.approval_status == "APPROVED"
    assert record.approved_by == "LeadReviewer"

    # Verify OCR candidate fields
    assert record.ocr_status == "SUCCESS"
    assert record.candidate_design_number == "108001"
    assert record.candidate_sku == "AM-17090"
    assert record.candidate_color == "Emerald Green"
    assert record.candidate_rate == 419.0

    # CRITICAL: Verify original supplier data preserved intact
    assert record.supplier_sku == "AM-17090"
    assert record.original_color == "Emerald Green"
    assert record.supplier_cost == 419.0
    assert var.supplier_sku == "AM-17090"  # Original model not overwritten


def test_approved_image_without_readable_text(sample_product_with_variant):
    """
    Verify approved image with NO_TEXT_FOUND preserves image association
    without fabricating any candidate fields.
    """
    prod, var = sample_product_with_variant

    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_photo_only",
                image_path="clean_fabric.jpeg",
                original_filename="clean_fabric.jpeg",
                proposed_variant_code="AMRAH-AAB-MINAXI-V01",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                approved_by="QA_Staff",
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_photo_only", "AMRAH-AAB-MINAXI-V01"),
            )
        ]
    )

    ocr_no_text = OCRResult(
        provider="fake",
        source_filename="clean_fabric.jpeg",
        source_identity="clean_fabric.jpeg",
        status=OCRStatus.NO_TEXT_DETECTED,
        raw_text="",
        confidence=0.0,
        candidate_fields=[],
    )

    refined = ProductEnrichmentService.enrich(
        products=[prod],
        approval_manifest=manifest,
        ocr_results=[ocr_no_text],
    )

    record = refined.records[0]
    assert record.has_approved_image is True
    assert record.image_id == "img_photo_only"
    assert record.ocr_status == "NO_TEXT_DETECTED"
    assert record.candidate_design_number is None
    assert record.candidate_sku is None
    # Original supplier data still intact
    assert record.supplier_sku == "AM-17090"


def test_pending_rejected_ambiguous_and_stale_approved_assignments():
    """
    Verify quality gate rules:
    - PENDING: has_approved_image=False, requires_review=True
    - REJECTED: has_approved_image=False, requires_review=True
    - UNASSIGNED/AMBIGUOUS: has_approved_image=False, requires_review=True
    - STALE_HASH_MISMATCH: has_approved_image=False, requires_review=True
    """
    prod = Product(
        supplier_name="Brand",
        catalog_name="Catalog Alpha",
        product_name="Saree Collection",
    )
    v1 = Variant(variant_code="VAR-PENDING", variant_name="Pending Variant")
    v2 = Variant(variant_code="VAR-REJECTED", variant_name="Rejected Variant")
    v3 = Variant(variant_code="VAR-UNASSIGNED", variant_name="Unassigned Variant")
    v4 = Variant(variant_code="VAR-STALE", variant_name="Stale Hash Variant")
    prod.add_variant(v1)
    prod.add_variant(v2)
    prod.add_variant(v3)
    prod.add_variant(v4)

    manifest = ImageApprovalManifest(
        entries=[
            # Pending entry
            ImageManifestEntry(
                image_id="img_pend",
                image_path="p.jpg",
                original_filename="p.jpg",
                proposed_variant_code="VAR-PENDING",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.PENDING,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_pend", "VAR-PENDING"),
            ),
            # Rejected entry
            ImageManifestEntry(
                image_id="img_rej",
                image_path="r.jpg",
                original_filename="r.jpg",
                proposed_variant_code="VAR-REJECTED",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.REJECTED,
                approved_by="Manager",
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_rej", "VAR-REJECTED"),
            ),
            # Stale entry: marked APPROVED but hash does NOT match
            ImageManifestEntry(
                image_id="img_stale",
                image_path="s.jpg",
                original_filename="s.jpg",
                proposed_variant_code="VAR-STALE",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                approved_by="Manager",
                assignment_hash="hash_tampered_or_stale",  # Deliberate mismatch
            ),
        ]
    )

    refined = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)
    rec_by_code = {r.variant_code: r for r in refined.records}

    # 1. Pending
    assert rec_by_code["VAR-PENDING"].has_approved_image is False
    assert rec_by_code["VAR-PENDING"].approval_status == "PENDING"
    assert rec_by_code["VAR-PENDING"].requires_review is True

    # 2. Rejected
    assert rec_by_code["VAR-REJECTED"].has_approved_image is False
    assert rec_by_code["VAR-REJECTED"].approval_status == "REJECTED"
    assert rec_by_code["VAR-REJECTED"].requires_review is True

    # 3. Unassigned
    assert rec_by_code["VAR-UNASSIGNED"].has_approved_image is False
    assert rec_by_code["VAR-UNASSIGNED"].approval_status == "UNASSIGNED"
    assert rec_by_code["VAR-UNASSIGNED"].requires_review is True

    # 4. Stale Hash Mismatch
    assert rec_by_code["VAR-STALE"].has_approved_image is False
    assert rec_by_code["VAR-STALE"].approval_status == "STALE_HASH_MISMATCH"
    assert rec_by_code["VAR-STALE"].requires_review is True
    assert any("hash mismatch" in r.lower() for r in rec_by_code["VAR-STALE"].review_reasons)


def test_conflicting_ocr_candidates_vs_manually_verified_data(sample_product_with_variant):
    """
    CRITICAL SAFETY REQUIREMENT:
    When OCR candidate conflicts with original supplier data:
    1. Supplier data is NEVER overwritten.
    2. Discrepancy is recorded in candidate_* columns.
    3. requires_review is flagged True with detailed diagnostic notes.
    """
    prod, var = sample_product_with_variant
    var.supplier_sku = "AM-17090"
    var.color = "Royal Blue"

    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_conflict",
                image_path="test.jpg",
                original_filename="test.jpg",
                proposed_variant_code=var.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_conflict", var.variant_code),
            )
        ]
    )

    # Conflicting OCR: claims SKU is AM-99999 and color is Red
    ocr_conflict = OCRResult(
        provider="fake",
        source_filename="test.jpg",
        source_identity="test.jpg",
        status=OCRStatus.SUCCESS,
        raw_text="SKU: AM-99999 Color: Red",
        confidence=0.88,
        candidate_fields=[
            CandidateField(field_name="sku", candidate_value="AM-99999", raw_text="SKU: AM-99999", confidence=0.88),
            CandidateField(field_name="color", candidate_value="Red", raw_text="Color: Red", confidence=0.88),
        ],
    )

    refined = ProductEnrichmentService.enrich(
        products=[prod],
        approval_manifest=manifest,
        ocr_results=[ocr_conflict],
    )

    record = refined.records[0]
    # 1. Supplier data remains intact
    assert record.supplier_sku == "AM-17090"
    assert record.original_color == "Royal Blue"

    # 2. Candidate data stored separately
    assert record.candidate_sku == "AM-99999"
    assert record.candidate_color == "Red"

    # 3. Quality gate flagged for review
    assert record.requires_review is True
    reasons_str = " ".join(record.review_reasons)
    assert "SKU discrepancy" in reasons_str
    assert "Color discrepancy" in reasons_str


def test_missing_blob_url_and_failed_upload(sample_product_with_variant):
    """
    Verify:
    1. Failed or missing upload results in is_uploaded=False, blob_url=None.
    2. No fabricated or dummy URL is emitted.
    """
    prod, var = sample_product_with_variant
    var.primary_image_url = None  # No upload

    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_noupload",
                image_path="test.jpg",
                original_filename="test.jpg",
                proposed_variant_code=var.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_noupload", var.variant_code),
            )
        ]
    )

    refined = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)
    record = refined.records[0]

    assert record.is_uploaded is False
    assert record.blob_url is None


def test_repeat_run_idempotency(sample_product_with_variant):
    """Verify repeated execution on identical inputs yields identical refined records."""
    prod, var = sample_product_with_variant

    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_idem",
                image_path="test.jpg",
                original_filename="test.jpg",
                proposed_variant_code=var.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_idem", var.variant_code),
            )
        ]
    )

    run1 = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)
    run2 = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)

    assert run1.summary() == run2.summary()
    assert run1.to_flat_records() == run2.to_flat_records()


def test_refined_excel_and_mana_store_export_files(tmp_path: Path, sample_product_with_variant):
    """
    Verify:
    1. refined_products.xlsx contains Refined_Variants, Review_Required, Audit_Summary sheets.
    2. mana_store_import_ready.xlsx strictly conforms to the MANA Store importer contract:
       - 'Import Ready' sheet with all 25 contract columns.
       - 'IMPORT_INSTRUCTIONS' sheet.
       - publish is strictly False.
       - main_image_url is empty when no Blob URL exists.
       - supplier_sku preserved separately from variant_code.
    """
    prod, var = sample_product_with_variant
    var.selling_price = 899.0  # Explicitly verified price

    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_ok",
                image_path="test.jpg",
                original_filename="test.jpg",
                proposed_variant_code=var.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_ok", var.variant_code),
            )
        ]
    )

    refined_data = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)

    # 1. Test Refined Excel Output
    p_refined = OutputWriter.write_refined_xlsx(refined_data, tmp_path / "refined_products.xlsx")
    assert p_refined.exists()
    xl_ref = pd.ExcelFile(p_refined)
    assert "Refined_Variants" in xl_ref.sheet_names
    assert "Review_Required" in xl_ref.sheet_names
    assert "Audit_Summary" in xl_ref.sheet_names

    # 2. Test MANA Store Importer Output
    p_mana = OutputWriter.write_mana_store_import_xlsx([prod], tmp_path / "mana_store_import_ready.xlsx")
    assert p_mana.exists()
    xl_mana = pd.ExcelFile(p_mana)
    assert "Import Ready" in xl_mana.sheet_names
    assert "IMPORT_INSTRUCTIONS" in xl_mana.sheet_names

    df_import = xl_mana.parse("Import Ready")
    expected_cols = [
        "supplier_name", "catalog_name", "product_group", "product_name",
        "variant_name", "supplier_sku", "image_sku", "category_slug",
        "brand_name", "fabric", "blouse_fabric", "work", "occasion", "size",
        "description", "supplier_price", "gst_percent", "selling_price",
        "publish", "main_image_url", "image_1_url", "image_2_url",
        "image_3_url", "image_4_url", "image_5_url"
    ]
    for col in expected_cols:
        assert col in df_import.columns, f"Column '{col}' must exist in MANA Store import sheet"

    # Contract invariants
    first_row = df_import.iloc[0]
    assert first_row["supplier_name"] == "Amra Wholesale"
    assert first_row["supplier_sku"] == "AM-17090"  # Real supplier SKU preserved
    assert first_row["publish"] is False or str(first_row["publish"]).upper() == "FALSE"
    assert first_row["main_image_url"] == "" or pd.isna(first_row["main_image_url"])  # No Blob URL uploaded yet


def test_three_catalogue_benchmark_phase3_enrichment(tmp_path: Path):
    """
    Run the end-to-end pipeline benchmark on the 3 real supplier catalogues
    (MANA_Raw_Supplier_Data.xlsx with 8 images):
    - Confirms generation of refined_products.xlsx and mana_store_import_ready.xlsx.
    - Confirms that without human approval, all images remain PENDING and un-uploaded.
    - Simulates human approval of the 2 single-variant images and verifies that only
      those 2 obtain uploaded Blob URLs in mana_store_import_ready.xlsx.
    """
    benchmark_dir = Path("input/raw-catalog-001")
    excel_path = benchmark_dir / "MANA_Raw_Supplier_Data.xlsx"
    out_dir = tmp_path / "phase3_benchmark"

    # Initial Run (No pre-existing approvals)
    cfg1 = PipelineConfig(
        input_path=excel_path,
        output_dir=out_dir,
        images_path=benchmark_dir,
        supplier="amra",
        export_formats=["xlsx", "json"],
        generate_report=True,
    )
    res1 = PipelineOrchestrator.run(cfg1)

    assert res1.success is True
    assert res1.metrics.total_products == 3
    assert res1.metrics.total_variants == 8
    assert (out_dir / "_internal" / "refined_products.xlsx").exists()
    assert (out_dir / "_internal" / "mana_store_import_ready.xlsx").exists()
    assert (out_dir / "_internal" / "image_approval_manifest.json").exists()

    # Load initial manifest: all 8 entries start PENDING
    manifest1 = ImageApprovalManifest.load_from_file(out_dir / "_internal" / "image_approval_manifest.json")
    assert manifest1.summary()["pending_approval_count"] == 8

    # Now simulate human review: Approve the 2 single-variant matched images
    matched_entries = [e for e in manifest1.entries if e.match_status == MatchStatus.MATCHED]
    assert len(matched_entries) == 2
    for e in matched_entries:
        manifest1.approve(e.image_id, approved_by="CatalogManager")

    approved_manifest_path = out_dir / "approved_manifest.json"
    manifest1.save_to_file(approved_manifest_path)

    # Re-run pipeline with approved manifest, require_image_approval=True, and FakeBlobUploader
    fake_uploader = FakeBlobUploader(base_url="https://blob.manastore.internal")
    out_dir2 = tmp_path / "phase3_benchmark_approved"
    cfg2 = PipelineConfig(
        input_path=excel_path,
        output_dir=out_dir2,
        images_path=benchmark_dir,
        supplier="amra",
        manifest_path=approved_manifest_path,
        upload_images=True,
        require_image_approval=True,
        export_formats=["xlsx", "json"],
        generate_report=True,
    )
    res2 = PipelineOrchestrator.run(cfg2, uploader=fake_uploader)

    assert res2.success is True
    assert res2.metrics.total_images_approved == 2
    assert res2.metrics.total_images_uploaded == 2  # Exactly the 2 approved images uploaded!
    assert res2.upload_summary.successful_uploads == 2

    # Verify mana_store_import_ready.xlsx in _internal/ has Blob URLs for the 2 approved variants ONLY
    df_mana = pd.read_excel(out_dir2 / "_internal" / "mana_store_import_ready.xlsx", sheet_name="Import Ready")
    assert len(df_mana) == 8

    # Filter rows by catalogue
    minaxi_row = df_mana[df_mana["catalog_name"] == "Amrah Wholesale Aab Minaxi Vol 1"].iloc[0]
    maroon_row = df_mana[df_mana["catalog_name"] == "Amrah Wholesale Aab Maroon Velly Vol 1"].iloc[0]
    noorani_rows = df_mana[df_mana["catalog_name"] == "RAJTEX NOORANI"]

    assert minaxi_row["main_image_url"].startswith("https://blob.manastore.internal")
    assert maroon_row["main_image_url"].startswith("https://blob.manastore.internal")

    # Rajtex Noorani variants (all 6 were ambiguous, never approved) must have EMPTY main_image_url
    for _, n_row in noorani_rows.iterrows():
        assert pd.isna(n_row["main_image_url"]) or n_row["main_image_url"] == ""
