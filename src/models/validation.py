from typing import Any, Optional
from pydantic import BaseModel, Field
from src.models.enums import ValidationSeverity


class ValidationIssue(BaseModel):
    """Represents an individual validation finding on a product or variant."""
    severity: ValidationSeverity = Field(
        default=ValidationSeverity.ERROR,
        description="Severity classification (ERROR, WARNING, INFO)"
    )
    code: str = Field(
        ...,
        description="Machine-readable error/warning code (e.g. 'MISSING_FIELD', 'DUPLICATE_CODE')"
    )
    message: str = Field(
        ...,
        description="Human-readable explanation of the validation finding"
    )
    field: Optional[str] = Field(
        default=None,
        description="Name of the affected field or schema path"
    )
    supplier_sku: Optional[str] = Field(
        default=None,
        description="Supplier SKU related to this finding"
    )
    variant_code: Optional[str] = Field(
        default=None,
        description="Variant code related to this finding"
    )
    source_row: Optional[int] = Field(
        default=None,
        description="Row number in the original supplier source file (1-indexed)"
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional debugging or entity metadata"
    )


class ValidationReport(BaseModel):
    """Comprehensive validation result across products and variants."""
    total_products: int = Field(
        default=0,
        ge=0,
        description="Total number of product catalogs evaluated"
    )
    total_variants: int = Field(
        default=0,
        ge=0,
        description="Total number of individual variants evaluated"
    )
    issues: list[ValidationIssue] = Field(
        default_factory=list,
        description="List of all recorded validation findings"
    )

    @property
    def is_valid(self) -> bool:
        """Returns True if there are zero ERROR-level issues."""
        return not any(i.severity == ValidationSeverity.ERROR for i in self.issues)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == ValidationSeverity.ERROR for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == ValidationSeverity.WARNING for i in self.issues)

    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]

    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]

    def infos(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.INFO]

    def add_issue(
        self,
        severity: ValidationSeverity,
        code: str,
        message: str,
        field: Optional[str] = None,
        supplier_sku: Optional[str] = None,
        variant_code: Optional[str] = None,
        source_row: Optional[int] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        """Helper to append a new validation issue."""
        self.issues.append(
            ValidationIssue(
                severity=severity,
                code=code,
                message=message,
                field=field,
                supplier_sku=supplier_sku,
                variant_code=variant_code,
                source_row=source_row,
                context=context or {},
            )
        )

    def summary(self) -> dict[str, Any]:
        """Produce a high-level metrics summary dictionary."""
        return {
            "total_products": self.total_products,
            "total_variants": self.total_variants,
            "is_valid": self.is_valid,
            "error_count": len(self.errors()),
            "warning_count": len(self.warnings()),
            "info_count": len(self.infos()),
        }
