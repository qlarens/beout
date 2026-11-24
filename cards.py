"""Карточка тренировки — картинка для чатов: маршрут без карты, цифры, логотип."""
import io
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from settings import ACTIVITIES, APP_NAME
from stats import fmt_duration, fmt_pace

ASSETS = Path(__file__).parent / "assets"
BG, LIME, WHITE, MUTED = "#0E1116", "#C6F432", "#FFFFFF", "#B7BDC7"
W, H = 1080, 1350
S = 2  # рисуем вдвое крупнее и уменьшаем: Pillow не сглаживает линии сам
PAD = 72
ROUTE_BOX = (PAD, 220, W - PAD, 860)


@lru_cache
def font(name, size):
    return ImageFont.truetype(str(ASSETS / "fonts" / name), size * S)


def fit_route(track, box):
    """Нормализует [(lat, lon)] в прямоугольник box с сохранением пропорций, по центру."""
    x0, y0, x1, y1 = box
    k = math.cos(math.radians(sum(lat for lat, _ in track) / len(track)))  # градус долготы короче к полюсам
    xs = [lon * k for _, lon in track]
    ys = [lat for lat, _ in track]
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)
    scale = min((x1 - x0) / dx if dx else math.inf, (y1 - y0) / dy if dy else math.inf)
    scale = 0 if scale == math.inf else scale  # одна точка — рисуем в центре
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    mx, my = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return [(cx + (x - mx) * scale, cy - (y - my) * scale) for x, y in zip(xs, ys)]


def render_card(kind, stats, records=()):
    """PNG-байты карточки. stats — из compute_stats (нужен track), records — подписи новых рекордов."""
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)

    def up(*xy):
        return [v * S for v in xy]

    logo = Image.open(ASSETS / "brand" / "duet-512.png").resize((96 * S, 96 * S), Image.LANCZOS)
    img.paste(logo, up(PAD - 12, PAD - 12))
    d.text(up(PAD + 96, PAD + 36), APP_NAME, font=font("InterDisplay-Bold.ttf", 44), fill=WHITE, anchor="lm")
    d.text(up(W - PAD, PAD + 36), ACTIVITIES[kind]["title"], font=font("Inter-Regular.ttf", 36), fill=MUTED, anchor="rm")

    if stats["track"]:
        xy = [tuple(up(x, y)) for x, y in fit_route(stats["track"], ROUTE_BOX)]
        if len(xy) > 1:
            d.line(xy, fill=LIME, width=12 * S, joint="curve")
        r = 18 * S
        for (x, y), fill in ((xy[0], WHITE), (xy[-1], LIME)):
            d.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=BG, width=6 * S)

    if kind == "ride":
        speed, speed_label = f"{stats['avg_speed_kmh']}", "км/ч"
    else:
        speed, speed_label = fmt_pace(stats["avg_speed_kmh"]).removesuffix(" /км"), "темп /км"
    columns = [
        (f"{stats['distance_km']:.2f}", "км"),
        (fmt_duration(stats["moving_s"]), "в движении"),
        (speed, speed_label),
    ]
    col_w = (W - 2 * PAD) / len(columns)
    for i, (value, label) in enumerate(columns):
        x = PAD + i * col_w
        d.text(up(x, 1010), value, font=font("InterDisplay-Bold.ttf", 80), fill=WHITE, anchor="ls")
        d.text(up(x, 1060), label, font=font("Inter-Regular.ttf", 32), fill=MUTED, anchor="ls")

    for i, text in enumerate(records[:3]):
        d.text(up(PAD, 1150 + i * 52), text, font=font("Inter-Regular.ttf", 36), fill=LIME, anchor="ls")

    out = io.BytesIO()
    img.resize((W, H), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()
