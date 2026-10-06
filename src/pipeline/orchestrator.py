from pathlib import Path
from typing import Optional, Union
import logging

from src.adapters.registry import AdapterRegistry
from src.exporters.canonical import CanonicalExport
from src.exporters.writers import OutputWriter
from src.extractors.tabular import GenericTabularExtractor
from src.images.inventory import GenericImageInventory
from src.images.matcher import DeterministicImageMatcher, ImageMatchReport
from src.images.uploader import ImageUploadService, ImageUploadSummary
from src.models.product import Product
from src.models.validation import ValidationReport
from src.pipeline.config import PipelineConfig
from src.pipeline.result import PipelineMetrics, PipelineResult
from src.uploaders.base import BaseBlobUploader
from src.uploaders.vercel_blob import VercelBlobUploader
from src.validators.generic import GenericProductValidator

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    Unified end-to-end orchestrator for the MANA Store product import pipeline.
    
    Coordinates:
    1. Ingestion: Generic tabular extraction (CSV, TSV, XLSX)
    2. Adapter: Supplier normalization via AdapterRegistry
    3. Images: Inventory discovery and deterministic matching
    4. Upload: Optional Blob storage upload via pluggable uploader
    5. Validation: Multi-level quality gate verification
    6. Export: Canonical domain export to XLSX, JSON, and CSV
    7. Reporting: Structured validation and audit report generation
    """

    @classmethod
    def run(
        cls,
        config: PipelineConfig,
        uploader: Optional[BaseBlobUploader] = None,
    ) -> PipelineResult:
        """
        Execute the product ingestion pipeline end-to-end.
        """
        if config.verbose:
            print(f"[Pipeline] Starting execution with input: {config.input_path}")

        # 1. Extraction
        try:
            raw_data = GenericTabularExtractor.extract(
                file_path=config.input_path,
                sheet_name=config.sheet_name,
            )
            if config.verbose:
                print(f"[Pipeline] Extracted {raw_data.total_rows} rows from '{raw_data.source_file}'")
        except Exception as e:
            return PipelineResult(
                success=False,
                error_message=f"Extraction failed: {str(e)}",
            )

        # 2. Supplier Adapter Selection & Normalization
        try:
            adapter = AdapterRegistry.get_adapter(
                supplier_name=config.supplier,
                raw_data=raw_data,
            )
            if config.verbose:
                print(f"[Pipeline] Using adapter: '{adapter.adapter_name}'")

            products: list[Product] = adapter.transform(raw_data)
            if config.verbose:
                total_vars = sum(len(p.variants) for p in products)
                print(f"[Pipeline] Normalized {len(products)} products ({total_vars} variants)")
        except Exception as e:
            return PipelineResult(
                success=False,
                error_message=f"Adapter transformation failed: {str(e)}",
            )

        # 3. Image Discovery & Deterministic Matching
        match_report: Optional[ImageMatchReport] = None
        if config.images_path and Path(config.images_path).exists():
            if config.verbose:
                print(f"[Pipeline] Discovering images in: {config.images_path}")
            inventory = GenericImageInventory.from_source(config.images_path)
            if config.verbose:
                print(f"[Pipeline] Discovered {inventory.total_images} image assets")

            match_report = DeterministicImageMatcher.match(products, inventory)
            if config.verbose:
                print(
                    f"[Pipeline] Matched: {match_report.matched_count}, "
                    f"Ambiguous: {match_report.ambiguous_count}, "
                    f"Unmatched: {match_report.unmatched_count}"
                )

        # 4. Optional Image Uploading
        upload_summary: Optional[ImageUploadSummary] = None
        if config.upload_images and match_report is not None and not config.dry_run:
            active_uploader = uploader or VercelBlobUploader(prefix=config.upload_prefix)
            if config.verbose:
                print(f"[Pipeline] Uploading matched images via '{active_uploader.uploader_type}'...")
            
            upload_summary = ImageUploadService.upload_matched_images(
                products=products,
                match_report=match_report,
                uploader=active_uploader,
                prefix=config.upload_prefix,
            )
            if config.verbose:
                print(
                    f"[Pipeline] Uploaded: {upload_summary.successful_uploads}, "
                    f"Failed: {upload_summary.failed_count}, "
                    f"Skipped: {upload_summary.skipped_count}"
                )

        # 5. Generic Validation Gate
        if config.verbose:
            print("[Pipeline] Running generic product validation...")
        validation_report: ValidationReport = GenericProductValidator.validate_products(products)
        if config.verbose:
            print(
                f"[Pipeline] Validation: is_valid={validation_report.is_valid}, "
                f"errors={len(validation_report.errors())}, "
                f"warnings={len(validation_report.warnings())}"
            )

        # 6. Canonical Export Assembly
        export_data = CanonicalExport.from_products(
            products=products,
            validation_report=validation_report,
        )

        # 7. Output Artifact Writing (if not dry_run)
        output_files: dict[str, str] = {}
        if not config.dry_run:
            out_dir = Path(config.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)

            for fmt in config.export_formats:
                fmt_clean = fmt.lower().strip()
                if fmt_clean == "xlsx":
                    p = OutputWriter.write_canonical_xlsx(
                        export_data=export_data,
                        output_path=out_dir / "canonical_products.xlsx",
                        max_gallery_columns=config.max_gallery_columns,
                    )
                    output_files["xlsx"] = str(p)
                elif fmt_clean == "json":
                    p = OutputWriter.write_canonical_json(
                        export_data=export_data,
                        output_path=out_dir / "canonical_products.json",
                    )
                    output_files["json"] = str(p)
                elif fmt_clean == "csv":
                    p = OutputWriter.write_canonical_csv(
                        export_data=export_data,
                        output_path=out_dir / "canonical_products.csv",
                        max_gallery_columns=config.max_gallery_columns,
                    )
                    output_files["csv"] = str(p)

            if config.generate_report:
                p_rep_xlsx = OutputWriter.write_validation_report_xlsx(
                    report=validation_report,
                    output_path=out_dir / "validation_report.xlsx",
                    match_report=match_report,
                    upload_results=upload_summary.results if upload_summary else None,
                )
                output_files["validation_report_xlsx"] = str(p_rep_xlsx)

                p_rep_json = OutputWriter.write_validation_report_json(
                    report=validation_report,
                    output_path=out_dir / "validation_report.json",
                    match_report=match_report,
                    upload_results=upload_summary.results if upload_summary else None,
                )
                output_files["validation_report_json"] = str(p_rep_json)

        # 8. Compute Metrics & Success Flag
        has_val_errors = validation_report.has_errors
        is_success = True
        error_msg = None

        if config.fail_on_error and has_val_errors:
            is_success = False
            error_msg = f"Validation failed with {len(validation_report.errors())} error(s)"

        metrics = PipelineMetrics(
            total_source_rows=raw_data.total_rows,
            total_products=export_data.total_products,
            total_variants=export_data.total_variants,
            total_images_discovered=match_report.total_images if match_report else 0,
            total_images_matched=match_report.matched_count if match_report else 0,
            total_images_uploaded=upload_summary.successful_uploads if upload_summary else 0,
            total_validation_errors=len(validation_report.errors()),
            total_validation_warnings=len(validation_report.warnings()),
            is_valid=validation_report.is_valid,
            success=is_success,
        )

        return PipelineResult(
            success=is_success,
            adapter_used=adapter.adapter_name,
            metrics=metrics,
            output_files=output_files,
            products=products,
            export_data=export_data,
            validation_report=validation_report,
            match_report=match_report,
            upload_summary=upload_summary,
            error_message=error_msg,
        )
