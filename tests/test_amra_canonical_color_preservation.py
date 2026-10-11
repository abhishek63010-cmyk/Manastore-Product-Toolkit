from pathlib import Path
import tempfile
import numpy as np
from PIL import Image
import openpyxl
import pandas as pd
import pytest

from src.adapters.amra import AmraAdapter
from src.enrichment.service import ProductEnrichmentService
from src.exporters.canonical import CanonicalExport
from src.exporters.writers import OutputWriter
from src.extractors.tabular import TabularExtractor
from src.images.color import ImageColorAnalyzer
from src.images.manifest import ApprovalStatus, ImageApprovalManifest, ImageManifestEntry
from src.images.matcher import MatchStatus
from src.models.product import Product
from src.models.variant import Variant


def _create_solid_image(path: Path, rgb: tuple[int, int, int], size: tuple[int, int] = (150, 150)) -> Path:
    """Helper to synthesize solid color image."""
    img = Image.new("RGB", size, color=rgb)
    img.save(path, format="JPEG")
    return path


def test_amra_adapter_path_unapproved_canonical_color_and_sku_empty(tmp_path: Path):
    """
    Requirement 6.1: Supplier variant name contains a colour (e.g. 'Emarald Green'),
    but no approved image is provided.
    - Raw supplier variant_name and original_color preserved.
    - canonical color and image_sku in canonical_products.xlsx and refined_products.xlsx are None / empty.
    - original_color in refined_products.xlsx reflects the original supplier value ('Emarald Green').
    """
    fixture_path = tmp_path / "amra_unapproved.xlsx"
    df = pd.DataFrame([
        {
            "Supplier Name": "Amra Wholesale",
            "Catalogue Name": "Mahotsav Kriti",
            "Brand Name": "Mahotsav",
            "Product Group": "Mahots-K",
            "Supplier SKU": "AM-17090",
            "Supplier Price": "₹ 1,500",
            "GST": "5%",
            "Variant Name": "Emarald Green",
            "Fabric": "Soft Chinon Silk",
            "Blouse Fabric": "Soft Chinon Silk",
        },
        {
            "Supplier Name": "Amra Wholesale",
            "Catalogue Name": "Mahotsav Kriti",
            "Brand Name": "Mahotsav",
            "Product Group": "Mahots-K",
            "Supplier SKU": "AM-17090",
            "Supplier Price": "₹ 1,500",
            "GST": "5%",
            "Variant Name": "Wine Red",
            "Fabric": "Soft Chinon Silk",
            "Blouse Fabric": "Soft Chinon Silk",
        },
    ])
    df.to_excel(fixture_path, index=False)

    raw_data = TabularExtractor.extract(fixture_path)
    adapter = AmraAdapter()
    assert adapter.can_handle(raw_data) is True

    products = adapter.transform(raw_data)
    assert len(products) == 1
    prod = products[0]
    assert len(prod.variants) == 2

    v1 = prod.variants[0]
    v2 = prod.variants[1]
    assert v1.variant_name == "Emarald Green"
    assert v2.variant_name == "Wine Red"

    # Run enrichment WITHOUT approved images (approval_manifest=None)
    refined = ProductEnrichmentService.enrich(products=[prod], approval_manifest=None)
    canonical = CanonicalExport.from_products(products=[prod])

    # Write both workbooks
    p_canonical = OutputWriter.write_canonical_xlsx(canonical, tmp_path / "canonical.xlsx")
    p_refined = OutputWriter.write_refined_xlsx(refined, tmp_path / "refined.xlsx")

    df_canon = pd.read_excel(p_canonical, sheet_name="Products")
    df_ref = pd.read_excel(p_refined, sheet_name="Refined_Variants")

    # In domain variant instances:
    assert v1.color is None
    assert v1.image_sku is None
    assert v2.color is None
    assert v2.image_sku is None

    # In canonical workbook:
    assert pd.isna(df_canon["color"].iloc[0]) or df_canon["color"].iloc[0] == ""
    assert pd.isna(df_canon["image_sku"].iloc[0]) or df_canon["image_sku"].iloc[0] == ""
    assert pd.isna(df_canon["color"].iloc[1]) or df_canon["color"].iloc[1] == ""
    assert pd.isna(df_canon["image_sku"].iloc[1]) or df_canon["image_sku"].iloc[1] == ""

    # In refined workbook:
    assert pd.isna(df_ref["color"].iloc[0]) or df_ref["color"].iloc[0] == ""
    assert pd.isna(df_ref["image_sku"].iloc[0]) or df_ref["image_sku"].iloc[0] == ""
    assert pd.isna(df_ref["color"].iloc[1]) or df_ref["color"].iloc[1] == ""
    assert pd.isna(df_ref["image_sku"].iloc[1]) or df_ref["image_sku"].iloc[1] == ""

    # original_color in refined workbook strictly preserves source supplier values:
    assert df_ref["original_color"].iloc[0] == "Emarald Green"
    assert df_ref["original_color"].iloc[1] == "Wine Red"

    # variant_name remains unchanged:
    assert df_canon["variant_name"].iloc[0] == "Emarald Green"
    assert df_canon["variant_name"].iloc[1] == "Wine Red"
    assert df_ref["variant_name"].iloc[0] == "Emarald Green"
    assert df_ref["variant_name"].iloc[1] == "Wine Red"


