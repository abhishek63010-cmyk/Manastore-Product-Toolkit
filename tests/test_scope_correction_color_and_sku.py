from pathlib import Path
import tempfile
import numpy as np
from PIL import Image
import openpyxl
import pandas as pd
import pytest

from src.enrichment.service import ProductEnrichmentService
from src.exporters.writers import OutputWriter
from src.images.color import ImageColorAnalyzer, ImageColorResult
from src.images.inventory import GenericImageInventory
from src.images.manifest import ApprovalStatus, ImageApprovalManifest, ImageManifestEntry
from src.images.matcher import MatchStatus
from src.models.product import Product
from src.models.variant import Variant
from src.pipeline.config import PipelineConfig
from src.pipeline.orchestrator import PipelineOrchestrator


def _create_test_image(path: Path, rgb: tuple[int, int, int], size: tuple[int, int] = (200, 200)) -> Path:
    """Helper to synthesize test solid color image."""
    img = Image.new("RGB", size, color=rgb)
    img.save(path, format="JPEG")
    return path


def _create_multi_tonal_image(path: Path, rgb1: tuple[int, int, int], rgb2: tuple[int, int, int], size: tuple[int, int] = (200, 200)) -> Path:
    """Helper to synthesize 50/50 split multi-tonal image."""
    img = Image.new("RGB", size)
    pixels = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    half = size[0] // 2
    pixels[:, :half] = rgb1
    pixels[:, half:] = rgb2
    img = Image.fromarray(pixels, "RGB")
    img.save(path, format="JPEG")
    return path


def test_color_analyzer_pure_colors(tmp_path: Path):
    """Verify ImageColorAnalyzer correctly classifies standard single colors."""
    # Red
    red_path = _create_test_image(tmp_path / "red.jpg", (220, 20, 30))
    res_red = ImageColorAnalyzer.analyze_image(red_path)
    assert res_red.status == "EXTRACTED"
    assert res_red.canonical_color == "Red"
    assert not res_red.is_ambiguous
    assert res_red.confidence >= 0.5

    # Green
    green_path = _create_test_image(tmp_path / "green.jpg", (30, 180, 50))
    res_green = ImageColorAnalyzer.analyze_image(green_path)
    assert res_green.status == "EXTRACTED"
    assert res_green.canonical_color == "Green"

    # Blue
    blue_path = _create_test_image(tmp_path / "blue.jpg", (30, 80, 220))
    res_blue = ImageColorAnalyzer.analyze_image(blue_path)
    assert res_blue.status == "EXTRACTED"
    assert res_blue.canonical_color in ("Blue", "Navy Blue")

    # Yellow
    yellow_path = _create_test_image(tmp_path / "yellow.jpg", (240, 220, 40))
    res_yellow = ImageColorAnalyzer.analyze_image(yellow_path)
    assert res_yellow.status == "EXTRACTED"
    assert res_yellow.canonical_color in ("Yellow", "Mustard")


def test_color_analyzer_neutral_tones(tmp_path: Path):
    """Verify neutrals: Black, White, Grey."""
    white_path = _create_test_image(tmp_path / "white.jpg", (240, 240, 240))
    res_white = ImageColorAnalyzer.analyze_image(white_path)
    assert res_white.canonical_color == "White"

    black_path = _create_test_image(tmp_path / "black.jpg", (35, 35, 35))
    res_black = ImageColorAnalyzer.analyze_image(black_path)
    assert res_black.canonical_color == "Black"

    grey_path = _create_test_image(tmp_path / "grey.jpg", (130, 130, 130))
    res_grey = ImageColorAnalyzer.analyze_image(grey_path)
    assert res_grey.canonical_color == "Grey"


def test_color_analyzer_multi_tonal_ambiguity(tmp_path: Path):
    """Section 8.4: Mixed/distracting foreground marked UNCERTAIN & leaves canonical_color blank."""
    split_path = _create_multi_tonal_image(tmp_path / "split.jpg", (220, 20, 30), (30, 80, 220))
    res_split = ImageColorAnalyzer.analyze_image(split_path)
    assert res_split.status == "UNCERTAIN"
    assert res_split.is_ambiguous is True
    assert res_split.canonical_color is None
    assert "Multi-tonal" in (res_split.notes or "")


