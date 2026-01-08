import pandas as pd
from .config import RESIDENTS_CSV

def main():
    df = pd.DataFrame([{
        "resident_id": "r_demo",
        "name": "Demo Resident",
        "age": 80,
        "sex": "F",
        "weight_kg": 60,
        "height_cm": 160,
        "bmi": "",
        "calorie_target_kcal": 1800,
        "protein_target_g": "",
        "allergies": "nuts",
        "conditions": "hypertension",
        "texture": "regular",
        "diet_type": "none",
        "cultural_prefs": "british",
    }])
    # If you want headers only (no row), change to: df.iloc[0:0].to_csv(...)
    df.to_csv(RESIDENTS_CSV, index=False)
    print(f"Wrote headers (and one demo row) to {RESIDENTS_CSV}")

if __name__ == "__main__":
    main()
