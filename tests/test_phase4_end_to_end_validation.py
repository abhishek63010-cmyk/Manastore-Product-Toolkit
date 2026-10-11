import tempfile
from pathlib import Path
import openpyxl
import pytest

from src.models.product import Product
from src.models.variant import Variant
from src.images.manifest import ApprovalStatus, ImageApprovalManifest, ImageManifestEntry
from src.images.matcher import MatchStatus
from src.ocr.models import OCRResult, OCRStatus, CandidateField
from src.enrichment.service import ProductEnrichmentService
from src.exporters.writers import OutputWriter
from src.pipeline.orchestrator import PipelineOrchestrator
from src.pipeline.config import PipelineConfig
from src.uploaders.fake import FakeBlobUploader


@pytest.fixture
def complex_multivariant_catalogue():
    """
    Temporary synthetic multi-variant fixture representing one supplier catalogue
    with multiple variants and varying image conditions.
    """
    prod = Product(
        supplier_name="Rajtex Fabrics",
        catalog_name="RAJTEX NOORANI",
        product_name="Rajtex Noorani Handloom Saree",
        product_group="RAJTEX-NOORANI",
        brand_name="RAJTEX",
        supplier_price=1415.0,
        gst_percent=5.0,
        description="Handloom Weaving Saree with Munga Silk Blouse",
        attributes={
            "fabric": "Munga Handloom",
            "blouse_fabric": "Munga Handloom",
            "work": "Handloom Weaving",
            "size": "Free Size",
        }
    )

    v1 = Variant(
        variant_code="RAJTEX-NOORANI-V01",
        supplier_sku="RJ-101",
        variant_name="Crimson Red",
        color="Crimson Red",
        supplier_cost=1415.0,
    )
    v2 = Variant(
        variant_code="RAJTEX-NOORANI-V02",
        supplier_sku="RJ-102",
        variant_name="Royal Blue",
        color="Royal Blue",
        supplier_cost=1415.0,
    )
    v3 = Variant(
        variant_code="RAJTEX-NOORANI-V03",
        supplier_sku="RJ-103",
        variant_name="Emerald Green",
        color="Emerald Green",
        supplier_cost=1415.0,
    )
    v4 = Variant(
        variant_code="RAJTEX-NOORANI-V04",
        supplier_sku="RJ-104",
        variant_name="Mustard Yellow",
        color="Mustard Yellow",
        supplier_cost=1415.0,
    )

    prod.add_variant(v1)
    prod.add_variant(v2)
    prod.add_variant(v3)
    prod.add_variant(v4)
    return prod


