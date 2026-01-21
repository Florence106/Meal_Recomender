import joblib
import numpy as np
from pathlib import Path
from .models import Resident, Meal

# Load the trained model
MODEL_PATH = Path(__file__).resolve().parents[1] / "model" / "xgb_model.pkl"
model = joblib.load(MODEL_PATH)

def meal_to_features(resident: Resident, meal: Meal) -> list:
    """
    Convert a resident and a meal into the feature vector expected by the model.
    Adjust this function based on how your training features were structured.
    """
    return [
        resident.age,
        resident.weight_kg,
        resident.height_cm,
        resident.bmi or 0.0,
        1 if "diabetes" in (resident.conditions or []) else 0,
        1 if "hypertension" in (resident.conditions or []) else 0,
        1 if "nuts" in (resident.allergies or []) else 0,
        1 if (resident.texture or "").lower() == "pureed" else 0,
        meal.calories_kcal,
        meal.protein_g,
        meal.carbs_g,
        meal.fat_g,
        meal.sodium_mg,
    ]

def predict_score(resident: Resident, meal: Meal) -> float:
    """Return predicted probability that this meal is a good match for the resident"""
    features = np.array(meal_to_features(resident, meal)).reshape(1, -1)
    return model.predict_proba(features)[0][1]  # probability of "like"
