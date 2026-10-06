from pathlib import Path
from pathlib import PurePosixPath
from shutil import copyfileobj
from zipfile import ZipFile

import pandas as pd


SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}
INVENTORY_COLUMNS = [
    "source_file", "filename", "extension", "size_bytes", "source",
    "supplier_sku", "image_id", "image_index", "status", "extracted_path",
]


def _match_sku(filename: str, supplier_skus) -> str:
    stem = Path(filename).stem.casefold()
    matches = []
    for sku in supplier_skus or []:
        sku_text = str(sku).strip()
        key = sku_text.casefold()
        if key and (
            stem == key
            or stem.startswith(key + "_")
            or stem.startswith(key + "-")
            or stem.startswith(key + ".")
        ):
            matches.append(sku_text)
    return max(matches, key=len) if matches else ""


def _record(source_file, filename, size, source, supplier_skus):
    extension = Path(filename).suffix.lower()
    supplier_sku = _match_sku(filename, supplier_skus)
    return {
        "source_file": source_file,
        "filename": Path(filename).name,
        "extension": extension,
        "size_bytes": size,
        "source": source,
        "supplier_sku": supplier_sku,
        "image_id": Path(filename).stem,
        "image_index": "",
        "status": (
            "PENDING_REVIEW" if extension in SUPPORTED_FORMATS
            else "UNSUPPORTED_FORMAT"
        ) if supplier_sku else (
            "UNMATCHED" if extension in SUPPORTED_FORMATS
            else "UNSUPPORTED_FORMAT"
        ),
    }


def inspect_zip(zip_path: Path, supplier_skus=None) -> pd.DataFrame:
    """Inventory supported and unsupported files in a supplier ZIP."""

    records = []
    with ZipFile(zip_path, "r") as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            records.append(_record(
                item.filename, item.filename, item.file_size,
                Path(zip_path).name, supplier_skus,
            ))
    return pd.DataFrame(records, columns=INVENTORY_COLUMNS)


def extract_zip_images(
    zip_path: Path, output_dir: Path, supplier_skus=None
) -> pd.DataFrame:
    """Copy supported ZIP images to output without modifying the archive."""

    zip_path = Path(zip_path)
    output_dir = Path(output_dir)
    records = []
    with ZipFile(zip_path, "r") as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            raw_name = item.filename.replace("\\", "/")
            relative = PurePosixPath(raw_name)
            record = _record(
                item.filename, raw_name, item.file_size,
                zip_path.name, supplier_skus,
            )
            record["extracted_path"] = ""
            if relative.is_absolute() or ".." in relative.parts or (
                relative.parts and ":" in relative.parts[0]
            ):
                record["status"] = "INVALID_PATH"
            elif record["extension"] in SUPPORTED_FORMATS:
                extraction_root = output_dir / zip_path.name
                destination = extraction_root / Path(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    destination.resolve().relative_to(extraction_root.resolve())
                except ValueError:
                    record["status"] = "INVALID_PATH"
                    records.append(record)
                    continue
                with archive.open(item, "r") as source, destination.open("wb") as target:
                    copyfileobj(source, target)
                record["extracted_path"] = str(destination)
            records.append(record)
    return pd.DataFrame(records, columns=INVENTORY_COLUMNS)


def inspect_folder(folder_path: Path, supplier_skus=None) -> pd.DataFrame:
    """Inventory files in an image folder without changing the source."""

    folder_path = Path(folder_path)
    records = []
    for path in sorted(folder_path.rglob("*")):
        if path.is_file():
            record = _record(
                path.relative_to(folder_path).as_posix(),
                path.name,
                path.stat().st_size,
                str(folder_path),
                supplier_skus,
            )
            record["extracted_path"] = str(path)
            records.append(record)
    return pd.DataFrame(records, columns=INVENTORY_COLUMNS)


def inspect_images(source_path: Path, supplier_skus=None) -> pd.DataFrame:
    source_path = Path(source_path)
    if source_path.is_dir():
        return inspect_folder(source_path, supplier_skus)
    if source_path.suffix.lower() == ".zip":
        return inspect_zip(source_path, supplier_skus)
    raise ValueError("Image source must be a folder or ZIP archive.")


def create_inventory(
    input_dir: Path, output_dir: Path, supplier_skus=None
) -> None:
    """Generate inventories and safely extract supported ZIP image files."""

    input_dir = Path(input_dir)
    sources = sorted(input_dir.rglob("*.zip"))
    sources.extend(
        path for path in sorted(input_dir.iterdir())
        if path.is_dir() and path.name.lower() not in {"__pycache__"}
    )
    if not sources:
        print("No image ZIPs or folders found in the input directory.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        if source.suffix.lower() == ".zip":
            inventory = extract_zip_images(
                source, output_dir / "raw_images", supplier_skus
            )
        else:
            inventory = inspect_folder(source, supplier_skus)
        report_path = output_dir / f"{source.stem}_image_inventory.xlsx"
        inventory.to_excel(report_path, index=False)
        print(f"Images inventoried: {len(inventory)}")
        print(f"Report saved: {report_path}")


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[2]
    create_inventory(project_root / "input", project_root / "reports")
