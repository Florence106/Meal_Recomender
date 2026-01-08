from typing import List, Optional
from pydantic import BaseModel, Field, validator
import math

class Resident(BaseModel):
    resident_id: str
    name: str
    age: int
    sex: str
    weight_kg: float
    height_cm: float
    bmi: Optional[float] = None
    calorie_target_kcal: Optional[float] = None
    protein_target_g: Optional[float] = None

    allergies: List[str] = Field(default_factory=list)
    conditions: List[str] = Field(default_factory=list)
    texture: str = "regular"
    diet_type: str = "none"
    cultural_prefs: Optional[str] = None

    @validator("allergies", "conditions", pre=True)
    def split_comma_separated(cls, v):
        if v is None:
            return []
        if isinstance(v, float) and math.isnan(v):
            return []
        if isinstance(v, list):
            return [str(item).strip().lower() for item in v if str(item).strip()]
        if isinstance(v, str):
            return [item.strip().lower() for item in v.split(",") if item.strip()]
        return []

    @validator("cultural_prefs", "sex", "texture", "diet_type", pre=True)
    def coerce_strings(cls, v):
        if v is None:
            return None
        if isinstance(v, float) and math.isnan(v):
            return None
        return str(v).strip() if str(v).strip() else None

    @validator("bmi", always=True)
    def compute_bmi_if_missing(cls, v, values):
        if v is not None:
            return v
        weight = values.get("weight_kg")
        height_cm = values.get("height_cm")
        if weight and height_cm:
            h = height_cm / 100
            return round(weight / (h * h), 1)
        return None

class Meal(BaseModel):
    meal_id: str
    name: str
    meal_type: str  # breakfast, lunch, dinner, snack

    tags: List[str] = Field(default_factory=list)
    allergens: List[str] = Field(default_factory=list)

    calories_kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    sodium_mg: float

    ml_score: Optional[float] = None  # Added for machine learning prediction scoring

    @validator("tags", "allergens", pre=True)
    def split_tags(cls, v):
        if v is None:
            return []
        if isinstance(v, float) and math.isnan(v):
            return []
        if isinstance(v, list):
            return [str(item).strip().lower() for item in v if str(item).strip()]
        if isinstance(v, str):
            return [item.strip().lower() for item in v.split(",") if item.strip()]
        return [str(v).strip().lower()] if str(v).strip() else []

class Interaction(BaseModel):
    interaction_id: str
    resident_id: str
    meal_id: str
    date: str  # can be converted to datetime if needed
    rating: Optional[int] = None  # 1–5 rating
    eaten_fraction: Optional[float] = None  # e.g. 0.8 if 80% eaten
    comments: Optional[str] = None
