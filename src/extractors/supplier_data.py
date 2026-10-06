import re
from pathlib import Path
from zipfile import is_zipfile

import pandas as pd


FIELD_ALIASES = {
    "supplier_sku": {
        "sku", "item_code", "product_code", "style_code", "style_no",
        "article_no", "design_code", "item_sku",
    },
    "supplier_name": {"supplier", "vendor", "vendor_name"},
    "catalogue_name": {
        "catalog_name", "catalogue", "catalog", "collection",
        "collection_name",
    },
    "brand_name": {"brand", "maker", "manufacturer"},
    "product_name": {"product_title", "title", "item_name", "name"},
    "product_type": {"type", "item_type"},
    "product_group": {"group", "product_category"},
    "category_slug": {"category", "category_name"},
    "fabric": {"saree_fabric", "material", "textile"},
    "blouse_fabric": {"blouse_material"},
    "supplier_price": {"cost", "cost_price", "wholesale_price", "unit_cost"},
    "selling_proce": {"selling_price", "retail_price", "mrp"},
    "gst_percent": {"gst", "tax_percent", "tax_rate"},
    "moq": {"minimum_order_quantity", "min_order_quantity"},
}


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def normalize_source_columns(
    df: pd.DataFrame, aliases: dict | None = None
) -> pd.DataFrame:
    """Normalize common supplier headers while retaining unknown columns."""

    alias_map = {name: set(values) for name, values in FIELD_ALIASES.items()}
    for canonical, names in (aliases or {}).items():
        alias_map.setdefault(canonical, set()).update(names)

    lookup = {
        alias: canonical
        for canonical, names in alias_map.items()
        for alias in {canonical, *(_normalize_name(name) for name in names)}
    }
    normalized = [_normalize_name(column) for column in df.columns]
    renamed = [lookup.get(column, column) for column in normalized]
    if len(set(renamed)) != len(renamed):
        duplicates = sorted(
            name for name in set(renamed) if renamed.count(name) > 1
        )
        raise ValueError(f"Multiple source columns map to the same field: {duplicates}")

    result = df.copy()
    result.columns = renamed
    for column in result.columns:
        if re.search(
            r"secret|token|password|passwd|credential|api.?key|"
            r"access.?key|authorization",
            column,
        ):
            result[column] = "[REDACTED]"
    return result


def extract_supplier_data(
    file_path: Path, aliases: dict | None = None
) -> pd.DataFrame:
    """Read a supplier CSV/XLSX without modifying its source."""

    file_path = Path(file_path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Supplier data file not found: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"} or (suffix == "" and is_zipfile(file_path)):
        workbook = pd.ExcelFile(file_path)
        product_sheet = next(
            (name for name in workbook.sheet_names if name.casefold() == "products"),
            workbook.sheet_names[0],
        )
        df = pd.read_excel(file_path, sheet_name=product_sheet, dtype=str)
    elif suffix in {".csv", ".tsv", ".txt"}:
        df = pd.read_csv(
            file_path,
            sep=None,
            engine="python",
            dtype=str,
            keep_default_na=False,
        )
        if [_normalize_name(column) for column in df.columns] == [
            "column", "value"
        ]:
            attributes = df.copy()
            attributes.columns = ["column", "value"]
            attributes["column"] = attributes["column"].map(_normalize_name)
            if attributes["column"].duplicated().any():
                raise ValueError("Supplier data contains duplicate attribute names.")
            df = pd.DataFrame([
                dict(zip(attributes["column"], attributes["value"]))
            ])
    else:
        raise ValueError(
            f"Unsupported supplier data format: {suffix or 'unknown'}"
        )

    df = normalize_source_columns(df, aliases=aliases)
    if "supplier_sku" not in df.columns:
        raise ValueError("Missing required column: supplier_sku")
    df["supplier_sku"] = df["supplier_sku"].fillna("").astype(str).str.strip()
    if df.empty:
        raise ValueError("Supplier source contains no product rows.")
    if df["supplier_sku"].fillna("").astype(str).str.strip().eq("").any():
        raise ValueError("Each supplier product row must have a supplier_sku.")
    return df


def main():
    import argparse

    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Extract supplier CSV/XLSX data.")
    parser.add_argument(
        "--input",
        type=Path,
        default=project_root / "input",
        help="Supplier CSV/XLSX file (default: input/)",
    )
    args = parser.parse_args()
    input_file = args.input
    if input_file.is_dir():
        candidates = sorted(
            path for path in input_file.iterdir()
            if path.suffix.lower() in {".csv", ".tsv", ".txt", ".xlsx", ".xlsm"}
        )
        if len(candidates) != 1:
            raise ValueError(
                "Provide --input as a file when the input directory does not "
                "contain exactly one supplier data file."
            )
        input_file = candidates[0]

    output_dir = project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    df = extract_supplier_data(input_file)
    output_file = output_dir / "extracted_supplier_data.xlsx"
    df.to_excel(output_file, index=False)
    print(f"Products extracted: {len(df)}")
    print(f"Output saved: {output_file}")


if __name__ == "__main__":
    main()
