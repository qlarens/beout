"""Трекер тренировок: Telegram-бот + сервер для Mini App в одном процессе.

Бот принимает трансляцию геопозиции (Live Location) и сохраняет точки маршрута.
Telegram присылает обновления, даже когда телефон заблокирован, поэтому
запись не обрывается. Mini App (webapp/index.html) показывает карту и статистику.
"""
import asyncio
import json
import logging
import math
import re
import secrets
from html import escape
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    InlineQueryResultsButton,
    InputTextMessageContent,
    ReplyParameters,
    BufferedInputFile,
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultCachedPhoto,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from aiohttp import ClientError, ClientSession, ClientTimeout, web

from auth import validate_init_data
import clubs
from cards import FIELDS, FORMATS, PANELS, PhotoError, map_height, map_view, render, render_card, render_week
from i18n import DEFAULT_LANG, LANGS, pick, t
from stats import clean, compute_stats, elevation_gain, fmt_duration, streaks
from settings import (
    ACTIVITIES,
    APP_NAME,
    DEFAULT_ACCURACY_M,
    DUEL_DAYS,
    EVENT_REMIND_BEFORE_S,
    EVENT_SUMMARY_AFTER_S,
    FEED_MIN_KM,
    INLINE_CARD_TTL_S,
    APP_VERSION,
    ELEVATION_API,
    ELEVATION_BATCH,
    ELEVATION_MAX_POINTS,
    ELEVATION_MIN_STEP_M,
    ELEVATION_SMOOTH_POINTS,
    ELEVATION_TIMEOUT_S,
    MAP_CACHE_TILES,
    MAP_MATCH_CHUNK,
    MAP_MATCH_MAX_CHANGE,
    MAP_MATCH_MAX_POINTS,
    MAP_MATCH_MIN_CONFIDENCE,
    MAP_MATCH_RADIUS_M,
    MAP_MATCH_TIMEOUT_S,
    MAP_MATCH_TOTAL_TIMEOUT_S,
    MAP_MATCH_URLS,
    MAP_TILES,
    MAP_TIMEOUT_S,
    MAP_USER_AGENT,
    NUDGE_HOURS,
    NUDGE_IDLE_DAYS,
    NUDGE_STREAK_DAYS,
    OPEN_CALL_TTL_S,
    RATING_POST_HOUR_UTC,
    RATING_TOP_N,
    WAR_DAYS,
)
from storage import STAT_KEYS, Storage

# Секреты — только из окружения, чтобы токен не лежал в коде.
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ["WEBAPP_URL"].rstrip("/")  # публичный HTTPS-адрес этого сервера
# cloudflared ходит на localhost; 0.0.0.0 открыл бы API всей локальной сети.
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH", "beout.db")
BACKUP_DIR = os.environ.get("BACKUP_DIR") or str(Path(DB_PATH).resolve().parent / "backups")
# Сюда бот пишет о запуске и ошибках. Свой user_id можно узнать у @userinfobot.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
# Только для локальной отладки в обычном браузере, без Telegram. В проде не задавать!
DEV_USER_ID = os.environ.get("DEV_USER_ID")
# Поменял webapp/privacy.html — увеличь: бот попросит всех принять условия заново.
POLICY_VERSION = 9
PAGE_SIZE = 50
NETWORK_RETRY_S = 10
FLOOD_MAX_WAIT_S = 600

WEBAPP_DIR = Path(__file__).parent / "webapp"
ASSETS_DIR = Path(__file__).parent / "assets"

logging.basicConfig(level=logging.INFO)
db = Storage(DB_PATH)
dp = Dispatcher()
# Личные команды и трансляция — только в личке: /export в группе выложил бы данные всем.
private = Router()
private.message.filter(F.chat.type == "private")
private.edited_message.filter(F.chat.type == "private")
dp.include_router(private)
GROUP_CHATS = {"group", "supergroup"}

def commands(lang):
    return [
        BotCommand(command=name, description=t(lang, f"cmd_{name}"))
        for name in ("start", "stop", "weight", "privacy", "export", "delete", "top")
    ]


# ---------- язык ----------

def lang_of(user):
    """Язык ответа пользователю Telegram: выбранный в профиле, иначе язык его Telegram.

    Язык Telegram запоминаем (если условия приняты) — для сообщений без апдейта: итог по таймеру, SOS.
    """
    if not user.language_code:  # Telegram присылает не всегда
        return db.user_lang(user.id)
    db.remember_tg_lang(user.id, pick(user.language_code))
    return db.lang_choice(user.id) or pick(user.language_code)


def menu_button(lang):
    return MenuButtonWebApp(text=t(lang, "menu"), web_app=WebAppInfo(url=f"{WEBAPP_URL}/"))


async def set_user_menu(bot, user_id, lang):
    """Кнопка «Мои тренировки» в чате с пользователем — на его языке."""
    try:
        await bot.set_chat_menu_button(chat_id=user_id, menu_button=menu_button(lang))
    except TelegramAPIError as e:
        logging.warning("Меню для %s не обновлено: %r", user_id, e)


# ---------- вспомогательное ----------

def to_ts(value):
    if value is None:
        return time.time()
    if isinstance(value, datetime):
        return value.timestamp()
    return float(value)


def has_consent(user_id):
    return db.consent_version(user_id) >= POLICY_VERSION


def stats_of(ride):
    """Статистика закрытой тренировки хранится в базе, идущей — считается на лету."""
    if ride["ended_at"] is not None:
        return {k: ride[k] for k in STAT_KEYS}
    return compute_stats(db.get_points(ride["id"]), db.get_weight(ride["user_id"]), ride["kind"])


def speed_line(stats, lang):
    return t(lang, "speed_line", avg=stats["avg_speed_kmh"], max=stats["max_speed_kmh"])


def start_keyboard(ride, lang):
    """Под сообщением о старте: трекер, выбор типа (выбранный с галочкой), финиш."""
    kinds = [
        InlineKeyboardButton(
            text=("✓ " if kind == ride["kind"] else "") + t(lang, f"kind_{kind}"),
            callback_data=f"kind:{ride['id']}:{kind}",
        )
        for kind in ACTIVITIES
    ]
    return open_app_keyboard(
        ride["id"], t(lang, "btn_live"),
        kinds,
        [InlineKeyboardButton(text=t(lang, "btn_finish"), callback_data=f"finish:{ride['id']}")],
    )


def open_app_keyboard(ride_id, text, *extra_rows):
    url = f"{WEBAPP_URL}/?ride={ride_id}" if ride_id else f"{WEBAPP_URL}/"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))], *extra_rows]
    )


def policy_keyboard(lang, *extra_rows):
    button = InlineKeyboardButton(text=t(lang, "btn_policy"), url=f"{WEBAPP_URL}/privacy?lang={lang}")
    return InlineKeyboardMarkup(inline_keyboard=[[button], *extra_rows])


