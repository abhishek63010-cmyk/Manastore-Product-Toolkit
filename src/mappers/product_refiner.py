import json
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
SCHEMA_FILE = BASE_DIR / "config" / "mana_store_schema.json"
INPUT_FILE = BASE_DIR / "output" / "manastore_mapped_product.xlsx"
OUTPUT_FILE = BASE_DIR / "output" / "product_refinement_review.xlsx"

with SCHEMA_FILE.open(encoding="utf-8") as handle:
    SCHEMA = json.load(handle)

def _text(row, field):
    value = row.get(field, "")
    return "" if pd.isna(value) else str(value).strip()

def refine_product(row):
    """Prepare a review-ready record without inventing supplier facts."""
    supplier = _text(row, "supplier_name")
    brand = _text(row, "brand_name")
    product_type = _text(row, "product_type")
    fabric = _text(row, "fabric")
    work = _text(row, "work")
    blouse_fabric = _text(row, "blouse_fabric")

    description_parts = []
    if brand or product_type:
        description_parts.append(" ".join(v for v in [brand, product_type] if v))
    if fabric:
        description_parts.append(f"Fabric: {fabric}.")
    if work:
        description_parts.append(f"Design/work: {work}.")
    if blouse_fabric:
        description_parts.append(f"Blouse fabric: {blouse_fabric}.")

    result = {
        "product_group": _text(row, "product_group"),
        "product_name": _text(row, "product_name"),
        "varient_name": _text(row, "varient_name"),
        "description": _text(row, "description") or " ".join(description_parts),
        "selling_proce": _text(row, "selling_proce"),
        "image-sku": _text(row, "image-sku"),
        "image_url": _text(row, "image_url"),
        "publish": False,
        "review_status": "PENDING",
        "refinement_notes": (
            "Review customer-facing name, description, category, variant, "
            "image association and selling price before publication."
        ),
    }
    return pd.Series(result)

def refine_dataframe(df):
    refined = df.copy()
    review_fields = df.apply(refine_product, axis=1)
    for column in review_fields.columns:
        refined[column] = review_fields[column]
    return refined

if __name__ == "__main__":
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input workbook not found: {INPUT_FILE}")
    df = pd.read_excel(INPUT_FILE, dtype=str).fillna("")
    refined = refine_dataframe(df)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Mapped_Source", index=False)
        refined.to_excel(writer, sheet_name="Refinement_Review", index=False)
    print("Generic product refinement workbook generated.")
    print(f"Products: {len(refined)}")
    print(f"Output: {OUTPUT_FILE}")
