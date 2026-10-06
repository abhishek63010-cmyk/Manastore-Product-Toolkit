from typing import Any, Optional
from pydantic import BaseModel, Field


class RawRecord(BaseModel):
    """
    Represents an unmapped, raw row extracted from a tabular supplier source.
    Preserves exact column headers, values, and origin provenance.
    """
    row_number: int = Field(
        ...,
        ge=1,
        description="1-indexed row position in the source file or worksheet"
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw key-value pairs representing original column names and cell values"
    )
    source_file: str = Field(
        ...,
        description="Path or filename of the source dataset"
    )
    sheet_name: Optional[str] = Field(
        default=None,
        description="Worksheet name if extracted from a multi-sheet spreadsheet"
    )

    def get(self, key: str, default: Any = None) -> Any:
        """Case-insensitive key lookup helper for raw column values."""
        key_cf = key.strip().casefold()
        for k, v in self.data.items():
            if str(k).strip().casefold() == key_cf:
                return v
        return default


class RawTabularData(BaseModel):
    """
    Container representing the entire raw extracted dataset from a file/sheet.
    """
    source_file: str = Field(
        ...,
        description="Path or identifier of the source file"
    )
    sheet_name: Optional[str] = Field(
        default=None,
        description="Worksheet name processed, if applicable"
    )
    available_sheets: list[str] = Field(
        default_factory=list,
        description="List of all available sheets if source is a workbook"
    )
    headers: list[str] = Field(
        default_factory=list,
        description="Preserved column header names in original order"
    )
    records: list[RawRecord] = Field(
        default_factory=list,
        description="List of individual raw record rows"
    )

    @property
    def total_rows(self) -> int:
        return len(self.records)

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self):
        return iter(self.records)
