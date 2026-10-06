import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from src.extractors.image_inventory import (
    extract_zip_images,
    inspect_folder,
    inspect_zip,
)
from src.extractors.supplier_data import extract_supplier_data
from src.images.product_images import map_approved_image_urls
from src.images.product_images import export_final_products
from src.mappers.mana_store_mapper import map_to_manastore
from src.mappers.product_refiner import refine_products
from src.validators.product_validator import validate_products
from main import run_pipeline


ROOT = Path(__file__).parent


class SupplierPipelineTests(unittest.TestCase):
    def test_amra_compatibility_fixture(self):
        raw = extract_supplier_data(ROOT / "fixtures" / "amra_compat.csv")
        mapped = map_to_manastore(raw)

        self.assertEqual(mapped.loc[0, "supplier_sku"], "AM-001")
        self.assertEqual(mapped.loc[0, "fabric"], "Cotton")
        self.assertEqual(mapped.loc[0, "product_group"], "")
        self.assertFalse(mapped.loc[0, "publish"])
        self.assertIn("manual_review", mapped.loc[0, "field_provenance"])

    def test_different_csv_shape_and_unmapped_provenance(self):
        raw = extract_supplier_data(ROOT / "fixtures" / "other_supplier.csv")
        mapped = map_to_manastore(raw)
        refined, _ = refine_products(mapped)

        self.assertEqual(mapped.loc[0, "supplier_sku"], "ZX-204")
        self.assertEqual(mapped.loc[0, "product_name"], "Printed tunic")
        self.assertIn("Blue floral", mapped.loc[0, "source_fields"])
        self.assertEqual(refined.loc[0, "product_name"], "Printed tunic")
        self.assertFalse(refined.loc[0, "publish"])
        findings = validate_products(mapped)
        self.assertFalse(findings["severity"].eq("ERROR").any())
        self.assertTrue(findings["field"].eq("category_slug").any())

    def test_xlsx_extraction_requires_only_supplier_sku(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "supplier.xlsx"
            with pd.ExcelWriter(path) as writer:
                pd.DataFrame({"Title": ["Cover"]}).to_excel(
                    writer, sheet_name="Cover", index=False
                )
                pd.DataFrame({
                    "Style Code": ["0007"],
                    "Custom Detail": ["kept"],
                }).to_excel(writer, sheet_name="Products", index=False)

            extracted = extract_supplier_data(path)

        self.assertEqual(extracted.loc[0, "supplier_sku"], "0007")
        self.assertEqual(extracted.loc[0, "custom_detail"], "kept")

    def test_key_value_tsv_and_secret_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "supplier.tsv"
            path.write_text(
                "Column\tValue\nSupplier SKU\tABC-1\nAPI Token\tdo-not-export\n",
                encoding="utf-8",
            )
            extracted = extract_supplier_data(path)

        self.assertEqual(extracted.loc[0, "supplier_sku"], "ABC-1")
        self.assertEqual(extracted.loc[0, "api_token"], "[REDACTED]")

    def test_missing_sku_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "supplier.csv"
            path.write_text("description\nno identifier\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "supplier_sku"):
                extract_supplier_data(path)

    def test_zip_and_folder_inventory_match_by_sku(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_dir = root / "approved"
            image_dir.mkdir()
            (image_dir / "ZX-204_front.jpg").write_bytes(b"image")
            (image_dir / "not-an-image.exe").write_bytes(b"data")
            folder_report = inspect_folder(image_dir, ["ZX-204"])

            archive_path = root / "raw.zip"
            with ZipFile(archive_path, "w") as archive:
                archive.writestr("ZX-204_back.png", b"image")
                archive.writestr("unrelated.jpg", b"image")
                archive.writestr("unsupported.gif", b"image")
                archive.writestr("../escape.png", b"image")
            zip_report = inspect_zip(archive_path, ["ZX-204"])
            extracted_report = extract_zip_images(
                archive_path, root / "extracted", ["ZX-204"]
            )
            extracted_exists = Path(
                extracted_report.loc[0, "extracted_path"]
            ).is_file()
            traversal_blocked = not (root / "escape.png").exists()

        self.assertEqual(folder_report.loc[0, "supplier_sku"], "ZX-204")
        self.assertEqual(folder_report.loc[1, "status"], "UNSUPPORTED_FORMAT")
        self.assertEqual(zip_report.loc[0, "supplier_sku"], "ZX-204")
        self.assertEqual(zip_report.loc[1, "status"], "UNMATCHED")
        self.assertEqual(zip_report.loc[2, "status"], "UNSUPPORTED_FORMAT")
        self.assertTrue(extracted_exists)
        self.assertEqual(extracted_report.loc[3, "status"], "INVALID_PATH")
        self.assertTrue(traversal_blocked)

    def test_actual_uploaded_urls_map_only_to_exact_sku_and_order(self):
        products = pd.DataFrame({"supplier_sku": ["A-1", "A-10"]})
        images = [
            {
                "supplier_sku": "A-1", "image_id": "side",
                "image_index": 1, "blob_url": "https://blob.test/side",
            },
            {
                "supplier_sku": "A-1", "image_id": "front",
                "image_index": 0, "blob_url": "https://blob.test/front",
            },
            {
                "supplier_sku": "A-10", "image_id": "only",
                "image_index": 0, "blob_url": "https://blob.test/other",
            },
            {
                "supplier_sku": "UNKNOWN", "image_id": "extra",
                "image_index": 0, "blob_url": "https://blob.test/unmatched",
            },
        ]

        mapped, report = map_approved_image_urls(products, images)
        self.assertEqual(
            mapped.loc[0, "image_url"], "https://blob.test/front"
        )
        self.assertIn("https://blob.test/side", mapped.loc[0, "image_urls"])
        self.assertEqual(
            mapped.loc[1, "image_url"], "https://blob.test/other"
        )
        self.assertTrue(report["status"].eq("UNMATCHED_SKU").any())
        with self.assertRaisesRegex(ValueError, "unique supplier_sku"):
            map_approved_image_urls(
                pd.DataFrame({"supplier_sku": ["A-1", "A-1"]}), images
            )

    def test_final_export_requires_actual_urls_and_supports_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "final.csv"
            products = pd.DataFrame({
                "supplier_sku": ["SKU-1"],
                "image_url": ["https://blob.test/actual-upload"],
                "publish": [True],
            })
            export_final_products(products, path)
            exported = pd.read_csv(path)

            with self.assertRaisesRegex(ValueError, "missing"):
                export_final_products(
                    pd.DataFrame({"supplier_sku": ["SKU-2"], "image_url": [""]}),
                    path,
                )

        self.assertEqual(
            exported.loc[0, "image_url"], "https://blob.test/actual-upload"
        )
        self.assertFalse(exported.loc[0, "publish"])

    def test_validator_requires_draft_state(self):
        findings = validate_products(pd.DataFrame({"supplier_sku": ["SKU-1"]}))
        draft_errors = findings.loc[
            findings["field"].eq("publish"), "severity"
        ]
        self.assertEqual(draft_errors.tolist(), ["ERROR"])

    def test_pipeline_generates_review_exports_without_image_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = root / "output"
            reports = root / "reports"
            _, _, refined, findings, _ = run_pipeline(
                ROOT / "fixtures" / "other_supplier.csv", outputs, reports
            )

            self.assertTrue((outputs / "product_refinement_review.csv").exists())
            self.assertEqual(refined.loc[0, "image_url"], "")
            self.assertFalse(refined.loc[0, "publish"])
            self.assertTrue(findings["severity"].eq("WARNING").all())
            self.assertGreater(len(findings), 5)


if __name__ == "__main__":
    unittest.main()
