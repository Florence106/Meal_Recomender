from typing import List, Optional
from .models import Resident, Meal
import math

# --- UK nutrition guidelines for older adults (simplified) ---
UK_GUIDELINES = {
    # --- Macronutrients ---
    "max_calories_per_meal": 700,    # Based on ~3 meals/day + snacks
    "min_protein_per_meal": 20,      # Older adults need ~1.0–1.2 g/kg/day

    # --- Sodium and hypertension ---
    "max_sodium_per_meal": 600,      # per meal recommendation (SACN / NHS)
    "max_sodium_hypertension": 400,  # stricter limit for hypertension

    # --- Diabetes (very simplified clinical rule of thumb) ---
    "max_carbs_diabetes": 45,        # per meal recommended CHO target
    "max_sugars_diabetes": 10,       # simplified sugar cap (if you add sugar data later)

    # --- General fat guidance ---
    "max_saturated_fat_g": 10,

    # --- Fibre (minimum recommended per meal) ---
    "min_fibre_per_meal": 6,         # ~18–24 g/day
}

# --- Individual filters ---

def filter_meal_type(meals: List[Meal], meal_type: Optional[str]) -> List[Meal]:
    """
    IMPORTANT DIVERSITY FIX:
    If meal_type is requested but your dataset has few/no meals with that meal_type,
    we DO NOT want to return empty and fall into repetitive fallback.
    Instead we "soft filter": try meal_type; if none match, return original meals.
    """
    if not meal_type:
        return meals
    mt = meal_type.lower()
    filtered = [m for m in meals if (m.meal_type or "").lower() == mt]
    return filtered if filtered else meals


def filter_allergies(resident: Resident, meals: List[Meal]) -> List[Meal]:
    if not resident.allergies:
        return meals
    blocked = set(a.lower() for a in resident.allergies)
    safe = []
    for m in meals:
        meal_allergens = set(a.lower() for a in m.allergens)
        if blocked & meal_allergens:
            continue
        safe.append(m)
    return safe


def filter_diet_type(resident: Resident, meals: List[Meal]) -> List[Meal]:
    diet = (resident.diet_type or "none").lower()
    if diet == "none":
        return meals

    def ok(m: Meal) -> bool:
        tags = {t.lower() for t in m.tags}
        if diet == "vegetarian":
            return "vegetarian" in tags or "vegan" in tags
        if diet == "vegan":
            return "vegan" in tags
        if diet == "halal":
            return "halal" in tags
        if diet == "kosher":
            return "kosher" in tags
        return True

    return [m for m in meals if ok(m)]


def filter_conditions(resident: Resident, meals: List[Meal]) -> List[Meal]:
    """
    Apply simple condition-based thresholds *using UK_GUIDELINES*:

      - diabetes: per-meal carbohydrate cap
      - hypertension: stricter sodium cap
    """
    conds = {c.lower() for c in (resident.conditions or [])}
    filtered = meals

    if "diabetes" in conds:
        max_carbs = UK_GUIDELINES["max_carbs_diabetes"]
        filtered = [
            m for m in filtered
            if (m.carbs_g is None) or (m.carbs_g <= max_carbs)
        ]

    if "hypertension" in conds:
        max_na = UK_GUIDELINES["max_sodium_hypertension"]
        filtered = [
            m for m in filtered
            if (m.sodium_mg is None) or (m.sodium_mg <= max_na)
        ]

    return filtered


def filter_calories(
    resident: Resident,
    meals: List[Meal],
    daily_fraction: float = 0.35,
) -> List[Meal]:
    if resident.calorie_target_kcal:
        cap = resident.calorie_target_kcal * daily_fraction
    else:
        cap = UK_GUIDELINES["max_calories_per_meal"]

    safe = []
    for m in meals:
        if (m.calories_kcal is None) or (m.calories_kcal <= cap):
            safe.append(m)
    return safe


def _mifflin_st_jeor_kcal(res: Resident) -> float:
    if not res.weight_kg or not res.height_cm or not res.age:
        return 1800.0
    if (str(res.sex).upper() == "M"):
        bmr = 10 * res.weight_kg + 6.25 * res.height_cm - 5 * res.age + 5
    else:
        bmr = 10 * res.weight_kg + 6.25 * res.height_cm - 5 * res.age - 161
    return bmr * 1.2


