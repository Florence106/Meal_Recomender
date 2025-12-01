from flask import Flask, render_template, request, redirect, url_for, flash
import pandas as pd
from uuid import uuid4
from pathlib import Path

from ..config import MEALS_CSV, RESIDENTS_CSV
from ..models import Resident, Meal
from ..rules import apply_basic_rules

from pathlib import Path
TEMPLATES = Path(__file__).resolve().parent / "templates"
STATIC    = Path(__file__).resolve().parent / "static"
app = Flask(__name__, template_folder=str(TEMPLATES), static_folder=str(STATIC))
app.secret_key = "dev-secret"


# ---------- utilities ----------
def count_csv_rows(path):
    try:
        return len(pd.read_csv(path).index)
    except Exception:
        return 0

def read_residents_df() -> pd.DataFrame:
    cols = [
        "resident_id","name","age","sex","weight_kg","height_cm","bmi",
        "calorie_target_kcal","protein_target_g","allergies","conditions",
        "texture","diet_type","cultural_prefs"
    ]
    try:
        df = pd.read_csv(RESIDENTS_CSV)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        df = pd.DataFrame(columns=cols)
        df.to_csv(RESIDENTS_CSV, index=False)
    return df

def read_meals_df() -> pd.DataFrame:
    try:
        return pd.read_csv(MEALS_CSV)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame(columns=[
            "meal_id","name","meal_type","tags","allergens",
            "calories_kcal","protein_g","carbs_g","fat_g","sodium_mg"
        ])


def rows_to_residents(df: pd.DataFrame) -> list[Resident]:
    # Ensure expected columns exist and fill NaNs with safe defaults for parsing
    cols_defaults = {
        "sex": "Other",
        "texture": "regular",
        "diet_type": "none",
        "allergies": "",
        "conditions": "",
        "cultural_prefs": None,
        "bmi": None,
        "calorie_target_kcal": None,
        "protein_target_g": None,
    }
    df = df.copy()
    for col, default in cols_defaults.items():
        if col not in df.columns:
            df[col] = default
    df[["allergies", "conditions"]] = df[["allergies", "conditions"]].fillna("")

    def parse_list(v):
        if isinstance(v, list):
            return [str(x).strip().lower() for x in v if str(x).strip()]
        s = str(v).strip()
        if not s or s.lower() == "nan":
            return []
        return [x.strip().lower() for x in s.split(",") if x.strip()]

    out = []
    for _, r in df.iterrows():
        out.append(Resident(
            resident_id=str(r["resident_id"]),
            name=str(r["name"]),
            age=int(r["age"]),
            sex=(str(r.get("sex","Other")).strip() or "Other"),
            weight_kg=float(r["weight_kg"]),
            height_cm=float(r["height_cm"]),
            bmi=(None if pd.isna(r.get("bmi", None)) else r.get("bmi", None)),
            calorie_target_kcal=(None if pd.isna(r.get("calorie_target_kcal", None)) else r.get("calorie_target_kcal", None)),
            protein_target_g=(None if pd.isna(r.get("protein_target_g", None)) else r.get("protein_target_g", None)),
            allergies=parse_list(r.get("allergies","")),
            conditions=parse_list(r.get("conditions","")),
            texture=(str(r.get("texture","regular")).strip() or "regular"),
            diet_type=(str(r.get("diet_type","none")).strip() or "none"),
            cultural_prefs=(None if pd.isna(r.get("cultural_prefs", None)) else (str(r.get("cultural_prefs","")).strip() or None)),
        ))
    return out

