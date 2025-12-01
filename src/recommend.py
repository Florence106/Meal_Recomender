from .data_loading import load_residents, load_meals
from .rules import apply_basic_rules

def demo_rules(meal_type="lunch"):
    residents = load_residents()
    meals = load_meals()

    if not residents:
        print("No residents found. Add data to data/raw/residents.csv")
        return
    if not meals:
        print("No meals found. Add data to data/raw/meals.csv")
        return

    r = residents[0]
    print(f"Resident: {r.name} | diet={r.diet_type} | allergies={r.allergies} | conditions={r.conditions}")
    candidates = apply_basic_rules(r, meals, meal_type=meal_type, meal_fraction=0.35)
    print(f"\nRule-based candidates for {meal_type}:")
    for m in candidates[:10]:
        print(f"- {m.name} | {m.calories_kcal} kcal | carbs={m.carbs_g}g | Na={m.sodium_mg}mg")

if __name__ == "__main__":
    demo_rules("lunch")
