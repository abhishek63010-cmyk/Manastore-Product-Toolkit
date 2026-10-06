#!/usr/bin/env python3
"""
MANA Store Product Import & Normalization Toolkit CLI.

Usage:
    python main.py --input <path_to_file> [options]
"""

import argparse
import json
import sys
from pathlib import Path

from src.adapters.registry import AdapterRegistry
from src.pipeline.config import PipelineConfig
from src.pipeline.orchestrator import PipelineOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MANA Store Supplier Product Import & Normalization Toolkit",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "-i", "--input",
        dest="input_path",
        type=str,
        help="Path to supplier input tabular file (CSV, TSV, or XLSX)",
    )
    parser.add_argument(
        "-s", "--supplier",
        type=str,
        default=None,
        help="Explicit supplier adapter name (e.g. 'amra', 'generic'). Auto-detected if omitted.",
    )
    parser.add_argument(
        "--sheet",
        dest="sheet_name",
        type=str,
        default=None,
        help="Worksheet name to process for multi-sheet Excel files",
    )
    parser.add_argument(
        "--images",
        dest="images_path",
        type=str,
        default=None,
        help="Path to directory or ZIP archive containing product images",
    )
    parser.add_argument(
        "-o", "--output",
        dest="output_dir",
        type=str,
        default="./output",
        help="Destination directory for canonical exports and reports",
    )
    parser.add_argument(
        "-f", "--format",
        dest="export_formats",
        type=str,
        default="xlsx,json",
        help="Comma-separated export formats: xlsx, json, csv",
    )
    parser.add_argument(
        "--upload-images",
        action="store_true",
        default=False,
        help="Upload matched images to Blob storage (requires BLOB_READ_WRITE_TOKEN in env)",
    )
    parser.add_argument(
        "--upload-prefix",
        type=str,
        default="products",
        help="Prefix path for destination keys in blob storage",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run pipeline without writing output files or executing network uploads",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        default=False,
        help="Exit with non-zero code if validation errors are detected",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        default=False,
        help="Disable generating validation report spreadsheets and JSON audits",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Print detailed execution logging to stderr/stdout",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="Emit execution result strictly as machine-readable JSON to stdout",
    )
    parser.add_argument(
        "--list-adapters",
        action="store_true",
        default=False,
        help="List all registered supplier adapters and exit",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Handle --list-adapters
    if args.list_adapters:
        adapters = AdapterRegistry.list_adapters()
        if args.json_output:
            print(json.dumps({"registered_adapters": adapters}, indent=2))
        else:
            print("Registered Supplier Adapters:")
            for a in adapters:
                print(f"  - {a}")
        return 0

    if not args.input_path:
        parser.error("The --input (-i) argument is required unless using --list-adapters.")
        return 2

    input_file = Path(args.input_path)
    if not input_file.exists():
        if args.json_output:
            print(json.dumps({"success": False, "error": f"Input file not found: {args.input_path}"}))
        else:
            print(f"Error: Input file does not exist: {args.input_path}", file=sys.stderr)
        return 1

    formats = [fmt.strip().lower() for fmt in args.export_formats.split(",") if fmt.strip()]

    config = PipelineConfig(
        input_path=input_file,
        output_dir=Path(args.output_dir),
        supplier=args.supplier,
        sheet_name=args.sheet_name,
        images_path=Path(args.images_path) if args.images_path else None,
        upload_images=args.upload_images,
        upload_prefix=args.upload_prefix,
        export_formats=formats,
        dry_run=args.dry_run,
        fail_on_error=args.fail_on_error,
        generate_report=not args.no_report,
        verbose=args.verbose,
    )

    result = PipelineOrchestrator.run(config)

    if args.json_output:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        result.print_summary()

    if not result.success:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
