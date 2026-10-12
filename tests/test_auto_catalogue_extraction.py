import csv
import json
from pathlib import Path
import pytest

from src.extractors.image import ImageCatalogueExtractor
from src.extractors.tabular import TabularExtractor
from src.images.manifest import ApprovalStatus
from src.models.enums import ReviewStatus, ValidationSeverity
from src.ocr.fake import FakeOCRExtractor
from src.ocr.models import OCRResult
from src.ocr.registry import OCRRegistry
from src.pipeline.config import PipelineConfig
from src.pipeline.orchestrator import PipelineOrchestrator


class MockCatalogueOCRExtractor(FakeOCRExtractor):
    """Custom mock OCR extractor that injects realistic supplier catalogue image text."""

    def __init__(self, raw_text: str):
        super().__init__(default_text=raw_text)


@pytest.fixture
def register_mock_ocr():
    """Register custom mock OCR provider and restore registry after test."""
    original_providers = dict(OCRRegistry._providers)

    def _register(name: str, text: str):
        OCRRegistry.register(name, lambda: MockCatalogueOCRExtractor(text))

    yield _register
    OCRRegistry._providers = original_providers


def test_labeled_catalogue_image_to_structured_products(tmp_path: Path, register_mock_ocr):
    """
    1. A labelled catalogue image or document is OCR-processed and automatically transformed
    into structured product data.
    """
    catalogue_img = tmp_path / "catalogue_slip.jpg"
    catalogue_img.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 100)  # Valid fake JPEG header

    ocr_text = (
        "Catalog Name: Gulzar Festive Collection\n"
        "Brand: Shubh Silk Mills\n"
        "Wholesale Price: Rs. 1450/-\n"
        "Single Piece: Rs. 1650/-\n"
        "Total Designs: 4 Pcs\n"
        "Fabric: Pure Dola Silk\n"
        "Work: Zari Weaving\n"
        "GST: 5%\n"
    )
    register_mock_ocr("test_slip_ocr", ocr_text)

    raw_data = ImageCatalogueExtractor.extract(
        file_path=catalogue_img,
        ocr_provider="test_slip_ocr",
    )

    assert raw_data.total_rows == 1
    record_data = raw_data.records[0].data
    assert record_data["catalog_name"] == "Gulzar Festive Collection"
    assert record_data["brand_name"] == "Shubh Silk Mills"
    assert record_data["wholesale_price"] == 1450.0
    assert record_data["supplier_price"] == 1650.0
    assert record_data["single_piece_price"] == 1650.0
    assert record_data["total_designs"] == 4
    assert record_data["fabric"] == "Pure Dola Silk"
    assert record_data["work"] == "Zari Weaving"
    assert record_data["gst_percent"] == 5.0


def test_single_product_declaring_variant_count_generates_all_variants(tmp_path: Path, register_mock_ocr):
    """
    2. A single product row declaring 6 designs automatically generates 1 parent product
    and 6 distinct variants.
    """
    catalogue_img = tmp_path / "raas_leela.png"
    catalogue_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    ocr_text = (
        "Catalog: Raas Leela Premium\n"
        "Wholesale Price: 850\n"
        "Total Designs: 6 Pcs\n"
        "Fabric: Chanderi Cotton\n"
    )
    register_mock_ocr("test_raas_ocr", ocr_text)

    out_dir = tmp_path / "output_raas"
    cfg = PipelineConfig(
        input_path=catalogue_img,
        output_dir=out_dir,
        ocr_provider="test_raas_ocr",
        export_formats=["json"],
    )

    result = PipelineOrchestrator.run(cfg)
    assert result.success is True
    assert len(result.products) == 1
    prod = result.products[0]
    assert prod.catalog_name == "Raas Leela Premium"
    assert prod.supplier_price == 850.0
    assert len(prod.variants) == 6

    # Verify deterministic variants Design 01 to Design 06
    for idx, v in enumerate(prod.variants, start=1):
        assert v.variant_name == f"Design {idx:02d}"
        assert v.variant_code == f"RAAS-LEELA-PREMIUM-V{idx:02d}"
        assert v.supplier_cost == 850.0
        assert v.selling_price is None  # Never invented!
        assert v.color is None  # Pending human review/approval!


