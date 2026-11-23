"""Хранилище на SQLite: пользователи, поездки, точки маршрута."""
import sqlite3
import time
from pathlib import Path

from settings import DEFAULT_WEIGHT, MAX_ACCURACY_M, STALE_AFTER_S
from stats import compute_stats

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

STAT_KEYS = ("distance_km", "duration_s", "moving_s", "avg_speed_kmh", "max_speed_kmh", "kcal")
# Колонки, появившиеся после первой версии: в старую базу они добавляются при запуске.
NEW_COLUMNS = {
    "users": ("consent_at REAL", "consent_version INTEGER NOT NULL DEFAULT 0"),
    "rides": tuple(f"{k} NUMERIC" for k in STAT_KEYS),  # заполняются при завершении поездки
}


class Storage:
    def __init__(self, path="beout.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Удалённые по /delete данные затираются в файле, а не остаются в свободных страницах.
        self.conn.execute("PRAGMA secure_delete = ON")
        self.conn.executescript(SCHEMA)
        with self.conn:
            for table, columns in NEW_COLUMNS.items():
                have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                for column in columns:
                    if column.split()[0] not in have:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            # Поездки, закрытые до появления колонок статистики.
            for (ride_id,) in self.conn.execute(
                "SELECT id FROM rides WHERE ended_at IS NOT NULL AND distance_km IS NULL"
            ).fetchall():
                self._save_stats(ride_id)

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

    def consent_version(self, user_id):
        """Версия условий, которую принял пользователь; 0 — не принимал."""
        row = self.conn.execute(
            "SELECT consent_version FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["consent_version"] if row else 0

    def set_consent(self, user_id, version):
        with self.conn:
            self.conn.execute(
                "INSERT INTO users(user_id, consent_at, consent_version) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "consent_at = excluded.consent_at, consent_version = excluded.consent_version",
                (user_id, time.time(), version),
            )

    def delete_user(self, user_id):
        """Удаляет всё о пользователе: точки, поездки, вес, согласие."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM points WHERE ride_id IN (SELECT id FROM rides WHERE user_id = ?)",
                (user_id,),
            )
            self.conn.execute("DELETE FROM rides WHERE user_id = ?", (user_id,))
            self.conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))

    def export_user(self, user_id):
        """Все данные пользователя одним словарём — для выгрузки по /export."""
        user = self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        rides = self.conn.execute(
            "SELECT * FROM rides WHERE user_id = ? ORDER BY started_at", (user_id,)
        ).fetchall()
        return {
            "user": dict(user) if user else {"user_id": user_id},
            "rides": [
                {**dict(r), "points": [dict(p) for p in self.conn.execute(
                    "SELECT ts, lat, lon, accuracy FROM points WHERE ride_id = ? ORDER BY ts",
                    (r["id"],),
                )]}
                for r in rides
            ],
        }

    # --- поездки ---
    def start_ride(self, user_id, chat_id, message_id, ts, live_until=None):
        """Начинает поездку; незавершённые поездки пользователя закрываются."""
        for (old_id,) in self.conn.execute(
            "SELECT id FROM rides WHERE user_id = ? AND ended_at IS NULL", (user_id,)
        ).fetchall():
            self.finish_ride(old_id, ts)
        with self.conn:
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
        """Закрывает поездку и сохраняет её статистику. False — если уже была закрыта."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE rides SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (ts or time.time(), ride_id),
            )
            if cur.rowcount:
                self._save_stats(ride_id)
        return cur.rowcount == 1

    def _save_stats(self, ride_id):
        """Без commit: вызывается внутри транзакции."""
        ride = self.get_ride(ride_id)
        stats = compute_stats(self.get_points(ride_id), self.get_weight(ride["user_id"]))
        self.conn.execute(
            f"UPDATE rides SET {', '.join(f'{k} = ?' for k in STAT_KEYS)} WHERE id = ?",
            (*(stats[k] for k in STAT_KEYS), ride_id),
        )

    def stale_rides(self, now=None):
        """Открытые поездки, которые пора закрыть: [(ride_id, время последней точки)]."""
        now = now or time.time()
        rows = self.conn.execute(
            "SELECT r.id, r.live_until, COALESCE(MAX(p.ts), r.started_at) AS last_ts "
            "FROM rides r LEFT JOIN points p ON p.ride_id = r.id "
            "WHERE r.ended_at IS NULL GROUP BY r.id"
        ).fetchall()
        return [
            (r["id"], r["last_ts"]) for r in rows
            if now - r["last_ts"] > STALE_AFTER_S
            or (r["live_until"] is not None and now > r["live_until"] + 60)
        ]

    def get_ride(self, ride_id):
        return self.conn.execute(
            "SELECT * FROM rides WHERE id = ?", (ride_id,)
        ).fetchone()

    def list_rides(self, user_id, limit=50, before=None):
        """Поездки от новых к старым; before — started_at последней показанной."""
        return self.conn.execute(
            "SELECT * FROM rides WHERE user_id = ? AND started_at < ? "
            "ORDER BY started_at DESC LIMIT ?",
            (user_id, before if before is not None else 1e18, limit),
        ).fetchall()

    def totals(self, user_id):
        """Итоги по всем завершённым поездкам."""
        row = self.conn.execute(
            "SELECT COUNT(ended_at) AS count, ROUND(COALESCE(SUM(distance_km), 0), 1) AS distance_km, "
            "COALESCE(SUM(moving_s), 0) AS moving_s, COALESCE(SUM(kcal), 0) AS kcal "
            "FROM rides WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(row)

    # --- точки ---
    def add_point(self, ride_id, ts, lat, lon, accuracy=None):
        if accuracy is not None and accuracy > MAX_ACCURACY_M:
            return
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

    # --- резервные копии ---
    def backup(self, backup_dir, keep=7):
        """Одна копия в день; старше keep дней удаляются (вместе с данными удалённых пользователей)."""
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"beout-{time.strftime('%Y-%m-%d')}.db"
        if not target.exists():
            tmp = target.with_suffix(".tmp")
            dst = sqlite3.connect(tmp)
            try:
                self.conn.backup(dst)
            finally:
                dst.close()
            tmp.replace(target)
        for old in sorted(backup_dir.glob("beout-*.db"))[:-keep]:
            old.unlink()
