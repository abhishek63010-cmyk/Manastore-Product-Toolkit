import csv
from pathlib import Path
from typing import Any, Optional, Union
from zipfile import is_zipfile
import openpyxl
import pandas as pd
from src.extractors.raw_record import RawRecord, RawTabularData


class TabularExtractor:
    """
    Generic tabular data extractor supporting XLSX, CSV, and TSV files.
    
    Principles:
    - Completely supplier-agnostic (no hardcoded schemas, sheet names, or domain fields).
    - Preserves all columns, cell values, sheet names, and source row indices.
    - Inspects workbooks and allows processing any worksheet.
    """

    @classmethod
    def list_sheets(cls, file_path: Union[str, Path]) -> list[str]:
        """Inspect available worksheets in an Excel workbook."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if is_zipfile(path):
            wb = openpyxl.load_workbook(path, read_only=True)
            sheet_names = list(wb.sheetnames)
            wb.close()
            return sheet_names
        return []

    @classmethod
    def extract(
        cls,
        file_path: Union[str, Path],
        sheet_name: Optional[Union[str, int]] = None,
        delimiter: Optional[str] = None,
    ) -> RawTabularData:
        """
        Extract raw tabular data from CSV, TSV, or XLSX file.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found: {path}")

        available_sheets: list[str] = []
        selected_sheet: Optional[str] = None

        if is_zipfile(path):
            # Process XLSX / Excel workbook
            available_sheets = cls.list_sheets(path)
            if not available_sheets:
                raise ValueError(f"No worksheets found in workbook: {path.name}")

            if sheet_name is None:
                selected_sheet = available_sheets[0]
            elif isinstance(sheet_name, int):
                selected_sheet = available_sheets[sheet_name]
            else:
                if sheet_name not in available_sheets:
                    raise ValueError(
                        f"Worksheet '{sheet_name}' not found. Available sheets: {available_sheets}"
                    )
                selected_sheet = sheet_name

            df = pd.read_excel(
                path,
                sheet_name=selected_sheet,
                dtype=object,  # Keep all values as raw objects/strings
                keep_default_na=False,
            )
        else:
            # Process CSV / TSV text file
            if delimiter is None:
                delimiter = cls._detect_delimiter(path)

            # Check if file is a 2-column key-value attribute file (e.g. Column\tValue)
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                sample_lines = [f.readline() for _ in range(5)]
            
            first_line = sample_lines[0] if sample_lines else ""
            cols = [c.strip().lower() for c in first_line.split(delimiter)]
            
            if cols == ["column", "value"]:
                # Parse 2-column attribute key-value file into a single composite row
                df_kv = pd.read_csv(
                    path,
                    sep=delimiter,
                    dtype=str,
                    keep_default_na=False,
                )
                kv_dict = {}
                for _, r in df_kv.iterrows():
                    k = str(r.iloc[0]).strip()
                    v = r.iloc[1] if len(r) > 1 else ""
                    if k:
                        kv_dict[k] = v
                df = pd.DataFrame([kv_dict])
            else:
                df = pd.read_csv(
                    path,
                    sep=delimiter,
                    dtype=object,
                    keep_default_na=False,
                )

        headers = [str(c).strip() for c in df.columns]
        records: list[RawRecord] = []

        for idx, row in df.iterrows():
            row_data = {}
            for col_idx, col_name in enumerate(headers):
                val = row.iloc[col_idx]
                if pd.isna(val) or val == "":
                    row_data[col_name] = None
                else:
                    row_data[col_name] = val

            records.append(
                RawRecord(
                    row_number=idx + 2,  # 1-indexed (row 1 is header)
                    data=row_data,
                    source_file=str(path),
                    sheet_name=selected_sheet,
                )
            )

        return RawTabularData(
            source_file=str(path),
            sheet_name=selected_sheet,
            available_sheets=available_sheets,
            headers=headers,
            records=records,
        )

    @staticmethod
    def _detect_delimiter(path: Path) -> str:
        """Detect separator (comma, tab, semicolon, pipe) using file inspection."""
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            sample = f.read(2048)

        if not sample:
            return ","

        try:
            sniffer = csv.Sniffer()
            dialect = sniffer.sniff(sample, delimiters=[",", "\t", ";", "|"])
            return dialect.delimiter
        except Exception:
            # Fallback to extension check or count
            if path.suffix.lower() in [".tsv", ".tab"]:
                return "\t"
            if sample.count("\t") > sample.count(","):
                return "\t"
            return ","


# Alias for naming consistency
GenericTabularExtractor = TabularExtractor
