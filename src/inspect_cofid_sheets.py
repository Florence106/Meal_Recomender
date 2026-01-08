import pandas as pd
from pathlib import Path

excel_path = Path(__file__).resolve().parent.parent / "data" / "raw" / "cofid.xlsx"

def show(sheet):
    df = pd.read_excel(excel_path, sheet_name=sheet)
    print(f"\n=== {sheet} ===")
    print("Columns:", list(df.columns))
    print(df.head(5).to_string(index=False))

if __name__ == "__main__":
    show("1.3 Proximates")
    show("1.4 Inorganics")