def test_canonical_fields_empty_without_approved_images():
    """Section 8.1: Before approved images are provided, canonical color and image_sku remain empty."""
    prod = Product(
        supplier_name="Test Supplier",
        catalog_name="Cat-A",
        product_name="Product Alpha",
        supplier_price=500.0,
    )
    v1 = Variant(
        variant_code="CAT-A-V01",
        variant_name="Option 1",
        supplier_sku="SKU-100",
        color=None,
        image_sku=None,
    )
    prod.add_variant(v1)

    # Enrich without manifest
    refined = ProductEnrichmentService.enrich(products=[prod], approval_manifest=None)
    record = refined.records[0]

    assert record.has_approved_image is False
    assert record.color is None
    assert record.image_sku is None
    assert record.image_color_status == "NOT_ANALYZED"
    assert v1.color is None
    assert v1.image_sku is None


def test_single_clear_color_approved_image_enrichment(tmp_path: Path):
    """
    Section 8.2: Approved image with clear single color populates:
    - variant.color
    - variant.image_sku
    - RefinedVariantRecord fields
    - Review status resolved for color & image_sku
    """
    prod = Product(
        supplier_name="Test Supplier",
        catalog_name="Cat-B",
        product_name="Product Beta",
        supplier_price=600.0,
    )
    v1 = Variant(
        variant_code="CAT-B-V01",
        variant_name="Option 1",
        supplier_sku="SKU-200",
        selling_price=1200.0,
    )
    prod.add_variant(v1)

    img_file = _create_test_image(tmp_path / "img_red_01.jpg", (220, 20, 30))

    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_b01",
                image_path=str(img_file),
                original_filename="img_red_01.jpg",
                proposed_variant_code="CAT-B-V01",
                image_sku="SKU-200-RED",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_b01", "CAT-B-V01"),
            )
        ]
    )

    refined = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)
    rec = refined.records[0]

    # Target variant populated
    assert v1.color == "Red"
    assert v1.image_sku == "SKU-200-RED"

    # Refined record populated
    assert rec.has_approved_image is True
    assert rec.color == "Red"
    assert rec.image_sku == "SKU-200-RED"
    assert rec.image_color_status == "EXTRACTED"
    assert rec.image_color_confidence >= 0.5
    assert rec.image_color_hex is not None


def test_multi_variant_catalogue_variant_isolation(tmp_path: Path):
    """
    Section 8.3: Catalogue with multiple variants assigns image strictly to matching variant row.
    Other variants remain untouched.
    """
    prod = Product(
        supplier_name="Test Supplier",
        catalog_name="Cat-C",
        product_name="Multi Variant Saree",
        supplier_price=700.0,
    )
    v1 = Variant(variant_code="CAT-C-V01", variant_name="Design 1", supplier_sku="C-100")
    v2 = Variant(variant_code="CAT-C-V02", variant_name="Design 2", supplier_sku="C-100")
    v3 = Variant(variant_code="CAT-C-V03", variant_name="Design 3", supplier_sku="C-100")
    prod.add_variant(v1)
    prod.add_variant(v2)
    prod.add_variant(v3)

    img_green = _create_test_image(tmp_path / "green_02.jpg", (30, 180, 50))

    # Approve image ONLY for V02
    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_c02",
                image_path=str(img_green),
                original_filename="green_02.jpg",
                proposed_variant_code="CAT-C-V02",
                image_sku="C-100-GRN",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_c02", "CAT-C-V02"),
            )
        ]
    )

    refined = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)
    recs = {r.variant_code: r for r in refined.records}

    # V01 and V03 remain unmutated and empty
    assert v1.color is None
    assert v1.image_sku is None
    assert recs["CAT-C-V01"].color is None
    assert recs["CAT-C-V01"].image_sku is None

    assert v3.color is None
    assert v3.image_sku is None
    assert recs["CAT-C-V03"].color is None
    assert recs["CAT-C-V03"].image_sku is None

    # Only V02 is enriched
    assert v2.color == "Green"
    assert v2.image_sku == "C-100-GRN"
    assert recs["CAT-C-V02"].color == "Green"
    assert recs["CAT-C-V02"].image_sku == "C-100-GRN"


