import pytest
from src.adapters.generic import GenericTabularAdapter
from src.extractors.tabular import TabularExtractor
from src.models.enums import FieldSource


def test_generic_adapter_non_saree_apparel_fixture(tmp_path):
    """
    STRUCTURALLY DIFFERENT SUPPLIER FIXTURE:
    Tests generic apparel / kurti dataset with non-saree fields:
    - Headers: Item Code, Product Title, Collection, Brand, Category, Wholesale Price, Tax Rate, Shade, Size, Fabric Composition, Sleeve Type
    - Proves the system is completely supplier-agnostic and category-agnostic.
    """
    csv_file = tmp_path / "generic_kurti_supplier.csv"
    csv_content = (
        "Item Code,Product Title,Collection,Brand,Category,Wholesale Price,Tax Rate,Shade,Size,Fabric Composition,Sleeve Type\n"
        "WV-KUR-101,Aura Straight Kurti,Aura Summer 2026,WeaveStyle,Kurti,420,5,Maroon,M,100% Rayon,3/4 Sleeve\n"
        "WV-KUR-101,Aura Straight Kurti,Aura Summer 2026,WeaveStyle,Kurti,420,5,Mustard Yellow,L,100% Rayon,3/4 Sleeve\n"
        "WV-KUR-101,Aura Straight Kurti,Aura Summer 2026,WeaveStyle,Kurti,420,5,Teal,XL,100% Rayon,3/4 Sleeve\n"
    )
    csv_file.write_text(csv_content, encoding="utf-8")

    raw_data = TabularExtractor.extract(csv_file)
    adapter = GenericTabularAdapter()

    assert adapter.can_handle(raw_data) is True
    products = adapter.transform(raw_data)

    # 1. Product grouping verification (1 grouped parent product for 3 variants)
    assert len(products) == 1
    product = products[0]

    assert product.catalog_name == "Aura Summer 2026"
    assert product.product_name == "Aura Straight Kurti"
    assert product.brand_name == "WeaveStyle"
    assert product.product_type == "Kurti"
    assert product.category_slug == "kurti"
    assert product.supplier_price == 420.0
    assert product.gst_percent == 5.0

    # 2. Category-neutral attributes preserved
    assert product.raw_attributes.get("Fabric Composition") == "100% Rayon"
    assert product.raw_attributes.get("Sleeve Type") == "3/4 Sleeve"

    # 3. Variants verification
    assert len(product.variants) == 3

    # All 3 variants share supplier_sku = 'WV-KUR-101'
    assert {v.supplier_sku for v in product.variants} == {"WV-KUR-101"}

    # Variant 1
    v1 = product.variants[0]
    assert v1.variant_name == "Maroon"
    assert v1.color == "Maroon"
    assert v1.size == "M"
    assert v1.supplier_cost == 420.0
    assert v1.variant_code == "WV-KUR-101-V01"  # Deterministically derived unique variant code
    assert v1.source_metadata.field_provenance["variant_code"].source_type == FieldSource.DERIVED

    # Variant 2
    v2 = product.variants[1]
    assert v2.variant_name == "Mustard Yellow"
    assert v2.color == "Mustard Yellow"
    assert v2.size == "L"
    assert v2.variant_code == "WV-KUR-101-V02"

    # Variant 3
    v3 = product.variants[2]
    assert v3.variant_name == "Teal"
    assert v3.color == "Teal"
    assert v3.size == "XL"
    assert v3.variant_code == "WV-KUR-101-V03"

    # 4. CRITICAL: selling_price is NOT invented
    for v in product.variants:
        assert v.selling_price is None
        assert v.source_metadata.field_provenance["selling_price"].source_type == FieldSource.MANUAL_REVIEW


def test_generic_adapter_single_row_products(tmp_path):
    """
    Verify standalone rows where each row is a distinct item (e.g. Footwear).
    """
    csv_file = tmp_path / "footwear_catalog.csv"
    csv_content = (
        "SKU,Name,Category,Cost,Color,Material,Sole\n"
        "SH-01,Leather Oxford Shoes,Footwear,1200,Brown,Full Grain Leather,Rubber\n"
        "SH-02,Slip-on Loafers,Footwear,950,Black,Suede,Leather\n"
    )
    csv_file.write_text(csv_content, encoding="utf-8")

    raw_data = TabularExtractor.extract(csv_file)
    adapter = GenericTabularAdapter()
    products = adapter.transform(raw_data)

    assert len(products) == 2
    p1 = products[0]
    assert p1.product_name == "Leather Oxford Shoes"
    assert p1.supplier_price == 1200.0
    assert p1.raw_attributes.get("Material") == "Full Grain Leather"
    assert p1.raw_attributes.get("Sole") == "Rubber"
    assert len(p1.variants) == 1
    assert p1.variants[0].supplier_sku == "SH-01"
    assert p1.variants[0].color == "Brown"