def consent_keyboard(lang, ref=None):
    """«Прочитать» и «Принимаю» в один ряд. ref — «ref_<token>» из реферальной ссылки:
    едет в кнопке, чтобы до согласия ничего не хранить."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(lang, "btn_read"), url=f"{WEBAPP_URL}/privacy?lang={lang}"),
        InlineKeyboardButton(text=t(lang, "btn_accept"), callback_data=f"consent:{ref}" if ref else "consent"),
    ]])


def consent_text(user_id, lang):
    """Новичку — коротко о данных; принимавшему старые условия — что они обновились."""
    return t(lang, "policy_updated" if db.consent_version(user_id) else "consent")


def welcome_text(user_id, lang):
    return t(lang, "welcome", weight=f"{db.get_weight(user_id):g}")


def ride_summary(ride, lang):
    stats = stats_of(ride)
    return (
        t(lang, "summary", kind=t(lang, f"kind_{ride['kind']}"), km=stats["distance_km"],
          duration=fmt_duration(stats["duration_s"]), moving=fmt_duration(stats["moving_s"]),
          speed=speed_line(stats, lang), kcal=stats["kcal"])
        + (f"\n{t(lang, 'summary_elevation', m=ride['elevation_gain_m'])}"
           if ride["elevation_gain_m"] is not None else "")
        + "".join(f"\n🏆 {label}" for label in record_labels(ride, lang))
    )


def record_labels(ride, lang):
    return [t(lang, "new_record", what=t(lang, f"rec_{key}")) for key in db.new_records(ride)]


async def fetch_elevations(track):
    """Высоты точек [(lat, lon)] у Open-Meteo, пачками; None — сервис недоступен.

    Уходят только координаты, без Telegram ID.
    """
    sample = track[::math.ceil(len(track) / ELEVATION_MAX_POINTS)]
    elevations = []
    try:
        async with ClientSession(timeout=ClientTimeout(total=ELEVATION_TIMEOUT_S)) as http:
            for i in range(0, len(sample), ELEVATION_BATCH):
                chunk = sample[i:i + ELEVATION_BATCH]
                params = {
                    "latitude": ",".join(f"{lat:.5f}" for lat, _ in chunk),
                    "longitude": ",".join(f"{lon:.5f}" for _, lon in chunk),
                }
                async with http.get(ELEVATION_API, params=params) as response:
                    response.raise_for_status()
                    elevations += (await response.json())["elevation"]
    except (ClientError, asyncio.TimeoutError, KeyError, TypeError, ValueError) as e:
        logging.warning("Высота недоступна: %r", e)
        return None
    return elevations


_tiles = {}  # правила OSM требуют кэшировать плитки


def leg_path(leg):
    """Вершины пути OSRM между двумя соседними точками, без них самих: [(lat, lon)]."""
    coords = []
    for step in leg.get("steps") or []:
        for lon, lat in (step.get("geometry") or {}).get("coordinates") or []:
            if not coords or coords[-1] != (lat, lon):
                coords.append((lat, lon))
    return [(round(lat, 6), round(lon, 6)) for lat, lon in coords[1:-1]]


async def map_match(points, kind):
    """Привязка трека к дорогам или тропинкам (OSRM map matching). points — [(ts, lat, lon, accuracy, rowid)].

    Шлём точки без выбросов кусками по MAP_MATCH_CHUNK (соседние куски делят одну точку, чтобы путь не рвался),
    не чаще запроса в секунду (правила публичного сервера); только координаты и время, без Telegram ID.
    Кусок с низкой уверенностью OSRM пропускаем. Возвращает [(rowid, lat, lon, via)] привязанных точек или None,
    если ничего не вышло; via — None или (ts предыдущей привязанной точки, [(lat, lon)] — путь по дорогам от неё).
    """
    url = MAP_MATCH_URLS.get(kind)
    points = clean(points, kind)
    if not url or not 2 <= len(points) <= MAP_MATCH_MAX_POINTS:
        return None
    lo, hi = MAP_MATCH_RADIUS_M
    snapped = {}
    try:
        async with ClientSession(timeout=ClientTimeout(total=MAP_MATCH_TIMEOUT_S),
                                 headers={"User-Agent": MAP_USER_AGENT}) as http:
            for start in range(0, len(points) - 1, MAP_MATCH_CHUNK - 1):
                chunk = points[start:start + MAP_MATCH_CHUNK]
                if start:
                    await asyncio.sleep(1)
                coords = ";".join(f"{lon:.6f},{lat:.6f}" for _, lat, lon, *_ in chunk)
                params = {
                    "timestamps": ";".join(str(int(p[0])) for p in chunk),
                    "radiuses": ";".join(str(round(min(max(p[3] or DEFAULT_ACCURACY_M, lo), hi))) for p in chunk),
                    # gaps=ignore: и через долгую паузу между точками нужен путь по дорогам, а не прямая
                    "overview": "false", "steps": "true", "geometries": "geojson", "gaps": "ignore",
                }
                async with http.get(f"{url}/{coords}", params=params) as response:
                    data = await response.json(content_type=None) if response.status == 200 else None
                if not data or data.get("code") != "Ok" or not data.get("matchings"):
                    continue
                if min(m.get("confidence", 0) for m in data["matchings"]) < MAP_MATCH_MIN_CONFIDENCE:
                    continue
                last = {}  # matchings_index → ts предыдущей привязанной точки этого куска пути
                for p, tp in zip(chunk, data.get("tracepoints") or []):
                    if not tp or not tp.get("location"):
                        continue
                    m, w = tp["matchings_index"], tp["waypoint_index"]
                    legs = data["matchings"][m].get("legs") or []
                    path = leg_path(legs[w - 1]) if m in last and 0 < w <= len(legs) else []
                    via = (last[m], path) if path else None  # leg w-1 ведёт от точки w-1 к точке w
                    last[m] = p[0]
                    if p[4] not in snapped:  # общая с прошлым куском — там уже привязана
                        snapped[p[4]] = (p[4], tp["location"][1], tp["location"][0], via)
    except (ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError, IndexError) as e:
        logging.warning("Привязка к дорогам не удалась: %r", e)
        return None
    return list(snapped.values()) or None


async def fetch_tiles(keys):
    """PNG плиток OSM по ключам (z, x, y), по одной; None — сервер недоступен, карточка будет без карты.

    Уходят только номера плиток (район маршрута), без Telegram ID.
    """
    try:
        async with ClientSession(
            timeout=ClientTimeout(total=MAP_TIMEOUT_S), headers={"User-Agent": MAP_USER_AGENT}
        ) as http:
            for key in keys:
                if key not in _tiles:
                    async with http.get(MAP_TILES.format(*key)) as response:
                        response.raise_for_status()
                        _tiles[key] = await response.read()
                    if len(_tiles) > MAP_CACHE_TILES:
                        _tiles.pop(next(iter(_tiles)))  # самая старая
    except (ClientError, asyncio.TimeoutError) as e:
        logging.warning("Карта недоступна: %r", e)
        return None
    return {key: _tiles[key] for key in keys if key in _tiles}


async def card_tiles(track):
    return await fetch_tiles(map_view(track)[-1]) if track else None


async def finish_and_report(bot, ride_id, ts=None, manual=False):
    """Закрывает тренировку и присылает итог — только если её не закрыли раньше.

    manual — закрыли кнопкой или /stop: трансляция в Telegram при этом не останавливается.
    """
    if not db.finish_ride(ride_id, ts):
        return
    ride = db.get_ride(ride_id)
    # Трек — к дорогам (велосипед) или тропинкам (бег, ходьба); не вышло — остаётся как записан.
    try:
        snapped = await asyncio.wait_for(map_match(db.raw_points(ride_id), ride["kind"]), MAP_MATCH_TOTAL_TIMEOUT_S)
    except asyncio.TimeoutError:
        snapped = None
    if snapped and db.snap_ride(ride_id, snapped, MAP_MATCH_MAX_CHANGE):
        ride = db.get_ride(ride_id)
    track = compute_stats(db.get_points(ride_id), 0, ride["kind"])["track"]
    elevations = await fetch_elevations(track) if len(track) > 1 else None
    if elevations:  # сервис недоступен — просто без высоты
        db.set_elevation(ride_id, elevation_gain(elevations, ELEVATION_MIN_STEP_M, ELEVATION_SMOOTH_POINTS))
        ride = db.get_ride(ride_id)
    live = manual and (ride["live_until"] or 0) > time.time()
    lang = db.user_lang(ride["user_id"])
    await bot.send_message(
        ride["chat_id"],
        ride_summary(ride, lang) + (f"\n\n{t(lang, 'live_warning')}" if live else ""),
        parse_mode="HTML",
        reply_markup=open_app_keyboard(
            ride["id"], t(lang, "btn_route"),
            [InlineKeyboardButton(text=t(lang, "btn_share"), callback_data=f"share:{ride['id']}")],
        ),
    )
    await after_ride(bot, db.get_ride(ride_id))


async def send_card(bot, ride, png=None):
    """Шлёт карточку тренировки в чат с владельцем (png — уже нарисованная, иначе пост по умолчанию).
    Возвращает (сообщение, подпись, кнопки)."""
    lang = db.user_lang(ride["user_id"])
    stats = compute_stats(db.get_points(ride["id"]), db.get_weight(ride["user_id"]), ride["kind"])
    if png is None:
        tiles = await card_tiles(stats["track"])
        png = await asyncio.to_thread(render_card, ride["kind"], stats, record_labels(ride, lang), tiles, lang)
    caption = t(lang, "card_caption", kind=t(lang, f"kind_{ride['kind']}"), km=stats["distance_km"],
                moving=fmt_duration(stats["moving_s"]), speed=speed_line(stats, lang), app=APP_NAME)
    # web_app-кнопки работают только в личке с ботом, а карточку пересылают в любые чаты — нужна ссылка.
    # Без Main Mini App в @BotFather ссылка просто откроет чат с ботом.
    link = f"https://t.me/{(await bot.me()).username}?startapp"
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t(lang, "btn_card_open"), url=link)]])
    message = await bot.send_photo(
        ride["chat_id"], BufferedInputFile(png, filename="beout.png"), caption=caption, reply_markup=markup
    )
    return message, caption, markup


_last_alert = 0.0


async def notify_admin(bot, text):
    """Сообщение администраторам; не чаще раза в 5 минут, чтобы не завалить чат."""
    global _last_alert
    if not admin_ids() or time.time() - _last_alert < 300:
        return
    _last_alert = time.time()
    for chat_id in admin_ids():
        try:
            await bot.send_message(chat_id, text[:3500])
        except Exception:
            logging.exception("Не удалось написать администратору %s", chat_id)


async def reply_in_place(callback: CallbackQuery, text, reply_markup=None):
    """Заменяет сообщение с кнопками на text (слишком старые Telegram менять не даёт)."""
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await callback.bot.send_message(
            callback.from_user.id, text, parse_mode="HTML", reply_markup=reply_markup
        )
    await callback.answer()


# ---------- согласие, данные пользователя ----------

@private.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    lang = lang_of(message.from_user)
    if command.args and command.args.startswith("sos_"):
        await sos_invitation(message, command.args.removeprefix("sos_"), lang)
        return
    if not has_consent(message.from_user.id):
        if not db.consent_version(message.from_user.id):
            db.count("start_new")  # обезличенно: сколько раз новые люди открывали бота
        ref = command.args if command.args and REF_ARG.match(command.args) else None
        await message.answer(consent_text(message.from_user.id, lang), parse_mode="HTML",
                             reply_markup=consent_keyboard(lang, ref))
        return
    await set_user_menu(message.bot, message.from_user.id, lang)
    await message.answer(
        welcome_text(message.from_user.id, lang), parse_mode="HTML",
        reply_markup=open_app_keyboard(None, t(lang, "btn_open")),
    )


# ---------- SOS: экстренные контакты ----------
# Бот пишет только тем, кто его запускал, поэтому контакт приходит сам — по ссылке t.me/<bot>?start=sos_<token>.

async def sos_invitation(message, token, lang):
    owner = db.user_by_sos_token(token)
    if owner == message.from_user.id:
        await message.answer(t(lang, "sos_own_link"))
        return
    if not owner:
        await message.answer(t(lang, "sos_bad_link"))
        return
    name = await user_name(message.bot, owner, lang)
    await message.answer(
        t(lang, "sos_invite", name=name, app=APP_NAME),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_sos_yes"), callback_data=f"sos:on:{token}")],
            [InlineKeyboardButton(text=t(lang, "btn_sos_no"), callback_data="sos:no")],
        ]),
    )


@dp.callback_query(F.data.regexp(r"^sos:(on:[\w-]+|off:\d+|no)$"))
async def on_sos_contact(callback: CallbackQuery):
    contact, action = callback.from_user.id, callback.data.split(":")
    lang = lang_of(callback.from_user)
    if action[1] == "no":
        await reply_in_place(callback, t(lang, "sos_declined"))
        return
    if action[1] == "off":  # контакт отказывается сам
        owner = int(action[2])
        db.set_sos_contact(owner, contact, False)
        name = escape(await user_name(callback.bot, owner, lang))
        await reply_in_place(callback, t(lang, "sos_left", name=name))
        return
    owner = db.user_by_sos_token(action[2])
    if not owner or owner == contact:
        await callback.answer(t(lang, "sos_bad_link"), show_alert=True)
        return
    db.set_sos_contact(owner, contact, True, lang)  # контакт мог не принимать условия бота — язык храним тут
    db.rotate_sos_token(owner)  # ссылка одноразовая: пересланная дальше уже не сработает
    await reply_in_place(
        callback,
        t(lang, "sos_accepted", name=escape(await user_name(callback.bot, owner, lang))),
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=t(lang, "btn_sos_off"), callback_data=f"sos:off:{owner}"),
        ]]),
    )
    owner_lang = db.user_lang(owner)
    try:
        await callback.bot.send_message(
            owner, t(owner_lang, "sos_owner_notice", name=await user_name(callback.bot, contact, owner_lang))
        )
    except TelegramAPIError:
        logging.warning("Не удалось сообщить %s о новом контакте", owner)


@dp.callback_query(F.data.regexp(r"^consent(:ref_[\w-]{1,40})?$"))
async def on_consent(callback: CallbackQuery):
    user_id = callback.from_user.id
    newcomer = db.consent_version(user_id) == 0  # приглашением считается только первое согласие
    db.set_consent(user_id, POLICY_VERSION)
    lang = lang_of(callback.from_user)  # после согласия: язык Telegram запоминается
    accepted = t(lang, "consent_accepted")
    if not newcomer:  # обновлённые условия: без приветствия, сразу можно в трекер
        await reply_in_place(callback, f"{t(lang, 'policy_updated')}\n\n{accepted}",
                             open_app_keyboard(None, t(lang, "btn_open")))
        await set_user_menu(callback.bot, user_id, lang)
        return
    await reply_in_place(callback, f"{t(lang, 'consent')}\n\n{accepted}", policy_keyboard(lang))
    await set_user_menu(callback.bot, user_id, lang)
    await callback.bot.send_message(
        user_id, welcome_text(user_id, lang), parse_mode="HTML",
        reply_markup=open_app_keyboard(None, t(lang, "btn_open")),
    )
    if callback.data.startswith("consent:ref_"):
        await count_referral(callback.bot, user_id, callback.data.removeprefix("consent:ref_"))


# ---------- реферальные ссылки: «пригласить друзей» ----------
# t.me/<bot>?start=ref_<token>. Засчитывается, когда новый пользователь принял условия.

REF_ARG = re.compile(r"^ref_[\w-]{1,40}$")


async def count_referral(bot, user_id, token):
    inviter = db.user_by_ref_token(token)
    if not inviter or not db.add_referral(user_id, inviter):
        return
    lang = db.user_lang(inviter)
    name = await user_name(bot, user_id, lang)
    text = t(lang, "ref_notice", name=name, n=len(db.referrals(inviter)), app=APP_NAME)
    try:
        await bot.send_message(inviter, text)
    except TelegramAPIError:
        logging.warning("Не удалось сообщить %s о приглашённом", inviter)


def admin_ids():
    """ADMIN_CHAT_ID: один или несколько Telegram ID через запятую или пробел."""
    return {int(x) for x in re.split(r"[,\s]+", ADMIN_CHAT_ID or "") if re.fullmatch(r"-?\d+", x)}


def is_admin(message):
    return message.chat.id in admin_ids()


@private.message(Command("invites"))
async def cmd_invites(message: Message):
    """Только для администраторов (ADMIN_CHAT_ID): кто сколько и кого пригласил. Подробнее — вкладка «Админ»."""
    if not is_admin(message):
        return
    lang = lang_of(message.from_user)
    totals = db.referral_totals()
    lines = [t(lang, "invites_title", users=db.users_count(), invited=sum(n for _, n in totals))]
    for i, (inviter, n) in enumerate(totals, 1):
        names = [await user_name(message.bot, r["user_id"], lang) for r in db.referrals(inviter)]
        lines.append(f"{i}. {await user_name(message.bot, inviter, lang)} — {n}: {', '.join(reversed(names))}")
    if not totals:
        lines.append(t(lang, "invites_none"))
    await message.answer("\n".join(lines)[:4000])


@private.message(Command("privacy"))
async def cmd_privacy(message: Message):
    lang = lang_of(message.from_user)
    await message.answer(t(lang, "privacy"), reply_markup=policy_keyboard(lang))


@private.message(Command("export"))
async def cmd_export(message: Message):
    lang = lang_of(message.from_user)
    data = json.dumps(db.export_user(message.from_user.id), ensure_ascii=False, indent=1)
    await message.answer_document(
        BufferedInputFile(data.encode(), filename="beout-export.json"), caption=t(lang, "export_caption"),
    )


@private.message(Command("delete"))
async def cmd_delete(message: Message):
    lang = lang_of(message.from_user)
    await message.answer(
        t(lang, "delete_confirm"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_delete_yes"), callback_data="delete:yes")],
            [InlineKeyboardButton(text=t(lang, "btn_cancel"), callback_data="delete:no")],
        ]),
    )


@dp.callback_query(F.data.in_({"delete:yes", "delete:no"}))
async def on_delete(callback: CallbackQuery):
    lang = lang_of(callback.from_user)  # до удаления: потом выбранного языка уже нет
    if callback.data == "delete:no":
        await reply_in_place(callback, t(lang, "delete_cancelled"))
        return
    db.delete_user(callback.from_user.id)
    await reply_in_place(callback, t(lang, "deleted"))


# ---------- команды ----------

@private.message(Command("weight"))
async def cmd_weight(message: Message, command: CommandObject):
    lang = lang_of(message.from_user)
    if not has_consent(message.from_user.id):
        await message.answer(consent_text(message.from_user.id, lang), parse_mode="HTML",
                             reply_markup=consent_keyboard(lang))
        return
    try:
        weight = float((command.args or "").replace(",", "."))
        if not 30 <= weight <= 250:
            raise ValueError
    except ValueError:
        await message.answer(t(lang, "weight_help"))
        return
    db.set_weight(message.from_user.id, weight)
    await message.answer(t(lang, "weight_saved", w=f"{weight:g}"))


@private.message(Command("stop"))
async def cmd_stop(message: Message):
    ride = db.active_ride(message.from_user.id)
    if not ride:
        await message.answer(t(lang_of(message.from_user), "no_active"))
        return
    await finish_and_report(message.bot, ride["id"], manual=True)


@dp.callback_query(F.data.regexp(r"^kind:\d+:(ride|run|walk)$"))
async def on_kind(callback: CallbackQuery):
    _, ride_id, kind = callback.data.split(":")
    lang = lang_of(callback.from_user)
    ride = db.get_ride(int(ride_id))
    if not ride or ride["user_id"] != callback.from_user.id:
        await callback.answer(t(lang, "ride_not_found"))
        return
    if not db.set_ride_kind(ride["id"], kind):  # кнопки под старым сообщением о старте — тренировка уже закончена
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=open_app_keyboard(ride["id"], t(lang, "btn_route")))
        await callback.answer(t(lang, "kind_locked"), show_alert=True)
        return
    db.set_kind(ride["user_id"], kind)  # следующая тренировка начнётся с этого типа
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=start_keyboard(db.get_ride(ride["id"]), lang))
    await callback.answer(t(lang, f"kind_{kind}"))


@dp.callback_query(F.data.regexp(r"^share:\d+$"))
async def on_share(callback: CallbackQuery):
    lang = lang_of(callback.from_user)  # заодно запоминает язык: карточка берёт его из базы
    ride = db.get_ride(int(callback.data.split(":")[1]))
    if not ride or ride["user_id"] != callback.from_user.id:
        await callback.answer(t(lang, "ride_not_found"))
        return
    await callback.answer()
    await send_card(callback.bot, ride)


@dp.callback_query(F.data.regexp(r"^finish:\d+$"))
async def on_finish(callback: CallbackQuery):
    lang = lang_of(callback.from_user)
    ride = db.get_ride(int(callback.data.split(":")[1]))
    if not ride or ride["user_id"] != callback.from_user.id:
        await callback.answer(t(lang, "ride_not_found"))
        return
    await finish_and_report(callback.bot, ride["id"], manual=True)
    # Финиш не останавливает трансляцию в Telegram: бот не может выключить чужую геопозицию.
    await callback.answer(t(lang, "live_warning"), show_alert=True)


# ---------- геопозиция ----------

@private.message(F.location)
async def on_location(message: Message):
    loc = message.location
    lang = lang_of(message.from_user)
    if not loc.live_period:
        await message.answer(t(lang, "not_live"))
        return
    if not has_consent(message.from_user.id):
        await message.answer(
            f"{consent_text(message.from_user.id, lang)}\n\n{t(lang, 'consent_then_restart')}", parse_mode="HTML",
            reply_markup=consent_keyboard(lang),
        )
        return

    ts = to_ts(message.date)
    ride_id = db.start_ride(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        ts=ts,
        live_until=ts + loc.live_period,
        kind=db.get_kind(message.from_user.id),
    )
    db.add_point(ride_id, ts, loc.latitude, loc.longitude, loc.horizontal_accuracy)
    await message.answer(t(lang, "started"), reply_markup=start_keyboard(db.get_ride(ride_id), lang))


@private.edited_message(F.location)
async def on_location_update(message: Message):
    """Каждое обновление трансляции приходит как редактирование того же сообщения."""
    ride = db.find_ride_by_message(message.chat.id, message.message_id)
    if not ride or ride["ended_at"] is not None:
        return

    loc = message.location
    db.add_point(
        ride["id"], to_ts(message.edit_date), loc.latitude, loc.longitude,
        loc.horizontal_accuracy,
    )
    # Когда трансляцию останавливают, приходит последнее обновление без live_period.
    if not loc.live_period:
        await finish_and_report(message.bot, ride["id"])


# ---------- группы: рейтинг, лента, челленджи, сборы, дуэли, битва клубов ----------
# В группе бот видит только команды и нажатия своих кнопок. Имя и цифры человека группа видит, только если он
# сам нажал «Участвовать», «Публиковать», «Иду» или принял дуэль.

GROUP_COMMANDS = ("top", "challenge", "run", "duel", "clubwar", "reminders", "help")


def group_commands(lang):
    return [BotCommand(command=name, description=t(lang, f"cmd_{name}")) for name in GROUP_COMMANDS]


def week_start(now):
    """Понедельник 00:00 UTC текущей недели, unix time."""
    # shortcut: неделя по UTC — в Европе граница сдвинута на 1–3 часа; хранить пояс группы, если будут жалобы.
    day = datetime.fromtimestamp(now, timezone.utc).date()
    monday = day - timedelta(days=day.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc).timestamp()


def month_start(now, shift=0):
    """1-е число месяца 00:00 UTC; shift — на сколько месяцев вперёд."""
    d = datetime.fromtimestamp(now, timezone.utc)
    year, month = divmod(d.year * 12 + d.month - 1 + shift, 12)
    return datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp()


async def member_name(bot, chat_id, user_id):
    """Имя участника группы; None — вышел из группы."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return None
    return None if member.status in ("left", "kicked") else member.user.full_name


