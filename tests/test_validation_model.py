from src.models import (
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
)


def test_validation_severity_levels():
    """Verify ValidationSeverity levels."""
    assert ValidationSeverity.ERROR == "ERROR"
    assert ValidationSeverity.WARNING == "WARNING"
    assert ValidationSeverity.INFO == "INFO"


def test_validation_report_empty():
    """Verify clean state for an empty ValidationReport."""
    report = ValidationReport(total_products=10, total_variants=25)
    assert report.is_valid is True
    assert report.has_errors is False
    assert report.has_warnings is False
    assert len(report.errors()) == 0
    assert len(report.warnings()) == 0
    assert len(report.infos()) == 0

    summary = report.summary()
    assert summary["total_products"] == 10
    assert summary["total_variants"] == 25
    assert summary["is_valid"] is True
    assert summary["error_count"] == 0


def test_validation_report_with_mixed_issues():
    """Verify report properly categorizes and aggregates ERROR, WARNING, and INFO issues."""
    report = ValidationReport(total_products=2, total_variants=5)

    # 1. Error issue
    report.add_issue(
        severity=ValidationSeverity.ERROR,
        code="INVALID_SELLING_PRICE",
        message="Selling price cannot be less than or equal to 0",
        field="selling_price",
        supplier_sku="AM-17090",
        variant_code="Mahots-K-S3207",
        source_row=2,
    )

    # 2. Warning issue
    report.add_issue(
        severity=ValidationSeverity.WARNING,
        code="UNVERIFIED_IMAGE_DIMENSIONS",
        message="Image resolution is lower than 1080px",
        field="primary_image_url",
        supplier_sku="AM-17090",
        variant_code="Mahots-K-S3208",
        source_row=3,
    )

    # 3. Info issue
    report.add_issue(
        severity=ValidationSeverity.INFO,
        code="AUTO_GENERATED_TITLE",
        message="Product title was synthesized from template",
        field="product_name",
        supplier_sku="AM-17090",
        source_row=2,
    )

    assert report.is_valid is False
    assert report.has_errors is True
    assert report.has_warnings is True
    assert len(report.errors()) == 1
    assert len(report.warnings()) == 1
    assert len(report.infos()) == 1

    error = report.errors()[0]
    assert error.code == "INVALID_SELLING_PRICE"
    assert error.variant_code == "Mahots-K-S3207"
    assert error.source_row == 2

    summary = report.summary()
    assert summary["is_valid"] is False
    assert summary["error_count"] == 1
    assert summary["warning_count"] == 1
    assert summary["info_count"] == 1


def test_validation_report_warning_only_remains_valid():
    """Verify that warnings and infos do not invalidate the report (is_valid remains True)."""
    report = ValidationReport(total_products=1, total_variants=2)

    report.add_issue(
        severity=ValidationSeverity.WARNING,
        code="PENDING_REVIEW",
        message="Product description is marked for manual review",
        field="description",
    )
    report.add_issue(
        severity=ValidationSeverity.INFO,
        code="PRICE_MARGIN_HEALTHY",
        message="Gross margin exceeds 20%",
    )

    assert report.is_valid is True
    assert report.has_errors is False
    assert report.has_warnings is True
    assert len(report.errors()) == 0
    assert len(report.warnings()) == 1
    assert len(report.infos()) == 1
