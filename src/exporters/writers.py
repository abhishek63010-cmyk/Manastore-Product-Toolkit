import json
from pathlib import Path
from typing import Any, Optional, Union
import pandas as pd
import openpyxl

from src.exporters.canonical import CanonicalExport
from src.images.matcher import ImageMatchReport
from src.models.product import Product
from src.models.refined import RefinedExport
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

        cls._save_safe_xlsx(df, target, sheet_name="Products")

        return target.resolve()

    @classmethod
    def _save_safe_xlsx(cls, df: pd.DataFrame, target: Path, sheet_name: str = "Sheet1") -> Path:
        """
        Save dataframe to Excel workbook ensuring literal text beginning with '=', '+', '-', or '@'
        is saved safely as text (data_type='s') to prevent accidental formula evaluation or #NAME? errors.
        """
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet_name

        cols = list(df.columns)
        ws.append(cols)

        import json
        formula_prefixes = ("=", "+", "-", "@")
        for row_vals in df.itertuples(index=False):
            safe_vals = []
            for val in row_vals:
                if isinstance(val, (dict, list)):
                    safe_vals.append(json.dumps(val, default=str))
                elif pd.isna(val):
                    safe_vals.append(None)
                else:
                    safe_vals.append(val)
            ws.append(safe_vals)
            row_idx = ws.max_row
            for col_idx, val in enumerate(safe_vals, start=1):
                if isinstance(val, str) and val.startswith(formula_prefixes):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.data_type = "s"

        wb.save(target)
        return target.resolve()

    @classmethod
    def write_amra_import_xlsx(
        cls,
        products: list[Product],
        output_path: Union[str, Path],
        images_path: Optional[Union[str, Path]] = None,
    ) -> Path:
        """
        Export final Amra-format workbook conforming strictly to the 26-column schema
        (matching Amra_data_with_updated_charm_prices.xlsx / Amra_data_with_all_blob_urls.xlsx).
        """
        from src.mappers.amra_transformer import AmraWorkbookTransformer
        return AmraWorkbookTransformer.export_amra_workbook(
            products=products,
            output_path=output_path,
            images_path=images_path,
        )

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

    @classmethod
    def write_refined_xlsx(
        cls,
        refined_data: RefinedExport,
        output_path: Union[str, Path],
    ) -> Path:
        """
        Export multi-sheet refined products workbook containing:
        1. Refined_Variants: All actual and derived variants with supplier facts, approved image identity, and OCR evidence.
        2. Review_Required: Filtered variants with flags, discrepancies, missing approved images, or validation issues.
        3. Audit_Summary: High-level metric counts for quality control.
        """
        target = Path(output_path)
        if target.is_dir() or target.suffix == "":
            target.mkdir(parents=True, exist_ok=True)
            target = target / "refined_products.xlsx"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

        rows = refined_data.to_flat_records()
        df_all = pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
            "catalog_name", "product_name", "variant_code", "variant_name",
            "supplier_sku", "has_approved_image", "ocr_status", "requires_review"
        ])

        rev_rows = refined_data.review_required_records()
        df_rev = pd.DataFrame(rev_rows) if rev_rows else pd.DataFrame(columns=df_all.columns)

        summary_data = [
            {"Metric": "Total Variants", "Count": refined_data.total_variants},
            {"Metric": "Variants with Approved Image", "Count": refined_data.approved_image_variants},
            {"Metric": "Variants with Successful OCR", "Count": refined_data.ocr_enriched_variants},
            {"Metric": "Variants with Uploaded Blob URL", "Count": refined_data.uploaded_variants},
            {"Metric": "Variants Requiring Review", "Count": refined_data.variants_requiring_review},
        ]
        df_summary = pd.DataFrame(summary_data)

        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            df_all.to_excel(writer, sheet_name="Refined_Variants", index=False)
            df_rev.to_excel(writer, sheet_name="Review_Required", index=False)
            df_summary.to_excel(writer, sheet_name="Audit_Summary", index=False)

        return target.resolve()

    @classmethod
    def write_mana_store_import_xlsx(
        cls,
        products: list[Product],
        output_path: Union[str, Path],
        max_gallery_columns: int = 5,
    ) -> Path:
        """
        Export the final MANA Store admin-import workbook conforming strictly
        to the website importer contract ('Import Ready' sheet).
        
        Guarantees:
        - Actual supplier SKU preserved separately from internal variant code.
        - main_image_url populated ONLY when a verified uploaded Blob URL exists.
        - publish is strictly False (never auto-publishes to the website).
        - Includes standard 'IMPORT_INSTRUCTIONS' worksheet.
        """
        target = Path(output_path)
        if target.is_dir() or target.suffix == "":
            target.mkdir(parents=True, exist_ok=True)
            target = target / "mana_store_import_ready.xlsx"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

        import_rows: list[dict[str, Any]] = []

        for prod in products:
            for var in prod.variants:
                # Determine verified Blob URL for primary image
                main_image_url = ""
                if var.primary_image_url and ("blob.vercel-storage.com" in var.primary_image_url or "blob" in var.primary_image_url):
                    main_image_url = var.primary_image_url
                elif var.images and any(img.blob_url for img in var.images):
                    main_image_url = next(img.blob_url for img in var.images if img.blob_url)

                # Filter gallery URLs for verified Blob URLs only
                gallery_blob_urls = [
                    u for u in var.gallery_image_urls
                    if u and ("blob.vercel-storage.com" in u or "blob" in u) and u != main_image_url
                ]

                row: dict[str, Any] = {
                    "supplier_name": prod.supplier_name,
                    "catalog_name": prod.catalog_name,
                    "product_group": prod.product_group or "",
                    "product_name": prod.product_name,
                    "variant_name": var.variant_name,
                    "supplier_sku": var.supplier_sku or "",  # Preserved separately from variant_code
                    "image_sku": var.image_sku or "",
                    "category_slug": prod.category_slug or "sarees",
                    "brand_name": prod.brand_name or "",
                    "fabric": prod.get_attribute("fabric") or var.attributes.get("fabric", ""),
                    "blouse_fabric": prod.get_attribute("blouse_fabric") or var.attributes.get("blouse_fabric", ""),
                    "work": prod.get_attribute("work") or var.attributes.get("work", ""),
                    "occasion": prod.get_attribute("occasion") or var.attributes.get("occasion", ""),
                    "size": var.size or prod.get_attribute("size", ""),
                    "description": prod.description or "",
                    "supplier_price": prod.supplier_price if prod.supplier_price is not None else (var.supplier_cost if var.supplier_cost is not None else ""),
                    "gst_percent": prod.gst_percent if prod.gst_percent is not None else "",
                    "selling_price": var.selling_price if var.selling_price is not None else "",
                    "publish": False,  # Never auto-publish
                    "main_image_url": main_image_url,
                }

                # Gallery columns
                for i in range(1, max_gallery_columns + 1):
                    col_key = f"image_{i}_url"
                    idx = i - 1
                    row[col_key] = gallery_blob_urls[idx] if idx < len(gallery_blob_urls) else ""

                import_rows.append(row)

        df_import = pd.DataFrame(import_rows) if import_rows else pd.DataFrame(columns=[
            "supplier_name", "catalog_name", "product_name", "variant_name",
            "supplier_sku", "selling_price", "publish", "main_image_url"
        ])

        # Instructions sheet
        instructions_data = [
            {"Step": "1. Review Pricing", "Guidance": "Verify and enter selling_price for each variant before publishing."},
            {"Step": "2. Verify Images", "Guidance": "Rows with empty main_image_url require image approval and upload before publishing."},
            {"Step": "3. Publishing Gate", "Guidance": "Keep publish column as FALSE until human review is complete."},
            {"Step": "4. Import to Admin", "Guidance": "Upload this spreadsheet into MANA Store Admin Import Interface."},
        ]
        df_instructions = pd.DataFrame(instructions_data)

        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            df_import.to_excel(writer, sheet_name="Import Ready", index=False)
            df_instructions.to_excel(writer, sheet_name="IMPORT_INSTRUCTIONS", index=False)

        return target.resolve()