async def shown_name(bot, chat_id, user_id, lang):
    """Имя для текстов группы: вышел — «Участник»."""
    return await member_name(bot, chat_id, user_id) or t(lang, "participant")


async def is_admin_of(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return False
    return member.status in ("creator", "administrator")


def group_tz(chat_id):
    row = db.group(chat_id)
    return (row and row["tz"]) or 0


def kind_suffix(lang, kind):
    return f" · {t(lang, f'kind_{kind}').lower()}" if kind else ""


def until_text(ts, tz):
    return f"{clubs.local(ts, tz):%d.%m %H:%M}"


async def in_group(message, lang):
    """Команда для групп: в личке — подсказка. Заодно запоминаем группу, её название и часовой пояс."""
    if message.chat.type not in GROUP_CHATS:
        await message.answer(t(lang, "group_only"))
        return False
    db.add_group(message.chat.id, lang)
    db.set_group(message.chat.id, title=message.chat.title)
    tz = db.user_tz(message.from_user.id)
    if tz is not None:
        db.set_group(message.chat.id, tz=tz)
    return True


async def edit_text(bot, chat_id, message_id, text, markup=None):
    """Обновить сообщение группы; «не изменилось» и удалённое сообщение — не ошибка."""
    if not message_id:
        return
    try:
        await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    except TelegramAPIError as e:
        if "not modified" not in str(e):
            logging.info("Сообщение %s/%s не обновлено: %r", chat_id, message_id, e)


async def post(bot, chat_id, text, markup=None, reply_to=None):
    """Сообщение в группу; бота удалили — забываем группу. Возвращает сообщение или None."""
    try:
        return await bot.send_message(
            chat_id, text, reply_markup=markup,
            reply_parameters=reply_to and ReplyParameters(message_id=reply_to, allow_sending_without_reply=True),
        )
    except TelegramForbiddenError:
        db.forget_group(chat_id)
    except TelegramAPIError:
        logging.exception("Не удалось написать в группу %s", chat_id)
    return None


async def needs_consent(callback, lang):
    """Нажал кнопку в группе, не приняв условия в боте — объясняем, что делать."""
    if has_consent(callback.from_user.id):
        return False
    await callback.answer(t(lang, "rating_consent_first", bot=(await callback.bot.me()).username), show_alert=True)
    return True


# --- рейтинг: неделя, месяц, серии ---

async def rating_text(bot, chat_id, since, until, title_key, lang):
    """Рейтинг по дистанции за [since, until), отдельно по типам. Простой текст: имена не экранировать."""
    lines = [f"{t(lang, title_key)} {datetime.fromtimestamp(since, timezone.utc):%d.%m}–"
             f"{datetime.fromtimestamp(until - 1, timezone.utc):%d.%m}"]
    rating = db.group_rating(chat_id, since, until)
    for kind in ACTIVITIES:
        places = []
        for user_id, km in rating.get(kind, []):
            name = await member_name(bot, chat_id, user_id)
            if name:
                places.append(t(lang, "rating_place", n=len(places) + 1, name=name, km=km))
            if len(places) == RATING_TOP_N:
                break
        if places:
            lines += ["", t(lang, f"kind_{kind}"), *places]
    if len(lines) == 1:
        lines += ["", t(lang, "rating_empty")]
    return lines


async def streak_text(bot, chat_id, now, lang):
    """Серии участников: дней и недель подряд с тренировкой (по их часовому поясу)."""
    rides = db.group_rides(chat_id, now - 400 * 86400, now + 1)
    starts = {}
    for r in rides:
        starts.setdefault(r["user_id"], []).append(r["started_at"])
    rows = []
    for user_id, ts in starts.items():
        tz = db.user_tz(user_id) or 0
        days, weeks = streaks([local_date(x, tz) for x in ts], local_date(now, tz))
        if days or weeks:
            rows.append((days, weeks, user_id))
    lines = [t(lang, "rating_streak")]
    places = []
    for days, weeks, user_id in sorted(rows, reverse=True):
        name = await member_name(bot, chat_id, user_id)
        if name:
            places.append(t(lang, "streak_place", n=len(places) + 1, name=name, days=days, weeks=weeks))
        if len(places) == RATING_TOP_N:
            break
    return lines + ([""] + places if places else ["", t(lang, "rating_empty")])


async def top_text(bot, chat_id, tab, lang, now):
    if tab == "month":
        lines = await rating_text(bot, chat_id, month_start(now), month_start(now, 1), "rating_month", lang)
    elif tab == "streak":
        lines = await streak_text(bot, chat_id, now, lang)
    else:
        lines = await rating_text(bot, chat_id, week_start(now), week_start(now) + 7 * 86400, "rating_week", lang)
    return "\n".join(lines + ["", t(lang, "rating_footer")])


def top_keyboard(lang, tab="week"):
    tabs = [InlineKeyboardButton(text=("• " if key == tab else "") + t(lang, f"btn_tab_{key}"), callback_data=f"top:{key}")
            for key in ("week", "month", "streak")]
    return InlineKeyboardMarkup(inline_keyboard=[
        tabs,
        [InlineKeyboardButton(text=t(lang, "btn_join"), callback_data="rating:join"),
         InlineKeyboardButton(text=t(lang, "btn_leave"), callback_data="rating:leave")],
        [InlineKeyboardButton(text=t(lang, "btn_feed_on"), callback_data="feed:on"),
         InlineKeyboardButton(text=t(lang, "btn_feed_off"), callback_data="feed:off")],
    ])


rating_keyboard = top_keyboard


@dp.message(Command("top"))
async def cmd_top(message: Message):
    lang = lang_of(message.from_user)
    if message.chat.type not in GROUP_CHATS:
        await message.answer(t(lang, "top_private"))
        return
    await in_group(message, lang)  # на этом языке пойдёт и автопост по понедельникам
    await message.answer(await top_text(message.bot, message.chat.id, "week", lang, time.time()),
                         reply_markup=top_keyboard(lang))


@dp.callback_query(F.data.regexp(r"^top:(week|month|streak)$"))
async def on_top_tab(callback: CallbackQuery):
    lang = db.group_lang(callback.message.chat.id)
    tab = callback.data.split(":")[1]
    await edit_text(callback.bot, callback.message.chat.id, callback.message.message_id,
                    await top_text(callback.bot, callback.message.chat.id, tab, lang, time.time()),
                    top_keyboard(lang, tab))
    await callback.answer()


@dp.callback_query(F.data.in_({"rating:join", "rating:leave"}))
async def on_rating(callback: CallbackQuery):
    user_id, join = callback.from_user.id, callback.data == "rating:join"
    lang = lang_of(callback.from_user)
    if join and await needs_consent(callback, lang):
        return
    db.set_group_member(callback.message.chat.id, user_id, join)
    await callback.answer(t(lang, "rating_joined" if join else "rating_left"), show_alert=True)


@dp.callback_query(F.data.in_({"feed:on", "feed:off"}))
async def on_feed(callback: CallbackQuery):
    on = callback.data == "feed:on"
    lang = lang_of(callback.from_user)
    if on and await needs_consent(callback, lang):
        return
    db.set_feed(callback.message.chat.id, callback.from_user.id, on)
    await callback.answer(t(lang, "feed_on" if on else "feed_off"), show_alert=True)


@dp.message(Command("help"), F.chat.type.in_(GROUP_CHATS))
async def cmd_group_help(message: Message):
    lang = lang_of(message.from_user)
    await in_group(message, lang)
    await message.answer(t(lang, "group_help", bot=(await message.bot.me()).username))


# --- итоги недели: рейтинг, номинации, картинка ---

async def week_summary(bot, chat_id, since, lang):
    """Текст итогов недели с номинациями и PNG-картинка (None — никто не тренировался)."""
    until = since + 7 * 86400
    lines = await rating_text(bot, chat_id, since, until, "rating_results", lang)
    rides = db.group_rides(chat_id, since, until)
    prev = db.group_km(chat_id, since - 7 * 86400, since)
    awards = clubs.nominations(rides, prev, lambda user_id: db.user_tz(user_id) or 0)
    award_lines, award_cells = [], []
    for key, user_id, value in awards:
        name = await member_name(bot, chat_id, user_id)
        if not name:
            continue
        award_lines.append(t(lang, key, name=name, value=value))
        short = key.removeprefix("nom_")
        award_cells.append((t(lang, f"nom_short_{short}"), name, t(lang, f"nom_value_{short}", value=value)))
    if award_lines:
        lines += ["", t(lang, "nom_title"), *award_lines]
    lines += ["", t(lang, "rating_footer")]
    km = db.group_km(chat_id, since, until)
    rows = []
    for user_id, value in sorted(km.items(), key=lambda kv: -kv[1]):
        name = await member_name(bot, chat_id, user_id)
        if name and value > 0:
            rows.append((name, value))
    image = None
    if rows:
        title = (db.group(chat_id) or {"title": None})["title"] or t(lang, "group_untitled")
        period = (f"{datetime.fromtimestamp(since, timezone.utc):%d.%m}–"
                  f"{datetime.fromtimestamp(until - 1, timezone.utc):%d.%m}")
        image = await asyncio.to_thread(render_week, title, period, sum(v for _, v in rows), len(rows), rows,
                                        award_cells, lang)
    return "\n".join(lines), image


async def post_weekly_ratings(bot, now):
    """По понедельникам — итоги прошлой недели во все группы, где есть участники: картинка и рейтинг."""
    dt = datetime.fromtimestamp(now, timezone.utc)
    if dt.weekday() != 0 or dt.hour < RATING_POST_HOUR_UTC:
        return
    week = f"{dt:%G-W%V}"
    for chat_id in db.groups_to_post(week):
        db.mark_posted(chat_id, week)  # до отправки: сбой не повторяется каждую минуту
        try:
            lang = db.group_lang(chat_id)
            text, image = await week_summary(bot, chat_id, week_start(now) - 7 * 86400, lang)
            if image:
                await bot.send_photo(chat_id, BufferedInputFile(image, filename="beout-week.png"))
            await bot.send_message(chat_id, text, reply_markup=top_keyboard(lang))
        except TelegramForbiddenError:
            db.forget_group(chat_id)  # бота удалили из группы
        except TelegramAPIError:
            logging.exception("Не удалось отправить рейтинг в %s", chat_id)


# --- лента: тренировки участников, которые включили «Публиковать» ---

async def post_to_feeds(bot, ride):
    chats = db.feed_groups(ride["user_id"])
    stats = stats_of(ride)
    if not chats or stats["distance_km"] < FEED_MIN_KM:
        return
    full = compute_stats(db.get_points(ride["id"]), db.get_weight(ride["user_id"]), ride["kind"])
    tiles = await card_tiles(full["track"])
    file = None
    for chat_id in chats:
        lang = db.group_lang(chat_id)
        name = await member_name(bot, chat_id, ride["user_id"])
        if not name:
            continue
        if file is None:  # рисуем один раз — на языке первой группы
            png = await asyncio.to_thread(render_card, ride["kind"], full, record_labels(ride, lang), tiles, lang)
            file = BufferedInputFile(png, filename="beout.png")
        caption = t(lang, "feed_caption", name=name, kind=t(lang, f"kind_{ride['kind']}").lower(),
                    km=stats["distance_km"], moving=fmt_duration(stats["moving_s"]), speed=speed_line(stats, lang))
        link = f"https://t.me/{(await bot.me()).username}?start=feed"
        try:
            sent = await bot.send_photo(chat_id, file, caption=caption, reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=t(lang, "btn_card_open"), url=link)]]))
            file = sent.photo[-1].file_id  # дальше — тем же файлом, без повторной загрузки
        except TelegramForbiddenError:
            db.forget_group(chat_id)
        except TelegramAPIError:
            logging.exception("Не удалось опубликовать тренировку в %s", chat_id)


