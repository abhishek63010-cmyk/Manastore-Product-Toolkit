from pathlib import Path
import pytest
from PIL import Image

from src.models.enums import ReviewStatus
from src.ocr.fake import FakeOCRExtractor
from src.ocr.models import OCRResult, OCRStatus
from src.ocr.parser import ConservativeCandidateParser
from src.ocr.pdf import PDFTextExtractor
from src.ocr.registry import OCRRegistry
from src.ocr.tesseract import TesseractOCRExtractor


@pytest.fixture
def sample_image(tmp_path: Path) -> Path:
    """Create a minimal synthetic JPEG image for testing."""
    img_path = tmp_path / "test_saree_photo.jpg"
    img = Image.new("RGB", (200, 200), color=(255, 255, 255))
    img.save(img_path, format="JPEG")
    return img_path


# =====================================================================
# 1. Fake OCR Extractor Tests
# =====================================================================

def test_fake_ocr_success_with_candidates(sample_image: Path):
    """Verify FakeOCRExtractor extracts text, candidates, bounding boxes deterministically."""
    mock_text = (
        "Catalogue: RAJTEX NOORANI\n"
        "Design No: RN-101\n"
        "Fabric: Munga Handloom\n"
        "Wholesale Price: ₹1415\n"
        "GST: 5%\n"
        "Total Designs: 6 Pcs\n"
    )
    extractor = FakeOCRExtractor(
        mock_data={sample_image.name: mock_text},
        confidence=0.95,
    )

    result = extractor.extract_from_file(sample_image, batch_id="batch_001")

    assert result.status == OCRStatus.SUCCESS
    assert result.provider == "fake_ocr"
    assert result.source_filename == sample_image.name
    assert result.confidence == 0.95
    assert result.batch_id == "batch_001"
    assert len(result.bounding_boxes) > 0
    assert result.is_successful is True

    # Candidate fields verification
    design_cand = result.get_candidate("design_number")
    assert design_cand is not None
    assert design_cand.candidate_value == "RN-101"
    assert design_cand.review_status == ReviewStatus.PENDING

    fabric_cand = result.get_candidate("fabric")
    assert fabric_cand is not None
    assert "Munga Handloom" in fabric_cand.candidate_value

    price_cand = result.get_candidate("price")
    assert price_cand is not None
    assert price_cand.candidate_value == 1415.0

    gst_cand = result.get_candidate("gst_percent")
    assert gst_cand is not None
    assert gst_cand.candidate_value == 5.0

    count_cand = result.get_candidate("total_designs")
    assert count_cand is not None
    assert count_cand.candidate_value == 6


def test_fake_ocr_no_text_detected(sample_image: Path):
    """Verify behavior when an image contains no recognizable text."""
    extractor = FakeOCRExtractor(simulate_no_text=True)
    result = extractor.extract_from_file(sample_image)

    assert result.status == OCRStatus.NO_TEXT_DETECTED
    assert result.raw_text == ""
    assert len(result.candidate_fields) == 0
    assert result.is_successful is False


def test_fake_ocr_unsupported_format(tmp_path: Path):
    """Verify non-image formats are safely rejected with UNSUPPORTED_FORMAT."""
    bad_file = tmp_path / "document.xyz"
    bad_file.write_text("random payload")

    extractor = FakeOCRExtractor()
    result = extractor.extract_from_file(bad_file)

    assert result.status == OCRStatus.UNSUPPORTED_FORMAT
    assert "Unsupported image format" in result.error_message
    assert result.is_successful is False


def test_fake_ocr_provider_unavailable(sample_image: Path):
    """Verify provider unavailable status is reported cleanly without raising."""
    extractor = FakeOCRExtractor(available=False)
    result = extractor.extract_from_file(sample_image)

    assert result.status == OCRStatus.PROVIDER_UNAVAILABLE
    assert "unavailable" in result.error_message.lower()


def test_fake_ocr_simulated_failure(sample_image: Path):
    """Verify simulated error handling retains source identity and failure status."""
    extractor = FakeOCRExtractor(simulate_failure=True)
    result = extractor.extract_from_file(sample_image)

    assert result.status == OCRStatus.FAILED
    assert result.source_filename == sample_image.name
    assert "failure" in result.error_message.lower()


