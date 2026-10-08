from typing import Any, Optional
from pydantic import BaseModel, Field
from src.models.enums import FieldSource


class FieldProvenance(BaseModel):
    """Metadata detailing the origin and transformation history of a specific field."""
    source_type: FieldSource = Field(
        default=FieldSource.SOURCE,
        description="Classification of where this field value came from"
    )
    raw_key: Optional[str] = Field(
        default=None,
        description="Original supplier column/attribute name if extracted from source"
    )
    raw_value: Optional[Any] = Field(
        default=None,
        description="Original unmodified value prior to transformation"
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional context, transformation rules applied, or audit notes"
    )


class ProvenanceRecord(BaseModel):
    """Container for entity-level provenance metadata."""
    source_file: Optional[str] = Field(
        default=None,
        description="Path or identifier of the source file / archive"
    )
    supplier_id: Optional[str] = Field(
        default=None,
        description="Identifier of the supplier profile/adapter used"
    )
    import_batch_id: Optional[str] = Field(
        default=None,
        description="Unique identifier for the import execution batch"
    )
    field_provenance: dict[str, FieldProvenance] = Field(
        default_factory=dict,
        description="Field-by-field provenance map"
    )

    def track_field(
        self,
        field_name: str,
        source_type: FieldSource,
        raw_key: Optional[str] = None,
        raw_value: Optional[Any] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Helper to record provenance for a given field."""
        self.field_provenance[field_name] = FieldProvenance(
            source_type=source_type,
            raw_key=raw_key,
            raw_value=raw_value,
            notes=notes,
        )
