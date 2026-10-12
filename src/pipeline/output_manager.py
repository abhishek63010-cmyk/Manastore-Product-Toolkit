from pathlib import Path
from typing import Any, Optional, Union
import csv


class OutputManager:
    """
    Centralized output-path manager for the Manastore Product Toolkit pipeline.

    Enforces the streamlined output folder layout:
    - Root:
        - amra_data_ready.xlsx: The single final admin-import workbook.
        - RUN_SUMMARY.txt: Clear instructions identifying the import file.
    - _internal/:
        - canonical_products.xlsx, canonical_products.json, canonical_products.csv
        - raw_supplier_extracted.tsv
        - refined_products.xlsx
        - mana_store_import_ready.xlsx
        - image_approval_manifest.json
    - _reports/:
        - validation_report.xlsx, validation_report.json
        - processing summary logs
    """

    INTERNAL_DIR_NAME = "_internal"
    REPORTS_DIR_NAME = "_reports"
    FINAL_WORKBOOK_NAME = "amra_data_ready.xlsx"
    SUMMARY_FILE_NAME = "RUN_SUMMARY.txt"

    def __init__(self, root_dir: Union[str, Path]):
        self.root_dir = Path(root_dir)
        self.internal_dir = self.root_dir / self.INTERNAL_DIR_NAME
        self.reports_dir = self.root_dir / self.REPORTS_DIR_NAME

    def ensure_directories(self) -> None:
        """Create the root, _internal, and _reports directories."""
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.internal_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    # --- Target Paths ---

    @property
    def final_amra_workbook_path(self) -> Path:
        """The single final admin-import workbook path at root."""
        return self.root_dir / self.FINAL_WORKBOOK_NAME

    @property
    def run_summary_path(self) -> Path:
        """Path for RUN_SUMMARY.txt at root."""
        return self.root_dir / self.SUMMARY_FILE_NAME

    @property
    def canonical_xlsx_path(self) -> Path:
        return self.internal_dir / "canonical_products.xlsx"

    @property
    def canonical_json_path(self) -> Path:
        return self.internal_dir / "canonical_products.json"

    @property
    def canonical_csv_path(self) -> Path:
        return self.internal_dir / "canonical_products.csv"

    @property
    def raw_extracted_tsv_path(self) -> Path:
        return self.internal_dir / "raw_supplier_extracted.tsv"

    @property
    def refined_xlsx_path(self) -> Path:
        return self.internal_dir / "refined_products.xlsx"

    @property
    def mana_store_import_xlsx_path(self) -> Path:
        return self.internal_dir / "mana_store_import_ready.xlsx"

    @property
    def manifest_json_path(self) -> Path:
        return self.internal_dir / "image_approval_manifest.json"

    @property
    def validation_report_xlsx_path(self) -> Path:
        return self.reports_dir / "validation_report.xlsx"

    @property
    def validation_report_json_path(self) -> Path:
        return self.reports_dir / "validation_report.json"

    def write_run_summary(
        self,
        adapter_used: str,
        total_products: int,
        total_variants: int,
        validation_status: str,
    ) -> Path:
        """
        Create RUN_SUMMARY.txt in the run folder root explaining the folder layout.
        """
        lines = [
            "================================================================================",
            f"FINAL ADMIN IMPORT FILE: {self.FINAL_WORKBOOK_NAME}",
            "================================================================================",
            "",
            "NOTICE FOR CATALOG MANAGERS / ADMINS:",
            f"1. Please upload ONLY '{self.FINAL_WORKBOOK_NAME}' directly to the admin store importer.",
            "2. Files inside '_internal/' and '_reports/' are supporting artifacts and diagnostics.",
            "   DO NOT upload files from '_internal/' or '_reports/' to the admin importer.",
            "",
            "RUN DETAILS:",
            f"- Supplier Adapter:   {adapter_used}",
            f"- Products Extracted: {total_products}",
            f"- Variants Created:   {total_variants}",
            f"- Validation Status:  {validation_status}",
            "",
            "FOLDER CONTENTS:",
            f"- {self.FINAL_WORKBOOK_NAME} : Single production-ready workbook for MANA Store / Amra.",
            "- _internal/           : Raw extracted TSV, canonical models (JSON/XLSX), internal audits.",
            "- _reports/            : Data validation logs, issue manifests, and matching audits.",
            "================================================================================",
        ]
        summary_file = self.run_summary_path
        summary_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return summary_file

    def write_raw_extracted_tsv(self, raw_data: Any) -> Optional[Path]:
        """
        Save the unmapped raw extracted records as a TSV in _internal/ for auditing.
        """
        if not raw_data or not raw_data.records:
            return None

        tsv_path = self.raw_extracted_tsv_path
        headers = list(raw_data.headers)
        if not headers:
            # Collect from records
            all_keys = []
            for r in raw_data.records:
                for k in r.data.keys():
                    if k not in all_keys:
                        all_keys.append(k)
            headers = all_keys

        try:
            with open(tsv_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerow(headers)
                for record in raw_data.records:
                    row = [record.data.get(h, "") for h in headers]
                    writer.writerow(row)
            return tsv_path
        except Exception:
            return None
