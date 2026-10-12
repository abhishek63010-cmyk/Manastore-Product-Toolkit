from pathlib import Path
import pytest
import openpyxl
import pandas as pd

from src.models.product import Product
from src.models.variant import Variant
from src.models.provenance import ProvenanceRecord
from src.mappers.amra_transformer import AmraWorkbookTransformer
from src.exporters.writers import OutputWriter


@pytest.fixture
def sample_product_and_variants() -> Product:
    """Create a sample Rajtex canonical product with 6 derived variants."""
    prod = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Rajtex Ajrak Vol-58",
        product_name="Rajtex Ajrakh Printed Modal Satin Saree",
        brand_name="Rajtex",
        product_type="Saree",
        category_slug="sarees",
        supplier_price=680.0,
        gst_percent=5.0,
        attributes={
            "fabric": "Modal Satin",
            "blouse_fabric": "Modal Satin",
            "work": "Ajrakh Print",
            "occasion": "Festive Wear / Traditional Wear",
            "size": "Free Size",
            "wholesale_price": 580.0,
            "full_catalogue_price": 3480.0,
        },
        raw_attributes={
            "available": "=3480 (6 Pcs)",
            "wholesale_price": 580.0,
        },
    )
    for i in range(1, 7):
        v = Variant(
            variant_code=f"RAJTEX-AJRAKH-VOL-58-V{i:02d}",
            variant_name=f"Design {i:02d}",
            supplier_cost=680.0,
            publish=False,
        )
        prod.add_variant(v)
    return prod


def test_amra_schema_exact_columns_and_order():
    """1. Exact 26-column schema and column order matching Amra gold master."""
    expected_cols = [
        "No",
        "supplier_name",
        "catalog_name",
        "product_group",
        "product_name",
        "varient_name",
        "supplier_sku",
        "product_type",
        "category_slug",
        "brand_name",
        "fabric",
        "blouse_fabric",
        "work",
        "occasion",
        "size",
        "description",
        "supplier_price",
        "gst_percent",
        "selling_price",
        "image-sku",
        "image_url",
        "publish",
        "image1_url",
        "image2_url",
        "image3_url",
        "image4_url",
    ]
    assert len(expected_cols) == 26
    assert AmraWorkbookTransformer.EXACT_COLUMNS == expected_cols


def test_six_input_variants_generate_six_output_rows(sample_product_and_variants):
    """2. Six input variants generate exactly six output rows."""
    rows = AmraWorkbookTransformer.transform_to_amra_records([sample_product_and_variants])
    assert len(rows) == 6
    assert all(r["No"] == 1 for r in rows)


def test_product_group_generation():
    """3. Product-group generation follows business rules across multiple catalog names."""
    assert AmraWorkbookTransformer.generate_product_group("Rajtex Ajrak Vol-58") == "Rajtex-A"
    assert AmraWorkbookTransformer.generate_product_group("Mahotsav Kriti") == "Mahots-K"
    assert AmraWorkbookTransformer.generate_product_group("BUNAWAT ERADI") == "BUNAWA-E"
    assert AmraWorkbookTransformer.generate_product_group("SETHNIC OSHEE 114001 TO 114004") == "SETHNI-O"
    assert AmraWorkbookTransformer.generate_product_group("SETHNIC Lifestyle Sutrlala Vol-4") == "SETHNI-LS"
    assert AmraWorkbookTransformer.generate_product_group("SingleWord") == "Single"
    assert AmraWorkbookTransformer.generate_product_group("") == "GROUP-1"
    assert AmraWorkbookTransformer.generate_product_group(None) == "GROUP-1"


