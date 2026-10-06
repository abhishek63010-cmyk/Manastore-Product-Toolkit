import json
from urllib.parse import urlparse

import pandas as pd


def _records(value):
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    return list(value or [])


def _image_order(image):
    order = image.get("image_index", "")
    try:
        return (0, int(order), str(image.get("image_id", "")))
    except (TypeError, ValueError):
        return (1, str(order), str(image.get("image_id", "")))


def map_approved_image_urls(products, approved_images):
    """Map returned upload URLs to exact SKU identities; never synthesize URLs."""

    mapped = products.copy()
    if "supplier_sku" not in mapped:
        raise ValueError("Products must include supplier_sku.")
    skus = mapped["supplier_sku"].fillna("").astype(str).str.strip()
    duplicates = skus[skus.ne("") & skus.duplicated(keep=False)]
    if not duplicates.empty:
        raise ValueError("Image mapping requires unique supplier_sku values.")
    mapped["publish"] = False
    for column in ("image-sku", "image_url", "image_urls"):
        if column not in mapped:
            mapped[column] = ""

    uploaded = {}
    unmatched = []
    for image in _records(approved_images):
        sku = str(image.get("supplier_sku", "")).strip()
        image_id = str(image.get("image_id", "")).strip()
        blob_url = str(image.get("blob_url", "")).strip()
        if not sku or not image_id or not blob_url:
            unmatched.append({
                "supplier_sku": sku, "image_id": image_id,
                "status": "MISSING_IDENTITY_OR_URL",
            })
            continue
        parsed = urlparse(blob_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError(f"Invalid uploaded image URL for {image_id!r}.")
        uploaded.setdefault(sku, []).append(image)

    known_skus = set(mapped["supplier_sku"].fillna("").astype(str).str.strip())
    for sku, images in uploaded.items():
        if sku not in known_skus:
            unmatched.extend({
                "supplier_sku": sku,
                "image_id": str(image.get("image_id", "")),
                "status": "UNMATCHED_SKU",
            } for image in images)
            continue

        images.sort(key=_image_order)
        urls = [str(image["blob_url"]).strip() for image in images]
        image_ids = [str(image["image_id"]).strip() for image in images]
        selection = mapped["supplier_sku"].fillna("").astype(str).str.strip() == sku
        mapped.loc[selection, "image_url"] = urls[0]
        mapped.loc[selection, "image-sku"] = image_ids[0]
        mapped.loc[selection, "image_urls"] = json.dumps(urls)
        for image in images:
            unmatched.append({
                "supplier_sku": sku,
                "image_id": str(image["image_id"]),
                "status": "MAPPED",
            })

    return mapped, pd.DataFrame(
        unmatched, columns=["supplier_sku", "image_id", "status"]
    )


def export_final_products(products, output_path):
    """Export final production records after actual upload URLs are mapped."""

    if "image_url" not in products:
        raise ValueError("Final products must include uploaded image_url values.")
    if products["image_url"].fillna("").astype(str).str.strip().eq("").any():
        raise ValueError("Cannot export products with missing approved image URLs.")
    final_products = products.copy()
    final_products["publish"] = False
    if str(output_path).lower().endswith(".csv"):
        final_products.to_csv(output_path, index=False)
    else:
        final_products.to_excel(output_path, index=False)
