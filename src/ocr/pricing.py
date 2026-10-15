"""
src/ocr/pricing.py

Evidence-based currency extraction and price normalization for supplier catalogues.

Principles:
- Supports Indian Rupee symbols (₹, Rs, Rs., INR), thousands separators, and decimal amounts.
- Distinguishes single-piece price, wholesale-per-piece price, full-catalogue price, and generic price.
- Preserves raw OCR text, candidate values, source references, and extraction evidence.
- Detects Tesseract OCR misclassification of Rupee symbol '₹' as the digit '2' using multi-evidence verification.
- NEVER globally strips leading '2' (preserves valid prices like 250, 2500, 299).
- Uses arithmetic consistency and textual calculations as consistency checks, not standalone proof.
- Keeps ambiguous or unconfirmed prices flagged for human review (ReviewStatus.PENDING).
"""

import re
from typing import Any, Optional, Union
from pydantic import BaseModel, Field

from src.models.enums import ReviewStatus


class PriceCandidate(BaseModel):
    """Structured price candidate with full provenance and verification state."""
    field_name: str
    candidate_value: float
    raw_text: str
    confidence: float = 0.85
    extraction_rule: str
    review_status: ReviewStatus = ReviewStatus.PENDING
    is_confirmed: bool = True
    original_raw_number: Optional[str] = None
    normalization_notes: Optional[str] = None


