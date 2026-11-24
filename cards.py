"""Карточки тренировки: для чатов (4:5) и для Instagram Stories (9:16) — карта, маршрут, цифры, логотип."""
import io
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from settings import ACTIVITIES, APP_NAME, MAP_MAX_ZOOM
from stats import fmt_duration, fmt_pace

ASSETS = Path(__file__).parent / "assets"
BG, LIME, WHITE, MUTED = "#0E1116", "#C6F432", "#FFFFFF", "#B7BDC7"
W, H, STORY_H = 1080, 1350, 1920
S = 2  # рисуем вдвое крупнее и уменьшаем: Pillow не сглаживает линии сам
PAD = 72
TILE = 256
MAP_W, MAP_H = W - 2 * PAD, 640  # одна карта на обеих карточках: плитки те же
MAP_MARGIN = 48  # маршрут не прижимается к краю карты
# Светлые плитки OSM → тёмные: суша чуть светлее фона, дороги светлее суши, вода и парки — фон.
MAP_LUT = [max(0, v - 190) * 4 for v in range(256)]
MAP_DARK, MAP_LIGHT = BG, "#3F4754"


@lru_cache
def font(name, size):
    return ImageFont.truetype(str(ASSETS / "fonts" / name), size * S)


def project(lat, lon, z):
    """Web Mercator: пиксели карты мира на зуме z, как у плиток OSM."""
    n = TILE * 2 ** z
    lat = max(min(lat, 85.0511), -85.0511)
    return (lon + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n


def map_view(track):
    """Наибольший целый зум, на котором маршрут помещается в карту; левый верхний угол окна
    в пикселях мира и плитки (z, x, y), которые окно покрывает."""
    for z in range(MAP_MAX_ZOOM, -1, -1):
        xs, ys = zip(*(project(lat, lon, z) for lat, lon in track))
        if max(xs) - min(xs) <= MAP_W - 2 * MAP_MARGIN and max(ys) - min(ys) <= MAP_H - 2 * MAP_MARGIN:
            break
    left, top = (min(xs) + max(xs) - MAP_W) / 2, (min(ys) + max(ys) - MAP_H) / 2
    n = 2 ** z
    tiles = [
        (z, x, y)
        for x in range(max(0, int(left // TILE)), min(n, int((left + MAP_W) // TILE) + 1))
        for y in range(max(0, int(top // TILE)), min(n, int((top + MAP_H) // TILE) + 1))
    ]
    return z, left, top, tiles


def rounded(size, radius):
    """Маска прямоугольника со скруглёнными углами."""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *size), radius * S, fill=255)
    return mask


def draw_map(img, top, track, tiles):
    """Тёмная карта со скруглёнными углами (без плиток — просто фон) и маршрут поверх. track не пуст."""
    z, left, wtop, keys = map_view(track)
    panel, drawn = Image.new("L", (MAP_W, MAP_H), 0), False
    for key in keys:
        try:
            tile = Image.open(io.BytesIO(tiles[key])).convert("L")
        except (KeyError, TypeError, OSError):  # плиток нет (None) или битая — этот кусок без карты
            continue
        panel.paste(tile, (round(key[1] * TILE - left), round(key[2] * TILE - wtop)))
        drawn = True
    panel = ImageOps.colorize(panel.point(MAP_LUT), MAP_DARK, MAP_LIGHT).resize((MAP_W * S, MAP_H * S), Image.LANCZOS)
    img.paste(panel, (PAD * S, top * S), rounded(panel.size, 32))

    d = ImageDraw.Draw(img)
    xy = [((x - left + PAD) * S, (y - wtop + top) * S) for x, y in (project(lat, lon, z) for lat, lon in track)]
    if len(xy) > 1:
        d.line(xy, fill=BG, width=18 * S, joint="curve")  # окантовка: линия читается и на дорогах
        d.line(xy, fill=LIME, width=10 * S, joint="curve")
    r = 16 * S
    for (x, y), fill in ((xy[0], WHITE), (xy[-1], LIME)):
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=BG, width=6 * S)
    if drawn:  # лицензия OSM требует подпись
        d.text(((PAD + MAP_W - 20) * S, (top + MAP_H - 16) * S), "© участники OpenStreetMap",
               font=font("Inter-Regular.ttf", 20), fill=MUTED, anchor="rs")


def header(img, top, right_text):
    d = ImageDraw.Draw(img)
    logo = Image.open(ASSETS / "brand" / "duet-512.png").resize((96 * S, 96 * S), Image.LANCZOS)
    img.paste(logo, ((PAD - 12) * S, (top - 12) * S), rounded(logo.size, 24))  # у логотипа свой тёмный фон
    d.text(((PAD + 96) * S, (top + 36) * S), APP_NAME, font=font("InterDisplay-Bold.ttf", 44), fill=WHITE, anchor="lm")
    d.text(((W - PAD) * S, (top + 36) * S), right_text, font=font("Inter-Regular.ttf", 36), fill=MUTED, anchor="rm")


def pace_or_speed(kind, speed_kmh):
    """Велосипед — скорость, бег и ходьба — темп: (значение, подпись)."""
    if kind == "ride":
        return f"{speed_kmh}", "км/ч"
    return fmt_pace(speed_kmh).removesuffix(" /км"), "темп /км"


def png(img, height):
    out = io.BytesIO()
    img.resize((W, height), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


def render_card(kind, stats, records=(), tiles=None):
    """PNG карточки для чатов. stats — из compute_stats (нужен track), records — подписи новых рекордов,
    tiles — {(z, x, y): PNG} для map_view(track), None — без карты."""
    img = Image.new("RGB", (W * S, H * S), BG)
    header(img, PAD, ACTIVITIES[kind]["title"])
    if stats["track"]:
        draw_map(img, 200, stats["track"], tiles)

    d = ImageDraw.Draw(img)
    cells = [  # 2×2: «1:02:13» крупным шрифтом в четыре колонки не влезает
        (f"{stats['distance_km']:.2f}", "км"),
        (fmt_duration(stats["moving_s"]), "в движении"),
        pace_or_speed(kind, stats["avg_speed_kmh"]),
        (f"{stats['kcal']}", "ккал"),
    ]
    col_w = (W - 2 * PAD) / 2
    for i, (value, label) in enumerate(cells):
        x, y = PAD + i % 2 * col_w, 940 + i // 2 * 155
        d.text((x * S, y * S), value, font=font("InterDisplay-Bold.ttf", 80), fill=WHITE, anchor="ls")
        d.text((x * S, (y + 46) * S), label, font=font("Inter-Regular.ttf", 32), fill=MUTED, anchor="ls")
    for i, text in enumerate(records[:3]):
        d.text((PAD * S, (1222 + i * 46) * S), text, font=font("Inter-Regular.ttf", 36), fill=LIME, anchor="ls")
    return png(img, H)


def aurora(width, height):
    """Фон Stories: ночной фон бренда с размытыми пятнами — фиолетовым сверху, бирюзовым и лаймом снизу."""
    img = Image.new("RGB", (width, height), BG)
    for cx, cy, radius, color, strength in (
        (0.95, 0.02, 0.95, "#6A4BFF", 0.55),
        (0.0, 0.62, 0.75, "#13B5A0", 0.35),
        (0.85, 1.0, 0.85, LIME, 0.45),
    ):
        size = int(radius * width * 2)
        # radial_gradient: 0 в центре, 181 на краю вписанного круга — за ним пятно гаснет в ноль.
        glow = Image.radial_gradient("L").resize((size, size), Image.BICUBIC)
        glow = glow.point(lambda v, k=strength: int(max(0.0, 1 - v / 181) ** 2 * 255 * k))
        layer = Image.new("L", (width, height), 0)
        layer.paste(glow, (int(cx * width - size / 2), int(cy * height - size / 2)))
        img.paste(color, (0, 0, width, height), layer)
    return img


def render_story(kind, stats, records=(), tiles=None, elevation_m=None, date=""):
    """PNG 1080×1920 для Instagram Stories: градиент, карта, дистанция крупно и подробные цифры.
    Важное — между 200 и 1700 px по высоте: сверху и снизу Instagram кладёт свои кнопки."""
    img = aurora(W, STORY_H).resize((W * S, STORY_H * S), Image.BICUBIC)
    header(img, 200, f"{ACTIVITIES[kind]['title']} · {date}" if date else ACTIVITIES[kind]["title"])
    if stats["track"]:
        draw_map(img, 330, stats["track"], tiles)

    d = ImageDraw.Draw(img)
    value = f"{stats['distance_km']:.2f}"
    big = font("InterDisplay-Bold.ttf", 200)
    d.text((PAD * S, 1190 * S), value, font=big, fill=WHITE, anchor="ls")
    d.text((PAD * S + d.textlength(value, font=big) + 24 * S, 1190 * S), "км",
           font=font("InterDisplay-Bold.ttf", 72), fill=LIME, anchor="ls")

    pace = kind != "ride"
    best = (fmt_duration(stats["best_1k_s"]), "лучший 1 км") if kind == "run" and stats["best_1k_s"] else None
    cells = [
        (fmt_duration(stats["moving_s"]), "в движении"),
        pace_or_speed(kind, stats["avg_speed_kmh"]) if pace else (f"{stats['avg_speed_kmh']}", "км/ч средняя"),
        (fmt_pace(stats["max_speed_kmh"]).removesuffix(" /км"), "лучший темп") if pace
        else (f"{stats['max_speed_kmh']}", "км/ч макс."),
        (f"{stats['kcal']}", "ккал"),
        (f"{elevation_m} м" if elevation_m is not None else "–", "набор высоты"),
        best or (fmt_duration(stats["duration_s"]), "всего"),
    ]
    col_w = (W - 2 * PAD) / 3
    for i, (value, label) in enumerate(cells):
        x, y = PAD + i % 3 * col_w, 1320 + i // 3 * 170
        d.text((x * S, y * S), value, font=font("InterDisplay-Bold.ttf", 64), fill=WHITE, anchor="ls")
        d.text((x * S, (y + 44) * S), label, font=font("Inter-Regular.ttf", 30), fill=MUTED, anchor="ls")
    for i, text in enumerate(records[:2]):
        d.text((PAD * S, (1640 + i * 50) * S), text, font=font("Inter-Regular.ttf", 34), fill=LIME, anchor="ls")
    return png(img, STORY_H)
