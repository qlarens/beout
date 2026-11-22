"""Хранилище на SQLite: пользователи, поездки, точки маршрута."""
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id   INTEGER PRIMARY KEY,
    weight_kg REAL NOT NULL DEFAULT 75
);
CREATE TABLE IF NOT EXISTS rides (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at   REAL,
    live_until REAL
);
CREATE INDEX IF NOT EXISTS rides_by_msg  ON rides(chat_id, message_id);
CREATE INDEX IF NOT EXISTS rides_by_user ON rides(user_id, started_at);
CREATE TABLE IF NOT EXISTS points (
    ride_id  INTEGER NOT NULL,
    ts       REAL NOT NULL,
    lat      REAL NOT NULL,
    lon      REAL NOT NULL,
    accuracy REAL
);
CREATE INDEX IF NOT EXISTS points_by_ride ON points(ride_id, ts);
"""

DEFAULT_WEIGHT = 75.0


class Storage:
    def __init__(self, path="rides.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # --- пользователи ---
    def get_weight(self, user_id):
        row = self.conn.execute(
            "SELECT weight_kg FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["weight_kg"] if row else DEFAULT_WEIGHT

    def set_weight(self, user_id, weight_kg):
        with self.conn:
            self.conn.execute(
                "INSERT INTO users(user_id, weight_kg) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET weight_kg = excluded.weight_kg",
                (user_id, weight_kg),
            )

    # --- поездки ---
    def start_ride(self, user_id, chat_id, message_id, ts, live_until=None):
        """Начинает поездку; незавершённые поездки пользователя закрываются."""
        with self.conn:
            self.conn.execute(
                "UPDATE rides SET ended_at = ? WHERE user_id = ? AND ended_at IS NULL",
                (ts, user_id),
            )
            cur = self.conn.execute(
                "INSERT INTO rides(user_id, chat_id, message_id, started_at, live_until) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, chat_id, message_id, ts, live_until),
            )
            return cur.lastrowid

    def find_ride_by_message(self, chat_id, message_id):
        return self.conn.execute(
            "SELECT * FROM rides WHERE chat_id = ? AND message_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (chat_id, message_id),
        ).fetchone()

    def active_ride(self, user_id):
        return self.conn.execute(
            "SELECT * FROM rides WHERE user_id = ? AND ended_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    def finish_ride(self, ride_id, ts=None):
        with self.conn:
            self.conn.execute(
                "UPDATE rides SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (ts or time.time(), ride_id),
            )

    def close_expired(self, user_id, now=None):
        """Закрывает поездки, у которых истёк срок трансляции."""
        now = now or time.time()
        with self.conn:
            self.conn.execute(
                "UPDATE rides SET ended_at = live_until "
                "WHERE user_id = ? AND ended_at IS NULL "
                "AND live_until IS NOT NULL AND live_until < ?",
                (user_id, now - 60),
            )

    def get_ride(self, ride_id):
        return self.conn.execute(
            "SELECT * FROM rides WHERE id = ?", (ride_id,)
        ).fetchone()

    def list_rides(self, user_id, limit=50):
        return self.conn.execute(
            "SELECT * FROM rides WHERE user_id = ? ORDER BY started_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()

    # --- точки ---
    def add_point(self, ride_id, ts, lat, lon, accuracy=None):
        with self.conn:
            self.conn.execute(
                "INSERT INTO points(ride_id, ts, lat, lon, accuracy) VALUES (?, ?, ?, ?, ?)",
                (ride_id, ts, lat, lon, accuracy),
            )

    def get_points(self, ride_id):
        rows = self.conn.execute(
            "SELECT ts, lat, lon FROM points WHERE ride_id = ? ORDER BY ts",
            (ride_id,),
        ).fetchall()
        return [(r["ts"], r["lat"], r["lon"]) for r in rows]