def test_existing_multi_variant_catalogue_does_not_duplicate(tmp_path: Path):
    """
    3. Existing multi-variant rows (e.g., 3 explicit rows) with declared count do not duplicate
    or generate phantom variants.
    """
    csv_file = tmp_path / "explicit_variants.csv"
    csv_file.write_text(
        "Catalog Name,Variant Name,Supplier SKU,Wholesale Price,Total Designs\n"
        "Ananya Vol 2,Design Red,SKU-A1,1200,3 Pcs\n"
        "Ananya Vol 2,Design Blue,SKU-A2,1200,3 Pcs\n"
        "Ananya Vol 2,Design Green,SKU-A3,1200,3 Pcs\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "output_explicit"
    cfg = PipelineConfig(
        input_path=csv_file,
        output_dir=out_dir,
        export_formats=["json"],
    )

    result = PipelineOrchestrator.run(cfg)
    assert result.success is True
    assert len(result.products) == 1
    prod = result.products[0]
    assert len(prod.variants) == 3  # Explicit 3 rows preserved, not 3*3 = 9
    assert [v.variant_name for v in prod.variants] == ["Design Red", "Design Blue", "Design Green"]
    assert [v.supplier_sku for v in prod.variants] == ["SKU-A1", "SKU-A2", "SKU-A3"]


def test_category_attributes_and_unknown_fields_preserved_without_data_loss(tmp_path: Path, register_mock_ocr):
    """
    4. Generic extraction preserves category attributes and unknown attributes in raw_attributes.
    """
    catalogue_doc = tmp_path / "custom_attrs.jpg"
    catalogue_doc.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 100)

    ocr_text = (
        "Catalog: Royale Heritage\n"
        "Price: Rs 2200\n"
        "Total Designs: 2 Pcs\n"
        "Fabric: Tussar Silk\n"
        "Blouse Piece: Unstitched Jacquard 0.8m\n"
        "Weave Type: Handloom Kadwa\n"
        "Origin: Varanasi\n"
    )
    register_mock_ocr("test_attrs_ocr", ocr_text)

    cfg = PipelineConfig(
        input_path=catalogue_doc,
        output_dir=tmp_path / "out_attrs",
        ocr_provider="test_attrs_ocr",
        export_formats=["json"],
    )

    result = PipelineOrchestrator.run(cfg)
    assert result.success is True
    prod = result.products[0]

    # Category and line-by-line key-value pairs preserved without data loss
    assert prod.raw_attributes.get("weave_type") == "Handloom Kadwa"
    assert prod.raw_attributes.get("origin") == "Varanasi"
    assert prod.raw_attributes.get("blouse_piece") == "Unstitched Jacquard 0.8m"
    assert prod.raw_attributes.get("fabric") == "Tussar Silk"


def test_wholesale_and_single_piece_prices_distinguished_and_mapped_separately(tmp_path: Path, register_mock_ocr):
    """
    5. Wholesale and single piece prices are distinguished and mapped separately.
    When single piece price is available, supplier_price defaults to single piece price.
    """
    catalogue_doc = tmp_path / "pricing.jpg"
    catalogue_doc.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 100)

    ocr_text = (
        "Catalog: Classic Banarasi\n"
        "Wholesale Price: Rs 999/-\n"
        "Single Piece: Rs 1199/-\n"
        "Total Designs: 1 Pcs\n"
    )
    register_mock_ocr("test_pricing_ocr", ocr_text)

    cfg = PipelineConfig(
        input_path=catalogue_doc,
        output_dir=tmp_path / "out_pricing",
        ocr_provider="test_pricing_ocr",
        export_formats=["json"],
    )

    result = PipelineOrchestrator.run(cfg)
    assert result.success is True
    prod = result.products[0]

    # Supplier price mapped to single piece price
    assert prod.supplier_price == 1199.0
    assert prod.variants[0].supplier_cost == 1199.0

    # Wholesale price and single-piece price preserved in attributes and raw_attributes
    assert prod.attributes.get("single_piece_price") == 1199.0
    assert prod.attributes.get("wholesale_price") == 999.0
    assert prod.raw_attributes.get("single_piece_price") == 1199.0
    assert prod.raw_attributes.get("wholesale_price") == 999.0


