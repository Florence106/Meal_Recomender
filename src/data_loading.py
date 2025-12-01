from typing import List
import pandas as pd

from .config import RESIDENTS_CSV, MEALS_CSV, INTERACTIONS_CSV
from .models import Resident, Meal, Interaction


def load_residents() -> List[Resident]:
    """Load residents from CSV into a list of Resident objects."""
    try:
        df = pd.read_csv(RESIDENTS_CSV)
    except FileNotFoundError:
        print(f"Residents file not found at {RESIDENTS_CSV}")
        return []
    
    except EmptyDataError:
        print(f"Residents file is empty at {RESIDENTS_CSV}")
        return []
    
    residents: List[Resident] = []

    for _, row in df.iterrows():
        residents.append(
            Resident(
                resident_id=str(row["resident_id"]),
                name=row["name"],
                age=int(row["age"]),
                sex=row.get("sex", "Other"),
                weight_kg=float(row["weight_kg"]),
                height_cm=float(row["height_cm"]),
                bmi=row.get("bmi", None),
                calorie_target_kcal=row.get("calorie_target_kcal", None),
                protein_target_g=row.get("protein_target_g", None),
                allergies=row.get("allergies", ""),
                conditions=row.get("conditions", ""),
                texture=row.get("texture", "regular"),
                diet_type=row.get("diet_type", "none"),
                cultural_prefs=row.get("cultural_prefs", None),
            )
        )
    return residents



def load_meals() -> List[Meal]:
    """Load meals from CSV into a list of Meal objects."""
    try:
        df = pd.read_csv(MEALS_CSV)
    except FileNotFoundError:
        print(f"Meals file not found at {MEALS_CSV}")
        return []
    except pd.errors.EmptyDataError:
        print(f"Meals file is empty at {MEALS_CSV}")
        return []

    meals: List[Meal] = []
    for _, row in df.iterrows():
        tags = row.get("tags", "")
        allergens = row.get("allergens", "")
        # Normalize NaN -> ""
        if pd.isna(tags): tags = ""
        if pd.isna(allergens): allergens = ""
        meals.append(
            Meal(
                meal_id=str(row["meal_id"]),
                name=row["name"],
                meal_type=row["meal_type"],
                tags=tags,
                allergens=allergens,
                calories_kcal=float(row["calories_kcal"]),
                protein_g=float(row["protein_g"]),
                carbs_g=float(row["carbs_g"]),
                fat_g=float(row["fat_g"]),
                sodium_mg=float(row["sodium_mg"]),
            )
        )
    return meals



def load_interactions() -> List[Interaction]:
    """Load historical interactions (ratings, eaten fraction) from CSV."""
    try:
        df = pd.read_csv(INTERACTIONS_CSV)
    except FileNotFoundError:
        print(f"Interactions file not found at {INTERACTIONS_CSV}")
        return []

    interactions: List[Interaction] = []

    for _, row in df.iterrows():
        interactions.append(
            Interaction(
                interaction_id=str(row["interaction_id"]),
                resident_id=str(row["resident_id"]),
                meal_id=str(row["meal_id"]),
                date=str(row["date"]),
                rating=row.get("rating", None),
                eaten_fraction=row.get("eaten_fraction", None),
                comments=row.get("comments", None),
            )
        )
    return interactions
