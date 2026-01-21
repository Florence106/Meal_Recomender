from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
import os
from datetime import timedelta
from datetime import timezone
import pandas as pd
from uuid import uuid4
from pathlib import Path
import numpy as np
import sqlite3
from typing import Optional
from datetime import datetime, date, timedelta
import io
import hashlib  # ✅ NEW: for stable deterministic seeds

from flask import session
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash


from .pdf_utils import build_daily_plan_pdf

from ..config import MEALS_CSV, RESIDENTS_CSV
from ..models import Resident, Meal
from ..rules import apply_basic_rules
from ..ml_utils import score_meals_for_resident, xgb_model, xgb_scaler

TEMPLATES = Path(__file__).resolve().parent / "templates"
STATIC = Path(__file__).resolve().parent / "static"
app = Flask(__name__, template_folder=str(TEMPLATES), static_folder=str(STATIC))
app.secret_key = "dev-secret"
import os
STAFF_REGISTRATION_CODE = os.getenv("STAFF_REGISTRATION_CODE", "DEV_CODE")

# Use env var in real deployment; keep fallback for local dev
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,      # JS can't read session cookie
    SESSION_COOKIE_SAMESITE="Lax",     # protects against CSRF in most cases
    SESSION_COOKIE_SECURE=False,       # set True when using HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),  # default session duration
)


# -----------------------------
# Daily plan DB (SQLite)
# -----------------------------
DAILY_PLAN_DB = Path("data/daily_plan.db")