def test_fake_ocr_missing_file_handling(tmp_path: Path):
    """Verify missing file path produces clean FAILED status."""
    missing = tmp_path / "does_not_exist.jpg"
    extractor = FakeOCRExtractor()
    result = extractor.extract_from_file(missing)

    assert result.status == OCRStatus.FAILED
    assert "not found" in result.error_message.lower()


# =====================================================================
# 2. Conservative Candidate Parser Tests
# =====================================================================

def test_parser_extracts_all_patterns():
    """Verify conservative extraction across design number, SKU, price, fabric, catalog."""
    raw = (
        "COLLECTION: Kanjivaram Heritage\n"
        "Style No: KNJ-502-RED\n"
        "SKU: RAJ-SKU-9901\n"
        "Material: Banarasi Silk\n"
        "Rate: ₹ 2,850/-\n"
        "Tax: 5%\n"
        "Pack of 4\n"
    )
    candidates = ConservativeCandidateParser.parse(raw)
    field_map = {c.field_name: c.candidate_value for c in candidates}

    assert field_map.get("catalog_name") == "Kanjivaram Heritage"
    assert field_map.get("design_number") == "KNJ-502-RED"
    assert field_map.get("sku") == "RAJ-SKU-9901"
    assert field_map.get("fabric") == "Banarasi Silk"
    assert field_map.get("price") == 2850.0
    assert field_map.get("gst_percent") == 5.0
    assert field_map.get("total_designs") == 4

    # Every candidate must default to PENDING review status
    for c in candidates:
        assert c.review_status == ReviewStatus.PENDING
        assert c.raw_text != ""
        assert c.confidence > 0.0


@pytest.mark.parametrize(
    ("raw", "expected_count"),
    [
        ("Work ajrakh PrintedTotel Designs\n6 Pes", 6),
        ("Totsl Designs - 6 Pes", 6),
        ("Totsl Designs -5 Pes", 5),
    ],
)
def test_parser_extracts_design_counts_with_ocr_misspellings(raw, expected_count):
    candidates = ConservativeCandidateParser.parse(raw)

    assert next(c.candidate_value for c in candidates if c.field_name == "total_designs") == expected_count


def test_parser_empty_and_no_pattern_text():
    """Verify parser returns empty candidates when no patterns are detected."""
    assert ConservativeCandidateParser.parse("") == []
    assert ConservativeCandidateParser.parse("   \n\t  ") == []
    assert ConservativeCandidateParser.parse("Hello world without any apparel keywords or numbers") == []


def test_parser_never_invents_prices_without_evidence():
    """Verify numbers that are not prices/rates are not classified as price."""
    text = "Room 304, Street 12, Phone 9876543210"
    candidates = ConservativeCandidateParser.parse(text)
    assert not any(c.field_name == "price" for c in candidates)


# =====================================================================
# 3. Real Provider (Tesseract) Graceful Degradation Tests
# =====================================================================

def test_tesseract_unavailable_graceful_handling(sample_image: Path):
    """
    Verify Tesseract provider returns clean PROVIDER_UNAVAILABLE status
    when system binary is missing, with clear setup instructions and no crashes.
    """
    # Provide dummy binary path to guarantee unavailable detection
    extractor = TesseractOCRExtractor(tesseract_cmd="non_existent_tesseract_binary_xyz")
    assert extractor.is_available() is False

    result = extractor.extract_from_file(sample_image)
    assert result.status == OCRStatus.PROVIDER_UNAVAILABLE
    assert "tesseract" in result.error_message.lower()
    assert "https://github.com" in result.error_message
    assert result.is_successful is False


# =====================================================================
# 4. PDF Text Extraction & Scanned Document Tests
# =====================================================================

def test_pdf_extractor_unsupported_format(tmp_path: Path):
    """Verify non-PDF file returns UNSUPPORTED_FORMAT."""
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("Hello")
    extractor = PDFTextExtractor()
    results = extractor.extract_pdf(txt_file)

    assert len(results) == 1
    assert results[0].status == OCRStatus.UNSUPPORTED_FORMAT
    assert "Expected '.pdf'" in results[0].error_message


