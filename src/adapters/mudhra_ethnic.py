from __future__ import annotations

from typing import Any

from src.adapters.generic import GenericTabularAdapter
from src.extractors.raw_record import RawTabularData


class MudhraEthnicAdapter(GenericTabularAdapter):
    """Supplier-specific adapter for Mudhra Ethnic catalogues.

    The adapter is intentionally kept separate from the generic and Amra adapters so that
    supplier-specific field aliases and folder-based routing stay isolated. It still reuses
    the generic tabular transformation pipeline for the canonical product/variant shape.
    """

    adapter_name: str = "mudhra_ethnic"

    ALIASES = {
        **GenericTabularAdapter.ALIASES,
        "supplier_name": ["supplier_name", "supplier", "vendor", "manufacturer", "brand", "label"],
        "catalog_name": ["catalog_name", "catalogue_name", "collection", "catalog", "catalogue", "collection_name", "group_name"],
        "product_name": ["product_name", "product_title", "title", "item_name", "name", "style_name", "style"],
        "product_group": ["product_group", "group", "series", "family", "collection_code"],
        "brand_name": ["brand", "brand_name", "brand_title", "label"],
        "product_type": ["product_type", "type", "category", "item_type", "garment_type", "category_name"],
        "category_slug": ["category_slug", "category", "slug", "type"],
        "description": ["description", "details", "product_description", "summary", "fabric_details"],
        "supplier_sku": ["supplier_sku", "sku", "item_code", "product_code", "style_code", "code", "model", "style_no"],
        "supplier_price": ["supplier_price", "per_unit_price", "unit_price", "cost", "supplier_cost", "unit_cost", "purchase_price", "price", "single_piece_price", "single_piece", "wholesale_price", "wholesale_per_unit_price", "mrp"],
        "gst_percent": ["gst", "gst_percent", "tax_percent", "tax_rate", "vat"],
        "moq": ["minimum_order_quantity", "minimum_order_qty", "moq", "min_order_qty", "pack_size"],
        "variant_code": ["variant_code", "variant_sku", "barcode", "upc", "ean", "item_sku", "design_code"],
        "variant_name": ["variant_name", "variant", "option", "design", "style", "color_name", "colour"],
        "variant_count": ["total_designs", "designs", "pieces", "pcs", "full_catalogue", "total_pieces"],
        "color": ["color", "colour", "shade", "tone"],
        "size": ["size", "dimensions", "length", "size_chart"],
        "selling_price": ["selling_price", "retail_price", "mrp", "price"],
    }

    def can_handle(self, raw_data: RawTabularData) -> bool:
        """Recognize Mudhra catalogue datasets from the source path or repeated Mudhra-specific headers."""
        source_name = str(raw_data.source_file or "").lower()
        if "mudhra" in source_name:
            return True

        normalized_headers = {self.normalize_key(h) for h in raw_data.headers}
        mudhra_signatures = {
            "collection",
            "fabric",
            "work",
            "blouse",
            "price",
            "product_name",
            "brand",
        }
        if len(normalized_headers.intersection(mudhra_signatures)) >= 3:
            return True

        if raw_data.records:
            first_record = raw_data.records[0]
            raw_brand = first_record.get("brand") or first_record.get("supplier") or first_record.get("vendor")
            if raw_brand and "mudhra" in str(raw_brand).lower():
                return True

        return False

    def transform(self, raw_data: RawTabularData):
        """Use the generic tabular transformation pipeline with Mudhra-specific aliases."""
        if not raw_data.records:
            return []

        grouped_records: dict[str, list] = {}
        for record in raw_data.records:
            group_key = self._determine_group_key(record)
            grouped_records.setdefault(group_key, []).append(record)

        products = []
        for records in grouped_records.values():
            product = self._build_product(records, raw_data.source_file)
            self._attach_mudhra_attributes(product, records[0])
            products.append(product)

        return products

    def _attach_mudhra_attributes(self, product, first_record):
        """Map Mudhra-specific fields into normalized product attributes."""
        attribute_map = {
            "collection": ["collection", "catalog_name", "catalogue_name"],
            "fabric": ["fabric", "material", "fabric_composition", "saree_fabric"],
            "work": ["work", "work_type", "embroidery", "print_type"],
            "finishing": ["finishing", "finish", "wash", "treatment"],
            "blouse": ["blouse", "blouse_fabric", "blouse_material"],
            "supplier_price": ["supplier_price", "price", "single_piece_price", "single_piece", "wholesale_price"],
        }

        for canonical_name, aliases in attribute_map.items():
            _, raw_value = self.find_field_value(first_record, aliases)
            clean = self.clean_str(raw_value)
            if clean is None:
                continue
            product.attributes[canonical_name] = clean

        if product.supplier_price is None:
            _, raw_price = self.find_field_value(first_record, ["supplier_price", "price", "single_piece_price", "single_piece", "wholesale_price"])
            product.supplier_price = self.clean_float(raw_price)

        if product.catalog_name and product.catalog_name.lower() == "mudhra ethnic":
            product.catalog_name = "Mudhra Ethnic"
