from flask import Flask, jsonify, request
from flask_cors import CORS
import sqlite3
import jwt
import datetime
import json
import os
import re

try:
    import psycopg2
except ImportError:
    psycopg2 = None

app = Flask(__name__)
CORS(app)

SECRET_KEY = "supersecret123"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")


class CompatCursor:
    def __init__(self, cursor, is_postgres):
        self._cursor = cursor
        self._is_postgres = is_postgres

    def execute(self, query, params=None):
        sql = query
        if self._is_postgres:
            sql = sql.replace("?", "%s")
            sql = re.sub(r"\buser\b", '"user"', sql)
        if params is None:
            return self._cursor.execute(sql)
        return self._cursor.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class CompatConnection:
    def __init__(self, conn, is_postgres):
        self._conn = conn
        self._is_postgres = is_postgres

    def cursor(self):
        return CompatCursor(self._conn.cursor(), self._is_postgres)

    def commit(self):
        return self._conn.commit()

    def close(self):
        return self._conn.close()


def get_connection():
    if IS_POSTGRES:
        if psycopg2 is None:
            raise RuntimeError("PostgreSQL icin psycopg2-binary gerekli")
        return CompatConnection(psycopg2.connect(DATABASE_URL), True)
    return CompatConnection(sqlite3.connect("data.db"), False)

# ---------------- DB INIT ----------------
def db_init():
    conn = get_connection()
    cursor = conn.cursor()
    if IS_POSTGRES:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            password TEXT
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            "user" TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            difficulty INTEGER DEFAULT 5,
            energy_required INTEGER DEFAULT 5,
            focus_required INTEGER DEFAULT 5,
            estimated_time INTEGER DEFAULT 25,
            mood_fit TEXT DEFAULT '[]',
            category TEXT DEFAULT 'genel',
            reward INTEGER DEFAULT 10,
            deadline TEXT,
            created_at TEXT
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mood_logs (
            id SERIAL PRIMARY KEY,
            "user" TEXT NOT NULL,
            mood TEXT NOT NULL,
            energy_level INTEGER DEFAULT 5,
            logged_at TEXT NOT NULL
        )
        """)
    else:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            user TEXT NOT NULL,
            completed INTEGER DEFAULT 0
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mood_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL,
            mood TEXT NOT NULL,
            energy_level INTEGER DEFAULT 5,
            logged_at TEXT NOT NULL
        )
        """)
        cursor.execute("PRAGMA table_info(tasks)")
        task_columns = [row[1] for row in cursor.fetchall()]
        if "completed" not in task_columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN completed INTEGER DEFAULT 0")
        extra_columns = {
            "difficulty": "INTEGER DEFAULT 5",
            "energy_required": "INTEGER DEFAULT 5",
            "focus_required": "INTEGER DEFAULT 5",
            "estimated_time": "INTEGER DEFAULT 25",
            "mood_fit": "TEXT DEFAULT '[]'",
            "category": "TEXT DEFAULT 'genel'",
            "reward": "INTEGER DEFAULT 10",
            "deadline": "TEXT",
            "created_at": "TEXT"
        }
        for col_name, col_def in extra_columns.items():
            if col_name not in task_columns:
                cursor.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_def}")

    conn.commit()
    conn.close()

db_init()

# ---------------- TOKEN ----------------
def get_auth_from_token(token):
    try:
        if not token:
            return None

        if token.startswith("Bearer "):
            token = token.split(" ")[1]

        decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return {
            "user": decoded.get("user"),
            "role": decoded.get("role", "user")
        }

    except:
        return None


def is_admin(auth):
    return bool(auth and auth.get("role") == "admin")


def serialize_task(row):
    mood_fit_raw = row[7] or "[]"
    try:
        mood_fit = json.loads(mood_fit_raw)
        if not isinstance(mood_fit, list):
            mood_fit = []
    except:
        mood_fit = []

    return {
        "id": row[0],
        "title": row[1],
        "completed": row[2],
        "user": row[3],
        "difficulty": row[4],
        "energy_required": row[5],
        "focus_required": row[6],
        "mood_fit": mood_fit,
        "estimated_time": row[8],
        "category": row[9],
        "reward": row[10],
        "deadline": row[11]
    }


def mood_to_energy(mood):
    mapping = {
        "yorgun": 2,
        "odakli": 8,
        "enerjik": 9,
        "daginik": 4,
        "moral_dusuk": 3,
        "full_motive": 10,
        "normal": 6
    }
    return mapping.get(mood, 5)


