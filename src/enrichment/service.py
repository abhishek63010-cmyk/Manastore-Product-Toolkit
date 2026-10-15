from pathlib import Path
from typing import Any, Optional

from src.images.color import ImageColorAnalyzer, ImageColorResult
from src.images.inventory import ImageInventory
from src.images.manifest import ApprovalStatus, ImageApprovalManifest, ImageManifestEntry
from src.images.uploader import ImageUploadSummary
from src.models.product import Product
from src.models.provenance import FieldSource
from src.models.refined import RefinedExport, RefinedVariantRecord
from src.models.validation import ValidationReport
from src.ocr.models import OCRResult, OCRStatus


class ProductEnrichmentService:
    """
    Service responsible for enriching canonical products with:
    1. Verified approved image metadata from ImageApprovalManifest.
    2. Identifiable image SKU resolution and pixel color extraction.
    3. Non-destructive provisional OCR candidates extracted from approved images.
    4. Confirmed Blob storage upload URLs (when present).
    5. Review required flags and discrepancy diagnostics.
    """

    @classmethod
    def enrich(
        cls,
        products: list[Product],
        approval_manifest: Optional[ImageApprovalManifest] = None,
        ocr_results: Optional[list[OCRResult]] = None,
        upload_summary: Optional[ImageUploadSummary] = None,
        validation_report: Optional[ValidationReport] = None,
        image_inventory: Optional[ImageInventory] = None,
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
                    image_inventory=image_inventory,
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
        image_inventory: Optional[ImageInventory] = None,
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

        # Preserve original supplier facts before any image enrichment
        original_supplier_color = (
            getattr(var, "raw_attributes", {}).get("original_color")
            or getattr(var, "attributes", {}).get("original_color")
            or var.color
        )
        if original_supplier_color and hasattr(var, "raw_attributes"):
            var.raw_attributes.setdefault("original_color", original_supplier_color)
        original_supplier_sku = var.supplier_sku
        original_variant_name = var.variant_name

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
            if candidate_sku and original_supplier_sku:
                if candidate_sku.casefold() != original_supplier_sku.casefold():
                    requires_review = True
                    review_reasons.append(
                        f"SKU discrepancy: supplier has '{original_supplier_sku}' vs OCR candidate '{candidate_sku}'"
                    )

            if candidate_color and original_supplier_color:
                if candidate_color.casefold() != original_supplier_color.casefold():
                    requires_review = True
                    review_reasons.append(
                        f"Color discrepancy: supplier has '{original_supplier_color}' vs OCR candidate '{candidate_color}'"
                    )

        # -------------------------------------------------------------
        # Approved Image Enrichment: Image SKU & Pixel Color Analysis
        # -------------------------------------------------------------
        enriched_image_sku: Optional[str] = None
        enriched_color: Optional[str] = None
        image_color_status: str = "NOT_ANALYZED"
        image_color_confidence: Optional[float] = None
        image_color_hex: Optional[str] = None
        image_color_notes: Optional[str] = None

        if approved_entry:
            # 1. Resolve image_sku following strict precedence:
            #   (a) Explicit user assignment in entry (entry.image_sku)
            #   (b) OCR candidate SKU from printed text on the approved image
            #   (c) Leave empty if unavailable; never fabricate
            if approved_entry.image_sku and approved_entry.image_sku.strip():
                enriched_image_sku = approved_entry.image_sku.strip()
            elif candidate_sku and candidate_sku.strip():
                enriched_image_sku = candidate_sku.strip()
            else:
                enriched_image_sku = None
                requires_review = True
                review_reasons.append(
                    f"Image SKU missing for approved image '{approved_entry.original_filename}'; required for publication"
                )

            # 2. Analyze pixel color from the approved image
            color_result: Optional[ImageColorResult] = None

            # 2.1 Check if entry already has recorded color evidence
            if approved_entry.detected_color:
                ev = approved_entry.color_evidence or {}
                color_result = ImageColorResult(
                    status="EXTRACTED",
                    canonical_color=approved_entry.detected_color,
                    confidence=ev.get("confidence", 0.8),
                    hex_code=ev.get("hex_code"),
                    is_ambiguous=False,
                    notes=ev.get("notes", "Pre-analyzed image color from manifest"),
                )
            else:
                # 2.2 Attempt to load image data and run ImageColorAnalyzer
                img_data_to_analyze = None
                # Try image_path as absolute file or relative to working directory
                if approved_entry.image_path and Path(approved_entry.image_path).is_file():
                    img_data_to_analyze = approved_entry.image_path
                elif image_inventory:
                    disc_img = image_inventory.get_image(approved_entry.image_id) or image_inventory.get_image(approved_entry.original_filename)
                    if disc_img:
                        img_bytes = disc_img.read_bytes()
                        if img_bytes:
                            img_data_to_analyze = img_bytes
                        elif disc_img.local_path and Path(disc_img.local_path).is_file():
                            img_data_to_analyze = disc_img.local_path

                # Fallback to variant image assets if present
                if not img_data_to_analyze and var.images:
                    for asset in var.images:
                        if asset.image_id == approved_entry.image_id or asset.original_filename == approved_entry.original_filename:
                            if asset.local_path and Path(asset.local_path).is_file():
                                img_data_to_analyze = asset.local_path
                                break

                if img_data_to_analyze:
                    color_result = ImageColorAnalyzer.analyze_image(img_data_to_analyze)
                elif approved_entry.color_evidence and approved_entry.color_evidence.get("status"):
                    ev = approved_entry.color_evidence
                    color_result = ImageColorResult(
                        status=ev.get("status", "UNCERTAIN"),
                        canonical_color=ev.get("canonical_color"),
                        confidence=ev.get("confidence", 0.0),
                        hex_code=ev.get("hex_code"),
                        is_ambiguous=ev.get("is_ambiguous", True),
                        notes=ev.get("notes"),
                    )

            if color_result:
                image_color_status = color_result.status
                image_color_confidence = color_result.confidence
                image_color_hex = color_result.hex_code
                image_color_notes = color_result.notes

                if color_result.status == "EXTRACTED" and not color_result.is_ambiguous and color_result.canonical_color:
                    enriched_color = color_result.canonical_color
                    # Cross-check against supplier color if supplier gave a distinct color
                    if original_supplier_color and enriched_color.casefold() != original_supplier_color.casefold():
                        requires_review = True
                        review_reasons.append(
                            f"Image color discrepancy: image pixels suggest '{enriched_color}' vs supplier '{original_supplier_color}'"
                        )
                else:
                    enriched_color = None
                    requires_review = True
                    diag_reason = color_result.notes or f"Image color analysis {color_result.status.lower()}; manual color verification required"
                    review_reasons.append(diag_reason)
            else:
                image_color_status = "NOT_ANALYZED"
                image_color_notes = "Image file inaccessible for local pixel color analysis"
                requires_review = True
                review_reasons.append("Image pixel color could not be analyzed (file not accessible)")

            # Populate exact canonical variant row attributes from approved image
            var.image_sku = enriched_image_sku
            var.color = enriched_color
            var.attributes["candidate_image_sku"] = enriched_image_sku
            var.attributes["image_color_candidate"] = enriched_color
            if color_result and color_result.canonical_color:
                var.attributes["color_evidence"] = {
                    "canonical_color": color_result.canonical_color,
                    "confidence": color_result.confidence,
                    "hex_code": color_result.hex_code,
                    "notes": color_result.notes,
                }
        elif var.images:
            # Deterministically matched image available on variant
            hero_asset = var.images[0]
            cand_sku = var.image_sku or Path(hero_asset.original_filename).stem
            enriched_image_sku = cand_sku
            var.image_sku = cand_sku
            var.attributes["candidate_image_sku"] = cand_sku

            color_result = None
            if hero_asset.local_path and Path(hero_asset.local_path).is_file():
                color_result = ImageColorAnalyzer.analyze_image(hero_asset.local_path, crop_center_ratio=0.6)

            if color_result:
                image_color_status = color_result.status
                image_color_confidence = color_result.confidence
                image_color_hex = color_result.hex_code
                image_color_notes = color_result.notes

                if color_result.status == "EXTRACTED" and not color_result.is_ambiguous and color_result.canonical_color:
                    enriched_color = color_result.canonical_color
                    var.attributes["image_color_candidate"] = enriched_color
                    var.attributes["color_evidence"] = {
                        "canonical_color": enriched_color,
                        "confidence": color_result.confidence,
                        "hex_code": color_result.hex_code,
                        "notes": color_result.notes,
                    }
                    if not var.color:
                        var.color = enriched_color
                else:
                    enriched_color = None
            else:
                image_color_status = "NOT_ANALYZED"

            if enriched_color and not var.color:
                var.color = enriched_color

            # Record provenance
            if enriched_image_sku:
                var.source_metadata.track_field("image_sku", FieldSource.DERIVED, raw_value=enriched_image_sku, notes="Extracted from matched image filename")
            if enriched_color:
                var.source_metadata.track_field("color", FieldSource.DERIVED, raw_value=enriched_color, notes=f"Extracted from pixel color analysis (confidence={image_color_confidence})")
        else:
            # Without approved image or matched image, canonical color and image_sku MUST remain empty
            enriched_image_sku = None
            enriched_color = None
            image_color_status = "NOT_ANALYZED"
            var.image_sku = None
            var.color = None
            requires_review = True
            review_reasons.append("Missing image assignment")

        # Preserve variant name without mutation
        var.variant_name = original_variant_name

        # Blob Upload Status (strictly empty if not provided)
        is_uploaded = False
        blob_url = None
        gallery_blob_urls = []

        if var.primary_image_url and (
            var.primary_image_url.startswith("https://") or var.primary_image_url.startswith("http://")
        ):
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
            variant_name=original_variant_name,
            supplier_sku=original_supplier_sku,
            original_color=original_supplier_color,
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
            image_sku=enriched_image_sku,
            color=enriched_color,
            image_color_status=image_color_status,
            image_color_confidence=image_color_confidence,
            image_color_hex=image_color_hex,
            image_color_notes=image_color_notes,
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
