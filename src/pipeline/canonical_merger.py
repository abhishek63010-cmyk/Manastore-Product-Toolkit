"""
Standalone Canonical Dataset Merger for MANA Store Product Toolkit.

Merges multiple canonical product exports (JSON or XLSX), validates compatibility,
detects identity and SKU conflicts, and produces a single unified CanonicalExport.
Completely isolated and independent of the batch runner and Excel consolidation scripts.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Union

from src.exporters.canonical import CanonicalExport, CanonicalProduct, CanonicalVariant
from src.exporters.writers import OutputWriter


class CanonicalMergeConflictError(ValueError):
    """Raised when conflicting identities or incompatible datasets are merged."""
    pass


class CanonicalDatasetMerger:
    """
    Robust standalone merger for canonical product datasets.
    """

    @classmethod
    def load_canonical_dataset(cls, file_path: Union[str, Path]) -> CanonicalExport:
        """Load CanonicalExport from a JSON file or directory containing canonical_products.json."""
        p = Path(file_path)
        if p.is_dir():
            for cand in [
                p / "_internal" / "canonical_products.json",
                p / "canonical_products.json",
            ]:
                if cand.exists():
                    p = cand
                    break

        if not p.exists():
            raise FileNotFoundError(f"Canonical dataset file not found: {file_path}")

        if p.suffix.lower() == ".json":
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return CanonicalExport(**data)
        elif p.suffix.lower() in (".xlsx", ".xls"):
            # Load via openpyxl and rebuild products/variants
            import openpyxl
            wb = openpyxl.load_workbook(p, data_only=True)
            sheet = wb.active
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                return CanonicalExport(total_products=0, total_variants=0, products=[])
            headers = [str(h) for h in rows[0]]
            prods_by_id: dict[str, CanonicalProduct] = {}
            for r in rows[1:]:
                row_dict = dict(zip(headers, r))
                pid = str(row_dict.get("product_id") or row_dict.get("catalog_name"))
                if pid not in prods_by_id:
                    prods_by_id[pid] = CanonicalProduct(
                        product_id=pid,
                        supplier_name=str(row_dict.get("supplier_name") or ""),
                        catalog_name=str(row_dict.get("catalog_name") or ""),
                        product_name=str(row_dict.get("product_name") or ""),
                        product_group=row_dict.get("product_group"),
                        brand_name=row_dict.get("brand_name"),
                        product_type=str(row_dict.get("product_type") or "Saree"),
                        category_slug=row_dict.get("category_slug"),
                        description=row_dict.get("description"),
                        supplier_price=float(row_dict["supplier_price"]) if row_dict.get("supplier_price") is not None else None,
                        gst_percent=float(row_dict["gst_percent"]) if row_dict.get("gst_percent") is not None else None,
                    )
                var = CanonicalVariant(
                    variant_code=str(row_dict.get("variant_code") or f"VAR-{len(prods_by_id[pid].variants) + 1}"),
                    supplier_sku=row_dict.get("supplier_sku"),
                    variant_name=str(row_dict.get("variant_name") or "Standard"),
                    image_sku=row_dict.get("image_sku"),
                    color=row_dict.get("color"),
                    size=row_dict.get("size"),
                    supplier_cost=float(row_dict["supplier_cost"]) if row_dict.get("supplier_cost") is not None else None,
                    selling_price=float(row_dict["selling_price"]) if row_dict.get("selling_price") is not None else None,
                    primary_image_url=row_dict.get("primary_image_url"),
                )
                prods_by_id[pid].variants.append(var)

            prod_list = list(prods_by_id.values())
            total_vars = sum(len(p.variants) for p in prod_list)
            return CanonicalExport(
                total_products=len(prod_list),
                total_variants=total_vars,
                products=prod_list,
            )
        else:
            raise ValueError(f"Unsupported canonical dataset format: {p.suffix}")

    @classmethod
    def merge_datasets(
        cls,
        datasets: list[CanonicalExport],
        allow_duplicate_skus: bool = False,
    ) -> CanonicalExport:
        """
        Merge multiple CanonicalExport objects into a unified CanonicalExport.
        Validates schema compatibility and checks for duplicate/conflicting product IDs and variant codes.
        """
        if not datasets:
            return CanonicalExport(total_products=0, total_variants=0, products=[])

        seen_product_ids: dict[str, str] = {}  # product_id -> catalog_name
        seen_variant_codes: dict[str, str] = {} # variant_code -> product_id
        merged_products: list[CanonicalProduct] = []

        for ds in datasets:
            for prod in ds.products:
                if prod.product_id in seen_product_ids:
                    prev_cat = seen_product_ids[prod.product_id]
                    if prev_cat != prod.catalog_name:
                        raise CanonicalMergeConflictError(
                            f"Product ID conflict: ID '{prod.product_id}' used for both "
                            f"'{prev_cat}' and '{prod.catalog_name}'"
                        )
                seen_product_ids[prod.product_id] = prod.catalog_name

                for var in prod.variants:
                    if var.variant_code in seen_variant_codes:
                        prev_pid = seen_variant_codes[var.variant_code]
                        if prev_pid != prod.product_id:
                            raise CanonicalMergeConflictError(
                                f"Variant code collision: code '{var.variant_code}' assigned to "
                                f"different products ('{prev_pid}' vs '{prod.product_id}')"
                            )
                    seen_variant_codes[var.variant_code] = prod.product_id

                merged_products.append(prod)

        total_vars = sum(len(p.variants) for p in merged_products)
        return CanonicalExport(
            export_version="1.0",
            total_products=len(merged_products),
            total_variants=total_vars,
            products=merged_products,
        )

    @classmethod
    def merge_from_files(
        cls,
        file_paths: list[Union[str, Path]],
        allow_duplicate_skus: bool = False,
    ) -> CanonicalExport:
        """Load and merge canonical datasets from a list of files/directories."""
        datasets = [cls.load_canonical_dataset(p) for p in file_paths]
        return cls.merge_datasets(datasets, allow_duplicate_skus=allow_duplicate_skus)

    @classmethod
    def export_merged(
        cls,
        merged: CanonicalExport,
        output_path: Union[str, Path],
        format: str = "json",
    ) -> Path:
        """Write merged CanonicalExport to disk as JSON or XLSX."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fmt = format.lower().strip()
        if fmt == "json":
            return OutputWriter.write_canonical_json(merged, out)
        elif fmt == "xlsx":
            return OutputWriter.write_canonical_xlsx(merged, out)
        elif fmt == "csv":
            return OutputWriter.write_canonical_csv(merged, out)
        else:
            raise ValueError(f"Unsupported export format: {fmt}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Standalone Canonical Dataset Merger",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-i", "--inputs",
        nargs="+",
        required=True,
        help="Paths to canonical JSON or XLSX files or catalogue directories",
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        help="Destination path for merged canonical dataset",
    )
    parser.add_argument(
        "-f", "--format",
        default="json",
        choices=["json", "xlsx", "csv"],
        help="Export format for merged dataset",
    )

    args = parser.parse_args()

    try:
        merged = CanonicalDatasetMerger.merge_from_files(args.inputs)
        dest = CanonicalDatasetMerger.export_merged(merged, args.output, format=args.format)
        print(
            f"Successfully merged {len(args.inputs)} datasets: "
            f"{merged.total_products} products, {merged.total_variants} variants -> {dest}"
        )
        return 0
    except Exception as e:
        print(f"Error during canonical merge: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