def mood_to_pomodoro(mood):
    mapping = {
        "daginik": 15,
        "normal": 25,
        "odakli": 50,
        "enerjik": 40,
        "yorgun": 15,
        "moral_dusuk": 20,
        "full_motive": 60
    }
    return mapping.get(mood, 25)

# ---------------- HOME ----------------
@app.route("/")
def home():
    return "Task API çalışıyor 🚀"

# ---------------- REGISTER ----------------
@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"message": "kullanici adi ve sifre gerekli"}), 400

    if username == ADMIN_USERNAME:
        return jsonify({"message": "bu kullanici adi kullanilamaz"}), 400

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            (username, password)
        )
        conn.commit()
        return jsonify({"message": "user eklendi 🚀"}), 201

    except:
        return jsonify({"message": "user zaten var 💀"}), 400

    finally:
        conn.close()

# ---------------- LOGIN ----------------
@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        token = jwt.encode({
            "user": ADMIN_USERNAME,
            "role": "admin",
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=6)
        }, SECRET_KEY, algorithm="HS256")
        return jsonify({"token": token, "role": "admin", "username": ADMIN_USERNAME})

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username, password)
    )

    user = cursor.fetchone()
    conn.close()

    if not user:
        return jsonify({"message": "hatalı giriş 💀"}), 401

    token = jwt.encode({
        "user": username,
        "role": "user",
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    }, SECRET_KEY, algorithm="HS256")

    return jsonify({"token": token, "role": "user", "username": username})

# ---------------- GET TASKS ----------------
@app.route("/tasks", methods=["GET"])
def get_tasks():
    token = request.headers.get("Authorization")
    auth = get_auth_from_token(token)

    if not auth:
        return jsonify({"message": "yetkisiz 💀"}), 403

    conn = get_connection()
    cursor = conn.cursor()

    if is_admin(auth):
        cursor.execute(
            """
            SELECT id, title, completed, user, difficulty, energy_required,
                   focus_required, mood_fit, estimated_time, category, reward, deadline
            FROM tasks
            ORDER BY id DESC
            """
        )
    else:
        cursor.execute(
            """
            SELECT id, title, completed, user, difficulty, energy_required,
                   focus_required, mood_fit, estimated_time, category, reward, deadline
            FROM tasks
            WHERE user = ?
            ORDER BY id DESC
            """,
            (auth["user"],)
        )

    rows = cursor.fetchall()
    conn.close()

    tasks = [serialize_task(r) for r in rows]

    return jsonify(tasks)

# ---------------- GET ALL TASKS ----------------
@app.route("/tasks/all", methods=["GET"])
def get_all_tasks():
    token = request.headers.get("Authorization")
    auth = get_auth_from_token(token)

    if not auth or auth["role"] != "admin":
        return jsonify({"message": "yalnizca admin gorebilir"}), 403

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, completed, user, difficulty, energy_required,
               focus_required, mood_fit, estimated_time, category, reward, deadline
        FROM tasks
        ORDER BY id DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    tasks = [serialize_task(r) for r in rows]
    return jsonify(tasks)

# ---------------- ADD TASK ----------------
@app.route("/tasks", methods=["POST"])
def add_task():
    token = request.headers.get("Authorization")
    auth = get_auth_from_token(token)

    if not auth:
        return jsonify({"message": "yetkisiz 💀"}), 403

    data = request.get_json() or {}
    task_title = (data.get("title") or "").strip()

    if not task_title:
        return jsonify({"message": "task basligi bos olamaz"}), 400

    task_owner = auth["user"]
    if auth["role"] == "admin":
        candidate_user = (data.get("user") or "").strip()
        if candidate_user:
            task_owner = candidate_user

    mood_fit = data.get("mood_fit") or []
    if not isinstance(mood_fit, list):
        mood_fit = []

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO tasks
        (title, user, difficulty, energy_required, focus_required, estimated_time,
         mood_fit, category, reward, deadline, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_title,
            task_owner,
            int(data.get("difficulty") or 5),
            int(data.get("energy_required") or 5),
            int(data.get("focus_required") or 5),
            int(data.get("estimated_time") or 25),
            json.dumps(mood_fit),
            (data.get("category") or "genel").strip() or "genel",
            int(data.get("reward") or 10),
            (data.get("deadline") or "").strip() or None,
            datetime.datetime.utcnow().isoformat()
        )
    )

    conn.commit()
    conn.close()

    return jsonify({"message": "task eklendi 🚀"}), 201