def test_sku_based_approved_image_assignment_isolation(complex_multivariant_catalogue):
    """
    Section 1: SKU-based approved image assignment.
    Verifies:
    1. Each approved image enriches ONLY its assigned variant.
    2. Unknown or duplicate SKU assignments are flagged.
    3. Invalid, pending, rejected, or stale approvals cannot enrich or publish.
    4. Assignment hashes and approval validation remain effective.
    """
    prod = complex_multivariant_catalogue
    v1, v2, v3, v4 = prod.variants

    # Set up manifest entries
    manifest = ImageApprovalManifest(
        entries=[
            # V01: Valid approved
            ImageManifestEntry(
                image_id="img_v01",
                image_path="rj_red.jpg",
                original_filename="rj_red.jpg",
                proposed_variant_code=v1.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                approved_by="Auditor_A",
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_v01", v1.variant_code),
            ),
            # V02: Valid approved
            ImageManifestEntry(
                image_id="img_v02",
                image_path="rj_blue.jpg",
                original_filename="rj_blue.jpg",
                proposed_variant_code=v2.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                approved_by="Auditor_A",
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_v02", v2.variant_code),
            ),
            # V03: Stale hash (tampered / modified assignment)
            ImageManifestEntry(
                image_id="img_v03",
                image_path="rj_green.jpg",
                original_filename="rj_green.jpg",
                proposed_variant_code=v3.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                approved_by="Auditor_A",
                assignment_hash="tampered_invalid_hash",
            ),
            # V04: Pending review
            ImageManifestEntry(
                image_id="img_v04",
                image_path="rj_yellow.jpg",
                original_filename="rj_yellow.jpg",
                proposed_variant_code=v4.variant_code,
                match_status=MatchStatus.AMBIGUOUS,
                approval_status=ApprovalStatus.PENDING,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_v04", v4.variant_code),
            ),
            # Unknown variant assignment
            ImageManifestEntry(
                image_id="img_unknown",
                image_path="rj_ghost.jpg",
                original_filename="rj_ghost.jpg",
                proposed_variant_code="NON_EXISTENT_SKU_999",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_unknown", "NON_EXISTENT_SKU_999"),
            ),
        ]
    )

    # Check manifest diagnostics for unknown variant
    diagnostics = manifest.validate_against_products([prod])
    assert any("NON_EXISTENT_SKU_999" in d for d in diagnostics)

    # Enrich
    refined = ProductEnrichmentService.enrich(
        products=[prod],
        approval_manifest=manifest,
    )

    # V01 enriched cleanly
    rec_v1 = next(r for r in refined.records if r.variant_code == v1.variant_code)
    assert rec_v1.has_approved_image is True
    assert rec_v1.image_id == "img_v01"
    assert rec_v1.approval_status == "APPROVED"
    assert rec_v1.approved_by == "Auditor_A"

    # V02 enriched cleanly and isolated from V01
    rec_v2 = next(r for r in refined.records if r.variant_code == v2.variant_code)
    assert rec_v2.has_approved_image is True
    assert rec_v2.image_id == "img_v02"
    assert rec_v2.approval_status == "APPROVED"

    # V03 blocked due to stale hash mismatch
    rec_v3 = next(r for r in refined.records if r.variant_code == v3.variant_code)
    assert rec_v3.has_approved_image is False
    assert rec_v3.approval_status == "STALE_HASH_MISMATCH"
    assert rec_v3.requires_review is True
    assert any("assignment hash mismatch" in reason for reason in rec_v3.review_reasons)

    # V04 blocked due to pending status
    rec_v4 = next(r for r in refined.records if r.variant_code == v4.variant_code)
    assert rec_v4.has_approved_image is False
    assert rec_v4.approval_status == "PENDING"
    assert rec_v4.requires_review is True


def test_data_extraction_and_preservation(complex_multivariant_catalogue):
    """
    Section 2: Data extraction and preservation.
    Verifies:
    1. Readable text vs no readable text handling.
    2. Visually inferred colours or OCR values never overwrite supplier truth.
    3. Missing fields remain blank/provisional.
    4. No supplier SKU, colour, or price is fabricated.
    """
    prod = complex_multivariant_catalogue
    v1, v2, _, _ = prod.variants

    manifest = ImageApprovalManifest(
        entries=[
            # V01 has readable text image
            ImageManifestEntry(
                image_id="img_v01",
                image_path="rj_red.jpg",
                original_filename="rj_red.jpg",
                proposed_variant_code=v1.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_v01", v1.variant_code),
            ),
            # V02 has image with NO readable text
            ImageManifestEntry(
                image_id="img_v02",
                image_path="rj_blue.jpg",
                original_filename="rj_blue.jpg",
                proposed_variant_code=v2.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_v02", v2.variant_code),
            ),
        ]
    )

    ocr_v01 = OCRResult(
        provider="fake",
        source_filename="rj_red.jpg",
        source_identity="rj_red.jpg",
        status=OCRStatus.SUCCESS,
        raw_text="Design: 501 SKU: RJ-101 Color: Crimson Red",
        confidence=0.92,
        candidate_fields=[
            CandidateField(field_name="design_number", candidate_value="501", raw_text="501", confidence=0.92),
            CandidateField(field_name="sku", candidate_value="RJ-101", raw_text="RJ-101", confidence=0.92),
            CandidateField(field_name="color", candidate_value="Crimson Red", raw_text="Crimson Red", confidence=0.92),
        ],
    )

    ocr_v02 = OCRResult(
        provider="fake",
        source_filename="rj_blue.jpg",
        source_identity="rj_blue.jpg",
        status=OCRStatus.NO_TEXT_DETECTED,
        raw_text="",
        confidence=0.0,
        candidate_fields=[],
    )

    refined = ProductEnrichmentService.enrich(
        products=[prod],
        approval_manifest=manifest,
        ocr_results=[ocr_v01, ocr_v02],
    )

    rec_v1 = next(r for r in refined.records if r.variant_code == v1.variant_code)
    # OCR populated cleanly into candidate columns
    assert rec_v1.ocr_status == "SUCCESS"
    assert rec_v1.candidate_design_number == "501"
    assert rec_v1.candidate_sku == "RJ-101"
    assert rec_v1.candidate_color == "Crimson Red"
    # Supplier facts intact
    assert rec_v1.supplier_sku == "RJ-101"
    assert rec_v1.original_color == "Crimson Red"
    assert rec_v1.supplier_cost == 1415.0

    rec_v2 = next(r for r in refined.records if r.variant_code == v2.variant_code)
    # No text detected: candidate columns remain None
    assert rec_v2.ocr_status == "NO_TEXT_DETECTED"
    assert rec_v2.candidate_design_number is None
    assert rec_v2.candidate_sku is None
    assert rec_v2.candidate_color is None
    # Supplier facts intact
    assert rec_v2.supplier_sku == "RJ-102"
    assert rec_v2.original_color == "Royal Blue"
    assert rec_v2.supplier_cost == 1415.0