def test_image_skus_and_deterministic_order(sample_product_and_variants, tmp_path):
    """4. Image SKUs are extracted from filenames and maintain deterministic natural order."""
    img_dir = tmp_path / "images"
    img_dir.mkdir()

    # Create dummy image files in non-alphabetical order
    for name in ["58-D.jpg", "58-B.jpg", "58-A.jpg", "58-F.jpg", "58-C.jpg", "58-E.jpg"]:
        (img_dir / name).write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)

    discovered = AmraWorkbookTransformer.discover_and_sort_images(img_dir)
    discovered_stems = [f.stem for f in discovered]
    assert discovered_stems == ["58-A", "58-B", "58-C", "58-D", "58-E", "58-F"]

    rows = AmraWorkbookTransformer.transform_to_amra_records([sample_product_and_variants], images_path=img_dir)
    assert [r["image-sku"] for r in rows] == ["58-A", "58-B", "58-C", "58-D", "58-E", "58-F"]


def test_variant_color_from_image_analysis(sample_product_and_variants, tmp_path):
    """5. Variant colour is taken from the corresponding image analysis result."""
    from PIL import Image

    img_dir = tmp_path / "images"
    img_dir.mkdir()

    # Create 6 distinct solid RGB images
    colors = [
        ("Red", (220, 30, 30)),
        ("Green", (30, 200, 30)),
        ("Blue", (30, 50, 220)),
        ("Yellow", (240, 240, 30)),
        ("Orange", (240, 140, 20)),
        ("Pink", (240, 150, 180)),
    ]

    for idx, (cname, rgb) in enumerate(colors):
        filename = f"58-{'ABCDEF'[idx]}.png"
        img = Image.new("RGB", (100, 100), color=rgb)
        img.save(img_dir / filename)

    rows = AmraWorkbookTransformer.transform_to_amra_records([sample_product_and_variants], images_path=img_dir)
    assert len(rows) == 6
    extracted_colors = [r["varient_name"] for r in rows]
    # Verify that confident extracted colors populate varient_name
    assert extracted_colors == ["Red", "Green", "Blue", "Yellow", "Orange", "Pink"]


def test_source_supplier_sku_takes_precedence(sample_product_and_variants):
    """6. Existing source supplier SKUs take precedence over the fallback."""
    sample_product_and_variants.variants[0].supplier_sku = "AM-EXPLICIT-1"
    rows = AmraWorkbookTransformer.transform_to_amra_records([sample_product_and_variants])
    assert rows[0]["supplier_sku"] == "AM-EXPLICIT-1"
    # Others without explicit SKU fall back to catalog suffix
    assert rows[1]["supplier_sku"] == "Vol-58"


def test_catalogue_suffix_fallback(sample_product_and_variants):
    """7. The catalogue suffix fallback works when the source SKU is missing."""
    assert AmraWorkbookTransformer.derive_supplier_sku_fallback("Rajtex Ajrak Vol-58") == "Vol-58"
    assert AmraWorkbookTransformer.derive_supplier_sku_fallback("Deeptex Miss India Vol 12") == "Vol-12"
    assert AmraWorkbookTransformer.derive_supplier_sku_fallback("Sethnic Series 142000") == "142000"

    rows = AmraWorkbookTransformer.transform_to_amra_records([sample_product_and_variants])
    for r in rows:
        assert r["supplier_sku"] == "Vol-58"


def test_description_generation_reused(sample_product_and_variants):
    """8. Description generation is reused and preserves facts."""
    # When description is present, reuse it
    sample_product_and_variants.description = "Custom rich editorial description for this saree."
    rows = AmraWorkbookTransformer.transform_to_amra_records([sample_product_and_variants])
    assert rows[0]["description"] == "Custom rich editorial description for this saree."

    # When description is absent, synthesized from attributes
    sample_product_and_variants.description = None
    rows2 = AmraWorkbookTransformer.transform_to_amra_records([sample_product_and_variants])
    desc = rows2[0]["description"]
    assert "Modal Satin" in desc
    assert "Rajtex" in desc
    assert "Ajrakh Print" in desc


def test_supplier_price_and_selling_price_distinct(sample_product_and_variants):
    """9. Supplier price and selling price remain distinct; selling_price is not invented."""
    rows = AmraWorkbookTransformer.transform_to_amra_records([sample_product_and_variants])
    for r in rows:
        assert r["supplier_price"] == 680.0
        assert r["selling_price"] == ""