# ---------------- UPDATE TASK ----------------
@app.route("/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    token = request.headers.get("Authorization")
    auth = get_auth_from_token(token)

    if not auth:
        return jsonify({"message": "yetkisiz 💀"}), 403

    data = request.get_json() or {}
    new_title = (data.get("title") or "").strip()

    if not new_title:
        return jsonify({"message": "task basligi bos olamaz"}), 400

    mood_fit = data.get("mood_fit") or []
    if not isinstance(mood_fit, list):
        mood_fit = []

    conn = get_connection()
    cursor = conn.cursor()

    if auth["role"] == "admin":
        cursor.execute(
            """
            UPDATE tasks
            SET title = ?, difficulty = ?, energy_required = ?, focus_required = ?,
                estimated_time = ?, mood_fit = ?, category = ?, reward = ?, deadline = ?
            WHERE id = ?
            """,
            (
                new_title,
                int(data.get("difficulty") or 5),
                int(data.get("energy_required") or 5),
                int(data.get("focus_required") or 5),
                int(data.get("estimated_time") or 25),
                json.dumps(mood_fit),
                (data.get("category") or "genel").strip() or "genel",
                int(data.get("reward") or 10),
                (data.get("deadline") or "").strip() or None,
                task_id
            )
        )
    else:
        cursor.execute(
            """
            UPDATE tasks
            SET title = ?, difficulty = ?, energy_required = ?, focus_required = ?,
                estimated_time = ?, mood_fit = ?, category = ?, reward = ?, deadline = ?
            WHERE id = ? AND user = ?
            """,
            (
                new_title,
                int(data.get("difficulty") or 5),
                int(data.get("energy_required") or 5),
                int(data.get("focus_required") or 5),
                int(data.get("estimated_time") or 25),
                json.dumps(mood_fit),
                (data.get("category") or "genel").strip() or "genel",
                int(data.get("reward") or 10),
                (data.get("deadline") or "").strip() or None,
                task_id,
                auth["user"]
            )
        )

    if cursor.rowcount == 0:
        conn.close()
        return jsonify({"message": "task yok 💀"}), 404

    conn.commit()
    conn.close()

    return jsonify({"message": "task guncellendi ✅"})

# ---------------- DELETE TASK ----------------
@app.route("/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    token = request.headers.get("Authorization")
    auth = get_auth_from_token(token)

    if not auth:
        return jsonify({"message": "yetkisiz 💀"}), 403

    conn = get_connection()
    cursor = conn.cursor()

    if auth["role"] == "admin":
        cursor.execute(
            "DELETE FROM tasks WHERE id = ?",
            (task_id,)
        )
    else:
        cursor.execute(
            "DELETE FROM tasks WHERE id = ? AND user = ?",
            (task_id, auth["user"])
        )

    conn.commit()
    conn.close()

    return jsonify({"message": "silindi 💀"})

# ---------------- TOGGLE TASK (DONE / NOT DONE) ----------------
@app.route("/tasks/toggle/<int:task_id>", methods=["PUT"])
def toggle_task(task_id):
    token = request.headers.get("Authorization")
    auth = get_auth_from_token(token)

    if not auth:
        return jsonify({"message": "yetkisiz 💀"}), 403

    conn = get_connection()
    cursor = conn.cursor()

    if auth["role"] == "admin":
        cursor.execute(
            "SELECT completed FROM tasks WHERE id = ?",
            (task_id,)
        )
    else:
        cursor.execute(
            "SELECT completed FROM tasks WHERE id = ? AND user = ?",
            (task_id, auth["user"])
        )

    row = cursor.fetchone()

    if not row:
        conn.close()
        return jsonify({"message": "task yok 💀"}), 404

    new_value = 0 if row[0] == 1 else 1

    if auth["role"] == "admin":
        cursor.execute("""
            UPDATE tasks
            SET completed = ?
            WHERE id = ?
        """, (new_value, task_id))
    else:
        cursor.execute("""
            UPDATE tasks
            SET completed = ?
            WHERE id = ? AND user = ?
        """, (new_value, task_id, auth["user"]))

    conn.commit()
    conn.close()

    return jsonify({"message": "toggle ok 🔥"})


# ---------------- MOOD SAVE ----------------
@app.route("/mood", methods=["POST"])
def save_mood():
    token = request.headers.get("Authorization")
    auth = get_auth_from_token(token)
    if not auth:
        return jsonify({"message": "yetkisiz"}), 403

    data = request.get_json() or {}
    mood = (data.get("mood") or "normal").strip()
    energy_level = int(data.get("energy_level") or mood_to_energy(mood))

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO mood_logs (user, mood, energy_level, logged_at) VALUES (?, ?, ?, ?)",
        (auth["user"], mood, energy_level, datetime.datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "ruh hali kaydedildi"})


# ---------------- SMART PLANNER ----------------
@app.route("/planner", methods=["GET"])
def planner():
    token = request.headers.get("Authorization")
    auth = get_auth_from_token(token)
    if not auth:
        return jsonify({"message": "yetkisiz"}), 403

    mood = (request.args.get("mood") or "normal").strip()
    energy = mood_to_energy(mood)

    conn = get_connection()
    cursor = conn.cursor()
    if is_admin(auth):
        cursor.execute("""
            SELECT id, title, completed, user, difficulty, energy_required,
                   focus_required, mood_fit, estimated_time, category, reward, deadline
            FROM tasks
            WHERE completed = 0
            ORDER BY id DESC
        """)
    else:
        cursor.execute("""
            SELECT id, title, completed, user, difficulty, energy_required,
                   focus_required, mood_fit, estimated_time, category, reward, deadline
            FROM tasks
            WHERE user = ? AND completed = 0
            ORDER BY id DESC
        """, (auth["user"],))
    rows = cursor.fetchall()
    conn.close()

    tasks = [serialize_task(r) for r in rows]

    def score(task):
        score_value = 0
        if mood in task["mood_fit"]:
            score_value += 35
        score_value += max(0, 20 - abs(task["energy_required"] - energy) * 2)
        score_value += max(0, 20 - abs(task["focus_required"] - energy) * 2)
        score_value += max(0, 15 - int(task["estimated_time"] or 0) // 10)
        score_value += min(10, int(task["reward"] or 0))
        return score_value

    ranked = sorted(tasks, key=score, reverse=True)
    suggested = ranked[:5]
    deferred = ranked[5:8]

    return jsonify({
        "mood": mood,
        "pomodoro_minutes": mood_to_pomodoro(mood),
        "onerilen_gorevler": suggested,
        "bugun_agir_olabilir": deferred
    })


# ---------------- ANALYTICS ----------------
@app.route("/analytics", methods=["GET"])
def analytics():
    token = request.headers.get("Authorization")
    auth = get_auth_from_token(token)
    if not auth:
        return jsonify({"message": "yetkisiz"}), 403

    conn = get_connection()
    cursor = conn.cursor()

    if is_admin(auth):
        cursor.execute("SELECT completed, estimated_time FROM tasks")
        all_tasks = cursor.fetchall()
        cursor.execute("SELECT mood, energy_level FROM mood_logs")
        moods = cursor.fetchall()
    else:
        cursor.execute("SELECT completed, estimated_time FROM tasks WHERE user = ?", (auth["user"],))
        all_tasks = cursor.fetchall()
        cursor.execute("SELECT mood, energy_level FROM mood_logs WHERE user = ?", (auth["user"],))
        moods = cursor.fetchall()

    conn.close()

    total = len(all_tasks)
    completed = len([t for t in all_tasks if int(t[0]) == 1])
    completion_rate = round((completed / total) * 100, 1) if total else 0.0
    avg_focus_min = round(sum((t[1] or 0) for t in all_tasks) / total, 1) if total else 0.0
    avg_energy = round(sum((m[1] or 0) for m in moods) / len(moods), 1) if moods else 0.0

    mood_stats = {}
    for mood_name, _ in moods:
        mood_stats[mood_name] = mood_stats.get(mood_name, 0) + 1

    burnout_risk = completion_rate < 35 and avg_energy < 4 and total >= 5

    return jsonify({
        "tamamlanma_orani": completion_rate,
        "ortalama_odak_suresi": avg_focus_min,
        "ortalama_enerji": avg_energy,
        "mood_dagilimi": mood_stats,
        "burnout_riski": burnout_risk,
        "burnout_mesaji": (
            "Son gunlerde yogunluk yuksek. Bugun daha hafif gorevlerle devam etmeni oneririm."
            if burnout_risk else
            "Dengen iyi gorunuyor. Bu tempoyu koruyabilirsin."
        )
    })

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)