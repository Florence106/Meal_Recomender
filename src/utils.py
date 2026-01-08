# src/utils.py
import joblib
import numpy as np
from pathlib import Path

MODEL_PATH = Path("model/xgb_model.pkl")
SCALER_PATH = Path("model/xgb_scaler.pkl")

xgb_model = joblib.load(MODEL_PATH)
xgb_scaler = joblib.load(SCALER_PATH)

feedback_memory = {}

def score_meal(resident, meal):
    features = [
        meal.calories_kcal,
        meal.protein_g,
        meal.carbs_g,
        getattr(meal, 'sugars_g', 10.0),
        resident.age,
        resident.calorie_target_kcal or 1800.0,
    ]
    arr = np.array(features).reshape(1, -1)
    arr_scaled = xgb_scaler.transform(arr)
    return float(xgb_model.predict_proba(arr_scaled)[0, 1])

def adjust_score_with_feedback(resident_id, meal_id, ml_score):
    if resident_id in feedback_memory:
        feedback = feedback_memory[resident_id].get(meal_id)
        if feedback == "liked":
            return ml_score + 0.1
        elif feedback == "disliked":
            return ml_score - 0.2
    return ml_score

def score_meals_for_resident(resident, meals, model=None, scaler=None):
    scores = {}
    for meal in meals:
        base_score = score_meal(resident, meal)
        final_score = adjust_score_with_feedback(resident.resident_id, meal.meal_id, base_score)
        scores[meal.meal_id] = round(min(max(final_score, 0), 1), 4)
    return scores
