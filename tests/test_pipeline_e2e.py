import json
import subprocess
import sys
from pathlib import Path
import pytest
import pandas as pd
from zipfile import ZipFile

from src.adapters.registry import AdapterRegistry
from src.pipeline.config import PipelineConfig
from src.pipeline.orchestrator import PipelineOrchestrator
from src.uploaders.fake import FakeBlobUploader


@pytest.fixture
def sample_generic_data(tmp_path: Path) -> tuple[Path, Path]:
    """Create a temporary generic apparel CSV and matching image folder."""
    data_file = tmp_path / "kurtis.csv"
    data_file.write_text(
        "Product Name,SKU,Variant Code,Variant Name,Color,Size,Wholesale Price,Stock,Description\n"
        "Rayon Anarkali Kurti,KURTI-01,KURTI-01-RED-S,Red Small,Red,S,450.00,25,Pure rayon flared kurti\n"
        "Rayon Anarkali Kurti,KURTI-01,KURTI-01-RED-M,Red Medium,Red,M,450.00,30,Pure rayon flared kurti\n"
        "Rayon Anarkali Kurti,KURTI-01,KURTI-01-BLU-S,Blue Small,Blue,S,450.00,15,Pure rayon flared kurti\n"
        "Cotton Straight Kurti,KURTI-02,KURTI-02-BLK-L,Black Large,Black,L,380.00,10,100% breathable cotton kurti\n",
        encoding="utf-8",
    )

    img_dir = tmp_path / "images"
    img_dir.mkdir(parents=True)
    # Create fake image files
    (img_dir / "KURTI-01-RED-S.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRred_s")
    (img_dir / "KURTI-01-RED-S-1.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRred_s_gal1")
    (img_dir / "KURTI-01-BLU-S.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRblue_s")
    (img_dir / "KURTI-02-BLK-L.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRblk_l")

    return data_file, img_dir


