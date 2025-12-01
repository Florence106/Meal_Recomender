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
    if not meal_type:
        return meals
    mt = meal_type.lower()
    return [m for m in meals if m.meal_type.lower() == mt]


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

    These are safety filters before any personalisation.
    """
    conds = {c.lower() for c in (resident.conditions or [])}
    filtered = meals

    # Diabetes → apply carbohydrate cap
    if "diabetes" in conds:
        max_carbs = UK_GUIDELINES["max_carbs_diabetes"]
        filtered = [
            m for m in filtered
            if (m.carbs_g is None) or (m.carbs_g <= max_carbs)
        ]

    # Hypertension → stricter sodium cap
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
    """
    Cap per-meal calories.

    - If resident has a personal daily calorie target, use a fraction of that.
    - Otherwise, fall back to UK_GUIDELINES["max_calories_per_meal"].
    """
    if resident.calorie_target_kcal:
        cap = resident.calorie_target_kcal * daily_fraction
    else:
        cap = UK_GUIDELINES["max_calories_per_meal"]

    safe = []
    for m in meals:
        if (m.calories_kcal is None) or (m.calories_kcal <= cap):
            safe.append(m)
    return safe

# --- Orchestrator ---

def _mifflin_st_jeor_kcal(res: Resident) -> float:
    # Basic BMR estimate → sedentary multiplier
    if not res.weight_kg or not res.height_cm or not res.age:
        return 1800.0  # safe default
    if (str(res.sex).upper() == "M"):
        bmr = 10*res.weight_kg + 6.25*res.height_cm - 5*res.age + 5
    else:
        bmr = 10*res.weight_kg + 6.25*res.height_cm - 5*res.age - 161
    return bmr * 1.2  # sedentary factor

def apply_basic_rules(
    resident: Resident,
    meals: List[Meal],
    meal_type: Optional[str] = None,
    meal_fraction: float = 0.35,
    strict: bool = True,
    explain: bool = False,
):
    """
    Apply simple, explainable rule filters.

    - Filters by meal_type (if given)
    - Removes meals containing recorded allergens
    - Respects diet type where tags exist (vegetarian/vegan/halal/kosher)
    - Applies condition-based thresholds using UK_GUIDELINES:
        * diabetes     -> carbohydrate cap
        * hypertension -> sodium cap
    - Caps per-meal calories using either:
        * resident.calorie_target_kcal * meal_fraction, or
        * UK_GUIDELINES["max_calories_per_meal"] if no personal target

    If `explain=True`, also returns a dict mapping meal_id -> explanation string.
    """

    # ---------- start with full list ----------
    candidates = list(meals)

    # 1) meal type
    candidates = filter_meal_type(candidates, meal_type)

    # 2) allergies
    candidates = filter_allergies(resident, candidates)

    # 3) diet type (vegetarian / vegan / halal / kosher)
    candidates = filter_diet_type(resident, candidates)

    # 4) conditions (hypertension / diabetes) using UK_GUIDELINES
    candidates = filter_conditions(resident, candidates)

    # 5) calories cap (per-meal)
    candidates = filter_calories(resident, candidates, daily_fraction=meal_fraction)

    # ---------- basic relaxation if nothing remains ----------
    if not candidates and strict:
        # Relax calories, keep allergens/diet/conditions
        relaxed = list(meals)
        relaxed = filter_meal_type(relaxed, meal_type)
        relaxed = filter_allergies(resident, relaxed)
        relaxed = filter_diet_type(resident, relaxed)
        relaxed = filter_conditions(resident, relaxed)
        candidates = relaxed

    # If still nothing, very broad fallback: prefer low sodium, higher protein
    if not candidates:
        candidates = sorted(
            meals,
            key=lambda m: (m.sodium_mg or 0, -(m.protein_g or 0), m.calories_kcal or 0),
        )[:20]

    if not explain:
        return candidates

    # ---------- build explanations for each candidate ----------
    explanations: dict[str, str] = {}

    # helper values reused in explanations
    conds = {c.strip().lower() for c in (resident.conditions or []) if c.strip()}
    daily_kcal = resident.calorie_target_kcal or _mifflin_st_jeor_kcal(resident)

    # per-meal calorie cap for explanation
    if resident.calorie_target_kcal:
        cap = daily_kcal * meal_fraction
        cap_is_personal = True
    else:
        cap = UK_GUIDELINES["max_calories_per_meal"]
        cap_is_personal = False

    for m in candidates:
        parts = []

        # Meal type
        if meal_type and m.meal_type.lower() == meal_type.lower():
            parts.append(f"Appropriate for the selected meal type ({meal_type}).")

        # Allergies
        resident_all = {a.strip().lower() for a in (resident.allergies or []) if a.strip()}
        meal_all = {a.strip().lower() for a in (m.allergens or []) if a.strip()}

        if resident_all:
            if resident_all & meal_all:
                parts.append(
                    "⚠ Contains one or more allergens you are sensitive to (this should normally be filtered out)."
                )
            else:
                parts.append("Free from your recorded allergens.")

        # Diet type
        diet = (resident.diet_type or "").lower()
        if diet == "vegetarian":
            parts.append("Meets vegetarian dietary requirements.")
        elif diet == "vegan":
            parts.append("Meets vegan dietary requirements.")
        elif diet in {"halal", "kosher"}:
            parts.append(f"Marked as suitable for a {diet.capitalize()} diet.")

        # Hypertension / sodium (using UK guidelines)
        if m.sodium_mg is not None:
            if "hypertension" in conds:
                max_na_ht = UK_GUIDELINES["max_sodium_hypertension"]
                if m.sodium_mg <= max_na_ht:
                    parts.append(
                        f"Sodium level ({m.sodium_mg:.0f} mg) is within hypertension-friendly guidance (≤ {max_na_ht} mg per meal)."
                    )
                else:
                    parts.append(
                        f"Sodium level ({m.sodium_mg:.0f} mg) is above the stricter hypertension guideline (~{max_na_ht} mg per meal)."
                    )
            else:
                max_na = UK_GUIDELINES["max_sodium_per_meal"]
                if m.sodium_mg <= max_na:
                    parts.append(
                        f"Sodium content ({m.sodium_mg:.0f} mg) is within the UK per-meal guideline (≤ {max_na} mg)."
                    )
                else:
                    parts.append(
                        f"Sodium content ({m.sodium_mg:.0f} mg) is above the UK per-meal guideline (~{max_na} mg)."
                    )

        # Diabetes / carbohydrates (using UK guidelines)
        if "diabetes" in conds and m.carbs_g is not None:
            max_carbs = UK_GUIDELINES["max_carbs_diabetes"]
            if m.carbs_g <= max_carbs:
                parts.append(
                    f"Carbohydrate level ({m.carbs_g:.0f} g) fits within diabetes-friendly guidance (≤ {max_carbs} g per meal)."
                )
            else:
                parts.append(
                    f"Carbohydrates ({m.carbs_g:.0f} g) exceed typical diabetes guidance (~{max_carbs} g per meal)."
                )

        # Protein guideline
        if m.protein_g is not None:
            min_prot = UK_GUIDELINES["min_protein_per_meal"]
            if m.protein_g >= min_prot:
                parts.append(
                    f"Provides adequate protein for older adults (~{m.protein_g:.1f} g ≥ {min_prot} g guideline per meal)."
                )
            else:
                parts.append(
                    f"Lower protein meal (~{m.protein_g:.1f} g, guideline ≈ {min_prot} g per meal for older adults)."
                )

        # Calories
        if cap is not None and m.calories_kcal is not None:
            if m.calories_kcal <= cap:
                if cap_is_personal:
                    parts.append(
                        f"Energy content ({m.calories_kcal:.0f} kcal) fits your personalised per-meal target (~{cap:.0f} kcal)."
                    )
                else:
                    parts.append(
                        f"Energy content ({m.calories_kcal:.0f} kcal) is within a typical UK per-meal guideline (≤ {cap:.0f} kcal)."
                    )
            else:
                if cap_is_personal:
                    parts.append(
                        f"Energy content ({m.calories_kcal:.0f} kcal) is above your per-meal target (~{cap:.0f} kcal) but included after relaxing rules."
                    )
                else:
                    parts.append(
                        f"Energy content ({m.calories_kcal:.0f} kcal) is above the typical UK per-meal guideline (~{cap:.0f} kcal)."
                    )

        if not parts:
            parts.append("Suitable based on your overall nutritional profile and recorded preferences.")

        explanations[m.meal_id] = " ".join(parts)

    return candidates, explanations