# --- челленджи ---

async def challenge_text(bot, challenge, lang):
    tz = group_tz(challenge["chat_id"])
    goal = clubs.fmt_km(challenge["goal_km"])
    km = db.group_km(challenge["chat_id"], challenge["starts_at"], challenge["ends_at"], challenge["kind"])
    kind = kind_suffix(lang, challenge["kind"])
    until = until_text(challenge["ends_at"], tz)
    if not challenge["each"]:
        done = sum(km.values())
        lines = [t(lang, "challenge_title", goal=goal, kind=kind, until=until),
                 clubs.progress_bar(done, challenge["goal_km"]),
                 t(lang, "challenge_done", done=clubs.fmt_km(done), goal=goal)]
    else:
        lines = [t(lang, "challenge_title_each", goal=goal, kind=kind, until=until), ""]
        for user_id in sorted(db.group_members(challenge["chat_id"]), key=lambda u: -km.get(u, 0)):
            done = km.get(user_id, 0)
            name = await member_name(bot, challenge["chat_id"], user_id)
            if name:
                lines.append(t(lang, "challenge_line", mark="✅" if done >= challenge["goal_km"] else "▫️",
                               name=name, done=clubs.fmt_km(done), goal=goal))
    return "\n".join(lines + ["", t(lang, "challenge_footer")])


@dp.message(Command("challenge"))
async def cmd_challenge(message: Message, command: CommandObject):
    lang = lang_of(message.from_user)
    if not await in_group(message, lang):
        return
    chat_id, args = message.chat.id, (command.args or "").strip()
    current = db.active_challenge(chat_id)
    if args.lower() in ("stop", "стоп"):
        if not current:
            await message.answer(t(lang, "challenge_none"))
        elif current["creator_id"] != message.from_user.id and not await is_admin_of(message.bot, chat_id,
                                                                                   message.from_user.id):
            await message.answer(t(lang, "only_admins"))
        else:
            db.update_challenge(current["id"], closed=1)
            await message.answer(t(lang, "challenge_stopped"))
        return
    if current:
        await message.answer(t(lang, "challenge_exists"))
        return
    parsed = clubs.parse_challenge(args)
    if not parsed:
        await message.answer(t(lang, "challenge_usage"))
        return
    goal, kind, each, days = parsed
    challenge = db.create_challenge(chat_id, message.from_user.id, goal, kind, each, days)
    sent = await message.answer(await challenge_text(message.bot, challenge, lang), reply_markup=top_keyboard(lang))
    db.update_challenge(challenge["id"], message_id=sent.message_id)


async def refresh_challenge(bot, challenge, ride=None):
    """Обновить прогресс; общая цель достигнута — поздравить один раз; личная — поздравить того, кто её добил."""
    lang = db.group_lang(challenge["chat_id"])
    challenge = db.get_challenge(challenge["id"])
    await edit_text(bot, challenge["chat_id"], challenge["message_id"], await challenge_text(bot, challenge, lang),
                    top_keyboard(lang))
    km = db.group_km(challenge["chat_id"], challenge["starts_at"], challenge["ends_at"], challenge["kind"])
    goal = clubs.fmt_km(challenge["goal_km"])
    if not challenge["each"] and not challenge["reached_at"] and sum(km.values()) >= challenge["goal_km"]:
        db.update_challenge(challenge["id"], reached_at=time.time())
        await post(bot, challenge["chat_id"], t(lang, "challenge_reached", goal=goal), reply_to=challenge["message_id"])
    if challenge["each"] and ride and ride["user_id"] in km:
        after = km[ride["user_id"]]
        before = after - (stats_of(ride)["distance_km"] if not challenge["kind"] or ride["kind"] == challenge["kind"] else 0)
        if before < challenge["goal_km"] <= after:
            name = await shown_name(bot, challenge["chat_id"], ride["user_id"], lang)
            await post(bot, challenge["chat_id"], t(lang, "challenge_reached_each", name=name, goal=goal),
                       reply_to=challenge["message_id"])


async def close_challenge(bot, challenge):
    lang = db.group_lang(challenge["chat_id"])
    db.update_challenge(challenge["id"], closed=1)
    km = db.group_km(challenge["chat_id"], challenge["starts_at"], challenge["ends_at"], challenge["kind"])
    goal, done = challenge["goal_km"], sum(km.values())
    if challenge["each"]:
        members = db.group_members(challenge["chat_id"])
        text = t(lang, "challenge_end_each", n=sum(1 for u in members if km.get(u, 0) >= goal), total=len(members))
    else:
        text = t(lang, "challenge_end_ok" if done >= goal else "challenge_end_fail",
                 done=clubs.fmt_km(done), goal=clubs.fmt_km(goal))
    await post(bot, challenge["chat_id"], text, reply_to=challenge["message_id"])


