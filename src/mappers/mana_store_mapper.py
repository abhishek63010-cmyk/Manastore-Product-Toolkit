
from pathlib import Path
import pandas as pd


# Standard fields for the product import workbook.
MANASTORE_COLUMNS = [
    "supplier_name",
    "catalog_name",
    "product_group",
    "product_name",
    "varient_name",
    "supplier_sku",
    "product_type",
    "category_slug",
    "brand_name",
    "fabric",
    "blouse_fabric",
    "work",
    "occasion",
    "size",
    "description",
    "supplier_price",
    "gst_percent",
    "selling_proce",
    "image-sku",
    "image_url",
    "publish",
]


def map_to_manastore(df: pd.DataFrame) -> pd.DataFrame:
    """Map extracted supplier fields to ManaStore import columns."""

    mapped = pd.DataFrame(index=df.index)

    # Direct field mappings
    field_mapping = {
        "supplier_name": "supplier_name",
        "catalogue_name": "catalog_name",
        "brand_name": "brand_name",
        "supplier_sku": "supplier_sku",
        "product_type": "product_type",
        "saree_fabric": "fabric",
        "blouse_fabric": "blouse_fabric",
        "work": "work",
        "occasion": "occasion",
        "supplier_price": "supplier_price",
        "gst_percent": "gst_percent",
    }

    for source, target in field_mapping.items():
        if source in df.columns:
            mapped[target] = df[source]

    # Fields requiring refinement or user input
    mapped["product_group"] = "Sarees"
    mapped["product_name"] = ""
    mapped["varient_name"] = ""
    mapped["category_slug"] = "sarees"
    mapped["size"] = (
        "Saree 5.5 m, blouse 0.8 m"
    )
    mapped["description"] = ""
    mapped["selling_proce"] = ""
    mapped["image-sku"] = ""
    mapped["image_url"] = ""

    # Products must remain unpublished until reviewed.
    mapped["publish"] = False

    # Keep all expected columns in the correct order.
    return mapped.reindex(columns=MANASTORE_COLUMNS)


def main():
    project_root = Path(__file__).resolve().parents[2]

    input_file = (
        project_root
        / "output"
        / "extracted_supplier_data.xlsx"
    )

    output_dir = project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(input_file, dtype={"supplier_sku": str})
    mapped = map_to_manastore(df)

    output_file = (
        output_dir / "manastore_mapped_product.xlsx"
    )

    mapped.to_excel(output_file, index=False)

    print("ManaStore schema mapping successful.")
    print(f"Products mapped: {len(mapped)}")
    print(f"Columns generated: {len(mapped.columns)}")
    print(f"Output saved: {output_file}")


if __name__ == "__main__":
    main()
