from pathlib import Path
import pandas as pd

from .config import DATA_RAW, MEALS_CSV

EXCEL_PATH = DATA_RAW / "cofid.xlsx"
SHEET_PROX = "1.3 Proximates"
SHEET_INORG = "1.4 Inorganics"

def load_proximates(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=SHEET_PROX)
    # Keep only the columns we need
    keep = [
        "Food Code", "Food Name", "Group",
        "Energy (kcal) (kcal)", "Protein (g)", "Carbohydrate (g)", "Fat (g)"
    ]
    df = df[keep].copy()
    df.rename(columns={
        "Food Code": "food_code",
        "Food Name": "name",
        "Group": "group",
        "Energy (kcal) (kcal)": "calories_kcal",
        "Protein (g)": "protein_g",
        "Carbohydrate (g)": "carbs_g",
        "Fat (g)": "fat_g",
    }, inplace=True)
    return df

def load_inorganics(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=SHEET_INORG)
    # In this workbook, the first blank column ' ' is actually the food code
    # We'll keep that plus sodium and name (for sanity).
    if " " in df.columns:
        df.rename(columns={" ": "Food Code"}, inplace=True)
    keep = ["Food Code", "Food Name", "Sodium (mg)"]
    df = df[keep].copy()
    df.rename(columns={
        "Food Code": "food_code",
        "Food Name": "name_inorg",
        "Sodium (mg)": "sodium_mg",
    }, inplace=True)
    return df

def coerce_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    # Convert text like "Tr", "N", "NaN" etc. to numbers (coerce to NaN), then fill NaN with 0 for safety.
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def main():
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(f"Could not find {EXCEL_PATH}. Make sure cofid.xlsx is in data/raw/")

    prox = load_proximates(EXCEL_PATH)
    inorg = load_inorganics(EXCEL_PATH)

    # Coerce numeric columns
    prox = coerce_numeric(prox, ["calories_kcal", "protein_g", "carbs_g", "fat_g"])
    inorg = coerce_numeric(inorg, ["sodium_mg"])

    # Merge on food_code (inner join keeps rows present in both sheets)
    merged = prox.merge(inorg[["food_code", "sodium_mg"]], on="food_code", how="inner")

    # Basic cleaning
    merged["name"] = merged["name"].astype(str).str.strip()
    merged = merged[merged["name"].notna() & (merged["name"] != "")]

    # Drop rows with missing calories (rare but we’ll be strict for recommendations)
    merged = merged[merged["calories_kcal"].notna()]

    # Build our canonical meals.csv columns
    out = pd.DataFrame({
        "meal_id": merged["food_code"].astype(str).radd("cofid_"),
        "name": merged["name"],
        # CoFID is foods/recipes, not tied to a meal slot; we’ll default to 'lunch' for now.
        "meal_type": "lunch",
        "tags": "",        # placeholder; can map 'Group' to tags later
        "allergens": "",   # CoFID doesn’t list allergens
        "calories_kcal": merged["calories_kcal"].fillna(0),
        "protein_g": merged["protein_g"].fillna(0),
        "carbs_g": merged["carbs_g"].fillna(0),
        "fat_g": merged["fat_g"].fillna(0),
        "sodium_mg": merged["sodium_mg"].fillna(0),
    })

    # Write it to our canonical CSV
    out.to_csv(MEALS_CSV, index=False)
    print(f"Wrote {len(out)} meals to {MEALS_CSV}")

if __name__ == "__main__":
    main()
