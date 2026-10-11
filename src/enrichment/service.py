from typing import Any, Optional

from src.images.manifest import ApprovalStatus, ImageApprovalManifest, ImageManifestEntry
from src.images.uploader import ImageUploadSummary
from src.models.product import Product
from src.models.refined import RefinedExport, RefinedVariantRecord
from src.models.validation import ValidationReport
from src.ocr.models import OCRResult, OCRStatus


class ProductEnrichmentService:
    """
    Service responsible for enriching canonical products with:
    1. Verified approved image metadata from ImageApprovalManifest.
    2. Non-destructive provisional OCR candidates extracted from approved images.
    3. Confirmed Blob storage upload URLs.
    4. Review required flags and discrepancy diagnostics.
    """

    @classmethod
    def enrich(
        cls,
        products: list[Product],
        approval_manifest: Optional[ImageApprovalManifest] = None,
        ocr_results: Optional[list[OCRResult]] = None,
        upload_summary: Optional[ImageUploadSummary] = None,
        validation_report: Optional[ValidationReport] = None,
    ) -> RefinedExport:
        records: list[RefinedVariantRecord] = []

        # 1. Index validation issues by variant_code and supplier_sku
        val_errors: dict[str, list[str]] = {}
        val_warnings: dict[str, list[str]] = {}
        if validation_report:
            for issue in validation_report.issues:
                target_key = issue.variant_code or issue.supplier_sku or "global"
                msg = f"[{issue.code}] {issue.message}"
                if issue.severity.value == "ERROR":
                    val_errors.setdefault(target_key, []).append(msg)
                else:
                    val_warnings.setdefault(target_key, []).append(msg)

        # 2. Index OCR results by filename, path, and identity
        ocr_map: dict[str, OCRResult] = {}
        if ocr_results:
            for r in ocr_results:
                if r.source_filename:
                    ocr_map[r.source_filename.casefold()] = r
                if r.source_identity:
                    ocr_map[r.source_identity.casefold()] = r

        # 3. Index manifest entries by proposed_variant_code
        manifest_by_var: dict[str, list[ImageManifestEntry]] = {}
        if approval_manifest:
            for entry in approval_manifest.entries:
                if entry.proposed_variant_code:
                    manifest_by_var.setdefault(entry.proposed_variant_code, []).append(entry)

        # 4. Enrich each product variant
        for prod in products:
            for var in prod.variants:
                record = cls._enrich_variant(
                    prod=prod,
                    var=var,
                    manifest_entries=manifest_by_var.get(var.variant_code, []),
                    ocr_map=ocr_map,
                    val_errors=val_errors,
                    val_warnings=val_warnings,
                )
                records.append(record)

        # Compute summary metrics
        total = len(records)
        approved_count = sum(1 for r in records if r.has_approved_image)
        ocr_count = sum(1 for r in records if r.ocr_status == "SUCCESS")
        uploaded_count = sum(1 for r in records if r.is_uploaded and r.blob_url)
        review_count = sum(1 for r in records if r.requires_review)

        return RefinedExport(
            total_variants=total,
            approved_image_variants=approved_count,
            ocr_enriched_variants=ocr_count,
            uploaded_variants=uploaded_count,
            variants_requiring_review=review_count,
            records=records,
        )

    @classmethod
    def _enrich_variant(
        cls,
        prod: Product,
        var: Any,
        manifest_entries: list[ImageManifestEntry],
        ocr_map: dict[str, OCRResult],
        val_errors: dict[str, list[str]],
        val_warnings: dict[str, list[str]],
    ) -> RefinedVariantRecord:
        review_reasons: list[str] = []
        requires_review = False

        # Identify approved image entry (if any)
        valid_approved_entries = [
            e for e in manifest_entries
            if e.approval_status == ApprovalStatus.APPROVED and e.is_valid_approval()
        ]
        has_stale_hash = any(
            e.approval_status == ApprovalStatus.APPROVED and not e.is_valid_approval()
            for e in manifest_entries
        )
        has_pending = any(e.approval_status == ApprovalStatus.PENDING for e in manifest_entries)
        has_rejected = any(e.approval_status == ApprovalStatus.REJECTED for e in manifest_entries)

        approved_entry: Optional[ImageManifestEntry] = None
        if len(valid_approved_entries) > 1:
            # Duplicate approved images assigned to the same variant code
            requires_review = True
            review_reasons.append(
                f"Duplicate approved image assignments detected: {len(valid_approved_entries)} approved images assigned to variant '{var.variant_code}'"
            )
            # Pick first valid approval as primary representation
            approved_entry = valid_approved_entries[0]
        elif len(valid_approved_entries) == 1:
            approved_entry = valid_approved_entries[0]

        # Determine approved image status
        has_approved_image = approved_entry is not None
        if approved_entry:
            image_id = approved_entry.image_id
            image_filename = approved_entry.original_filename
            image_path = approved_entry.image_path
            approval_status = "APPROVED"
            approved_by = approved_entry.approved_by
            approved_at = approved_entry.approved_at
            assignment_hash = approved_entry.assignment_hash
        elif has_stale_hash:
            image_id = None
            image_filename = None
            image_path = None
            approval_status = "STALE_HASH_MISMATCH"
            approved_by = None
            approved_at = None
            assignment_hash = None
            requires_review = True
            review_reasons.append("Stale image approval detected: assignment hash mismatch")
        elif has_pending:
            image_id = None
            image_filename = None
            image_path = None
            approval_status = "PENDING"
            approved_by = None
            approved_at = None
            assignment_hash = None
            requires_review = True
            review_reasons.append("Image assignment pending human review")
        elif has_rejected:
            image_id = None
            image_filename = None
            image_path = None
            approval_status = "REJECTED"
            approved_by = None
            approved_at = None
            assignment_hash = None
            requires_review = True
            review_reasons.append("Image assignment was explicitly rejected")
        else:
            image_id = None
            image_filename = None
            image_path = None
            approval_status = "UNASSIGNED"
            approved_by = None
            approved_at = None
            assignment_hash = None
            requires_review = True
            review_reasons.append("No approved image assigned")

        # OCR Candidate Extraction (ONLY from approved image, NEVER fabricating truth)
        ocr_status = "NOT_RUN"
        ocr_provider = None
        ocr_confidence = None
        candidate_design_number = None
        candidate_sku = None
        candidate_color = None
        candidate_fabric = None
        candidate_rate = None
        raw_ocr_text = None

        if approved_entry:
            ocr_res = (
                ocr_map.get(approved_entry.original_filename.casefold())
                or ocr_map.get(approved_entry.image_path.casefold())
            )

            # Also check if OCR evidence was recorded in manifest entry
            if ocr_res:
                ocr_status = ocr_res.status.value
                ocr_provider = ocr_res.provider
                ocr_confidence = ocr_res.confidence
                raw_ocr_text = ocr_res.raw_text

                for cf in ocr_res.candidate_fields:
                    f_name = cf.field_name.lower().strip()
                    val = str(cf.candidate_value).strip()

                    if f_name in ("design_number", "design_no", "design"):
                        candidate_design_number = val
                    elif f_name in ("supplier_sku", "sku", "item_code"):
                        candidate_sku = val
                    elif f_name in ("color", "colour"):
                        candidate_color = val
                    elif f_name in ("fabric", "saree_fabric"):
                        candidate_fabric = val
                    elif f_name in ("rate", "price", "wholesale_rate"):
                        try:
                            candidate_rate = float(val)
                        except (ValueError, TypeError):
                            pass
            elif approved_entry.ocr_evidence:
                ocr_status = "SUCCESS"
                ocr_provider = approved_entry.ocr_evidence.get("provider", "manifest_record")
                ocr_confidence = approved_entry.ocr_evidence.get("confidence")
                cand_map = approved_entry.ocr_evidence.get("candidates", {})
                candidate_design_number = cand_map.get("design_number")
                candidate_sku = cand_map.get("supplier_sku") or cand_map.get("sku")
                candidate_color = cand_map.get("color")
                candidate_fabric = cand_map.get("fabric")
            else:
                ocr_status = "NOT_RUN"

            # Check for conflicting evidence vs original supplier data
            if candidate_sku and var.supplier_sku:
                if candidate_sku.casefold() != var.supplier_sku.casefold():
                    requires_review = True
                    review_reasons.append(
                        f"SKU discrepancy: supplier has '{var.supplier_sku}' vs OCR candidate '{candidate_sku}'"
                    )

            if candidate_color and var.color:
                if candidate_color.casefold() != var.color.casefold():
                    requires_review = True
                    review_reasons.append(
                        f"Color discrepancy: supplier has '{var.color}' vs OCR candidate '{candidate_color}'"
                    )

        # Blob Upload Status
        is_uploaded = False
        blob_url = None
        gallery_blob_urls = []

        if var.primary_image_url and (
            var.primary_image_url.startswith("https://") or var.primary_image_url.startswith("http://")
        ):
            # Check if this URL is from blob storage
            if "blob.vercel-storage.com" in var.primary_image_url or "blob" in var.primary_image_url:
                is_uploaded = True
                blob_url = var.primary_image_url
            elif var.images and any(img.blob_url for img in var.images):
                is_uploaded = True
                blob_url = next(img.blob_url for img in var.images if img.blob_url)

        if var.gallery_image_urls:
            gallery_blob_urls = [
                u for u in var.gallery_image_urls
                if u and ("blob.vercel-storage.com" in u or "blob" in u)
            ]

        # Check pricing and selling price quality gate
        if var.selling_price is None:
            requires_review = True
            review_reasons.append("Selling price requires manual review (never auto-calculated)")

        # Collect validation issues
        errs = val_errors.get(var.variant_code, []) + (val_errors.get(var.supplier_sku, []) if var.supplier_sku else [])
        warns = val_warnings.get(var.variant_code, []) + (val_warnings.get(var.supplier_sku, []) if var.supplier_sku else [])
        if errs:
            requires_review = True
            review_reasons.append(f"Validation errors present ({len(errs)})")

        # Merge category attributes
        merged_attrs = dict(prod.attributes)
        merged_attrs.update(var.attributes)

        return RefinedVariantRecord(
            product_id=prod.product_id,
            catalog_name=prod.catalog_name,
            product_name=prod.product_name,
            product_group=prod.product_group,
            brand_name=prod.brand_name,
            product_type=prod.product_type,
            category_slug=prod.category_slug or "sarees",
            variant_code=var.variant_code,
            variant_name=var.variant_name,
            supplier_sku=var.supplier_sku,
            original_color=var.color,
            original_size=var.size,
            supplier_price=prod.supplier_price,
            supplier_cost=var.supplier_cost,
            gst_percent=prod.gst_percent,
            selling_price=var.selling_price,
            stock_quantity=var.stock_quantity,
            description=prod.description,
            has_approved_image=has_approved_image,
            image_id=image_id,
            image_filename=image_filename,
            image_path=image_path,
            approval_status=approval_status,
            approved_by=approved_by,
            approved_at=approved_at,
            assignment_hash=assignment_hash,
            ocr_status=ocr_status,
            ocr_provider=ocr_provider,
            ocr_confidence=ocr_confidence,
            candidate_design_number=candidate_design_number,
            candidate_sku=candidate_sku,
            candidate_color=candidate_color,
            candidate_fabric=candidate_fabric,
            candidate_rate=candidate_rate,
            raw_ocr_text=raw_ocr_text,
            is_uploaded=is_uploaded,
            blob_url=blob_url,
            gallery_blob_urls=gallery_blob_urls,
            requires_review=requires_review,
            review_reasons=review_reasons,
            validation_errors=errs,
            validation_warnings=warns,
            attributes=merged_attrs,
        )
