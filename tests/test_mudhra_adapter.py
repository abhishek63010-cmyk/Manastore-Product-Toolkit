import json
import shutil
import subprocess
from pathlib import Path

from src.adapters.mudhra_ethnic import MudhraEthnicAdapter
from src.adapters.registry import AdapterRegistry
from src.extractors.raw_record import RawRecord, RawTabularData

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_BATCH_SCRIPT = PROJECT_ROOT / "scripts" / "run-batch.ps1"


def test_mudhra_ethnic_folder_recognition_uses_custom_adapter(tmp_path):
    source = tmp_path / "Mudhra_Ethnic" / "catalogue" / "catalog.xlsx"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("placeholder", encoding="utf-8")

    raw_data = RawTabularData(
        source_file=str(source),
        headers=["Collection", "Style", "Fabric", "Work", "Blouse", "Price"],
        records=[
            RawRecord(
                row_number=1,
                source_file=str(source),
                data={
                    "Collection": "Aaranya",
                    "Style": "Anokhi Kurta",
                    "Fabric": "Cotton",
                    "Work": "Embroidery",
                    "Blouse": "Contrast",
                    "Price": "₹799",
                },
            )
        ],
    )

    adapter = AdapterRegistry.get_adapter(raw_data=raw_data, source_path=str(source))

    assert isinstance(adapter, MudhraEthnicAdapter)
    assert adapter.adapter_name == "mudhra_ethnic"


def test_mudhra_ethnic_transform_extracts_price_and_collection():
    source = Path("input/Mudhra_Ethnic/Aaranya_Test/aurora.xlsx")
    raw_data = RawTabularData(
        source_file=str(source),
        headers=["Collection", "Product Name", "Brand", "Fabric", "Work", "Blouse", "Price"],
        records=[
            RawRecord(
                row_number=1,
                source_file=str(source),
                data={
                    "Collection": "Aaranya",
                    "Product Name": "Aaranya Silk Kurta",
                    "Brand": "Mudhra Ethnic",
                    "Fabric": "Silk",
                    "Work": "Zari",
                    "Blouse": "Contrast",
                    "Price": "₹799",
                },
            )
        ],
    )

    products = MudhraEthnicAdapter().transform(raw_data)

    assert len(products) == 1
    product = products[0]
    assert product.catalog_name == "Aaranya"
    assert product.product_name == "Aaranya Silk Kurta"
    assert product.supplier_price == 799.0
    assert product.attributes.get("fabric") == "Silk"
    assert product.attributes.get("work") == "Zari"


def test_supplier_root_resolution_uses_ancestor_supplier_folder():
    amra_source = PROJECT_ROOT / "input" / "Amra_wholesale" / "Nested" / "Folder" / "Test_Catalogue" / "primary.xlsx"
    mudhra_source = PROJECT_ROOT / "input" / "Mudhra_Ethnic" / "Nested" / "Folder" / "Test_Catalogue" / "primary.xlsx"

    assert AdapterRegistry.detect_supplier_from_path(amra_source) == "amra_wholesale"
    assert AdapterRegistry.detect_supplier_from_path(mudhra_source) == "mudhra_ethnic"
    assert AdapterRegistry.detect_supplier_from_path(PROJECT_ROOT / "input" / "Unknown" / "Catalogue" / "primary.xlsx") is None


def test_batch_runner_auto_discovers_supplier_roots_without_manifest_entries(tmp_path):
    batch_name = "AutoDiscoveryFixture"
    amra_dir = PROJECT_ROOT / "input" / "Amra_wholesale" / "Test_Catalogue"
    mudhra_dir = PROJECT_ROOT / "input" / "Mudhra_Ethnic" / "Test_Catalogue"

    for catalog_dir in (amra_dir, mudhra_dir):
        catalog_dir.mkdir(parents=True, exist_ok=True)
        (catalog_dir / "catalogue.csv").write_text(
            "Collection,Product Name,Brand,Fabric,Work,Blouse,Price\nAaranya,Test Product,Mudhra Ethnic,Silk,Zari,Contrast,799\n",
            encoding="utf-8",
        )

    manifest_file = tmp_path / "empty_manifest.json"
    manifest_file.write_text(json.dumps({"batchName": batch_name, "catalogues": []}), encoding="utf-8")

    try:
        proc = subprocess.run(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(RUN_BATCH_SCRIPT),
                "-ManifestPath",
                str(manifest_file),
                "-ValidateOnly",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Auto-Discovered Input Catalogues:" in proc.stdout
        assert "Amra_wholesale_Test_Catalogue" in proc.stdout
        assert "Mudhra_Ethnic_Test_Catalogue" in proc.stdout
        assert "Manifest validation: PASSED" in proc.stdout
    finally:
        for catalog_dir in (amra_dir, mudhra_dir):
            if catalog_dir.exists():
                shutil.rmtree(catalog_dir)
