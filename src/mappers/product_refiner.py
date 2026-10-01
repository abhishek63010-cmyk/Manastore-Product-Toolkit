
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    BASE_DIR / "output" / "manastore_mapped_product.xlsx"
)

OUTPUT_FILE = (
    BASE_DIR / "output" / "product_refinement_review.xlsx"
)

def refine_product(row):
    """
    Prepare a customer-facing product record.
    Preserve supplier facts and keep approval pending.
    """

    brand = str(row.get("brand_name", "")).strip()
    fabric = str(row.get("fabric", "")).strip()
    work = str(row.get("work", "")).strip()
    blouse_fabric = str(row.get("blouse_fabric", "")).strip()
    saree_length = str(row.get("saree_length", "")).strip()
    blouse_length = str(row.get("blouse_length", "")).strip()

    product_name = "Rajtex Ajrakh Printed Modal Satin Saree"

    description_parts = [
        "Discover this traditional Ajrakh printed saree from Rajtex.",
        f"Fabric: {fabric}.",
        f"Design: {work}.",
        f"Blouse fabric: {blouse_fabric}."
    ]

    if saree_length:
        description_parts.append(
            f"Saree length: {saree_length}."
        )

    if blouse_length:
        description_parts.append(
            f"Blouse length: {blouse_length}."
        )

    description = " ".join(
        part for part in description_parts if part.strip()
    )

    return pd.Series({
        "product_group": "Rajtex-A",
        "product_name": product_name,
        "varient_name": "",
        "description": description,
        "selling_proce": 899,
        "image-sku": "",
        "image_url": "",
        "publish": False,
        "review_status": "PENDING",
        "refinement_notes": (
            "Image mapping requires manual review. "
            "Seven source images are present; six design IDs "
            "are currently specified."
        )
    })

def create_image_manifest():
    """
    Create image identifiers without assuming which
    source file belongs to each design.
    """

    image_ids = [
        "Rajtex-A-035-A",
        "Rajtex-A-035-B",
        "Rajtex-A-035-C",
        "Rajtex-A-035-D",
        "Rajtex-A-035-E",
        "Rajtex-A-035-F"
    ]

    return pd.DataFrame({
        "product_group": ["Rajtex-A"] * 6,
        "supplier_sku": ["AM-1726I"] * 6,
        "image_id": image_ids,
        "source_image": [""] * 6,
        "image_url": [""] * 6,
        "image_status": ["PENDING_MANUAL_REVIEW"] * 6,
        "approval_status": ["PENDING"] * 6
    })


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Input workbook not found: {INPUT_FILE}"
        )

    df = pd.read_excel(INPUT_FILE)

    refined = df.copy()

    review_fields = df.apply(refine_product, axis=1)

    # Replace only fields intended for refinement.
    for column in review_fields.columns:
        refined[column] = review_fields[column]

    image_manifest = create_image_manifest()

    # Keep supplier and mapped source data in a separate sheet.
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        df.to_excel(
            writer,
            sheet_name="Mapped_Source",
            index=False
        )

        refined.to_excel(
            writer,
            sheet_name="Refinement_Review",
            index=False
        )

        image_manifest.to_excel(
            writer,
            sheet_name="Image_Manifest",
            index=False
        )

    print("Product refinement workbook generated.")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Products: {len(refined)}")


if __name__ == "__main__":
    main()
