from typing import Any
from src.adapters.base import BaseSupplierAdapter
from src.extractors.raw_record import RawRecord, RawTabularData
from src.models.enums import FieldSource
from src.models.product import Product
from src.models.provenance import ProvenanceRecord
from src.models.variant import Variant


class GenericTabularAdapter(BaseSupplierAdapter):
    """
    Fallback supplier-agnostic adapter for standard tabular product datasets (e.g. CSV/XLSX).
    
    Supports:
    - Apparel, footwear, electronics, home goods, accessories, etc.
    - Preserves all unmapped columns into raw_attributes.
    - Groups rows by catalog/product identity if multiple rows share the same parent.
    - Supports multiple variants sharing one supplier_sku.
    - Never calculates or invents selling_price.
    """

    adapter_name: str = "generic_tabular"

    ALIASES = {
        "supplier_name": ["supplier_name", "supplier", "vendor", "manufacturer"],
        "catalog_name": ["catalog_name", "catalogue_name", "collection", "catalog", "catalogue", "group_name"],
        "product_name": ["product_name", "title", "product_title", "item_name", "name"],
        "product_group": ["product_group", "group", "series", "family"],
        "brand_name": ["brand", "brand_name", "brand_title"],
        "product_type": ["product_type", "type", "category", "item_type"],
        "category_slug": ["category_slug", "category", "slug"],
        "description": ["description", "details", "product_description", "summary"],
        "supplier_sku": ["supplier_sku", "sku", "item_code", "product_code", "style_code", "code", "model"],
        "supplier_price": ["supplier_price", "cost", "supplier_cost", "wholesale_price", "unit_cost", "purchase_price"],
        "gst_percent": ["gst", "gst_percent", "tax_percent", "tax_rate", "vat"],
        "moq": ["moq", "min_order_qty", "minimum_order_quantity", "pack_size"],
        "variant_code": ["variant_code", "variant_sku", "barcode", "upc", "ean", "item_sku"],
        "variant_name": ["variant_name", "variant", "option", "design", "style", "color_name"],
        "color": ["color", "colour", "shade"],
        "size": ["size", "dimensions", "length"],
        "stock_quantity": ["stock", "quantity", "inventory", "stock_quantity", "qty"],
        "selling_price": ["selling_price", "retail_price", "mrp", "price"],
    }

    def can_handle(self, raw_data: RawTabularData) -> bool:
        """Can handle any non-empty tabular dataset as a general fallback."""
        return len(raw_data.records) > 0

    def transform(self, raw_data: RawTabularData) -> list[Product]:
        """Convert raw tabular records into canonical Product and Variant objects."""
        if not raw_data.records:
            return []

        # 1. Group records by parent product identity
        grouped_records: dict[str, list[RawRecord]] = {}
        for record in raw_data.records:
            group_key = self._determine_group_key(record)
            if group_key not in grouped_records:
                grouped_records[group_key] = []
            grouped_records[group_key].append(record)

        # 2. Build Product and Variant domain models for each group
        products: list[Product] = []
        for group_key, records in grouped_records.items():
            product = self._build_product(records, raw_data.source_file)
            products.append(product)

        return products

    def _determine_group_key(self, record: RawRecord) -> str:
        """Determine grouping key for multi-variant products."""
        for field in ("catalog_name", "product_group", "product_name"):
            _, val = self.find_field_value(record, self.ALIASES[field])
            clean = self.clean_str(val)
            if clean:
                return clean

        # Fallback to supplier_sku or row number if no grouping field exists
        _, sku = self.find_field_value(record, self.ALIASES["supplier_sku"])
        clean_sku = self.clean_str(sku)
        if clean_sku:
            return f"sku_{clean_sku}"
        return f"row_{record.row_number}"

    def _build_product(self, records: list[RawRecord], source_file: str) -> Product:
        first_record = records[0]

        # Extract product-level fields
        k_supp, v_supp = self.find_field_value(first_record, self.ALIASES["supplier_name"])
        supplier_name = self.clean_str(v_supp) or "Generic Supplier"

        k_cat, v_cat = self.find_field_value(first_record, self.ALIASES["catalog_name"])
        k_prod, v_prod = self.find_field_value(first_record, self.ALIASES["product_name"])
        
        catalog_name = self.clean_str(v_cat) or self.clean_str(v_prod) or f"Catalog-{first_record.row_number}"
        product_name = self.clean_str(v_prod) or catalog_name

        k_grp, v_grp = self.find_field_value(first_record, self.ALIASES["product_group"])
        product_group = self.clean_str(v_grp)

        k_brand, v_brand = self.find_field_value(first_record, self.ALIASES["brand_name"])
        brand_name = self.clean_str(v_brand)

        k_type, v_type = self.find_field_value(first_record, self.ALIASES["product_type"])
        product_type = self.clean_str(v_type) or "General"

        k_slug, v_slug = self.find_field_value(first_record, self.ALIASES["category_slug"])
        category_slug = self.normalize_key(v_slug) if v_slug else (self.normalize_key(product_type) if product_type else None)

        k_desc, v_desc = self.find_field_value(first_record, self.ALIASES["description"])
        description = self.clean_str(v_desc)

        k_cost, v_cost = self.find_field_value(first_record, self.ALIASES["supplier_price"])
        supplier_price = self.clean_float(v_cost)

        k_gst, v_gst = self.find_field_value(first_record, self.ALIASES["gst_percent"])
        gst_percent = self.clean_float(v_gst)

        k_moq, v_moq = self.find_field_value(first_record, self.ALIASES["moq"])
        moq = self.clean_int(v_moq)

        # Build provenance
        prod_prov = ProvenanceRecord(
            source_file=source_file,
            supplier_id=self.adapter_name,
        )
        if k_supp:
            self.record_provenance(prod_prov, "supplier_name", FieldSource.SOURCE, k_supp, v_supp)
        else:
            self.record_provenance(prod_prov, "supplier_name", FieldSource.AUTO_FILLED, notes="Default generic supplier")

        if k_cat:
            self.record_provenance(prod_prov, "catalog_name", FieldSource.SOURCE, k_cat, v_cat)
        elif k_prod:
            self.record_provenance(prod_prov, "catalog_name", FieldSource.DERIVED, k_prod, v_prod, notes="Derived from product name")
        else:
            self.record_provenance(prod_prov, "catalog_name", FieldSource.AUTO_FILLED, notes="Generated fallback")

        if k_prod:
            self.record_provenance(prod_prov, "product_name", FieldSource.SOURCE, k_prod, v_prod)
        else:
            self.record_provenance(prod_prov, "product_name", FieldSource.DERIVED, notes="Copied from catalog name")

        # Collect unmapped fields into raw_attributes
        mapped_keys = {
            k for aliases in self.ALIASES.values()
            for a in aliases
            for k in [self.normalize_key(a)]
        }
        raw_attributes: dict[str, Any] = {}
        for orig_k, orig_v in first_record.data.items():
            if self.normalize_key(orig_k) not in mapped_keys and orig_v is not None:
                raw_attributes[orig_k] = orig_v

        product = Product(
            supplier_name=supplier_name,
            catalog_name=catalog_name,
            product_name=product_name,
            product_group=product_group,
            brand_name=brand_name,
            product_type=product_type,
            category_slug=category_slug,
            description=description,
            supplier_price=supplier_price,
            gst_percent=gst_percent,
            moq=moq,
            raw_attributes=raw_attributes,
            source_metadata=prod_prov,
        )

        # Build variants for this product
        for idx, rec in enumerate(records, start=1):
            variant = self._build_variant(rec, product, idx, source_file)
            product.add_variant(variant)

        return product

    def _build_variant(
        self,
        record: RawRecord,
        parent: Product,
        variant_idx: int,
        source_file: str,
    ) -> Variant:
        var_prov = ProvenanceRecord(
            source_file=source_file,
            supplier_id=self.adapter_name,
        )

        # 1. Supplier SKU
        k_sku, v_sku = self.find_field_value(record, self.ALIASES["supplier_sku"])
        supplier_sku = self.clean_str(v_sku) or f"SKU-{record.row_number}"
        if k_sku:
            self.record_provenance(var_prov, "supplier_sku", FieldSource.SOURCE, k_sku, v_sku)
        else:
            self.record_provenance(var_prov, "supplier_sku", FieldSource.AUTO_FILLED, notes="Fallback row SKU")

        # 2. Variant Code (unique internal code)
        k_vcode, v_vcode = self.find_field_value(record, self.ALIASES["variant_code"])
        clean_vcode = self.clean_str(v_vcode)
        if clean_vcode:
            variant_code = clean_vcode
            self.record_provenance(var_prov, "variant_code", FieldSource.SOURCE, k_vcode, v_vcode)
        else:
            # Deterministically derived unique variant code
            variant_code = f"{supplier_sku}-V{variant_idx:02d}"
            self.record_provenance(
                var_prov,
                "variant_code",
                FieldSource.DERIVED,
                notes=f"Derived from supplier SKU + index {variant_idx}",
            )

        # 3. Variant Name
        k_vname, v_vname = self.find_field_value(record, self.ALIASES["variant_name"])
        k_col, v_col = self.find_field_value(record, self.ALIASES["color"])
        variant_name = self.clean_str(v_vname) or self.clean_str(v_col) or f"Option {variant_idx}"
        if k_vname:
            self.record_provenance(var_prov, "variant_name", FieldSource.SOURCE, k_vname, v_vname)
        elif k_col:
            self.record_provenance(var_prov, "variant_name", FieldSource.DERIVED, k_col, v_col, notes="Derived from color")
        else:
            self.record_provenance(var_prov, "variant_name", FieldSource.AUTO_FILLED, notes="Default option name")

        # 4. Color, Size, Cost, Stock
        color = self.clean_str(v_col)
        if k_col:
            self.record_provenance(var_prov, "color", FieldSource.SOURCE, k_col, v_col)

        k_size, v_size = self.find_field_value(record, self.ALIASES["size"])
        size = self.clean_str(v_size)
        if k_size:
            self.record_provenance(var_prov, "size", FieldSource.SOURCE, k_size, v_size)

        k_cost, v_cost = self.find_field_value(record, self.ALIASES["supplier_price"])
        supplier_cost = self.clean_float(v_cost) if v_cost else parent.supplier_price
        if k_cost:
            self.record_provenance(var_prov, "supplier_cost", FieldSource.SOURCE, k_cost, v_cost)

        k_stock, v_stock = self.find_field_value(record, self.ALIASES["stock_quantity"])
        stock_quantity = self.clean_int(v_stock)
        if k_stock:
            self.record_provenance(var_prov, "stock_quantity", FieldSource.SOURCE, k_stock, v_stock)

        # 5. Selling Price: NEVER invent or auto-calculate!
        k_price, v_price = self.find_field_value(record, self.ALIASES["selling_price"])
        selling_price = self.clean_float(v_price) if v_price is not None else None
        if k_price and selling_price is not None:
            self.record_provenance(var_prov, "selling_price", FieldSource.SOURCE, k_price, v_price)
        else:
            self.record_provenance(
                var_prov,
                "selling_price",
                FieldSource.MANUAL_REVIEW,
                notes="Selling price unset; requires pricing configuration",
            )

        # 6. Preserved unmapped variant attributes
        mapped_keys = {
            k for aliases in self.ALIASES.values()
            for a in aliases
            for k in [self.normalize_key(a)]
        }
        var_raw_attributes = {
            orig_k: orig_v
            for orig_k, orig_v in record.data.items()
            if self.normalize_key(orig_k) not in mapped_keys and orig_v is not None
        }

        return Variant(
            variant_code=variant_code,
            supplier_sku=supplier_sku,
            variant_name=variant_name,
            color=color,
            size=size,
            supplier_cost=supplier_cost,
            selling_price=selling_price,
            stock_quantity=stock_quantity,
            publish=False,
            raw_attributes=var_raw_attributes,
            source_metadata=var_prov,
        )
