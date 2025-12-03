import pandas as pd
from pathlib import Path

excel_path = Path(__file__).resolve().parent.parent / "data" / "raw" / "cofid.xlsx"

print(f"Opening: {excel_path}")

# List all sheet names in the workbook
xls = pd.ExcelFile(excel_path)
print("\nSheet names found:")
for name in xls.sheet_names:
    print("-", name)
