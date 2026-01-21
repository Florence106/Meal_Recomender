import math

# Update this import to match your project
from src.models import Resident, Meal


def test_resident_splits_comma_lists_and_lowercases():
    r = Resident(
        resident_id="r1",
        name="A",
        age=70,
        sex="F",
        weight_kg=70.0,
        height_cm=175.0,
        allergies=" Nuts,  MILK ",
        conditions="Diabetes,Hypertension",
    )
    assert r.allergies == ["nuts", "milk"]
    assert r.conditions == ["diabetes", "hypertension"]


def test_resident_handles_nan_in_lists():
    r = Resident(
        resident_id="r1",
        name="A",
        age=70,
        sex="F",
        weight_kg=70.0,
        height_cm=175.0,
        allergies=float("nan"),
        conditions=float("nan"),
    )
    assert r.allergies == []
    assert r.conditions == []


def test_resident_computes_bmi_when_missing():
    r = Resident(
        resident_id="r1",
        name="A",
        age=70,
        sex="F",
        weight_kg=80.0,
        height_cm=160.0,
        allergies="",
        conditions="",
    )
    # BMI = 80 / (1.6^2) = 31.25 -> 31.2 (1 dp)
    assert r.bmi == 31.2


def test_meal_splits_tags_and_allergens():
    m = Meal(
        meal_id="m1",
        name="Meal",
        meal_type="lunch",
        tags="Vegan, High Protein",
        allergens=" Nuts , Milk ",
        calories_kcal=500,
        protein_g=25,
        carbs_g=40,
        fat_g=10,
        sodium_mg=200,
    )
    assert m.tags == ["vegan", "high protein"]
    assert m.allergens == ["nuts", "milk"]
