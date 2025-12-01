from pathlib import Path

# Root of the project (meal_recommender/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"

RESIDENTS_CSV = DATA_RAW / "residents.csv"
MEALS_CSV = DATA_RAW / "meals.csv"
INTERACTIONS_CSV = DATA_RAW / "interactions.csv"
