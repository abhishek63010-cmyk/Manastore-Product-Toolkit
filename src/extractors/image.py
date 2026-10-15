from pathlib import Path
from typing import Optional, Union

from src.extractors.raw_record import RawRecord, RawTabularData
from src.ocr.parser import ConservativeCandidateParser
from src.ocr.registry import OCRRegistry


class ImageCatalogueExtractor:
    """
    Automatic catalogue extractor for image- and PDF-based supplier catalogues.
    
    Extracts text via OCR (or PDF text extractor), parses structured candidate fields
    (catalogue name, brand, wholesale price, single-piece price, GST, fabric, total designs),
    and transforms the extracted candidates into a standardized RawTabularData representation.
    """

    SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".pdf"}

    @classmethod
    def is_supported(cls, file_path: Union[str, Path]) -> bool:
        path = Path(file_path)
        return path.suffix.lower() in cls.SUPPORTED_EXTENSIONS

    @classmethod
    def extract(
        cls,
        file_path: Union[str, Path],
        ocr_provider: Optional[str] = None,
    ) -> RawTabularData:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found: {path}")

        ext = path.suffix.lower()
        if ext not in cls.SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported catalogue document format '{ext}' for ImageCatalogueExtractor")

        extracted_text = ""

        ocr_result = None
        if ext == ".pdf":
            pdf_extractor = OCRRegistry.get_pdf_extractor()
            pdf_doc = pdf_extractor.extract_from_pdf(path)
            # Combine text from all pages
            extracted_text = "\n".join(page.full_text for page in pdf_doc.pages if page.full_text)
            # If PDF text is empty, attempt OCR on the PDF or pages if supported
            if not extracted_text.strip():
                ocr_extractor = OCRRegistry.get_extractor(ocr_provider)
                ocr_result = ocr_extractor.extract_from_file(path)
                extracted_text = ocr_result.raw_text or ""
        else:
            ocr_extractor = OCRRegistry.get_extractor(ocr_provider)
            ocr_result = ocr_extractor.extract_from_file(path)
            extracted_text = ocr_result.raw_text or ""

        # Debug tracing for diagnostic observability
        provider_name = ocr_extractor.provider_name if 'ocr_extractor' in locals() else 'pdf'
        provider_avail = ocr_extractor.is_available() if 'ocr_extractor' in locals() else True
        text_len = len(extracted_text)
        print(f"[ImageCatalogueExtractor] OCR Provider: '{provider_name}' (available={provider_avail}), extracted {text_len} characters")

        if not extracted_text.strip():
            failure_reason = ""
            if ocr_result and ocr_result.error_message:
                failure_reason = f" (Details: {ocr_result.error_message})"
            elif ocr_result and ocr_result.status:
                failure_reason = f" (Status: {ocr_result.status})"
            raise ValueError(f"No extractable product data found in source image/PDF: {path.name}{failure_reason}")

        # Parse structured candidate fields from extracted OCR/PDF text
        candidates = ConservativeCandidateParser.parse(extracted_text)
        print(f"[ImageCatalogueExtractor] Candidate fields parsed: {len(candidates)}")
        if not candidates:
            raise ValueError(f"No recognizable catalogue fields found in extracted text from: {path.name}")

        # Synthesize a structured row from the parsed candidate fields
        row_data: dict[str, any] = {}
        row_data["raw_ocr_text"] = extracted_text

        # Map candidate values into synthetic columns
        for c in candidates:
            # First candidate found takes priority, unless empty
            if c.field_name not in row_data or row_data[c.field_name] is None:
                row_data[c.field_name] = c.candidate_value

        # Normalize catalog_name alias if present
        if "product_name" in row_data and "catalog_name" not in row_data:
            row_data["catalog_name"] = row_data["product_name"]

        # Reconcile and normalize prices using multi-evidence verification
        from src.ocr.pricing import EvidenceBasedPriceNormalizer
        price_cands = [
            EvidenceBasedPriceNormalizer.extract_price_candidates(extracted_text)
        ]
        flattened_price_cands = [item for sublist in price_cands for item in sublist]
        tot_cnt = row_data.get("total_designs")
        try:
            tot_cnt_int = int(tot_cnt) if tot_cnt is not None else None
        except (ValueError, TypeError):
            tot_cnt_int = None

        norm_prices = EvidenceBasedPriceNormalizer.reconcile_and_normalize_prices(
            candidates=flattened_price_cands,
            total_designs=tot_cnt_int,
            raw_text=extracted_text,
        )
        for k, v in norm_prices.items():
            if v is not None:
                row_data[k] = v

        # Ensure supplier_price reflects dropshipping single-piece price if present, else wholesale
        if "single_piece_price" in row_data and row_data["single_piece_price"] is not None:
            row_data["supplier_price"] = row_data["single_piece_price"]
        elif "wholesale_price" in row_data and row_data["wholesale_price"] is not None:
            row_data["supplier_price"] = row_data["wholesale_price"]
        elif "price" in row_data and row_data["price"] is not None:
            row_data["supplier_price"] = row_data["price"]

        headers = list(row_data.keys())
        record = RawRecord(
            row_number=1,
            data=row_data,
            source_file=str(path),
            sheet_name=None,
        )

        return RawTabularData(
            source_file=str(path),
            sheet_name=None,
            available_sheets=[],
            headers=headers,
            records=[record],
        )
