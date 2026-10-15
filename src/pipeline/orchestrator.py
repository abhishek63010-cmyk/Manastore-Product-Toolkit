from pathlib import Path
from typing import Optional, Union
import logging

from src.adapters.registry import AdapterRegistry
from src.exporters.canonical import CanonicalExport
from src.exporters.writers import OutputWriter
from src.extractors.tabular import GenericTabularExtractor
from src.images.inventory import GenericImageInventory
from src.images.manifest import ImageApprovalManifest
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
            from src.extractors.image import ImageCatalogueExtractor
            input_p = Path(config.input_path)
            if ImageCatalogueExtractor.is_supported(input_p):
                if config.verbose:
                    print(f"[Pipeline] Processing catalogue document via OCR/parser: '{input_p.name}'")
                raw_data = ImageCatalogueExtractor.extract(
                    file_path=input_p,
                    ocr_provider=config.ocr_provider,
                )
            else:
                raw_data = GenericTabularExtractor.extract(
                    file_path=config.input_path,
                    sheet_name=config.sheet_name,
                )
            if config.verbose:
                print(f"[Pipeline] Extracted {raw_data.total_rows} rows from '{raw_data.source_file}'")
        except Exception as e:
            return PipelineResult(
                success=False,
                metrics=PipelineMetrics(is_valid=False, success=False),
                error_message=f"Extraction failed: {str(e)}",
            )

        # 2. Supplier Adapter Selection & Normalization
        try:
            adapter = AdapterRegistry.get_adapter(
                supplier_name=config.supplier,
                raw_data=raw_data,
                source_path=config.input_path,
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
                metrics=PipelineMetrics(is_valid=False, success=False),
                error_message=f"Adapter transformation failed: {str(e)}",
            )

        # 3. Image Discovery & Deterministic Matching with OCR Assistance
        inventory = None
        match_report: Optional[ImageMatchReport] = None
        ocr_results = []
        approval_manifest: Optional[ImageApprovalManifest] = None

        if config.images_path and Path(config.images_path).exists():
            if config.verbose:
                print(f"[Pipeline] Discovering images in: {config.images_path}")
            inventory = GenericImageInventory.from_source(config.images_path)
            if config.verbose:
                print(f"[Pipeline] Discovered {inventory.total_images} image assets")

            # 3.1. Optional OCR Extraction (executed before matching so candidates can assist)
            if config.enable_ocr:
                from src.ocr.registry import OCRRegistry
                ocr_extractor = OCRRegistry.get_extractor(config.ocr_provider)
                if config.verbose:
                    print(f"[Pipeline] Running OCR extraction via provider '{ocr_extractor.provider_name}'...")
                for img in inventory.images:
                    if img.local_path:
                        res = ocr_extractor.extract_from_file(img.local_path)
                        ocr_results.append(res)
                if config.verbose:
                    print(f"[Pipeline] OCR extracted {len(ocr_results)} result(s)")

            # 3.2. Deterministic Image Matching with OCR candidate assistance
            match_report = DeterministicImageMatcher.match(
                products=products,
                inventory=inventory,
                attach_to_variants=True,
                ocr_results=ocr_results if ocr_results else None,
            )
            if config.verbose:
                print(
                    f"[Pipeline] Matched: {match_report.matched_count}, "
                    f"Ambiguous: {match_report.ambiguous_count}, "
                    f"Unmatched: {match_report.unmatched_count}"
                )

            # 3.3. Human-Review Manifest Initialization or Loading
            if config.manifest_path and Path(config.manifest_path).exists():
                if config.verbose:
                    print(f"[Pipeline] Loading pre-existing manifest from: {config.manifest_path}")
                approval_manifest = ImageApprovalManifest.load_from_file(config.manifest_path)
            else:
                approval_manifest = ImageApprovalManifest.create_from_match_report(
                    match_report=match_report,
                    ocr_results=ocr_results,
                )

        # 4. Optional Image Uploading (Strictly gated by Approval Manifest)
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
                approval_manifest=approval_manifest,
                require_approval=config.require_image_approval,
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

        # 6. Refined Product Data Enrichment (Phase 3 & Scope Correction)
        from src.enrichment.service import ProductEnrichmentService
        refined_export = ProductEnrichmentService.enrich(
            products=products,
            approval_manifest=approval_manifest,
            ocr_results=ocr_results,
            upload_summary=upload_summary,
            validation_report=validation_report,
            image_inventory=inventory,
        )
        if config.verbose:
            print(
                f"[Pipeline] Refined data enriched: {refined_export.total_variants} variants "
                f"({refined_export.approved_image_variants} approved, "
                f"{refined_export.variants_requiring_review} require review)"
            )

        # Re-validate canonical models post-enrichment
        validation_report = GenericProductValidator.validate_products(products)

        # 6.5. Canonical Export Assembly (reflects enriched color & image_sku)
        export_data = CanonicalExport.from_products(
            products=products,
            validation_report=validation_report,
        )

        # 7. Output Artifact Writing (if not dry_run)
        output_files: dict[str, str] = {}
        if not config.dry_run:
            from src.pipeline.output_manager import OutputManager
            out_mgr = OutputManager(config.output_dir)
            out_mgr.ensure_directories()

            # 7.1. Single Final Admin-Import Workbook placed at Run Root
            p_amra_ready = OutputWriter.write_amra_import_xlsx(
                products=products,
                output_path=out_mgr.final_amra_workbook_path,
                images_path=config.images_path,
            )
            output_files["amra_data_ready_xlsx"] = str(p_amra_ready)

            # 7.2. Supporting Canonical Data & Internal Artifacts saved in _internal/
            for fmt in config.export_formats:
                fmt_clean = fmt.lower().strip()
                if fmt_clean == "xlsx":
                    p = OutputWriter.write_canonical_xlsx(
                        export_data=export_data,
                        output_path=out_mgr.canonical_xlsx_path,
                        max_gallery_columns=config.max_gallery_columns,
                    )
                    output_files["xlsx"] = str(p)
                elif fmt_clean == "json":
                    p = OutputWriter.write_canonical_json(
                        export_data=export_data,
                        output_path=out_mgr.canonical_json_path,
                    )
                    output_files["json"] = str(p)
                elif fmt_clean == "csv":
                    p = OutputWriter.write_canonical_csv(
                        export_data=export_data,
                        output_path=out_mgr.canonical_csv_path,
                        max_gallery_columns=config.max_gallery_columns,
                    )
                    output_files["csv"] = str(p)

            # Write Raw Extracted TSV in _internal/ for traceability
            p_raw_tsv = out_mgr.write_raw_extracted_tsv(raw_data)
            if p_raw_tsv:
                output_files["raw_extracted_tsv"] = str(p_raw_tsv)

            # Write Separate Refined Products Workbook in _internal/
            p_refined = OutputWriter.write_refined_xlsx(
                refined_data=refined_export,
                output_path=out_mgr.refined_xlsx_path,
            )
            output_files["refined_xlsx"] = str(p_refined)

            # Write Website-Import Ready Workbook in _internal/
            p_mana_import = OutputWriter.write_mana_store_import_xlsx(
                products=products,
                output_path=out_mgr.mana_store_import_xlsx_path,
            )
            output_files["mana_store_import_xlsx"] = str(p_mana_import)

            # Write Image Approval Manifest in _internal/
            if approval_manifest:
                p_manifest = approval_manifest.save_to_file(out_mgr.manifest_json_path)
                output_files["manifest_json"] = str(p_manifest)

            # 7.3. Diagnostics, Audit & Reports saved in _reports/
            if config.generate_report:
                p_rep_xlsx = OutputWriter.write_validation_report_xlsx(
                    report=validation_report,
                    output_path=out_mgr.validation_report_xlsx_path,
                    match_report=match_report,
                    upload_results=upload_summary.results if upload_summary else None,
                )
                output_files["validation_report_xlsx"] = str(p_rep_xlsx)

                p_rep_json = OutputWriter.write_validation_report_json(
                    report=validation_report,
                    output_path=out_mgr.validation_report_json_path,
                    match_report=match_report,
                    upload_results=upload_summary.results if upload_summary else None,
                )
                output_files["validation_report_json"] = str(p_rep_json)

            # 7.4. RUN_SUMMARY.txt at Run Root
            val_stat = "FAILED" if validation_report.has_errors else "PASSED"
            total_vars = sum(len(p.variants) for p in products)
            p_summary = out_mgr.write_run_summary(
                adapter_used=adapter.adapter_name if 'adapter' in locals() else "unknown",
                total_products=len(products),
                total_variants=total_vars,
                validation_status=val_stat,
            )
            output_files["run_summary_txt"] = str(p_summary)

        # 8. Compute Metrics & Success Flag
        has_val_errors = validation_report.has_errors
        is_success = True
        error_msg = None

        if config.fail_on_error and has_val_errors:
            is_success = False
            error_msg = f"Validation failed with {len(validation_report.errors())} error(s)"

        total_ambiguous = match_report.ambiguous_count if match_report else 0
        total_approved = sum(1 for e in approval_manifest.entries if e.is_valid_approval()) if approval_manifest else 0

        metrics = PipelineMetrics(
            total_source_rows=raw_data.total_rows,
            total_products=export_data.total_products,
            total_variants=export_data.total_variants,
            total_images_discovered=match_report.total_images if match_report else 0,
            total_images_matched=match_report.matched_count if match_report else 0,
            total_images_ambiguous=total_ambiguous,
            total_images_approved=total_approved,
            total_images_uploaded=upload_summary.successful_uploads if upload_summary else 0,
            total_variants_requiring_review=refined_export.variants_requiring_review,
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
            approval_manifest=approval_manifest,
            refined_export=refined_export,
            upload_summary=upload_summary,
            ocr_results=ocr_results,
            error_message=error_msg,
        )
