# Batch Processing Guide — MANA Store Product Toolkit

This guide documents how to process one or more supplier catalogues in a single batch execution using the permanent PowerShell batch runner.

---

## 1. Quick Start

### Step 1: Edit the Manifest
Edit [`config/batch-manifest.json`](../config/batch-manifest.json) to list the catalogues you want to process:

```json
{
  "batchName": "October_Saree_Batch",
  "catalogues": [
    {
      "name": "Rajtex_Vol58",
      "supplierFolder": "Amra_wholesale",
      "supplierAdapter": "generic",
      "source": "input/Amra_wholesale/Rajtex Ajrak Vol 58/rajtex ajrakh vol-58.jpeg",
      "images": "input/Amra_wholesale/Rajtex Ajrak Vol 58/product-images"
    }
  ]
}
```

### Step 2: Validate the Configuration
Run validation mode to ensure all file paths exist, adapters are valid, and names are unique:

```powershell
.\scripts\run-batch.ps1 -ValidateOnly
```

### Step 3: Execute the Batch
Process all configured catalogues sequentially:

```powershell
.\scripts\run-batch.ps1
```

---

## 2. Configuration (`config/batch-manifest.json`)

The manifest file accepts any reasonable number of catalogue entries. Each entry represents an independent ingestion job.

### Fields Reference

| Field | Required | Description | Example |
| :--- | :--- | :--- | :--- |
| `batchName` | Yes | Label used for the output batch directory. | `"Diwali_Festive_Batch"` |
| `name` | Yes | Unique name for the catalogue (used for the subfolder). | `"Rajtex_Vol58"` |
| `supplierFolder` | Optional | Organizational tag for the supplier. | `"Amra_wholesale"` |
| `supplierAdapter` | Yes | Supplier adapter ID (`generic`, `amra`, `generic_tabular`, `amra_adapter`). | `"generic"` |
| `source` | Yes | Relative or absolute path to the catalogue file (image, PDF, XLSX, CSV, TSV). | `"input/supplier/slip.jpeg"` |
| `images` | Optional | Path to the directory of variant images. Can be `null` or omitted if no images folder exists. | `"input/supplier/images"` |

### Example A: Single Catalogue
```json
{
  "batchName": "Single_Catalog_Run",
  "catalogues": [
    {
      "name": "Rajtex_Vol58",
      "supplierFolder": "Amra_wholesale",
      "supplierAdapter": "generic",
      "source": "input/Amra_wholesale/Rajtex Ajrak Vol 58/rajtex ajrakh vol-58.jpeg",
      "images": "input/Amra_wholesale/Rajtex Ajrak Vol 58/product-images"
    }
  ]
}
```

### Example B: Multiple Catalogues with Mixed Formats
```json
{
  "batchName": "Multi_Supplier_Batch_2026",
  "catalogues": [
    {
      "name": "Rajtex_Vol58",
      "supplierFolder": "Amra_wholesale",
      "supplierAdapter": "generic",
      "source": "input/Amra_wholesale/Rajtex Ajrak Vol 58/rajtex ajrakh vol-58.jpeg",
      "images": "input/Amra_wholesale/Rajtex Ajrak Vol 58/product-images"
    },
    {
      "name": "Minaxi_Vol1",
      "supplierFolder": "Amra_wholesale",
      "supplierAdapter": "amra",
      "source": "input/raw-catalog-001/Amra wholesale Aab Minaxi Vol 1.jpeg",
      "images": null
    },
    {
      "name": "Generic_Kurti_Sheet",
      "supplierFolder": "WeaveStyle",
      "supplierAdapter": "generic",
      "source": "input/raw-catalog-001/MANA_Raw_Supplier_Data.xlsx",
      "images": null
    }
  ]
}
```

---

## 3. Supported Supplier Adapters

Use the adapter identifier that matches your catalogue format:

- **`generic`** (or `generic_tabular`): Universal tabular and image/OCR extractor. Suitable for image catalogue slips, generic spreadsheets, and apparel sheets.
- **`amra`** (or `amra_adapter`, `amra_wholesale`): Specialized adapter for Amra wholesale TSV / Excel catalogue structures with explicit Amra column conventions.

To see all currently registered adapters dynamically:
```powershell
python main.py --list-adapters
```

---

## 4. Supported Source Formats

The batch runner supports all formats natively handled by `main.py`:
- **Document / Slip Images**: `.jpeg`, `.jpg`, `.png`, `.webp`, `.bmp`, `.tiff`
- **PDF Documents**: `.pdf`
- **Tabular Files**: `.xlsx`, `.csv`, `.tsv`

