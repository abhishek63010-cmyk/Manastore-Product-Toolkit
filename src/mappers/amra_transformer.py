import re
from typing import Optional, Union, Any
from pathlib import Path
import pandas as pd
import openpyxl
from PIL import Image

from src.models.product import Product
from src.models.variant import Variant
from src.images.color import ImageColorAnalyzer, ImageColorResult


class AmraWorkbookTransformer:
    """
    Dedicated transformation engine to convert canonical Product and Variant models
    into the exact 26-column Amra import workbook schema.
    
    Target Schema:
    No, supplier_name, catalog_name, product_group, product_name, varient_name,
    supplier_sku, product_type, category_slug, brand_name, fabric, blouse_fabric,
    work, occasion, size, description, supplier_price, gst_percent, selling_proce,
    image-sku, image_url, publish, image1_url, image2_url, image3_url, image4_url
    """

    EXACT_COLUMNS = [
        "No",
        "supplier_name",
        "catalog_name",
        "product_group",
        "product_name",
        "varient_name",
        "supplier_sku",
        "product_type",
        "category_slug",
        "brand_name",
        "fabric",
        "blouse_fabric",
        "work",
        "occasion",
        "size",
        "description",
        "supplier_price",
        "gst_percent",
        "selling_price",
        "image-sku",
        "image_url",
        "publish",
        "image1_url",
        "image2_url",
        "image3_url",
        "image4_url",
    ]

    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

    @classmethod
    def generate_product_group(cls, catalog_name: Optional[str]) -> str:
        """
        Derive business product group identifier from catalog name.
        Example: 'Rajtex Ajrak Vol-58' -> 'Rajtex-A'
        Example: 'Mahotsav Kriti' -> 'Mahots-K'
        Removes catalogue volume, series, design suffixes, and numbers.
        """
        if not catalog_name or not str(catalog_name).strip():
            return "GROUP-1"

        clean = str(catalog_name).strip()
        # 1. Strip series / range patterns like "142001 TO 142005 Series", "S-5216 TO S-5227 SERIES"
        clean = re.sub(r"(?i)\b\d+\s+TO\s+\d+\s*(?:series)?\b.*", "", clean).strip()
        clean = re.sub(r"(?i)\bS-\d+\s+TO\s+S-\d+\s*(?:series)?\b.*", "", clean).strip()
        # 2. Strip volume/vol/part/edition patterns like "Vol-58", "Vol 4", "Volume 1"
        clean = re.sub(r"(?i)\b(?:vol|volume|edition|part)[\s\-_]*\d+.*", "", clean).strip()
        # 3. Strip trailing numbers
        clean = re.sub(r"[\s\-_]*\d+$", "", clean).strip()

        # Split into alphanumeric word tokens
        tokens = [t for t in re.split(r"[\s\-_/]+", clean) if t]
        if not tokens:
            return "GROUP-1"

        if len(tokens) == 1:
            t0 = tokens[0]
            if len(t0) <= 6:
                return t0
            return t0[:6]

        # Multi-word: First token + second token initial(s)
        # Handle 'Lifestyle' or other generic middle words if 3+ tokens:
        first = tokens[0]
        # Common brand prefixes in Amra catalog (e.g. Sethnic Lifestyle -> Sethni-L..., Bunawat -> Bunawa-...)
        # Amra standard style:
        # If first word length > 6, abbreviate or take first 6 chars (e.g. Rajtex -> Rajtex, Mahotsav -> Mahots, Bunawat -> Bunawa, Sethnic -> Sethni)
        if len(first) > 6:
            prefix = first[:6]
        else:
            prefix = first

        # Second part: if tokens[1] is 'Lifestyle' and len(tokens) > 2:
        if tokens[1].lower() in ("lifestyle", "wholesale") and len(tokens) > 2:
            initial = tokens[1][0].upper() + tokens[2][0].upper()
        else:
            initial = tokens[1][0].upper()

        return f"{prefix}-{initial}"

    @classmethod
    def derive_supplier_sku_fallback(cls, catalog_name: Optional[str]) -> Optional[str]:
        """
        Derive supplier SKU from catalog volume/suffix when explicit supplier SKU is absent.
        Example: 'Rajtex Ajrak Vol-58' -> 'Vol-58'
        """
        if not catalog_name:
            return None

        # Search for Vol-XX / Volume XX
        vol_match = re.search(r"(?i)\b(vol(?:ume)?[\s\-_]*\d+)\b", str(catalog_name))
        if vol_match:
            raw_vol = vol_match.group(1)
            # Normalize to 'Vol-<number>'
            num_match = re.search(r"\d+", raw_vol)
            if num_match:
                return f"Vol-{num_match.group(0)}"
            return raw_vol

        # Search for series code or number at end
        series_match = re.search(r"(?i)\b(?:series[\s\-_]*)?([a-z0-9\-]+)$", str(catalog_name).strip())
        if series_match:
            return series_match.group(1)

        return None

    @classmethod
    def generate_customer_product_name(
        cls,
        catalog_name: Optional[str],
        brand_name: Optional[str] = None,
        fabric: Optional[str] = None,
        work: Optional[str] = None,
        product_type: Optional[str] = None,
    ) -> str:
        """
        Synthesize customer-facing product title from brand, collection, fabric, work, and type.
        Example: 'Rajtex Ajrak Vol-58' -> 'Rajtex Ajrakh Printed Modal Satin Saree'
        Example: 'RAJTEX NOORANI' -> 'Rajtex Noorani Pashmina Silk Saree'
        """
        clean_cat = str(catalog_name or "").strip()
        brand = str(brand_name or "").strip()
        p_type = str(product_type or "Saree").strip()
        fab = str(fabric or "").strip()

        # Strip brand from catalog name if present
        if brand and clean_cat:
            clean_cat = re.sub(rf"(?i)\b{re.escape(brand)}\b", "", clean_cat).strip()

        # Strip volume, series, edition, numbers
        clean_cat = re.sub(r"(?i)\b(?:vol|volume|edition|part)[\s\-_]*\d+.*", "", clean_cat).strip()
        clean_cat = re.sub(r"[\s\-_]*\d+$", "", clean_cat).strip()

        # Handle work adjective (e.g. 'Ajrakh Print' -> 'Ajrakh Printed')
        work_str = str(work or "").strip()
        work_adj = ""
        if work_str:
            if work_str.lower().endswith(" print"):
                work_adj = work_str[:-6].strip() + " Printed"
            elif work_str.lower().endswith(" printed") or work_str.lower().endswith(" work"):
                work_adj = work_str
            elif len(work_str.split()) <= 2:
                work_adj = f"{work_str} Work"

        tokens = []
        if brand:
            tokens.append(brand)

        if work_adj and clean_cat and (clean_cat.lower() in work_adj.lower() or work_adj.lower().startswith(clean_cat.lower()[:4])):
            tokens.append(work_adj)
        else:
            if clean_cat:
                cat_token = clean_cat.title() if (brand and not brand.isupper() and clean_cat.isupper()) else clean_cat
                tokens.append(cat_token)
            if work_adj and len(work_adj.split()) <= 2:
                tokens.append(work_adj)

        if fab:
            tokens.append(fab)
        if p_type:
            tokens.append(p_type)

        return " ".join(tokens) if tokens else (catalog_name or "Product")

    @classmethod
    def generate_factual_description(
        cls,
        product: Product,
        row_dict: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Generate rich customer-facing factual description containing brand, catalogue,
        saree fabric, blouse fabric, work, occasion, and single-piece price.
        """
        if product.description and str(product.description).strip() and len(str(product.description).strip()) > 20:
            return str(product.description).strip()

        row = row_dict or {}
        brand = str(row.get("brand_name") or product.brand_name or "").strip()
        catalog = str(row.get("catalog_name") or product.catalog_name or "").strip()
        fabric = str(row.get("fabric") or product.get_attribute("fabric") or product.get_attribute("saree_fabric") or "").strip()
        blouse_fabric = str(row.get("blouse_fabric") or product.get_attribute("blouse_fabric") or "").strip()
        work = str(row.get("work") or product.get_attribute("work") or "").strip()
        occasion = str(row.get("occasion") or product.get_attribute("occasion") or "").strip()
        product_type = str(row.get("product_type") or product.product_type or "Saree").strip()

        price = product.supplier_price
        if price is None and "supplier_price" in row:
            try:
                price = float(row["supplier_price"])
            except (ValueError, TypeError):
                pass

        sentences: list[str] = []

        if brand and fabric and catalog:
            sentences.append(f"Discover this elegant {fabric} {product_type} from {brand} ({catalog} Collection).")
        elif brand and fabric:
            sentences.append(f"Discover this elegant {fabric} {product_type} from {brand}.")
        elif brand and catalog:
            sentences.append(f"Discover this premium {product_type} from {brand} ({catalog} Collection).")
        elif fabric:
            sentences.append(f"Designed in premium {fabric} fabric.")
        elif catalog:
            sentences.append(f"Premium quality {product_type} from {catalog} Collection.")
        else:
            sentences.append(f"Premium quality {product_type}.")

        if work:
            sentences.append(f"Showcases exquisite {work}.")

        if blouse_fabric and blouse_fabric.lower() != fabric.lower():
            sentences.append(f"Features a coordinated {blouse_fabric} blouse fabric.")
        elif blouse_fabric:
            sentences.append("Includes a matching coordinated blouse piece.")

        if occasion:
            sentences.append(f"An excellent choice for {occasion}, offering elegance, comfort, and timeless appeal.")

        if price is not None and price > 0:
            formatted_price = f"{int(price):,}" if price.is_integer() else f"{price:,.2f}"
            sentences.append(f"Single piece dropshipping price: ₹{formatted_price}.")

        if not sentences:
            return f"Premium quality {catalog} {product_type}."

        return " ".join(sentences)

    @classmethod
    def generate_description(
        cls,
        product: Product,
        row_dict: dict[str, Any],
    ) -> str:
        return cls.generate_factual_description(product, row_dict)

    @classmethod
    def discover_and_sort_images(cls, images_path: Union[str, Path]) -> list[Path]:
        """
        Discover image files in directory and sort deterministically by natural order.
        """
        p = Path(images_path)
        if not p.exists() or not p.is_dir():
            return []

        images: list[Path] = [
            f for f in p.iterdir()
            if f.is_file() and f.suffix.lower() in cls.IMAGE_EXTENSIONS
        ]

        def natural_sort_key(path: Path):
            return [
                int(c) if c.isdigit() else c.lower()
                for c in re.split(r"(\d+)", path.stem)
            ]

        return sorted(images, key=natural_sort_key)

    @classmethod
    def transform_to_amra_records(
        cls,
        products: list[Product],
        images_path: Optional[Union[str, Path]] = None,
    ) -> list[dict[str, Any]]:
        """
        Transform canonical Product and Variant domain models into the exact Amra schema rows.
        The pre-ready export is a strict projection of the canonical data.
        """
        # Discover images if provided for fallback matching only
        image_files = cls.discover_and_sort_images(images_path) if images_path else []

        image_analysis: list[dict[str, Any]] = []
        if image_files:
            for img_path in image_files:
                sku = img_path.stem
                res: ImageColorResult = ImageColorAnalyzer.analyze_image(img_path, crop_center_ratio=0.6)
                image_analysis.append({
                    "image_path": img_path,
                    "image_sku": sku,
                    "color_result": res,
                    "color": res.canonical_color if (res.status == "EXTRACTED" and not res.is_ambiguous) else None,
                })

        out_rows: list[dict[str, Any]] = []

        # Process each product catalog
        for prod_idx, prod in enumerate(products, start=1):
            prod_group = prod.product_group or cls.generate_product_group(prod.catalog_name)
            if not prod.product_group:
                prod.product_group = prod_group
            fallback_sku = cls.derive_supplier_sku_fallback(prod.catalog_name)

            # Common product attributes
            fabric = prod.get_attribute("fabric") or prod.get_attribute("saree_fabric") or ""
            blouse_fabric = prod.get_attribute("blouse_fabric") or ""
            work = prod.get_attribute("work") or ""
            occasion = prod.get_attribute("occasion") or ""
            size = prod.get_attribute("size") or ""
            gst_percent = f"{int(prod.gst_percent)}% Extra" if prod.gst_percent is not None else ""

            variants = prod.variants
            num_images = len(image_analysis)

            for var_idx, var in enumerate(variants):
                row: dict[str, Any] = {}
                row["No"] = prod_idx
                row["supplier_name"] = prod.supplier_name or "Amra Wholesale"
                row["catalog_name"] = prod.catalog_name
                row["product_group"] = prod_group

                # Customer-facing product name: use canonical product_name
                if prod.product_name and prod.product_name.strip() != (prod.catalog_name or "").strip():
                    p_name = prod.product_name
                else:
                    p_name = cls.generate_customer_product_name(
                        catalog_name=prod.catalog_name,
                        brand_name=prod.brand_name,
                        fabric=fabric,
                        work=work,
                        product_type=prod.product_type,
                    )
                row["product_name"] = p_name
                row["product_type"] = prod.product_type or "Saree"
                row["category_slug"] = prod.category_slug or "sarees"
                row["brand_name"] = prod.brand_name or ""
                row["fabric"] = fabric or var.attributes.get("fabric", "")
                row["blouse_fabric"] = blouse_fabric or var.attributes.get("blouse_fabric", "")
                row["work"] = work or var.attributes.get("work", "")
                row["occasion"] = occasion or var.attributes.get("occasion", "")
                row["size"] = var.size or size or ""

                # Supplier SKU: Explicit source SKU takes precedence, else fallback
                row["supplier_sku"] = var.supplier_sku or fallback_sku or ""

                # Variant pricing (single-piece dropshipping rule)
                row["supplier_price"] = prod.supplier_price if prod.supplier_price is not None else var.supplier_cost
                row["gst_percent"] = gst_percent
                row["selling_price"] = var.selling_price if var.selling_price is not None else ""

                # Image SKU & Variant Name directly from canonical model
                matched_img = image_analysis[var_idx] if var_idx < num_images else None

                if var.image_sku:
                    image_sku_val = var.image_sku
                elif matched_img:
                    image_sku_val = matched_img["image_sku"]
                else:
                    image_sku_val = ""

                if var.color:
                    varient_name_val = var.color
                elif matched_img and matched_img["color"]:
                    varient_name_val = matched_img["color"]
                elif var.variant_name and not var.variant_name.lower().startswith("design "):
                    varient_name_val = var.variant_name
                else:
                    varient_name_val = ""

                row["varient_name"] = varient_name_val
                row["image-sku"] = image_sku_val

                # Image URLs: Primary image URL from variant or image asset (strictly remote URLs only)
                def _is_remote_url(val: Optional[str]) -> bool:
                    if not val:
                        return False
                    s = str(val).strip()
                    return s.startswith("http://") or s.startswith("https://") or "blob.vercel-storage.com" in s

                hero_url = var.primary_image_url if _is_remote_url(var.primary_image_url) else ""
                if not hero_url and var.images:
                    for img in var.images:
                        if _is_remote_url(img.blob_url):
                            hero_url = img.blob_url
                            break
                row["image_url"] = hero_url

                gal_urls = [u for u in (var.gallery_image_urls or []) if _is_remote_url(u)]
                if not gal_urls and var.images:
                    for img in var.images:
                        if img.blob_url and _is_remote_url(img.blob_url) and img.blob_url != hero_url:
                            gal_urls.append(img.blob_url)

                row["image1_url"] = gal_urls[0] if len(gal_urls) > 0 else ""
                row["image2_url"] = gal_urls[1] if len(gal_urls) > 1 else ""
                row["image3_url"] = gal_urls[2] if len(gal_urls) > 2 else ""
                row["image4_url"] = gal_urls[3] if len(gal_urls) > 3 else ""

                row["publish"] = var.publish

                # Factual description
                if prod.description:
                    desc_val = prod.description
                else:
                    desc_val = cls.generate_factual_description(prod, row)
                    prod.description = desc_val
                row["description"] = desc_val

                # Publish flag: Strictly False until human review/approval
                row["publish"] = False

                out_rows.append(row)

        return out_rows

    @classmethod
    def export_amra_workbook(
        cls,
        products: list[Product],
        output_path: Union[str, Path],
        images_path: Optional[Union[str, Path]] = None,
    ) -> Path:
        """
        Generate and write the final Amra-format Excel workbook with formula-safe cell writing.
        """
        target = Path(output_path)
        if target.is_dir() or target.suffix == "":
            target.mkdir(parents=True, exist_ok=True)
            target = target / "amra_data_ready.xlsx"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

        rows = cls.transform_to_amra_records(products, images_path=images_path)
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=cls.EXACT_COLUMNS)

        # Ensure exact column presence and ordering
        for col in cls.EXACT_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        df = df[cls.EXACT_COLUMNS]

        # Write safely using openpyxl to prevent accidental formula evaluation (#NAME? on '=' or '@')
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Amra_Data"

        # Append headers
        ws.append(cls.EXACT_COLUMNS)

        # Append data rows with text data_type protection
        formula_prefixes = ("=", "+", "-", "@")
        for row_vals in df.itertuples(index=False):
            ws.append(list(row_vals))
            row_idx = ws.max_row
            for col_idx, val in enumerate(row_vals, start=1):
                if isinstance(val, str) and val.startswith(formula_prefixes):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.data_type = "s"

        wb.save(target)
        return target.resolve()
