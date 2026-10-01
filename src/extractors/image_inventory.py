
from pathlib import Path
from zipfile import ZipFile
import pandas as pd

SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}


def inspect_zip(zip_path: Path) -> pd.DataFrame:
    """Inspect image files inside a supplier ZIP without modifying it."""

    records = []

    with ZipFile(zip_path, "r") as archive:
        for item in archive.infolist():

            if item.is_dir():
                continue

            file_path = Path(item.filename)

            if file_path.suffix.lower() not in SUPPORTED_FORMATS:
                continue

            records.append({
                "source_file": item.filename,
                "filename": file_path.name,
                "extension": file_path.suffix.lower(),
                "size_bytes": item.file_size,
                "source_zip": zip_path.name,
                "status": "PENDING_REVIEW",
            })

    return pd.DataFrame(records)


def create_inventory(input_dir: Path, output_dir: Path) -> None:
    """Generate an inventory report for all ZIP files in the input folder."""

    zip_files = list(input_dir.rglob("*.zip"))

    if not zip_files:
        print("No ZIP files found in the input directory.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    for zip_path in zip_files:
        inventory = inspect_zip(zip_path)

        report_name = f"{zip_path.stem}_image_inventory.xlsx"
        report_path = output_dir / report_name

        inventory.to_excel(report_path, index=False)

        print(f"\nSource: {zip_path.name}")
        print(f"Images found: {len(inventory)}")
        print(f"Report saved: {report_path}")


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[2]

    create_inventory(
        input_dir=project_root / "input",
        output_dir=project_root / "reports",
    )