---

## 5. Output Organization

Every batch run creates a timestamped folder inside `output/`:

```text
output/
└── <batchName>_<YYYYMMDD_HHMMSS>/
    ├── batch_summary.csv                         <-- Overview table of all jobs in the batch
    │
    ├── 01_Rajtex_Vol58/
    │   ├── manastore_pre_ready.xlsx              <-- FINAL ADMIN IMPORT FILE for store import
    │   ├── RUN_SUMMARY.txt                       <-- Guide for catalog managers
    │   ├── execution.log                         <-- Complete processing output and timestamps
    │   ├── _internal/                            <-- Supporting artifacts (DO NOT upload to store)
    │   │   ├── canonical_products.xlsx
    │   │   ├── canonical_products.json
    │   │   ├── raw_supplier_extracted.tsv
    │   │   ├── refined_products.xlsx
    │   │   ├── mana_store_import_ready.xlsx
    │   │   └── image_approval_manifest.json
    │   └── _reports/                             <-- Audit logs and validation diagnostics
    │       ├── validation_report.xlsx
    │       └── validation_report.json
    │
    └── 02_Minaxi_Vol1/
        ├── manastore_pre_ready.xlsx
        ├── ...
```

### Key Rules:
1. **Admin Importer File**: Upload **ONLY** `manastore_pre_ready.xlsx` to the admin store importer.
2. **Internal Artifacts**: All canonical domain models, raw extracted TSVs, and enrichment workbooks are safely stored in `_internal/`.
3. **Reports**: Validation checks and match audit spreadsheets are isolated in `_reports/`.

---

## 6. Understanding `batch_summary.csv`

At the completion of each batch run, `batch_summary.csv` is written to the batch root folder. It contains the following columns:

| Column | Description |
| :--- | :--- |
| `CatalogueIndex` | 1-based index matching the manifest order. |
| `CatalogueName` | Name of the catalogue. |
| `Status` | `SUCCESS` if the pipeline exited with 0 and `manastore_pre_ready.xlsx` exists; otherwise `FAILED`. |
| `ExitCode` | Exit code returned by `main.py` (0 on success). |
| `DurationSeconds` | Wall-clock execution time for this catalogue. |
| `SupplierAdapter` | Adapter used for ingestion. |
| `SourcePath` | Configured source input path. |
| `ImagesPath` | Configured image directory (`N/A` if none). |
| `OutputDir` | Absolute path to this catalogue's output directory. |
| `FinalWorkbook` | Path to `manastore_pre_ready.xlsx` (`N/A` if failed). |
| `ErrorSummary` | Brief error description if the catalogue failed. |

---

## 7. Retrying Failed Catalogues Without Overwriting

Because each batch run creates a new timestamped directory, previous outputs are never overwritten or deleted.

If catalogue #2 in a 3-catalogue batch fails:
1. Review its error log in `output/<BatchFolder>/02_<Name>/execution.log` or `stderr.log`.
2. Fix the source data, image folder, or adapter in `config/batch-manifest.json` (you can comment out or remove catalogue #1 and #3 to only re-run catalogue #2).
3. Run `.\scripts\run-batch.ps1`.
4. A new batch folder with the fixed catalogue's `manastore_pre_ready.xlsx` will be generated.

---

## 8. Consolidating Workbooks After Review (`combine-pre-ready.py`)

After a batch finishes, you can review and edit each catalogue's individual `manastore_pre_ready.xlsx` workbook in its subfolder. Once reviewed and saved, use the standalone consolidation script to combine them into a single admin import workbook at the batch root:

```powershell
python scripts/combine-pre-ready.py "output/<BATCH_FOLDER>"
```

### Preview Without Creating Output (Dry Run)
```powershell
python scripts/combine-pre-ready.py "output/<BATCH_FOLDER>" --dry-run
```

### Overwrite an Existing Combined Workbook
```powershell
python scripts/combine-pre-ready.py "output/<BATCH_FOLDER>" --force
```

### Key Safety Guarantees:
- **Zero changes to source files**: Individual catalogue workbooks are never modified, moved, or deleted.
- **Strict schema validation**: All workbooks must match the exact 26-column Amra schema in the correct order.
- **Safe discovery**: Scans only immediate catalogue subdirectories; ignores `_internal/`, `_reports/`, and existing root files.
- **Data integrity**: Retains all data types, blank cells, repeated `No` values, prices, URLs, and rows without recalculation or deduplication.
- **Summary**: Creates `consolidation_summary.txt` and `<BATCH_FOLDER>/manastore_pre_ready.xlsx` at the batch directory root.

