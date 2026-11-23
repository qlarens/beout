"""Подсчёт статистики поездки: дистанция, время, скорость, калории."""
import math

EARTH_RADIUS_M = 6_371_000.0

# MET для велосипеда по средней скорости (Compendium of Physical Activities).
# (верхняя граница скорости в км/ч, MET)
MET_BY_SPEED = [
    (16.1, 4.0),    # < 16 км/ч — спокойная езда
    (19.3, 6.8),    # 16–19 км/ч
    (22.5, 8.0),    # 19–22 км/ч
    (25.7, 10.0),   # 22–26 км/ч
    (32.2, 12.0),   # 26–32 км/ч
    (math.inf, 15.8),  # > 32 км/ч — гоночный темп
]

MIN_MOVING_SPEED_KMH = 3.0   # медленнее — считаем остановкой
MAX_PLAUSIBLE_SPEED_KMH = 70.0  # быстрее — скорее всего, скачок GPS


def haversine_m(lat1, lon1, lat2, lon2):
    """Расстояние между двумя точками на сфере, в метрах."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def met_for_speed(speed_kmh):
    for limit, met in MET_BY_SPEED:
        if speed_kmh < limit:
            return met
    return MET_BY_SPEED[-1][1]


def compute_stats(points, weight_kg):
    """points: список (ts, lat, lon), отсортированный по времени.

    Калории считаются по каждому отрезку: MET(скорость) × вес × часы.
    Остановки (светофоры, перекусы) в калории и время в движении не входят.
    """
    distance_m = 0.0
    moving_s = 0.0
    kcal = 0.0
    max_speed = 0.0

    if not points:
        points = []
    prev = points[0] if points else None
    for cur in points[1:]:
        t1, la1, lo1 = prev
        t2, la2, lo2 = cur
        dt = t2 - t1
        if dt <= 0:
            continue
        d = haversine_m(la1, lo1, la2, lo2)
        speed = d / dt * 3.6
        if speed > MAX_PLAUSIBLE_SPEED_KMH:
            continue  # выброс GPS — выкидываем точку, сравниваем следующую с prev
        prev = cur
        if speed < MIN_MOVING_SPEED_KMH:
            continue  # стоим
        distance_m += d
        moving_s += dt
        max_speed = max(max_speed, speed)
        kcal += met_for_speed(speed) * weight_kg * dt / 3600

    duration_s = (points[-1][0] - points[0][0]) if len(points) >= 2 else 0
    avg_speed = (distance_m / moving_s * 3.6) if moving_s > 0 else 0.0

    return {
        "distance_km": round(distance_m / 1000, 2),
        "duration_s": int(duration_s),
        "moving_s": int(moving_s),
        "avg_speed_kmh": round(avg_speed, 1),
        "max_speed_kmh": round(max_speed, 1),
        "kcal": int(round(kcal)),
    }
