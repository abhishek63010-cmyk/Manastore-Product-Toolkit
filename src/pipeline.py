from pathlib import Path
import pandas as pd

from src.extractors.supplier_data import extract_supplier_data
from src.mappers.mana_store_mapper import map_to_manastore
from src.mappers.product_refiner import refine_dataframe
from src.validators.product_validator import validate_products

def run_pipeline(input_file: Path, output_file: Path, report_file: Path):
    raw = extract_supplier_data(input_file)
    mapped = map_to_manastore(raw)
    refined = refine_dataframe(mapped)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.parent.mkdir(parents=True, exist_ok=True)

    findings = validate_products(refined)

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        raw.to_excel(writer, sheet_name="Raw_Source", index=False)
        mapped.to_excel(writer, sheet_name="Mapped_Source", index=False)
        refined.to_excel(writer, sheet_name="Refinement_Review", index=False)
        findings.to_excel(writer, sheet_name="Validation_Findings", index=False)

    findings.to_excel(report_file, index=False)
    return refined, findings