def test_invalid_or_missing_sku_on_approved_image(tmp_path: Path):
    """
    Section 8.5: Approved image without identifiable SKU leaves image_sku blank,
    flags review_required, records diagnostic reason. Never fabricates.
    """
    prod = Product(
        supplier_name="Test Supplier",
        catalog_name="Cat-D",
        product_name="Product Delta",
        supplier_price=500.0,
    )
    v1 = Variant(variant_code="CAT-D-V01", variant_name="Design 1", supplier_sku="D-500")
    prod.add_variant(v1)

    img_blue = _create_test_image(tmp_path / "blue_no_sku.jpg", (30, 80, 220))

    # Approved image with image_sku = None (no OCR candidate, no manifest entry image_sku)
    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_d01",
                image_path=str(img_blue),
                original_filename="blue_no_sku.jpg",
                proposed_variant_code="CAT-D-V01",
                image_sku=None,
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_d01", "CAT-D-V01"),
            )
        ]
    )

    refined = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)
    rec = refined.records[0]

    assert rec.image_sku is None
    assert v1.image_sku is None
    # Color still extracts cleanly from pixels
    assert rec.color in ("Blue", "Navy Blue")
    assert v1.color in ("Blue", "Navy Blue")
    assert rec.requires_review is True
    assert any("Image SKU missing" in reason for reason in rec.review_reasons)


def test_pipeline_repeat_run_idempotency(tmp_path: Path):
    """
    Section 8.6: Running the pipeline twice with identical inputs produces byte-identical
    or value-identical workbook outputs without state accumulation or duplicate rows.
    """
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Catalog Repeat",
        product_name="Saree Repeat",
        supplier_price=650.0,
    )
    v1 = Variant(variant_code="REP-V01", variant_name="Design 1", supplier_sku="REP-100", selling_price=999.0)
    prod.add_variant(v1)

    img_red = _create_test_image(tmp_path / "repeat_red.jpg", (220, 20, 30))
    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_rep",
                image_path=str(img_red),
                original_filename="repeat_red.jpg",
                proposed_variant_code="REP-V01",
                image_sku="REP-100-A",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_rep", "REP-V01"),
            )
        ]
    )

    # Run 1
    ref1 = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)
    p_ref1 = OutputWriter.write_refined_xlsx(ref1, tmp_path / "run1" / "refined.xlsx")
    df1 = pd.read_excel(p_ref1, sheet_name="Refined_Variants")

    # Run 2
    ref2 = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)
    p_ref2 = OutputWriter.write_refined_xlsx(ref2, tmp_path / "run2" / "refined.xlsx")
    df2 = pd.read_excel(p_ref2, sheet_name="Refined_Variants")

    assert len(df1) == len(df2) == 1
    assert df1["color"].iloc[0] == df2["color"].iloc[0] == "Red"
    assert df1["image_sku"].iloc[0] == df2["image_sku"].iloc[0] == "REP-100-A"


def test_url_columns_remain_empty_when_no_blob_upload(tmp_path: Path):
    """
    Section 8.7: main_image_url and gallery image columns remain completely empty
    when Blob uploads are not performed.
    """
    prod = Product(
        supplier_name="Supplier E",
        catalog_name="Cat-E",
        product_name="Product Epsilon",
        supplier_price=550.0,
    )
    v1 = Variant(variant_code="E-V01", variant_name="Design 1", supplier_sku="E-100", selling_price=899.0)
    prod.add_variant(v1)

    img_path = _create_test_image(tmp_path / "e_red.jpg", (220, 20, 30))
    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_e",
                image_path=str(img_path),
                original_filename="e_red.jpg",
                proposed_variant_code="E-V01",
                image_sku="E-100-RED",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_e", "E-V01"),
            )
        ]
    )

    ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)
    out_xlsx = OutputWriter.write_mana_store_import_xlsx([prod], tmp_path / "mana_import.xlsx")

    df = pd.read_excel(out_xlsx, sheet_name="Import Ready")
    assert df["main_image_url"].iloc[0] == "" or pd.isna(df["main_image_url"].iloc[0])
    for i in range(1, 6):
        col = f"image_{i}_url"
        assert df[col].iloc[0] == "" or pd.isna(df[col].iloc[0])
