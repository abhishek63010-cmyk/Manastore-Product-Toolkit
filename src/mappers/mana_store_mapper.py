import json
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
SCHEMA_FILE = BASE_DIR / "config" / "mana_store_schema.json"

with SCHEMA_FILE.open(encoding="utf-8") as handle:
    SCHEMA = json.load(handle)

MANASTORE_COLUMNS = SCHEMA["columns"]
DEFAULT_VALUES = SCHEMA["default_values"]

def map_to_manastore(df: pd.DataFrame) -> pd.DataFrame:
    """Map normalized supplier fields into the canonical MANA Store schema.

    Unknown supplier columns are preserved nowhere in the final production
    sheet; source data should remain available in the upstream raw workbook.
    Missing optional MANA fields are left blank rather than guessed.
    """
    mapped = pd.DataFrame(index=df.index)

    for target in MANASTORE_COLUMNS:
        if target in df.columns:
            mapped[target] = df[target]
        else:
            mapped[target] = DEFAULT_VALUES.get(target, "")

    if "supplier_sku" not in df.columns:
        raise ValueError("Supplier input must contain a supplier_sku identity field.")
    if df["supplier_sku"].astype(str).str.strip().eq("").any():
        raise ValueError("supplier_sku cannot be blank.")

    mapped["publish"] = False
    return mapped[MANASTORE_COLUMNS]

if __name__ == "__main__":
    input_file = BASE_DIR / "output" / "extracted_supplier_data.xlsx"
    output_file = BASE_DIR / "output" / "manastore_mapped_product.xlsx"

    df = pd.read_excel(input_file, dtype=str).fillna("")
    mapped = map_to_manastore(df)
    mapped.to_excel(output_file, index=False)

    print("MANA Store schema mapping successful.")
    print(f"Products mapped: {len(mapped)}")
    print(f"Output saved: {output_file}")
