
from pathlib import Path
from zipfile import is_zipfile

import pandas as pd


REQUIRED_COLUMNS = [
    "supplier_name",
    "catalogue_name",
    "brand_name",
    "supplier_sku",
    "product_type",
    "supplier_price",
    "gst_percent",
    "moq",
]


def extract_supplier_data(file_path: Path) -> pd.DataFrame:
    """Read raw supplier data without modifying the source workbook."""

    if not file_path.exists():
        raise FileNotFoundError(
            f"Supplier data file not found: {file_path}"
        )

    if is_zipfile(file_path):
        df = pd.read_excel(
            file_path,
            sheet_name="Products",
            dtype={"supplier_sku": str},
        )
    else:
        attributes = pd.read_csv(file_path, sep="\t", dtype=str)
        attributes.columns = attributes.columns.str.strip().str.lower()

        if attributes.columns.tolist() != ["column", "value"]:
            raise ValueError(
                "Supplier data must be an XLSX workbook or a tab-separated "
                "Column/Value file."
            )

        attributes["column"] = (
            attributes["column"]
            .str.strip()
            .str.lower()
            .str.replace(" ", "_", regex=False)
        )
        if attributes["column"].duplicated().any():
            raise ValueError("Supplier data contains duplicate attribute names.")

        df = pd.DataFrame([dict(zip(attributes["column"], attributes["value"]))])

        for column in (
            "supplier_price",
            "catalogue_price_per_piece",
            "gst_percent",
            "moq",
            "design_count",
        ):
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="raise")

    df.columns = (
        df.columns
        .str.strip()
        .str.lower()
        .str.replace(" ", "_")
    )

    missing = [
        column
        for column in REQUIRED_COLUMNS
        if column not in df.columns
    ]

    if missing:
        raise ValueError(
            f"Missing required columns: {missing}"
        )

    if df.empty:
        raise ValueError("Supplier workbook contains no product rows.")

    return df


def main():
    project_root = Path(__file__).resolve().parents[2]

    input_file = (
        project_root
        / "input"
        / "AMRA-RAJTEX-001"
        / "raw_supplier_data.xlsx"
    )

    output_dir = project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    df = extract_supplier_data(input_file)

    output_file = output_dir / "extracted_supplier_data.xlsx"
    df.to_excel(output_file, index=False)

    print("Supplier data extraction successful.")
    print(f"Products extracted: {len(df)}")
    print(f"Output saved: {output_file}")


if __name__ == "__main__":
    main()
