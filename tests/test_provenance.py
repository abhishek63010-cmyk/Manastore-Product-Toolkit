from src.models import (
    FieldProvenance,
    FieldSource,
    Product,
    ProvenanceRecord,
    Variant,
)


def test_field_source_classification():
    """Verify all FieldSource enum variants are distinct and identifiable."""
    assert FieldSource.SOURCE == "SOURCE"
    assert FieldSource.DERIVED == "DERIVED"
    assert FieldSource.AUTO_FILLED == "AUTO_FILLED"
    assert FieldSource.MANUAL_REVIEW == "MANUAL_REVIEW"


def test_field_provenance_tracking():
    """Verify field-level provenance records correctly capture origin metadata."""
    record = ProvenanceRecord(
        source_file="input/AMRA-RAJTEX-001/raw_supplier_data.xlsx",
        supplier_id="amra_adapter",
        import_batch_id="batch_20261007_01",
    )

    # 1. Direct source field
    record.track_field(
        field_name="catalog_name",
        source_type=FieldSource.SOURCE,
        raw_key="catalogue_name",
        raw_value="Rajtex Ajrakh Vol-35",
    )

    # 2. Derived field
    record.track_field(
        field_name="product_name",
        source_type=FieldSource.DERIVED,
        notes="Generated from Brand + Catalog + Category template",
    )

    # 3. Auto-filled default
    record.track_field(
        field_name="publish",
        source_type=FieldSource.AUTO_FILLED,
        raw_value=False,
        notes="Default draft safety flag",
    )

    # 4. Manual review field
    record.track_field(
        field_name="selling_price",
        source_type=FieldSource.MANUAL_REVIEW,
        raw_value=899.0,
        notes="Pricing manually set during commercial review",
    )

    assert len(record.field_provenance) == 4
    assert record.field_provenance["catalog_name"].source_type == FieldSource.SOURCE
    assert record.field_provenance["catalog_name"].raw_key == "catalogue_name"
    assert record.field_provenance["product_name"].source_type == FieldSource.DERIVED
    assert record.field_provenance["publish"].source_type == FieldSource.AUTO_FILLED
    assert record.field_provenance["selling_price"].source_type == FieldSource.MANUAL_REVIEW


def test_provenance_attached_to_domain_models():
    """Verify ProvenanceRecord seamlessly integrates into Product and Variant."""
    product = Product(
        supplier_name="Amra Wholesale",
        catalog_name="Mahotsav Kriti",
        product_name="Mahotsav Soft Chinon Silk Party Wear Saree",
        source_metadata=ProvenanceRecord(
            source_file="Amra_data_with_all_blob_urls.xlsx",
            supplier_id="amra_wholesale",
        ),
    )
    product.source_metadata.track_field(
        field_name="supplier_name",
        source_type=FieldSource.SOURCE,
        raw_key="supplier_name",
        raw_value="Amra Wholesale",
    )

    variant = Variant(
        variant_code="Mahots-K-S3207",
        supplier_sku="AM-17090",
        variant_name="Emerald Green",
        source_metadata=ProvenanceRecord(
            source_file="Amra_data_with_all_blob_urls.xlsx",
        ),
    )
    variant.source_metadata.track_field(
        field_name="variant_name",
        source_type=FieldSource.SOURCE,
        raw_key="varient_name",
        raw_value="Emarald Green",
        notes="Corrected spelling from supplier input",
    )

    assert product.source_metadata.supplier_id == "amra_wholesale"
    assert "supplier_name" in product.source_metadata.field_provenance
    assert variant.source_metadata.field_provenance["variant_name"].raw_key == "varient_name"
