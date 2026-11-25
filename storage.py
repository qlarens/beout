"""Хранилище на SQLite: пользователи, поездки, точки маршрута."""
import secrets
import sqlite3
import time
from pathlib import Path

from i18n import DEFAULT_LANG
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
CREATE TABLE IF NOT EXISTS referrals (  -- user_id впервые принял условия по ссылке inviter_id
    user_id    INTEGER PRIMARY KEY,
    inviter_id INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS referrals_by_inviter ON referrals(inviter_id, created_at);
CREATE TABLE IF NOT EXISTS counters (  -- обезличенные счётчики по дням (UTC): без user_id
    day  TEXT NOT NULL,
    name TEXT NOT NULL,
    n    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, name)
);
CREATE TABLE IF NOT EXISTS challenges (  -- цель группы на N дней: общая или каждому
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    creator_id INTEGER NOT NULL,
    goal_km    REAL NOT NULL,
    kind       TEXT,              -- NULL — все типы
    each       INTEGER NOT NULL DEFAULT 0,
    starts_at  REAL NOT NULL,
    ends_at    REAL NOT NULL,
    message_id INTEGER,
    reached_at REAL,
    closed     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (  -- сбор на тренировку
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    creator_id INTEGER NOT NULL,
    starts_at  REAL NOT NULL,
    tz         INTEGER NOT NULL,  -- как Date.getTimezoneOffset() у создателя: для показа времени
    place      TEXT NOT NULL,
    message_id INTEGER,
    session_id INTEGER,
    stage      INTEGER NOT NULL DEFAULT 0  -- 0 объявлен, 1 напомнили, 2 начался, 3 итоги, -1 отменён
);
CREATE TABLE IF NOT EXISTS event_rsvp (
    event_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    answer   TEXT NOT NULL,       -- go | maybe | no
    PRIMARY KEY (event_id, user_id)
);
CREATE TABLE IF NOT EXISTS duels (  -- кто больше км за N дней
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    a_id       INTEGER NOT NULL,
    b_id       INTEGER,           -- NULL — открытый вызов, принимает любой
    kind       TEXT,
    days       INTEGER NOT NULL,
    created_at REAL NOT NULL,
    starts_at  REAL,
    ends_at    REAL,
    message_id INTEGER,
    status     TEXT NOT NULL DEFAULT 'open'  -- open | active | done | declined
);
CREATE TABLE IF NOT EXISTS club_wars (  -- группа против группы: сумма км участников рейтинга
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,
    a_chat     INTEGER NOT NULL,
    b_chat     INTEGER,
    days       INTEGER NOT NULL,
    created_at REAL NOT NULL,
    starts_at  REAL,
    ends_at    REAL,
    a_msg      INTEGER,
    b_msg      INTEGER,
    status     TEXT NOT NULL DEFAULT 'open'  -- open | active | done
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
        "lang TEXT",  # язык, выбранный в профиле; NULL — как в Telegram
        "ref_token TEXT",  # из реферальной ссылки «пригласить друзей»; постоянный
        "tg_lang TEXT",  # язык Telegram (ru/uk/en) — для сообщений без апдейта: итог по таймеру, SOS
        "joined_at REAL",  # первое согласие — для статистики роста
        "last_seen REAL",  # последний заход в Mini App (с точностью до 10 минут)
        "policy_notified INTEGER NOT NULL DEFAULT 0",  # о какой версии условий бот уже написал
        "tz INTEGER",  # часовой пояс телефона (Date.getTimezoneOffset()) — для времени сборов и номинаций
    ),
    "groups": (
        "lang TEXT",  # язык того, кто вызвал /top, — для автопоста рейтинга
        "title TEXT",  # название группы — для битвы клубов
        "tz INTEGER",  # часовой пояс того, кто последним настраивал группу, — для напоминаний
        "reminders INTEGER NOT NULL DEFAULT 1",  # напоминать группе, если давно не тренировались
        "nudged_at REAL",
    ),
    "group_members": ("feed INTEGER NOT NULL DEFAULT 0",),  # публиковать мои тренировки в этой группе
    "sos_contacts": ("lang TEXT",),  # язык контакта, когда он согласился: SOS придёт на нём
    "points": ("snap_lat REAL", "snap_lon REAL"),  # точка, привязанная к дороге (только велотренировки)
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
            if "joined_at" in added:  # для старых — первая тренировка или согласие, что раньше
                self.conn.execute(
                    "UPDATE users SET joined_at = MIN(COALESCE(consent_at, 9e18), COALESCE("
                    "(SELECT MIN(started_at) FROM rides WHERE rides.user_id = users.user_id), 9e18))"
                )
                self.conn.execute("UPDATE users SET joined_at = NULL WHERE joined_at = 9e18")
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

    def lang_choice(self, user_id):
        """Язык, выбранный в профиле; None — автоматически."""
        row = self.conn.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row and row["lang"]

    def set_lang(self, user_id, lang):
        self._set_user(user_id, "lang", lang)

    def user_lang(self, user_id):
        """Язык сообщений: выбранный в профиле, иначе язык Telegram, иначе английский."""
        row = self.conn.execute("SELECT lang, tg_lang FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return (row and (row["lang"] or row["tg_lang"])) or DEFAULT_LANG

    def remember_tg_lang(self, user_id, lang):
        """Язык Telegram — только тех, кто принял условия: без согласия ничего не храним."""
        with self.conn:
            self.conn.execute(
                "UPDATE users SET tg_lang = ? WHERE user_id = ? AND consent_version > 0 AND tg_lang IS NOT ?",
                (lang, user_id, lang),
            )

    def consent_version(self, user_id):
        """Версия условий, которую принял пользователь; 0 — не принимал."""
        row = self.conn.execute(
            "SELECT consent_version FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["consent_version"] if row else 0

    def users_count(self):
        """Сколько человек приняли условия."""
        return self.conn.execute("SELECT COUNT(*) FROM users WHERE consent_version > 0").fetchone()[0]

    def set_consent(self, user_id, version):
        now = time.time()
        with self.conn:
            self.conn.execute(
                "INSERT INTO users(user_id, consent_at, consent_version, joined_at, policy_notified) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                "consent_at = excluded.consent_at, consent_version = excluded.consent_version, "
                "joined_at = COALESCE(users.joined_at, excluded.joined_at), policy_notified = excluded.policy_notified",
                (user_id, now, version, now, version),
            )

    def users_to_notify(self, version):
        """Принимали старые условия, и о версии version бот им ещё не писал."""
        return [r[0] for r in self.conn.execute(
            "SELECT user_id FROM users WHERE consent_version > 0 AND consent_version < ? AND policy_notified < ?",
            (version, version),
        )]

    def mark_policy_notified(self, user_id, version):
        self._set_user(user_id, "policy_notified", version)

    def touch(self, user_id, now=None):
        """Отметка «заходил в Mini App»; пишем не чаще раза в 10 минут."""
        now = now or time.time()
        with self.conn:
            self.conn.execute(
                "UPDATE users SET last_seen = ? WHERE user_id = ? AND (last_seen IS NULL OR last_seen < ?)",
                (now, user_id, now - 600),
            )

    def count(self, name, now=None):
        """+1 к обезличенному счётчику за сегодня (UTC)."""
        day = time.strftime("%Y-%m-%d", time.gmtime(now or time.time()))
        with self.conn:
            self.conn.execute(
                "INSERT INTO counters(day, name, n) VALUES (?, ?, 1) "
                "ON CONFLICT(day, name) DO UPDATE SET n = n + 1", (day, name),
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
            self.conn.execute("DELETE FROM referrals WHERE user_id = ? OR inviter_id = ?", (user_id, user_id))
            self.conn.execute("DELETE FROM event_rsvp WHERE user_id = ?", (user_id,))
            self.conn.execute(
                "DELETE FROM event_rsvp WHERE event_id IN (SELECT id FROM events WHERE creator_id = ?)", (user_id,)
            )
            self.conn.execute("DELETE FROM events WHERE creator_id = ?", (user_id,))
            self.conn.execute("DELETE FROM challenges WHERE creator_id = ?", (user_id,))
            self.conn.execute("DELETE FROM duels WHERE a_id = ? OR b_id = ?", (user_id, user_id))

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
            "sos_contact_for": [dict(r) for r in self.conn.execute(
                "SELECT user_id, lang FROM sos_contacts WHERE contact_id = ?", (user_id,)
            )],
            "invited_by": self.inviter(user_id),
            "invited": [dict(r) for r in self.referrals(user_id)],
            "feed_groups": self.feed_groups(user_id),
            "event_answers": [dict(r) for r in self.conn.execute(
                "SELECT event_id, answer FROM event_rsvp WHERE user_id = ?", (user_id,))],
            "events_created": [dict(r) for r in self.conn.execute(
                "SELECT id, chat_id, starts_at, place FROM events WHERE creator_id = ?", (user_id,))],
            "challenges_created": [dict(r) for r in self.conn.execute(
                "SELECT id, chat_id, goal_km, kind, starts_at, ends_at FROM challenges WHERE creator_id = ?",
                (user_id,))],
            "duels": [dict(r) for r in self.conn.execute(
                "SELECT id, chat_id, a_id, b_id, kind, starts_at, ends_at, status FROM duels "
                "WHERE a_id = ? OR b_id = ?", (user_id, user_id))],
        }

    # --- реферальные ссылки ---
    def ref_token(self, user_id):
        """Секрет для ссылки «пригласить друзей»; создаётся при первом запросе и не меняется."""
        row = self.conn.execute("SELECT ref_token FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row["ref_token"]:
            return row["ref_token"]
        token = secrets.token_urlsafe(12)
        self._set_user(user_id, "ref_token", token)
        return token

    def user_by_ref_token(self, token):
        row = self.conn.execute("SELECT user_id FROM users WHERE ref_token = ?", (token,)).fetchone()
        return row and row["user_id"]

    def add_referral(self, user_id, inviter_id):
        """Засчитывает приглашение; у пользователя может быть только один пригласивший."""
        if user_id == inviter_id:
            return False
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO referrals(user_id, inviter_id, created_at) VALUES (?, ?, ?)",
                (user_id, inviter_id, time.time()),
            )
        return cur.rowcount == 1

    def inviter(self, user_id):
        row = self.conn.execute("SELECT inviter_id FROM referrals WHERE user_id = ?", (user_id,)).fetchone()
        return row and row["inviter_id"]

    def referrals(self, inviter_id):
        """Кого пригласил пользователь, новые сверху."""
        return self.conn.execute(
            "SELECT user_id, created_at FROM referrals WHERE inviter_id = ? ORDER BY created_at DESC", (inviter_id,)
        ).fetchall()

    def active_since(self, user_id, since):
        """Начинал ли тренировку с момента since."""
        return self.conn.execute(
            "SELECT 1 FROM rides WHERE user_id = ? AND started_at >= ? LIMIT 1", (user_id, since)
        ).fetchone() is not None

    def referral_totals(self):
        """Все пригласившие: (inviter_id, сколько), больше — выше."""
        return [tuple(r) for r in self.conn.execute(
            "SELECT inviter_id, COUNT(*) AS n FROM referrals GROUP BY inviter_id ORDER BY n DESC, MIN(created_at)"
        )]

    # --- статистика для админки ---
    def admin_stats(self, now, policy_version, tz=0, days=30):
        """Сводка для админ-вкладки. tz — как Date.getTimezoneOffset() (UTC − местное, в минутах):
        по нему режутся дни на графиках; policy_version — чтобы посчитать, кто не принял новые условия."""
        q = lambda sql, *args: self.conn.execute(sql, args).fetchone()[0] or 0  # noqa: E731
        day = 86400
        week, prev, month = now - 7 * day, now - 14 * day, now - 30 * day
        shift = -tz * 60  # местное время = UTC + shift
        since = ((now + shift) // day - (days - 1)) * day - shift  # полночь по местному времени, days дней назад
        users = "SELECT user_id FROM users WHERE consent_version > 0"

        def active(start, end):
            return q("SELECT COUNT(DISTINCT user_id) FROM rides WHERE started_at >= ? AND started_at < ?", start, end)

        def rides(start, end):
            return self.conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(distance_km), 0) FROM rides "
                "WHERE ended_at IS NOT NULL AND started_at >= ? AND started_at < ?", (start, end),
            ).fetchone()

        def per_day(sql, *args):
            out = [0] * days
            for ts, n in self.conn.execute(sql, (since, *args)):
                i = int((ts - since) // day)
                if 0 <= i < days:
                    out[i] += n
            return out

        def with_rides(at_least):
            return q(f"SELECT COUNT(*) FROM (SELECT user_id FROM rides WHERE user_id IN ({users}) "
                     "GROUP BY user_id HAVING COUNT(*) >= ?)", at_least)

        rides_7, rides_prev = rides(week, now + 1), rides(prev, week)
        cohort = q("SELECT COUNT(*) FROM users WHERE consent_version > 0 AND joined_at <= ?", week)
        came_back = q(
            "SELECT COUNT(*) FROM users u WHERE consent_version > 0 AND joined_at <= ? AND EXISTS "
            "(SELECT 1 FROM rides r WHERE r.user_id = u.user_id AND r.started_at >= u.joined_at + ?)", week, 7 * day,
        )
        last_week = {r[0] for r in self.conn.execute(
            "SELECT DISTINCT user_id FROM rides WHERE started_at >= ? AND started_at < ?", (prev, week))}
        this_week = {r[0] for r in self.conn.execute("SELECT DISTINCT user_id FROM rides WHERE started_at >= ?", (week,))}
        return {
            "users": q("SELECT COUNT(*) FROM users WHERE consent_version > 0"),
            "new_7d": q("SELECT COUNT(*) FROM users WHERE consent_version > 0 AND joined_at >= ?", week),
            "new_prev_7d": q("SELECT COUNT(*) FROM users WHERE consent_version > 0 AND joined_at >= ? AND joined_at < ?",
                             prev, week),
            "active_7d": active(week, now + 1), "active_prev_7d": active(prev, week), "active_30d": active(month, now + 1),
            "opened_7d": q("SELECT COUNT(*) FROM users WHERE consent_version > 0 AND last_seen >= ?", week),
            "rides_7d": rides_7[0], "km_7d": round(rides_7[1], 1),
            "rides_prev_7d": rides_prev[0], "km_prev_7d": round(rides_prev[1], 1),
            "outdated_consent": q("SELECT COUNT(*) FROM users WHERE consent_version > 0 AND consent_version < ?",
                                  policy_version),
            "daily": {
                "since": since,
                "new": per_day("SELECT joined_at, 1 FROM users WHERE consent_version > 0 AND joined_at >= ?"),
                "active": per_day("SELECT MIN(started_at), 1 FROM rides WHERE started_at >= ? "
                                  "GROUP BY user_id, CAST((started_at + ?) / 86400 AS INT)", shift),
                "rides": per_day("SELECT started_at, 1 FROM rides WHERE started_at >= ?"),
            },
            "funnel": [q("SELECT COUNT(*) FROM users WHERE consent_version > 0"),
                       with_rides(1), with_rides(2), with_rides(5)],
            "retention": {"cohort": cohort, "came_back": came_back,
                          "last_week": len(last_week), "both_weeks": len(last_week & this_week)},
            "kinds_30d": {k: {"rides": n, "km": round(km or 0, 1)} for k, n, km in self.conn.execute(
                "SELECT kind, COUNT(*), SUM(distance_km) FROM rides WHERE ended_at IS NOT NULL AND started_at >= ? "
                "GROUP BY kind", (month,))},
            "features": {
                "sessions": q("SELECT COUNT(*) FROM sessions"),
                "sessions_together": q("SELECT COUNT(*) FROM (SELECT session_id FROM session_members "
                                       "GROUP BY session_id HAVING COUNT(*) >= 2)"),
                "joint_rides_30d": q("SELECT COUNT(*) FROM rides WHERE session_id IS NOT NULL AND started_at >= ?", month),
                "sos_users": q("SELECT COUNT(DISTINCT user_id) FROM sos_contacts"),
                "sos_contacts": q("SELECT COUNT(*) FROM sos_contacts"),
                "rating_groups": q("SELECT COUNT(DISTINCT chat_id) FROM group_members"),
                "rating_members": q("SELECT COUNT(DISTINCT user_id) FROM group_members"),
                "ref_links": q("SELECT COUNT(*) FROM users WHERE ref_token IS NOT NULL"),
                "weight_set": q("SELECT COUNT(*) FROM users WHERE consent_version > 0 AND weight_kg != ?", DEFAULT_WEIGHT),
            },
            "counters_30d": dict(self.conn.execute(
                "SELECT name, SUM(n) FROM counters WHERE day >= ? GROUP BY name",
                (time.strftime("%Y-%m-%d", time.gmtime(month)),)).fetchall()),
            "langs": dict(self.conn.execute(
                f"SELECT COALESCE(lang, tg_lang, '{DEFAULT_LANG}'), COUNT(*) FROM users WHERE consent_version > 0 "
                "GROUP BY 1").fetchall()),
            "recent": [dict(r) for r in self.conn.execute(
                "SELECT u.user_id, u.joined_at, r.inviter_id, "
                "(SELECT COUNT(*) FROM rides WHERE rides.user_id = u.user_id) AS rides "
                "FROM users u LEFT JOIN referrals r ON r.user_id = u.user_id "
                "WHERE consent_version > 0 ORDER BY u.joined_at DESC LIMIT 15")],
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

    def set_sos_contact(self, user_id, contact_id, on, lang=None):
        with self.conn:
            if on:
                self.conn.execute(
                    "INSERT INTO sos_contacts(user_id, contact_id, lang) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id, contact_id) DO UPDATE SET lang = excluded.lang",
                    (user_id, contact_id, lang),
                )
            else:
                self.conn.execute(
                    "DELETE FROM sos_contacts WHERE user_id = ? AND contact_id = ?", (user_id, contact_id)
                )

    def contact_lang(self, user_id, contact_id):
        """Язык SOS для контакта: его язык в боте, иначе запомненный, когда он стал контактом."""
        row = self.conn.execute(
            "SELECT COALESCE(u.lang, u.tg_lang, c.lang) FROM sos_contacts c "
            "LEFT JOIN users u ON u.user_id = c.contact_id WHERE c.user_id = ? AND c.contact_id = ?",
            (user_id, contact_id),
        ).fetchone()
        return (row and row[0]) or DEFAULT_LANG

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

    def leave_session(self, session_id, user_id):
        """Выход из сессии: тренировки участника отвязываются; ушёл последний — сессия распускается."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM session_members WHERE session_id = ? AND user_id = ?", (session_id, user_id)
            )
            self.conn.execute(
                "UPDATE rides SET session_id = NULL WHERE session_id = ? AND user_id = ?", (session_id, user_id)
            )
            if not self.session_members(session_id):  # ссылка-приглашение больше не работает
                self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

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
    def add_group(self, chat_id, lang=None):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO groups(chat_id) VALUES (?)", (chat_id,))
            if lang:
                self.conn.execute("UPDATE groups SET lang = ? WHERE chat_id = ?", (lang, chat_id))

    def group_lang(self, chat_id):
        row = self.conn.execute("SELECT lang FROM groups WHERE chat_id = ?", (chat_id,)).fetchone()
        return (row and row["lang"]) or DEFAULT_LANG

    def forget_group(self, chat_id):
        """Бота убрали из группы."""
        with self.conn:
            self.conn.execute("DELETE FROM group_members WHERE chat_id = ?", (chat_id,))
            self.conn.execute("DELETE FROM groups WHERE chat_id = ?", (chat_id,))
            self.conn.execute("DELETE FROM challenges WHERE chat_id = ?", (chat_id,))
            self.conn.execute(
                "DELETE FROM event_rsvp WHERE event_id IN (SELECT id FROM events WHERE chat_id = ?)", (chat_id,)
            )
            self.conn.execute("DELETE FROM events WHERE chat_id = ?", (chat_id,))
            self.conn.execute("DELETE FROM duels WHERE chat_id = ?", (chat_id,))
            self.conn.execute(
                "UPDATE club_wars SET status = 'done' WHERE status != 'done' AND (a_chat = ? OR b_chat = ?)",
                (chat_id, chat_id),
            )

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

    # --- группы: настройки, участники, лента ---
    def group(self, chat_id):
        return self.conn.execute("SELECT * FROM groups WHERE chat_id = ?", (chat_id,)).fetchone()

    def set_group(self, chat_id, **fields):
        """title, tz, reminders, nudged_at — имена колонок только из кода."""
        self.add_group(chat_id)
        with self.conn:
            for column, value in fields.items():
                self.conn.execute(f"UPDATE groups SET {column} = ? WHERE chat_id = ?", (value, chat_id))

    def set_user_tz(self, user_id, tz):
        with self.conn:
            self.conn.execute("UPDATE users SET tz = ? WHERE user_id = ? AND tz IS NOT ?", (tz, user_id, tz))

    def user_tz(self, user_id):
        """Часовой пояс пользователя или None, если он ещё не открывал Mini App."""
        row = self.conn.execute("SELECT tz FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row and row["tz"]

    def is_group_member(self, chat_id, user_id):
        return self.conn.execute(
            "SELECT 1 FROM group_members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchone() is not None

    def group_members(self, chat_id):
        return [r[0] for r in self.conn.execute("SELECT user_id FROM group_members WHERE chat_id = ?", (chat_id,))]

    def member_groups(self, user_id):
        return [r[0] for r in self.conn.execute("SELECT chat_id FROM group_members WHERE user_id = ?", (user_id,))]

    def set_feed(self, chat_id, user_id, on):
        """Публиковать тренировки в группе. Включение заодно включает участие в рейтинге."""
        if on:
            self.set_group_member(chat_id, user_id, True)
        with self.conn:
            self.conn.execute(
                "UPDATE group_members SET feed = ? WHERE chat_id = ? AND user_id = ?", (int(on), chat_id, user_id)
            )

    def feed_groups(self, user_id):
        return [r[0] for r in self.conn.execute(
            "SELECT chat_id FROM group_members WHERE user_id = ? AND feed = 1", (user_id,))]

    def group_km(self, chat_id, since, until, kind=None):
        """{user_id: км} участников рейтинга за [since, until), по завершённым тренировкам."""
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT r.user_id, ROUND(SUM(r.distance_km), 2) FROM rides r "
            "JOIN group_members g ON g.user_id = r.user_id AND g.chat_id = ? "
            "WHERE r.ended_at IS NOT NULL AND r.started_at >= ? AND r.started_at < ? AND (? IS NULL OR r.kind = ?) "
            "GROUP BY r.user_id",
            (chat_id, since, until, kind, kind),
        )}

    def group_rides(self, chat_id, since, until):
        """Завершённые тренировки участников рейтинга за [since, until) — для номинаций и серий."""
        return self.conn.execute(
            "SELECT r.* FROM rides r JOIN group_members g ON g.user_id = r.user_id AND g.chat_id = ? "
            "WHERE r.ended_at IS NOT NULL AND r.started_at >= ? AND r.started_at < ? ORDER BY r.started_at",
            (chat_id, since, until),
        ).fetchall()

    def user_km(self, user_id, since, until, kind=None):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(distance_km), 0) FROM rides WHERE user_id = ? AND ended_at IS NOT NULL "
            "AND started_at >= ? AND started_at < ? AND (? IS NULL OR kind = ?)",
            (user_id, since, until, kind, kind),
        ).fetchone()
        return round(row[0], 2)

    def groups_for_nudge(self):
        return self.conn.execute(
            "SELECT * FROM groups WHERE reminders = 1 AND chat_id IN (SELECT chat_id FROM group_members)"
        ).fetchall()

    # --- челленджи ---
    def create_challenge(self, chat_id, creator_id, goal_km, kind, each, days, now=None):
        now = now or time.time()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO challenges(chat_id, creator_id, goal_km, kind, each, starts_at, ends_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chat_id, creator_id, goal_km, kind, int(each), now, now + days * 86400),
            )
        return self.get_challenge(cur.lastrowid)

    def get_challenge(self, challenge_id):
        return self.conn.execute("SELECT * FROM challenges WHERE id = ?", (challenge_id,)).fetchone()

    def active_challenge(self, chat_id):
        return self.conn.execute(
            "SELECT * FROM challenges WHERE chat_id = ? AND closed = 0 ORDER BY id DESC LIMIT 1", (chat_id,)
        ).fetchone()

    def update_challenge(self, challenge_id, **fields):
        """message_id, reached_at, closed."""
        with self.conn:
            for column, value in fields.items():
                self.conn.execute(f"UPDATE challenges SET {column} = ? WHERE id = ?", (value, challenge_id))

    def challenges_due(self, now):
        return self.conn.execute("SELECT * FROM challenges WHERE closed = 0 AND ends_at <= ?", (now,)).fetchall()

    # --- сборы ---
    def create_event(self, chat_id, creator_id, starts_at, tz, place):
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO events(chat_id, creator_id, starts_at, tz, place) VALUES (?, ?, ?, ?, ?)",
                (chat_id, creator_id, starts_at, tz, place),
            )
        return self.get_event(cur.lastrowid)

    def get_event(self, event_id):
        return self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()

    def update_event(self, event_id, **fields):
        """message_id, session_id, stage."""
        with self.conn:
            for column, value in fields.items():
                self.conn.execute(f"UPDATE events SET {column} = ? WHERE id = ?", (value, event_id))

    def set_rsvp(self, event_id, user_id, answer):
        with self.conn:
            self.conn.execute(
                "INSERT INTO event_rsvp(event_id, user_id, answer) VALUES (?, ?, ?) "
                "ON CONFLICT(event_id, user_id) DO UPDATE SET answer = excluded.answer",
                (event_id, user_id, answer),
            )

    def rsvps(self, event_id):
        """{answer: [user_id]} в порядке ответов."""
        out = {}
        for user_id, answer in self.conn.execute(
            "SELECT user_id, answer FROM event_rsvp WHERE event_id = ? ORDER BY rowid", (event_id,)
        ):
            out.setdefault(answer, []).append(user_id)
        return out

    def events_due(self, now, remind_before, summary_after):
        """[(событие, следующий этап)]: напомнить, начать, подвести итоги."""
        return [(e, stage) for e, stage in (
            [(e, 1) for e in self.conn.execute(
                "SELECT * FROM events WHERE stage = 0 AND starts_at - ? <= ? AND starts_at > ?",
                (remind_before, now, now))]
            + [(e, 2) for e in self.conn.execute(
                "SELECT * FROM events WHERE stage IN (0, 1) AND starts_at <= ?", (now,))]
            + [(e, 3) for e in self.conn.execute(
                "SELECT * FROM events WHERE stage = 2 AND starts_at + ? <= ?", (summary_after, now))]
        )]

    # --- дуэли ---
    def create_duel(self, chat_id, a_id, b_id, kind, days, now=None):
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO duels(chat_id, a_id, b_id, kind, days, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, a_id, b_id, kind, days, now or time.time()),
            )
        return self.get_duel(cur.lastrowid)

    def get_duel(self, duel_id):
        return self.conn.execute("SELECT * FROM duels WHERE id = ?", (duel_id,)).fetchone()

    def update_duel(self, duel_id, **fields):
        """b_id, starts_at, ends_at, message_id, status."""
        with self.conn:
            for column, value in fields.items():
                self.conn.execute(f"UPDATE duels SET {column} = ? WHERE id = ?", (value, duel_id))

    def user_duels(self, user_id):
        return self.conn.execute(
            "SELECT * FROM duels WHERE status = 'active' AND (a_id = ? OR b_id = ?)", (user_id, user_id)
        ).fetchall()

    def duels_due(self, now, open_ttl):
        """Идущие дуэли, которым пора подвести итог, и непринятые вызовы старше open_ttl."""
        return self.conn.execute(
            "SELECT * FROM duels WHERE (status = 'active' AND ends_at <= ?) OR (status = 'open' AND created_at <= ?)",
            (now, now - open_ttl),
        ).fetchall()

    # --- битва клубов ---
    def create_war(self, chat_id, days, now=None):
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO club_wars(code, a_chat, days, created_at) VALUES (?, ?, ?, ?)",
                ("".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(6)), chat_id, days,
                 now or time.time()),  # только буквы: число в /clubwar — это дни
            )
        return self.get_war(cur.lastrowid)

    def get_war(self, war_id):
        return self.conn.execute("SELECT * FROM club_wars WHERE id = ?", (war_id,)).fetchone()

    def war_by_code(self, code):
        return self.conn.execute(
            "SELECT * FROM club_wars WHERE code = ? AND status = 'open'", ((code or "").upper(),)
        ).fetchone()

    def update_war(self, war_id, **fields):
        """b_chat, starts_at, ends_at, a_msg, b_msg, status."""
        with self.conn:
            for column, value in fields.items():
                self.conn.execute(f"UPDATE club_wars SET {column} = ? WHERE id = ?", (value, war_id))

    def chat_wars(self, chat_id):
        return self.conn.execute(
            "SELECT * FROM club_wars WHERE status = 'active' AND (a_chat = ? OR b_chat = ?)", (chat_id, chat_id)
        ).fetchall()

    def wars_due(self, now, open_ttl):
        return self.conn.execute(
            "SELECT * FROM club_wars WHERE (status = 'active' AND ends_at <= ?) OR (status = 'open' AND created_at <= ?)",
            (now, now - open_ttl),
        ).fetchall()

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

    def last_finished_ride(self, user_id):
        return self.conn.execute(
            "SELECT * FROM rides WHERE user_id = ? AND ended_at IS NOT NULL ORDER BY started_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

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
        """[(ts, lat, lon, accuracy)] по времени. У велотренировки — привязанные к дорогам, где привязка есть."""
        rows = self.conn.execute(
            "SELECT p.ts, p.lat, p.lon, p.accuracy, p.snap_lat, p.snap_lon, r.kind FROM points p "
            "JOIN rides r ON r.id = p.ride_id WHERE p.ride_id = ? ORDER BY p.ts",
            (ride_id,),
        ).fetchall()
        return [
            (r["ts"], r["snap_lat"], r["snap_lon"], r["accuracy"])
            if r["kind"] == "ride" and r["snap_lat"] is not None else (r["ts"], r["lat"], r["lon"], r["accuracy"])
            for r in rows
        ]

    def raw_points(self, ride_id):
        """[(ts, lat, lon, accuracy, rowid)] как пришли из Telegram — для привязки к дорогам."""
        return [tuple(r) for r in self.conn.execute(
            "SELECT ts, lat, lon, accuracy, rowid FROM points WHERE ride_id = ? ORDER BY ts", (ride_id,))]

    def snap_ride(self, ride_id, snapped, max_change):
        """Привязка к дорогам: snapped — [(rowid, lat, lon)]. Если дистанция от неё изменилась больше чем
        на max_change (доля), привязке не верим и оставляем исходный трек. True — привязка принята."""
        ride = self.get_ride(ride_id)
        before = compute_stats(self.get_points(ride_id), 0, ride["kind"])["distance_km"]
        with self.conn:
            self.conn.executemany(
                "UPDATE points SET snap_lat = ?, snap_lon = ? WHERE rowid = ? AND ride_id = ?",
                [(lat, lon, rowid, ride_id) for rowid, lat, lon in snapped],
            )
            after = compute_stats(self.get_points(ride_id), 0, ride["kind"])["distance_km"]
            if not before or abs(after - before) / before > max_change:
                self.conn.execute("UPDATE points SET snap_lat = NULL, snap_lon = NULL WHERE ride_id = ?", (ride_id,))
                return False
            self._save_stats(ride_id)
        return True

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