def test_pdf_extractor_scanned_detection(tmp_path: Path):
    """
    Verify synthetic raster PDF with image object but no text stream
    is flagged as OCR_REQUIRED rather than inventing text.
    """
    pdf_path = tmp_path / "scanned_flyer.pdf"
    # Minimal PDF syntax containing an image object without BT/ET text operators
    raster_pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /XObject << /Im1 4 0 R >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Type /XObject /Subtype /Image /Width 100 /Height 100 >>\nstream\n"
        b"fake_image_bytes\nendstream\nendobj\n"
        b"xref\n0 5\n0000000000 65535 f \n"
        b"trailer\n<< /Root 1 0 R >>\n%%EOF"
    )
    pdf_path.write_bytes(raster_pdf_bytes)

    extractor = PDFTextExtractor()
    results = extractor.extract_pdf(pdf_path)

    assert len(results) == 1
    assert results[0].status == OCRStatus.OCR_REQUIRED
    assert "scanned" in results[0].error_message.lower()
    assert results[0].raw_text == ""


def test_pdf_extractor_text_based_pdf(tmp_path: Path):
    """
    Verify text-based digital PDF stream extracts text and candidates cleanly.
    """
    pdf_path = tmp_path / "digital_catalog.pdf"
    content = b"BT /F1 12 Tf (Design No: AMRA-801) Tj (Price: 950) Tj ET"
    stream_len = len(content)
    header = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>\nendobj\n"
    )
    obj4_header = f"4 0 obj\n<< /Length {stream_len} >>\nstream\n".encode("ascii")
    footer = (
        b"\nendstream\nendobj\n"
        b"xref\n0 5\n0000000000 65535 f \n"
        b"trailer\n<< /Root 1 0 R >>\n%%EOF"
    )
    digital_pdf = header + obj4_header + content + footer
    pdf_path.write_bytes(digital_pdf)

    extractor = PDFTextExtractor()
    results = extractor.extract_pdf(pdf_path)

    assert len(results) == 1
    assert results[0].status == OCRStatus.SUCCESS
    assert "Design No: AMRA-801" in results[0].raw_text
    assert results[0].get_candidate_value("design_number") == "AMRA-801"


# =====================================================================
# 5. Registry & Pipeline Integration Tests
# =====================================================================

def test_ocr_registry_resolution():
    """Verify registry resolves fake, tesseract, and custom extractors."""
    fake = OCRRegistry.get_extractor("fake")
    assert isinstance(fake, FakeOCRExtractor)

    tess = OCRRegistry.get_extractor("tesseract")
    assert isinstance(tess, TesseractOCRExtractor)

    pdf_ext = OCRRegistry.get_pdf_extractor()
    assert isinstance(pdf_ext, PDFTextExtractor)

    with pytest.raises(ValueError, match="Unknown OCR provider"):
        OCRRegistry.get_extractor("unknown_xyz")


def test_pipeline_with_ocr_enabled_integration(tmp_path: Path):
    """Verify PipelineOrchestrator runs OCR stage when enable_ocr is configured."""
    import pandas as pd
    from src.pipeline.config import PipelineConfig
    from src.pipeline.orchestrator import PipelineOrchestrator

    # Create dummy excel input
    excel_path = tmp_path / "supplier_catalog.xlsx"
    pd.DataFrame([{
        "Supplier": "Acme Sarees",
        "Catalog Name": "Royal Silk Vol 1",
        "Product Name": "Royal Saree",
        "Variant Name": "Red",
        "Supplier SKU": "SKU-RED-01",
        "Cost": 1200,
        "GST": 5,
        "Stock": 10,
    }]).to_excel(excel_path, index=False)

    # Create dummy images directory
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    img_path = img_dir / "SKU-RED-01.jpg"
    Image.new("RGB", (100, 100), color=(255, 0, 0)).save(img_path)

    config = PipelineConfig(
        input_path=excel_path,
        output_dir=tmp_path / "out",
        images_path=img_dir,
        enable_ocr=True,
        ocr_provider="fake",
        dry_run=True,
    )

    result = PipelineOrchestrator.run(config)
    assert result.success is True
    assert len(result.ocr_results) == 1
    assert result.ocr_results[0].status in (OCRStatus.SUCCESS, OCRStatus.NO_TEXT_DETECTED)
    assert result.ocr_results[0].provider == "fake_ocr"
