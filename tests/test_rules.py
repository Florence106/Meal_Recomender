# Update this import to match your project
from src.rules import apply_basic_rules, filter_allergies, filter_conditions, filter_calories
from src.models import Resident



def test_filter_allergies_excludes_meals(resident_base, meals_sample):
    # resident_base has "nuts" allergy
    filtered = filter_allergies(resident_base, meals_sample)
    ids = {m.meal_id for m in filtered}
    assert "m_nuts" not in ids
    assert "m_safe" in ids


def test_filter_conditions_diabetes_carbs_cap(meals_sample):
    r = Resident(
        resident_id="r2",
        name="Diabetic",
        age=75,
        sex="M",
        weight_kg=75.0,
        height_cm=170.0,
        allergies="",
        conditions="diabetes",
    )
    filtered = filter_conditions(r, meals_sample)
    ids = {m.meal_id for m in filtered}
    assert "m_highcarb" not in ids  # carbs 80 should be excluded by diabetes cap
    assert "m_safe" in ids


def test_filter_conditions_hypertension_sodium_cap(meals_sample):
    r = Resident(
        resident_id="r3",
        name="HTN",
        age=75,
        sex="M",
        weight_kg=75.0,
        height_cm=170.0,
        allergies="",
        conditions="hypertension",
    )
    filtered = filter_conditions(r, meals_sample)
    ids = {m.meal_id for m in filtered}
    assert "m_salty" not in ids
    assert "m_safe" in ids


def test_filter_calories_uses_personal_target_if_present(meals_sample):
    r = Resident(
        resident_id="r4",
        name="Target",
        age=70,
        sex="F",
        weight_kg=70.0,
        height_cm=165.0,
        allergies="",
        conditions="",
        calorie_target_kcal=1200.0,  # per-meal cap uses fraction below
    )

    # fraction 0.35 => cap = 420 kcal
    filtered = filter_calories(r, meals_sample, daily_fraction=0.35)
    ids = {m.meal_id for m in filtered}
    assert "m_safe" not in ids  # 500 > 420
    assert "m_salty" in ids     # 400 <= 420


def test_apply_basic_rules_orders_filters(resident_base, meals_sample):
    candidates, reasons = apply_basic_rules(
        resident_base,
        meals_sample,
        meal_type="lunch",
        meal_fraction=0.35,
        strict=True,
        explain=True,
    )

    ids = {m.meal_id for m in candidates}
    # Allergies, diabetes, hypertension should remove multiple unsafe meals
    assert "m_nuts" not in ids
    assert "m_highcarb" not in ids
    # "m_safe" should survive (fits carbs <=45 and sodium <=400 and calories cap)
    assert "m_safe" in ids

    # Explainability dictionary exists
    assert isinstance(reasons, dict)
    assert "m_safe" in reasons