@pytest.fixture
def sample_amra_zip_data(tmp_path: Path) -> tuple[Path, Path]:
    """Create a temporary Amra catalog TSV and image ZIP archive."""
    tsv_file = tmp_path / "sethni.tsv"
    tsv_content = (
        "Catalogue Name\tProduct Group\tSupplier SKU\tImage SKU\tVarient Name\tColor\tFabric\tSupplier Price\n"
        "SETHNI VOL 1\tSETHNI-V\tAM-18001\tSETHNI-V-108001\tWine\tWine\tDola Silk\t1295\n"
        "SETHNI VOL 1\tSETHNI-V\tAM-18001\tSETHNI-V-108002\tTeal\tTeal\tDola Silk\t1295\n"
        "SETHNI VOL 1\tSETHNI-V\tAM-18001\tSETHNI-V-108003\tNavy\tNavy\tDola Silk\t1295\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    zip_file = tmp_path / "sethni_images.zip"
    with ZipFile(zip_file, "w") as zf:
        zf.writestr("SETHNI-V-108001.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR108001_base")
        zf.writestr("SETHNI-V-108001-1.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR108001_gal1")
        zf.writestr("SETHNI-V-108002.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR108002_base")
        zf.writestr("SETHNI-V-108003.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR108003_base")

    return tsv_file, zip_file



def test_pipeline_e2e_generic_kurti_fixture(tmp_path: Path, sample_generic_data):
    """Test full pipeline on generic apparel fixture with all output formats."""
    data_file, img_dir = sample_generic_data
    out_dir = tmp_path / "output_generic"

    config = PipelineConfig(
        input_path=data_file,
        output_dir=out_dir,
        images_path=img_dir,
        export_formats=["xlsx", "json", "csv"],
        generate_report=True,
    )

    result = PipelineOrchestrator.run(config)

    assert result.success is True
    assert result.adapter_used == "generic_tabular"
    assert result.metrics.total_source_rows == 4
    assert result.metrics.total_products == 2
    assert result.metrics.total_variants == 4
    assert result.metrics.total_images_discovered == 4
    assert result.metrics.total_images_matched == 4
    assert result.metrics.is_valid is True

    # Check generated files
    assert (out_dir / "canonical_products.xlsx").exists()
    assert (out_dir / "canonical_products.json").exists()
    assert (out_dir / "canonical_products.csv").exists()
    assert (out_dir / "validation_report.xlsx").exists()
    assert (out_dir / "validation_report.json").exists()

    # Verify JSON content
    json_data = json.loads((out_dir / "canonical_products.json").read_text(encoding="utf-8"))
    assert json_data["total_products"] == 2
    assert json_data["total_variants"] == 4
    assert "export_version" in json_data

    # Verify Excel content has expected sheets
    excel_sheets = pd.ExcelFile(out_dir / "canonical_products.xlsx").sheet_names
    assert "Products" in excel_sheets


def test_pipeline_e2e_amra_zip_fixture(tmp_path: Path, sample_amra_zip_data):
    """Test pipeline regression on Amra TSV input with ZIP image archive."""
    tsv_file, zip_file = sample_amra_zip_data
    out_dir = tmp_path / "output_amra"

    config = PipelineConfig(
        input_path=tsv_file,
        output_dir=out_dir,
        supplier="amra",
        images_path=zip_file,
        export_formats=["xlsx", "json"],
        generate_report=True,
    )

    result = PipelineOrchestrator.run(config)

    assert result.success is True
    assert result.adapter_used == "amra_adapter"
    assert result.metrics.total_products == 1
    assert result.metrics.total_variants == 3
    assert result.metrics.total_images_discovered == 4
    assert result.metrics.total_images_matched == 4
    assert result.metrics.is_valid is True

    # Verify validation report Excel sheets
    val_excel = pd.ExcelFile(out_dir / "validation_report.xlsx")
    assert "Summary" in val_excel.sheet_names
    assert "Validation_Issues" in val_excel.sheet_names
    assert "Image_Matching" in val_excel.sheet_names


def test_pipeline_e2e_dry_run_mode(tmp_path: Path, sample_generic_data):
    """Test dry-run mode executes pipeline without generating files on disk."""
    data_file, img_dir = sample_generic_data
    out_dir = tmp_path / "dry_run_output"

    config = PipelineConfig(
        input_path=data_file,
        output_dir=out_dir,
        images_path=img_dir,
        dry_run=True,
    )

    result = PipelineOrchestrator.run(config)

    assert result.success is True
    assert result.metrics.total_products == 2
    assert result.metrics.total_variants == 4
    assert len(result.output_files) == 0
    assert not out_dir.exists()


def test_pipeline_e2e_fail_on_error_gate(tmp_path: Path):
    """Test --fail-on-error strictly halts execution when validation errors occur."""
    invalid_csv = tmp_path / "invalid.csv"
    # Duplicate variant codes across products trigger DUPLICATE_VARIANT_CODE error in validator
    invalid_csv.write_text(
        "Product Name,SKU,Variant Code,Variant Name,Wholesale Price,Stock\n"
        "Product Alpha,SKU-1,VAR-DUP,Option 1,500,10\n"
        "Product Beta,SKU-2,VAR-DUP,Option 2,600,15\n",
        encoding="utf-8",
    )

    # 1. With fail_on_error=True
    cfg_strict = PipelineConfig(
        input_path=invalid_csv,
        output_dir=tmp_path / "out_strict",
        fail_on_error=True,
    )
    res_strict = PipelineOrchestrator.run(cfg_strict)
    assert res_strict.success is False
    assert res_strict.metrics.is_valid is False
    assert res_strict.metrics.total_validation_errors > 0
    assert "Validation failed" in (res_strict.error_message or "")

    # 2. With fail_on_error=False (default lenient mode)
    cfg_lenient = PipelineConfig(
        input_path=invalid_csv,
        output_dir=tmp_path / "out_lenient",
        fail_on_error=False,
    )
    res_lenient = PipelineOrchestrator.run(cfg_lenient)
    assert res_lenient.success is True
    assert res_lenient.metrics.is_valid is False
    assert res_lenient.metrics.total_validation_errors > 0



def test_pipeline_e2e_with_fake_uploader(tmp_path: Path, sample_generic_data):
    """Test image upload lifecycle using in-memory FakeBlobUploader."""
    data_file, img_dir = sample_generic_data
    out_dir = tmp_path / "out_upload"

    uploader = FakeBlobUploader(base_url="https://blob.manastore.internal")

    config = PipelineConfig(
        input_path=data_file,
        output_dir=out_dir,
        images_path=img_dir,
        upload_images=True,
        upload_prefix="catalog_2026",
    )

    result = PipelineOrchestrator.run(config, uploader=uploader)

    assert result.success is True
    assert result.metrics.total_images_uploaded == 4
    assert result.upload_summary is not None
    assert result.upload_summary.successful_uploads == 4

    # Verify that products have blob URLs assigned
    assert result.products[0].variants[0].primary_image_url.startswith("https://blob.manastore.internal")


def test_pipeline_e2e_deterministic_repeated_execution(tmp_path: Path, sample_generic_data):
    """Verify repeated execution on identical input produces identical deterministic output."""
    data_file, img_dir = sample_generic_data
    out_dir1 = tmp_path / "run_1"
    out_dir2 = tmp_path / "run_2"

    cfg1 = PipelineConfig(input_path=data_file, output_dir=out_dir1, images_path=img_dir, export_formats=["json"])
    cfg2 = PipelineConfig(input_path=data_file, output_dir=out_dir2, images_path=img_dir, export_formats=["json"])

    res1 = PipelineOrchestrator.run(cfg1)
    res2 = PipelineOrchestrator.run(cfg2)

    assert res1.success and res2.success
    assert res1.metrics == res2.metrics

    json1 = json.loads((out_dir1 / "canonical_products.json").read_text(encoding="utf-8"))
    json2 = json.loads((out_dir2 / "canonical_products.json").read_text(encoding="utf-8"))

    assert json1["total_products"] == json2["total_products"]
    assert json1["total_variants"] == json2["total_variants"]

    # Verify variant structure and data match exactly
    for p1, p2 in zip(json1["products"], json2["products"]):
        assert p1["catalog_name"] == p2["catalog_name"]
        assert p1["product_name"] == p2["product_name"]
        assert len(p1["variants"]) == len(p2["variants"])
        for v1, v2 in zip(p1["variants"], p2["variants"]):
            assert v1["variant_code"] == v2["variant_code"]
            assert v1["supplier_sku"] == v2["supplier_sku"]
            assert v1["variant_name"] == v2["variant_name"]
            assert v1["color"] == v2["color"]
            assert v1["size"] == v2["size"]
            assert v1["supplier_cost"] == v2["supplier_cost"]
            assert len(v1["images"]) == len(v2["images"])


def test_pipeline_e2e_cli_subprocess(tmp_path: Path, sample_generic_data):
    """Test CLI end-to-end execution via subprocess."""
    data_file, img_dir = sample_generic_data
    out_dir = tmp_path / "cli_output"

    cmd = [
        sys.executable,
        "main.py",
        "--input", str(data_file),
        "--images", str(img_dir),
        "--output", str(out_dir),
        "--format", "json,xlsx",
        "--json",
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0

    stdout_json = json.loads(proc.stdout)
    assert stdout_json["success"] is True
    assert stdout_json["metrics"]["total_products"] == 2
    assert stdout_json["metrics"]["total_variants"] == 4
    assert (out_dir / "canonical_products.json").exists()