def _ensure_daily_plan_db():
    DAILY_PLAN_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DAILY_PLAN_DB)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_plans (
            plan_id TEXT PRIMARY KEY,
            plan_date TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            strict INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'draft',
            fulfilled_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_plan_items (
            item_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            plan_date TEXT NOT NULL,
            resident_id TEXT NOT NULL,
            meal_type TEXT NOT NULL,
            meal_id TEXT,
            ml_score REAL,
            reason TEXT,
            override_meal_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(plan_id) REFERENCES daily_plans(plan_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_plan_item_feedback (
            feedback_id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            plan_date TEXT NOT NULL,
            resident_id TEXT NOT NULL,
            meal_type TEXT NOT NULL,
            meal_id TEXT,
            feedback TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(item_id) REFERENCES daily_plan_items(item_id),
            FOREIGN KEY(plan_id) REFERENCES daily_plans(plan_id)
        )
    """)

    conn.commit()
    conn.close()


def _daily_db():
    _ensure_daily_plan_db()
    return sqlite3.connect(DAILY_PLAN_DB)


def _parse_date_yyyy_mm_dd(s: str) -> str:
    s = (s or "").strip()
    dt = datetime.strptime(s, "%Y-%m-%d").date()
    return dt.isoformat()


# -----------------------------
# Auth DB (SQLite) - Staff users
# -----------------------------
AUTH_DB = Path("data/auth.db")

def _ensure_auth_db():
    AUTH_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUTH_DB)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS staff_users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def _auth_db():
    _ensure_auth_db()
    return sqlite3.connect(AUTH_DB)


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            # send user back to the page they wanted after login
            return redirect(url_for("login", next=request.url))
        return view_func(*args, **kwargs)
    return wrapper

# ---------- staff user management ----------

def ensure_default_user():
    """
    Creates a default staff user if none exists.
    Change the password after first login.
    """
    conn = _auth_db()
    cur = conn.cursor()

    # Check if any user exists
    cur.execute("SELECT COUNT(*) FROM staff_users")
    count = cur.fetchone()[0]

    if count == 0:
        username = "staff"
        password = "staff123"  # CHANGE after first use
        cur.execute(
            "INSERT INTO staff_users (user_id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (str(uuid4()), username, generate_password_hash(password), datetime.utcnow().isoformat())
        )
        conn.commit()

    conn.close()


# ---------- utilities ----------

def stable_seed(*parts, mod: int = 2**32) -> int:
    """
    Deterministic seed derived from arbitrary values. Stable across runs/machines.
    (Replaces Python's built-in hash(), which may vary between runs.)
    """
    s = "|".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha256(s.encode("utf-8")).digest()
    # Use first 8 bytes (64-bit) then mod to fit seed range
    return int.from_bytes(digest[:8], "big") % mod


def count_csv_rows(path):
    try:
        return len(pd.read_csv(path).index)
    except Exception:
        return 0


def read_residents_df() -> pd.DataFrame:
    cols = [
        "resident_id", "name", "age", "sex", "weight_kg", "height_cm", "bmi",
        "calorie_target_kcal", "protein_target_g", "allergies", "conditions",
        "texture", "diet_type", "cultural_prefs"
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
            "meal_id", "name", "meal_type", "tags", "allergens",
            "calories_kcal", "protein_g", "carbs_g", "fat_g", "sodium_mg"
        ])


def rows_to_residents(df: pd.DataFrame) -> list[Resident]:
    return [Resident(**r.dropna().to_dict()) for _, r in df.iterrows()]


def rows_to_meals(df: pd.DataFrame) -> list[Meal]:
    return [Meal(**r.dropna().to_dict()) for _, r in df.iterrows()]


# -----------------------------
# Daily plan generation helpers
# -----------------------------

MEAL_TYPES_FOR_DAY = ["breakfast", "lunch", "dinner", "snack"]


def _get_or_create_daily_plan(plan_date: str, strict: bool, allow_regenerate: bool) -> tuple[str, bool, bool]:
    """
    Returns (plan_id, created_new, cleared_items)

    - If plan exists and draft:
        - regenerate False => reuse existing plan (created_new=False, cleared_items=False)
        - regenerate True  => delete existing items + feedback (created_new=False, cleared_items=True)

    - If plan exists and fulfilled => block by default (keeps your current rule)
    - If plan does not exist => create it (created_new=True, cleared_items=True)
    """
    conn = _daily_db()
    cur = conn.cursor()

    cur.execute("SELECT plan_id, status FROM daily_plans WHERE plan_date = ?", (plan_date,))
    row = cur.fetchone()

    if row:
        plan_id, status = row[0], row[1]
        if status == "fulfilled":
            conn.close()
            raise ValueError(
                f"A daily plan for {plan_date} is already fulfilled."
            )

        if not allow_regenerate:
            conn.close()
            return plan_id, False, False

        cur.execute("DELETE FROM daily_plan_item_feedback WHERE plan_id = ?", (plan_id,))
        cur.execute("DELETE FROM daily_plan_items WHERE plan_id = ?", (plan_id,))
        cur.execute(
            "UPDATE daily_plans SET strict = ?, created_at = ? WHERE plan_id = ?",
            (1 if strict else 0, datetime.utcnow().isoformat(), plan_id),
        )
        conn.commit()
        conn.close()
        return plan_id, False, True

    plan_id = str(uuid4())
    cur.execute("""
        INSERT INTO daily_plans (plan_id, plan_date, created_at, strict, status)
        VALUES (?, ?, ?, ?, 'draft')
    """, (plan_id, plan_date, datetime.utcnow().isoformat(), 1 if strict else 0))
    conn.commit()
    conn.close()
    return plan_id, True, True


def _recent_meal_ids_for_resident(resident_id: str, days_back: int = 7) -> set[str]:
    """
    Return meal_ids used recently for a resident over the last N days.
    This helps rotate and avoid repeating the same meals day-after-day.
    """
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    conn = _daily_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(override_meal_id, meal_id)
        FROM daily_plan_items
        WHERE resident_id = ?
          AND plan_date >= ?
          AND COALESCE(override_meal_id, meal_id) IS NOT NULL
    """, (resident_id, cutoff))
    rows = cur.fetchall()
    conn.close()
    return {r[0] for r in rows if r and r[0]}


def _pick_top_meal_for_resident_mealtype(
    resident: Resident,
    meals: list[Meal],
    meal_type: str,
    strict: bool,
    exclude_meal_ids: Optional[set[str]] = None,
    seed: Optional[int] = None,
):
    """
    Uses rule-based filter + ML scoring to pick ONE good meal, but with DIVERSITY:
    - score candidates
    - pick from top-k (not always #1)
    - avoid excluded meal_ids (recent history + same-day already used for this resident)
    """
    exclude_meal_ids = exclude_meal_ids or set()

    result = apply_basic_rules(
        resident,
        meals,
        meal_type=meal_type,
        meal_fraction=0.35,
        strict=strict,
        explain=True,
    )
    candidates, reasons = result

    if not candidates:
        return None, "No meals matched rules", None

    # remove excluded (recent + already used today for same resident)
    filtered = [m for m in candidates if m.meal_id not in exclude_meal_ids]
    if filtered:
        candidates = filtered

    # ML scoring
    scores = score_meals_for_resident(resident, candidates, xgb_model, xgb_scaler)
    for m in candidates:
        m.ml_score = scores.get(m.meal_id, 0.0)

    # Sort by ML score desc
    candidates_sorted = sorted(
        candidates,
        key=lambda m: (getattr(m, "ml_score", 0.0) or 0.0),
        reverse=True
    )

    # DIVERSITY: choose from top-k with weighted randomness
    top_k = min(6, len(candidates_sorted))
    top = candidates_sorted[:top_k]

    # stable randomness per seed (so same draft plan stays consistent)
    rng = np.random.default_rng(seed if seed is not None else None)

    weights = np.array([(float(getattr(m, "ml_score", 0.0) or 0.0) + 0.01) for m in top], dtype=float)
    weights = weights / weights.sum() if weights.sum() > 0 else None
    choice = rng.choice(top, p=weights) if weights is not None else rng.choice(top)

    reason = None
    try:
        if isinstance(reasons, dict):
            reason = reasons.get(choice.meal_id)
    except Exception:
        reason = None

    return choice, reason, float(getattr(choice, "ml_score", 0.0) or 0.0)


def _generate_daily_plan_items(plan_id: str, plan_date: str, strict: bool, seed_salt: str):
    """
    Generates items for ALL residents and ALL meal types and saves into SQLite.
    - Avoids same meal across breakfast/lunch/dinner/snack for same resident.
    - Avoids meals used in last 7 days for that resident.
    - Uses top-k weighted selection instead of always top-1.
    """
    rdf = read_residents_df()
    mdf = read_meals_df()

    if rdf.empty:
        raise ValueError("No residents found. Add residents first.")
    if mdf.empty:
        raise ValueError("No meals found. Prepare data/raw/meals.csv first.")

    residents = rows_to_residents(rdf)
    meals = rows_to_meals(mdf)

    conn = _daily_db()
    cur = conn.cursor()
    now_iso = datetime.utcnow().isoformat()

    # ✅ CHANGED: stable base seed
    base_seed = stable_seed(seed_salt)

    for resident in residents:
        used_today = set()
        recent = _recent_meal_ids_for_resident(resident.resident_id, days_back=7)

        for idx, mt in enumerate(MEAL_TYPES_FOR_DAY):
            # ✅ CHANGED: stable per resident/meal_type seed
            seed = stable_seed(seed_salt, resident.resident_id, mt, idx, base_seed)
            exclude = set(used_today) | set(recent)

            best_meal, reason, ml_score = _pick_top_meal_for_resident_mealtype(
                resident=resident,
                meals=meals,
                meal_type=mt,
                strict=strict,
                exclude_meal_ids=exclude,
                seed=seed,
            )

            if best_meal:
                used_today.add(best_meal.meal_id)

            item_id = str(uuid4())
            cur.execute("""
                INSERT INTO daily_plan_items
                    (item_id, plan_id, plan_date, resident_id, meal_type, meal_id, ml_score, reason, override_meal_id, created_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """, (
                item_id,
                plan_id,
                plan_date,
                resident.resident_id,
                mt,
                best_meal.meal_id if best_meal else None,
                ml_score if best_meal else None,
                reason,
                now_iso,
            ))

    conn.commit()
    conn.close()


def _load_daily_plan(plan_date: str):
    conn = _daily_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT plan_id, plan_date, created_at, strict, status, fulfilled_at
        FROM daily_plans
        WHERE plan_date = ?
    """, (plan_date,))
    prow = cur.fetchone()
    if not prow:
        conn.close()
        return None, []

    plan = {
        "plan_id": prow[0],
        "plan_date": prow[1],
        "created_at": prow[2],
        "strict": bool(prow[3]),
        "status": prow[4],
        "fulfilled_at": prow[5],
    }

    cur.execute("""
        SELECT item_id, resident_id, meal_type, meal_id, ml_score, reason, override_meal_id, created_at
        FROM daily_plan_items
        WHERE plan_id = ?
        ORDER BY resident_id, meal_type
    """, (plan["plan_id"],))
    rows = cur.fetchall()
    conn.close()

    items = []
    for r in rows:
        items.append({
            "item_id": r[0],
            "resident_id": r[1],
            "meal_type": r[2],
            "meal_id": r[3],
            "ml_score": r[4],
            "reason": r[5],
            "override_meal_id": r[6],
            "created_at": r[7],
        })
    return plan, items


# ---------- routes ----------

@app.route("/")
def home():
    meals_count = count_csv_rows(MEALS_CSV)
    residents_count = count_csv_rows(RESIDENTS_CSV)
    return render_template(
        "index.html",
        meals_count=meals_count,
        residents_count=residents_count
    )


@app.route("/residents")
@login_required
def residents_list():
    df = read_residents_df().copy()
    q = (request.args.get("q") or "").strip().lower()
    diet = (request.args.get("diet") or "").strip().lower()
    has_all = (request.args.get("allergy") or "").strip().lower()
    has_con = (request.args.get("condition") or "").strip().lower()
    sort = request.args.get("sort", "name")
    order = request.args.get("order", "asc")
    limit_s = request.args.get("limit", "20")

    try:
        limit = max(5, min(int(limit_s), 200))
    except ValueError:
        limit = 20

    for col in ["name", "diet_type", "allergies", "conditions", "cultural_prefs"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str)

    def bmi_calc(row):
        try:
            w = float(row["weight_kg"])
            h = float(row["height_cm"]) / 100
            return round(w / (h * h), 1) if w > 0 and h > 0 else None
        except Exception:
            return None

    df["bmi_val"] = df.apply(bmi_calc, axis=1)

    if q:
        df = df[df["name"].str.lower().str.contains(q) | df["cultural_prefs"].str.lower().str.contains(q)]
    if diet:
        df = df[df["diet_type"].str.lower() == diet]
    if has_all:
        df = df[df["allergies"].str.lower().str.contains(has_all)]
    if has_con:
        df = df[df["conditions"].str.lower().str.contains(has_con)]

    if sort == "bmi":
        df["_s"] = df["bmi_val"].fillna(10_000)
        asc = (order == "asc")
        df = df.sort_values("_s", ascending=asc).drop(columns=["_s"])
    else:
        asc = (order == "asc")
        if sort not in {"name", "age"}:
            sort = "name"
        df = df.sort_values(sort, ascending=asc, na_position="last")

    df = df.head(limit)

    def bmi_tag(v):
        if v is None:
            return ("–", "badge-soft")
        if v < 18.5:
            return (f"{v} Under", "badge-soft warning")
        if v < 25:
            return (f"{v} Healthy", "badge-soft success")
        if v < 30:
            return (f"{v} Over", "badge-soft warning")
        else:
            return (f"{v} Obese", "badge-soft")

    def initials(name):
        parts = str(name).strip().split()
        return (parts[0][:1] + (parts[1][:1] if len(parts) > 1 else "")).upper() or "R"

    residents = []
    for _, r in df.iterrows():
        allergies = [s.strip() for s in str(r["allergies"]).split(",") if s.strip()]
        conditions = [s.strip() for s in str(r["conditions"]).split(",") if s.strip()]
        bmi_text, bmi_class = bmi_tag(r["bmi_val"])
        status = "Active" if not conditions else ("Active" if "stable" in ",".join(conditions).lower() else "Needs review")
        status_class = "success" if status == "Active" else "warning"

        residents.append({
            "resident_id": r["resident_id"],
            "name": r["name"],
            "age": r.get("age", ""),
            "diet_type": r.get("diet_type", "none"),
            "bmi_text": bmi_text,
            "bmi_class": bmi_class,
            "allergies_list": allergies,
            "conditions_list": conditions,
            "initials": initials(r["name"]),
            "status": status,
            "status_class": status_class,
        })

    total = count_csv_rows(RESIDENTS_CSV)

    return render_template(
        "residents_list.html",
        residents=residents,
        total=total,
        q=q,
        diet=diet,
        has_all=has_all,
        has_con=has_con,
        sort=sort,
        order=order,
        limit=limit,
    )


@app.route("/daily_plan/pdf", methods=["GET"])
@login_required
def daily_plan_pdf():
    """
    Export the daily plan as a professional kitchen order sheet PDF.
    Usage: /daily_plan/pdf?date=YYYY-MM-DD
    """
    plan_date = request.args.get("date") or date.today().isoformat()
    try:
        plan_date = _parse_date_yyyy_mm_dd(plan_date)
    except ValueError:
        flash("Invalid date format for PDF export. Use YYYY-MM-DD.", "danger")
        return redirect(url_for("daily_plan_view", date=date.today().isoformat()))

    plan, items = _load_daily_plan(plan_date)
    if not plan:
        flash(f"No plan exists for {plan_date}. Generate one first.", "warning")
        return redirect(url_for("daily_plan_new"))

    # Same enrichment logic as daily_plan_view (resident + meal objects)
    rdf = read_residents_df()
    mdf = read_meals_df()
    residents = rows_to_residents(rdf) if not rdf.empty else []
    meals = rows_to_meals(mdf) if not mdf.empty else []

    resident_map = {r.resident_id: r for r in residents}
    meal_map = {m.meal_id: m for m in meals}

    enriched = []
    for it in items:
        rid = it["resident_id"]
        chosen_meal_id = it["override_meal_id"] or it["meal_id"]
        enriched.append(
            {
                **it,
                "resident": resident_map.get(rid),
                "meal": meal_map.get(chosen_meal_id) if chosen_meal_id else None,
                "original_meal": meal_map.get(it["meal_id"]) if it["meal_id"] else None,
                "is_overridden": bool(it["override_meal_id"]),
            }
        )

    pdf_buf = build_daily_plan_pdf(
        plan_date=plan_date,
        plan=plan,
        items=enriched,
        facility_name="Care Home — Daily Kitchen Order Sheet",
    )

    filename = f"daily_plan_{plan_date}.pdf"
    return send_file(
        pdf_buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )

@app.route("/login", methods=["GET", "POST"])
def login():
    # If already logged in, go straight to residents
    if session.get("user_id"):
        return redirect(url_for("residents_list"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_url = request.form.get("next") or url_for("residents_list")

        conn = _auth_db()
        cur = conn.cursor()
        cur.execute("SELECT user_id, password_hash FROM staff_users WHERE username = ?", (username,))
        row = cur.fetchone()
        conn.close()

        if not row or not check_password_hash(row[1], password):
            flash("Invalid username or password.", "danger")
            return render_template("login.html", next=next_url)
        session.clear()
   

        remember = request.form.get("remember") == "1"
        session.permanent = remember


        session["user_id"] = row[0]
        session["username"] = username
        session["last_seen"] = datetime.utcnow().isoformat()
        flash("Login successful.", "success")
        return redirect(next_url)

    # GET request
    return render_template("login.html", next=request.args.get("next") or "")

# Logout route
@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))

# ------------- Resident management routes -------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("residents_list"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        reg_code = (request.form.get("reg_code") or "").strip()


        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template("register.html")

        if password != password2:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")
        
        expected_code = os.environ.get("STAFF_REGISTRATION_CODE", "DEV_CODE")

        if reg_code != expected_code:
            flash("Invalid staff registration code.", "danger")
            return render_template("register.html")


        conn = _auth_db()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO staff_users (user_id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (str(uuid4()), username, generate_password_hash(password), datetime.utcnow().isoformat())
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            flash("That username is already taken.", "danger")
            return render_template("register.html")

        conn.close()
        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


def _now_utc():
    return datetime.utcnow()

@app.before_request
def enforce_session_timeout():
    if not session.get("user_id"):
        return

    IDLE_MINUTES = 20
    last_seen = session.get("last_seen")
    now = _now_utc()

    if last_seen:
        try:
            last_seen_dt = datetime.fromisoformat(last_seen)
            if now - last_seen_dt > timedelta(minutes=IDLE_MINUTES):
                session.clear()
                flash("Session expired due to inactivity. Please log in again.", "warning")
                return redirect(url_for("login", next=request.url))
        except Exception:
            session.clear()
            flash("Session error. Please log in again.", "warning")
            return redirect(url_for("login"))

    session["last_seen"] = now.isoformat()

@app.before_request
def enforce_session_timeout():
    # Only enforce for logged-in users
    if not session.get("user_id"):
        return

    # Idle timeout in minutes
    IDLE_MINUTES = 20
    last_seen = session.get("last_seen")

    now = _now_utc()

    if last_seen:
        try:
            last_seen_dt = datetime.fromisoformat(last_seen)
            if now - last_seen_dt > timedelta(minutes=IDLE_MINUTES):
                session.clear()
                flash("Session expired due to inactivity. Please log in again.", "warning")
                return redirect(url_for("login", next=request.url))
        except Exception:
            # If parsing fails, reset safely
            session.clear()
            flash("Session error. Please log in again.", "warning")
            return redirect(url_for("login"))

    # Refresh activity timestamp on every request
    session["last_seen"] = now.isoformat()

# Resident management routes

@app.route("/residents/new", methods=["GET", "POST"])
@login_required
def residents_new():
    if request.method == "POST":
        resident_id = str(uuid4())
        new_data = {
            "resident_id": resident_id,
            "name": request.form["name"],
            "age": int(request.form["age"]),
            "sex": request.form["sex"],
            "weight_kg": float(request.form["weight_kg"]),
            "height_cm": float(request.form["height_cm"]),
            "bmi": None,
            "calorie_target_kcal": request.form.get("calorie_target_kcal") or None,
            "protein_target_g": request.form.get("protein_target_g") or None,
            "allergies": request.form.get("allergies", ""),
            "conditions": request.form.get("conditions", ""),
            "texture": request.form.get("texture", "regular"),
            "diet_type": request.form.get("diet_type", "none"),
            "cultural_prefs": request.form.get("cultural_prefs", ""),
        }
        df = read_residents_df()
        df = pd.concat([df, pd.DataFrame([new_data])], ignore_index=True)
        df.to_csv(RESIDENTS_CSV, index=False)
        flash("Resident registered successfully!", "success")
        return redirect(url_for("residents_list"))

    return render_template("residents_new.html")


@app.route("/residents/<resident_id>/edit", methods=["GET", "POST"])
@login_required
def residents_edit(resident_id):
    df = read_residents_df()
    row = df[df["resident_id"] == resident_id]
    if row.empty:
        flash("Resident not found.", "danger")
        return redirect(url_for("residents_list"))

    resident_data = row.iloc[0].to_dict()

    if request.method == "POST":
        idx = df.index[df["resident_id"] == resident_id][0]
        df.at[idx, "name"] = request.form["name"]
        df.at[idx, "age"] = int(request.form["age"])
        df.at[idx, "sex"] = request.form["sex"]
        df.at[idx, "weight_kg"] = float(request.form["weight_kg"])
        df.at[idx, "height_cm"] = float(request.form["height_cm"])
        df.at[idx, "calorie_target_kcal"] = request.form.get("calorie_target_kcal") or None
        df.at[idx, "protein_target_g"] = request.form.get("protein_target_g") or None
        df.at[idx, "allergies"] = request.form.get("allergies", "")
        df.at[idx, "conditions"] = request.form.get("conditions", "")
        df.at[idx, "texture"] = request.form.get("texture", "regular")
        df.at[idx, "diet_type"] = request.form.get("diet_type", "none")
        df.at[idx, "cultural_prefs"] = request.form.get("cultural_prefs", "")

        df.to_csv(RESIDENTS_CSV, index=False)
        flash("Resident updated successfully.", "success")
        return redirect(url_for("residents_list"))

    return render_template("residents_edit.html", r=resident_data)


@app.route("/residents/<resident_id>/recommendations")
@login_required
def resident_recommendations(resident_id):
    meal_type = request.args.get("meal_type")
    sort_key = request.args.get("sort", "ml_score")
    view_mode = request.args.get("view", "cards")
    strict = request.args.get("strict", "1") != "0"
    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        limit = 20

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

    result = apply_basic_rules(
        resident,
        meals,
        meal_type=meal_type,
        meal_fraction=0.35,
        strict=strict,
        explain=True,
    )
    candidates, reasons = result

    scores = score_meals_for_resident(resident, candidates, xgb_model, xgb_scaler)
    for meal in candidates:
        meal.ml_score = scores.get(meal.meal_id, 0)

    allowed = {"name", "calories_kcal", "protein_g", "sodium_mg", "ml_score"}
    if sort_key not in allowed:
        sort_key = "ml_score"

    def sort_value(m):
        v = getattr(m, sort_key, None)
        if v is None:
            return "" if sort_key == "name" else 0
        return v.lower() if sort_key == "name" and isinstance(v, str) else v

    candidates_sorted = sorted(
        candidates,
        key=sort_value,
        reverse=(sort_key == "ml_score"),
    )[: max(1, min(limit, 200))]

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


@app.route("/feedback", methods=["POST"])
@login_required
def submit_feedback():
    try:
        data = request.get_json()
        resident_id = data.get("resident_id")
        meal_id = data.get("meal_id")
        feedback = data.get("feedback")

        if not all([resident_id, meal_id, feedback]):
            return jsonify({"success": False, "error": "Missing fields"}), 400

        conn = sqlite3.connect("feedback.db")
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resident_id TEXT,
                meal_id TEXT,
                feedback TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(
            "INSERT INTO feedback (resident_id, meal_id, feedback) VALUES (?, ?, ?)",
            (resident_id, meal_id, feedback),
        )
        conn.commit()
        conn.close()

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/residents/<resident_id>/delete", methods=["POST"])
@login_required
def residents_delete(resident_id):
    df = read_residents_df()
    new_df = df[df["resident_id"] != resident_id]
    if len(new_df) < len(df):
        new_df.to_csv(RESIDENTS_CSV, index=False)
        flash("Resident deleted.", "success")
    else:
        flash("Resident not found.", "danger")
    return redirect(url_for("residents_list"))


# ==========================================================
# DAILY PLAN ROUTES
# ==========================================================

@app.route("/daily_plan", methods=["GET"])
@login_required
def daily_plan_view():
    plan_date = request.args.get("date") or date.today().isoformat()
    try:
        plan_date = _parse_date_yyyy_mm_dd(plan_date)
    except ValueError:
        flash("Invalid date format. Use YYYY-MM-DD.", "danger")
        return redirect(url_for("residents_list"))

    plan, items = _load_daily_plan(plan_date)

    rdf = read_residents_df()
    mdf = read_meals_df()
    residents = rows_to_residents(rdf) if not rdf.empty else []
    meals = rows_to_meals(mdf) if not mdf.empty else []

    resident_map = {r.resident_id: r for r in residents}
    meal_map = {m.meal_id: m for m in meals}

    enriched = []
    for it in items:
        rid = it["resident_id"]
        chosen_meal_id = it["override_meal_id"] or it["meal_id"]

        enriched.append({
            **it,
            "resident": resident_map.get(rid),
            "meal": meal_map.get(chosen_meal_id) if chosen_meal_id else None,
            "original_meal": meal_map.get(it["meal_id"]) if it["meal_id"] else None,
            "is_overridden": bool(it["override_meal_id"]),
        })

    return render_template(
        "daily_plan.html",
        plan_date=plan_date,
        plan=plan,
        items=enriched,
        meal_types=MEAL_TYPES_FOR_DAY,
    )


@app.route("/daily_plan/new", methods=["GET"])
@login_required
def daily_plan_new():
    default_date = date.today().isoformat()
    return render_template("daily_plan_new.html", default_date=default_date)


@app.route("/daily_plan/generate", methods=["POST"])
@login_required
def daily_plan_generate():
    plan_date_raw = request.form.get("plan_date") or ""
    strict = request.form.get("strict", "1") != "0"
    regenerate = request.form.get("regenerate", "0") == "1"

    try:
        plan_date = _parse_date_yyyy_mm_dd(plan_date_raw)
    except ValueError:
        flash("Invalid date. Please choose a valid YYYY-MM-DD date.", "danger")
        return redirect(url_for("daily_plan_new"))

    try:
        plan_id, created_new, cleared_items = _get_or_create_daily_plan(
            plan_date=plan_date,
            strict=strict,
            allow_regenerate=regenerate,
        )

        # Only generate items if:
        # - plan is newly created OR
        # - regenerate was checked (items cleared)
        if created_new or cleared_items:
            seed_salt = f"{plan_id}:{plan_date}:{datetime.utcnow().isoformat() if regenerate else ''}"
            _generate_daily_plan_items(plan_id=plan_id, plan_date=plan_date, strict=strict, seed_salt=seed_salt)
            flash(f"Daily plan generated for {plan_date}.", "success")
        else:
            flash(f"Draft plan already exists for {plan_date}. Reusing existing recommendations.", "info")

        return redirect(url_for("daily_plan_view", date=plan_date))

    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("daily_plan_new"))
    except Exception as e:
        flash(f"Failed to generate daily plan: {e}", "danger")
        return redirect(url_for("daily_plan_new"))


@app.route("/daily_plan/fulfill", methods=["POST"])
@login_required
def daily_plan_fulfill():
    plan_date_raw = request.form.get("plan_date") or ""
    try:
        plan_date = _parse_date_yyyy_mm_dd(plan_date_raw)
    except ValueError:
        flash("Invalid date format.", "danger")
        return redirect(url_for("residents_list"))

    conn = _daily_db()
    cur = conn.cursor()
    cur.execute("SELECT plan_id, status FROM daily_plans WHERE plan_date = ?", (plan_date,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash("No daily plan exists for that date.", "warning")
        return redirect(url_for("daily_plan_view", date=plan_date))

    plan_id, status = row[0], row[1]
    if status == "fulfilled":
        conn.close()
        flash("This plan is already fulfilled.", "info")
        return redirect(url_for("daily_plan_view", date=plan_date))

    cur.execute(
        "UPDATE daily_plans SET status = 'fulfilled', fulfilled_at = ? WHERE plan_id = ?",
        (datetime.utcnow().isoformat(), plan_id),
    )
    conn.commit()
    conn.close()

    flash("Plan marked as fulfilled.", "success")
    return redirect(url_for("daily_plan_view", date=plan_date))


@app.route("/daily_plan/item_feedback", methods=["POST"])
@login_required
def daily_plan_item_feedback():
    item_id = request.form.get("item_id") or ""
    feedback = (request.form.get("feedback") or "").strip().lower()
    note = (request.form.get("note") or "").strip()

    if feedback not in {"liked", "disliked"}:
        flash("Invalid feedback value.", "danger")
        return redirect(request.referrer or url_for("residents_list"))

    conn = _daily_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT plan_id, plan_date, resident_id, meal_type, COALESCE(override_meal_id, meal_id) as chosen_meal_id
        FROM daily_plan_items
        WHERE item_id = ?
    """, (item_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash("Plan item not found.", "danger")
        return redirect(request.referrer or url_for("residents_list"))

    plan_id, plan_date, resident_id, meal_type, chosen_meal_id = row

    fb_id = str(uuid4())
    cur.execute("""
        INSERT INTO daily_plan_item_feedback
            (feedback_id, item_id, plan_id, plan_date, resident_id, meal_type, meal_id, feedback, note, created_at)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fb_id,
        item_id,
        plan_id,
        plan_date,
        resident_id,
        meal_type,
        chosen_meal_id,
        feedback,
        note if note else None,
        datetime.utcnow().isoformat(),
    ))
    conn.commit()
    conn.close()

    # ALSO write to feedback.db
    try:
        conn2 = sqlite3.connect("feedback.db")
        c2 = conn2.cursor()
        c2.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resident_id TEXT,
                meal_id TEXT,
                feedback TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c2.execute(
            "INSERT INTO feedback (resident_id, meal_id, feedback) VALUES (?, ?, ?)",
            (resident_id, chosen_meal_id, feedback),
        )
        conn2.commit()
        conn2.close()
    except Exception:
        pass

    flash("Feedback saved.", "success")
    return redirect(url_for("daily_plan_view", date=plan_date))


@app.route("/daily_plan/override", methods=["POST"])
@login_required
def daily_plan_override():
    item_id = request.form.get("item_id") or ""
    override_meal_id = (request.form.get("override_meal_id") or "").strip()

    if not item_id:
        flash("Missing item_id.", "danger")
        return redirect(request.referrer or url_for("residents_list"))

    if not override_meal_id:
        flash("Please choose a meal to override.", "warning")
        return redirect(request.referrer or url_for("residents_list"))

    mdf = read_meals_df()
    if mdf.empty or override_meal_id not in set(mdf["meal_id"].astype(str).tolist()):
        flash("Selected meal does not exist.", "danger")
        return redirect(request.referrer or url_for("residents_list"))

    conn = _daily_db()
    cur = conn.cursor()
    cur.execute("SELECT plan_date, plan_id FROM daily_plan_items WHERE item_id = ?", (item_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash("Plan item not found.", "danger")
        return redirect(request.referrer or url_for("residents_list"))

    plan_date, plan_id = row

    cur.execute("SELECT status FROM daily_plans WHERE plan_id = ?", (plan_id,))
    srow = cur.fetchone()
    if srow and srow[0] == "fulfilled":
        conn.close()
        flash("Plan is fulfilled (locked). You cannot override.", "warning")
        return redirect(url_for("daily_plan_view", date=plan_date))

    cur.execute("UPDATE daily_plan_items SET override_meal_id = ? WHERE item_id = ?", (override_meal_id, item_id))
    conn.commit()
    conn.close()

    flash("Meal overridden successfully.", "success")
    return redirect(url_for("daily_plan_view", date=plan_date))


# ==========================================================
# NEW: PDF EXPORT
# ==========================================================


if __name__ == "__main__":
    ensure_default_user()
    app.run(debug=True)

