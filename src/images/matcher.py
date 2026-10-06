import re
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union
from pydantic import BaseModel, Field

from src.images.inventory import DiscoveredImage, ImageInventory, natural_sort_key
from src.models.enums import FieldSource
from src.models.image import ImageAsset
from src.models.product import Product
from src.models.provenance import ProvenanceRecord
from src.models.variant import Variant


class MatchStatus(str, Enum):
    """Outcome status for image-to-variant matching."""
    MATCHED = "MATCHED"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"


class ImageMatchFinding(BaseModel):
    """Detailed record of an image match decision."""
    image: DiscoveredImage
    status: MatchStatus
    matched_product_id: Optional[str] = None
    matched_catalog_name: Optional[str] = None
    matched_variant_code: Optional[str] = None
    matched_supplier_sku: Optional[str] = None
    match_strategy: Optional[str] = None
    confidence: float = 0.0
    reason: Optional[str] = None
    candidate_variant_codes: list[str] = Field(default_factory=list)


class ImageMatchReport(BaseModel):
    """Comprehensive report summarizing image discovery and variant matching results."""
    total_images: int = 0
    matched_count: int = 0
    unmatched_count: int = 0
    ambiguous_count: int = 0
    findings: list[ImageMatchFinding] = Field(default_factory=list)

    def matched_findings(self) -> list[ImageMatchFinding]:
        return [f for f in self.findings if f.status == MatchStatus.MATCHED]

    def unmatched_findings(self) -> list[ImageMatchFinding]:
        return [f for f in self.findings if f.status == MatchStatus.UNMATCHED]

    def ambiguous_findings(self) -> list[ImageMatchFinding]:
        return [f for f in self.findings if f.status == MatchStatus.AMBIGUOUS]

    def summary(self) -> dict[str, Any]:
        return {
            "total_images": self.total_images,
            "matched_count": self.matched_count,
            "unmatched_count": self.unmatched_count,
            "ambiguous_count": self.ambiguous_count,
            "match_rate_percent": (
                round((self.matched_count / self.total_images) * 100, 2)
                if self.total_images > 0 else 0.0
            ),
        }


