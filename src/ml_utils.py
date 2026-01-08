import joblib
import numpy as np
import sqlite3
from pathlib import Path

# Load model and scaler
MODEL_PATH = Path("model/xgb_model.pkl")
SCALER_PATH = Path("model/xgb_scaler.pkl")

xgb_model = joblib.load(MODEL_PATH)
xgb_scaler = joblib.load(SCALER_PATH)

# SQLite feedback database path
FEEDBACK_DB = Path("data/feedback.db")


def score_meal(resident, meal):
    features = [
        meal.calories_kcal,
        meal.protein_g,
        meal.carbs_g,
        getattr(meal, "sugars_g", 10.0),  # fallback
        resident.age,
        resident.calorie_target_kcal or 1800.0,
    ]
    arr = np.array(features).reshape(1, -1)
    arr_scaled = xgb_scaler.transform(arr)
    ml_score = float(xgb_model.predict_proba(arr_scaled)[0, 1])
    return ml_score


def get_feedback_adjustment(resident_id, meal_id):
    """
    Reads feedback from data/feedback.db table: feedback(resident_id, meal_id, feedback).
    Returns a small adjustment to encourage/discourage meals.
    """
    if not FEEDBACK_DB.exists():
        return 0.0

    try:
        conn = sqlite3.connect(FEEDBACK_DB)
        cur = conn.cursor()

        # Ensure table exists (safe)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resident_id TEXT,
                meal_id TEXT,
                feedback TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Get latest feedback for this resident+meal
        cur.execute(
            """
            SELECT feedback
            FROM feedback
            WHERE resident_id = ? AND meal_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (resident_id, meal_id),
        )
        row = cur.fetchone()
        conn.close()

        if row and row[0]:
            fb = str(row[0]).strip().lower()
            if fb == "liked":
                return 0.10
            if fb == "disliked":
                return -0.20

    except Exception:
        return 0.0

    return 0.0


def score_meals_for_resident(resident, meals, model=None, scaler=None):
    """
    Scores meals using ML model and then applies lightweight feedback adjustment.
    """
    scores = {}
    for meal in meals:
        base_score = score_meal(resident, meal)
        adjustment = get_feedback_adjustment(resident.resident_id, meal.meal_id)
        final_score = base_score + adjustment
        scores[meal.meal_id] = round(min(max(final_score, 0), 1), 4)
    return scores
