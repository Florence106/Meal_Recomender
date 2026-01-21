import pytest

# Update these imports to match your project package structure
import pytest
from src.models import Resident, Meal


@pytest.fixture
def resident_base():
    return Resident(
        resident_id="r1",
        name="Test Resident",
        age=70,
        sex="F",
        weight_kg=70.0,
        height_cm=165.0,
        allergies="nuts, milk",
        conditions="diabetes, hypertension",
        texture="regular",
        diet_type="none",
        cultural_prefs="",
        calorie_target_kcal=1800.0,
    )


@pytest.fixture
def meals_sample():
    return [
        Meal(
            meal_id="m_safe",
            name="Safe Meal",
            meal_type="lunch",
            tags="",
            allergens="",
            calories_kcal=500,
            protein_g=25,
            carbs_g=30,
            fat_g=15,
            sodium_mg=300,
        ),
        Meal(
            meal_id="m_nuts",
            name="Nut Meal",
            meal_type="lunch",
            tags="",
            allergens="nuts",
            calories_kcal=450,
            protein_g=20,
            carbs_g=35,
            fat_g=12,
            sodium_mg=250,
        ),
        Meal(
            meal_id="m_salty",
            name="Salty Meal",
            meal_type="lunch",
            tags="",
            allergens="",
            calories_kcal=400,
            protein_g=22,
            carbs_g=40,
            fat_g=10,
            sodium_mg=900,
        ),
        Meal(
            meal_id="m_highcarb",
            name="High Carb Meal",
            meal_type="lunch",
            tags="",
            allergens="",
            calories_kcal=420,
            protein_g=18,
            carbs_g=80,
            fat_g=8,
            sodium_mg=200,
        ),
    ]
