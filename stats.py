"""Подсчёт статистики тренировки: дистанция, время, скорость, сплиты, калории."""
import heapq
import math
from bisect import bisect_left, bisect_right
from datetime import timedelta

from settings import (
    ACCURACY_OUTLIER_M,
    ACCURACY_OUTLIER_RATIO,
    ACTIVITIES,
    AUTOPAUSE_AFTER_S,
    DEFAULT_ACCURACY_M,
    DEFAULT_ACTIVITY,
    JITTER_MAX_WINDOW_S,
    JITTER_SIGMAS,
    JITTER_WINDOW_S,
    JUMP_NEIGHBOURS,
    MAX_SPEED_MIN_M,
    MAX_SPEED_WINDOW_S,
    MIN_MOVE_M,
    RESIDUAL_SIGMAS,
    STOP_EDGE_SIGMAS,
    STOP_FLICKER_S,
    STOP_WINDOW_S,
    SPIKE_MIN_DETOUR_M,
    SPIKE_SPEED_RATIO,
    VIA_MAX_RATIO,
    VIA_SLACK_M,
)

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


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[(n - 1) // 2] + xs[n // 2]) / 2 if xs else 0.0


def _accuracy(p):
    acc = p[3] if len(p) > 3 else None
    return max(acc, MIN_MOVE_M) if acc else DEFAULT_ACCURACY_M


def drop_jumps(points, max_kmh, k=JUMP_NEIGHBOURS):
    """Скачки GPS: до точки не добраться быстрее max_kmh от большинства соседей (до k с каждой стороны),
    даже если обе точки сдвинуть к друг другу на их погрешность. Убираем по одной, начиная с худшей, и
    пересчитываем соседей. В отличие от проверки «от предыдущей принятой точки» одна плохая точка не тянет
    за собой хорошие: если ошибочна первая (Telegram часто шлёт старую позицию), уйдёт она, а не начало трека."""
    n, max_ms = len(points), max_kmh / 3.6
    prev, nxt, alive, version = list(range(-1, n - 1)), list(range(1, n + 1)), [True] * n, [0] * n

    def reachable(i, j):
        a, b = points[i], points[j]
        gap = haversine_m(a[1], a[2], b[1], b[2]) - _accuracy(a) - _accuracy(b)
        return gap <= max_ms * abs(b[0] - a[0])

    def neighbours(i):
        out = []
        for links, j in ((prev, prev[i]), (nxt, nxt[i])):
            for _ in range(k):
                if not 0 <= j < n:
                    break
                out.append(j)
                j = links[j]
        return out

    def entry(i):
        near = neighbours(i)
        share = sum(reachable(i, j) for j in near) / len(near) if near else 1.0
        return share, -_accuracy(points[i]), i, version[i]  # при равенстве — сначала менее точную

    heap = [entry(i) for i in range(n)]
    heapq.heapify(heap)
    while heap:
        share, _, i, ver = heapq.heappop(heap)
        if not alive[i] or ver != version[i]:
            continue
        if share >= 0.5:
            break
        near = neighbours(i)
        alive[i] = False
        if prev[i] >= 0:
            nxt[prev[i]] = nxt[i]
        if nxt[i] < n:
            prev[nxt[i]] = prev[i]
        for j in near:
            version[j] += 1
            heapq.heappush(heap, entry(j))
    return [p for p, ok in zip(points, alive) if ok]


def clean(points, kind=DEFAULT_ACTIVITY):
    """Отсев выбросов GPS. points — (ts, lat, lon[, accuracy]) по времени; возвращает оставшиеся как есть.

    1. Погрешность намного хуже соседей — точка из «плохого» момента (здание, тоннель).
    2. Нереальная скорость до большинства соседей, даже с учётом погрешности, — скачок (drop_jumps).
    3. Одна точка «туда и обратно» намного быстрее обычного темпа — выброс. Настоящий разворот
       (бегун у конуса) идёт с обычной скоростью и остаётся.
    """
    limits = ACTIVITIES[kind]
    good = []
    for i, p in enumerate(points):
        acc = p[3] if len(p) > 3 else None
        near = [q[3] for q in points[max(0, i - 5):i] + points[i + 1:i + 6] if len(q) > 3 and q[3]]
        if acc and near and acc > ACCURACY_OUTLIER_M and acc > ACCURACY_OUTLIER_RATIO * _median(near):
            continue
        if good and p[0] <= good[-1][0]:  # то же время — оставляем более точную
            if _accuracy(p) < _accuracy(good[-1]):
                good[-1] = p
            continue
        good.append(p)

    steady = drop_jumps(good, limits["max_kmh"])

    def kmh(a, b):
        return haversine_m(a[1], a[2], b[1], b[2]) / (b[0] - a[0]) * 3.6

    typical = _median([kmh(a, b) for a, b in zip(steady, steady[1:])])
    out = []
    for p in steady:
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            detour = (haversine_m(a[1], a[2], b[1], b[2]) + haversine_m(b[1], b[2], p[1], p[2])
                      - haversine_m(a[1], a[2], p[1], p[2]))
            if (detour > max(SPIKE_MIN_DETOUR_M, 3 * _accuracy(b))
                    and min(kmh(a, b), kmh(b, p)) > SPIKE_SPEED_RATIO * max(typical, limits["pause_kmh"])):
                out.pop()
        out.append(p)
    return out


def smooth(points, agility):
    """Сглаживание Калмана (модель «постоянная скорость») вперёд и назад (RTS) по каждой оси.

    Шум измерения — погрешность точки: неточная точка почти не сдвигает трек, точная — тянет к себе.
    agility — спектральная плотность ускорения, м²/с³: насколько резко может меняться скорость.
    Возвращает [(lat, lon, дисперсия положения м², скорость м/с)].
    """
    lat0, lon0 = points[0][1], points[0][2]
    m_per_deg = math.radians(1) * EARTH_RADIUS_M
    k_lon = m_per_deg * math.cos(math.radians(lat0))
    ts = [p[0] for p in points]
    r = [_accuracy(p) ** 2 for p in points]
    out_axes = []
    for zs in ([(p[2] - lon0) * k_lon for p in points], [(p[1] - lat0) * m_per_deg for p in points]):
        # Состояние (положение, скорость), ковариация — [[a, b], [b, c]].
        x, v, pa, pb, pc = zs[0], 0.0, r[0], 0.0, 1e4  # скорость поначалу неизвестна совсем
        filt, pred = [], []
        for k, z in enumerate(zs):
            if k:
                dt = ts[k] - ts[k - 1]
                x = x + v * dt
                pa, pb, pc = (pa + 2 * dt * pb + dt * dt * pc + agility * dt ** 3 / 3,
                              pb + dt * pc + agility * dt * dt / 2,
                              pc + agility * dt)
            pred.append((x, v, pa, pb, pc))
            s = pa + r[k]
            ka, kb = pa / s, pb / s
            y = z - x
            x, v = x + ka * y, v + kb * y
            pa, pb, pc = (1 - ka) * pa, (1 - ka) * pb, pc - kb * pb
            filt.append((x, v, pa, pb, pc))
        sm = [None] * len(zs)
        sm[-1] = filt[-1]
        for k in range(len(zs) - 2, -1, -1):
            dt = ts[k + 1] - ts[k]
            fx, fv, fa, fb, fc = filt[k]
            px, pv, qa, qb, qc = pred[k + 1]
            sx, sv, sa, sb, sc = sm[k + 1]
            # C = Pf · Fᵀ · Pp⁻¹
            fa_, fb_ = fa + dt * fb, fb  # Pf·Fᵀ, первая строка: (a + dt·b, b)
            fc1, fc2 = fb + dt * fc, fc  # вторая строка
            det = qa * qc - qb * qb
            ia, ib, ic = qc / det, -qb / det, qa / det
            c11, c12 = fa_ * ia + fb_ * ib, fa_ * ib + fb_ * ic
            c21, c22 = fc1 * ia + fc2 * ib, fc1 * ib + fc2 * ic
            dx, dv = sx - px, sv - pv
            da, db, dc = sa - qa, sb - qb, sc - qc
            nx, nv = fx + c11 * dx + c12 * dv, fv + c21 * dx + c22 * dv
            na = fa + c11 * (c11 * da + c12 * db) + c12 * (c11 * db + c12 * dc)
            nb = fb + c11 * (c21 * da + c22 * db) + c12 * (c21 * db + c22 * dc)
            nc = fc + c21 * (c21 * da + c22 * db) + c22 * (c21 * db + c22 * dc)
            sm[k] = (nx, nv, na, nb, nc)
        out_axes.append([(st[0], st[2], st[1]) for st in sm])
    return [(lat0 + y / m_per_deg, lon0 + x / k_lon, px + py, math.hypot(ux, uy))
            for (x, px, ux), (y, py, uy) in zip(*out_axes)]


def around(ts, i, span):
    """Окно вокруг отрезка i-1 → i: индексы (lo, hi) ближайших точек снаружи [середина ± span/2], так что окно
    охватывает не меньше span секунд, даже если точки редкие (у края трека — сдвигаем внутрь)."""
    n, mid = len(ts), (ts[i - 1] + ts[i]) / 2
    lo = min(max(bisect_right(ts, mid - span / 2) - 1, 0), i - 1)
    hi = max(min(bisect_left(ts, mid + span / 2), n - 1), i)
    if lo == 0:
        hi = max(hi, min(bisect_left(ts, ts[0] + span), n - 1))
    if hi == n - 1:
        lo = min(lo, max(bisect_right(ts, ts[-1] - span) - 1, 0))
    return lo, hi


def local_speeds(points, window_s):
    """Скорость у каждой отрезка, км/ч: прямая по сырым точкам за ±window_s/2 вокруг его середины
    (наименьшие квадраты, вес точки — 1/погрешность²). В отличие от сглаженного трека остановка
    размывается на секунды, а не на десятки секунд — по ней и решаем, едем или стоим."""
    lat0, lon0 = points[0][1], points[0][2]
    m_per_deg = math.radians(1) * EARTH_RADIUS_M
    k_lon = m_per_deg * math.cos(math.radians(lat0))
    xs = [((p[2] - lon0) * k_lon, (p[1] - lat0) * m_per_deg, 1 / _accuracy(p) ** 2) for p in points]
    ts = [p[0] for p in points]
    out = [0.0]
    for i in range(1, len(points)):
        mid = (ts[i] + ts[i - 1]) / 2
        lo = min(bisect_left(ts, mid - window_s / 2), i - 1)
        hi = max(bisect_right(ts, mid + window_s / 2) - 1, i)
        w = sum(xs[k][2] for k in range(lo, hi + 1))
        tm = sum(xs[k][2] * ts[k] for k in range(lo, hi + 1)) / w
        stt = sum(xs[k][2] * (ts[k] - tm) ** 2 for k in range(lo, hi + 1))
        v = [sum(xs[k][2] * (ts[k] - tm) * xs[k][axis] for k in range(lo, hi + 1)) / stt for axis in (0, 1)]
        out.append(math.hypot(*v) * 3.6)
    return out


def _via(a, b, max_kmh):
    """Путь по дорогам от точки a до b после привязки: (вершины между ними, на сколько он длиннее хорды).
    (None, 0) — пути нет (не привязана, путь от другой точки) или он неправдоподобен: петля, быстрее max_kmh."""
    if len(b) < 5 or not b[4] or b[4][0] != a[0]:
        return None, 0.0
    nodes = [a[1:3], *b[4][1], b[1:3]]
    length = sum(haversine_m(*u, *v) for u, v in zip(nodes, nodes[1:]))
    chord = haversine_m(a[1], a[2], b[1], b[2])
    if length > VIA_MAX_RATIO * chord + VIA_SLACK_M or length / (b[0] - a[0]) * 3.6 > max_kmh:
        return None, 0.0
    return b[4][1], length - chord


def compute_stats(points, weight_kg, kind=DEFAULT_ACTIVITY):
    """points: список (ts, lat, lon[, accuracy[, via]]), отсортированный по времени. Пятое поле есть у точек,
    привязанных к дорогам: None или (ts предыдущей привязанной точки, [(lat, lon)] — путь по дорогам от неё).

    Порядок: выбросы GPS → сглаживание Калмана с учётом погрешности → для каждого отрезка смотрим окна
    вокруг него. Сместились за JITTER_WINDOW_S меньше шума — стоим; скорость — по пути за SPEED_WINDOW_S.
    Стоянка или медленнее pause_kmh дольше AUTOPAUSE_AFTER_S — автопауза. Калории по каждому отрезку:
    MET(тип, скорость окна) × вес × часы. Сплиты и лучшие 1/5 км — по времени в движении.
    """
    limits = ACTIVITIES[kind]
    duration_s = (points[-1][0] - points[0][0]) if len(points) >= 2 else 0
    pts = clean(points, kind)
    sm = smooth(pts, limits["agility"]) if pts else []
    # Выброс вдоль трека («скакнула вперёд и вернулась») крюка не даёт — его видно по сглаженному треку:
    # точка дальше RESIDUAL_SIGMAS погрешностей от него. Убираем такие и сглаживаем заново.
    keep = [p for p, q in zip(pts, sm)
            if haversine_m(p[1], p[2], q[0], q[1]) <= max(SPIKE_MIN_DETOUR_M, RESIDUAL_SIGMAS * _accuracy(p))]
    if 1 < len(keep) < len(pts):
        pts, sm = keep, smooth(keep, limits["agility"])
    ts = [p[0] for p in pts]
    n_pts = len(pts)
    seg = [0.0] + [haversine_m(sm[i - 1][0], sm[i - 1][1], sm[i][0], sm[i][1]) for i in range(1, n_pts)]
    # После привязки к дорогам между соседними точками известен путь по ним: на поворотах между редкими точками
    # он длиннее хорды — добавляем разницу к отрезку сглаженного трека, а на карте рисуем его изгибы, а не прямую.
    # Привязанную точку рисуем как есть: она уже на дороге, а сглаживание срезало бы угол мимо неё.
    via = [None] * n_pts
    for i in range(1, n_pts):
        via[i], excess = _via(pts[i - 1], pts[i], limits["max_kmh"])
        seg[i] += excess
    shown = [tuple(p[1:3]) if len(p) > 4 else q[:2] for p, q in zip(pts, sm)]

    # Каждый отрезок: стоим (за JITTER_WINDOW_S сместились в пределах шума), медленно (скорость по точкам
    # за STOP_WINDOW_S ниже pause_kmh) или едем. Скорость — по нескольким точкам: ошибка целых секунд не копится.
    # Если точки неточные, за 40 с и медленный ход тонет в шуме — окно удлиняем, пока ход на slow_kmh не стал бы
    # заметен (до JITTER_MAX_WINDOW_S): иначе ходьбу с телефоном в кармане приняли бы за стоянку.
    status, speeds, reach = [None], [0.0], [0.0]
    local = local_speeds(pts, STOP_WINDOW_S) if pts else []
    slow_ms = limits["slow_kmh"] / 3.6
    for i in range(1, n_pts):
        span = JITTER_WINDOW_S
        for _ in range(4):
            lo, hi = around(ts, i, span)
            moved = haversine_m(sm[lo][0], sm[lo][1], sm[hi][0], sm[hi][1])
            noise = max(MIN_MOVE_M, JITTER_SIGMAS * math.sqrt(sm[lo][2] + sm[hi][2]))
            need = min(noise / slow_ms, JITTER_MAX_WINDOW_S)
            if moved >= noise or ts[hi] - ts[lo] >= need or (lo, hi) == (0, n_pts - 1):
                break
            span = need
        speeds.append((sm[i - 1][3] + sm[i][3]) / 2 * 3.6)
        reach.append(max(STOP_WINDOW_S / 2, ts[i] - ts[i - 1]))  # насколько окно скорости размывает край
        if moved < noise:
            status.append("still")
        else:
            status.append("slow" if local[i] < limits["pause_kmh"] else "move")

    # Всплеск «едем» короче STOP_FLICKER_S посреди остановки — шум, остановку он не разрывает.
    i = 1
    while i < n_pts:
        if status[i] != "move":
            i += 1
            continue
        j = i
        while j < n_pts and status[j] == "move":
            j += 1
        if i > 1 and j < n_pts and ts[j - 1] - ts[i - 1] <= STOP_FLICKER_S:
            for k in range(i, j):
                status[k] = "slow"
        i = j

    # Автопауза задним числом: стоянка или медленный кусок дольше AUTOPAUSE_AFTER_S не идёт в движение целиком,
    # короче — идёт целиком (светофор на 5 с — это ещё тренировка).
    counted = [False] * n_pts
    # Через длинную остановку засчитываем путь по прямой от точки до неё до точки после (без времени):
    # и подъезд к светофору не теряется, и дрожание на месте не копится.
    bridge = [0.0] * n_pts
    i = 1
    while i < n_pts:
        if status[i] == "move":
            counted[i], i = True, i + 1
            continue
        j = i
        while j < n_pts and status[j] != "move":
            j += 1
        if ts[j - 1] - ts[i - 1] <= AUTOPAUSE_AFTER_S:
            for k in range(i, j):
                counted[k] = status[k] == "slow"  # «стоим» внутри короткой паузы — без шума в дистанции
            i = j
            continue
        # Края длинной остановки уточняем по сырым точкам: пока точка в пределах STOP_EDGE_SIGMAS погрешностей
        # от места остановки — уже (ещё) стоим. Окно скорости размывает край на ~10 с (или на отрезок, если точки
        # редкие) — это его убирает, но не дальше: у неточных точек круг «рядом» большой, остановка съела бы ход.
        core = range(i - 1, j)
        w = [1 / _accuracy(pts[k]) ** 2 for k in core]
        here = (sum(wk * pts[k][1] for wk, k in zip(w, core)) / sum(w),
                sum(wk * pts[k][2] for wk, k in zip(w, core)) / sum(w))

        def near(k):
            return haversine_m(*here, pts[k][1], pts[k][2]) <= STOP_EDGE_SIGMAS * _accuracy(pts[k])
        start, end = ts[i - 1], ts[j - 1]
        while i > 1 and counted[i - 1] and near(i - 2) and start - ts[i - 2] <= reach[i - 1]:
            i -= 1
            counted[i] = False
        while j < n_pts and status[j] == "move" and near(j) and ts[j] - end <= reach[j]:
            status[j] = "slow"
            j += 1
        bridge[j - 1] = haversine_m(sm[i - 1][0], sm[i - 1][1], sm[j - 1][0], sm[j - 1][1])
        i = j

    kcal = 0.0
    cum_d, cum_t = [0.0], [0.0]  # после каждого засчитанного отрезка
    track = shown[:1]  # сглаженный (или привязанный к дорогам) маршрут без стоянок — для карты и карточки
    run_pts, run_id, run_cum, run = [], [], [], 0  # засчитанные точки без пауз: (ts, lat, lon), кусок, путь
    for i in range(1, n_pts):
        if not counted[i]:
            if bridge[i]:
                cum_d.append(cum_d[-1] + bridge[i])
                cum_t.append(cum_t[-1])
            run += 1
            continue
        if not run_pts or run_id[-1] != run:
            run_pts.append((ts[i - 1], *sm[i - 1][:2]))
            run_id.append(run)
            run_cum.append(run_cum[-1] if run_cum else 0.0)
        run_pts.append((ts[i], *sm[i][:2]))
        run_id.append(run)
        run_cum.append(run_cum[-1] + seg[i])
        dt = ts[i] - ts[i - 1]
        track += via[i] or ()
        track.append(shown[i])
        cum_d.append(cum_d[-1] + seg[i])
        cum_t.append(cum_t[-1] + dt)
        kcal += met_for_speed(speeds[i], kind) * weight_kg * dt / 3600

    distance_m, moving_s = cum_d[-1], cum_t[-1]
    n = len(cum_d) - 1
    # Максимум — смещение по прямой за окно не короче MAX_SPEED_WINDOW_S в движении (без пауз внутри):
    # и один шумный отрезок, и боковое дрожание трека рекорд не дадут; на поворотах окно чуть занижает.
    max_speed, i = 0.0, 0
    for j in range(1, len(run_pts)):
        if run_id[j] != run_id[i]:
            i = j
        while i + 1 < j and run_pts[j][0] - run_pts[i + 1][0] >= MAX_SPEED_WINDOW_S and \
                run_cum[j] - run_cum[i + 1] >= MAX_SPEED_MIN_M:
            i += 1
        span = run_pts[j][0] - run_pts[i][0]
        if span >= MAX_SPEED_WINDOW_S and run_cum[j] - run_cum[i] >= MAX_SPEED_MIN_M:
            max_speed = max(max_speed, haversine_m(*run_pts[i][1:], *run_pts[j][1:]) / span * 3.6)
    if not max_speed and moving_s:  # тренировка короче окна — средняя
        max_speed = distance_m / moving_s * 3.6

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


def elevation_gain(elevations, threshold_m, window):
    """Набор высоты, м, по скользящему среднему из window точек, с гистерезисом threshold_m:
    подъём начинается, когда высота ушла от низшей точки вверх больше порога (и считается от неё),
    и кончается, когда упала от вершины больше порога. Шум и кочки меньше порога не копятся."""
    gain, ref, climbing = 0.0, None, False
    for i in range(len(elevations)):
        part = elevations[max(0, i - window + 1):i + 1]
        e = sum(part) / len(part)
        if ref is None:
            ref = e
        elif climbing:
            if e > ref:
                gain, ref = gain + e - ref, e
            elif ref - e > threshold_m:
                climbing, ref = False, e
        elif e < ref:
            ref = e
        elif e - ref > threshold_m:
            gain, ref, climbing = gain + e - ref, e, True
    return int(round(gain))


def streaks(days, today):
    """Серии: (дней подряд, недель подряд) с тренировками.

    days — даты тренировок. Если сегодня (на этой неделе) тренировки ещё не было,
    серия не прервана и считается со вчера (с прошлой недели).
    """
    def run(units, cur, step):
        if cur not in units:
            cur -= step
        n = 0
        while cur in units:
            n, cur = n + 1, cur - step
        return n

    days = set(days)
    weeks = {d - timedelta(days=d.weekday()) for d in days}  # понедельники
    return (
        run(days, today, timedelta(days=1)),
        run(weeks, today - timedelta(days=today.weekday()), timedelta(weeks=1)),
    )


# --- форматирование для сообщений бота и карточки ---

def fmt_duration(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