def test_ambiguous_and_missing_data_flagged_for_review_never_invented(tmp_path: Path, register_mock_ocr):
    """
    6. Missing data (selling price, missing supplier SKU) are flagged for review and never invented.
    """
    catalogue_doc = tmp_path / "no_skus.jpg"
    catalogue_doc.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 100)

    ocr_text = (
        "Catalog: Noorani Georgette\n"
        "Wholesale Price: 750\n"
        "Total Designs: 2 Pcs\n"
    )
    register_mock_ocr("test_nosku_ocr", ocr_text)

    cfg = PipelineConfig(
        input_path=catalogue_doc,
        output_dir=tmp_path / "out_review",
        ocr_provider="test_nosku_ocr",
        export_formats=["json"],
    )

    result = PipelineOrchestrator.run(cfg)
    assert result.success is True
    prod = result.products[0]

    for v in prod.variants:
        # Supplier SKU is NOT invented
        assert v.supplier_sku is None
        # Selling price is NOT invented
        assert v.selling_price is None

    # Validation report flags warnings for commercial review
    warnings = result.validation_report.warnings()
    warning_codes = [w.code for w in warnings]
    assert "MISSING_SUPPLIER_SKU" in warning_codes
    assert "MISSING_SELLING_PRICE" in warning_codes


def test_product_images_not_arbitrarily_matched_without_evidence(tmp_path: Path, register_mock_ocr):
    """
    7. Product images in an image folder are NOT arbitrarily matched to variants.
    Multi-variant images remain AMBIGUOUS pending human approval.
    """
    catalogue_doc = tmp_path / "multi_var.jpg"
    catalogue_doc.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 100)

    ocr_text = (
        "Catalog: Chandni Chowk\n"
        "Wholesale Price: 500\n"
        "Total Designs: 3 Pcs\n"
    )
    register_mock_ocr("test_multi_ocr", ocr_text)

    # Create image directory with 3 unlabeled images for this collection
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    for i in range(1, 4):
        (img_dir / f"chandni_chowk_photo_{i}.jpg").write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 50)

    cfg = PipelineConfig(
        input_path=catalogue_doc,
        images_path=img_dir,
        output_dir=tmp_path / "out_img_evidence",
        ocr_provider="test_multi_ocr",
        export_formats=["json"],
    )

    result = PipelineOrchestrator.run(cfg)
    assert result.success is True
    assert result.match_report is not None

    # None of the 3 photos have distinct design numbers or SKU barcodes,
    # so they MUST be classified as ambiguous rather than randomly assigned!
    assert result.match_report.ambiguous_count == 3
    assert result.match_report.matched_count == 0

    # Human-review manifest created with PENDING review status
    manifest = result.approval_manifest
    assert manifest is not None
    assert len(manifest.entries) == 3
    for entry in manifest.entries:
        assert entry.approval_status == ApprovalStatus.PENDING
        assert entry.proposed_variant_code is None


def test_empty_and_header_only_inputs_fail_clearly(tmp_path: Path):
    """
    8. Empty and header-only inputs fail clearly with explicit error (no false success).
    """
    # Empty CSV
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("", encoding="utf-8")

    cfg_empty = PipelineConfig(
        input_path=empty_csv,
        output_dir=tmp_path / "out_empty",
    )
    res_empty = PipelineOrchestrator.run(cfg_empty)
    assert res_empty.success is False
    assert any(msg in res_empty.error_message.lower() for msg in ["no data rows", "no columns", "empty"])

    # Header-only CSV
    header_only_csv = tmp_path / "header_only.csv"
    header_only_csv.write_text("Product Name,Catalog Name,Price,SKU\n", encoding="utf-8")

    cfg_header = PipelineConfig(
        input_path=header_only_csv,
        output_dir=tmp_path / "out_header",
    )
    res_header = PipelineOrchestrator.run(cfg_header)
    assert res_header.success is False
    assert "no data rows" in res_header.error_message.lower()


def test_existing_xlsx_and_csv_workflows_continue_working(tmp_path: Path):
    """
    9. Existing tabular XLSX and CSV workflows continue to work without regression.
    """
    csv_file = tmp_path / "standard.csv"
    csv_file.write_text(
        "Product Name,SKU,Variant Name,Cost,Price,Stock\n"
        "Cotton Kurti,CK-001,Size S,350,799,20\n"
        "Cotton Kurti,CK-002,Size M,350,799,25\n",
        encoding="utf-8",
    )

    cfg = PipelineConfig(
        input_path=csv_file,
        output_dir=tmp_path / "out_standard",
        export_formats=["json", "xlsx"],
    )

    result = PipelineOrchestrator.run(cfg)
    assert result.success is True
    assert len(result.products) == 1
    prod = result.products[0]
    assert prod.product_name == "Cotton Kurti"
    assert len(prod.variants) == 2
    assert prod.variants[0].supplier_sku == "CK-001"
    assert prod.variants[0].selling_price == 799.0


