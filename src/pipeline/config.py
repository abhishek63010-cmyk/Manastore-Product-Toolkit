from pathlib import Path
from typing import Optional, Union
from pydantic import BaseModel, Field, field_validator


class PipelineConfig(BaseModel):
    """
    Configuration options for the End-to-End Product Ingestion and Export Pipeline.
    """
    input_path: Path = Field(
        ...,
        description="Path to supplier input file (CSV, TSV, or XLSX)"
    )
    output_dir: Path = Field(
        default=Path("./output"),
        description="Directory where output files and reports will be saved"
    )
    supplier: Optional[str] = Field(
        default=None,
        description="Supplier name override (e.g. 'amra', 'generic'). Auto-detected if omitted."
    )
    sheet_name: Optional[str] = Field(
        default=None,
        description="Optional worksheet name for multi-sheet Excel workbooks"
    )
    images_path: Optional[Path] = Field(
        default=None,
        description="Optional path to directory or ZIP archive containing product images"
    )
    upload_images: bool = Field(
        default=False,
        description="Whether to upload matched images to Blob storage"
    )
    upload_prefix: str = Field(
        default="products",
        description="Prefix path for destination keys in blob storage"
    )
    export_formats: list[str] = Field(
        default_factory=lambda: ["xlsx", "json"],
        description="Output formats to export: 'xlsx', 'json', 'csv'"
    )
    dry_run: bool = Field(
        default=False,
        description="If True, executes pipeline and validation without writing files or uploading"
    )
    fail_on_error: bool = Field(
        default=False,
        description="If True, pipeline execution fails if validation errors are present"
    )
    generate_report: bool = Field(
        default=True,
        description="Whether to generate structured validation report artifacts"
    )
    max_gallery_columns: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum gallery image columns to generate in flat tabular output"
    )
    verbose: bool = Field(
        default=False,
        description="Enable detailed console output during pipeline run"
    )

    @field_validator("input_path", "output_dir", "images_path", mode="before")
    @classmethod
    def parse_path(cls, v: Union[str, Path, None]) -> Optional[Path]:
        if v is None:
            return None
        return Path(v)

    @field_validator("export_formats", mode="before")
    @classmethod
    def clean_formats(cls, v: Union[str, list[str]]) -> list[str]:
        if isinstance(v, str):
            formats = [fmt.strip().lower().lstrip(".") for fmt in v.split(",") if fmt.strip()]
            return formats or ["xlsx", "json"]
        return [str(fmt).lower().lstrip(".") for fmt in v]