# --- сборы на тренировку ---

def event_keyboard(event, lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "btn_go"), callback_data=f"ev:{event['id']}:go"),
         InlineKeyboardButton(text=t(lang, "btn_maybe"), callback_data=f"ev:{event['id']}:maybe"),
         InlineKeyboardButton(text=t(lang, "btn_no"), callback_data=f"ev:{event['id']}:no")],
        [InlineKeyboardButton(text=t(lang, "btn_cancel_event"), callback_data=f"ev:{event['id']}:cancel")],
    ])


async def names(bot, chat_id, user_ids, lang):
    return ", ".join([await shown_name(bot, chat_id, u, lang) for u in user_ids])


async def event_text(bot, event, lang):
    when = clubs.local(event["starts_at"], event["tz"])
    weekday = t(lang, f"weekday_{when.weekday()}")
    lines = [t(lang, "event_title", place=event["place"],
               when=f"{weekday} {when:%d.%m} · {when:%H:%M} ({clubs.tz_label(event['tz'])})"), ""]
    answers = db.rsvps(event["id"])
    if answers.get("go"):
        lines.append(t(lang, "event_going", n=len(answers["go"]), names=await names(bot, event["chat_id"], answers["go"], lang)))
    if answers.get("maybe"):
        lines.append(t(lang, "event_maybe", n=len(answers["maybe"]),
                       names=await names(bot, event["chat_id"], answers["maybe"], lang)))
    if not answers.get("go") and not answers.get("maybe"):
        lines.append(t(lang, "event_nobody"))
    if event["stage"] < 0:
        lines += ["", t(lang, "event_cancelled")]
    elif event["stage"] < 2:
        lines += ["", t(lang, "event_footer")]
    return "\n".join(lines)


@dp.message(Command("run"))
async def cmd_run(message: Message, command: CommandObject):
    lang = lang_of(message.from_user)
    if not await in_group(message, lang):
        return
    user_tz = db.user_tz(message.from_user.id)
    tz = user_tz if user_tz is not None else group_tz(message.chat.id)
    parsed = clubs.parse_event(command.args, time.time(), tz)
    if not parsed:
        await message.answer(t(lang, "event_usage"))
        return
    event = db.create_event(message.chat.id, message.from_user.id, parsed[0], tz, parsed[1][:200])
    if has_consent(message.from_user.id):
        db.set_rsvp(event["id"], message.from_user.id, "go")  # кто зовёт — тот идёт
    text = await event_text(message.bot, event, lang)
    if user_tz is None:
        text += "\n\n" + t(lang, "event_tz_hint", tz=clubs.tz_label(tz))
    sent = await message.answer(text, reply_markup=event_keyboard(event, lang))
    db.update_event(event["id"], message_id=sent.message_id)


@dp.callback_query(F.data.regexp(r"^ev:\d+:(go|maybe|no|cancel)$"))
async def on_event(callback: CallbackQuery):
    _, event_id, action = callback.data.split(":")
    event = db.get_event(int(event_id))
    lang = lang_of(callback.from_user)
    if not event or event["stage"] < 0 or event["stage"] >= 2:
        await callback.answer(t(lang, "event_closed"), show_alert=True)
        return
    group_lang = db.group_lang(event["chat_id"])
    if action == "cancel":
        if event["creator_id"] != callback.from_user.id and not await is_admin_of(
                callback.bot, event["chat_id"], callback.from_user.id):
            await callback.answer(t(lang, "only_admins"), show_alert=True)
            return
        db.update_event(event["id"], stage=-1)
        await edit_text(callback.bot, event["chat_id"], event["message_id"],
                        await event_text(callback.bot, db.get_event(event["id"]), group_lang))
        await callback.answer()
        return
    if await needs_consent(callback, lang):
        return
    db.set_rsvp(event["id"], callback.from_user.id, action)
    await edit_text(callback.bot, event["chat_id"], event["message_id"],
                    await event_text(callback.bot, event, group_lang), event_keyboard(event, group_lang))
    await callback.answer(t(lang, f"rsvp_{action}"))


async def advance_event(bot, event, stage, now):
    """Этапы сбора: 1 — напомнить за час, 2 — открыть совместную тренировку, 3 — итоги."""
    lang = db.group_lang(event["chat_id"])
    db.update_event(event["id"], stage=stage)
    going = db.rsvps(event["id"]).get("go", [])
    when = clubs.local(event["starts_at"], event["tz"])
    if stage == 1 and going:
        await post(bot, event["chat_id"], t(lang, "event_reminder", place=event["place"], time=f"{when:%H:%M}",
                                            names=await names(bot, event["chat_id"], going, lang)),
                   reply_to=event["message_id"])
    elif stage == 2:
        await edit_text(bot, event["chat_id"], event["message_id"], await event_text(bot, db.get_event(event["id"]), lang))
        if not going:
            return
        session = db.create_session(event["creator_id"], now)
        db.update_event(event["id"], session_id=session["id"])
        await post(bot, event["chat_id"], t(lang, "event_start", place=event["place"]), InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=t(lang, "btn_event_join"), url=await session_link(bot, session))]]),
            reply_to=event["message_id"])
    elif stage == 3 and event["session_id"]:
        km = {}
        for ride in db.session_rides(event["session_id"]):
            if ride["ended_at"] is not None:
                km[ride["user_id"]] = km.get(ride["user_id"], 0) + (ride["distance_km"] or 0)
        km = {u: v for u, v in km.items() if v > 0}
        if not km:
            return
        lines = [t(lang, "event_summary", place=event["place"], n=len(km), km=clubs.fmt_km(sum(km.values())))]
        for user_id, value in sorted(km.items(), key=lambda kv: -kv[1]):
            lines.append(t(lang, "event_summary_line", name=await shown_name(bot, event["chat_id"], user_id, lang),
                           km=clubs.fmt_km(value)))
        await post(bot, event["chat_id"], "\n".join(lines), reply_to=event["message_id"])


# --- дуэли ---

def duel_days_kind(args):
    kind, days = None, DUEL_DAYS
    tokens = (args or "").split()
    for i, tok in enumerate(tokens):
        if clubs.kind_of(tok):
            kind = clubs.kind_of(tok)
        elif tok.isdigit() and 1 <= int(tok) <= 30:
            days = int(tok)
    return kind, days


async def duel_text(bot, duel, lang):
    kind = kind_suffix(lang, duel["kind"])
    a = await shown_name(bot, duel["chat_id"], duel["a_id"], lang)
    if duel["status"] == "open":
        if duel["b_id"]:
            b = await shown_name(bot, duel["chat_id"], duel["b_id"], lang)
            return t(lang, "duel_call", a=a, b=b, days=duel["days"], kind=kind)
        return t(lang, "duel_call_open", a=a, days=duel["days"], kind=kind)
    b = await shown_name(bot, duel["chat_id"], duel["b_id"], lang)
    if duel["status"] == "declined":
        return t(lang, "duel_call", a=a, b=b, days=duel["days"], kind=kind) + "\n\n" + t(lang, "duel_declined")
    a_km = db.user_km(duel["a_id"], duel["starts_at"], duel["ends_at"], duel["kind"])
    b_km = db.user_km(duel["b_id"], duel["starts_at"], duel["ends_at"], duel["kind"])
    return t(lang, "duel_active", kind=kind, until=until_text(duel["ends_at"], group_tz(duel["chat_id"])),
             a=a, b=b, a_km=clubs.fmt_km(a_km), b_km=clubs.fmt_km(b_km))


def duel_keyboard(duel, lang):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(lang, "btn_duel_accept"), callback_data=f"duel:{duel['id']}:accept"),
        InlineKeyboardButton(text=t(lang, "btn_duel_decline"), callback_data=f"duel:{duel['id']}:decline"),
    ]])


@dp.message(Command("duel"))
async def cmd_duel(message: Message, command: CommandObject):
    lang = lang_of(message.from_user)
    if not await in_group(message, lang):
        return
    if not has_consent(message.from_user.id):
        await message.answer(t(lang, "rating_consent_first", bot=(await message.bot.me()).username))
        return
    rival = message.reply_to_message and message.reply_to_message.from_user
    if rival and (rival.is_bot or rival.id == message.from_user.id):
        rival = None
    kind, days = duel_days_kind(command.args)
    duel = db.create_duel(message.chat.id, message.from_user.id, rival and rival.id, kind, days)
    sent = await message.answer(await duel_text(message.bot, duel, lang), reply_markup=duel_keyboard(duel, lang))
    db.update_duel(duel["id"], message_id=sent.message_id)


@dp.callback_query(F.data.regexp(r"^duel:\d+:(accept|decline)$"))
async def on_duel(callback: CallbackQuery):
    _, duel_id, action = callback.data.split(":")
    duel = db.get_duel(int(duel_id))
    lang = lang_of(callback.from_user)
    user_id = callback.from_user.id
    if not duel or duel["status"] != "open":
        await callback.answer(t(lang, "duel_closed"), show_alert=True)
        return
    group_lang = db.group_lang(duel["chat_id"])
    if action == "decline":
        if user_id not in (duel["a_id"], duel["b_id"]):
            await callback.answer(t(lang, "duel_not_you"), show_alert=True)
            return
        db.update_duel(duel["id"], status="declined", b_id=duel["b_id"] or user_id)
    else:
        if user_id == duel["a_id"]:
            await callback.answer(t(lang, "duel_self"), show_alert=True)
            return
        if duel["b_id"] and duel["b_id"] != user_id:
            await callback.answer(t(lang, "duel_not_you"), show_alert=True)
            return
        if await needs_consent(callback, lang):
            return
        now = time.time()
        db.update_duel(duel["id"], b_id=user_id, starts_at=now, ends_at=now + duel["days"] * 86400, status="active")
    await edit_text(callback.bot, duel["chat_id"], duel["message_id"],
                    await duel_text(callback.bot, db.get_duel(duel["id"]), group_lang))
    await callback.answer()


async def finish_duel(bot, duel):
    lang = db.group_lang(duel["chat_id"])
    if duel["status"] == "open":
        db.update_duel(duel["id"], status="declined")
        await edit_text(bot, duel["chat_id"], duel["message_id"],
                        await duel_text(bot, duel, lang) + "\n\n" + t(lang, "duel_expired"))
        return
    db.update_duel(duel["id"], status="done")
    km = {u: db.user_km(u, duel["starts_at"], duel["ends_at"], duel["kind"]) for u in (duel["a_id"], duel["b_id"])}
    (w, w_km), (_, l_km) = sorted(km.items(), key=lambda kv: -kv[1])
    if w_km == l_km:
        text = t(lang, "duel_draw", km=clubs.fmt_km(w_km))
    else:
        text = t(lang, "duel_win", winner=await shown_name(bot, duel["chat_id"], w, lang),
                 w_km=clubs.fmt_km(w_km), l_km=clubs.fmt_km(l_km))
    await post(bot, duel["chat_id"], text, reply_to=duel["message_id"])


# --- битва клубов ---

def group_title(chat_id, lang):
    row = db.group(chat_id)
    return (row and row["title"]) or t(lang, "group_untitled")


def war_km(war, chat_id):
    return sum(db.group_km(chat_id, war["starts_at"], war["ends_at"]).values())


def war_text(war, chat_id, lang):
    """С точки зрения группы chat_id: она — первая строка."""
    other = war["b_chat"] if chat_id == war["a_chat"] else war["a_chat"]
    return t(lang, "war_active", until=until_text(war["ends_at"], group_tz(chat_id)),
             a=group_title(chat_id, lang), a_km=clubs.fmt_km(war_km(war, chat_id)),
             b=group_title(other, lang), b_km=clubs.fmt_km(war_km(war, other)))


