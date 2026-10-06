from typing import Any
from src.adapters.base import BaseSupplierAdapter
from src.extractors.raw_record import RawRecord, RawTabularData
from src.models.enums import FieldSource
from src.models.product import Product
from src.models.provenance import ProvenanceRecord
from src.models.variant import Variant


class AmraAdapter(BaseSupplierAdapter):
    """
    Adapter for Amra Wholesale product and catalog datasets.
    
    Compatible with:
    - Amra gold-master multi-catalog/multi-variant workbooks (e.g., Amra_data_with_all_blob_urls.xlsx)
    - Amra single-catalog raw supplier files (e.g., raw_supplier_data.xlsx)
    - Tabular workbooks containing saree/ethnic wear metadata
    
    Principles:
    - Groups 1 customer-facing Product -> Multiple color/design Variants.
    - Preserves shared supplier_sku across variants without rejecting duplicates.
    - Category-specific fields (fabric, blouse_fabric, work, etc.) placed in `attributes`.
    - Preserves unmapped raw facts in `raw_attributes`.
    - Never calculates or invents selling_price.
    """

    adapter_name: str = "amra_adapter"

    PRODUCT_ALIASES = {
        "supplier_name": ["supplier_name", "supplier", "vendor"],
        "catalog_name": ["catalogue_name", "catalog_name", "catalogue", "catalog", "catalogue_title"],
        "product_group": ["product_group", "group", "group_name", "product_code"],
        "product_name": ["product_name", "product_title", "title", "name"],
        "brand_name": ["brand_name", "brand"],
        "product_type": ["product_type", "type", "category"],
        "category_slug": ["category_slug", "category"],
        "description": ["description", "product_description", "details"],
        "supplier_sku": ["supplier_sku", "sku", "item_code", "product_sku"],
        "supplier_price": ["supplier_price", "per_unit_price", "single_piece", "cost", "catalogue_price_per_piece"],
        "gst_percent": ["gst_percent", "gst", "tax_percent"],
        "moq": ["moq", "single_piece", "minimum_order_qty"],
        "dispatch_location": ["dispatch_location", "dispatch_from", "location"],
    }

    CATEGORY_ATTRIBUTES = {
        "fabric": ["fabric", "saree_fabric", "material"],
        "blouse_fabric": ["blouse_fabric", "blouse_material"],
        "work": ["work", "work_print_type", "work/print_type", "work_type", "print_type", "embroidery"],
        "occasion": ["occasion", "wear_type"],
        "size": ["size", "dimensions"],
        "saree_length": ["saree_length", "saree_cut"],
        "blouse_length": ["blouse_length", "blouse_cut"],
        "stitch_type": ["stitch_type", "stitching"],
    }

    VARIANT_ALIASES = {
        "variant_name": ["varient_name", "variant_name", "variant", "design_name", "color", "colour", "shade"],
        "color": ["color", "colour", "shade"],
        "size": ["size", "dimensions"],
        "image_sku": ["image-sku", "image_sku", "image_id", "design_id", "design_code"],
        "primary_image_url": ["image_url", "image", "main_image_url"],
        "gallery_image_urls": ["image1_url", "image2_url", "image3_url", "image4_url"],
        "selling_price": ["selling_proce", "selling_price", "mrp", "retail_price"],
        "supplier_cost": ["supplier_price", "per_unit_price", "single_piece", "cost"],
    }

    def can_handle(self, raw_data: RawTabularData) -> bool:
        """Identify Amra dataset via supplier headers or supplier name values."""
        normalized_headers = {self.normalize_key(h) for h in raw_data.headers}
        
        # Check for signature Amra column combinations
        amra_signatures = {
            "catalogue_name", "catalog_name", "saree_fabric", "blouse_fabric",
            "varient_name", "product_group", "dispatch_location"
        }
        if len(normalized_headers.intersection(amra_signatures)) >= 2:
            return True

        # Check for supplier name in first record
        if raw_data.records:
            first_rec = raw_data.records[0]
            _, val = self.find_field_value(first_rec, ["supplier_name", "supplier"])
            if val and "amra" in str(val).lower():
                return True

        return False

    def transform(self, raw_data: RawTabularData) -> list[Product]:
        """Transform raw Amra records into canonical Product + Variant hierarchy."""
        if not raw_data.records:
            return []

        # 1. Group records by catalog / product_group
        groups: dict[str, list[RawRecord]] = {}
        for record in raw_data.records:
            group_key = self._determine_amra_group_key(record)
            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append(record)

        # 2. Build canonical Product and child Variants for each group
        products: list[Product] = []
        for group_key, records in groups.items():
            product = self._build_amra_product(records, raw_data.source_file)
            products.append(product)

        return products

    def _determine_amra_group_key(self, record: RawRecord) -> str:
        """Derive grouping key using product_group, catalog_name, or supplier_sku."""
        for field in ("product_group", "catalog_name", "supplier_sku"):
            _, val = self.find_field_value(record, self.PRODUCT_ALIASES[field])
            clean = self.clean_str(val)
            if clean:
                return clean
        return f"amra_catalog_{record.row_number}"

    def _build_amra_product(self, records: list[RawRecord], source_file: str) -> Product:
        first_record = records[0]
        prod_prov = ProvenanceRecord(
            source_file=source_file,
            supplier_id=self.adapter_name,
        )

        # Core Identity
        k_supp, v_supp = self.find_field_value(first_record, self.PRODUCT_ALIASES["supplier_name"])
        supplier_name = self.clean_str(v_supp) or "Amra Wholesale"
        if k_supp:
            self.record_provenance(prod_prov, "supplier_name", FieldSource.SOURCE, k_supp, v_supp)
        else:
            self.record_provenance(prod_prov, "supplier_name", FieldSource.AUTO_FILLED, notes="Default Amra Wholesale")

        k_cat, v_cat = self.find_field_value(first_record, self.PRODUCT_ALIASES["catalog_name"])
        catalog_name = self.clean_str(v_cat) or f"Amra Catalog {first_record.row_number}"
        if k_cat:
            self.record_provenance(prod_prov, "catalog_name", FieldSource.SOURCE, k_cat, v_cat)
        else:
            self.record_provenance(prod_prov, "catalog_name", FieldSource.AUTO_FILLED, notes="Generated catalog name")

        k_grp, v_grp = self.find_field_value(first_record, self.PRODUCT_ALIASES["product_group"])
        product_group = self.clean_str(v_grp)
        if k_grp:
            self.record_provenance(prod_prov, "product_group", FieldSource.SOURCE, k_grp, v_grp)

        k_brand, v_brand = self.find_field_value(first_record, self.PRODUCT_ALIASES["brand_name"])
        brand_name = self.clean_str(v_brand)
        if k_brand:
            self.record_provenance(prod_prov, "brand_name", FieldSource.SOURCE, k_brand, v_brand)

        k_type, v_type = self.find_field_value(first_record, self.PRODUCT_ALIASES["product_type"])
        product_type = self.clean_str(v_type) or "Saree"
        if k_type:
            self.record_provenance(prod_prov, "product_type", FieldSource.SOURCE, k_type, v_type)
        else:
            self.record_provenance(prod_prov, "product_type", FieldSource.AUTO_FILLED, notes="Default Saree")

        k_slug, v_slug = self.find_field_value(first_record, self.PRODUCT_ALIASES["category_slug"])
        category_slug = self.clean_str(v_slug) or "sarees"
        if k_slug:
            self.record_provenance(prod_prov, "category_slug", FieldSource.SOURCE, k_slug, v_slug)
        else:
            self.record_provenance(prod_prov, "category_slug", FieldSource.AUTO_FILLED, notes="Default sarees")

        k_name, v_name = self.find_field_value(first_record, self.PRODUCT_ALIASES["product_name"])
        clean_pname = self.clean_str(v_name)
        if clean_pname:
            product_name = clean_pname
            self.record_provenance(prod_prov, "product_name", FieldSource.SOURCE, k_name, v_name)
        else:
            # Derive customer-friendly product title from brand + catalog
            parts = [p for p in (brand_name, catalog_name, product_type) if p]
            product_name = " ".join(parts)
            self.record_provenance(
                prod_prov,
                "product_name",
                FieldSource.DERIVED,
                notes="Synthesized from Brand + Catalog + Type",
            )

        k_desc, v_desc = self.find_field_value(first_record, self.PRODUCT_ALIASES["description"])
        description = self.clean_str(v_desc)
        if k_desc:
            self.record_provenance(prod_prov, "description", FieldSource.SOURCE, k_desc, v_desc)

        # Pricing & Order Quantities
        k_price, v_price = self.find_field_value(first_record, self.PRODUCT_ALIASES["supplier_price"])
        supplier_price = self.clean_float(v_price)
        if k_price:
            self.record_provenance(prod_prov, "supplier_price", FieldSource.SOURCE, k_price, v_price)

        k_gst, v_gst = self.find_field_value(first_record, self.PRODUCT_ALIASES["gst_percent"])
        gst_percent = self.clean_float(v_gst)
        if k_gst:
            self.record_provenance(prod_prov, "gst_percent", FieldSource.SOURCE, k_gst, v_gst)

        k_moq, v_moq = self.find_field_value(first_record, self.PRODUCT_ALIASES["moq"])
        moq = self.clean_int(v_moq) or 1
        if k_moq:
            self.record_provenance(prod_prov, "moq", FieldSource.SOURCE, k_moq, v_moq)
        else:
            self.record_provenance(prod_prov, "moq", FieldSource.AUTO_FILLED, notes="Default MOQ = 1")

        k_disp, v_disp = self.find_field_value(first_record, self.PRODUCT_ALIASES["dispatch_location"])
        dispatch_location = self.clean_str(v_disp)
        if k_disp:
            self.record_provenance(prod_prov, "dispatch_location", FieldSource.SOURCE, k_disp, v_disp)

        # Extensible category attributes
        attributes: dict[str, Any] = {}
        for attr_key, attr_aliases in self.CATEGORY_ATTRIBUTES.items():
            k_attr, v_attr = self.find_field_value(first_record, attr_aliases)
            if v_attr is not None:
                attributes[attr_key] = v_attr
                self.record_provenance(prod_prov, f"attr_{attr_key}", FieldSource.SOURCE, k_attr, v_attr)

        # Preserved unmapped supplier facts
        all_mapped_aliases = [
            alias
            for alias_group in (self.PRODUCT_ALIASES, self.CATEGORY_ATTRIBUTES, self.VARIANT_ALIASES)
            for aliases in alias_group.values()
            for alias in aliases
        ]
        mapped_keys = {self.normalize_key(a) for a in all_mapped_aliases}
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
            dispatch_location=dispatch_location,
            attributes=attributes,
            raw_attributes=raw_attributes,
            source_metadata=prod_prov,
        )

        # Build variants for this product
        for idx, rec in enumerate(records, start=1):
            variant = self._build_amra_variant(rec, product, idx, source_file)
            product.add_variant(variant)

        return product

    def _build_amra_variant(
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

        # 1. Supplier SKU (shared across variants in catalog)
        k_sku, v_sku = self.find_field_value(record, self.PRODUCT_ALIASES["supplier_sku"])
        supplier_sku = self.clean_str(v_sku) or f"AMRA-{record.row_number}"
        if k_sku:
            self.record_provenance(var_prov, "supplier_sku", FieldSource.SOURCE, k_sku, v_sku)
        else:
            self.record_provenance(var_prov, "supplier_sku", FieldSource.AUTO_FILLED, notes="Fallback row SKU")

        # 2. Image SKU & Variant Code
        k_img_sku, v_img_sku = self.find_field_value(record, self.VARIANT_ALIASES["image_sku"])
        image_sku = self.clean_str(v_img_sku)
        if k_img_sku:
            self.record_provenance(var_prov, "image_sku", FieldSource.SOURCE, k_img_sku, v_img_sku)

        # Determine unique variant_code
        if image_sku:
            base_code = image_sku
            # Check if this base code is already taken in the parent product
            existing_codes = {v.variant_code.casefold() for v in parent.variants}
            if base_code.casefold() in existing_codes:
                variant_code = f"{base_code}-{variant_idx}"
                self.record_provenance(
                    var_prov,
                    "variant_code",
                    FieldSource.DERIVED,
                    raw_key=k_img_sku,
                    raw_value=v_img_sku,
                    notes=f"Disambiguated duplicate supplier image_sku '{image_sku}' with variant index {variant_idx}",
                )
            else:
                variant_code = base_code
                self.record_provenance(var_prov, "variant_code", FieldSource.SOURCE, k_img_sku, v_img_sku)
        else:
            # Generate deterministic variant code e.g. <group_or_sku>-V01
            prefix = parent.product_group or supplier_sku
            variant_code = f"{prefix}-V{variant_idx:02d}"
            self.record_provenance(
                var_prov,
                "variant_code",
                FieldSource.DERIVED,
                notes=f"Derived variant code from prefix '{prefix}' and index {variant_idx}",
            )

        # 3. Variant Name
        k_vname, v_vname = self.find_field_value(record, self.VARIANT_ALIASES["variant_name"])
        clean_vname = self.clean_str(v_vname)
        if clean_vname:
            variant_name = clean_vname
            self.record_provenance(var_prov, "variant_name", FieldSource.SOURCE, k_vname, v_vname)
        else:
            variant_name = f"Design {variant_idx}"
            self.record_provenance(
                var_prov,
                "variant_name",
                FieldSource.AUTO_FILLED,
                notes=f"Default design variant name {variant_idx}",
            )

        color = clean_vname if clean_vname else None

        # 4. Images
        k_img, v_img = self.find_field_value(record, self.VARIANT_ALIASES["primary_image_url"])
        primary_image_url = self.clean_str(v_img)
        if k_img:
            self.record_provenance(var_prov, "primary_image_url", FieldSource.SOURCE, k_img, v_img)

        gallery_urls: list[str] = []
        for alias in self.VARIANT_ALIASES["gallery_image_urls"]:
            k_g, v_g = self.find_field_value(record, [alias])
            clean_g = self.clean_str(v_g)
            if clean_g:
                gallery_urls.append(clean_g)
                self.record_provenance(var_prov, f"gallery_{alias}", FieldSource.SOURCE, k_g, v_g)

        # 5. Pricing: NEVER invent selling price!
        k_cost, v_cost = self.find_field_value(record, self.VARIANT_ALIASES["supplier_cost"])
        supplier_cost = self.clean_float(v_cost) if v_cost is not None else parent.supplier_price
        if k_cost:
            self.record_provenance(var_prov, "supplier_cost", FieldSource.SOURCE, k_cost, v_cost)

        k_sprice, v_sprice = self.find_field_value(record, self.VARIANT_ALIASES["selling_price"])
        selling_price = self.clean_float(v_sprice) if v_sprice is not None else None
        if k_sprice and selling_price is not None:
            self.record_provenance(var_prov, "selling_price", FieldSource.SOURCE, k_sprice, v_sprice)
        else:
            self.record_provenance(
                var_prov,
                "selling_price",
                FieldSource.MANUAL_REVIEW,
                notes="Selling price unset; requires admin review",
            )

        # 6. Preserved raw variant attributes
        all_mapped_aliases = [
            alias
            for alias_group in (self.PRODUCT_ALIASES, self.CATEGORY_ATTRIBUTES, self.VARIANT_ALIASES)
            for aliases in alias_group.values()
            for alias in aliases
        ]
        mapped_keys = {self.normalize_key(a) for a in all_mapped_aliases}
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
            supplier_cost=supplier_cost,
            selling_price=selling_price,
            image_sku=image_sku,
            primary_image_url=primary_image_url,
            gallery_image_urls=gallery_urls,
            publish=False,
            raw_attributes=var_raw_attributes,
            source_metadata=var_prov,
        )
