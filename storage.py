"""Хранилище на SQLite: пользователи, поездки, точки маршрута."""
import secrets
import sqlite3
import time
from pathlib import Path

from settings import DEFAULT_ACTIVITY, DEFAULT_WEIGHT, MAX_ACCURACY_M, SESSION_TTL_S, STALE_AFTER_S
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
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT NOT NULL UNIQUE,  -- случайный: по ссылке с ним видны маршруты участников
    owner_id   INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS session_members (
    session_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    PRIMARY KEY (session_id, user_id)
);
CREATE TABLE IF NOT EXISTS groups (
    chat_id     INTEGER PRIMARY KEY,
    posted_week TEXT  -- неделя последнего автопоста рейтинга, «2026-W48»
);
CREATE TABLE IF NOT EXISTS sos_contacts (  -- contact_id сам согласился по ссылке-приглашению
    user_id    INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, contact_id)
);
CREATE TABLE IF NOT EXISTS group_members (  -- согласились участвовать в рейтинге группы
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
"""

STAT_KEYS = (
    "distance_km", "duration_s", "moving_s", "avg_speed_kmh", "max_speed_kmh", "kcal",
    "best_1k_s", "best_5k_s",
)
# Личные рекорды по типам: (колонка, что лучше). Лучшие 1/5 км — только для бега.
RECORDS = {
    "ride": (("distance_km", max), ("max_speed_kmh", max)),
    "run": (("distance_km", max), ("best_1k_s", min), ("best_5k_s", min)),
    "walk": (("distance_km", max),),
}
# Колонки, появившиеся после первой версии: в старую базу они добавляются при запуске.
NEW_COLUMNS = {
    "users": (
        "consent_at REAL", "consent_version INTEGER NOT NULL DEFAULT 0", "kind TEXT",
        "sos_token TEXT",  # из ссылки-приглашения экстренного контакта
    ),
    "rides": (
        *(f"{k} NUMERIC" for k in STAT_KEYS),  # заполняются при завершении тренировки
        f"kind TEXT NOT NULL DEFAULT '{DEFAULT_ACTIVITY}'",
        "session_id INTEGER",  # совместная тренировка
        "elevation_gain_m INTEGER",  # от Open-Meteo после финиша; NULL — сервис был недоступен
    ),
}


class Storage:
    def __init__(self, path="beout.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Удалённые по /delete данные затираются в файле, а не остаются в свободных страницах.
        self.conn.execute("PRAGMA secure_delete = ON")
        self.conn.executescript(SCHEMA)
        with self.conn:
            added = set()
            for table, columns in NEW_COLUMNS.items():
                have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                for column in columns:
                    if column.split()[0] not in have:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
                        added.add(column.split()[0])
            # Появилась новая статистика — пересчитать закрытые тренировки.
            if added & set(STAT_KEYS):
                for (ride_id,) in self.conn.execute(
                    "SELECT id FROM rides WHERE ended_at IS NOT NULL"
                ).fetchall():
                    self._save_stats(ride_id)

    # --- пользователи ---
    def get_weight(self, user_id):
        row = self.conn.execute(
            "SELECT weight_kg FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["weight_kg"] if row else DEFAULT_WEIGHT

    def _set_user(self, user_id, column, value):
        """column — только из кода, не от пользователя."""
        with self.conn:
            self.conn.execute(
                f"INSERT INTO users(user_id, {column}) VALUES (?, ?) "
                f"ON CONFLICT(user_id) DO UPDATE SET {column} = excluded.{column}",
                (user_id, value),
            )

    def set_weight(self, user_id, weight_kg):
        self._set_user(user_id, "weight_kg", weight_kg)

    def get_kind(self, user_id):
        """Тип по умолчанию — последний выбранный."""
        row = self.conn.execute("SELECT kind FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return (row and row["kind"]) or DEFAULT_ACTIVITY

    def set_kind(self, user_id, kind):
        self._set_user(user_id, "kind", kind)

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
        """Удаляет всё о пользователе: точки, тренировки, вес, согласие, участие в сессиях и рейтингах."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM points WHERE ride_id IN (SELECT id FROM rides WHERE user_id = ?)",
                (user_id,),
            )
            self.conn.execute("DELETE FROM rides WHERE user_id = ?", (user_id,))
            self.conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            self.conn.execute(
                "DELETE FROM session_members WHERE user_id = ? "
                "OR session_id IN (SELECT id FROM sessions WHERE owner_id = ?)",
                (user_id, user_id),
            )
            self.conn.execute("DELETE FROM sessions WHERE owner_id = ?", (user_id,))
            self.conn.execute("DELETE FROM group_members WHERE user_id = ?", (user_id,))
            self.conn.execute(
                "DELETE FROM sos_contacts WHERE user_id = ? OR contact_id = ?", (user_id, user_id)
            )

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
            "sessions": [dict(r) for r in self.conn.execute(
                "SELECT s.id, s.owner_id, s.created_at FROM sessions s "
                "JOIN session_members m ON m.session_id = s.id WHERE m.user_id = ?", (user_id,),
            )],
            "rating_groups": [r[0] for r in self.conn.execute(
                "SELECT chat_id FROM group_members WHERE user_id = ?", (user_id,)
            )],
            "sos_contacts": self.sos_contacts(user_id),
            "sos_contact_for": [r[0] for r in self.conn.execute(
                "SELECT user_id FROM sos_contacts WHERE contact_id = ?", (user_id,)
            )],
        }

    # --- SOS ---
    def sos_token(self, user_id):
        """Секрет для ссылки-приглашения экстренного контакта; создаётся при первом запросе."""
        row = self.conn.execute("SELECT sos_token FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row["sos_token"]:
            return row["sos_token"]
        token = secrets.token_urlsafe(12)
        self._set_user(user_id, "sos_token", token)
        return token

    def rotate_sos_token(self, user_id):
        """Старая ссылка-приглашение перестаёт работать; новая создастся при следующем запросе."""
        self._set_user(user_id, "sos_token", None)

    def user_by_sos_token(self, token):
        row = self.conn.execute("SELECT user_id FROM users WHERE sos_token = ?", (token,)).fetchone()
        return row and row["user_id"]

    def set_sos_contact(self, user_id, contact_id, on):
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO sos_contacts(user_id, contact_id) VALUES (?, ?)" if on
                else "DELETE FROM sos_contacts WHERE user_id = ? AND contact_id = ?",
                (user_id, contact_id),
            )

    def sos_contacts(self, user_id):
        return [r[0] for r in self.conn.execute(
            "SELECT contact_id FROM sos_contacts WHERE user_id = ?", (user_id,)
        )]

    def set_elevation(self, ride_id, gain_m):
        with self.conn:
            self.conn.execute("UPDATE rides SET elevation_gain_m = ? WHERE id = ?", (gain_m, ride_id))

    # --- поездки ---
    def start_ride(self, user_id, chat_id, message_id, ts, live_until=None, kind=DEFAULT_ACTIVITY):
        """Начинает тренировку; незавершённые тренировки пользователя закрываются."""
        for (old_id,) in self.conn.execute(
            "SELECT id FROM rides WHERE user_id = ? AND ended_at IS NULL", (user_id,)
        ).fetchall():
            self.finish_ride(old_id, ts)
        session = self.current_session(user_id, ts)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO rides(user_id, chat_id, message_id, started_at, live_until, kind, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, chat_id, message_id, ts, live_until, kind, session and session["id"]),
            )
            return cur.lastrowid

    # --- совместные тренировки ---
    def create_session(self, owner_id, now=None):
        now = now or time.time()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO sessions(token, owner_id, created_at) VALUES (?, ?, ?)",
                (secrets.token_urlsafe(12), owner_id, now),
            )
        return self.join_session(self.get_session(cur.lastrowid)["token"], owner_id, now)

    def get_session(self, session_id):
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()

    def join_session(self, token, user_id, now=None):
        """Добавляет в сессию и привязывает идущую тренировку. None — нет такой или истекла."""
        now = now or time.time()
        session = self.conn.execute(
            "SELECT * FROM sessions WHERE token = ? AND created_at > ?", (token, now - SESSION_TTL_S)
        ).fetchone()
        if session:
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO session_members(session_id, user_id) VALUES (?, ?)",
                    (session["id"], user_id),
                )
                self.conn.execute(
                    "UPDATE rides SET session_id = ? WHERE user_id = ? AND ended_at IS NULL",
                    (session["id"], user_id),
                )
        return session

    def current_session(self, user_id, now=None):
        """Последняя неистёкшая сессия, в которой участвует пользователь."""
        return self.conn.execute(
            "SELECT s.* FROM sessions s JOIN session_members m ON m.session_id = s.id "
            "WHERE m.user_id = ? AND s.created_at > ? ORDER BY s.created_at DESC LIMIT 1",
            (user_id, (now or time.time()) - SESSION_TTL_S),
        ).fetchone()

    def is_member(self, session_id, user_id):
        return self.conn.execute(
            "SELECT 1 FROM session_members WHERE session_id = ? AND user_id = ?", (session_id, user_id)
        ).fetchone() is not None

    def session_members(self, session_id):
        return [r[0] for r in self.conn.execute(
            "SELECT user_id FROM session_members WHERE session_id = ?", (session_id,)
        )]

    def session_rides(self, session_id):
        return self.conn.execute(
            "SELECT * FROM rides WHERE session_id = ? ORDER BY started_at", (session_id,)
        ).fetchall()

    # --- рейтинги в группах ---
    def add_group(self, chat_id):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO groups(chat_id) VALUES (?)", (chat_id,))

    def forget_group(self, chat_id):
        """Бота убрали из группы."""
        with self.conn:
            self.conn.execute("DELETE FROM group_members WHERE chat_id = ?", (chat_id,))
            self.conn.execute("DELETE FROM groups WHERE chat_id = ?", (chat_id,))

    def set_group_member(self, chat_id, user_id, member):
        self.add_group(chat_id)
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO group_members(chat_id, user_id) VALUES (?, ?)" if member
                else "DELETE FROM group_members WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )

    def group_rating(self, chat_id, since, until):
        """{kind: [(user_id, км)]} по убыванию — только согласившиеся участники группы."""
        rating = {}
        for r in self.conn.execute(
            "SELECT r.kind, r.user_id, ROUND(SUM(r.distance_km), 1) AS km FROM rides r "
            "JOIN group_members g ON g.user_id = r.user_id AND g.chat_id = ? "
            "WHERE r.ended_at IS NOT NULL AND r.started_at >= ? AND r.started_at < ? "
            "GROUP BY r.kind, r.user_id HAVING km > 0 ORDER BY km DESC",
            (chat_id, since, until),
        ):
            rating.setdefault(r["kind"], []).append((r["user_id"], r["km"]))
        return rating

    def groups_to_post(self, week):
        """Группы с участниками, куда рейтинг за прошлую неделю ещё не отправлен."""
        return [r[0] for r in self.conn.execute(
            "SELECT chat_id FROM groups WHERE COALESCE(posted_week, '') != ? "
            "AND chat_id IN (SELECT chat_id FROM group_members)",
            (week,),
        )]

    def mark_posted(self, chat_id, week):
        with self.conn:
            self.conn.execute("UPDATE groups SET posted_week = ? WHERE chat_id = ?", (week, chat_id))

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

    def set_ride_kind(self, ride_id, kind):
        """Меняет тип тренировки; у завершённой пересчитывает статистику (фильтры и MET зависят от типа)."""
        with self.conn:
            self.conn.execute("UPDATE rides SET kind = ? WHERE id = ?", (kind, ride_id))
            if self.get_ride(ride_id)["ended_at"] is not None:
                self._save_stats(ride_id)

    def _save_stats(self, ride_id):
        """Без commit: вызывается внутри транзакции."""
        ride = self.get_ride(ride_id)
        stats = compute_stats(self.get_points(ride_id), self.get_weight(ride["user_id"]), ride["kind"])
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

    def list_rides(self, user_id, limit=50, before=None, kind=None):
        """Тренировки от новых к старым; before — started_at последней показанной, kind — фильтр."""
        return self.conn.execute(
            "SELECT * FROM rides WHERE user_id = ? AND started_at < ? AND kind = COALESCE(?, kind) "
            "ORDER BY started_at DESC LIMIT ?",
            (user_id, before if before is not None else 1e18, kind, limit),
        ).fetchall()

    def delete_ride(self, ride_id):
        with self.conn:
            self.conn.execute("DELETE FROM points WHERE ride_id = ?", (ride_id,))
            self.conn.execute("DELETE FROM rides WHERE id = ?", (ride_id,))

    def totals_by_kind(self, user_id, since=0):
        """Итоги завершённых тренировок, начатых не раньше since: {kind: {count, distance_km, moving_s}}."""
        rows = self.conn.execute(
            "SELECT kind, COUNT(*) AS count, ROUND(SUM(distance_km), 1) AS distance_km, "
            "SUM(moving_s) AS moving_s FROM rides "
            "WHERE user_id = ? AND ended_at IS NOT NULL AND started_at >= ? GROUP BY kind",
            (user_id, since),
        ).fetchall()
        return {r["kind"]: {k: r[k] for k in ("count", "distance_km", "moving_s")} for r in rows}

    def finished_starts(self, user_id):
        """Время начала всех завершённых тренировок — для серий."""
        return [r[0] for r in self.conn.execute(
            "SELECT started_at FROM rides WHERE user_id = ? AND ended_at IS NOT NULL", (user_id,)
        )]

    def best_values(self, user_id, kind, before=1e18):
        """Лучшие значения рекордных метрик среди завершённых тренировок типа kind, начатых до before.

        None — если таких тренировок не было.
        """
        cols = ", ".join(f"{'MAX' if better is max else 'MIN'}({key}) AS {key}" for key, better in RECORDS[kind])
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n, {cols} FROM rides "
            "WHERE user_id = ? AND kind = ? AND ended_at IS NOT NULL AND started_at < ?",
            (user_id, kind, before),
        ).fetchone()
        return {key: row[key] for key, _ in RECORDS[kind]} if row["n"] else None

    def new_records(self, ride):
        """Какие личные рекорды поставила завершённая тренировка (первая тренировка типа — не рекорд)."""
        if ride["ended_at"] is None:
            return []
        prev = self.best_values(ride["user_id"], ride["kind"], before=ride["started_at"])
        if prev is None:
            return []
        return [
            key for key, better in RECORDS[ride["kind"]]
            if ride[key] and (prev[key] is None or (ride[key] != prev[key] and better(ride[key], prev[key]) == ride[key]))
        ]

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