@dp.message(Command("clubwar"))
async def cmd_clubwar(message: Message, command: CommandObject):
    lang = lang_of(message.from_user)
    if not await in_group(message, lang):
        return
    if not await is_admin_of(message.bot, message.chat.id, message.from_user.id):
        await message.answer(t(lang, "only_admins"))
        return
    tokens = (command.args or "").split()
    code = next((tok for tok in tokens if any(ch.isalpha() for ch in tok)), None)
    days = next((int(tok) for tok in tokens if tok.isdigit() and 1 <= int(tok) <= 30), WAR_DAYS)
    if not code:
        war = db.create_war(message.chat.id, days)
        await message.answer(t(lang, "war_created", days=days, code=war["code"]))
        return
    war = db.war_by_code(code)
    if not war:
        await message.answer(t(lang, "war_bad_code"))
        return
    if war["a_chat"] == message.chat.id:
        await message.answer(t(lang, "war_same"))
        return
    now = time.time()
    db.update_war(war["id"], b_chat=message.chat.id, starts_at=now, ends_at=now + war["days"] * 86400, status="active")
    war = db.get_war(war["id"])
    for chat_id, column in ((war["a_chat"], "a_msg"), (war["b_chat"], "b_msg")):
        sent = await post(message.bot, chat_id, war_text(war, chat_id, db.group_lang(chat_id)))
        if sent:
            db.update_war(war["id"], **{column: sent.message_id})


async def refresh_war(bot, war):
    war = db.get_war(war["id"])
    for chat_id, column in ((war["a_chat"], "a_msg"), (war["b_chat"], "b_msg")):
        await edit_text(bot, chat_id, war[column], war_text(war, chat_id, db.group_lang(chat_id)))


async def finish_war(bot, war):
    db.update_war(war["id"], status="done")
    if not war["b_chat"]:
        return  # код никто не ввёл
    for chat_id, column in ((war["a_chat"], "a_msg"), (war["b_chat"], "b_msg")):
        lang = db.group_lang(chat_id)
        scores = sorted(((war_km(war, c), group_title(c, lang)) for c in (war["a_chat"], war["b_chat"])), reverse=True)
        (w_km, winner), (l_km, _) = scores
        text = (t(lang, "war_draw", km=clubs.fmt_km(w_km)) if w_km == l_km
                else t(lang, "war_win", winner=winner, w_km=clubs.fmt_km(w_km), l_km=clubs.fmt_km(l_km)))
        await post(bot, chat_id, text, reply_to=war[column])


# --- напоминания группе ---

@dp.message(Command("reminders"))
async def cmd_reminders(message: Message, command: CommandObject):
    lang = lang_of(message.from_user)
    if not await in_group(message, lang):
        return
    arg = (command.args or "").strip().lower()
    if arg not in ("on", "off"):
        await message.answer(t(lang, "reminders_usage"))
        return
    if not await is_admin_of(message.bot, message.chat.id, message.from_user.id):
        await message.answer(t(lang, "only_admins"))
        return
    db.set_group(message.chat.id, reminders=int(arg == "on"))
    await message.answer(t(lang, f"reminders_{arg}"))


async def nudge_groups(bot, now):
    """Вечером (17–21 по времени группы): давно не тренировались — позвать; серия клуба под угрозой — напомнить."""
    for group in db.groups_for_nudge():
        tz = group["tz"] or 0
        local_now = clubs.local(now, tz)
        if not NUDGE_HOURS[0] <= local_now.hour < NUDGE_HOURS[1]:
            continue
        last_nudge = group["nudged_at"] or 0
        rides = db.group_rides(group["chat_id"], now - 60 * 86400, now + 1)
        if not rides:
            continue  # группа ещё ни разу не тренировалась — не надоедаем
        idle_days = int((now - rides[-1]["started_at"]) // 86400)
        lang = group["lang"] or DEFAULT_LANG
        text = None
        if idle_days >= NUDGE_IDLE_DAYS and now - last_nudge >= NUDGE_IDLE_DAYS * 86400:
            text = t(lang, "nudge_idle", days=idle_days)
        elif now - last_nudge >= 20 * 3600:
            days = {clubs.local(r["started_at"], tz).date() for r in rides}
            today = local_now.date()
            streak, day = 0, today - timedelta(days=1)
            while day in days:
                streak, day = streak + 1, day - timedelta(days=1)
            if today not in days and streak >= NUDGE_STREAK_DAYS:
                text = t(lang, "nudge_streak", days=streak)
        if text:
            db.set_group(group["chat_id"], nudged_at=now)
            await post(bot, group["chat_id"], text)


# --- после каждой тренировки и по таймеру ---

async def after_ride(bot, ride):
    """Тренировка завершена: лента, челленджи, дуэли и битвы клубов. Ошибки здесь итог не ломают."""
    try:
        await post_to_feeds(bot, ride)
        for chat_id in db.member_groups(ride["user_id"]):
            challenge = db.active_challenge(chat_id)
            if challenge:
                await refresh_challenge(bot, challenge, ride)
            for war in db.chat_wars(chat_id):
                await refresh_war(bot, war)
        for duel in db.user_duels(ride["user_id"]):
            await edit_text(bot, duel["chat_id"], duel["message_id"], await duel_text(bot, duel, db.group_lang(duel["chat_id"])))
    except Exception:
        logging.exception("Групповые активности после тренировки %s", ride["id"])


# --- inline-режим: @бот в любом чате — карточка последней тренировки, неделя, рекорды, приглашение ---
# Включается в @BotFather: /setinline. Картинку Telegram забирает по ссылке на наш сервер (JPEG).

_inline_cards = {}  # token → (до какого времени, ride_id, JPEG или None)


async def inline_card(request):
    token = request.match_info["token"]
    until, ride_id, image = _inline_cards.get(token, (0, None, None))
    if until < time.time():
        raise web.HTTPNotFound()
    if image is None:
        ride = db.get_ride(ride_id)
        lang = db.user_lang(ride["user_id"])
        stats = compute_stats(db.get_points(ride_id), db.get_weight(ride["user_id"]), ride["kind"])
        image = await asyncio.to_thread(render, "post", ride["kind"], stats, "map", FIELDS, record_labels(ride, lang),
                                        await card_tiles(stats["track"]), lang=lang, as_jpeg=True)
        _inline_cards[token] = (until, ride_id, image)
    return web.Response(body=image, content_type="image/jpeg", headers={"Cache-Control": "no-store"})


@dp.inline_query()
async def on_inline(query: InlineQuery):
    user_id = query.from_user.id
    lang = lang_of(query.from_user)
    if not has_consent(user_id):
        await query.answer([], cache_time=0, is_personal=True, button=InlineQueryResultsButton(
            text=t(lang, "inline_start", app=APP_NAME), start_parameter="inline"))
        return
    bot_link = f"https://t.me/{(await query.bot.me()).username}"
    open_button = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(lang, "btn_card_open"), url=f"{bot_link}?startapp")]])
    results = []
    ride = db.last_finished_ride(user_id)
    if ride and (ride["distance_km"] or 0) > 0:
        now = time.time()
        for token in [k for k, (until, *_) in _inline_cards.items() if until < now]:
            del _inline_cards[token]
        token = secrets.token_urlsafe(16)
        _inline_cards[token] = (now + INLINE_CARD_TTL_S, ride["id"], None)
        url = f"{WEBAPP_URL}/inline/{token}.jpg"
        stats = stats_of(ride)
        caption = t(lang, "card_caption", kind=t(lang, f"kind_{ride['kind']}"), km=stats["distance_km"],
                    moving=fmt_duration(stats["moving_s"]), speed=speed_line(stats, lang), app=APP_NAME)
        results.append(InlineQueryResultPhoto(
            id=f"last{ride['id']}", photo_url=url, thumbnail_url=url, photo_width=1080, photo_height=1350,
            title=t(lang, "inline_last"), description=caption.split("\n")[0], caption=caption,
            reply_markup=open_button))
    tz = db.user_tz(user_id) or 0
    today = local_date(time.time(), tz)
    monday = today - timedelta(days=today.weekday())
    week = db.totals_by_kind(user_id, datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc).timestamp()
                             + tz * 60)
    if week:
        lines = [t(lang, "inline_week_text", app=APP_NAME, n=sum(v["count"] for v in week.values()),
                   km=clubs.fmt_km(sum(v["distance_km"] for v in week.values())))]
        lines += [t(lang, "inline_kind_line", kind=t(lang, f"kind_{k}"), km=clubs.fmt_km(v["distance_km"]))
                  for k, v in week.items()]
        results.append(InlineQueryResultArticle(
            id="week", title=t(lang, "inline_week"), description=lines[0],
            input_message_content=InputTextMessageContent(message_text="\n".join(lines)), reply_markup=open_button))
    records = []
    for kind in ACTIVITIES:
        best = db.best_values(user_id, kind) or {}
        for key, value in best.items():
            if value:
                shown = f"{value} {t(lang, 'card_km')}" if key == "distance_km" else (
                    f"{value} {t(lang, 'card_kmh')}" if key == "max_speed_kmh" else fmt_duration(value))
                records.append(t(lang, "inline_record_line", kind=t(lang, f"kind_{kind}"), what=t(lang, f"rec_{key}"),
                                 value=shown))
    if records:
        results.append(InlineQueryResultArticle(
            id="records", title=t(lang, "inline_records"), description=records[0].lstrip("• "),
            input_message_content=InputTextMessageContent(
                message_text="\n".join([t(lang, "inline_records_text", app=APP_NAME), *records])),
            reply_markup=open_button))
    invite = f"{bot_link}?start=ref_{db.ref_token(user_id)}"
    results.append(InlineQueryResultArticle(
        id="invite", title=t(lang, "inline_invite", app=APP_NAME), description=invite,
        input_message_content=InputTextMessageContent(message_text=t(lang, "inline_invite_text", app=APP_NAME,
                                                                     link=invite))))
    await query.answer(results, cache_time=0, is_personal=True)


async def group_timers(bot, now):
    """Раз в минуту: этапы сборов, конец челленджей, дуэлей и битв клубов, напоминания."""
    for event, stage in db.events_due(now, EVENT_REMIND_BEFORE_S, EVENT_SUMMARY_AFTER_S):
        await advance_event(bot, event, stage, now)
    for challenge in db.challenges_due(now):
        await close_challenge(bot, challenge)
    for duel in db.duels_due(now, OPEN_CALL_TTL_S):
        await finish_duel(bot, duel)
    for war in db.wars_due(now, OPEN_CALL_TTL_S):
        await finish_war(bot, war)
    await nudge_groups(bot, now)


@dp.errors()
async def on_error(event: ErrorEvent, bot: Bot):
    logging.error("Ошибка при обработке update", exc_info=event.exception)
    await notify_admin(bot, f"⚠️ Ошибка в боте: {event.exception!r}")


# ---------- API для Mini App ----------

def fail(error, request, key):
    """Ошибка для Mini App на языке её интерфейса (заголовок X-Lang); X-Error — код для приложения."""
    return error(text=t(pick(request.headers.get("X-Lang")), key), headers={"X-Error": key})


def auth_user_id(request):
    """Telegram ID из подписанной initData — без проверки согласия."""
    user = validate_init_data(request.headers.get("X-Init-Data", ""), BOT_TOKEN)
    if user:
        user_id = int(user["id"])
        if user.get("language_code"):
            db.remember_tg_lang(user_id, pick(user["language_code"]))
    elif DEV_USER_ID:
        user_id = int(DEV_USER_ID)
    else:
        raise fail(web.HTTPUnauthorized, request, "err_open_in_tg")
    return user_id


