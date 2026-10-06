# MANA Store Product Import & Normalization Toolkit

A modular, supplier-agnostic pipeline for ingesting, transforming, matching, validating, and exporting catalog datasets into canonical MANA Store domain models.

---

## Key Capabilities

- **Generic Tabular Ingestion (`src/extractors/`):**
  Supplier-agnostic reading of Excel (`.xlsx`), CSV, and TSV formats with full cell and column preservation.
- **Pluggable Supplier Adapters (`src/adapters/`):**
  Automatic detection and explicit adapter registry mapping supplier-specific schemas (e.g. Amra Wholesale, Weave365, Generic) to canonical domain models.
- **Deterministic Image Inventory & Matching (`src/images/`):**
  Natural alphanumeric sorting, priority resolution (Exact SKU > Scoped Parent Token > Disambiguated Sequence), ZIP archive & folder discovery.
- **Pluggable Blob Storage Uploader (`src/uploaders/`):**
  Storage-agnostic blob uploader abstraction with deterministic destination keys, MIME type detection, idempotent duplicate reuse, and simulated local/test uploaders.
- **Canonical Quality Gate & Validation (`src/validators/`):**
  Multi-level validation rules (ERROR, WARNING, INFO) ensuring schema integrity without hardcoding category biases.
- **Standardized Multi-Format Export (`src/exporters/`):**
  Zero legacy typos (`variant_name`, `selling_price`, `image_sku`, `catalog_name`), flat tabular Excel/CSV serialization, and structured JSON output.

---

## Architecture Overview

```
Supplier Source (CSV / TSV / XLSX)
           │
           ▼
┌───────────────────────────┐
│ Generic Tabular Extractor │  ──> RawTabularData & RawRecords
└───────────────────────────┘
           │
           ▼
┌───────────────────────────┐
│ AdapterRegistry / Adapter │  ──> Product + Variant Canonical Domain Models
└───────────────────────────┘
           │
           ├────────────────────────────┐
           ▼                            ▼
┌───────────────────────────┐ ┌───────────────────────────┐
│  Generic Image Inventory  │ │ Pluggable Blob Uploader   │
│ & Deterministic Matcher   │ │ (Vercel Blob / FakeMock)  │
└───────────────────────────┘ └───────────────────────────┘
           │                            │
           ▼                            ▼
┌─────────────────────────────────────────────────────────┐
│ Generic Quality Gate & Validation (Multi-Level Rules)   │
└─────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────┐
│ Canonical Exporters & Multi-Tab Audit Writers           │
│ (canonical_products.xlsx, JSON, CSV, validation_report) │
└─────────────────────────────────────────────────────────┘
```

---

## Installation & Setup

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Or on Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Environment Variables

If uploading to Vercel Blob:
```env
BLOB_READ_WRITE_TOKEN=vercel_blob_rw_...
```

---

## CLI Usage (`main.py`)

### Command Options

```text
usage: main.py [-h] [-i INPUT_PATH] [-s SUPPLIER] [--sheet SHEET_NAME]
               [--images IMAGES_PATH] [-o OUTPUT_DIR] [-f EXPORT_FORMATS]
               [--upload-images] [--upload-prefix UPLOAD_PREFIX] [--dry-run]
               [--fail-on-error] [--no-report] [-v] [--json] [--list-adapters]

Options:
  -h, --help            Show help message and exit
  -i, --input PATH      Path to supplier input tabular file (CSV, TSV, or XLSX)
  -s, --supplier NAME   Supplier adapter name override (e.g. 'amra', 'generic')
  --sheet SHEET         Worksheet name for multi-sheet Excel files
  --images PATH         Path to folder or ZIP archive containing product images
  -o, --output DIR      Destination directory for exports (default: ./output)
  -f, --format FORMATS  Comma-separated export formats: xlsx, json, csv (default: xlsx,json)
  --upload-images       Upload matched images to Blob storage
  --upload-prefix PATH  Storage path prefix for destination keys (default: products)
  --dry-run             Run pipeline without creating disk files or uploading
  --fail-on-error       Exit with non-zero exit code if validation errors are detected
  --no-report           Disable generating validation report spreadsheets/audits
  -v, --verbose         Print detailed step-by-step logs
  --json                Emit execution summary strictly as JSON to stdout
  --list-adapters       List all registered supplier adapters and exit
```

---

## Example Commands

### 1. Ingest Generic CSV Catalog with Images Folder
```bash
python main.py \
  --input data/kurtis.csv \
  --images data/images/ \
  --output ./output/kurtis \
  --format xlsx,json,csv
```

### 2. Ingest Amra Wholesale Excel Workbook (Auto-Detected)
```bash
python main.py \
  --input Amra_data_with_all_blob_urls.xlsx \
  --output ./output/amra_catalog
```

### 3. Dry-Run Execution with Machine-Readable Output (CI/CD)
```bash
python main.py \
  --input input/AMRA-RAJTEX-001/raw_supplier_data.xlsx \
  --dry-run \
  --json
```

### 4. Strict Quality Gate Mode
```bash
python main.py \
  --input new_supplier_data.csv \
  --fail-on-error \
  --output ./output/validated
```

---

## Running Automated Tests

```bash
pytest -v
```
All 70 unit and end-to-end regression tests verify schema consistency, edge-case disambiguation, idempotency, and error-handling without accessing external network resources.