def test_rajtex_ajrakh_vol58_realistic_catalogue_ocr(tmp_path: Path, register_mock_ocr):
    """
    10. Verify realistic OCR text from Rajtex Ajrakh Vol-58 slip is accurately extracted,
    produces 1 product, exactly 6 variants, preserves all category attributes, separates wholesale
    and single-piece prices, and flags missing selling prices/skus for review.
    """
    catalogue_img = tmp_path / "rajtex_ajrakh_vol-58.jpeg"
    catalogue_img.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 100)

    ocr_text = (
        "Catalogue Name - Rajtex Ajrakh Vol-58\n"
        "Brand Name - Rajtex\n"
        "Product Type - Saree\n"
        "Stitch Type - Unstitched\n"
        "Saree Fabric - Modal Satin\n"
        "Blouse Fabric - Modal Satin\n"
        "Work - Ajrakh Print\n"
        "Size - Free Size\n"
        "Total Designs - 6\n"
        "Minimum Order Quantity - 1 Pc (Single Available)\n"
        "Wholesale Per Unit Price - % 580 (Per Piece Wholesale)\n"
        "Full Catalogue - = 3480 (6 Pcs)\n"
        "Single Piece - ¥ 680 (Single Piece Only)\n"
        "GST - 5% Extra\n"
        "Approx Weight - 0.6 KG per pc (3.6 KG total\n"
        "catalogue)\n"
    )
    register_mock_ocr("test_rajtex_mock", ocr_text)

    cfg = PipelineConfig(
        input_path=catalogue_img,
        output_dir=tmp_path / "out_rajtex",
        ocr_provider="test_rajtex_mock",
        supplier="generic",
        export_formats=["json"],
    )

    result = PipelineOrchestrator.run(cfg)
    assert result.success is True
    assert len(result.products) == 1

    prod = result.products[0]
    assert prod.catalog_name == "Rajtex Ajrakh Vol-58"
    assert prod.brand_name == "Rajtex"
    assert prod.product_type == "Saree"
    assert prod.supplier_price == 680.0
    assert prod.gst_percent == 5.0
    assert prod.moq == 1

    # Check preserved attributes
    attrs = prod.attributes
    assert attrs.get("stitch_type") == "Unstitched"
    assert attrs.get("saree_fabric") == "Modal Satin"
    assert attrs.get("blouse_fabric") == "Modal Satin"
    assert attrs.get("work") == "Ajrakh Print"
    assert attrs.get("size") == "Free Size"
    assert attrs.get("wholesale_price") == 580.0
    assert attrs.get("single_piece_price") == 680.0
    assert attrs.get("full_catalogue_price") == 3480.0
    assert attrs.get("approx_weight") == "0.6 KG per pc (3.6 KG total catalogue)"

    # Exactly 6 draft variants generated
    assert len(prod.variants) == 6
    expected_codes = [f"RAJTEX-AJRAKH-VOL-58-V{i:02d}" for i in range(1, 7)]
    assert [v.variant_code for v in prod.variants] == expected_codes
    assert [v.variant_name for v in prod.variants] == [f"Design {i:02d}" for i in range(1, 7)]

    for v in prod.variants:
        assert v.supplier_cost == 680.0
        assert v.selling_price is None  # Not invented
        assert v.supplier_sku is None  # Not invented
        assert v.color is None  # Pending review
        assert v.size == "Free Size"


def test_failed_extraction_reports_validation_status_failed(tmp_path: Path):
    """
    11. Verify that when extraction fails, validation status reports FAILED,
    not misleadingly PASSED.
    """
    empty_img = tmp_path / "empty_catalog.jpg"
    empty_img.write_text("not an image")

    cfg = PipelineConfig(
        input_path=empty_img,
        output_dir=tmp_path / "out_failed",
    )

    result = PipelineOrchestrator.run(cfg)
    assert result.success is False
    assert result.metrics.is_valid is False
    summary_text = result.format_summary_text()
    assert "MANA STORE PIPELINE EXECUTION: FAILED" in summary_text
    assert "Validation Status:  FAILED" in summary_text
    assert "Validation Status:  PASSED" not in summary_text