def current_user_id(request):
    """Telegram ID того, кто принял актуальные условия; иначе 403 с X-Error: err_consent —
    Mini App тогда сама предлагает прочитать и принять."""
    user_id = auth_user_id(request)
    if not has_consent(user_id):
        raise fail(web.HTTPForbidden, request, "err_consent")
    db.touch(user_id)
    return user_id


async def api_consent(request):
    """Принять условия прямо в Mini App (без /start в чате)."""
    user_id = auth_user_id(request)
    newcomer = not db.consent_version(user_id)
    db.set_consent(user_id, POLICY_VERSION)
    await set_user_menu(request.app[BOT_KEY], user_id, db.user_lang(user_id))
    return web.json_response({"accepted": POLICY_VERSION, "newcomer": newcomer})


async def api_consent_info(request):
    """Что показать на экране согласия: новичку — о данных, остальным — что условия обновились."""
    user_id = auth_user_id(request)
    updated = bool(db.consent_version(user_id))
    return web.json_response({"updated": updated, "version": POLICY_VERSION,
                              "lang": db.user_lang(user_id) if updated else None})  # новичку — язык Telegram


def ride_to_json(ride):
    return {
        "id": ride["id"],
        "kind": ride["kind"],
        "started_at": ride["started_at"],
        "ended_at": ride["ended_at"],
        "active": ride["ended_at"] is None,
        "elevation_gain_m": ride["elevation_gain_m"],
        **stats_of(ride),
    }


async def api_rides(request):
    user_id = current_user_id(request)
    try:
        before = float(request.query["before"]) if "before" in request.query else None
    except ValueError:
        raise web.HTTPBadRequest(text="before должен быть числом")
    kind = request.query.get("kind")
    if kind is not None and kind not in ACTIVITIES:
        raise web.HTTPBadRequest(text=f"kind: одно из {', '.join(ACTIVITIES)}")
    rides = [ride_to_json(r) for r in db.list_rides(user_id, PAGE_SIZE, before, kind)]
    return web.json_response({
        "weight": db.get_weight(user_id),
        "kind": db.get_kind(user_id),
        "rides": rides,
        "has_more": len(rides) == PAGE_SIZE,
        "session_id": (session := db.current_session(user_id)) and session["id"],
        "lang": db.user_lang(user_id),
        "lang_choice": db.lang_choice(user_id),  # None — как в Telegram
        "admin": user_id in admin_ids(),
        "version": APP_VERSION,
    })


async def api_set_lang(request):
    """Язык из профиля Mini App: ru/uk/en или null — как в Telegram. Меняет и язык бота."""
    user_id = current_user_id(request)
    try:
        lang = (await request.json())["lang"]
    except (KeyError, TypeError, ValueError):
        lang = ""
    if lang is not None and lang not in LANGS:
        raise web.HTTPBadRequest(text=f"lang: одно из {', '.join(LANGS)} или null")
    db.set_lang(user_id, lang)
    await set_user_menu(request.app[BOT_KEY], user_id, db.user_lang(user_id))
    return web.json_response({"lang": db.user_lang(user_id), "lang_choice": lang})


def local_date(ts, tz):
    """Дата у пользователя; tz — смещение в минутах, как Date.getTimezoneOffset() (UTC − местное)."""
    return datetime.fromtimestamp(ts - tz * 60, timezone.utc).date()


def read_tz(request):
    try:
        tz = int(request.query.get("tz", "0"))
        if not -840 <= tz <= 840:
            raise ValueError
    except ValueError:
        raise web.HTTPBadRequest(text="tz — смещение в минутах, как getTimezoneOffset()")
    return tz


async def api_summary(request):
    """Итоги недели и месяца по типам, серии и личные рекорды."""
    user_id = current_user_id(request)
    tz = read_tz(request)
    db.set_user_tz(user_id, tz)  # для времени сборов и «ранней пташки»

    def start_of(day):  # полночь по времени пользователя, unix time
        return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() + tz * 60

    today = local_date(time.time(), tz)
    days, weeks = streaks((local_date(ts, tz) for ts in db.finished_starts(user_id)), today)
    return web.json_response({
        "week": db.totals_by_kind(user_id, start_of(today - timedelta(days=today.weekday()))),
        "month": db.totals_by_kind(user_id, start_of(today.replace(day=1))),
        "streak_days": days,
        "streak_weeks": weeks,
        "records": {kind: db.best_values(user_id, kind) for kind in ACTIVITIES},
    })


def own_ride(request):
    """Поездка из URL; чужая — как несуществующая, чтобы не выдавать чужие id."""
    user_id = current_user_id(request)
    ride = db.get_ride(int(request.match_info["ride_id"]))
    if not ride or ride["user_id"] != user_id:
        raise fail(web.HTTPNotFound, request, "ride_not_found")
    return ride


async def api_ride(request):
    ride = own_ride(request)
    stats = compute_stats(db.get_points(ride["id"]), db.get_weight(ride["user_id"]), ride["kind"])
    return web.json_response({
        "ride": {**ride_to_json(ride), "splits": stats["splits"], "records": db.new_records(ride)},
        "points": stats["track"],  # [[lat, lon]] без выбросов GPS
    })


_names = {}


async def user_name(bot, user_id, lang):
    """Имя для совместной тренировки и SOS: Telegram отдаёт его, раз человек запускал бота."""
    # shortcut: кэш на время работы процесса — новое имя видно после перезапуска.
    if user_id not in _names:
        try:
            _names[user_id] = (await bot.get_chat(user_id)).first_name
        except TelegramAPIError:
            return t(lang, "participant")
    return _names[user_id] or t(lang, "participant")


async def session_link(bot, session):
    """Приглашение: Mini App получит session_<token> в start_param."""
    return f"https://t.me/{(await bot.me()).username}?startapp=session_{session['token']}"


async def api_create_session(request):
    session = db.create_session(current_user_id(request))
    return web.json_response({"id": session["id"], "link": await session_link(request.app[BOT_KEY], session)})


async def api_join_session(request):
    session = db.join_session(request.match_info["token"], current_user_id(request))
    if not session:
        raise fail(web.HTTPNotFound, request, "err_session_gone")
    return web.json_response({"id": session["id"]})


async def api_leave_session(request):
    """Покинуть совместную тренировку; ушёл последний — она распускается."""
    user_id = current_user_id(request)
    session_id = int(request.match_info["session_id"])
    if not db.is_member(session_id, user_id):
        raise fail(web.HTTPNotFound, request, "err_session")
    db.leave_session(session_id, user_id)
    return web.json_response({"left": True})


async def api_session(request):
    """Участники совместной тренировки: имя, последняя тренировка в сессии, маршрут. Только для участников."""
    user_id = current_user_id(request)
    session_id = int(request.match_info["session_id"])
    if not db.is_member(session_id, user_id):
        raise fail(web.HTTPNotFound, request, "err_session")
    lang = db.user_lang(user_id)
    rides = {r["user_id"]: r for r in db.session_rides(session_id)}  # по started_at: остаётся последняя
    participants = []
    for member in db.session_members(session_id):
        ride = rides.get(member)
        participants.append({
            "you": member == user_id,
            "name": await user_name(request.app[BOT_KEY], member, lang),
            "ride": ride and ride_to_json(ride),
            "points": compute_stats(db.get_points(ride["id"]), 0, ride["kind"])["track"] if ride else [],
        })
    session = db.get_session(session_id)
    return web.json_response({
        "created_at": session["created_at"],
        "link": await session_link(request.app[BOT_KEY], session),  # участники могут звать других
        "participants": participants,
    })


async def api_sos(request):
    user_id = current_user_id(request)
    bot, lang = request.app[BOT_KEY], db.user_lang(user_id)
    return web.json_response({
        "contacts": [{"id": c, "name": await user_name(bot, c, lang)} for c in db.sos_contacts(user_id)],
        "invite": f"https://t.me/{(await bot.me()).username}?start=sos_{db.sos_token(user_id)}",
    })


async def api_referrals(request):
    """Своя реферальная ссылка и кого по ней пригласил — новые сверху."""
    user_id = current_user_id(request)
    bot, lang = request.app[BOT_KEY], db.user_lang(user_id)
    return web.json_response({
        "link": f"https://t.me/{(await bot.me()).username}?start=ref_{db.ref_token(user_id)}",
        "invited": [
            {"name": await user_name(bot, r["user_id"], lang), "at": r["created_at"]} for r in db.referrals(user_id)
        ],
    })


async def api_sos_remove(request):
    db.set_sos_contact(current_user_id(request), int(request.match_info["contact_id"]), False)
    return web.json_response({"removed": True})