def test_image_urls_blank_before_blob_upload(sample_product_and_variants, tmp_path):
    """10. Every image URL column is blank before Blob upload."""
    rows = AmraWorkbookTransformer.transform_to_amra_records([sample_product_and_variants])
    for r in rows:
        assert r["image_url"] == ""
        assert r["image1_url"] == ""
        assert r["image2_url"] == ""
        assert r["image3_url"] == ""
        assert r["image4_url"] == ""


def test_new_rows_remain_unpublished(sample_product_and_variants):
    """11. New rows remain unpublished until approval."""
    rows = AmraWorkbookTransformer.transform_to_amra_records([sample_product_and_variants])
    for r in rows:
        assert r["publish"] is False


def test_formula_safety_export(sample_product_and_variants, tmp_path):
    """12. Text beginning with '=' does not become an accidental spreadsheet formula."""
    sample_product_and_variants.catalog_name = "=CatalogFormula"
    out_file = tmp_path / "amra_test.xlsx"

    OutputWriter.write_amra_import_xlsx(
        products=[sample_product_and_variants],
        output_path=out_file,
    )
    assert out_file.exists()

    # Load with openpyxl and inspect cell data_type
    wb = openpyxl.load_workbook(out_file)
    ws = wb.active
    # Row 1 is headers, Row 2 is first data row
    # Column 3 is catalog_name
    cat_cell = ws.cell(row=2, column=3)
    assert cat_cell.value == "=CatalogFormula"
    assert cat_cell.data_type == "s", f"Expected cell data_type 's' but got '{cat_cell.data_type}'"

    # Also verify pandas readback
    df = pd.read_excel(out_file)
    assert df["catalog_name"].iloc[0] == "=CatalogFormula"


def test_customer_facing_product_name_generation():
    """13. Customer-facing product name synthesis correctly derives rich titles."""
    name = AmraWorkbookTransformer.generate_customer_product_name(
        catalog_name="Rajtex Ajrak Vol-58",
        brand_name="Rajtex",
        fabric="Modal Satin",
        work="Ajrakh Print",
        product_type="Saree",
    )
    assert name == "Rajtex Ajrakh Printed Modal Satin Saree"

    name_plain = AmraWorkbookTransformer.generate_customer_product_name(
        catalog_name="BUNAWAT ERADI",
        brand_name="BUNAWAT",
        fabric="Silk",
        work="",
        product_type="Saree",
    )
    assert name_plain == "BUNAWAT ERADI Silk Saree"


def test_rajtex_real_image_color_analysis():
    """14. Real Rajtex images distinguish Teal, Rust, Red, Navy Blue and flag multi-tonal designs as uncertain."""
    from src.images.color import ImageColorAnalyzer
    img_dir = Path("input/Amra_wholesale/Rajtex Ajrak Vol 58/product-images")
    if not img_dir.exists():
        pytest.skip("Real Rajtex product-images directory not found")

    res_a = ImageColorAnalyzer.analyze_image(img_dir / "58-A.png")
    assert res_a.status == "EXTRACTED"
    assert res_a.canonical_color == "Teal"
    assert not res_a.is_ambiguous

    res_b = ImageColorAnalyzer.analyze_image(img_dir / "58-B.png")
    assert res_b.status == "EXTRACTED"
    assert res_b.canonical_color == "Rust"
    assert not res_b.is_ambiguous

    res_c = ImageColorAnalyzer.analyze_image(img_dir / "58-C.png")
    assert res_c.status == "EXTRACTED"
    assert res_c.canonical_color == "Red"
    assert not res_c.is_ambiguous

    res_e = ImageColorAnalyzer.analyze_image(img_dir / "58-E.png")
    assert res_e.status == "UNCERTAIN"
    assert res_e.canonical_color is None
    assert res_e.is_ambiguous

    res_f = ImageColorAnalyzer.analyze_image(img_dir / "58-F.png")
    assert res_f.status == "EXTRACTED"
    assert res_f.canonical_color == "Navy Blue"
    assert not res_f.is_ambiguous