def test_multiline_ocr_continuation_and_price_distinction(tmp_path: Path, register_mock_ocr):
    """
    12. Regression test:
    - Wrapped approximate weight text is retained completely without truncation.
    - Full-catalogue price 3480 is preserved distinctly from wholesale price 580 and single piece price 680.
    - Distinct pricing fields do not overwrite supplier_price, single_piece_price or selling_price.
    """
    catalogue_doc = tmp_path / "test_multiline_prices.png"
    catalogue_doc.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    ocr_text = (
        "Catalogue Name - Zoya Festive Silk\n"
        "Brand Name - Zoya\n"
        "Product Type - Saree\n"
        "Wholesale Per Unit Price - Rs 580\n"
        "Full Catalogue - Rs 3480 (6 Pcs)\n"
        "Single Piece - Rs 680\n"
        "Total Designs - 6\n"
        "Approx Weight - 0.6 KG per pc (3.6 KG total\n"
        "catalogue)\n"
    )
    register_mock_ocr("test_multiline_pricing_mock", ocr_text)

    cfg = PipelineConfig(
        input_path=catalogue_doc,
        output_dir=tmp_path / "out_multiline_prices",
        ocr_provider="test_multiline_pricing_mock",
        supplier="generic",
        export_formats=["json"],
    )

    result = PipelineOrchestrator.run(cfg)
    assert result.success is True
    assert len(result.products) == 1

    prod = result.products[0]
    # Single piece price mapped to supplier_price
    assert prod.supplier_price == 680.0
    # Wholesale and single piece prices preserved distinctly
    assert prod.attributes.get("wholesale_price") == 580.0
    assert prod.attributes.get("single_piece_price") == 680.0
    # Full catalogue price preserved distinctly
    assert prod.attributes.get("full_catalogue_price") == 3480.0
    # Selling price never invented
    for v in prod.variants:
        assert v.supplier_cost == 680.0
        assert v.selling_price is None

    # Multiline continuation preserved
    assert prod.attributes.get("approx_weight") == "0.6 KG per pc (3.6 KG total catalogue)"
    assert prod.raw_attributes.get("approx_weight") == "0.6 KG per pc (3.6 KG total catalogue)"


def test_unrecognized_source_fields_and_category_attrs_survive_excel_export(tmp_path: Path, register_mock_ocr):
    """
    13. Regression test:
    - Extracted unrecognized source fields and category-specific attributes survive
      into canonical products and Excel/CSV/JSON export rows without silent data loss.
    """
    catalogue_doc = tmp_path / "test_unrecognized_export.png"
    catalogue_doc.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    ocr_text = (
        "Catalogue Name - Kanjivaram Royal Edition\n"
        "Brand Name - Royal Heritage\n"
        "Product Type - Saree\n"
        "Wholesale Per Unit Price - 1500\n"
        "Total Designs - 2\n"
        "Zari Type - Pure Gold Zari\n"
        "Weaving Technique - Korvai Handloom\n"
        "Custom Supplier Tag - EXP-2026-VIP\n"
    )
    register_mock_ocr("test_unrecognized_mock", ocr_text)

    cfg = PipelineConfig(
        input_path=catalogue_doc,
        output_dir=tmp_path / "out_unrecognized_export",
        ocr_provider="test_unrecognized_mock",
        supplier="generic",
        export_formats=["xlsx", "csv", "json"],
    )

    result = PipelineOrchestrator.run(cfg)
    assert result.success is True
    assert len(result.products) == 1

    prod = result.products[0]
    # Verify presence in Product models
    assert prod.attributes.get("zari_type") == "Pure Gold Zari"
    assert prod.attributes.get("weaving_technique") == "Korvai Handloom"
    assert prod.attributes.get("custom_supplier_tag") == "EXP-2026-VIP"

    # Verify survival in canonical export flat records (used for Excel and CSV)
    from src.exporters.canonical import CanonicalExport
    export_data = CanonicalExport.from_products(result.products)
    flat_rows = export_data.to_flat_records()
    assert len(flat_rows) == 2  # 2 variants

    for row in flat_rows:
        assert row.get("zari_type") == "Pure Gold Zari"
        assert row.get("weaving_technique") == "Korvai Handloom"
        assert row.get("custom_supplier_tag") == "EXP-2026-VIP"

    # Verify physical XLSX output has these columns in _internal/
    import pandas as pd
    xlsx_path = tmp_path / "out_unrecognized_export" / "_internal" / "canonical_products.xlsx"
    assert xlsx_path.exists()
    df = pd.read_excel(xlsx_path, sheet_name="Products")
    assert "zari_type" in df.columns
    assert "weaving_technique" in df.columns
    assert "custom_supplier_tag" in df.columns
    assert df["zari_type"].iloc[0] == "Pure Gold Zari"
    assert df["weaving_technique"].iloc[0] == "Korvai Handloom"
    assert df["custom_supplier_tag"].iloc[0] == "EXP-2026-VIP"

