from pathlib import Path
from typing import Any, Optional
from zipfile import ZipFile, is_zipfile
from pydantic import BaseModel, Field

from src.images.manifest import ImageApprovalManifest
from src.images.matcher import ImageMatchReport, MatchStatus
from src.models.enums import FieldSource
from src.models.image import ImageAsset
from src.models.product import Product
from src.models.provenance import ProvenanceRecord
from src.uploaders.base import BaseBlobUploader, UploadResult, UploadStatus


class ImageUploadSummary(BaseModel):
    """Summary of batch image upload operations."""
    total_attempted: int = 0
    successful_uploads: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    collision_count: int = 0
    results: list[UploadResult] = Field(default_factory=list)

    @property
    def is_all_successful(self) -> bool:
        return self.failed_count == 0 and self.collision_count == 0

    def summary(self) -> dict[str, Any]:
        return {
            "total_attempted": self.total_attempted,
            "successful_uploads": self.successful_uploads,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "collision_count": self.collision_count,
            "is_all_successful": self.is_all_successful,
        }


class ImageUploadService:
    """
    Orchestration service connecting matched image assets to a pluggable BlobUploader.
    
    Guarantees:
    - Only MATCHED images are uploaded; AMBIGUOUS and UNMATCHED are strictly skipped.
    - Preserves deterministic sort_index and primary image guarantees (index 0 is primary).
    - Populates ImageAsset.blob_url, variant.primary_image_url, and variant.gallery_image_urls.
    - Retains original source_url without conflation with blob_url.
    - Reads assets from either local filesystem or ZIP archives.
    """

    @classmethod
    def upload_matched_images(
        cls,
        products: list[Product],
        match_report: ImageMatchReport,
        uploader: BaseBlobUploader,
        prefix: Optional[str] = "products",
        approval_manifest: Optional[ImageApprovalManifest] = None,
        require_approval: bool = False,
    ) -> ImageUploadSummary:
        summary = ImageUploadSummary()

        # Build set of matched image relative paths for verification
        matched_image_ids = {
            f.image.image_id
            for f in match_report.findings
            if f.status == MatchStatus.MATCHED
        }

        # If approval manifest is provided, attach any approved entries that might have been reassigned
        if approval_manifest:
            var_map = {v.variant_code: v for p in products for v in p.variants}
            finding_img_map = {f.image.image_id: f.image for f in match_report.findings}

            for entry in approval_manifest.entries:
                if entry.is_valid_approval() and entry.proposed_variant_code in var_map:
                    matched_image_ids.add(entry.image_id)
                    target_v = var_map[entry.proposed_variant_code]
                    if not any(a.image_id == entry.image_id for a in target_v.images):
                        disc_img = finding_img_map.get(entry.image_id)
                        if disc_img:
                            idx = len(target_v.images)
                            is_hero = (idx == 0)
                            asset = ImageAsset(
                                image_id=disc_img.image_id,
                                local_path=disc_img.local_path,
                                supplier_sku=target_v.supplier_sku,
                                variant_code=target_v.variant_code,
                                original_filename=disc_img.original_filename,
                                source_identity=disc_img.source_identity,
                                sort_index=idx,
                                is_primary=is_hero,
                                source_metadata=ProvenanceRecord(
                                    source_file=disc_img.source_identity,
                                    supplier_id="manifest_approval",
                                ),
                                extra_metadata={
                                    "file_size_bytes": disc_img.file_size_bytes,
                                    "file_extension": disc_img.file_extension,
                                    "relative_path": disc_img.relative_path,
                                    "approved_by": entry.approved_by,
                                }
                            )
                            target_v.images.append(asset)

        # Count skipped non-matched images
        skipped_findings = [
            f for f in match_report.findings
            if f.status in (MatchStatus.AMBIGUOUS, MatchStatus.UNMATCHED)
        ]
        summary.skipped_count = len(skipped_findings)

        # Process each product and child variant
        for prod in products:
            for variant in prod.variants:
                if not variant.images:
                    continue

                uploaded_gallery_urls: list[tuple[int, str]] = []

                for asset in variant.images:
                    # Skip if not part of verified matched/approved set
                    if asset.image_id not in matched_image_ids:
                        summary.skipped_count += 1
                        continue

                    # Strict human-review manifest approval gate
                    if require_approval:
                        if not approval_manifest or not approval_manifest.is_approved(asset.image_id, variant.variant_code):
                            # Blocked by strict approval gate: must be explicitly APPROVED with valid hash
                            summary.skipped_count += 1
                            continue

                    summary.total_attempted += 1

                    # 1. Read binary image payload
                    data = cls._read_asset_bytes(asset)
                    if data is None:
                        res = UploadResult(
                            status=UploadStatus.FAILED,
                            destination_key=f"missing/{asset.original_filename}",
                            source_identity=asset.source_identity or str(asset.local_path),
                            error_message=f"Could not load image bytes for '{asset.original_filename}'",
                        )
                        summary.failed_count += 1
                        summary.results.append(res)
                        continue

                    # 2. Generate deterministic destination key
                    dest_key = uploader.generate_destination_key(
                        supplier_name=prod.supplier_name,
                        catalog_name=prod.catalog_name,
                        variant_code=variant.variant_code,
                        filename=asset.original_filename,
                        prefix=prefix,
                    )

                    # 3. Perform upload via pluggable uploader
                    res = uploader.upload_bytes(
                        data=data,
                        destination_key=dest_key,
                        source_identity=asset.source_identity or str(asset.local_path),
                    )
                    summary.results.append(res)

                    if res.status == UploadStatus.UPLOADED and res.blob_url:
                        summary.successful_uploads += 1
                        asset.blob_url = res.blob_url

                        # Record upload provenance
                        asset.source_metadata.track_field(
                            field_name="blob_url",
                            source_type=FieldSource.DERIVED,
                            raw_value=res.destination_key,
                            notes=f"Uploaded via {uploader.uploader_type} (idempotent={res.is_idempotent_reuse})",
                        )

                        # Update variant-level URLs preserving primary vs gallery order
                        if asset.is_primary:
                            variant.primary_image_url = res.blob_url
                        else:
                            uploaded_gallery_urls.append((asset.sort_index, res.blob_url))

                    elif res.status == UploadStatus.COLLISION:
                        summary.collision_count += 1
                    else:
                        summary.failed_count += 1

                # Ensure gallery URLs are mapped in exact sort_index order
                if uploaded_gallery_urls:
                    uploaded_gallery_urls.sort(key=lambda item: item[0])
                    variant.gallery_image_urls = [url for _, url in uploaded_gallery_urls]

        return summary

    @staticmethod
    def _read_asset_bytes(asset: Any) -> Optional[bytes]:
        """Read image bytes from filesystem path or source ZIP archive."""
        # 1. Direct local file
        if asset.local_path:
            p = Path(asset.local_path)
            if p.exists() and p.is_file():
                return p.read_bytes()

        # 2. ZIP Archive
        if asset.source_identity:
            zip_p = Path(asset.source_identity)
            if zip_p.exists() and is_zipfile(zip_p):
                rel_member = asset.extra_metadata.get("relative_path") or asset.original_filename
                with ZipFile(zip_p, "r") as zf:
                    # Try exact relative path first, then member search
                    if rel_member in zf.namelist():
                        return zf.read(rel_member)
                    for member_name in zf.namelist():
                        if Path(member_name).name == asset.original_filename:
                            return zf.read(member_name)

        return None