def test_amra_adapter_path_approved_image_different_color_and_discrepancy(tmp_path: Path):
    """
    Requirement 6.2: Approved image produces a different colour from the supplier value.
    - Amra supplier variant name is 'Emarald Green'.
    - Approved image analyzed produces 'Red'.
    - Canonical color in both workbooks becomes 'Red'.
    - original_color in refined workbook remains 'Emarald Green'.
    - Discrepancy flag and diagnostic review reason are recorded.
    """
    fixture_path = tmp_path / "amra_discrepancy.xlsx"
    df = pd.DataFrame([
        {
            "Supplier Name": "Amra Wholesale",
            "Catalogue Name": "Mahotsav Kriti",
            "Brand Name": "Mahotsav",
            "Product Group": "Mahots-K",
            "Supplier SKU": "AM-17090",
            "Supplier Price": "₹ 1,500",
            "GST": "5%",
            "Variant Name": "Emarald Green",
            "Fabric": "Soft Chinon Silk",
            "Blouse Fabric": "Soft Chinon Silk",
        }
    ])
    df.to_excel(fixture_path, index=False)

    raw_data = TabularExtractor.extract(fixture_path)
    adapter = AmraAdapter()
    assert adapter.can_handle(raw_data) is True

    products = adapter.transform(raw_data)
    prod = products[0]
    v1 = prod.variants[0]

    # Create solid RED test image
    img_red_path = _create_solid_image(tmp_path / "discrepant_red.jpg", (220, 20, 30))

    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_disc_01",
                image_path=str(img_red_path),
                original_filename="discrepant_red.jpg",
                proposed_variant_code=v1.variant_code,
                image_sku="AM-17090-IMG01",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_disc_01", v1.variant_code),
            )
        ]
    )

    refined = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)
    canonical = CanonicalExport.from_products(products=[prod])

    rec = refined.records[0]

    # Domain variant state:
    assert v1.color == "Red"
    assert v1.image_sku == "AM-17090-IMG01"
    assert v1.variant_name == "Emarald Green"

    # Refined record state:
    assert rec.color == "Red"
    assert rec.original_color == "Emarald Green"
    assert rec.image_sku == "AM-17090-IMG01"
    assert rec.variant_name == "Emarald Green"
    assert rec.requires_review is True
    assert any("Image color discrepancy" in reason for reason in rec.review_reasons)
    assert any("suggest 'Red' vs supplier 'Emarald Green'" in reason for reason in rec.review_reasons)

    # Workbooks agreement:
    p_canonical = OutputWriter.write_canonical_xlsx(canonical, tmp_path / "canonical.xlsx")
    p_refined = OutputWriter.write_refined_xlsx(refined, tmp_path / "refined.xlsx")

    df_canon = pd.read_excel(p_canonical, sheet_name="Products")
    df_ref = pd.read_excel(p_refined, sheet_name="Refined_Variants")

    assert df_canon["color"].iloc[0] == "Red"
    assert df_ref["color"].iloc[0] == "Red"
    assert df_canon["image_sku"].iloc[0] == "AM-17090-IMG01"
    assert df_ref["image_sku"].iloc[0] == "AM-17090-IMG01"
    assert df_ref["original_color"].iloc[0] == "Emarald Green"


