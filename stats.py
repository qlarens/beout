"""Подсчёт статистики тренировки: дистанция, время, скорость, темп, сплиты, калории."""
import math
from bisect import bisect_left

from settings import ACTIVITIES, AUTOPAUSE_AFTER_S, DEFAULT_ACTIVITY, MIN_MOVE_M, SMOOTH_SEGMENTS

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1, lon1, lat2, lon2):
    """Расстояние между двумя точками на сфере, в метрах."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def met_for_speed(speed_kmh, kind=DEFAULT_ACTIVITY):
    for limit, met in ACTIVITIES[kind]["met"]:
        if speed_kmh < limit:
            return met
    return ACTIVITIES[kind]["met"][-1][1]


def compute_stats(points, weight_kg, kind=DEFAULT_ACTIVITY):
    """points: список (ts, lat, lon), отсортированный по времени.

    Калории считаются по каждому отрезку: MET(тип, скорость) × вес × часы.
    Остановки (светофоры, перекусы) после AUTOPAUSE_AFTER_S в дистанцию,
    калории и время в движении не входят. Сплиты и лучшие 1/5 км — по времени в движении.
    """
    limits = ACTIVITIES[kind]
    kcal = 0.0
    slow_s = 0.0  # сколько секунд подряд медленнее pause_kmh
    # Накопленные дистанция (м) и время в движении (с) после каждого засчитанного отрезка.
    cum_d, cum_t = [0.0], [0.0]
    track = [points[0][1:]] if points else []  # маршрут без выбросов GPS — для карты и карточки

    prev = points[0] if points else None
    for cur in points[1:]:
        t1, la1, lo1 = prev
        t2, la2, lo2 = cur
        dt = t2 - t1
        if dt <= 0:
            continue
        d = haversine_m(la1, lo1, la2, lo2)
        speed = d / dt * 3.6
        if speed > limits["max_kmh"]:
            continue  # выброс GPS — выкидываем точку, сравниваем следующую с prev
        if d < MIN_MOVE_M:
            # Дрожание на месте: место prev не двигаем, время — да, чтобы первый отрезок
            # после стоянки считался по своей скорости, а не растягивался на всю стоянку.
            # shortcut: дрожание больше MIN_MOVE_M у ходьбы сойдёт за движение — сглаживать, если будут жалобы.
            prev = (t2, la1, lo1)
            continue
        prev = cur
        track.append(cur[1:])
        slow_s = slow_s + dt if speed < limits["pause_kmh"] else 0.0
        if slow_s > AUTOPAUSE_AFTER_S:
            continue  # стоим
        cum_d.append(cum_d[-1] + d)
        cum_t.append(cum_t[-1] + dt)
        kcal += met_for_speed(speed, kind) * weight_kg * dt / 3600

    distance_m, moving_s = cum_d[-1], cum_t[-1]
    n = len(cum_d) - 1
    # Максимум — по скользящему окну из SMOOTH_SEGMENTS отрезков (или всем, если их меньше).
    w = min(SMOOTH_SEGMENTS, n)
    max_speed = max(
        ((cum_d[i] - cum_d[i - w]) / (cum_t[i] - cum_t[i - w]) * 3.6 for i in range(w, n + 1)),
        default=0.0,
    ) if w else 0.0

    def time_at(dist):
        """Время в движении, когда пройдено dist метров (линейно внутри отрезка)."""
        i = min(bisect_left(cum_d, dist), n)  # dist чуть больше всей дистанции — ошибка округления
        if i == 0:
            return 0.0
        d0, d1, t0, t1 = cum_d[i - 1], cum_d[i], cum_t[i - 1], cum_t[i]
        return t0 + (t1 - t0) * (dist - d0) / (d1 - d0)

    def best_effort(dist):
        """Самый быстрый непрерывный отрезок длиной dist: окно, прижатое к началу или концу отрезка."""
        if distance_m < dist:
            return None
        ends = [cum_t[j] - time_at(cum_d[j] - dist) for j in range(n + 1) if cum_d[j] >= dist]
        starts = [time_at(cum_d[i] + dist) - cum_t[i] for i in range(n + 1) if cum_d[i] + dist <= distance_m]
        return int(round(min(ends + starts)))

    km_marks = [time_at(k * 1000) for k in range(int(round(distance_m, 1) // 1000) + 1)]
    duration_s = (points[-1][0] - points[0][0]) if len(points) >= 2 else 0
    return {
        "distance_km": round(distance_m / 1000, 2),
        "duration_s": int(duration_s),
        "moving_s": int(moving_s),
        "avg_speed_kmh": round(distance_m / moving_s * 3.6, 1) if moving_s else 0.0,
        "max_speed_kmh": round(max_speed, 1),
        "kcal": int(round(kcal)),
        "best_1k_s": best_effort(1000),
        "best_5k_s": best_effort(5000),
        "splits": [int(round(b - a)) for a, b in zip(km_marks, km_marks[1:])],  # секунды на каждый полный км
        "track": track,  # [(lat, lon)]
    }


# --- форматирование для сообщений бота и карточки ---

def fmt_duration(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_pace(speed_kmh):
    """Темп «5:30 /км» из скорости; без движения — прочерк."""
    if not speed_kmh:
        return "–"
    return f"{fmt_duration(round(3600 / speed_kmh))} /км"
