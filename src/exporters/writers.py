import json
from pathlib import Path
from typing import Any, Optional, Union
import pandas as pd
import openpyxl

from src.exporters.canonical import CanonicalExport
from src.images.matcher import ImageMatchReport
from src.models.validation import ValidationReport
from src.uploaders.base import UploadResult


class OutputWriter:
    """
    Writer service responsible for serializing canonical product datasets
    and validation reports into XLSX, JSON, and CSV formats.
    """

    @classmethod
    def write_canonical_xlsx(
        cls,
        export_data: CanonicalExport,
        output_path: Union[str, Path],
        max_gallery_columns: int = 10,
    ) -> Path:
        """
        Export canonical products and variants to a structured Excel spreadsheet.
        """
        target = Path(output_path)
        if target.is_dir() or target.suffix == "":
            target.mkdir(parents=True, exist_ok=True)
            target = target / "canonical_products.xlsx"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

        rows = export_data.to_flat_records()
        if not rows:
            # Create an empty dataframe with standard columns
            df = pd.DataFrame(columns=[
                "supplier_name", "catalog_name", "product_name",
                "variant_name", "variant_code", "supplier_sku",
                "primary_image_url", "selling_price"
            ])
        else:
            df = pd.DataFrame(rows)

        # Reorder standard columns to the front if they exist
        front_cols = [
            "supplier_name",
            "catalog_name",
            "product_group",
            "product_name",
            "variant_name",
            "variant_code",
            "supplier_sku",
            "image_sku",
            "product_type",
            "category_slug",
            "brand_name",
            "color",
            "size",
            "description",
            "supplier_price",
            "supplier_cost",
            "gst_percent",
            "selling_price",
            "stock_quantity",
            "publish",
            "review_status",
            "primary_image_url",
            "image_url",
        ]
        image_cols = [f"image{i}_url" for i in range(1, max_gallery_columns + 1)]
        
        existing_front = [c for c in front_cols if c in df.columns]
        existing_images = [c for c in image_cols if c in df.columns]
        remaining = [c for c in df.columns if c not in existing_front and c not in existing_images]
        
        ordered_cols = existing_front + existing_images + remaining
        df = df[ordered_cols]

        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Products", index=False)

        return target.resolve()

    @classmethod
    def write_canonical_json(
        cls,
        export_data: CanonicalExport,
        output_path: Union[str, Path],
    ) -> Path:
        """
        Export canonical dataset to structured JSON.
        """
        target = Path(output_path)
        if target.is_dir() or target.suffix == "":
            target.mkdir(parents=True, exist_ok=True)
            target = target / "canonical_products.json"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

        target.write_text(
            json.dumps(export_data.model_dump(), indent=2, default=str),
            encoding="utf-8",
        )
        return target.resolve()

    @classmethod
    def write_canonical_csv(
        cls,
        export_data: CanonicalExport,
        output_path: Union[str, Path],
        max_gallery_columns: int = 10,
    ) -> Path:
        """
        Export canonical products to CSV format.
        """
        target = Path(output_path)
        if target.is_dir() or target.suffix == "":
            target.mkdir(parents=True, exist_ok=True)
            target = target / "canonical_products.csv"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

        rows = export_data.to_flat_records()
        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        df.to_csv(target, index=False, encoding="utf-8")
        return target.resolve()

    @classmethod
    def write_validation_report_xlsx(
        cls,
        report: ValidationReport,
        output_path: Union[str, Path],
        match_report: Optional[ImageMatchReport] = None,
        upload_results: Optional[list[UploadResult]] = None,
    ) -> Path:
        """
        Write a detailed multi-tab Excel validation and pipeline audit report.
        """
        target = Path(output_path)
        if target.is_dir() or target.suffix == "":
            target.mkdir(parents=True, exist_ok=True)
            target = target / "validation_report.xlsx"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            # 1. Summary Sheet
            summary_data = [
                {"Metric": "Overall Status", "Value": "PASSED" if report.is_valid else "FAILED"},
                {"Metric": "Total Products", "Value": report.total_products},
                {"Metric": "Total Variants", "Value": report.total_variants},
                {"Metric": "Validation Errors", "Value": len(report.errors())},
                {"Metric": "Validation Warnings", "Value": len(report.warnings())},
                {"Metric": "Validation Infos", "Value": len(report.infos())},
            ]
            if match_report:
                summary_data.extend([
                    {"Metric": "Total Images Discovered", "Value": match_report.total_images},
                    {"Metric": "Images Matched", "Value": match_report.matched_count},
                    {"Metric": "Images Ambiguous", "Value": match_report.ambiguous_count},
                    {"Metric": "Images Unmatched", "Value": match_report.unmatched_count},
                ])
            if upload_results is not None:
                summary_data.extend([
                    {"Metric": "Total Uploads Attempted", "Value": len(upload_results)},
                    {"Metric": "Successful Uploads", "Value": sum(1 for r in upload_results if r.status.value == "UPLOADED")},
                    {"Metric": "Failed Uploads", "Value": sum(1 for r in upload_results if r.status.value == "FAILED")},
                ])

            df_summary = pd.DataFrame(summary_data)
            df_summary.to_excel(writer, sheet_name="Summary", index=False)

            # 2. Validation Issues Sheet
            issues_data = []
            for issue in report.issues:
                issues_data.append({
                    "Severity": issue.severity.value,
                    "Code": issue.code,
                    "Message": issue.message,
                    "Field": issue.field or "",
                    "Supplier SKU": issue.supplier_sku or "",
                    "Variant Code": issue.variant_code or "",
                    "Source Row": issue.source_row or "",
                })
            df_issues = pd.DataFrame(issues_data) if issues_data else pd.DataFrame(
                columns=["Severity", "Code", "Message", "Field", "Supplier SKU", "Variant Code", "Source Row"]
            )
            df_issues.to_excel(writer, sheet_name="Validation_Issues", index=False)

            # 3. Image Matching Sheet
            if match_report:
                matches_data = []
                for finding in match_report.findings:
                    matches_data.append({
                        "Image ID": finding.image.image_id,
                        "Filename": finding.image.original_filename,
                        "Status": finding.status.value,
                        "Matched Variant Code": finding.matched_variant_code or "",
                        "Matched Supplier SKU": finding.matched_supplier_sku or "",
                        "Strategy": finding.match_strategy or "",
                        "Confidence": finding.confidence,
                        "Reason": finding.reason or "",
                    })
                df_matches = pd.DataFrame(matches_data) if matches_data else pd.DataFrame(
                    columns=["Image ID", "Filename", "Status", "Matched Variant Code", "Matched Supplier SKU", "Strategy", "Confidence", "Reason"]
                )
                df_matches.to_excel(writer, sheet_name="Image_Matching", index=False)


            # 4. Upload Results Sheet
            if upload_results is not None:
                uploads_data = []
                for res in upload_results:
                    uploads_data.append({
                        "Status": res.status.value,
                        "Destination Key": res.destination_key,
                        "Blob URL": res.blob_url or "",
                        "File Size (Bytes)": res.file_size_bytes,
                        "Idempotent Reuse": res.is_idempotent_reuse,
                        "Error": res.error_message or "",
                    })
                df_uploads = pd.DataFrame(uploads_data) if uploads_data else pd.DataFrame(
                    columns=["Status", "Destination Key", "Blob URL", "File Size (Bytes)", "Idempotent Reuse", "Error"]
                )
                df_uploads.to_excel(writer, sheet_name="Upload_Results", index=False)

        return target.resolve()

    @classmethod
    def write_validation_report_json(
        cls,
        report: ValidationReport,
        output_path: Union[str, Path],
        match_report: Optional[ImageMatchReport] = None,
        upload_results: Optional[list[UploadResult]] = None,
    ) -> Path:
        """
        Write validation findings and metadata to JSON format.
        """
        target = Path(output_path)
        if target.is_dir() or target.suffix == "":
            target.mkdir(parents=True, exist_ok=True)
            target = target / "validation_report.json"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {
            "validation_summary": report.summary(),
            "issues": [issue.model_dump() for issue in report.issues],
        }

        if match_report:
            data["image_matching_summary"] = match_report.summary()
            data["image_findings"] = [f.model_dump() for f in match_report.findings]

        if upload_results is not None:
            data["upload_results"] = [r.model_dump() for r in upload_results]

        target.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        return target.resolve()
