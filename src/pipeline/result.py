from typing import Any, Optional
from pydantic import BaseModel, Field

from src.exporters.canonical import CanonicalExport
from src.images.matcher import ImageMatchReport
from src.images.uploader import ImageUploadSummary
from src.models.product import Product
from src.models.validation import ValidationReport


class PipelineMetrics(BaseModel):
    """Execution counts and summary statistics for a pipeline run."""
    total_source_rows: int = 0
    total_products: int = 0
    total_variants: int = 0
    total_images_discovered: int = 0
    total_images_matched: int = 0
    total_images_uploaded: int = 0
    total_validation_errors: int = 0
    total_validation_warnings: int = 0
    is_valid: bool = True
    success: bool = True


class PipelineResult(BaseModel):
    """
    Complete structured output returned by PipelineOrchestrator.run().
    """
    success: bool = True
    adapter_used: str = ""
    metrics: PipelineMetrics = Field(default_factory=PipelineMetrics)
    output_files: dict[str, str] = Field(
        default_factory=dict,
        description="Map of export/report format to generated absolute file paths"
    )
    products: list[Product] = Field(default_factory=list)
    export_data: Optional[CanonicalExport] = None
    validation_report: Optional[ValidationReport] = None
    match_report: Optional[ImageMatchReport] = None
    upload_summary: Optional[ImageUploadSummary] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert result into a serializable summary dictionary."""
        return {
            "success": self.success,
            "adapter_used": self.adapter_used,
            "metrics": self.metrics.model_dump(),
            "output_files": self.output_files,
            "error_message": self.error_message,
        }

    def format_summary_text(self) -> str:
        """Render a readable summary table for terminal logging."""
        status_str = "SUCCESS" if self.success else "FAILED"
        val_status = "PASSED" if self.metrics.is_valid else "FAILED"

        lines = [
            "==================================================",
            f"MANA STORE PIPELINE EXECUTION: {status_str}",
            "==================================================",
            f"Adapter:            {self.adapter_used}",
            f"Source Rows:        {self.metrics.total_source_rows}",
            f"Products Created:   {self.metrics.total_products}",
            f"Variants Created:   {self.metrics.total_variants}",
            "--------------------------------------------------",
            f"Images Discovered:  {self.metrics.total_images_discovered}",
            f"Images Matched:     {self.metrics.total_images_matched}",
            f"Images Uploaded:    {self.metrics.total_images_uploaded}",
            "--------------------------------------------------",
            f"Validation Status:  {val_status}",
            f"Validation Errors:  {self.metrics.total_validation_errors}",
            f"Validation Warnings:{self.metrics.total_validation_warnings}",
            "==================================================",
        ]

        if self.output_files:
            lines.append("Generated Output Files:")
            for key, path in self.output_files.items():
                lines.append(f"  - [{key.upper()}]: {path}")
            lines.append("==================================================")

        if self.error_message:
            lines.append(f"Execution Error: {self.error_message}")
            lines.append("==================================================")

        return "\n".join(lines)

    def print_summary(self) -> None:
        """Print execution summary to stdout."""
        print(self.format_summary_text())
