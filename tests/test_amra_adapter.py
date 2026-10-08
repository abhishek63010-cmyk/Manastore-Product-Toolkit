from pathlib import Path
import pytest
from src.adapters.amra import AmraAdapter
from src.extractors.tabular import TabularExtractor
from src.models.enums import FieldSource


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_amra_adapter_can_handle_gold_master(project_root):
    """Verify AmraAdapter correctly identifies the committed Amra gold-master workbook."""
    gold_master_path = project_root / "Amra_data_with_all_blob_urls.xlsx"
    assert gold_master_path.exists(), "Gold master workbook must exist in repository root"

    raw_data = TabularExtractor.extract(gold_master_path)
    adapter = AmraAdapter()
    assert adapter.can_handle(raw_data) is True


def test_amra_adapter_transform_gold_master(project_root):
    """
    Verify transforming the 149-row Amra gold-master workbook into canonical Product + Variant models.
    Validates:
    - 23 distinct catalog groups produced
    - 149 total variants across all products
    - Shared supplier_sku across variants in the same catalog
    - Unique variant_codes
    - Multi-angle gallery image URLs preserved
    - Category attributes preserved
    - Provenance tracking
    """
    gold_master_path = project_root / "Amra_data_with_all_blob_urls.xlsx"
    raw_data = TabularExtractor.extract(gold_master_path)
    adapter = AmraAdapter()

    products = adapter.transform(raw_data)

    # 1. Catalog count verification (23 unique catalogs in the gold-master)
    assert len(products) == 23

    total_variants = sum(len(p.variants) for p in products)
    assert total_variants == 149

    # 2. Test Mahotsav Kriti catalog group
    mahotsav = next((p for p in products if p.catalog_name == "Mahotsav Kriti"), None)
    assert mahotsav is not None
    assert mahotsav.supplier_name == "Amra Wholesale"
    assert mahotsav.product_group == "Mahots-K"
    assert mahotsav.brand_name == "Mahotsav"
    assert mahotsav.get_attribute("fabric") == "Soft Chinon Silk"
    assert mahotsav.get_attribute("blouse_fabric") == "Soft Chinon Silk"
    assert len(mahotsav.variants) == 15

    # 3. Verify shared supplier_sku among variants
    assert {v.supplier_sku for v in mahotsav.variants} == {"AM-17090"}

    # 4. Verify unique variant_codes
    variant_codes = [v.variant_code for v in mahotsav.variants]
    assert len(variant_codes) == len(set(variant_codes))
    assert "Mahots-K-S3207" in variant_codes

    # 5. Verify image URLs
    v_first = mahotsav.variants[0]
    assert v_first.variant_name == "Emarald Green"
    assert v_first.primary_image_url.startswith("https://ladg3qktuldto6fi.public.blob.vercel-storage.com/")
    assert v_first.image_sku == "Mahots-K-S3207"

    # 6. Test multi-angle gallery images in SETHNIC VANTASA
    vantasa = next((p for p in products if p.catalog_name == "SETHNIC VANTASA"), None)
    assert vantasa is not None
    assert len(vantasa.variants) == 6
    v_vantasa_first = vantasa.variants[0]
    assert len(v_vantasa_first.gallery_image_urls) == 3
    assert all("ladg3qktuldto6fi.public.blob.vercel-storage.com" in url for url in v_vantasa_first.gallery_image_urls)

    # 7. Verify provenance
    assert mahotsav.source_metadata.field_provenance["supplier_name"].source_type == FieldSource.SOURCE
    assert v_first.source_metadata.field_provenance["variant_name"].source_type == FieldSource.SOURCE


def test_amra_adapter_single_catalog_raw_file(project_root):
    """
    Verify AmraAdapter processing single-catalog raw fixture (input/AMRA-RAJTEX-001/raw_supplier_data.xlsx).
    Ensures:
    - Saree attributes extracted (fabric, blouse_fabric, saree_length, etc.)
    - Unmapped fields preserved in raw_attributes (e.g. stitch_type, dispatch_location)
    - Selling price is NOT invented (leaves selling_price as None)
    """
    fixture_path = project_root / "input" / "AMRA-RAJTEX-001" / "raw_supplier_data.xlsx"
    assert fixture_path.exists(), "AMRA-RAJTEX-001 fixture must exist"

    raw_data = TabularExtractor.extract(fixture_path)
    adapter = AmraAdapter()
    assert adapter.can_handle(raw_data) is True

    products = adapter.transform(raw_data)
    assert len(products) == 1

    product = products[0]
    assert product.supplier_name == "Amra Wholesale"
    assert product.catalog_name == "Rajtex Ajrakh Vol-35"
    assert product.brand_name == "Rajtex"
    assert product.supplier_price == 780.0
    assert product.gst_percent == 5.0
    assert product.get_attribute("fabric") == "Modal Satin"
    assert product.get_attribute("blouse_fabric") == "Modal Satin"
    assert product.get_attribute("work") == "Ajrakh Block Printed Work"
    # Verify category attributes
    assert product.get_attribute("stitch_type") == "Unstitched"
    assert product.dispatch_location == "Surat, India"

    # Verify unmapped facts preserved in raw_attributes
    assert product.raw_attributes.get("design_count") == "6"
    assert product.raw_attributes.get("source_upload_date") == "2026-09-28"

    # Verify variants
    assert len(product.variants) == 1
    variant = product.variants[0]
    assert variant.supplier_sku == "AM-1726I"

    # CRITICAL: Verify selling_price is NOT invented
    assert variant.selling_price is None
    assert variant.source_metadata.field_provenance["selling_price"].source_type == FieldSource.MANUAL_REVIEW
