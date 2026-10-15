"""
Regression test suite for Price Extraction Quality, Single Source of Truth Canonical Data,
Deterministic Sequence Matching, and Standalone Canonical Dataset Combiner.
"""

from pathlib import Path
import pytest

from src.ocr.pricing import EvidenceBasedPriceNormalizer, PriceCandidate
from src.images.matcher import DeterministicImageMatcher, MatchStatus
from src.images.inventory import ImageInventory
from src.models.product import Product
from src.models.variant import Variant
from src.models.enums import ReviewStatus
from src.pipeline.canonical_merger import CanonicalDatasetMerger, CanonicalMergeConflictError
from src.exporters.canonical import CanonicalExport, CanonicalProduct, CanonicalVariant
from src.mappers.amra_transformer import AmraWorkbookTransformer


# -------------------------------------------------------------------------
# 1. Price Extraction & Normalization Tests
# -------------------------------------------------------------------------

def test_price_normalizer_reconciles_rupee_misread_via_formula():
    """
    Test that '₹' misread as '2' in '21415' and '28490' is corrected
    using count formula 6 Pcs x 1415 = 8490 and alternative text mention #1515.
    """
    ocr_text = (
        "Wholesale Per Unit Price - 21415 (Per Piece Wholesale)\n"
        "Full Catalogue - 28490 (6 Pcs)\n"
        "Single Piece - #1515 (Single Piece Only)\n"
        "6 Pes x 21415 = #8490"
    )
    candidates = EvidenceBasedPriceNormalizer.extract_price_candidates(ocr_text)
    assert len(candidates) >= 4

    norm = EvidenceBasedPriceNormalizer.reconcile_and_normalize_prices(
        candidates=candidates,
        total_designs=6,
        raw_text=ocr_text,
    )

    assert norm["single_piece_price"] == 1515.0
    assert norm["wholesale_price"] == 1415.0
    assert norm["full_catalogue_price"] == 8490.0
    assert norm["supplier_price"] == 1515.0
    assert norm["price_notes"] is not None


def test_price_normalizer_preserves_legitimate_prices_starting_with_two():
    """
    Ensure legitimate prices starting with 2 (e.g. ₹2500, ₹2100) are NEVER stripped
    when there is no multi-evidence breakdown indicating a misread rupee symbol.
    """
    ocr_text = (
        "Brand: Kanchipuram\n"
        "Price: Rs. 2500 /-\n"
        "Single Piece: 2500\n"
        "MOQ: 1 Pc"
    )
    candidates = EvidenceBasedPriceNormalizer.extract_price_candidates(ocr_text)
    norm = EvidenceBasedPriceNormalizer.reconcile_and_normalize_prices(
        candidates=candidates,
        total_designs=1,
        raw_text=ocr_text,
    )

    assert norm["single_piece_price"] == 2500.0
    assert norm["supplier_price"] == 2500.0


def test_price_normalizer_handles_indian_currency_symbols():
    """Test extracting prices prefixed by ₹, INR, Rs., #, and @."""
    text = "Rate: ₹1,299 | Single: Rs. 1450.00 | Full: #7,794 (6 Pcs)"
    candidates = EvidenceBasedPriceNormalizer.extract_price_candidates(text)
    values = {c.candidate_value for c in candidates}
    assert 1299.0 in values
    assert 1450.0 in values
    assert 7794.0 in values


# -------------------------------------------------------------------------
# 2. Sequence Index Matching Tests
# -------------------------------------------------------------------------

def test_deterministic_matcher_sequence_indexing(tmp_path: Path):
    """
    Test that image filenames ending in sequence numbers (e.g. Rajtex-N-001 .. 006)
    match placeholder variants (Design 01 .. Design 06 / V01 .. V06) with confidence 0.90.
    """
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    (img_dir / "Rajtex-N-001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (img_dir / "Rajtex-N-002.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    inv = ImageInventory.discover_from_directory(img_dir)

    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="RAJTEX NOORANI",
        product_name="Rajtex Noorani Saree",
        product_group="RAJTEX-N",
        brand_name="RAJTEX",
        product_type="Saree",
    )
    v1 = Variant(variant_code="RAJTEX-NOORANI-V01", variant_name="Design 01")
    v2 = Variant(variant_code="RAJTEX-NOORANI-V02", variant_name="Design 02")
    prod.add_variant(v1)
    prod.add_variant(v2)

    report = DeterministicImageMatcher.match(
        products=[prod],
        inventory=inv,
        attach_to_variants=True,
    )

    assert report.matched_count == 2
    assert report.ambiguous_count == 0
    assert v1.image_sku == "rajtex-n-001"
    assert v2.image_sku == "rajtex-n-002"
    assert len(v1.images) == 1
    assert len(v2.images) == 1


