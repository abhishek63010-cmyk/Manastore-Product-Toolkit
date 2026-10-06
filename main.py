import argparse
from pathlib import Path

import pandas as pd

from src.extractors.supplier_data import extract_supplier_data
from src.extractors.image_inventory import extract_zip_images, inspect_folder
from src.mappers.mana_store_mapper import map_to_manastore
from src.mappers.product_refiner import refine_products
from src.validators.product_validator import validate_products


def run_pipeline(input_path: Path, output_dir: Path, report_dir: Path):
    """Extract, map, validate, and prepare a supplier import for review."""

    input_path = Path(input_path).resolve()
    generated_paths = {
        (Path(output_dir) / name).resolve()
        for name in (
            "extracted_supplier_data.xlsx",
            "manastore_mapped_product.xlsx",
            "product_refinement_review.xlsx",
        )
    }
    generated_paths.add(
        (Path(report_dir) / "product_validation_report.xlsx").resolve()
    )
    if input_path in generated_paths:
        raise ValueError("Input supplier source cannot be an output file.")

    raw = extract_supplier_data(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    raw.to_excel(output_dir / "extracted_supplier_data.xlsx", index=False)
    mapped = map_to_manastore(raw)
    mapped.to_excel(output_dir / "manastore_mapped_product.xlsx", index=False)

    sku_values = raw["supplier_sku"].dropna().astype(str).tolist()
    image_reports = []
    for source in sorted(input_path.parent.iterdir()):
        if source.resolve() == input_path:
            continue
        if source.resolve() in {
            Path(output_dir).resolve(), Path(report_dir).resolve()
        }:
            continue
        if source.suffix.lower() == ".zip":
            image_reports.append(extract_zip_images(
                source, output_dir / "raw_images", sku_values
            ))
        elif source.is_dir():
            image_reports.append(inspect_folder(source, sku_values))

    if image_reports:
        image_inventory = pd.concat(image_reports, ignore_index=True)
        image_inventory.to_excel(
            report_dir / "image_inventory.xlsx", index=False
        )
    else:
        image_inventory = None

    findings = validate_products(mapped)
    with pd.ExcelWriter(
        report_dir / "product_validation_report.xlsx", engine="openpyxl"
    ) as writer:
        pd.DataFrame([
            {"metric": "Products checked", "count": len(mapped)},
            {
                "metric": "Errors",
                "count": int((findings["severity"] == "ERROR").sum()),
            },
            {
                "metric": "Warnings",
                "count": int((findings["severity"] == "WARNING").sum()),
            },
        ]).to_excel(writer, sheet_name="Summary", index=False)
        findings.to_excel(writer, sheet_name="Findings", index=False)

    refined, image_manifest = refine_products(mapped, image_inventory)
    with pd.ExcelWriter(
        output_dir / "product_refinement_review.xlsx", engine="openpyxl"
    ) as writer:
        mapped.to_excel(writer, sheet_name="Mapped_Source", index=False)
        refined.to_excel(writer, sheet_name="Refinement_Review", index=False)
        image_manifest.to_excel(writer, sheet_name="Image_Manifest", index=False)
    refined.to_csv(output_dir / "product_refinement_review.csv", index=False)
    return raw, mapped, refined, findings, image_manifest


def main():
    project_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Prepare generic supplier product data for MANA review."
    )
    parser.add_argument(
        "--input", type=Path, required=True,
        help="Supplier CSV/XLSX source file.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=project_root / "output"
    )
    parser.add_argument(
        "--report-dir", type=Path, default=project_root / "reports"
    )
    args = parser.parse_args()
    raw, _, _, findings, _ = run_pipeline(
        args.input, args.output_dir, args.report_dir
    )
    errors = int((findings["severity"] == "ERROR").sum())
    warnings = int((findings["severity"] == "WARNING").sum())
    print(f"Products processed: {len(raw)}")
    print(f"Validation errors: {errors}; review warnings: {warnings}")
    print("Refined export is ready for manual review; no products were published.")


if __name__ == "__main__":
    main()