async def api_sos_send(request):
    """Последняя точка идущей тренировки — всем экстренным контактам."""
    user_id = current_user_id(request)
    ride = db.active_ride(user_id)
    points = db.get_points(ride["id"]) if ride else []
    if not points:
        raise fail(web.HTTPConflict, request, "err_sos_no_location")
    contacts = db.sos_contacts(user_id)
    if not contacts:
        raise fail(web.HTTPBadRequest, request, "err_sos_no_contacts")
    ts, lat, lon = points[-1][:3]
    bot = request.app[BOT_KEY]
    minutes = int(max(0, time.time() - ts) // 60)
    sent = 0
    for contact in contacts:
        lang = db.contact_lang(user_id, contact)  # каждому контакту — на его языке
        text = t(lang, "sos_alert", name=await user_name(bot, user_id, lang), min=minutes)
        try:
            await bot.send_message(contact, text)
            await bot.send_location(contact, lat, lon)
            sent += 1
        except TelegramAPIError:  # контакт заблокировал бота — остальным всё равно отправляем
            logging.warning("SOS не доставлен контакту %s", contact)
    return web.json_response({"sent": sent, "total": len(contacts)})


async def api_delete_ride(request):
    db.delete_ride(own_ride(request)["id"])
    return web.json_response({"deleted": True})


async def read_kind(request):
    try:
        kind = (await request.json())["kind"]
    except (KeyError, TypeError, ValueError):
        kind = None
    if kind not in ACTIVITIES:
        raise web.HTTPBadRequest(text=f"kind: одно из {', '.join(ACTIVITIES)}")
    return kind


async def api_ride_kind(request):
    """Тип идущей тренировки; у завершённой не меняется — её цифры уже посчитаны для своего типа."""
    ride = own_ride(request)
    kind = await read_kind(request)
    if not db.set_ride_kind(ride["id"], kind):
        raise fail(web.HTTPConflict, request, "kind_locked")
    db.set_kind(ride["user_id"], kind)
    return web.json_response({"ride": ride_to_json(db.get_ride(ride["id"]))})


async def api_set_kind(request):
    user_id = current_user_id(request)
    db.set_kind(user_id, await read_kind(request))
    return web.json_response({"kind": db.get_kind(user_id)})


async def api_finish(request):
    ride = own_ride(request)
    await finish_and_report(request.app[BOT_KEY], ride["id"], manual=True)
    return web.json_response({"ride": ride_to_json(db.get_ride(ride["id"]))})


async def share_prepared(bot, ride, png):
    """Карточка уходит в чат с ботом, а Mini App получает id для shareMessage — отправить в любой чат."""
    message, caption, markup = await send_card(bot, ride, png)
    try:
        prepared = await bot.save_prepared_inline_message(
            user_id=ride["user_id"],
            result=InlineQueryResultCachedPhoto(
                id=f"ride{ride['id']}", photo_file_id=message.photo[-1].file_id,
                caption=caption, reply_markup=markup,
            ),
            allow_user_chats=True, allow_bot_chats=True, allow_group_chats=True, allow_channel_chats=True,
        )
    except TelegramAPIError as e:  # карточка всё равно в чате с ботом — её можно переслать
        logging.warning("savePreparedInlineMessage: %r", e)
        return None
    return prepared.id


CARD_ACTIONS = ("preview", "chat", "bot", "download")
CARD_FILE_TTL_S = 600
_card_files = {}  # token → (до какого времени, PNG): для tg.downloadFile


def card_options(request, form):
    try:
        opts = json.loads(form.get("options") or "{}")
        fmt, panel, action = opts.get("format", "post"), opts.get("panel", "map"), opts.get("action", "preview")
        fields = opts.get("fields", list(FIELDS))
        if fmt not in FORMATS or panel not in PANELS or action not in CARD_ACTIONS or not set(fields) <= set(FIELDS):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise web.HTTPBadRequest(text=f"options: format {'/'.join(FORMATS)}, panel {'/'.join(PANELS)}, "
                                      f"fields из {', '.join(FIELDS)}, action {'/'.join(CARD_ACTIONS)}")
    return fmt, panel, fields, action


async def api_card(request):
    """Настраиваемая карточка: превью (JPEG), в любой чат (пост), в чат с ботом или файлом в галерею.

    multipart: options — JSON {format, panel, fields, action}, photo — фон (необязательно).
    Фото обрабатывается в памяти и нигде не сохраняется; готовая картинка для скачивания живёт 10 минут.
    """
    ride = own_ride(request)
    form = await request.post()
    fmt, panel, fields, action = card_options(request, form)
    photo = form.get("photo")
    photo = photo.file.read() if photo is not None and hasattr(photo, "file") else None
    lang = db.user_lang(ride["user_id"])
    stats = compute_stats(db.get_points(ride["id"]), db.get_weight(ride["user_id"]), ride["kind"])
    records, elevation = record_labels(ride, lang), ride["elevation_gain_m"]
    date = f"{local_date(ride['started_at'], read_tz(request)):%d.%m.%Y}"
    map_h = map_height(fmt, ride["kind"], stats, panel, fields, records, elevation, bool(photo), lang)
    tiles = await fetch_tiles(map_view(stats["track"], map_h)[-1]) if map_h and panel == "map" else None
    try:
        image = await asyncio.to_thread(
            render, fmt, ride["kind"], stats, panel, fields, records, tiles, elevation, date, photo, lang,
            action == "preview",
        )
    except PhotoError:
        raise fail(web.HTTPBadRequest, request, "err_photo")
    if action == "preview":
        return web.Response(body=image, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    db.count(f"card_{fmt}_{action}")  # обезличенно: какие карточки и куда отправляют
    if photo:
        db.count("card_photo")
    bot = request.app[BOT_KEY]
    if action == "chat":
        return web.json_response({"prepared_id": await share_prepared(bot, ride, image)})
    if action == "bot":
        await bot.send_photo(ride["chat_id"], BufferedInputFile(image, filename="beout.png"),
                             caption=t(lang, "story_caption" if fmt == "story" else "card_saved_caption"))
        return web.json_response({"sent": True})
    now = time.time()
    for token in [k for k, (until, _) in _card_files.items() if until < now]:
        del _card_files[token]
    token = secrets.token_urlsafe(16)
    _card_files[token] = (now + CARD_FILE_TTL_S, image)
    return web.json_response({"url": f"{WEBAPP_URL}/card/{token}.png", "file_name": f"beout-{fmt}-{ride['id']}.png"})


async def card_file(request):
    """Готовая карточка для tg.downloadFile: ссылка случайная и живёт 10 минут."""
    until, image = _card_files.get(request.match_info["token"], (0, None))
    if until < time.time():
        raise web.HTTPNotFound()
    return web.Response(body=image, content_type="image/png", headers={
        "Content-Disposition": 'attachment; filename="beout.png"', "Cache-Control": "no-store"})


async def api_admin(request):
    """Статистика для вкладки «Админ». Не администратору — 404, будто адреса нет."""
    user_id = current_user_id(request)
    if user_id not in admin_ids():
        raise web.HTTPNotFound()
    bot, lang = request.app[BOT_KEY], db.user_lang(user_id)
    stats = db.admin_stats(time.time(), POLICY_VERSION, read_tz(request))
    leaders = []
    for inviter, n in db.referral_totals():
        invited = db.referrals(inviter)
        leaders.append({
            "name": await user_name(bot, inviter, lang), "count": n,
            "invited": [{"name": await user_name(bot, r["user_id"], lang), "at": r["created_at"]} for r in invited],
            "active": sum(1 for r in invited if db.active_since(r["user_id"], time.time() - 30 * 86400)),
        })
    for user in stats["recent"]:
        user["name"] = await user_name(bot, user.pop("user_id"), lang)
        inviter = user.pop("inviter_id")
        user["invited_by"] = inviter and await user_name(bot, inviter, lang)
    return web.json_response({**stats, "referrals": leaders, "policy_version": POLICY_VERSION})


async def api_set_weight(request):
    user_id = current_user_id(request)
    try:
        weight = float((await request.json())["weight"])
        if not 30 <= weight <= 250:
            raise ValueError
    except (KeyError, TypeError, ValueError):  # битый JSON — тоже ValueError
        raise fail(web.HTTPBadRequest, request, "err_weight")
    db.set_weight(user_id, weight)
    return web.json_response({"weight": weight})


async def index(_request):
    # Без no-cache Telegram держит старую Mini App после выкладки; так он сверяет ETag при каждом открытии.
    return web.FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def privacy(request):
    """Политика на языке ?lang=; без него — русская (ссылка из старых сообщений бота)."""
    lang = request.query.get("lang", "ru")
    name = f"privacy.{lang}.html" if lang in LANGS and lang != "ru" else "privacy.html"
    return web.FileResponse(WEBAPP_DIR / name)


BOT_KEY = web.AppKey("bot", Bot)


def make_web_app(bot):
    app = web.Application(client_max_size=12 * 1024 * 1024)  # фото для карточки
    app[BOT_KEY] = bot  # итог по кнопке «Финиш» из Mini App уходит в чат
    app.router.add_get("/", index)
    app.router.add_get("/privacy", privacy)
    app.router.add_static("/assets", ASSETS_DIR)  # шрифты и логотип для Mini App
    app.router.add_get("/api/rides", api_rides)
    app.router.add_get("/api/rides/{ride_id:\\d+}", api_ride)
    app.router.add_delete("/api/rides/{ride_id:\\d+}", api_delete_ride)
    app.router.add_get("/api/summary", api_summary)
    app.router.add_post("/api/sessions", api_create_session)
    app.router.add_post("/api/sessions/join/{token:[\\w-]+}", api_join_session)
    app.router.add_get("/api/sessions/{session_id:\\d+}", api_session)
    app.router.add_post("/api/sessions/{session_id:\\d+}/leave", api_leave_session)
    app.router.add_get("/api/sos", api_sos)
    app.router.add_get("/api/referrals", api_referrals)
    app.router.add_post("/api/sos/send", api_sos_send)
    app.router.add_delete("/api/sos/{contact_id:\\d+}", api_sos_remove)
    app.router.add_post("/api/rides/{ride_id:\\d+}/finish", api_finish)
    app.router.add_post("/api/rides/{ride_id:\\d+}/kind", api_ride_kind)
    app.router.add_post("/api/rides/{ride_id:\\d+}/card", api_card)
    app.router.add_get("/card/{token:[\\w-]+}.png", card_file)
    app.router.add_get("/inline/{token:[\\w-]+}.jpg", inline_card)
    app.router.add_post("/api/consent", api_consent)
    app.router.add_get("/api/consent", api_consent_info)
    app.router.add_get("/api/admin", api_admin)
    app.router.add_post("/api/weight", api_set_weight)
    app.router.add_post("/api/kind", api_set_kind)
    app.router.add_post("/api/lang", api_set_lang)
    return app


# ---------- фон: брошенные поездки и резервные копии ----------

async def housekeeping(bot):
    while True:
        try:
            for ride_id, last_ts in db.stale_rides():
                try:
                    await finish_and_report(bot, ride_id, last_ts)
                except Exception:  # пользователь заблокировал бота и т.п. — поездка всё равно закрыта
                    logging.exception("Не удалось отправить итог поездки %s", ride_id)
            db.backup(BACKUP_DIR)
            await post_weekly_ratings(bot, time.time())
            await group_timers(bot, time.time())
        except Exception as e:
            logging.exception("Фоновая задача упала")
            await notify_admin(bot, f"⚠️ Фоновая задача: {e!r}")
        await asyncio.sleep(60)


async def announce_policy(bot):
    """Условия поменялись: один раз пишем всем, кто принимал старые, — с кнопками «Прочитать» и «Принимаю».
    Без этого человек узнавал бы о новых условиях, только когда бот откажется записать тренировку."""
    for user_id in db.users_to_notify(POLICY_VERSION):
        lang = db.user_lang(user_id)
        for _ in range(3):
            try:
                await bot.send_message(user_id, t(lang, "policy_updated"), parse_mode="HTML",
                                       reply_markup=consent_keyboard(lang))
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramAPIError as e:  # заблокировал бота и т.п. — больше не пробуем
                logging.info("Уведомление об условиях не доставлено %s: %r", user_id, e)
                break
        db.mark_policy_notified(user_id, POLICY_VERSION)
        await asyncio.sleep(0.05)  # ~20 сообщений в секунду: ниже лимита Telegram


async def wait_out_flood(bot):
    """Ждёт, пока Telegram доступен и снял лимит запросов.

    Падать нельзя: каждый перезапуск шлёт пачку запросов и продлевает лимит (Flood control).
    """
    floods = 0
    while True:
        try:
            return await bot.me()  # результат кэшируется, start_polling его переиспользует
        except TelegramRetryAfter as e:
            # Повтор ровно через retry_after снова продлевает лимит — ждём всё дольше, до 10 минут.
            delay = min(e.retry_after * 2**floods, FLOOD_MAX_WAIT_S)
            floods += 1
            logging.warning("Flood control: Telegram просит %s с, ждём %s с", e.retry_after, delay)
            await asyncio.sleep(delay)
        except TelegramNetworkError as e:  # WARP иногда рвёт соединение — ждём, а не падаем
            logging.warning("Нет связи с Telegram, повтор через %s с: %r", NETWORK_RETRY_S, e)
            await asyncio.sleep(NETWORK_RETRY_S)


async def main():
    bot = Bot(BOT_TOKEN)
    await wait_out_flood(bot)
    try:
        # Кнопка «Мои тренировки» слева от поля ввода: по умолчанию английская,
        # на языке пользователя — с согласия или /start (set_user_menu).
        await bot.set_chat_menu_button(menu_button=menu_button(DEFAULT_LANG))
        # Команды Telegram сам показывает на языке приложения пользователя; прочие языки — английский.
        await bot.set_my_commands(commands(DEFAULT_LANG))
        for lang in LANGS:
            if lang != DEFAULT_LANG:
                await bot.set_my_commands(commands(lang), language_code=lang)
        # В группах — свои команды: рейтинг, челленджи, сборы, дуэли, битва клубов.
        await bot.set_my_commands(group_commands(DEFAULT_LANG), scope=BotCommandScopeAllGroupChats())
        for lang in LANGS:
            if lang != DEFAULT_LANG:
                await bot.set_my_commands(group_commands(lang), scope=BotCommandScopeAllGroupChats(), language_code=lang)
    except TelegramAPIError as e:
        # Меню хранится в Telegram с прошлого запуска. Падать тут нельзя: перезапуск снова
        # упрётся в лимит запросов (Flood control), и бот не поднимется вовсе.
        logging.warning("Меню бота не обновлено: %r", e)

    runner = web.AppRunner(make_web_app(bot))
    await runner.setup()
    await web.TCPSite(runner, HOST, PORT).start()
    logging.info("Mini App: http://%s:%s (публично: %s)", HOST, PORT, WEBAPP_URL)

    # Частые сообщения о запуске — признак того, что бот падает и перезапускается.
    await notify_admin(bot, f"✅ {APP_NAME} запущен: {WEBAPP_URL}")
    background = asyncio.create_task(housekeeping(bot))
    announce = asyncio.create_task(announce_policy(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        background.cancel()
        announce.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
