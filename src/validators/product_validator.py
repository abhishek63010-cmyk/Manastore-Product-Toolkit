from pathlib import Path
import pandas as pd

REQUIRED_FIELDS = ["supplier_sku"]
FIELDS_FOR_REVIEW = [
    "product_name",
    "description",
    "selling_proce",
    "image-sku",
    "image_url",
]

def is_blank(value):
    return pd.isna(value) or str(value).strip() == ""

def validate_products(df: pd.DataFrame):
    findings = []
    seen_skus = set()

    for index, row in df.iterrows():
        excel_row = index + 2
        sku = str(row.get("supplier_sku", "")).strip()

        for field in REQUIRED_FIELDS:
            if field not in df.columns or is_blank(row.get(field)):
                findings.append({
                    "excel_row": excel_row,
                    "supplier_sku": sku,
                    "field": field,
                    "severity": "ERROR",
                    "issue": f"Missing required identity field: {field}",
                })

        if sku:
            key = sku.casefold()
            if key in seen_skus:
                findings.append({
                    "excel_row": excel_row,
                    "supplier_sku": sku,
                    "field": "supplier_sku",
                    "severity": "ERROR",
                    "issue": "Duplicate supplier SKU in batch",
                })
            else:
                seen_skus.add(key)

        for field in ["supplier_price", "gst_percent"]:
            if field not in df.columns or is_blank(row.get(field)):
                continue
            try:
                number = float(row.get(field))
                if number < 0 or (field == "gst_percent" and number > 100):
                    raise ValueError
            except (ValueError, TypeError):
                findings.append({
                    "excel_row": excel_row,
                    "supplier_sku": sku,
                    "field": field,
                    "severity": "ERROR",
                    "issue": f"Invalid numeric value: {row.get(field)}",
                })

        for field in FIELDS_FOR_REVIEW:
            if field not in df.columns or is_blank(row.get(field)):
                findings.append({
                    "excel_row": excel_row,
                    "supplier_sku": sku,
                    "field": field,
                    "severity": "WARNING",
                    "issue": f"Pending later workflow step: {field}",
                })

        publish = row.get("publish", False)
        if not is_blank(publish) and str(publish).strip().lower() not in {"false", "0", "no"}:
            findings.append({
                "excel_row": excel_row,
                "supplier_sku": sku,
                "field": "publish",
                "severity": "ERROR",
                "issue": "Product must remain unpublished during processing",
            })

    return pd.DataFrame(findings, columns=[
        "excel_row", "supplier_sku", "field", "severity", "issue"
    ])

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[2]
    input_file = project_root / "output" / "manastore_mapped_product.xlsx"
    report_dir = project_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(input_file, dtype=str).fillna("")
    findings = validate_products(df)

    report_file = report_dir / "product_validation_report.xlsx"
    summary = pd.DataFrame([
        {"metric": "Products checked", "count": len(df)},
        {"metric": "Errors", "count": int((findings["severity"] == "ERROR").sum()) if not findings.empty else 0},
        {"metric": "Warnings", "count": int((findings["severity"] == "WARNING").sum()) if not findings.empty else 0},
    ])

    with pd.ExcelWriter(report_file, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        findings.to_excel(writer, sheet_name="Findings", index=False)

    print("Validation completed.")
    print(f"Products checked: {len(df)}")
    print(f"Errors: {int((findings['severity'] == 'ERROR').sum()) if not findings.empty else 0}")
    print(f"Warnings: {int((findings['severity'] == 'WARNING').sum()) if not findings.empty else 0}")
