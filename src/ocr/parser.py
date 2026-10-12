import re
from typing import Any, Optional

from src.models.enums import ReviewStatus
from src.ocr.models import CandidateField


class ConservativeCandidateParser:
    """
    Conservative parser for extracting domain candidate fields from raw OCR text.
    
    Principles:
    - Evidence-based only: Never infers values without explicit text evidence.
    - Non-destructive: Every extracted value is marked as a candidate with PENDING review status.
    - Preserves matched substrings and raw text provenance for human verification.
    """

    # 1. Design Number Patterns
    DESIGN_PATTERNS = [
        (
            r"(?i)\b(?:design\s*(?:no\.?|num(?:ber)?|#)|d\.?\s*no\.?|d-no\.?|d/no\.?|dn)\s*[:=\-#]?\s*([a-z0-9\-_]+)",
            "REGEX_DESIGN_NUMBER_EXPLICIT",
        ),
        (
            r"(?i)\b(?:style\s*(?:no\.?|code|#)|art(?:\.|\s*no\.?))\s*[:=\-#]?\s*([a-z0-9\-_]+)",
            "REGEX_STYLE_NUMBER",
        ),
    ]

    # 2. SKU / Item Code Patterns
    SKU_PATTERNS = [
        (
            r"(?i)\b(?:sku|supplier\s*sku|item\s*code|item\s*no\.?|product\s*code)\s*[:=\-#]?\s*([a-z0-9\-_]+)",
            "REGEX_SKU_EXPLICIT",
        ),
    ]

    # 3. Price Patterns (Distinguishing Wholesale vs Single Piece vs Generic)
    WHOLESALE_PRICE_PATTERNS = [
        (
            r"(?i)\b(?:wholesale\s*(?:per\s*unit\s*)?(?:price|rate)|ws\s*price|w\.s\.?\s*price|wholesale)\s*[:=\-#—–]?\s*(?:[₹%¥$=:\-\s]|rs\.?|inr)*\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:\/-|\/pc|\bper\s*(?:pc|piece)\b)?",
            "REGEX_WHOLESALE_PRICE",
        ),
    ]

    SINGLE_PIECE_PRICE_PATTERNS = [
        (
            r"(?i)\b(?:single\s*piece(?:\s*only)?|single\s*price|retail\s*price|single\s*rate|single)\s*[:=\-#—–]?\s*(?:[₹%¥$=:\-\s]|rs\.?|inr)*\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:\/-|\/pc|\bper\s*(?:pc|piece)\b)?",
            "REGEX_SINGLE_PIECE_PRICE",
        ),
    ]

    FULL_CATALOGUE_PRICE_PATTERNS = [
        (
            r"(?i)\b(?:full\s*(?:catalogue|catalog)(?:\s*price)?|catalogue\s*price|catalog\s*price)\s*[:=\-#—–]?\s*(?:[₹%¥$=:\-\s]|rs\.?|inr)*\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:\/-|\/pc|\bper\s*(?:pc|piece)\b|\(\d+\s*(?:pcs?|pieces?)\))?",
            "REGEX_FULL_CATALOGUE_PRICE",
        ),
    ]

    PRICE_PATTERNS = [
        (
            r"(?i)\b(?:mrp|cost|price|rate)\s*[:=\-#—–]?\s*(?:[₹%¥$=:\-\s]|rs\.?|inr)*\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:\/-|\/pc|\bper\s*(?:pc|piece)\b)?",
            "REGEX_PRICE_LABELED",
        ),
        (
            r"(?:₹\s*|rs\.\s*)(\d+(?:,\d{3})*(?:\.\d{1,2})?)\b(?!\s*%)",
            "REGEX_CURRENCY_SYMBOL",
        ),
    ]

    # 4. Catalogue / Collection Patterns
    CATALOG_PATTERNS = [
        (
            r"(?i)\b(?:catalogue|catalog|collection|series)\s*(?:name)?\s*[:=\-#]?\s*([a-z0-9\s\-]+?)(?:\r?\n|$|(?=\b(?:price|rate|fabric|design|d\.no|gst|brand)\b))",
            "REGEX_CATALOG_NAME_LABELED",
        ),
    ]

    # 4.1 Brand Patterns
    BRAND_PATTERNS = [
        (
            r"(?i)\b(?:brand\s*(?:name)?)\s*[:=\-#]?\s*([a-z0-9\s\-]+?)(?:\r?\n|$|(?=\b(?:catalogue|catalog|price|rate|fabric|design|gst)\b))",
            "REGEX_BRAND_NAME_LABELED",
        ),
    ]

    # 5. Fabric / Material Patterns
    FABRIC_LABEL_PATTERNS = [
        (
            r"(?i)\b(?:saree\s*fabric|blouse\s*fabric|fabric|material)\s*[:=\-#]?\s*([a-z0-9\s\-]+?)(?:\r?\n|$|(?=\b(?:blouse|work|size|length|price|rate)\b))",
            "REGEX_FABRIC_LABELED",
        ),
    ]

    KNOWN_FABRICS = [
        "munga handloom",
        "modal satin",
        "art silk",
        "crinkle silk",
        "cotton silk",
        "pure silk",
        "tussar silk",
        "banarasi silk",
        "banarasi",
        "georgette",
        "chiffon",
        "organza",
        "crepe",
        "linen",
        "rayon",
        "jacquard",
    ]

    # 6. GST Patterns
    GST_PATTERNS = [
        (
            r"(?i)\b(?:gst|tax)\s*[:=\-#]?\s*(\d+(?:\.\d+)?)\s*%",
            "REGEX_GST_PERCENT",
        ),
    ]

    # 7. Total Designs / Pieces Patterns
    COUNT_PATTERNS = [
        (
            r"(?i)\b(?:total\s*(?:designs?|pieces?|pcs?)|pack\s*of|set\s*of)\s*[:=\-#]?\s*(\d+)\s*(?:pcs?|pieces?|designs?)?",
            "REGEX_TOTAL_DESIGNS",
        ),
    ]

    @classmethod
    def parse(cls, text: str) -> list[CandidateField]:
        """
        Scan raw text and extract structured candidates.
        """
        if not text or not text.strip():
            return []

        candidates: list[CandidateField] = []
        seen_keys: set[str] = set()

        def add_candidate(field: str, val: Any, raw: str, conf: float, rule: str):
            key = f"{field}:{str(val).lower()}"
            if key not in seen_keys:
                seen_keys.add(key)
                candidates.append(
                    CandidateField(
                        field_name=field,
                        candidate_value=val,
                        raw_text=raw.strip(),
                        confidence=conf,
                        review_status=ReviewStatus.PENDING,
                        extraction_rule=rule,
                    )
                )

        # 1. Design numbers
        for pattern, rule in cls.DESIGN_PATTERNS:
            for match in re.finditer(pattern, text):
                val = match.group(1).strip()
                if val and len(val) >= 2:
                    add_candidate("design_number", val, match.group(0), 0.9, rule)

        # 2. SKU
        for pattern, rule in cls.SKU_PATTERNS:
            for match in re.finditer(pattern, text):
                val = match.group(1).strip()
                if val and len(val) >= 2:
                    add_candidate("sku", val, match.group(0), 0.9, rule)

        # 3. Wholesale Price
        for pattern, rule in cls.WHOLESALE_PRICE_PATTERNS:
            for match in re.finditer(pattern, text):
                raw_num = match.group(1).replace(",", "").strip()
                try:
                    num = float(raw_num)
                    if num > 0:
                        add_candidate("wholesale_price", num, match.group(0), 0.9, rule)
                        add_candidate("price", num, match.group(0), 0.9, rule)
                except ValueError:
                    continue

        # 3.1.1 Full Catalogue Price
        for pattern, rule in cls.FULL_CATALOGUE_PRICE_PATTERNS:
            for match in re.finditer(pattern, text):
                raw_num = match.group(1).replace(",", "").strip()
                try:
                    num = float(raw_num)
                    if num > 0:
                        add_candidate("full_catalogue_price", num, match.group(0), 0.9, rule)
                except ValueError:
                    continue

        # 3.2 Single Piece Price
        for pattern, rule in cls.SINGLE_PIECE_PRICE_PATTERNS:
            for match in re.finditer(pattern, text):
                raw_num = match.group(1).replace(",", "").strip()
                try:
                    num = float(raw_num)
                    if num > 0:
                        add_candidate("single_piece_price", num, match.group(0), 0.9, rule)
                except ValueError:
                    continue

        # 3.3 Generic Price
        for pattern, rule in cls.PRICE_PATTERNS:
            for match in re.finditer(pattern, text):
                raw_num = match.group(1).replace(",", "").strip()
                try:
                    num = float(raw_num)
                    if num > 0:
                        add_candidate("price", num, match.group(0), 0.85, rule)
                except ValueError:
                    continue

        # 4. Catalog name
        for pattern, rule in cls.CATALOG_PATTERNS:
            for match in re.finditer(pattern, text):
                val = match.group(1).strip()
                if val and len(val) >= 3:
                    add_candidate("catalog_name", val, match.group(0), 0.8, rule)

        # 4.1 Brand name
        for pattern, rule in cls.BRAND_PATTERNS:
            for match in re.finditer(pattern, text):
                val = match.group(1).strip()
                if val and len(val) >= 2:
                    add_candidate("brand_name", val, match.group(0), 0.85, rule)

        # 5. Fabric (labeled or unambiguous known fabric)
        fabric_found = False
        for pattern, rule in cls.FABRIC_LABEL_PATTERNS:
            for match in re.finditer(pattern, text):
                val = match.group(1).strip()
                if val and len(val) >= 3:
                    add_candidate("fabric", val, match.group(0), 0.85, rule)
                    fabric_found = True

        if not fabric_found:
            # Check for known fabric keywords mentioned in text
            text_lower = text.lower()
            for kf in cls.KNOWN_FABRICS:
                # Use word boundary search
                match = re.search(r"\b" + re.escape(kf) + r"\b", text_lower)
                if match:
                    title_case_fabric = kf.title()
                    add_candidate("fabric", title_case_fabric, match.group(0), 0.75, "KEYWORD_KNOWN_FABRIC")
                    break

        # 6. GST
        for pattern, rule in cls.GST_PATTERNS:
            for match in re.finditer(pattern, text):
                try:
                    gst = float(match.group(1))
                    if 0 <= gst <= 100:
                        add_candidate("gst_percent", gst, match.group(0), 0.95, rule)
                except ValueError:
                    continue

        # 7. Total designs
        for pattern, rule in cls.COUNT_PATTERNS:
            for match in re.finditer(pattern, text):
                try:
                    cnt = int(match.group(1))
                    if cnt > 0:
                        add_candidate("total_designs", cnt, match.group(0), 0.9, rule)
                except ValueError:
                    continue

        # 8. Generic line-by-line label: value parsing with multiline continuation support
        # Pre-group lines into labeled blocks allowing continuation lines
        labeled_blocks: list[tuple[str, str, str]] = []
        current_k: Optional[str] = None
        current_v_parts: list[str] = []
        current_raw_lines: list[str] = []

        delimiter_regex = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9\s/_\.]{1,40}?)\s*[:=\-–—]\s*(.*)$")

        for line in text.splitlines():
            line_s = line.strip()
            if not line_s:
                # Blank line indicates paragraph / field boundary; close active field
                if current_k and current_v_parts:
                    val_str = " ".join(current_v_parts).strip()
                    val_str = re.sub(r"\s+", " ", val_str)
                    raw_str = "\n".join(current_raw_lines)
                    labeled_blocks.append((current_k, val_str, raw_str))
                current_k = None
                current_v_parts = []
                current_raw_lines = []
                continue

            match = delimiter_regex.match(line_s)
            if match:
                # Save previous block if exists
                if current_k and current_v_parts:
                    val_str = " ".join(current_v_parts).strip()
                    val_str = re.sub(r"\s+", " ", val_str)
                    raw_str = "\n".join(current_raw_lines)
                    labeled_blocks.append((current_k, val_str, raw_str))

                k_candidate = match.group(1).strip()
                v_candidate = match.group(2).strip()
                current_k = k_candidate
                current_v_parts = [v_candidate] if v_candidate else []
                current_raw_lines = [line_s]
            else:
                # If we have an active labeled field, this is a continuation line
                if current_k is not None:
                    current_v_parts.append(line_s)
                    current_raw_lines.append(line_s)

        if current_k and current_v_parts:
            val_str = " ".join(current_v_parts).strip()
            val_str = re.sub(r"\s+", " ", val_str)
            raw_str = "\n".join(current_raw_lines)
            labeled_blocks.append((current_k, val_str, raw_str))

        for k_raw, v_raw, raw_block in labeled_blocks:
            if k_raw and v_raw and len(k_raw) >= 2 and len(v_raw) >= 1:
                norm_k = re.sub(r"[^\w]+", "_", k_raw.lower()).strip("_")
                # Do not override core regex fields unless beneficial
                if norm_k not in ("price", "rate", "gst", "total_designs", "design_no", "d_no", "full_catalogue_price"):
                    add_candidate(norm_k, v_raw, raw_block, 0.85, "REGEX_KEY_VALUE_LINE")

        return candidates
