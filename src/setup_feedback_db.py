import sqlite3
from pathlib import Path

# Ensure the data directory exists
data_dir = Path("data")
data_dir.mkdir(parents=True, exist_ok=True)

# Connect to the SQLite database
db_path = data_dir / "feedback.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Create feedback table
cursor.execute("""
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id TEXT NOT NULL,
    meal_id TEXT NOT NULL,
    feedback TEXT CHECK( feedback IN ('like','dislike') ) NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()
conn.close()

print(f"Feedback database created at: {db_path}")
