from pathlib import Path
import re
import pandas as pd

SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv"}

def _normalize_column(name: object) -> str:
    value = str(name).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    aliases = {
        "sku": "supplier_sku",
        "supplier_sku_code": "supplier_sku",
        "supplier_code": "supplier_sku",
        "cost": "supplier_price",
        "cost_price": "supplier_price",
        "wholesale_price": "supplier_price",
        "price": "supplier_price",
        "gst": "gst_percent",
        "gst_rate": "gst_percent",
        "supplier": "supplier_name",
        "catalogue": "catalog_name",
        "catalog": "catalog_name",
        "category": "category_slug",
        "saree_fabric": "fabric",
        "variant": "varient_name",
        "variant_name": "varient_name"
    }
    return aliases.get(value, value)

def _read_table(file_path: Path) -> pd.DataFrame:
    suffix = file_path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        workbook = pd.ExcelFile(file_path)
        sheet = next(
            (s for s in workbook.sheet_names if s.strip().lower() in {"products", "product", "catalogue", "catalog"}),
            workbook.sheet_names[0],
        )
        return pd.read_excel(file_path, sheet_name=sheet, dtype=str)
    if suffix == ".csv":
        return pd.read_csv(file_path, dtype=str)
    if suffix == ".tsv":
        return pd.read_csv(file_path, sep="\t", dtype=str)
    raise ValueError(f"Unsupported supplier input format: {suffix}")

def extract_supplier_data(file_path: Path) -> pd.DataFrame:
    """Read a supplier table without imposing a supplier-specific schema."""
    if not file_path.exists():
        raise FileNotFoundError(f"Supplier data file not found: {file_path}")
    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported supplier input format: {file_path.suffix}. "
            f"Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    df = _read_table(file_path)
    if df.empty:
        raise ValueError("Supplier input contains no product rows.")

    df.columns = [_normalize_column(c) for c in df.columns]
    if len(set(df.columns)) != len(df.columns):
        raise ValueError("Supplier input contains duplicate columns after normalization.")

    # Preserve source values as strings; downstream mapping decides what is numeric.
    return df.fillna("")
