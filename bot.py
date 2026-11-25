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
from cards import FIELDS, FORMATS, PANELS, PhotoError, map_height, map_view, render, render_card
from i18n import DEFAULT_LANG, LANGS, pick, t
from stats import compute_stats, elevation_gain, fmt_duration, streaks
from settings import (
    ACTIVITIES,
    APP_NAME,
    APP_VERSION,
    ELEVATION_API,
    ELEVATION_BATCH,
    ELEVATION_MAX_POINTS,
    ELEVATION_MIN_STEP_M,
    ELEVATION_SMOOTH_POINTS,
    ELEVATION_TIMEOUT_S,
    MAP_CACHE_TILES,
    MAP_TILES,
    MAP_TIMEOUT_S,
    MAP_USER_AGENT,
    RATING_POST_HOUR_UTC,
    RATING_TOP_N,
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
POLICY_VERSION = 6
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
    db.set_ride_kind(ride["id"], kind)
    db.set_kind(ride["user_id"], kind)  # следующая тренировка начнётся с этого типа
    if ride["ended_at"] is None and isinstance(callback.message, Message):
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


# ---------- рейтинг в группах ----------

def week_start(now):
    """Понедельник 00:00 UTC текущей недели, unix time."""
    # shortcut: неделя по UTC — в Европе граница сдвинута на 1–3 часа; хранить пояс группы, если будут жалобы.
    day = datetime.fromtimestamp(now, timezone.utc).date()
    monday = day - timedelta(days=day.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc).timestamp()


async def member_name(bot, chat_id, user_id):
    """Имя участника группы; None — вышел из группы."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return None
    return None if member.status in ("left", "kicked") else member.user.full_name


async def rating_text(bot, chat_id, since, title_key, lang):
    """Рейтинг по дистанции за неделю с since, отдельно по типам. Простой текст: имена не экранировать."""
    lines = [f"{t(lang, title_key)} {datetime.fromtimestamp(since, timezone.utc):%d.%m}–"
             f"{datetime.fromtimestamp(since + 6 * 86400, timezone.utc):%d.%m}"]
    rating = db.group_rating(chat_id, since, since + 7 * 86400)
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
    lines += ["", t(lang, "rating_footer")]
    return "\n".join(lines)


def rating_keyboard(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(lang, "btn_join"), callback_data="rating:join"),
        InlineKeyboardButton(text=t(lang, "btn_leave"), callback_data="rating:leave"),
    ]])


@dp.message(Command("top"))
async def cmd_top(message: Message):
    lang = lang_of(message.from_user)
    if message.chat.type not in GROUP_CHATS:
        await message.answer(t(lang, "top_private"))
        return
    db.add_group(message.chat.id, lang)  # на этом языке пойдёт и автопост по понедельникам
    text = await rating_text(message.bot, message.chat.id, week_start(time.time()), "rating_week", lang)
    await message.answer(text, reply_markup=rating_keyboard(lang))


@dp.callback_query(F.data.in_({"rating:join", "rating:leave"}))
async def on_rating(callback: CallbackQuery):
    user_id, join = callback.from_user.id, callback.data == "rating:join"
    lang = lang_of(callback.from_user)
    if join and not has_consent(user_id):
        await callback.answer(
            t(lang, "rating_consent_first", bot=(await callback.bot.me()).username), show_alert=True,
        )
        return
    db.set_group_member(callback.message.chat.id, user_id, join)
    await callback.answer(t(lang, "rating_joined" if join else "rating_left"), show_alert=True)


async def post_weekly_ratings(bot, now):
    """По понедельникам — итоги прошлой недели во все группы, где есть участники."""
    dt = datetime.fromtimestamp(now, timezone.utc)
    if dt.weekday() != 0 or dt.hour < RATING_POST_HOUR_UTC:
        return
    week = f"{dt:%G-W%V}"
    for chat_id in db.groups_to_post(week):
        db.mark_posted(chat_id, week)  # до отправки: сбой не повторяется каждую минуту
        try:
            lang = db.group_lang(chat_id)
            text = await rating_text(bot, chat_id, week_start(now) - 7 * 86400, "rating_results", lang)
            await bot.send_message(chat_id, text, reply_markup=rating_keyboard(lang))
        except TelegramForbiddenError:
            db.forget_group(chat_id)  # бота удалили из группы
        except TelegramAPIError:
            logging.exception("Не удалось отправить рейтинг в %s", chat_id)


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
    ts, lat, lon = points[-1]
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
    ride = own_ride(request)
    kind = await read_kind(request)
    db.set_ride_kind(ride["id"], kind)
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