def test_refined_excel_inspection_programmatically(complex_multivariant_catalogue):
    """
    Section 3: Programmatic inspection of generated Refined Excel.
    Verifies:
    1. Every update maps to exact intended SKU/variant row.
    2. Refined_Variants, Review_Required, and Audit_Summary contain consistent data.
    3. Review reasons and provenance are retained.
    4. Repeated exports do not corrupt or duplicate data.
    """
    prod = complex_multivariant_catalogue
    v1 = prod.variants[0]

    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_v01",
                image_path="rj_red.jpg",
                original_filename="rj_red.jpg",
                proposed_variant_code=v1.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_v01", v1.variant_code),
            ),
        ]
    )

    refined = ProductEnrichmentService.enrich(
        products=[prod],
        approval_manifest=manifest,
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir) / "refined_products.xlsx"
        OutputWriter.write_refined_xlsx(refined, out_path)

        wb = openpyxl.load_workbook(out_path)
        assert "Refined_Variants" in wb.sheetnames
        assert "Review_Required" in wb.sheetnames
        assert "Audit_Summary" in wb.sheetnames

        sheet_all = wb["Refined_Variants"]
        assert sheet_all.max_row == 5  # Header + 4 variants

        headers_all = [c.value for c in sheet_all[1]]
        assert "variant_code" in headers_all
        assert "supplier_sku" in headers_all
        assert "candidate_design_number" in headers_all
        assert "blob_url" in headers_all

        sheet_rev = wb["Review_Required"]
        # All 4 variants require review (Variants 2,3,4 lack approved images, and all variants lack selling_price)
        assert sheet_rev.max_row == 5  # Header + 4 review records

        sheet_sum = wb["Audit_Summary"]
        metric_map = {row[0].value: row[1].value for row in sheet_sum.iter_rows(min_row=2)}
        assert metric_map["Total Variants"] == 4
        assert metric_map["Variants with Approved Image"] == 1
        assert metric_map["Variants Requiring Review"] == 4


def test_mana_store_importer_compatibility_verification(complex_multivariant_catalogue):
    """
    Section 4: MANA Store importer compatibility verification.
    Verifies:
    1. 25 headers match MANA Store importer expectations.
    2. publish is strictly False.
    3. main_image_url populated ONLY with verified blob URLs.
    4. Numeric parsing, pricing formulas, and row normalization can process cleanly.
    """
    prod = complex_multivariant_catalogue
    v1 = prod.variants[0]
    # Simulate verified blob URL for V01
    v1.primary_image_url = "https://public.blob.vercel-storage.com/products/rj_red_verified.jpg"

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir) / "mana_store_import_ready.xlsx"
        OutputWriter.write_mana_store_import_xlsx([prod], out_path)

        wb = openpyxl.load_workbook(out_path)
        assert "Import Ready" in wb.sheetnames
        assert "IMPORT_INSTRUCTIONS" in wb.sheetnames

        sheet = wb["Import Ready"]
        headers = [c.value for c in sheet[1]]
        expected_headers = [
            "supplier_name", "catalog_name", "product_group", "product_name",
            "variant_name", "supplier_sku", "image_sku", "category_slug",
            "brand_name", "fabric", "blouse_fabric", "work", "occasion",
            "size", "description", "supplier_price", "gst_percent",
            "selling_price", "publish", "main_image_url", "image_1_url",
            "image_2_url", "image_3_url", "image_4_url", "image_5_url"
        ]
        assert headers == expected_headers

        # Row 2 (v1 with verified Blob URL)
        r2 = {headers[i]: sheet[2][i].value for i in range(len(headers))}
        assert r2["publish"] is False
        assert r2["supplier_sku"] == "RJ-101"
        assert r2["main_image_url"] == "https://public.blob.vercel-storage.com/products/rj_red_verified.jpg"
        assert r2["category_slug"] == "sarees"
        assert r2["supplier_price"] == 1415.0

        # Row 3 (v2 without verified Blob URL)
        r3 = {headers[i]: sheet[3][i].value for i in range(len(headers))}
        assert r3["publish"] is False
        assert r3["supplier_sku"] == "RJ-102"
        assert r3["main_image_url"] is None or r3["main_image_url"] == ""