def rows_to_meals(df: pd.DataFrame) -> list[Meal]:
    # Ensure required columns exist and fill NaNs with safe defaults
    required_cols = [
        "meal_id","name","meal_type","tags","allergens",
        "calories_kcal","protein_g","carbs_g","fat_g","sodium_mg"
    ]
    df = df.copy()
    for c in required_cols:
        if c not in df.columns:
            df[c] = "" if c in {"name","meal_type","tags","allergens"} else 0

    # Normalize nullable text fields
    df["tags"] = df["tags"].fillna("")
    df["allergens"] = df["allergens"].fillna("")
    df["meal_type"] = df["meal_type"].fillna("lunch")
    df["name"] = df["name"].astype(str)

    # Coerce numerics
    for c in ["calories_kcal","protein_g","carbs_g","fat_g","sodium_mg"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    out: list[Meal] = []
    for _, m in df.iterrows():
        tags = m["tags"]
        allergens = m["allergens"]
        # Pydantic Meal already handles string->list, but guard anyway
        tags = "" if isinstance(tags, float) and pd.isna(tags) else tags
        allergens = "" if isinstance(allergens, float) and pd.isna(allergens) else allergens
        out.append(Meal(
            meal_id=str(m["meal_id"]),
            name=str(m["name"]),
            meal_type=str(m["meal_type"]),
            tags=tags,
            allergens=allergens,
            calories_kcal=float(m["calories_kcal"]),
            protein_g=float(m["protein_g"]),
            carbs_g=float(m["carbs_g"]),
            fat_g=float(m["fat_g"]),
            sodium_mg=float(m["sodium_mg"]),
        ))
    return out

# ---------- routes ----------
@app.route("/")
def home():
    meals_count = count_csv_rows(MEALS_CSV)
    residents_count = count_csv_rows(RESIDENTS_CSV)
    return render_template("index.html", meals_count=meals_count, residents_count=residents_count)

@app.route("/residents")
def residents_list():
    df = read_residents_df().copy()

    # ---- Query params
    q       = (request.args.get("q") or "").strip().lower()
    diet    = (request.args.get("diet") or "").strip().lower()
    has_all = (request.args.get("allergy") or "").strip().lower()
    has_con = (request.args.get("condition") or "").strip().lower()
    sort    = request.args.get("sort", "name")  # name|age|bmi
    order   = request.args.get("order", "asc")  # asc|desc
    limit_s = request.args.get("limit", "20")

    try:
        limit = max(5, min(int(limit_s), 200))
    except ValueError:
        limit = 20

    # ---- Coerce text cols
    for col in ["name","diet_type","allergies","conditions","cultural_prefs"]:
        if col not in df.columns: df[col] = ""
        df[col] = df[col].fillna("").astype(str)

    # ---- BMI compute
    def bmi_calc(row):
        try:
            w = float(row["weight_kg"]); h = float(row["height_cm"])/100
            return round(w/(h*h), 1) if w>0 and h>0 else None
        except Exception: return None
    df["bmi_val"] = df.apply(bmi_calc, axis=1)

    # ---- Filters
    if q:
        df = df[df["name"].str.lower().str.contains(q) | df["cultural_prefs"].str.lower().str.contains(q)]
    if diet: df = df[df["diet_type"].str.lower() == diet]
    if has_all: df = df[df["allergies"].str.lower().str.contains(has_all)]
    if has_con: df = df[df["conditions"].str.lower().str.contains(has_con)]

    # ---- Sort
    if sort == "bmi":
        df["_s"] = df["bmi_val"].fillna(10_000); asc = (order=="asc")
        df = df.sort_values("_s", ascending=asc).drop(columns=["_s"])
    else:
        asc = (order=="asc")
        if sort not in {"name","age"}: sort = "name"
        df = df.sort_values(sort, ascending=asc, na_position="last")

    df = df.head(limit)

    # ---- Decorate rows
    def bmi_tag(v):
        if v is None:      return ("–",  "badge-soft")
        if v < 18.5:       return (f"{v} Under",  "badge-soft warning")
        if v < 25:         return (f"{v} Healthy","badge-soft success")
        if v < 30:         return (f"{v} Over",   "badge-soft warning")
        else:              return (f"{v} Obese",  "badge-soft")
    def initials(name):
        parts = str(name).strip().split()
        letters = (parts[0][:1] + (parts[1][:1] if len(parts)>1 else "")).upper()
        return letters or "R"

    records=[]
    for _, r in df.iterrows():
        allergies = [s.strip() for s in str(r["allergies"]).split(",") if s.strip()]
        conditions = [s.strip() for s in str(r["conditions"]).split(",") if s.strip()]

        bmi_text, bmi_class = bmi_tag(r["bmi_val"])
        status = "Active" if len(conditions)==0 else ("Active" if "stable" in ",".join(conditions).lower() else "Needs review")
        status_class = "success" if status=="Active" else "warning"

        records.append({
            "resident_id": r["resident_id"],
            "name": r["name"],
            "age": r.get("age",""),
            "diet_type": r.get("diet_type","none"),
            "bmi_text": bmi_text, "bmi_class": bmi_class,
            "allergies_list": allergies, "conditions_list": conditions,
            "initials": initials(r["name"]),
            "status": status, "status_class": status_class,
        })

    # simple stats for header (right side)
    total = count_csv_rows(RESIDENTS_CSV)
    active = sum(1 for rec in records if rec["status"]=="Active")

    return render_template(
        "residents_list.html",
        residents=records,
        total=total, active=active,
        q=q, diet=diet, has_all=has_all, has_con=has_con,
        sort=sort, order=order, limit=limit
    )

@app.route("/residents/new", methods=["GET", "POST"])
def residents_new():
    if request.method == "POST":
        f = request.form
        try:
            new_row = {
                "resident_id": str(uuid4())[:8],
                "name": f["name"].strip(),
                "age": int(f["age"]),
                "sex": f.get("sex","Other"),
                "weight_kg": float(f["weight_kg"]),
                "height_cm": float(f["height_cm"]),
                "bmi": "",
                "calorie_target_kcal": float(f["calorie_target_kcal"]) if f.get("calorie_target_kcal") else "",
                "protein_target_g": f.get("protein_target_g",""),
                "allergies": f.get("allergies",""),
                "conditions": f.get("conditions",""),
                "texture": f.get("texture","regular"),
                "diet_type": f.get("diet_type","none"),
                "cultural_prefs": f.get("cultural_prefs",""),
            }
        except (KeyError, ValueError):
            flash("Please fill the required fields correctly.", "danger")
            return redirect(url_for("residents_new"))

        df = read_residents_df()
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df.to_csv(RESIDENTS_CSV, index=False)
        flash("Resident registered successfully.", "success")
        return redirect(url_for("residents_list"))

    return render_template("residents_new.html")

@app.route("/residents/<resident_id>/edit", methods=["GET", "POST"])
def residents_edit(resident_id):
    df = read_residents_df()
    row = df.loc[df["resident_id"] == resident_id]
    if row.empty:
        flash("Resident not found.", "danger")
        return redirect(url_for("residents_list"))

    if request.method == "POST":
        f = request.form
        # update fields (keep simple and safe)
        idx = row.index[0]
        df.loc[idx, "name"] = f.get("name","").strip()
        df.loc[idx, "age"] = int(f.get("age") or 0)
        df.loc[idx, "sex"] = f.get("sex","Other")
        df.loc[idx, "weight_kg"] = float(f.get("weight_kg") or 0)
        df.loc[idx, "height_cm"] = float(f.get("height_cm") or 0)
        df.loc[idx, "calorie_target_kcal"] = f.get("calorie_target_kcal") or ""
        df.loc[idx, "protein_target_g"] = f.get("protein_target_g") or ""
        df.loc[idx, "allergies"] = f.get("allergies","")
        df.loc[idx, "conditions"] = f.get("conditions","")
        df.loc[idx, "texture"] = f.get("texture","regular")
        df.loc[idx, "diet_type"] = f.get("diet_type","none")
        df.loc[idx, "cultural_prefs"] = f.get("cultural_prefs","")
        df.to_csv(RESIDENTS_CSV, index=False)
        flash("Resident updated.", "success")
        return redirect(url_for("residents_list"))

    # GET → render form prefilled
    rec = row.iloc[0].to_dict()
    return render_template("residents_edit.html", r=rec)

@app.route("/residents/<resident_id>/delete", methods=["POST"])
def residents_delete(resident_id):
    df = read_residents_df()
    before = len(df)
    df = df[df["resident_id"] != resident_id]
    after = len(df)
    df.to_csv(RESIDENTS_CSV, index=False)
    if after < before:
        flash("Resident deleted.", "success")
    else:
        flash("Resident not found.", "warning")
    return redirect(url_for("residents_list"))

@app.route("/residents/<resident_id>/recommendations")
def resident_recommendations(resident_id):
    # ---- query params ----
    meal_type = request.args.get("meal_type")
    sort_key = request.args.get("sort", "name")       # name | calories_kcal | protein_g | sodium_mg
    view_mode = request.args.get("view", "cards")
    strict = request.args.get("strict", "1") != "0"   # "1" = strict, "0" = relaxed
    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        limit = 20

    # ---- load data ----
    rdf = read_residents_df()
    mdf = read_meals_df()
    if mdf.empty:
        flash("No meals loaded yet. Prepare data/raw/meals.csv first.", "warning")
        return redirect(url_for("home"))

    rrow = rdf.loc[rdf["resident_id"] == resident_id]
    if rrow.empty:
        flash("Resident not found.", "danger")
        return redirect(url_for("residents_list"))

    resident = rows_to_residents(rrow)[0]
    meals = rows_to_meals(mdf)

    # ---- apply rules WITH explanations ----
    result = apply_basic_rules(
        resident,
        meals,
        meal_type=meal_type,
        meal_fraction=0.35,
        strict=strict,
        explain=True,
    )
    candidates, reasons = result

    # ---- sort on server-side ----
    allowed = {"name", "calories_kcal", "protein_g", "sodium_mg"}
    if sort_key not in allowed:
        sort_key = "name"

    def sort_value(m: Meal):
        v = getattr(m, sort_key, None)
        if v is None:
            return "" if sort_key == "name" else 0
        if sort_key == "name" and isinstance(v, str):
            return v.lower()
        return v

    candidates_sorted = sorted(candidates, key=sort_value)[:max(1, min(limit, 200))]

    return render_template(
        "recommendations.html",
        resident=resident,
        meal_type=meal_type,
        meals=candidates_sorted,
        sort_key=sort_key,
        view_mode=view_mode,
        limit=limit,
        strict=strict,
        reasons=reasons,
    )

@app.route("/ping")
def ping():
    return "pong"

def create_app():
    return app

if __name__ == "__main__":
    # Print URL map so we can see all routes registered
    print("URL MAP:", app.url_map)
    app.run(debug=True)