class DeterministicImageMatcher:
    """
    Deterministic image-to-variant matcher.
    
    Principles:
    - Strict priority ordering: Exact ID -> Scoped Parent + Variant Token -> Disambiguated SKU.
    - Resolves multi-variant shared supplier SKUs without guessing.
    - Guarantees deterministic natural sorting (Index 0 = primary image).
    - Enforces cross-product isolation (generic color names cannot leak across products).
    - Preserves origin provenance for matched, ambiguous, and unmatched images.
    """

    @classmethod
    def match(
        cls,
        products: list[Product],
        inventory: ImageInventory,
        attach_to_variants: bool = True,
    ) -> ImageMatchReport:
        findings: list[ImageMatchFinding] = []
        variant_matched_images: dict[str, list[DiscoveredImage]] = {}

        # Map all variants and products for fast, scoped lookup
        variant_lookup: dict[str, tuple[Product, Variant]] = {}
        for prod in products:
            for var in prod.variants:
                variant_lookup[var.variant_code.casefold()] = (prod, var)
                if attach_to_variants:
                    # Reset image list for clean state
                    var.images = []

        # Process each discovered image in inventory
        for img in inventory.images:
            finding = cls._match_single_image(img, products, variant_lookup)
            findings.append(finding)

            if finding.status == MatchStatus.MATCHED and finding.matched_variant_code:
                code_key = finding.matched_variant_code.casefold()
                if code_key not in variant_matched_images:
                    variant_matched_images[code_key] = []
                variant_matched_images[code_key].append(img)

        # If requested, attach matched ImageAssets deterministically to each Variant
        if attach_to_variants:
            for code_key, matched_imgs in variant_matched_images.items():
                if code_key in variant_lookup:
                    _, target_variant = variant_lookup[code_key]
                    
                    # Sort matched images deterministically for this variant
                    sorted_images = sorted(
                        matched_imgs,
                        key=lambda x: cls._variant_image_sort_key(x, target_variant)
                    )

                    for idx, img in enumerate(sorted_images):
                        is_hero = (idx == 0)
                        img_provenance = ProvenanceRecord(
                            source_file=img.source_identity,
                            supplier_id="image_matcher",
                        )
                        img_provenance.track_field(
                            field_name="variant_code",
                            source_type=FieldSource.SOURCE if is_hero else FieldSource.DERIVED,
                            raw_value=img.relative_path,
                            notes=f"Matched at index {idx} (is_primary={is_hero})",
                        )

                        asset = ImageAsset(
                            image_id=img.image_id,
                            local_path=img.local_path,
                            supplier_sku=target_variant.supplier_sku,
                            variant_code=target_variant.variant_code,
                            original_filename=img.original_filename,
                            source_identity=img.source_identity,
                            sort_index=idx,
                            is_primary=is_hero,
                            source_metadata=img_provenance,
                            extra_metadata={
                                "file_size_bytes": img.file_size_bytes,
                                "file_extension": img.file_extension,
                                "relative_path": img.relative_path,
                            }
                        )
                        target_variant.images.append(asset)

        matched_count = sum(1 for f in findings if f.status == MatchStatus.MATCHED)
        unmatched_count = sum(1 for f in findings if f.status == MatchStatus.UNMATCHED)
        ambiguous_count = sum(1 for f in findings if f.status == MatchStatus.AMBIGUOUS)

        return ImageMatchReport(
            total_images=len(inventory.images),
            matched_count=matched_count,
            unmatched_count=unmatched_count,
            ambiguous_count=ambiguous_count,
            findings=findings,
        )

    @classmethod
    def _variant_image_sort_key(
        cls,
        img: DiscoveredImage,
        target_variant: Variant,
    ) -> tuple:
        """
        Produce a deterministic sort key relative to target variant:
        1. Exact base match (e.g. SETHNI-V-108001.png) -> (0, 0)
        2. Suffix sub-image (e.g. SETHNI-V-108001-1.png) -> (1, 1)
        3. Fallback natural sort
        """
        stem = img.normalized_stem.casefold()
        base_keys = [target_variant.variant_code.casefold()]
        if target_variant.image_sku:
            base_keys.append(target_variant.image_sku.casefold())

        for bk in base_keys:
            if stem == bk:
                return (0, 0, natural_sort_key(img.original_filename))
            if stem.startswith(f"{bk}-") or stem.startswith(f"{bk}_"):
                suffix = stem[len(bk) + 1:]
                if suffix.isdigit():
                    return (1, int(suffix), natural_sort_key(img.original_filename))

        return (2, 0, natural_sort_key(img.relative_path))

    @classmethod
    def _match_single_image(
        cls,
        img: DiscoveredImage,
        products: list[Product],
        variant_lookup: dict[str, tuple[Product, Variant]],
    ) -> ImageMatchFinding:
        norm_stem = img.normalized_stem.casefold()
        img_tokens = set(img.tokens)

        # -----------------------------------------------------------------
        # Priority 1: Exact Variant Identity (variant_code, image_sku, explicit url)
        # -----------------------------------------------------------------
        for prod in products:
            for var in prod.variants:
                v_code = var.variant_code.strip().casefold()
                v_img_sku = var.image_sku.strip().casefold() if var.image_sku else None

                # Exact match against variant_code or image_sku
                if norm_stem == v_code or norm_stem.startswith(f"{v_code}_") or norm_stem.startswith(f"{v_code}-"):
                    return ImageMatchFinding(
                        image=img,
                        status=MatchStatus.MATCHED,
                        matched_product_id=prod.product_id,
                        matched_catalog_name=prod.catalog_name,
                        matched_variant_code=var.variant_code,
                        matched_supplier_sku=var.supplier_sku,
                        match_strategy="EXACT_VARIANT_CODE",
                        confidence=1.0,
                        reason=f"Matched exact variant_code '{var.variant_code}'",
                    )

                if v_img_sku and (norm_stem == v_img_sku or norm_stem.startswith(f"{v_img_sku}_") or norm_stem.startswith(f"{v_img_sku}-")):
                    return ImageMatchFinding(
                        image=img,
                        status=MatchStatus.MATCHED,
                        matched_product_id=prod.product_id,
                        matched_catalog_name=prod.catalog_name,
                        matched_variant_code=var.variant_code,
                        matched_supplier_sku=var.supplier_sku,
                        match_strategy="EXACT_IMAGE_SKU",
                        confidence=1.0,
                        reason=f"Matched exact image_sku '{var.image_sku}'",
                    )

                # Explicit remote source URL filename matching
                if var.primary_image_url:
                    remote_name = Path(var.primary_image_url).name.casefold()
                    if img.original_filename.casefold() == remote_name:
                        return ImageMatchFinding(
                            image=img,
                            status=MatchStatus.MATCHED,
                            matched_product_id=prod.product_id,
                            matched_catalog_name=prod.catalog_name,
                            matched_variant_code=var.variant_code,
                            matched_supplier_sku=var.supplier_sku,
                            match_strategy="SOURCE_URL_EXACT",
                            confidence=1.0,
                            reason=f"Matched remote primary_image_url filename '{remote_name}'",
                        )

        # -----------------------------------------------------------------
        # Priority 2: Scoped Product Group / Catalog + Color/Variant Name Token
        # -----------------------------------------------------------------
        for prod in products:
            prod_group = cls._clean_token(prod.product_group) if prod.product_group else None
            catalog_norm = cls._clean_token(prod.catalog_name)
            clean_rel_path = cls._clean_token(img.relative_path)

            # Check if this image path/tokens belong to this product scope
            is_product_scope = (
                (prod_group and prod_group in clean_rel_path)
                or (catalog_norm and catalog_norm in clean_rel_path)
            )

            if is_product_scope:
                # Find matching variant within this product scope only
                matched_variants: list[Variant] = []
                for var in prod.variants:
                    color_token = cls._clean_token(var.color) if var.color else None
                    name_token = cls._clean_token(var.variant_name) if var.variant_name else None

                    if color_token and color_token in clean_rel_path:
                        matched_variants.append(var)
                    elif name_token and name_token in clean_rel_path:
                        matched_variants.append(var)

                if len(matched_variants) == 1:
                    chosen = matched_variants[0]
                    return ImageMatchFinding(
                        image=img,
                        status=MatchStatus.MATCHED,
                        matched_product_id=prod.product_id,
                        matched_catalog_name=prod.catalog_name,
                        matched_variant_code=chosen.variant_code,
                        matched_supplier_sku=chosen.supplier_sku,
                        match_strategy="SCOPED_PRODUCT_COLOR_TOKEN",
                        confidence=0.85,
                        reason=f"Matched scoped catalog '{prod.catalog_name}' with variant '{chosen.variant_name}'",
                    )
                elif len(matched_variants) > 1:
                    return ImageMatchFinding(
                        image=img,
                        status=MatchStatus.AMBIGUOUS,
                        matched_product_id=prod.product_id,
                        matched_catalog_name=prod.catalog_name,
                        candidate_variant_codes=[v.variant_code for v in matched_variants],
                        match_strategy="SCOPED_PRODUCT_AMBIGUOUS",
                        confidence=0.5,
                        reason=(
                            f"Image belongs to catalog '{prod.catalog_name}' but matches "
                            f"{len(matched_variants)} variants: {[v.variant_code for v in matched_variants]}"
                        ),
                    )

        # -----------------------------------------------------------------
        # Priority 3: Supplier SKU Match
        # -----------------------------------------------------------------
        sku_candidates: list[tuple[Product, Variant]] = []
        for prod in products:
            for var in prod.variants:
                sku_norm = var.supplier_sku.strip().casefold()
                if sku_norm in img_tokens or sku_norm in norm_stem:
                    sku_candidates.append((prod, var))

        if sku_candidates:
            # Check if all candidates belong to the exact same variant or single variant
            distinct_variants = {var.variant_code: (prod, var) for prod, var in sku_candidates}
            
            if len(distinct_variants) == 1:
                p, v = list(distinct_variants.values())[0]
                return ImageMatchFinding(
                    image=img,
                    status=MatchStatus.MATCHED,
                    matched_product_id=p.product_id,
                    matched_catalog_name=p.catalog_name,
                    matched_variant_code=v.variant_code,
                    matched_supplier_sku=v.supplier_sku,
                    match_strategy="SUPPLIER_SKU_SINGLE_VARIANT",
                    confidence=0.8,
                    reason=f"Matched single variant '{v.variant_code}' for supplier SKU '{v.supplier_sku}'",
                )
            else:
                # Disambiguate by checking variant index / sequence in filename (e.g., _1, _2)
                p_first, _ = sku_candidates[0]
                all_prod_variants = p_first.variants
                
                # Check for numeric suffix in filename
                digit_match = re.search(r"[_\-](\d+)(?:\.\w+)?$", img.original_filename)
                if digit_match:
                    seq_num = int(digit_match.group(1))
                    # 1-indexed match
                    if 1 <= seq_num <= len(all_prod_variants):
                        v_indexed = all_prod_variants[seq_num - 1]
                        return ImageMatchFinding(
                            image=img,
                            status=MatchStatus.MATCHED,
                            matched_product_id=p_first.product_id,
                            matched_catalog_name=p_first.catalog_name,
                            matched_variant_code=v_indexed.variant_code,
                            matched_supplier_sku=v_indexed.supplier_sku,
                            match_strategy="SUPPLIER_SKU_INDEXED_SEQUENCE",
                            confidence=0.75,
                            reason=f"Matched variant index {seq_num} ('{v_indexed.variant_code}') for supplier SKU '{v_indexed.supplier_sku}'",
                        )

                # Multiple variants share SKU and cannot be disambiguated -> Flag AMBIGUOUS
                return ImageMatchFinding(
                    image=img,
                    status=MatchStatus.AMBIGUOUS,
                    candidate_variant_codes=list(distinct_variants.keys()),
                    matched_supplier_sku=sku_candidates[0][1].supplier_sku,
                    match_strategy="SHARED_SUPPLIER_SKU_AMBIGUOUS",
                    confidence=0.4,
                    reason=(
                        f"Image matches supplier SKU '{sku_candidates[0][1].supplier_sku}' "
                        f"but multiple variants share this SKU ({list(distinct_variants.keys())}) without disambiguator"
                    ),
                )

        # -----------------------------------------------------------------
        # Priority 4: Unmatched
        # -----------------------------------------------------------------
        return ImageMatchFinding(
            image=img,
            status=MatchStatus.UNMATCHED,
            confidence=0.0,
            reason="No reliable variant identity found",
        )

    @staticmethod
    def _clean_token(s: Optional[str]) -> str:
        if not s:
            return ""
        return re.sub(r"[^\w]+", "-", s.strip().lower()).strip("-")