# -------------------------------------------------------------------------
# 3. Canonical Model & Amra Transformation Agreement
# -------------------------------------------------------------------------

def test_canonical_and_amra_schema_agreement():
    """
    Verify that AmraWorkbookTransformer accurately projects canonical data
    and respects single-piece price, customer-facing product name, and factual description.
    """
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="RAJTEX NOORANI",
        product_name="Rajtex Noorani Saree",
        product_group="RAJTEX-N",
        brand_name="Rajtex",
        product_type="Saree",
        category_slug="sarees",
        supplier_price=1515.0,
        gst_percent=5.0,
        attributes={
            "fabric": "Pashmina Silk",
            "blouse_fabric": "Silk Blend",
            "work": "Heavy embroidery with sequence work",
            "occasion": "Festive Wear",
        }
    )
    v1 = Variant(
        variant_code="RAJTEX-NOORANI-V01",
        variant_name="Design 01",
        color="Maroon",
        image_sku="rajtex-n-001",
        primary_image_url="https://blob.vercel-storage.com/catalogs/Rajtex-N-001.png",
    )
    prod.add_variant(v1)

    records = AmraWorkbookTransformer.transform_to_amra_records([prod])
    assert len(records) == 1
    r = records[0]

    assert r["supplier_price"] == 1515.0
    assert r["catalog_name"] == "RAJTEX NOORANI"
    assert r["product_name"] == "Rajtex Noorani Saree"
    assert r["varient_name"] == "Maroon"
    assert r["image-sku"] == "rajtex-n-001"
    assert r["image_url"] == "https://blob.vercel-storage.com/catalogs/Rajtex-N-001.png"

    # Local filenames must never populate image_url before Blob upload
    v1.primary_image_url = "Rajtex-N-001.png"
    records_local = AmraWorkbookTransformer.transform_to_amra_records([prod])
    assert records_local[0]["image_url"] == ""
    assert r["gst_percent"] == "5% Extra"
    assert "Pashmina Silk" in r["description"]
    assert "1,515" in r["description"]


# -------------------------------------------------------------------------
# 4. Standalone Canonical Dataset Merger Tests
# -------------------------------------------------------------------------

def test_canonical_dataset_merger_combines_datasets():
    """Test merging multiple canonical exports into a unified CanonicalExport."""
    v1 = CanonicalVariant(variant_code="VAR-A-01", variant_name="Red")
    p1 = CanonicalProduct(
        product_id="prod-001",
        supplier_name="Supplier A",
        catalog_name="CATALOG A",
        product_name="Product A",
        supplier_price=1200.0,
        variants=[v1],
    )
    v2 = CanonicalVariant(variant_code="VAR-B-01", variant_name="Blue")
    p2 = CanonicalProduct(
        product_id="prod-002",
        supplier_name="Supplier B",
        catalog_name="CATALOG B",
        product_name="Product B",
        supplier_price=1500.0,
        variants=[v2],
    )

    ds1 = CanonicalExport(total_products=1, total_variants=1, products=[p1])
    ds2 = CanonicalExport(total_products=1, total_variants=1, products=[p2])

    merged = CanonicalDatasetMerger.merge_datasets([ds1, ds2])
    assert merged.total_products == 2
    assert merged.total_variants == 2
    assert len(merged.products) == 2


def test_canonical_dataset_merger_detects_conflicts():
    """Test that conflicting product IDs or variant codes trigger CanonicalMergeConflictError."""
    v1 = CanonicalVariant(variant_code="VAR-01", variant_name="Red")
    p1 = CanonicalProduct(
        product_id="conflict-id",
        supplier_name="Supplier A",
        catalog_name="CATALOG ONE",
        product_name="Product 1",
        variants=[v1],
    )
    v2 = CanonicalVariant(variant_code="VAR-02", variant_name="Blue")
    p2 = CanonicalProduct(
        product_id="conflict-id",
        supplier_name="Supplier B",
        catalog_name="CATALOG TWO",
        product_name="Product 2",
        variants=[v2],
    )

    ds1 = CanonicalExport(total_products=1, total_variants=1, products=[p1])
    ds2 = CanonicalExport(total_products=1, total_variants=1, products=[p2])

    with pytest.raises(CanonicalMergeConflictError, match="Product ID conflict"):
        CanonicalDatasetMerger.merge_datasets([ds1, ds2])