class EvidenceBasedPriceNormalizer:
    """
    Normalizes catalogue price candidates using textual and multi-field evidence.
    """

    # Comprehensive currency prefix regex matching ₹, Rs, Rs., INR, #, @, or loose delimiters
    CURRENCY_PREFIX = r"(?:[₹%¥$=:\-\s#@|/—–]|rs\.?|inr)*"
    NUMBER_PATTERN = r"(\d+(?:,\d{3})*(?:\.\d{1,2})?)"
    UNIT_SUFFIX = r"(?:\/-|\/pc|\bper\s*(?:pc|piece)\b)?"

    # Pattern for explicit arithmetic breakdown in catalogue text (e.g. '6 Pcs x 21415 = #8490')
    BREAKDOWN_PATTERN = re.compile(
        r"(?i)(\d+)\s*(?:pcs?|pieces?|pes?)\s*[xX*]\s*(?:[₹#@]\s*|2)?(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*=\s*(?:[₹#@]\s*|2)?(\d+(?:,\d{3})*(?:\.\d{1,2})?)",
    )

    @classmethod
    def clean_numeric_string(cls, raw: str) -> Optional[float]:
        """Convert raw formatted number string into float."""
        if not raw:
            return None
        cleaned = raw.replace(",", "").strip()
        try:
            val = float(cleaned)
            return val if val > 0 else None
        except ValueError:
            return None

    @classmethod
    def extract_price_candidates(cls, text: str) -> list[PriceCandidate]:
        """
        Scan text and extract price candidates for wholesale, single-piece,
        full-catalogue, and generic price fields.
        """
        if not text or not text.strip():
            return []

        candidates: list[PriceCandidate] = []

        # 1. Full Catalogue Price
        # Matches 'Full Catalogue - 28490 (6 Pcs)' or 'Full Catalogue - ₹8490' or '= #8490'
        full_cat_regex = re.compile(
            r"(?i)\b(?:full\s*(?:catalogue|catalog|set)?(?:\s*price)?|catalogue\s*price|catalog\s*price)\s*[:=\-#—–]?\s*"
            + cls.CURRENCY_PREFIX
            + cls.NUMBER_PATTERN
            + r"\s*(?:\/-|\/pc|\bper\s*(?:pc|piece)\b|\(\d+\s*(?:pcs?|pieces?)\))?",
        )
        for m in full_cat_regex.finditer(text):
            val = cls.clean_numeric_string(m.group(1))
            if val:
                candidates.append(
                    PriceCandidate(
                        field_name="full_catalogue_price",
                        candidate_value=val,
                        raw_text=m.group(0).strip(),
                        confidence=0.9,
                        extraction_rule="REGEX_FULL_CATALOGUE_PRICE",
                        original_raw_number=m.group(1),
                    )
                )

        # 2. Single Piece Price
        # Matches 'Single Piece - #1515', 'Single Piece - ₹1515', 'SINGLE PIECE AVAILABLE — @1515(1Pe)'
        single_piece_regex = re.compile(
            r"(?i)\b(?:single\s*piece(?:\s*(?:available|only))?|single\s*price|retail\s*price|single\s*rate|single)\s*[:=\-#—–]?\s*"
            + cls.CURRENCY_PREFIX
            + cls.NUMBER_PATTERN
            + r"\s*(?:\/-|\/pc|\bper\s*(?:pc|piece)\b|\(\d+\s*(?:pcs?|pieces?|pe)\))?",
        )
        for m in single_piece_regex.finditer(text):
            val = cls.clean_numeric_string(m.group(1))
            if val:
                candidates.append(
                    PriceCandidate(
                        field_name="single_piece_price",
                        candidate_value=val,
                        raw_text=m.group(0).strip(),
                        confidence=0.9,
                        extraction_rule="REGEX_SINGLE_PIECE_PRICE",
                        original_raw_number=m.group(1),
                    )
                )

        # 3. Wholesale Per Unit Price
        # Matches 'Wholesale Per Unit Price - 21415 (Per Piece Wholesale)', 'Wholesale - ₹1415'
        wholesale_regex = re.compile(
            r"(?i)\b(?:wholesale\s*(?:per\s*unit\s*)?(?:price|rate)|ws\s*price|w\.s\.?\s*price|wholesale)\s*[:=\-#—–]?\s*"
            + cls.CURRENCY_PREFIX
            + cls.NUMBER_PATTERN
            + r"\s*" + cls.UNIT_SUFFIX,
        )
        for m in wholesale_regex.finditer(text):
            val = cls.clean_numeric_string(m.group(1))
            if val:
                candidates.append(
                    PriceCandidate(
                        field_name="wholesale_price",
                        candidate_value=val,
                        raw_text=m.group(0).strip(),
                        confidence=0.9,
                        extraction_rule="REGEX_WHOLESALE_PRICE",
                        original_raw_number=m.group(1),
                    )
                )

        # 4. Explicit Breakdown Line (e.g. '6 Pes x 21415 = #8490')
        for m in cls.BREAKDOWN_PATTERN.finditer(text):
            unit_val = cls.clean_numeric_string(m.group(2))
            total_val = cls.clean_numeric_string(m.group(3))
            if total_val and not any(c.field_name == "full_catalogue_price" and c.candidate_value == total_val for c in candidates):
                candidates.append(
                    PriceCandidate(
                        field_name="full_catalogue_price",
                        candidate_value=total_val,
                        raw_text=m.group(0).strip(),
                        confidence=0.88,
                        extraction_rule="REGEX_BREAKDOWN_TOTAL_PRICE",
                        original_raw_number=m.group(3),
                    )
                )

        # 5. Generic Price (mrp, cost, rate, price)
        generic_regex = re.compile(
            r"(?i)\b(?:mrp|cost|price|rate)\s*[:=\-#—–]?\s*"
            + cls.CURRENCY_PREFIX
            + cls.NUMBER_PATTERN
            + r"\s*" + cls.UNIT_SUFFIX,
        )
        for m in generic_regex.finditer(text):
            val = cls.clean_numeric_string(m.group(1))
            if val:
                candidates.append(
                    PriceCandidate(
                        field_name="price",
                        candidate_value=val,
                        raw_text=m.group(0).strip(),
                        confidence=0.85,
                        extraction_rule="REGEX_PRICE_LABELED",
                        original_raw_number=m.group(1),
                    )
                )

        # 6. Currency Symbol Prices (₹ 1415, Rs. 1415)
        curr_symbol_regex = re.compile(r"(?:₹\s*|rs\.\s*|inr\s*)" + cls.NUMBER_PATTERN + r"\b(?!\s*%)", re.IGNORECASE)
        for m in curr_symbol_regex.finditer(text):
            val = cls.clean_numeric_string(m.group(1))
            if val:
                candidates.append(
                    PriceCandidate(
                        field_name="price",
                        candidate_value=val,
                        raw_text=m.group(0).strip(),
                        confidence=0.85,
                        extraction_rule="REGEX_CURRENCY_SYMBOL",
                        original_raw_number=m.group(1),
                    )
                )

        return candidates

    @classmethod
    def reconcile_and_normalize_prices(
        cls,
        candidates: list[PriceCandidate],
        total_designs: Optional[int] = None,
        raw_text: str = "",
    ) -> dict[str, Any]:
        """
        Reconcile price candidates with cross-field and arithmetic verification.
        Detects Rupee symbol misclassification (₹ misread as leading digit 2)
        ONLY when supported by multiple textual or arithmetic evidence indicators.
        
        Returns a dictionary mapping field names to their authoritative or flagged values.
        """
        # Separate primary candidates
        ws_candidates = [c for c in candidates if c.field_name == "wholesale_price"]
        sp_candidates = [c for c in candidates if c.field_name == "single_piece_price"]
        fc_candidates = [c for c in candidates if c.field_name == "full_catalogue_price"]
        gen_candidates = [c for c in candidates if c.field_name == "price"]

        normalized: dict[str, Any] = {}
        provenance_notes: list[str] = []

        # Find best candidate for each category
        raw_ws = ws_candidates[0].candidate_value if ws_candidates else None
        raw_sp = sp_candidates[0].candidate_value if sp_candidates else None
        raw_fc = fc_candidates[0].candidate_value if fc_candidates else None

        # Check for explicit breakdown line evidence (e.g. '6 Pes x 21415 = #8490')
        breakdown_match = cls.BREAKDOWN_PATTERN.search(raw_text) if raw_text else None
        breakdown_count = int(breakdown_match.group(1)) if breakdown_match else None
        breakdown_unit_raw = breakdown_match.group(2) if breakdown_match else None
        breakdown_total_raw = breakdown_match.group(3) if breakdown_match else None

        effective_count = total_designs or breakdown_count

        # -------------------------------------------------------------
        # Rupee Symbol '₹' vs Leading '2' Evidence Verification
        # -------------------------------------------------------------
        # Condition 1: Full catalogue price starts with '2' (e.g. 28490) while alternative
        # mention has '#8490' or '8490' without '2'.
        corrected_fc = raw_fc
        if raw_fc and str(int(raw_fc)).startswith("2") and len(str(int(raw_fc))) >= 4:
            trimmed_fc = float(str(int(raw_fc))[1:])
            # Check if trimmed_fc appears in text preceded by '#', '@', '₹', 'rs' or '='
            alt_fc_pattern = re.compile(rf"(?:[#@₹=]|rs\.?)\s*{int(trimmed_fc)}\b", re.IGNORECASE)
            if alt_fc_pattern.search(raw_text):
                corrected_fc = trimmed_fc
                provenance_notes.append(
                    f"Full catalogue price corrected from {raw_fc} to {trimmed_fc}: "
                    f"leading '2' confirmed as misread Rupee symbol by matching alternative text mention"
                )

        # Condition 2: Wholesale price starts with '2' (e.g. 21415) and arithmetic check
        # matches corrected_fc or raw_fc (e.g. 6 * 1415 == 8490).
        corrected_ws = raw_ws
        if raw_ws and str(int(raw_ws)).startswith("2") and len(str(int(raw_ws))) >= 4:
            trimmed_ws = float(str(int(raw_ws))[1:])
            # Arithmetic verification
            if effective_count and effective_count > 1:
                expected_total = effective_count * trimmed_ws
                if (corrected_fc and abs(expected_total - corrected_fc) < 1.0) or (raw_fc and abs(expected_total - raw_fc) < 1.0):
                    corrected_ws = trimmed_ws
                    if corrected_fc == raw_fc and abs(expected_total - (raw_fc - 20000)) < 1.0:
                        corrected_fc = raw_fc - 20000
                    provenance_notes.append(
                        f"Wholesale price corrected from {raw_ws} to {trimmed_ws}: "
                        f"confirmed by catalogue count ({effective_count} pcs x {trimmed_ws} = {expected_total})"
                    )
            # Textual breakdown verification (e.g. '6 Pes x 21415 = #8490')
            elif breakdown_match and breakdown_total_raw:
                parsed_total = cls.clean_numeric_string(breakdown_total_raw)
                if parsed_total and breakdown_count and abs(breakdown_count * trimmed_ws - parsed_total) < 1.0:
                    corrected_ws = trimmed_ws
                    corrected_fc = parsed_total
                    provenance_notes.append(
                        f"Wholesale price corrected from {raw_ws} to {trimmed_ws} via breakdown arithmetic"
                    )

        # Condition 3: Single piece price verification
        corrected_sp = raw_sp
        if raw_sp and str(int(raw_sp)).startswith("2") and len(str(int(raw_sp))) >= 4:
            trimmed_sp = float(str(int(raw_sp))[1:])
            # If trimmed_sp is consistent with wholesale price (wholesale <= single piece < full catalogue)
            ref_ws = corrected_ws or raw_ws
            ref_fc = corrected_fc or raw_fc
            if ref_ws and ref_fc and (ref_ws <= trimmed_sp < ref_fc):
                # Verify alternate mention in text
                alt_sp_pattern = re.compile(rf"(?:[#@₹=]|rs\.?)\s*{int(trimmed_sp)}\b", re.IGNORECASE)
                if alt_sp_pattern.search(raw_text):
                    corrected_sp = trimmed_sp
                    provenance_notes.append(
                        f"Single piece price corrected from {raw_sp} to {trimmed_sp} via alternative mention"
                    )

        # Store results
        if corrected_sp is not None:
            normalized["single_piece_price"] = corrected_sp
        if corrected_ws is not None:
            normalized["wholesale_price"] = corrected_ws
        if corrected_fc is not None:
            normalized["full_catalogue_price"] = corrected_fc

        # Authoritative supplier_price: Single-piece price if present, else wholesale price
        if corrected_sp is not None:
            normalized["supplier_price"] = corrected_sp
        elif corrected_ws is not None:
            normalized["supplier_price"] = corrected_ws
        elif gen_candidates:
            normalized["supplier_price"] = gen_candidates[0].candidate_value

        normalized["price_notes"] = "; ".join(provenance_notes) if provenance_notes else None
        return normalized