def test_amra_adapter_path_multi_variant_isolation_and_preservation(tmp_path: Path):
    """
    Requirement 6.3: Multi-variant catalogue where only one variant has an approved image.
    - Other variants remain completely untouched (canonical color and image_sku empty).
    - Original source data preserved across all variants.
    """
    fixture_path = tmp_path / "amra_multivar.xlsx"
    df = pd.DataFrame([
        {
            "Supplier Name": "Amra Wholesale",
            "Catalogue Name": "SETHNIC VANTASA",
            "Brand Name": "Sethnic",
            "Product Group": "SETHNIC-VAN",
            "Supplier SKU": "AM-99001",
            "Supplier Price": "₹ 1,200",
            "GST": "5%",
            "Variant Name": "Navy Blue",
            "Fabric": "Georgette",
            "Blouse Fabric": "Georgette",
        },
        {
            "Supplier Name": "Amra Wholesale",
            "Catalogue Name": "SETHNIC VANTASA",
            "Brand Name": "Sethnic",
            "Product Group": "SETHNIC-VAN",
            "Supplier SKU": "AM-99001",
            "Supplier Price": "₹ 1,200",
            "GST": "5%",
            "Variant Name": "Mustard Yellow",
            "Fabric": "Georgette",
            "Blouse Fabric": "Georgette",
        },
        {
            "Supplier Name": "Amra Wholesale",
            "Catalogue Name": "SETHNIC VANTASA",
            "Brand Name": "Sethnic",
            "Product Group": "SETHNIC-VAN",
            "Supplier SKU": "AM-99001",
            "Supplier Price": "₹ 1,200",
            "GST": "5%",
            "Variant Name": "Maroon",
            "Fabric": "Georgette",
            "Blouse Fabric": "Georgette",
        },
    ])
    df.to_excel(fixture_path, index=False)

    raw_data = TabularExtractor.extract(fixture_path)
    adapter = AmraAdapter()
    assert adapter.can_handle(raw_data) is True

    products = adapter.transform(raw_data)
    prod = products[0]
    assert len(prod.variants) == 3

    v1, v2, v3 = prod.variants

    # Create solid YELLOW image ONLY for v2 (Mustard Yellow)
    img_yellow_path = _create_solid_image(tmp_path / "yellow.jpg", (240, 220, 40))

    manifest = ImageApprovalManifest(
        entries=[
            ImageManifestEntry(
                image_id="img_y02",
                image_path=str(img_yellow_path),
                original_filename="yellow.jpg",
                proposed_variant_code=v2.variant_code,
                image_sku="AM-99001-YEL",
                match_status=MatchStatus.MATCHED,
                approval_status=ApprovalStatus.APPROVED,
                assignment_hash=ImageManifestEntry.compute_assignment_hash("img_y02", v2.variant_code),
            )
        ]
    )

    refined = ProductEnrichmentService.enrich(products=[prod], approval_manifest=manifest)
    canonical = CanonicalExport.from_products(products=[prod])

    # v1 (Navy Blue): unapproved -> empty canonical fields, original preserved
    assert v1.color is None
    assert v1.image_sku is None
    assert v1.variant_name == "Navy Blue"

    # v2 (Mustard Yellow): approved -> enriched canonical fields, original preserved
    assert v2.color in ("Yellow", "Mustard")
    assert v2.image_sku == "AM-99001-YEL"
    assert v2.variant_name == "Mustard Yellow"

    # v3 (Maroon): unapproved -> empty canonical fields, original preserved
    assert v3.color is None
    assert v3.image_sku is None
    assert v3.variant_name == "Maroon"

    # Refined records
    recs = {r.variant_code: r for r in refined.records}
    assert recs[v1.variant_code].original_color == "Navy Blue"
    assert recs[v1.variant_code].color is None
    assert recs[v1.variant_code].image_sku is None

    assert recs[v2.variant_code].original_color == "Mustard Yellow"
    assert recs[v2.variant_code].color in ("Yellow", "Mustard")
    assert recs[v2.variant_code].image_sku == "AM-99001-YEL"

    assert recs[v3.variant_code].original_color == "Maroon"
    assert recs[v3.variant_code].color is None
    assert recs[v3.variant_code].image_sku is None
