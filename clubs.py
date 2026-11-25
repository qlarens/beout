"""Активности в группах без Telegram и базы: разбор команд, прогресс, номинации недели.

Время сборов — по часовому поясу как в Date.getTimezoneOffset(): tz = UTC − местное, в минутах.
"""
import re
from datetime import datetime, timedelta, timezone

DAY = 86400

KIND_WORDS = {
    "ride": ("вело", "велосипед", "велопрогулка", "велотренування", "ride", "bike", "cycling"),
    "run": ("бег", "біг", "пробежка", "пробіжка", "run", "running"),
    "walk": ("ходьба", "прогулка", "walk", "walking"),
}
EACH_WORDS = ("каждый", "каждому", "кожен", "кожному", "each", "everyone")
DAY_WORDS = re.compile(r"^(д|дн|дня|дней|день|днів|дні|d|day|days)$", re.I)
KM_WORDS = ("км", "km")
WEEKDAYS = {  # понедельник = 0
    0: ("пн", "понедельник", "понеділок", "mon", "monday"),
    1: ("вт", "вторник", "вівторок", "tue", "tuesday"),
    2: ("ср", "среда", "середа", "wed", "wednesday"),
    3: ("чт", "четверг", "четвер", "thu", "thursday"),
    4: ("пт", "пятница", "п'ятниця", "п’ятниця", "fri", "friday"),
    5: ("сб", "суббота", "субота", "sat", "saturday"),
    6: ("вс", "нд", "воскресенье", "неділя", "sun", "sunday"),
}
TODAY = ("сегодня", "сьогодні", "today")
TOMORROW = ("завтра", "tomorrow")
MAX_EVENT_AHEAD_DAYS = 60


def kind_of(word):
    word = word.lower().strip(".,!")
    return next((k for k, words in KIND_WORDS.items() if word in words), None)


def parse_challenge(args):
    """«300», «300 км бег», «каждый 20 км», «100 км 14 дней» → (цель км, тип|None, каждому, дней) или None."""
    goal = kind = None
    each, days = False, 7
    tokens = (args or "").replace(",", ".").split()
    for i, tok in enumerate(tokens):
        low = tok.lower()
        if re.fullmatch(r"\d+(\.\d+)?", low):
            if i + 1 < len(tokens) and DAY_WORDS.match(tokens[i + 1]):
                days = int(float(low))
            elif goal is None:
                goal = float(low)
        elif m := re.fullmatch(r"(\d+(?:\.\d+)?)(км|km)", low):
            goal = float(m.group(1))
        elif low in EACH_WORDS:
            each = True
        elif kind_of(low):
            kind = kind_of(low)
        elif low in KM_WORDS or DAY_WORDS.match(low):
            continue
        else:
            return None
    if goal is None or not 1 <= goal <= 100_000 or not 1 <= days <= 31:
        return None
    return goal, kind, each, days


def parse_event(args, now, tz):
    """«сб 9:00 Парк Шевченко», «завтра 7.30 стадион», «29.11 18:00 набережная» → (начало UTC, описание).

    Без даты — сегодня, а если время уже прошло — завтра. None — не разобрали или слишком далеко.
    """
    tokens = (args or "").split()
    local_now = datetime.fromtimestamp(now - tz * 60, timezone.utc).replace(tzinfo=None)
    # Время: «9:00» важнее «9.00» — «01.12 9:00» это 1 декабря в 9 утра.
    times = [(i, re.fullmatch(r"(\d{1,2})([:.])(\d{2})", t.strip(","))) for i, t in enumerate(tokens)]
    times = [(i, m) for i, m in times if m and int(m[1]) < 24 and int(m[3]) < 60]
    pick = [x for x in times if x[1][2] == ":"] or times[-1:]
    if not pick:
        return None
    clock_i, m = pick[0]
    clock = (int(m[1]), int(m[3]))
    date = None
    rest = []
    for i, tok in enumerate(tokens):
        low = tok.lower().strip(",")
        if i == clock_i:
            continue
        if date is None and i < clock_i and (m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", low)):
            year = int(m[3]) if m[3] else local_now.year
            year += 2000 if year < 100 else 0
            try:
                date = datetime(year, int(m[2]), int(m[1])).date()
            except ValueError:
                return None
            if not m[3] and date < local_now.date():
                date = date.replace(year=year + 1)
        elif date is None and i < clock_i and low in TODAY:
            date = local_now.date()
        elif date is None and i < clock_i and low in TOMORROW:
            date = local_now.date() + timedelta(days=1)
        elif date is None and i < clock_i and (wd := next((d for d, w in WEEKDAYS.items() if low in w), None)) is not None:
            date = local_now.date() + timedelta(days=(wd - local_now.weekday()) % 7)
        else:
            rest.append(tok)
    if not rest:
        return None
    if date is None:
        date = local_now.date()
        if (clock[0], clock[1]) <= (local_now.hour, local_now.minute):
            date += timedelta(days=1)
    start = datetime(date.year, date.month, date.day, *clock, tzinfo=timezone.utc).timestamp() + tz * 60
    if start <= now or start > now + MAX_EVENT_AHEAD_DAYS * DAY:
        return None
    return start, " ".join(rest)


def local(ts, tz):
    return datetime.fromtimestamp(ts - tz * 60, timezone.utc)


def tz_label(tz):
    """«UTC+2», «UTC−3:30»."""
    minutes = -tz
    sign = "+" if minutes >= 0 else "−"
    h, m = divmod(abs(minutes), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def progress_bar(done, goal, width=10):
    frac = max(0.0, min(1.0, done / goal)) if goal else 0.0
    full = round(frac * width)
    return "▓" * full + "░" * (width - full) + f" {round(frac * 100)}%"


def fmt_km(km):
    return f"{km:.1f}".rstrip("0").rstrip(".") if km < 100 else f"{km:.0f}"


def nominations(rides, prev_km, tz_of):
    """Номинации недели по тренировкам участников. rides — строки с user_id, started_at, distance_km;
    prev_km — {user_id: км прошлой недели}; tz_of(user_id) — его часовой пояс.
    Возвращает [(ключ, user_id, значение)] в порядке показа; без кандидатов — без номинации."""
    out = []
    real = [r for r in rides if (r["distance_km"] or 0) >= 1]
    if real:
        longest = max(real, key=lambda r: r["distance_km"])
        out.append(("nom_longest", longest["user_id"], fmt_km(longest["distance_km"])))

        def minutes(r):
            t = local(r["started_at"], tz_of(r["user_id"]))
            return t.hour * 60 + t.minute
        early = min(real, key=minutes)
        if minutes(early) < 9 * 60:  # «ранняя пташка» — до девяти утра
            out.append(("nom_early", early["user_id"], f"{minutes(early) // 60}:{minutes(early) % 60:02d}"))
    days = {}
    km = {}
    for r in real:
        days.setdefault(r["user_id"], set()).add(local(r["started_at"], tz_of(r["user_id"])).date())
        km[r["user_id"]] = km.get(r["user_id"], 0) + r["distance_km"]
    if days:
        user, d = max(days.items(), key=lambda kv: (len(kv[1]), km[kv[0]]))
        if len(d) >= 2:
            out.append(("nom_steady", user, len(d)))
    growth = {u: km[u] - prev_km[u] for u in km if prev_km.get(u, 0) > 0 and km[u] > prev_km[u]}
    if growth:
        user = max(growth, key=growth.get)
        out.append(("nom_progress", user, fmt_km(growth[user])))
    return out
