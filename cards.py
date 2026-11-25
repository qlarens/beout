"""Карточки тренировки: пост для чатов (4:5) и Instagram Stories (9:16, 1080×1920).

Настройки: карта с маршрутом, только маршрут на тёмном или без карты; какие цифры показывать;
своё фото на фоне — снизу оно плавно уходит в тёмную карточку с картой и цифрами.
"""
import io
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from i18n import DEFAULT_LANG, t
from settings import APP_NAME, MAP_MAX_ZOOM
from stats import fmt_duration

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


def map_view(track, map_h=MAP_H):
    """Окно карты MAP_W×map_h, в котором маршрут заполняет карту целиком (до MAP_MARGIN от края).

    Нужный зум дробный, а плитки есть только на целых: берём плитки зума z на ступень крупнее
    и уменьшаем мозаику в scale раз (0.5 < scale ≤ 1) — карта остаётся чёткой.
    Возвращает z, scale, левый верхний угол окна в пикселях мира на зуме z и плитки (z, x, y) под окном.
    """
    xs, ys = zip(*(project(lat, lon, 0) for lat, lon in track))
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)
    fit = min((MAP_W - 2 * MAP_MARGIN) / dx if dx else math.inf, (map_h - 2 * MAP_MARGIN) / dy if dy else math.inf)
    zoom = max(0.0, min(math.log2(fit), MAP_MAX_ZOOM))  # одна точка: fit = inf → MAP_MAX_ZOOM
    z = math.ceil(zoom)
    scale, n = 2 ** (zoom - z), 2 ** z
    w, h = MAP_W / scale, map_h / scale
    left, top = (min(xs) + max(xs)) / 2 * n - w / 2, (min(ys) + max(ys)) / 2 * n - h / 2
    tiles = [
        (z, x, y)
        for x in range(max(0, int(left // TILE)), min(n, int((left + w) // TILE) + 1))
        for y in range(max(0, int(top // TILE)), min(n, int((top + h) // TILE) + 1))
    ]
    return z, scale, left, top, tiles


def rounded(size, radius):
    """Маска прямоугольника со скруглёнными углами."""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *size), radius * S, fill=255)
    return mask


def draw_map(img, top, track, tiles, lang, map_h=MAP_H):
    """Тёмная карта со скруглёнными углами (без плиток — просто фон) и маршрут поверх. track не пуст."""
    z, scale, left, wtop, keys = map_view(track, map_h)
    panel, drawn = Image.new("L", (math.ceil(MAP_W / scale), math.ceil(map_h / scale)), 0), False
    for key in keys:
        try:
            tile = Image.open(io.BytesIO(tiles[key])).convert("L")
        except (KeyError, TypeError, OSError):  # плиток нет (None) или битая — этот кусок без карты
            continue
        panel.paste(tile, (round(key[1] * TILE - left), round(key[2] * TILE - wtop)))
        drawn = True
    panel = ImageOps.colorize(panel.point(MAP_LUT), MAP_DARK, MAP_LIGHT).resize((MAP_W * S, map_h * S), Image.LANCZOS)
    img.paste(panel, (PAD * S, top * S), rounded(panel.size, 32))

    d = ImageDraw.Draw(img)
    xy = [(((x - left) * scale + PAD) * S, ((y - wtop) * scale + top) * S)
          for x, y in (project(lat, lon, z) for lat, lon in track)]
    if len(xy) > 1:
        d.line(xy, fill=BG, width=18 * S, joint="curve")  # окантовка: линия читается и на дорогах
        d.line(xy, fill=LIME, width=10 * S, joint="curve")
    r = 16 * S
    for (x, y), fill in ((xy[0], WHITE), (xy[-1], LIME)):
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=BG, width=6 * S)
    if drawn:  # лицензия OSM требует подпись
        d.text(((PAD + MAP_W - 20) * S, (top + map_h - 16) * S), t(lang, "osm"),
               font=font("Inter-Regular.ttf", 20), fill=MUTED, anchor="rs")


def header(img, top, right_text, lang):
    d = ImageDraw.Draw(img)
    logo = Image.open(ASSETS / "brand" / "duet-512.png").resize((96 * S, 96 * S), Image.LANCZOS)
    img.paste(logo, ((PAD - 12) * S, (top - 12) * S), rounded(logo.size, 24))  # у логотипа свой тёмный фон
    d.text(((PAD + 96) * S, (top + 40) * S), APP_NAME, font=font("InterDisplay-Bold.ttf", 44), fill=WHITE, anchor="ls")
    d.text(((PAD + 96) * S, (top + 76) * S), t(lang, "watermark"), font=font("Inter-Regular.ttf", 26), fill=MUTED, anchor="ls")
    d.text(((W - PAD) * S, (top + 36) * S), right_text, font=font("Inter-Regular.ttf", 36), fill=MUTED, anchor="rm")


def png(img, height):
    out = io.BytesIO()
    img.resize((W, height), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


def jpeg_preview(img, height):
    """Превью в Mini App: вдвое меньше и JPEG — быстро считается и быстро приходит."""
    out = io.BytesIO()
    img.resize((W // 2, height // 2), Image.LANCZOS).save(out, "JPEG", quality=82)
    return out.getvalue()


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


# ---------- настраиваемая карточка ----------

# высота, верх шапки, начало и конец содержимого
FORMATS = {
    "post": (H, PAD, 200, H - 64),
    "story": (STORY_H, 200, 330, 1700),  # выше 200 и ниже 1700 px Instagram кладёт свои кнопки
}
FIELDS = ("distance", "time", "speed", "kcal", "elevation", "date", "records")
PANELS = ("map", "route", "none")  # карта с маршрутом, маршрут на тёмном, без карты
HERO = {"post": 150, "story": 200}  # дистанция крупно
GAP = 48
MIN_MAP_H = 280
MAX_PHOTO_PIXELS = 40_000_000


class PhotoError(ValueError):
    """Не картинка или слишком большая."""


def open_photo(data):
    """Фото пользователя: учитываем поворот из EXIF, отказываемся от гигантских картинок."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_PHOTO_PIXELS:
            raise PhotoError("too large")
        return ImageOps.exif_transpose(img).convert("RGB")
    except (OSError, Image.DecompressionBombError) as e:
        raise PhotoError(str(e)) from e


def cells_of(fmt, kind, stats, fields, elevation_m, lang):
    """Сетка цифр под крупной дистанцией. У поста до 4 клеток, у Stories до 6."""
    cells = []
    if "time" in fields:
        cells.append((fmt_duration(stats["moving_s"]), t(lang, "card_moving")))
    if "speed" in fields:
        cells.append((f"{stats['avg_speed_kmh']}", t(lang, "card_avg" if fmt == "story" else "card_kmh")))
        if fmt == "story":
            cells.append((f"{stats['max_speed_kmh']}", t(lang, "card_max")))
    if "kcal" in fields:
        cells.append((f"{stats['kcal']}", t(lang, "card_kcal")))
    if "elevation" in fields and elevation_m:  # не узнали или по ровному — не показываем
        cells.append((t(lang, "card_meters", m=elevation_m), t(lang, "card_elevation")))
    if fmt == "story":
        if "speed" in fields and kind == "run" and stats["best_1k_s"]:
            cells.append((fmt_duration(stats["best_1k_s"]), t(lang, "rec_best_1k_s")))
        if "time" in fields:
            cells.append((fmt_duration(stats["duration_s"]), t(lang, "card_total")))
    return cells[:6 if fmt == "story" else 4]


def grid_shape(n, compact=False):
    """Колонки, строки и кегль цифр: 2 и 4 клетки — по две в ряд и крупнее, иначе по три.
    compact (поверх фото) — до четырёх в один ряд, чтобы фото осталось больше места."""
    if compact and n <= 4:
        return n, 1, 64 if n <= 3 else 52
    cols = n if n <= 2 else 2 if n == 4 else 3
    size = 80 if cols <= 2 else 64
    return cols, math.ceil(n / cols), size


def row_height(size):
    return round(size * 0.75) + 52


def grid_height(n, compact=False):
    if not n:
        return 0
    _, rows, size = grid_shape(n, compact)
    return rows * row_height(size) + (rows - 1) * 40


def layout(fmt, n_cells, hero, n_records, panel, photo):
    """Где стоят блоки: {имя: (верх, высота)}. Карта забирает свободное место, но не больше 640 px.

    Без фото содержимое по центру под шапкой; с фото — прижато к низу, сверху видно фото.
    """
    height, _, top, bottom = FORMATS[fmt]
    blocks = []
    if hero:
        blocks.append(("hero", round(HERO[fmt] * 0.74)))
    if n_cells:
        blocks.append(("grid", grid_height(n_cells, photo)))
    if n_records:
        blocks.append(("records", n_records * 50 - 14))
    if panel != "none":
        rest = sum(h for _, h in blocks) + GAP * len(blocks)
        floor = round(height * 0.45) if photo else top  # с фото: верхние ~45% — само фото
        prefer = (480 if fmt == "story" else 380) if photo else 640
        blocks.insert(0, ("panel", max(MIN_MAP_H, min(prefer, bottom - floor - rest))))
    total = sum(h for _, h in blocks) + GAP * max(0, len(blocks) - 1)
    y = bottom - total if photo else top + max(0, (bottom - top - total) // 2)
    where = {}
    for name, h in blocks:
        where[name] = (y, h)
        y += h + GAP
    return where


def plan(fmt, kind, stats, panel="map", fields=FIELDS, records=(), elevation_m=None, photo=False, lang=DEFAULT_LANG):
    """Состав и расстановка карточки; нужна и боту — узнать высоту карты, чтобы скачать плитки."""
    fields = set(fields)
    panel = panel if stats["track"] else "none"
    cells = cells_of(fmt, kind, stats, fields, elevation_m, lang)
    recs = list(records[:2]) if "records" in fields else []
    hero = "distance" in fields
    return panel, cells, recs, hero, layout(fmt, len(cells), hero, len(recs), panel, photo)


def map_height(*args, **kwargs):
    """Высота карты на карточке (None — карты нет): под неё скачиваются плитки."""
    panel, *_, where = plan(*args, **kwargs)
    return where["panel"][1] if panel != "none" else None


def photo_background(data, height):
    """Фото на весь кадр; сверху лёгкая тень под шапку."""
    img = ImageOps.fit(open_photo(data), (W * S, height * S), Image.LANCZOS, centering=(0.5, 0.35))
    shade = Image.linear_gradient("L").rotate(180).resize((W * S, 420 * S))  # 255 сверху → 0 снизу
    img.paste(BG, (0, 0, W * S, 420 * S), shade.point(lambda v: int(v * 0.65)))
    return img


def fade_into_card(img, top, height):
    """Плавный переход фото → тёмная карточка: от прозрачного за 420 px до блока к сплошному фону."""
    y0, y1 = max(0, top - 420), max(1, top - 24)
    ramp = Image.linear_gradient("L").resize((W * S, (y1 - y0) * S))
    ramp = ramp.point(lambda v: int((v / 255) ** 1.6 * 255))  # мягче вверху, плотнее у цифр
    img.paste(BG, (0, y0 * S, W * S, y1 * S), ramp)
    img.paste(BG, (0, y1 * S, W * S, height * S))


def render(fmt, kind, stats, panel="map", fields=FIELDS, records=(), tiles=None, elevation_m=None, date="",
           photo=None, lang=DEFAULT_LANG, preview=False):
    """PNG карточки (preview — JPEG вдвое меньше). fmt: post | story; panel: map | route | none;
    fields — что показать из FIELDS; photo — байты картинки для фона; tiles — {(z, x, y): PNG}
    для map_view(track, map_height(...)). PhotoError — фото не открылось."""
    height, header_top, _, _ = FORMATS[fmt]
    panel, cells, recs, hero, where = plan(fmt, kind, stats, panel, fields, records, elevation_m, bool(photo), lang)
    if photo:
        img = photo_background(photo, height)
        fade_into_card(img, min(top for top, _ in where.values()), height)
    elif fmt == "story":
        img = aurora(W, height).resize((W * S, height * S), Image.BICUBIC)
    else:
        img = Image.new("RGB", (W * S, height * S), BG)
    title = t(lang, f"kind_{kind}")
    header(img, header_top, f"{title} · {date}" if date and "date" in fields else title, lang)
    if panel != "none":
        top, map_h = where["panel"]
        draw_map(img, top, stats["track"], tiles if panel == "map" else None, lang, map_h)

    d = ImageDraw.Draw(img)
    if hero:
        top, h = where["hero"]
        value, big = f"{stats['distance_km']:.2f}", font("InterDisplay-Bold.ttf", HERO[fmt])
        d.text((PAD * S, (top + h) * S), value, font=big, fill=WHITE, anchor="ls")
        d.text((PAD * S + d.textlength(value, font=big) + 24 * S, (top + h) * S), t(lang, "card_km"),
               font=font("InterDisplay-Bold.ttf", round(HERO[fmt] * 0.36)), fill=LIME, anchor="ls")
    if cells:
        top, _ = where["grid"]
        cols, _, size = grid_shape(len(cells), bool(photo))
        col_w, step = (W - 2 * PAD) / cols, row_height(size) + 40
        for i, (value, label) in enumerate(cells):
            x, y = PAD + i % cols * col_w, top + i // cols * step + round(size * 0.75)
            d.text((x * S, y * S), value, font=font("InterDisplay-Bold.ttf", size), fill=WHITE, anchor="ls")
            d.text((x * S, (y + 44) * S), label, font=font("Inter-Regular.ttf", 32 if cols <= 2 else 30 if cols == 3 else 26),
                   fill=MUTED, anchor="ls")
    if recs:
        top, _ = where["records"]
        for i, text in enumerate(recs):
            d.text((PAD * S, (top + 36 + i * 50) * S), text, font=font("Inter-Regular.ttf", 34), fill=LIME, anchor="ls")
    return jpeg_preview(img, height) if preview else png(img, height)


def render_card(kind, stats, records=(), tiles=None, lang=DEFAULT_LANG):
    """Пост 4:5 с настройками по умолчанию — для кнопки «Поделиться» под итогом в чате."""
    return render("post", kind, stats, records=records, tiles=tiles, lang=lang)


def render_story(kind, stats, records=(), tiles=None, elevation_m=None, date="", lang=DEFAULT_LANG):
    """Stories 1080×1920 с настройками по умолчанию."""
    return render("story", kind, stats, records=records, tiles=tiles, elevation_m=elevation_m, date=date, lang=lang)
