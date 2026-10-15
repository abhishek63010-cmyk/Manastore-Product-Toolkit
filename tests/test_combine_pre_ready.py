"""
tests/test_combine_pre_ready.py

Automated test suite for standalone Excel consolidation script scripts/combine-pre-ready.py.
Covers:
- Discovery safety (excluding _internal, _reports, root-level, old batches)
- Exact header and schema validation
- Missing, corrupt, empty (0 data rows) rejection
- Preserving blank cells, repeated No, prices, datatypes
- Protection against overwriting existing output unless forced
- Dry-run mode validation and preview without creating output
- Ensuring source workbooks remain unchanged
- Verifying total combined rows equal sum of source rows
"""

import sys
import subprocess
from pathlib import Path
import pytest
import openpyxl
from openpyxl.styles import Font

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "combine-pre-ready.py"

EXACT_COLUMNS = [
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


def create_sample_workbook(file_path: Path, rows_data: list, headers: list = None):
    """Helper to generate sample manastore_pre_ready.xlsx with specified rows."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Products"
    
    hdr = headers if headers is not None else EXACT_COLUMNS
    for col_idx, col_name in enumerate(hdr, 1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = Font(bold=True)
        
    for r_idx, row in enumerate(rows_data, 2):
        for c_idx, val in enumerate(row, 1):
            ws.cell(row=r_idx, column=c_idx, value=val)
            
    wb.save(file_path)
    wb.close()


def run_script(args: list) -> subprocess.CompletedProcess:
    """Helper to execute scripts/combine-pre-ready.py via Python."""
    cmd = [sys.executable, str(SCRIPT_PATH)] + args
    return subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True
    )


def test_combine_single_workbook(tmp_path):
    batch_dir = tmp_path / "batch_single"
    cat1_dir = batch_dir / "01_CatalogueA"
    
    rows1 = [
        [1, "SupplierA", "CatA", "GroupA", "Product 1", "Red", "SKU1", "Saree", "saree", "BrandA", "Silk", None, "Zari", "Party", "Free", "Desc 1", 500, 5, 750, "IMG-01", "http://img1.jpg", "yes", None, None, None, None],
        [2, "SupplierA", "CatA", "GroupA", "Product 1", "Blue", "SKU2", "Saree", "saree", "BrandA", "Silk", None, "Zari", "Party", "Free", "Desc 2", 500, 5, 750, "IMG-02", "http://img2.jpg", "yes", None, None, None, None],
    ]
    create_sample_workbook(cat1_dir / "manastore_pre_ready.xlsx", rows1)

    result = run_script([str(batch_dir)])
    assert result.returncode == 0
    assert "[SUCCESS]" in result.stdout
    assert "2 row(s)" in result.stdout

    combined_path = batch_dir / "manastore_pre_ready.xlsx"
    assert combined_path.exists()
    
    wb = openpyxl.load_workbook(combined_path)
    ws = wb.active
    assert ws.max_row == 3  # 1 header + 2 product rows
    wb.close()


def test_combine_multiple_workbooks_different_row_counts(tmp_path):
    batch_dir = tmp_path / "batch_multi"
    cat1_dir = batch_dir / "01_CatA"
    cat2_dir = batch_dir / "02_CatB"
    cat3_dir = batch_dir / "03_CatC"
    
    row_template = [1, "Sup", "Cat", "Grp", "Prod", "Var", "SKU", "Saree", "saree", "Brand", "Cotton", None, "Plain", "Casual", "Free", "D", 400, 5, 600, "ISKU", "url", "yes", None, None, None, None]
    
    # Cat 1 has 3 rows
    create_sample_workbook(cat1_dir / "manastore_pre_ready.xlsx", [row_template] * 3)
    # Cat 2 has 5 rows
    create_sample_workbook(cat2_dir / "manastore_pre_ready.xlsx", [row_template] * 5)
    # Cat 3 has 2 rows
    create_sample_workbook(cat3_dir / "manastore_pre_ready.xlsx", [row_template] * 2)

    result = run_script([str(batch_dir)])
    assert result.returncode == 0
    assert "Total Discovered Product Rows: 10" in result.stdout

    combined_path = batch_dir / "manastore_pre_ready.xlsx"
    assert combined_path.exists()

    wb = openpyxl.load_workbook(combined_path)
    ws = wb.active
    assert ws.max_row == 11  # 1 header + 10 product rows
    wb.close()

    summary_file = batch_dir / "consolidation_summary.txt"
    assert summary_file.exists()
    summary_text = summary_file.read_text(encoding="utf-8")
    assert "Total Product Rows:   10" in summary_text
    assert "01_CatA" in summary_text
    assert "02_CatB" in summary_text
    assert "03_CatC" in summary_text


def test_dry_run_mode(tmp_path):
    batch_dir = tmp_path / "batch_dry_run"
    cat1_dir = batch_dir / "01_CatA"
    row = [1, "Sup", "Cat", "Grp", "Prod", "Var", "SKU", "Saree", "saree", "Brand", "Cotton", None, "Plain", "Casual", "Free", "D", 400, 5, 600, "ISKU", "url", "yes", None, None, None, None]
    create_sample_workbook(cat1_dir / "manastore_pre_ready.xlsx", [row] * 4)

    result = run_script([str(batch_dir), "--dry-run"])
    assert result.returncode == 0
    assert "[DRY-RUN]" in result.stdout
    assert "Validation passed for all workbooks" in result.stdout

    combined_path = batch_dir / "manastore_pre_ready.xlsx"
    assert not combined_path.exists(), "Dry-run must not create the output file"


def test_rejection_on_exact_header_mismatch(tmp_path):
    batch_dir = tmp_path / "batch_bad_header"
    cat1_dir = batch_dir / "01_CatA"
    
    # Swapped column order
    bad_columns = list(EXACT_COLUMNS)
    bad_columns[1], bad_columns[2] = bad_columns[2], bad_columns[1]
    
    row = [1] * len(bad_columns)
    create_sample_workbook(cat1_dir / "manastore_pre_ready.xlsx", [row], headers=bad_columns)

    result = run_script([str(batch_dir)])
    assert result.returncode != 0
    assert "Schema mismatch" in result.stderr
    assert not (batch_dir / "manastore_pre_ready.xlsx").exists()


def test_rejection_on_empty_product_rows(tmp_path):
    batch_dir = tmp_path / "batch_empty"
    cat1_dir = batch_dir / "01_CatA"
    
    # Only header, 0 data rows
    create_sample_workbook(cat1_dir / "manastore_pre_ready.xlsx", [])

    result = run_script([str(batch_dir)])
    assert result.returncode != 0
    assert "0 product data rows (empty workbook)" in result.stderr
    assert not (batch_dir / "manastore_pre_ready.xlsx").exists()


def test_preserves_blank_cells_and_values(tmp_path):
    batch_dir = tmp_path / "batch_blanks"
    cat1_dir = batch_dir / "01_CatA"
    
    # Row with specific blanks and data types
    row1 = [1, "SupA", "CatA", "GrpA", "Prod 1", "Var 1", "SKU1", "Saree", "saree", "BrandA", "Silk", None, "Zari", "Party", "Free", "Desc", 500.50, 5, 800.75, "IMG-01", "http://img1.jpg", "yes", None, None, None, None]
    create_sample_workbook(cat1_dir / "manastore_pre_ready.xlsx", [row1])

    result = run_script([str(batch_dir)])
    assert result.returncode == 0

    combined_path = batch_dir / "manastore_pre_ready.xlsx"
    wb = openpyxl.load_workbook(combined_path)
    ws = wb.active
    
    # Check blouse_fabric (col 12) is None
    assert ws.cell(row=2, column=12).value is None
    # Check price (col 17) is 500.50
    assert ws.cell(row=2, column=17).value == 500.50
    # Check image1_url (col 23) is None
    assert ws.cell(row=2, column=23).value is None
    wb.close()


def test_preserves_repeated_no_and_duplicate_looking_products(tmp_path):
    batch_dir = tmp_path / "batch_dups"
    cat1_dir = batch_dir / "01_CatA"
    cat2_dir = batch_dir / "02_CatB"
    
    # Both catalogues have row starting with No=1
    row_cat1 = [1, "SupA", "CatA", "GrpA", "Prod 1", "Var 1", "SKU-1", "Saree", "saree", "BrandA", "Silk", None, "Zari", "Party", "Free", "Desc", 500, 5, 750, "IMG-01", "http://img1.jpg", "yes", None, None, None, None]
    row_cat2 = [1, "SupA", "CatA", "GrpA", "Prod 1", "Var 1", "SKU-1", "Saree", "saree", "BrandA", "Silk", None, "Zari", "Party", "Free", "Desc", 500, 5, 750, "IMG-01", "http://img1.jpg", "yes", None, None, None, None]
    
    create_sample_workbook(cat1_dir / "manastore_pre_ready.xlsx", [row_cat1])
    create_sample_workbook(cat2_dir / "manastore_pre_ready.xlsx", [row_cat2])

    result = run_script([str(batch_dir)])
    assert result.returncode == 0

    combined_path = batch_dir / "manastore_pre_ready.xlsx"
    wb = openpyxl.load_workbook(combined_path)
    ws = wb.active
    assert ws.max_row == 3
    # Both rows must be present and preserve their original 'No' value of 1 without recalculation or deduplication
    assert ws.cell(row=2, column=1).value == 1
    assert ws.cell(row=3, column=1).value == 1
    wb.close()


def test_ignores_internal_and_reports_and_root_files(tmp_path):
    batch_dir = tmp_path / "batch_ignore"
    cat1_dir = batch_dir / "01_CatA"
    internal_dir = batch_dir / "_internal"
    reports_dir = batch_dir / "_reports"
    
    row = [1, "Sup", "Cat", "Grp", "Prod", "Var", "SKU", "Saree", "saree", "Brand", "Silk", None, "Zari", "Party", "Free", "Desc", 500, 5, 750, "IMG-01", "http://img1.jpg", "yes", None, None, None, None]
    
    create_sample_workbook(cat1_dir / "manastore_pre_ready.xlsx", [row] * 2)
    # Put bogus workbooks inside _internal, _reports, and root
    create_sample_workbook(internal_dir / "manastore_pre_ready.xlsx", [row] * 5)
    create_sample_workbook(reports_dir / "manastore_pre_ready.xlsx", [row] * 5)
    create_sample_workbook(batch_dir / "other_report.xlsx", [row] * 5)

    result = run_script([str(batch_dir)])
    assert result.returncode == 0
    assert "Discovered 1 catalogue workbook(s)" in result.stdout
    assert "Total Discovered Product Rows: 2" in result.stdout


def test_existing_output_protection(tmp_path):
    batch_dir = tmp_path / "batch_protect"
    cat1_dir = batch_dir / "01_CatA"
    row = [1, "Sup", "Cat", "Grp", "Prod", "Var", "SKU", "Saree", "saree", "Brand", "Silk", None, "Zari", "Party", "Free", "Desc", 500, 5, 750, "IMG-01", "http://img1.jpg", "yes", None, None, None, None]
    create_sample_workbook(cat1_dir / "manastore_pre_ready.xlsx", [row])

    # Pre-create output workbook
    existing_output = batch_dir / "manastore_pre_ready.xlsx"
    existing_output.write_text("dummy", encoding="utf-8")

    # Run without --force should fail
    result = run_script([str(batch_dir)])
    assert result.returncode != 0
    assert "Destination workbook already exists" in result.stderr

    # Run with --force / --overwrite should succeed
    result_force = run_script([str(batch_dir), "--force"])
    assert result_force.returncode == 0
    assert "[SUCCESS]" in result_force.stdout


def test_source_workbooks_remain_unchanged(tmp_path):
    batch_dir = tmp_path / "batch_immutable"
    cat1_dir = batch_dir / "01_CatA"
    cat2_dir = batch_dir / "02_CatB"
    row = [1, "Sup", "Cat", "Grp", "Prod", "Var", "SKU", "Saree", "saree", "Brand", "Silk", None, "Zari", "Party", "Free", "Desc", 500, 5, 750, "IMG-01", "http://img1.jpg", "yes", None, None, None, None]
    
    file1 = cat1_dir / "manastore_pre_ready.xlsx"
    file2 = cat2_dir / "manastore_pre_ready.xlsx"
    create_sample_workbook(file1, [row] * 2)
    create_sample_workbook(file2, [row] * 3)

    bytes1_before = file1.read_bytes()
    bytes2_before = file2.read_bytes()

    result = run_script([str(batch_dir)])
    assert result.returncode == 0

    assert file1.read_bytes() == bytes1_before, "Source workbook 1 was modified!"
    assert file2.read_bytes() == bytes2_before, "Source workbook 2 was modified!"
