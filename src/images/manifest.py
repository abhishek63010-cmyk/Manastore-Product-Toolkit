from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Optional, Union
from pydantic import BaseModel, Field

from src.images.matcher import ImageMatchFinding, ImageMatchReport, MatchStatus
from src.ocr.models import OCRResult


class ApprovalStatus(str, Enum):
    """Review lifecycle status for an image assignment."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ImageManifestEntry(BaseModel):
    """
    Reviewable manifest entry linking a discovered image to a proposed product variant.
    
    Safety Rules:
    - Never defaults to approved; always starts as PENDING.
    - Tracks an `assignment_hash` = sha256(image_id + proposed_variant_code).
    - If the proposed variant changes or image identity changes, the approval is invalidated.
    """
    image_id: str = Field(..., description="Stable deterministic image identifier")
    image_path: str = Field(..., description="Relative filesystem or archive path")
    original_filename: str = Field(..., description="Exact original filename")
    catalog_name: Optional[str] = Field(default=None, description="Proposed parent catalogue title")
    proposed_product_id: Optional[str] = Field(default=None, description="Proposed parent product ID")
    proposed_variant_code: Optional[str] = Field(default=None, description="Proposed target variant code")
    match_status: MatchStatus = Field(..., description="Automatic match status (MATCHED, AMBIGUOUS, UNMATCHED)")
    match_strategy: Optional[str] = Field(default=None, description="Matching rule/strategy used")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Match confidence score")
    ocr_evidence: Optional[dict[str, Any]] = Field(
        default=None,
        description="Structured candidate attributes from OCR used to guide assignment"
    )
    approval_status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        description="Explicit human review decision"
    )
    approved_by: Optional[str] = Field(default=None, description="Identifier of human reviewer")
    approved_at: Optional[str] = Field(default=None, description="ISO timestamp of approval")
    assignment_hash: str = Field(
        ...,
        description="Integrity hash of (image_id, proposed_variant_code) to detect reassignments"
    )
    candidate_variant_codes: list[str] = Field(
        default_factory=list,
        description="Alternative plausible variant candidates if match is ambiguous"
    )
    notes: Optional[str] = Field(default=None, description="Reviewer or matcher notes")

    @staticmethod
    def compute_assignment_hash(image_id: str, proposed_variant_code: Optional[str]) -> str:
        code_str = proposed_variant_code or "UNASSIGNED"
        h = hashlib.sha256(f"{image_id}:{code_str}".encode("utf-8")).hexdigest()[:16]
        return f"hash_{h}"

    def is_valid_approval(self) -> bool:
        """
        True only if explicitly approved, assignment hash is verified,
        and proposed variant code is present.
        """
        if self.approval_status != ApprovalStatus.APPROVED:
            return False
        if not self.proposed_variant_code:
            return False
        expected_hash = self.compute_assignment_hash(self.image_id, self.proposed_variant_code)
        return self.assignment_hash == expected_hash

    @classmethod
    def from_finding(
        cls,
        finding: ImageMatchFinding,
        ocr_result: Optional[OCRResult] = None,
    ) -> "ImageManifestEntry":
        ocr_summary = None
        if ocr_result and ocr_result.candidate_fields:
            ocr_summary = {
                "provider": ocr_result.provider,
                "confidence": ocr_result.confidence,
                "candidates": {c.field_name: c.candidate_value for c in ocr_result.candidate_fields},
            }

        assign_hash = cls.compute_assignment_hash(
            image_id=finding.image.image_id,
            proposed_variant_code=finding.matched_variant_code,
        )

        return cls(
            image_id=finding.image.image_id,
            image_path=finding.image.relative_path,
            original_filename=finding.image.original_filename,
            catalog_name=finding.matched_catalog_name,
            proposed_product_id=finding.matched_product_id,
            proposed_variant_code=finding.matched_variant_code,
            match_status=finding.status,
            match_strategy=finding.match_strategy,
            confidence=finding.confidence,
            ocr_evidence=ocr_summary,
            approval_status=ApprovalStatus.PENDING,
            assignment_hash=assign_hash,
            candidate_variant_codes=finding.candidate_variant_codes,
            notes=finding.reason,
        )


class ImageApprovalManifest(BaseModel):
    """
    Reviewable manifest tracking all image-to-variant assignments and approvals.
    """
    manifest_version: str = "1.0"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    entries: list[ImageManifestEntry] = Field(default_factory=list)

    @classmethod
    def create_from_match_report(
        cls,
        match_report: ImageMatchReport,
        ocr_results: Optional[list[OCRResult]] = None,
    ) -> "ImageApprovalManifest":
        ocr_map: dict[str, OCRResult] = {}
        if ocr_results:
            for r in ocr_results:
                ocr_map[r.source_filename.lower()] = r
                ocr_map[r.source_identity.lower()] = r

        manifest_entries: list[ImageManifestEntry] = []
        for finding in match_report.findings:
            fname = finding.image.original_filename.lower()
            rel_p = finding.image.relative_path.lower()
            matching_ocr = ocr_map.get(fname) or ocr_map.get(rel_p)
            entry = ImageManifestEntry.from_finding(finding, ocr_result=matching_ocr)
            manifest_entries.append(entry)

        return cls(entries=manifest_entries)

    def get_entry(self, image_id: str) -> Optional[ImageManifestEntry]:
        for e in self.entries:
            if e.image_id == image_id:
                return e
        return None

    def is_approved(self, image_id: str, variant_code: str) -> bool:
        """
        Check if an image is explicitly approved for a specific variant.
        Must have valid approval status AND valid assignment hash.
        """
        entry = self.get_entry(image_id)
        if not entry:
            return False
        if entry.proposed_variant_code != variant_code:
            return False
        return entry.is_valid_approval()

    def approve(
        self,
        image_id: str,
        approved_by: str = "reviewer",
        notes: Optional[str] = None,
    ) -> bool:
        """Approve an image assignment explicitly."""
        entry = self.get_entry(image_id)
        if not entry or not entry.proposed_variant_code:
            return False
        entry.approval_status = ApprovalStatus.APPROVED
        entry.approved_by = approved_by
        entry.approved_at = datetime.now(timezone.utc).isoformat()
        entry.assignment_hash = ImageManifestEntry.compute_assignment_hash(
            entry.image_id, entry.proposed_variant_code
        )
        if notes:
            entry.notes = notes
        return True

    def reject(
        self,
        image_id: str,
        reviewer: str = "reviewer",
        reason: Optional[str] = None,
    ) -> bool:
        """Reject an image assignment."""
        entry = self.get_entry(image_id)
        if not entry:
            return False
        entry.approval_status = ApprovalStatus.REJECTED
        entry.approved_by = reviewer
        entry.approved_at = datetime.now(timezone.utc).isoformat()
        if reason:
            entry.notes = reason
        return True

    def reassign(
        self,
        image_id: str,
        new_variant_code: str,
        reviewer: str = "reviewer",
    ) -> bool:
        """
        Reassign an image to a different variant code.
        Crucial safety: resets approval status to PENDING and recomputes hash.
        """
        entry = self.get_entry(image_id)
        if not entry:
            return False
        entry.proposed_variant_code = new_variant_code
        entry.assignment_hash = ImageManifestEntry.compute_assignment_hash(image_id, new_variant_code)
        entry.approval_status = ApprovalStatus.PENDING
        entry.approved_by = None
        entry.approved_at = None
        entry.notes = f"Reassigned to {new_variant_code} by {reviewer}; awaiting re-approval"
        return True

    def summary(self) -> dict[str, Any]:
        """Return counts of matched, unmatched, ambiguous, pending, approved, and rejected."""
        total = len(self.entries)
        approved = sum(1 for e in self.entries if e.is_valid_approval())
        pending = sum(1 for e in self.entries if e.approval_status == ApprovalStatus.PENDING)
        rejected = sum(1 for e in self.entries if e.approval_status == ApprovalStatus.REJECTED)
        matched = sum(1 for e in self.entries if e.match_status == MatchStatus.MATCHED)
        ambiguous = sum(1 for e in self.entries if e.match_status == MatchStatus.AMBIGUOUS)
        unmatched = sum(1 for e in self.entries if e.match_status == MatchStatus.UNMATCHED)

        return {
            "total_images": total,
            "matched_count": matched,
            "ambiguous_count": ambiguous,
            "unmatched_count": unmatched,
            "approved_count": approved,
            "pending_approval_count": pending,
            "rejected_count": rejected,
            "approval_rate_percent": round((approved / total) * 100, 2) if total > 0 else 0.0,
        }

    def save_to_file(self, path: Union[str, Path]) -> Path:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return out_path

    def validate_against_products(self, products: list[Any]) -> list[str]:
        """
        Validate manifest entries against the active set of products and variants.
        Returns a list of warning/error diagnostic messages for unknown variants,
        unassigned approved entries, or duplicate assignments.
        """
        valid_variant_codes = {v.variant_code for p in products for v in p.variants}
        diagnostics: list[str] = []

        # Track variant codes assigned to multiple approved images
        approved_by_var: dict[str, list[str]] = {}

        for entry in self.entries:
            if not entry.proposed_variant_code:
                if entry.approval_status == ApprovalStatus.APPROVED:
                    diagnostics.append(f"Image '{entry.image_id}' is marked APPROVED but has no assigned variant code.")
                continue

            if entry.proposed_variant_code not in valid_variant_codes:
                diagnostics.append(
                    f"Image '{entry.image_id}' assigned to unknown variant '{entry.proposed_variant_code}'"
                )

            if entry.is_valid_approval():
                approved_by_var.setdefault(entry.proposed_variant_code, []).append(entry.image_id)

        for var_code, img_ids in approved_by_var.items():
            if len(img_ids) > 1:
                diagnostics.append(
                    f"Multiple approved images {img_ids} assigned to single variant '{var_code}'"
                )

        return diagnostics

    @classmethod
    def load_from_file(cls, path: Union[str, Path]) -> "ImageApprovalManifest":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Manifest file not found: {p}")
        return cls.model_validate_json(p.read_text(encoding="utf-8"))
