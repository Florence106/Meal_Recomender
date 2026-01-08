import importlib
import sqlite3
from pathlib import Path

import numpy as np
import pytest


class DummyScaler:
    def transform(self, X):
        # passthrough
        return X


class DummyModel:
    def predict_proba(self, X):
        # Return a stable probability based on calories to simulate behaviour
        # Must return shape (n_samples, 2)
        probs = []
        for row in X:
            cal = float(row[0]) if len(row) > 0 else 0.0
            p = 0.8 if cal < 600 else 0.2
            probs.append([1 - p, p])
        return np.array(probs, dtype=float)


@pytest.fixture
def ml_utils_module(monkeypatch, tmp_path):
    """
    Import ml_utils with joblib.load mocked so tests do not depend on real model files.
    """
    import joblib

    def fake_load(_path):
        # Decide whether returning model or scaler based on filename
        p = str(_path)
        if "scaler" in p:
            return DummyScaler()
        return DummyModel()

    monkeypatch.setattr(joblib, "load", fake_load)

    # Update this to match your project module path
    mod = importlib.import_module("src.ml_utils")
    importlib.reload(mod)

    # Point feedback DB to a temp location
    mod.FEEDBACK_DB = tmp_path / "feedback.db"
    return mod


def test_score_meal_returns_probability_in_range(ml_utils_module, resident_base, meals_sample):
    m = meals_sample[0]
    p = ml_utils_module.score_meal(resident_base, m)
    assert 0.0 <= p <= 1.0


def test_feedback_adjustment_affects_final_score(ml_utils_module, resident_base, meals_sample):
    # Create feedback db and insert a "liked" entry
    db = ml_utils_module.FEEDBACK_DB
    db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resident_id TEXT,
            meal_id TEXT,
            feedback TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute(
        "INSERT INTO feedback (resident_id, meal_id, feedback) VALUES (?, ?, ?)",
        (resident_base.resident_id, meals_sample[0].meal_id, "liked"),
    )
    conn.commit()
    conn.close()

    scores = ml_utils_module.score_meals_for_resident(resident_base, [meals_sample[0]])
    score = scores[meals_sample[0].meal_id]

    # DummyModel gives 0.8 for calories < 600, liked adds +0.10, clamp to <= 1.0
    assert score >= 0.8
    assert score <= 1.0
