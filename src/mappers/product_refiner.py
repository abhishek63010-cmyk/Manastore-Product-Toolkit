from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
INPUT_FILE = BASE_DIR / "output" / "manastore_mapped_product.xlsx"
OUTPUT_FILE = BASE_DIR / "output" / "product_refinement_review.xlsx"

FIELDS_FOR_REVIEW = [
    "product_name",
    "product_group",
    "category_slug",
    "description",
    "selling_proce",
    "image_url",
]


def _is_blank(value):
    return pd.isna(value) or str(value).strip() == ""


def refine_product(row):
    """Keep supplier facts intact and flag missing decisions for human review."""

    pending = [
        field for field in FIELDS_FOR_REVIEW
        if _is_blank(row.get(field, ""))
    ]
    return pd.Series({
        "publish": False,
        "review_status": "PENDING_REVIEW",
        "refinement_notes": (
            "Manual review required for: " + ", ".join(pending)
            if pending else "Ready for review."
        ),
    })


def create_image_manifest(inventory=None, products=None):
    """Return a review manifest from discovered images, without SKU guessing."""

    if inventory is None or inventory.empty:
        return pd.DataFrame(columns=[
            "supplier_sku", "image_id", "image_index", "source_image",
            "image_url", "image_status", "approval_status",
        ])

    manifest = inventory.copy()
    if "supplier_sku" not in manifest:
        manifest["supplier_sku"] = ""
    if "image_id" not in manifest:
        manifest["image_id"] = manifest.get("filename", "")
    if "image_index" not in manifest:
        manifest["image_index"] = ""
    if "source_image" not in manifest:
        manifest["source_image"] = manifest.get("source_file", "")
    manifest["image_url"] = ""
    manifest["approval_status"] = "PENDING"
    manifest["image_status"] = manifest.get("status", "PENDING_REVIEW")

    if products is not None and "supplier_sku" in products:
        known_skus = set(products["supplier_sku"].dropna().astype(str))
        manifest.loc[
            ~manifest["supplier_sku"].astype(str).isin(known_skus),
            "image_status",
        ] = "UNMATCHED"
    return manifest


def refine_products(df: pd.DataFrame, inventory=None) -> pd.DataFrame:
    refined = df.copy()
    review_fields = df.apply(refine_product, axis=1)
    for column in review_fields.columns:
        refined[column] = review_fields[column]
    refined["publish"] = False
    return refined, create_image_manifest(inventory, products=df)


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input workbook not found: {INPUT_FILE}")
    df = pd.read_excel(INPUT_FILE, dtype={"supplier_sku": str})
    refined, image_manifest = refine_products(df)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Mapped_Source", index=False)
        refined.to_excel(writer, sheet_name="Refinement_Review", index=False)
        image_manifest.to_excel(writer, sheet_name="Image_Manifest", index=False)
    print(f"Output: {OUTPUT_FILE}")
    print(f"Products: {len(refined)}")


if __name__ == "__main__":
    main()
