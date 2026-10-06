import json
from pathlib import Path

import pandas as pd


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
    "image_urls",
    "field_provenance",
    "source_fields",
    "review_status",
]

SOURCE_TO_MANA = {
    "catalogue_name": "catalog_name",
    "saree_fabric": "fabric",
}


def _json_value(value):
    if pd.isna(value):
        return ""
    if hasattr(value, "item"):
        value = value.item()
    return value if isinstance(value, (str, int, float, bool)) else str(value)


def map_to_manastore(
    df: pd.DataFrame, field_mapping: dict | None = None
) -> pd.DataFrame:
    """Map normalized supplier fields without inventing product attributes."""

    mapping = {**SOURCE_TO_MANA, **(field_mapping or {})}
    mapped = pd.DataFrame(index=df.index)
    source_targets = {}
    for source in df.columns:
        target = mapping.get(source, source)
        if target in MANASTORE_COLUMNS and target not in {
            "image_url", "image_urls", "image-sku", "publish",
            "field_provenance", "source_fields", "review_status",
        }:
            if target in source_targets.values():
                raise ValueError(f"Multiple source columns map to {target!r}.")
            mapped[target] = df[source]
            source_targets[source] = target

    if "supplier_sku" not in mapped:
        raise ValueError("Missing required column: supplier_sku")
    if mapped["supplier_sku"].fillna("").astype(str).str.strip().eq("").any():
        raise ValueError("Each supplier product row must have a supplier_sku.")
    mapped["supplier_sku"] = mapped["supplier_sku"].map(
        lambda value: "" if pd.isna(value) else str(value).strip()
    )

    for column in MANASTORE_COLUMNS:
        if column not in mapped:
            mapped[column] = ""

    # Approved-image upload is a separate stage; never retain guessed URLs.
    mapped["image_url"] = ""
    mapped["image_urls"] = ""
    mapped["image-sku"] = ""
    mapped["publish"] = False

    for index in df.index:
        origins = {
            target: "source" for target in source_targets.values()
        }
        for column in MANASTORE_COLUMNS:
            if column not in origins and column not in {
                "field_provenance", "source_fields", "review_status"
            }:
                origins[column] = "manual_review"
        mapped.at[index, "field_provenance"] = json.dumps(
            origins, sort_keys=True
        )
        mapped.at[index, "source_fields"] = json.dumps(
            {
                str(column): _json_value(df.at[index, column])
                for column in df.columns
            },
            sort_keys=True,
        )

    mapped["review_status"] = "PENDING_REVIEW"
    return mapped.reindex(columns=MANASTORE_COLUMNS)


def main():
    project_root = Path(__file__).resolve().parents[2]
    input_file = project_root / "output" / "extracted_supplier_data.xlsx"
    output_dir = project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_excel(input_file, dtype=str)
    mapped = map_to_manastore(df)
    output_file = output_dir / "manastore_mapped_product.xlsx"
    mapped.to_excel(output_file, index=False)
    print(f"Products mapped: {len(mapped)}")
    print(f"Output saved: {output_file}")


if __name__ == "__main__":
    main()