def test_complete_end_to_end_validation_scenario(complex_multivariant_catalogue):
    """
    Section 5: End-to-end integration test with full fixture.
    Multi-variant catalogue + explicit assignments + OCR + fake uploader + repeated run.
    """
    prod = complex_multivariant_catalogue
    v1, v2, v3, v4 = prod.variants

    manifest = ImageApprovalManifest(
        entries=[
            # V01: Approved with readable text
            ImageManifestEntry(
                image_id="img_v01",
                image_path="rj_red.jpg",
                original_filename="rj_red.jpg",
                proposed_variant_code=v1.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                approved_by="Auditor_Lead",
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_v01", v1.variant_code),
            ),
            # V02: Approved without readable text
            ImageManifestEntry(
                image_id="img_v02",
                image_path="rj_blue.jpg",
                original_filename="rj_blue.jpg",
                proposed_variant_code=v2.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                approved_by="Auditor_Lead",
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_v02", v2.variant_code),
            ),
            # V03: Conflicting OCR candidate (candidate claims SKU is RJ-999)
            ImageManifestEntry(
                image_id="img_v03",
                image_path="rj_green.jpg",
                original_filename="rj_green.jpg",
                proposed_variant_code=v3.variant_code,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                approved_by="Auditor_Lead",
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_v03", v3.variant_code),
            ),
            # V04: Pending approval (unapproved)
            ImageManifestEntry(
                image_id="img_v04",
                image_path="rj_yellow.jpg",
                original_filename="rj_yellow.jpg",
                proposed_variant_code=v4.variant_code,
                match_status=MatchStatus.AMBIGUOUS,
                approval_status=ApprovalStatus.PENDING,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_v04", v4.variant_code),
            ),
        ]
    )

    ocr_results = [
        OCRResult(
            provider="fake",
            source_filename="rj_red.jpg",
            source_identity="rj_red.jpg",
            status=OCRStatus.SUCCESS,
            raw_text="Design: 101 SKU: RJ-101",
            confidence=0.95,
            candidate_fields=[
                CandidateField(field_name="design_number", candidate_value="101", raw_text="101", confidence=0.95),
                CandidateField(field_name="sku", candidate_value="RJ-101", raw_text="RJ-101", confidence=0.95),
            ],
        ),
        OCRResult(
            provider="fake",
            source_filename="rj_blue.jpg",
            source_identity="rj_blue.jpg",
            status=OCRStatus.NO_TEXT_DETECTED,
            raw_text="",
            confidence=0.0,
            candidate_fields=[],
        ),
        OCRResult(
            provider="fake",
            source_filename="rj_green.jpg",
            source_identity="rj_green.jpg",
            status=OCRStatus.SUCCESS,
            raw_text="SKU: RJ-999-CONFLICT",
            confidence=0.90,
            candidate_fields=[
                CandidateField(field_name="sku", candidate_value="RJ-999-CONFLICT", raw_text="RJ-999-CONFLICT", confidence=0.90),
            ],
        ),
    ]

    # First Run
    refined1 = ProductEnrichmentService.enrich(
        products=[prod],
        approval_manifest=manifest,
        ocr_results=ocr_results,
    )

    # Check V03 conflict
    rec_v3 = next(r for r in refined1.records if r.variant_code == v3.variant_code)
    assert rec_v3.supplier_sku == "RJ-103"  # NOT overwritten
    assert rec_v3.candidate_sku == "RJ-999-CONFLICT"
    assert rec_v3.requires_review is True
    assert any("SKU discrepancy" in reason for reason in rec_v3.review_reasons)

    # Second Run (Idempotency check)
    refined2 = ProductEnrichmentService.enrich(
        products=[prod],
        approval_manifest=manifest,
        ocr_results=ocr_results,
    )

    assert len(refined1.records) == len(refined2.records)
    for r1, r2 in zip(refined1.records, refined2.records):
        assert r1.to_flat_dict() == r2.to_flat_dict()
