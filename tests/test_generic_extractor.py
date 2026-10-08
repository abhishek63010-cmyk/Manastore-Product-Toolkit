import pandas as pd
import pytest
from src.extractors.tabular import TabularExtractor


def test_extract_csv(tmp_path):
    """Verify generic CSV extraction with preserved headers and row indices."""
    csv_file = tmp_path / "sample_products.csv"
    csv_content = (
        "SKU,Product Title,Category,Cost,Color\n"
        "TSH-001,Basic Cotton Tee,Apparel,250,Navy Blue\n"
        "TSH-002,Basic Cotton Tee,Apparel,250,White\n"
    )
    csv_file.write_text(csv_content, encoding="utf-8")

    raw = TabularExtractor.extract(csv_file)
    assert raw.total_rows == 2
    assert raw.headers == ["SKU", "Product Title", "Category", "Cost", "Color"]
    
    rec1 = raw.records[0]
    assert rec1.row_number == 2
    assert rec1.data["SKU"] == "TSH-001"
    assert rec1.data["Product Title"] == "Basic Cotton Tee"
    assert rec1.data["Cost"] == "250"
    assert rec1.data["Color"] == "Navy Blue"

    rec2 = raw.records[1]
    assert rec2.row_number == 3
    assert rec2.data["SKU"] == "TSH-002"
    assert rec2.data["Color"] == "White"


def test_extract_tsv(tmp_path):
    """Verify TSV file extraction with automatic tab delimiter detection."""
    tsv_file = tmp_path / "supplier_export.tsv"
    tsv_content = (
        "ItemCode\tItemName\tWholesale\tMaterial\n"
        "JW-101\tKundan Earring\t450\tBrass\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    raw = TabularExtractor.extract(tsv_file)
    assert raw.total_rows == 1
    assert raw.headers == ["ItemCode", "ItemName", "Wholesale", "Material"]
    assert raw.records[0].data["ItemCode"] == "JW-101"
    assert raw.records[0].data["Material"] == "Brass"


def test_extract_xlsx_custom_sheets(tmp_path):
    """
    Verify generic XLSX extraction supporting custom worksheet names
    without assuming 'Products'.
    """
    xlsx_file = tmp_path / "multi_sheet_catalog.xlsx"
    with pd.ExcelWriter(xlsx_file, engine="openpyxl") as writer:
        pd.DataFrame([
            {"Style": "K-01", "Title": "A-Line Kurti", "Fabric": "Rayon"},
            {"Style": "K-02", "Title": "Straight Kurti", "Fabric": "Cotton"},
        ]).to_excel(writer, sheet_name="Kurtis_2026", index=False)

        pd.DataFrame([
            {"Code": "D-01", "Title": "Anarkali Gown", "Fabric": "Silk"},
        ]).to_excel(writer, sheet_name="Dresses", index=False)

    # 1. Inspect sheets
    sheets = TabularExtractor.list_sheets(xlsx_file)
    assert sheets == ["Kurtis_2026", "Dresses"]

    # 2. Extract first sheet by default
    raw_default = TabularExtractor.extract(xlsx_file)
    assert raw_default.sheet_name == "Kurtis_2026"
    assert raw_default.total_rows == 2
    assert raw_default.records[0].data["Style"] == "K-01"

    # 3. Extract explicit named sheet
    raw_dresses = TabularExtractor.extract(xlsx_file, sheet_name="Dresses")
    assert raw_dresses.sheet_name == "Dresses"
    assert raw_dresses.total_rows == 1
    assert raw_dresses.records[0].data["Code"] == "D-01"


def test_extract_key_value_attribute_file(tmp_path):
    """Verify parsing of 2-column key-value attribute files (Column/Value format)."""
    kv_file = tmp_path / "attributes.tsv"
    kv_content = (
        "Column\tValue\n"
        "supplier_name\tAmra Wholesale\n"
        "catalogue_name\tRajtex Ajrakh Vol-35\n"
        "supplier_sku\tAM-1726I\n"
        "supplier_price\t780\n"
    )
    kv_file.write_text(kv_content, encoding="utf-8")

    raw = TabularExtractor.extract(kv_file)
    assert raw.total_rows == 1
    rec = raw.records[0]
    assert rec.get("supplier_name") == "Amra Wholesale"
    assert rec.get("catalogue_name") == "Rajtex Ajrakh Vol-35"
    assert rec.get("supplier_sku") == "AM-1726I"