def apply_basic_rules(
    resident: Resident,
    meals: List[Meal],
    meal_type: Optional[str] = None,
    meal_fraction: float = 0.35,
    strict: bool = True,
    explain: bool = False,
):
    candidates = list(meals)

    # 1) meal type (SOFT)
    candidates = filter_meal_type(candidates, meal_type)

    # 2) allergies
    candidates = filter_allergies(resident, candidates)

    # 3) diet
    candidates = filter_diet_type(resident, candidates)

    # 4) conditions
    candidates = filter_conditions(resident, candidates)

    # 5) calories cap
    candidates = filter_calories(resident, candidates, daily_fraction=meal_fraction)

    # relaxation if nothing remains
    if not candidates and strict:
        relaxed = list(meals)
        relaxed = filter_meal_type(relaxed, meal_type)
        relaxed = filter_allergies(resident, relaxed)
        relaxed = filter_diet_type(resident, relaxed)
        relaxed = filter_conditions(resident, relaxed)
        candidates = relaxed

    if not candidates:
        candidates = sorted(
            meals,
            key=lambda m: (m.sodium_mg or 0, -(m.protein_g or 0), m.calories_kcal or 0),
        )[:20]

    if not explain:
        return candidates

    explanations: dict[str, str] = {}

    conds = {c.strip().lower() for c in (resident.conditions or []) if c.strip()}
    daily_kcal = resident.calorie_target_kcal or _mifflin_st_jeor_kcal(resident)

    if resident.calorie_target_kcal:
        cap = daily_kcal * meal_fraction
        cap_is_personal = True
    else:
        cap = UK_GUIDELINES["max_calories_per_meal"]
        cap_is_personal = False

    for m in candidates:
        parts = []

        if meal_type and (m.meal_type or "").lower() == meal_type.lower():
            parts.append(f"Appropriate for the selected meal type ({meal_type}).")

        resident_all = {a.strip().lower() for a in (resident.allergies or []) if a.strip()}
        meal_all = {a.strip().lower() for a in (m.allergens or []) if a.strip()}

        if resident_all:
            if resident_all & meal_all:
                parts.append("⚠ Contains one or more allergens you are sensitive to (should normally be filtered out).")
            else:
                parts.append("Free from your recorded allergens.")

        diet = (resident.diet_type or "").lower()
        if diet == "vegetarian":
            parts.append("Meets vegetarian dietary requirements.")
        elif diet == "vegan":
            parts.append("Meets vegan dietary requirements.")
        elif diet in {"halal", "kosher"}:
            parts.append(f"Marked as suitable for a {diet.capitalize()} diet.")

        if m.sodium_mg is not None:
            if "hypertension" in conds:
                max_na_ht = UK_GUIDELINES["max_sodium_hypertension"]
                if m.sodium_mg <= max_na_ht:
                    parts.append(f"Sodium ({m.sodium_mg:.0f} mg) is within hypertension guidance (≤ {max_na_ht} mg).")
                else:
                    parts.append(f"Sodium ({m.sodium_mg:.0f} mg) is above hypertension guidance (~{max_na_ht} mg).")
            else:
                max_na = UK_GUIDELINES["max_sodium_per_meal"]
                if m.sodium_mg <= max_na:
                    parts.append(f"Sodium ({m.sodium_mg:.0f} mg) is within UK per-meal guideline (≤ {max_na} mg).")
                else:
                    parts.append(f"Sodium ({m.sodium_mg:.0f} mg) is above UK per-meal guideline (~{max_na} mg).")

        if "diabetes" in conds and m.carbs_g is not None:
            max_carbs = UK_GUIDELINES["max_carbs_diabetes"]
            if m.carbs_g <= max_carbs:
                parts.append(f"Carbs ({m.carbs_g:.0f} g) fits diabetes guidance (≤ {max_carbs} g).")
            else:
                parts.append(f"Carbs ({m.carbs_g:.0f} g) exceed diabetes guidance (~{max_carbs} g).")

        if m.protein_g is not None:
            min_prot = UK_GUIDELINES["min_protein_per_meal"]
            if m.protein_g >= min_prot:
                parts.append(f"Protein is good ({m.protein_g:.1f} g ≥ {min_prot} g guideline).")
            else:
                parts.append(f"Lower protein (~{m.protein_g:.1f} g, guideline ≈ {min_prot} g).")

        if cap is not None and m.calories_kcal is not None:
            if m.calories_kcal <= cap:
                if cap_is_personal:
                    parts.append(f"Calories ({m.calories_kcal:.0f}) fit your per-meal target (~{cap:.0f}).")
                else:
                    parts.append(f"Calories ({m.calories_kcal:.0f}) are within typical per-meal guideline (≤ {cap:.0f}).")
            else:
                if cap_is_personal:
                    parts.append(f"Calories ({m.calories_kcal:.0f}) exceed your target (~{cap:.0f}) but included after relaxing.")
                else:
                    parts.append(f"Calories ({m.calories_kcal:.0f}) exceed typical per-meal guideline (~{cap:.0f}).")

        if not parts:
            parts.append("Suitable based on your overall nutritional profile and recorded preferences.")

        explanations[m.meal_id] = " ".join(parts)

    return candidates, explanations
