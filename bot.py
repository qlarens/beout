"""Трекер тренировок: Telegram-бот + сервер для Mini App в одном процессе.

Бот принимает трансляцию геопозиции (Live Location) и сохраняет точки маршрута.
Telegram присылает обновления, даже когда телефон заблокирован, поэтому
запись не обрывается. Mini App (webapp/index.html) показывает карту и статистику.
"""
import asyncio
import json
import logging
import math
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
from cards import render_card
from stats import compute_stats, elevation_gain, fmt_duration, fmt_pace, streaks
from settings import (
    ACTIVITIES,
    APP_NAME,
    ELEVATION_API,
    ELEVATION_BATCH,
    ELEVATION_MAX_POINTS,
    ELEVATION_MIN_STEP_M,
    ELEVATION_SMOOTH_POINTS,
    ELEVATION_TIMEOUT_S,
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
POLICY_VERSION = 2
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

COMMANDS = [
    BotCommand(command="start", description="Как начать тренировку"),
    BotCommand(command="stop", description="Завершить тренировку"),
    BotCommand(command="weight", description="Вес для калорий: /weight 72"),
    BotCommand(command="privacy", description="Условия и конфиденциальность"),
    BotCommand(command="export", description="Выгрузить мои данные"),
    BotCommand(command="delete", description="Удалить все мои данные"),
    BotCommand(command="top", description="Рейтинг группы за неделю"),
]

CONSENT_TEXT = (
    "Перед началом — коротко о данных.\n\n"
    "Я сохраняю геопозицию во время трансляции (координаты, время, точность), "
    "твой Telegram ID и вес, если укажешь. Это нужно только для маршрута и статистики. "
    "Данные не продаются и не используются для рекламы.\n\n"
    "Выгрузить свои данные: /export. Удалить всё: /delete.\n\n"
    "Нажимая «Принимаю», ты соглашаешься с условиями использования "
    "и политикой конфиденциальности."
)


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


def speed_line(ride, stats):
    """Велосипед — скорость, бег и ходьба — темп."""
    if ride["kind"] == "ride":
        return f"Средняя: {stats['avg_speed_kmh']} км/ч, макс: {stats['max_speed_kmh']} км/ч"
    return f"Темп: {fmt_pace(stats['avg_speed_kmh'])}, лучший: {fmt_pace(stats['max_speed_kmh'])}"


def start_keyboard(ride):
    """Под сообщением о старте: трекер, выбор типа (выбранный с галочкой), финиш."""
    kinds = [
        InlineKeyboardButton(
            text=("✓ " if kind == ride["kind"] else "") + a["title"],
            callback_data=f"kind:{ride['id']}:{kind}",
        )
        for kind, a in ACTIVITIES.items()
    ]
    return open_app_keyboard(
        ride["id"], "📍 Смотреть в реальном времени",
        kinds,
        [InlineKeyboardButton(text="🏁 Финиш", callback_data=f"finish:{ride['id']}")],
    )


def open_app_keyboard(ride_id=None, text="🗺 Открыть трекер", *extra_rows):
    url = f"{WEBAPP_URL}/?ride={ride_id}" if ride_id else f"{WEBAPP_URL}/"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))], *extra_rows]
    )


def policy_keyboard(*extra_rows):
    button = InlineKeyboardButton(text="📄 Условия и конфиденциальность", url=f"{WEBAPP_URL}/privacy")
    return InlineKeyboardMarkup(inline_keyboard=[[button], *extra_rows])


def consent_keyboard():
    return policy_keyboard([InlineKeyboardButton(text="✅ Принимаю", callback_data="consent")])


def welcome_text(user_id):
    return (
        "Привет! Я записываю тренировки: велосипед, бег и ходьбу.\n\n"
        "<b>Как начать тренировку:</b>\n"
        "1. Нажми 📎 → «Геопозиция» → «Транслировать геопозицию» "
        "и выбери срок с запасом.\n"
        "2. Двигайся — телефон можно убрать в карман. Следи за дорогой, а не за экраном.\n"
        "3. В конце нажми «Финиш» (или останови трансляцию, или отправь /stop).\n\n"
        f"Вес для расчёта калорий: <b>{db.get_weight(user_id):g} кг</b>. Изменить: /weight 72\n"
        "Мои данные: /export, удалить всё: /delete."
    )


def ride_summary(ride):
    stats = stats_of(ride)
    return (
        f"🏁 <b>{ACTIVITIES[ride['kind']]['title']}: тренировка завершена</b>\n\n"
        f"📏 Дистанция: <b>{stats['distance_km']} км</b>\n"
        f"⏱ Время: {fmt_duration(stats['duration_s'])} "
        f"(в движении {fmt_duration(stats['moving_s'])})\n"
        f"⚡ {speed_line(ride, stats)}\n"
        f"🔥 Калории: ≈ <b>{stats['kcal']} ккал</b>"
        + (f"\n⛰ Набор высоты: {ride['elevation_gain_m']} м" if ride["elevation_gain_m"] is not None else "")
        + "".join(f"\n🏆 {label}" for label in record_labels(ride))
    )


RECORD_TITLES = {
    "distance_km": "самая длинная тренировка",
    "max_speed_kmh": "максимальная скорость",
    "best_1k_s": "лучший 1 км",
    "best_5k_s": "лучшие 5 км",
}


def record_labels(ride):
    return [f"Новый рекорд: {RECORD_TITLES[key]}" for key in db.new_records(ride)]


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


async def finish_and_report(bot, ride_id, ts=None):
    """Закрывает тренировку и присылает итог — только если её не закрыли раньше."""
    if not db.finish_ride(ride_id, ts):
        return
    ride = db.get_ride(ride_id)
    track = compute_stats(db.get_points(ride_id), 0, ride["kind"])["track"]
    elevations = await fetch_elevations(track) if len(track) > 1 else None
    if elevations:  # сервис недоступен — просто без высоты
        db.set_elevation(ride_id, elevation_gain(elevations, ELEVATION_MIN_STEP_M, ELEVATION_SMOOTH_POINTS))
        ride = db.get_ride(ride_id)
    await bot.send_message(
        ride["chat_id"],
        ride_summary(ride),
        parse_mode="HTML",
        reply_markup=open_app_keyboard(
            ride["id"], "🗺 Маршрут на карте",
            [InlineKeyboardButton(text="📤 Карточка для друзей", callback_data=f"share:{ride['id']}")],
        ),
    )


async def send_card(bot, ride):
    """Шлёт карточку тренировки в чат с владельцем. Возвращает (сообщение, подпись, кнопки)."""
    stats = compute_stats(db.get_points(ride["id"]), db.get_weight(ride["user_id"]), ride["kind"])
    png = await asyncio.to_thread(render_card, ride["kind"], stats, record_labels(ride))
    caption = (
        f"{ACTIVITIES[ride['kind']]['title']}: {stats['distance_km']} км "
        f"за {fmt_duration(stats['moving_s'])} · {speed_line(ride, stats)}\n{APP_NAME}"
    )
    # web_app-кнопки работают только в личке с ботом, а карточку пересылают в любые чаты — нужна ссылка.
    # Без Main Mini App в @BotFather ссылка просто откроет чат с ботом.
    link = f"https://t.me/{(await bot.me()).username}?startapp"
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть трекер", url=link)]])
    message = await bot.send_photo(
        ride["chat_id"], BufferedInputFile(png, filename="beout.png"), caption=caption, reply_markup=markup
    )
    return message, caption, markup


_last_alert = 0.0


async def notify_admin(bot, text):
    """Сообщение администратору; не чаще раза в 5 минут, чтобы не завалить чат."""
    global _last_alert
    if not ADMIN_CHAT_ID or time.time() - _last_alert < 300:
        return
    _last_alert = time.time()
    try:
        await bot.send_message(int(ADMIN_CHAT_ID), text[:3500])
    except Exception:
        logging.exception("Не удалось написать администратору")


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
    if command.args and command.args.startswith("sos_"):
        await sos_invitation(message, command.args.removeprefix("sos_"))
        return
    if not has_consent(message.from_user.id):
        await message.answer(CONSENT_TEXT, reply_markup=consent_keyboard())
        return
    await message.answer(
        welcome_text(message.from_user.id), parse_mode="HTML", reply_markup=open_app_keyboard()
    )


# ---------- SOS: экстренные контакты ----------
# Бот пишет только тем, кто его запускал, поэтому контакт приходит сам — по ссылке t.me/<bot>?start=sos_<token>.

async def sos_invitation(message, token):
    owner = db.user_by_sos_token(token)
    if owner == message.from_user.id:
        await message.answer("Это твоя ссылка — отправь её близкому человеку.")
        return
    if not owner:
        await message.answer("Ссылка недействительна: попроси прислать новую.")
        return
    name = await user_name(message.bot, owner)
    await message.answer(
        f"{name} просит стать экстренным контактом в {APP_NAME}.\n\n"
        f"Если во время тренировки {name} нажмёт SOS, я пришлю тебе последнюю геопозицию. "
        "О тебе сохраняется только Telegram ID.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да, стать контактом", callback_data=f"sos:on:{token}")],
            [InlineKeyboardButton(text="Нет", callback_data="sos:no")],
        ]),
    )


@dp.callback_query(F.data.regexp(r"^sos:(on:[\w-]+|off:\d+|no)$"))
async def on_sos_contact(callback: CallbackQuery):
    contact, action = callback.from_user.id, callback.data.split(":")
    if action[1] == "no":
        await reply_in_place(callback, "Хорошо, ты не экстренный контакт.")
        return
    if action[1] == "off":  # контакт отказывается сам
        owner = int(action[2])
        db.set_sos_contact(owner, contact, False)
        await reply_in_place(callback, f"Ты больше не экстренный контакт для {escape(await user_name(callback.bot, owner))}.")
        return
    owner = db.user_by_sos_token(action[2])
    if not owner or owner == contact:
        await callback.answer("Ссылка недействительна: попроси прислать новую.", show_alert=True)
        return
    db.set_sos_contact(owner, contact, True)
    db.rotate_sos_token(owner)  # ссылка одноразовая: пересланная дальше уже не сработает
    await reply_in_place(
        callback,
        f"Готово: ты экстренный контакт для {escape(await user_name(callback.bot, owner))}.",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Отказаться", callback_data=f"sos:off:{owner}"),
        ]]),
    )
    try:
        await callback.bot.send_message(
            owner, f"{await user_name(callback.bot, contact)} теперь твой экстренный контакт. "
                   "Список и удаление — в профиле Mini App."
        )
    except TelegramAPIError:
        logging.warning("Не удалось сообщить %s о новом контакте", owner)


@dp.callback_query(F.data == "consent")
async def on_consent(callback: CallbackQuery):
    user_id = callback.from_user.id
    db.set_consent(user_id, POLICY_VERSION)
    await reply_in_place(callback, CONSENT_TEXT + "\n\n✅ <b>Принято</b>", policy_keyboard())
    await callback.bot.send_message(
        user_id, welcome_text(user_id), parse_mode="HTML", reply_markup=open_app_keyboard()
    )


@private.message(Command("privacy"))
async def cmd_privacy(message: Message):
    await message.answer(
        "Условия использования и политика конфиденциальности — по кнопке ниже.\n"
        "Выгрузить свои данные: /export. Удалить всё: /delete.",
        reply_markup=policy_keyboard(),
    )


@private.message(Command("export"))
async def cmd_export(message: Message):
    data = json.dumps(db.export_user(message.from_user.id), ensure_ascii=False, indent=1)
    await message.answer_document(
        BufferedInputFile(data.encode(), filename="beout-export.json"),
        caption="Все данные о тебе, которые хранит бот (время — unix timestamp).",
    )


@private.message(Command("delete"))
async def cmd_delete(message: Message):
    await message.answer(
        "Удалить все твои данные: тренировки, маршруты, вес, участие в сессиях и рейтингах, согласие с условиями? "
        "Отменить это нельзя. Если нужна копия — сначала /export.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить всё", callback_data="delete:yes")],
            [InlineKeyboardButton(text="Отмена", callback_data="delete:no")],
        ]),
    )


@dp.callback_query(F.data.in_({"delete:yes", "delete:no"}))
async def on_delete(callback: CallbackQuery):
    if callback.data == "delete:no":
        await reply_in_place(callback, "Отменено, данные на месте.")
        return
    db.delete_user(callback.from_user.id)
    await reply_in_place(
        callback,
        "🗑 Все твои данные удалены. Из резервных копий они исчезнут в течение 7 дней.\n"
        "Если трансляция геопозиции ещё идёт — останови её в Telegram. "
        "Сообщения в этом чате удаляются средствами Telegram.\n"
        "Чтобы снова пользоваться ботом — /start.",
    )


# ---------- команды ----------

@private.message(Command("weight"))
async def cmd_weight(message: Message, command: CommandObject):
    if not has_consent(message.from_user.id):
        await message.answer(CONSENT_TEXT, reply_markup=consent_keyboard())
        return
    try:
        weight = float((command.args or "").replace(",", "."))
        if not 30 <= weight <= 250:
            raise ValueError
    except ValueError:
        await message.answer("Напиши вес в килограммах, например: /weight 72")
        return
    db.set_weight(message.from_user.id, weight)
    await message.answer(f"Готово, вес: {weight:g} кг ✅")


@private.message(Command("stop"))
async def cmd_stop(message: Message):
    ride = db.active_ride(message.from_user.id)
    if not ride:
        await message.answer("Сейчас нет активной тренировки.")
        return
    await finish_and_report(message.bot, ride["id"])


@dp.callback_query(F.data.regexp(r"^kind:\d+:(ride|run|walk)$"))
async def on_kind(callback: CallbackQuery):
    _, ride_id, kind = callback.data.split(":")
    ride = db.get_ride(int(ride_id))
    if not ride or ride["user_id"] != callback.from_user.id:
        await callback.answer("Тренировка не найдена")
        return
    db.set_ride_kind(ride["id"], kind)
    db.set_kind(ride["user_id"], kind)  # следующая тренировка начнётся с этого типа
    if ride["ended_at"] is None and isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=start_keyboard(db.get_ride(ride["id"])))
    await callback.answer(ACTIVITIES[kind]["title"])


@dp.callback_query(F.data.regexp(r"^share:\d+$"))
async def on_share(callback: CallbackQuery):
    ride = db.get_ride(int(callback.data.split(":")[1]))
    if not ride or ride["user_id"] != callback.from_user.id:
        await callback.answer("Тренировка не найдена")
        return
    await callback.answer()
    await send_card(callback.bot, ride)


@dp.callback_query(F.data.regexp(r"^finish:\d+$"))
async def on_finish(callback: CallbackQuery):
    ride = db.get_ride(int(callback.data.split(":")[1]))
    if not ride or ride["user_id"] != callback.from_user.id:
        await callback.answer("Поездка не найдена")
        return
    await finish_and_report(callback.bot, ride["id"])
    # Финиш не останавливает трансляцию в Telegram — новые точки просто не записываются.
    await callback.answer("Готово. Трансляцию геопозиции можно остановить.")


# ---------- геопозиция ----------

@private.message(F.location)
async def on_location(message: Message):
    loc = message.location
    if not loc.live_period:
        await message.answer(
            "Это обычная геопозиция, по ней маршрут не построить. "
            "Нужна именно «Трансляция геопозиции» 📡"
        )
        return
    if not has_consent(message.from_user.id):
        await message.answer(
            CONSENT_TEXT + "\n\nПока условия не приняты, я ничего не записываю. "
            "После этого начни трансляцию заново.",
            reply_markup=consent_keyboard(),
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
    await message.answer(
        "Тренировка началась, записываю маршрут! Тип можно поменять кнопками ниже.\n"
        "Чтобы закончить, нажми «Финиш», останови трансляцию или отправь /stop.",
        reply_markup=start_keyboard(db.get_ride(ride_id)),
    )


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


async def rating_text(bot, chat_id, since, title):
    """Рейтинг по дистанции за неделю с since, отдельно по типам. Простой текст: имена не экранировать."""
    lines = [f"{title} {datetime.fromtimestamp(since, timezone.utc):%d.%m}–"
             f"{datetime.fromtimestamp(since + 6 * 86400, timezone.utc):%d.%m}"]
    rating = db.group_rating(chat_id, since, since + 7 * 86400)
    for kind in ACTIVITIES:
        places = []
        for user_id, km in rating.get(kind, []):
            name = await member_name(bot, chat_id, user_id)
            if name:
                places.append(f"{len(places) + 1}. {name} — {km} км")
            if len(places) == RATING_TOP_N:
                break
        if places:
            lines += ["", ACTIVITIES[kind]["title"], *places]
    if len(lines) == 1:
        lines += ["", "Пока никто из участников не тренировался."]
    lines += ["", "В рейтинге только те, кто нажал «Участвовать». Тренировки записывает бот в личном чате."]
    return "\n".join(lines)


def rating_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Участвовать", callback_data="rating:join"),
        InlineKeyboardButton(text="Выйти", callback_data="rating:leave"),
    ]])


@dp.message(Command("top"))
async def cmd_top(message: Message):
    if message.chat.type not in GROUP_CHATS:
        await message.answer(
            "Рейтинг работает в группах: добавь меня в групповой чат (например, чат клуба) и отправь там /top."
        )
        return
    db.add_group(message.chat.id)
    text = await rating_text(message.bot, message.chat.id, week_start(time.time()), "🏆 Рейтинг недели")
    await message.answer(text, reply_markup=rating_keyboard())


@dp.callback_query(F.data.in_({"rating:join", "rating:leave"}))
async def on_rating(callback: CallbackQuery):
    user_id, join = callback.from_user.id, callback.data == "rating:join"
    if join and not has_consent(user_id):
        await callback.answer(
            f"Сначала открой @{(await callback.bot.me()).username}, нажми «Старт» и прими условия.",
            show_alert=True,
        )
        return
    db.set_group_member(callback.message.chat.id, user_id, join)
    await callback.answer(
        "Ты в рейтинге этой группы." if join else "Ты больше не в рейтинге этой группы.", show_alert=True
    )


async def post_weekly_ratings(bot, now):
    """По понедельникам — итоги прошлой недели во все группы, где есть участники."""
    dt = datetime.fromtimestamp(now, timezone.utc)
    if dt.weekday() != 0 or dt.hour < RATING_POST_HOUR_UTC:
        return
    week = f"{dt:%G-W%V}"
    for chat_id in db.groups_to_post(week):
        db.mark_posted(chat_id, week)  # до отправки: сбой не повторяется каждую минуту
        try:
            text = await rating_text(bot, chat_id, week_start(now) - 7 * 86400, "🏆 Итоги недели")
            await bot.send_message(chat_id, text, reply_markup=rating_keyboard())
        except TelegramForbiddenError:
            db.forget_group(chat_id)  # бота удалили из группы
        except TelegramAPIError:
            logging.exception("Не удалось отправить рейтинг в %s", chat_id)


@dp.errors()
async def on_error(event: ErrorEvent, bot: Bot):
    logging.error("Ошибка при обработке update", exc_info=event.exception)
    await notify_admin(bot, f"⚠️ Ошибка в боте: {event.exception!r}")


# ---------- API для Mini App ----------

def current_user_id(request):
    user = validate_init_data(request.headers.get("X-Init-Data", ""), BOT_TOKEN)
    if user:
        user_id = int(user["id"])
    elif DEV_USER_ID:
        user_id = int(DEV_USER_ID)
    else:
        raise web.HTTPUnauthorized(text="Откройте приложение из Telegram")
    if not has_consent(user_id):
        raise web.HTTPForbidden(text="Сначала прими условия в чате с ботом: /start")
    return user_id


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
    })


def local_date(ts, tz):
    """Дата у пользователя; tz — смещение в минутах, как Date.getTimezoneOffset() (UTC − местное)."""
    return datetime.fromtimestamp(ts - tz * 60, timezone.utc).date()


async def api_summary(request):
    """Итоги недели и месяца по типам, серии и личные рекорды."""
    user_id = current_user_id(request)
    try:
        tz = int(request.query.get("tz", "0"))
        if not -840 <= tz <= 840:
            raise ValueError
    except ValueError:
        raise web.HTTPBadRequest(text="tz — смещение в минутах, как getTimezoneOffset()")

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
        raise web.HTTPNotFound(text="Поездка не найдена")
    return ride


async def api_ride(request):
    ride = own_ride(request)
    stats = compute_stats(db.get_points(ride["id"]), db.get_weight(ride["user_id"]), ride["kind"])
    return web.json_response({
        "ride": {**ride_to_json(ride), "splits": stats["splits"], "records": db.new_records(ride)},
        "points": stats["track"],  # [[lat, lon]] без выбросов GPS
    })


_names = {}


async def user_name(bot, user_id):
    """Имя для совместной тренировки: Telegram отдаёт его, раз человек запускал бота."""
    # shortcut: кэш на время работы процесса — новое имя видно после перезапуска.
    if user_id not in _names:
        try:
            _names[user_id] = (await bot.get_chat(user_id)).first_name or "Участник"
        except TelegramAPIError:
            return "Участник"
    return _names[user_id]


async def session_link(bot, session):
    """Приглашение: Mini App получит session_<token> в start_param."""
    return f"https://t.me/{(await bot.me()).username}?startapp=session_{session['token']}"


async def api_create_session(request):
    session = db.create_session(current_user_id(request))
    return web.json_response({"id": session["id"], "link": await session_link(request.app[BOT_KEY], session)})


async def api_join_session(request):
    session = db.join_session(request.match_info["token"], current_user_id(request))
    if not session:
        raise web.HTTPNotFound(text="Совместная тренировка не найдена или уже закончилась")
    return web.json_response({"id": session["id"]})


async def api_session(request):
    """Участники совместной тренировки: имя, последняя тренировка в сессии, маршрут. Только для участников."""
    user_id = current_user_id(request)
    session_id = int(request.match_info["session_id"])
    if not db.is_member(session_id, user_id):
        raise web.HTTPNotFound(text="Совместная тренировка не найдена")
    rides = {r["user_id"]: r for r in db.session_rides(session_id)}  # по started_at: остаётся последняя
    participants = []
    for member in db.session_members(session_id):
        ride = rides.get(member)
        participants.append({
            "you": member == user_id,
            "name": await user_name(request.app[BOT_KEY], member),
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
    bot = request.app[BOT_KEY]
    return web.json_response({
        "contacts": [{"id": c, "name": await user_name(bot, c)} for c in db.sos_contacts(user_id)],
        "invite": f"https://t.me/{(await bot.me()).username}?start=sos_{db.sos_token(user_id)}",
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
        raise web.HTTPConflict(text="Нет геопозиции: SOS работает во время тренировки с трансляцией")
    contacts = db.sos_contacts(user_id)
    if not contacts:
        raise web.HTTPBadRequest(text="Сначала добавь экстренный контакт в профиле")
    ts, lat, lon = points[-1]
    bot = request.app[BOT_KEY]
    text = (
        f"🆘 SOS от {await user_name(bot, user_id)} во время тренировки.\n"
        f"Последняя геопозиция — ниже, получена {int(max(0, time.time() - ts) // 60)} мин назад.\n"
        "Свяжись как можно скорее. Если ответа нет — звони 112."
    )
    sent = 0
    for contact in contacts:
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
    await finish_and_report(request.app[BOT_KEY], ride["id"])
    return web.json_response({"ride": ride_to_json(db.get_ride(ride["id"]))})


async def api_share(request):
    """Карточка уходит в чат с ботом, а Mini App получает id для shareMessage — отправить в любой чат."""
    ride = own_ride(request)
    bot = request.app[BOT_KEY]
    message, caption, markup = await send_card(bot, ride)
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
        return web.json_response({"prepared_id": None})
    return web.json_response({"prepared_id": prepared.id})


async def api_set_weight(request):
    user_id = current_user_id(request)
    try:
        weight = float((await request.json())["weight"])
        if not 30 <= weight <= 250:
            raise ValueError
    except (KeyError, TypeError, ValueError):  # битый JSON — тоже ValueError
        raise web.HTTPBadRequest(text="Вес должен быть от 30 до 250 кг")
    db.set_weight(user_id, weight)
    return web.json_response({"weight": weight})


async def index(_request):
    return web.FileResponse(WEBAPP_DIR / "index.html")


async def privacy(_request):
    return web.FileResponse(WEBAPP_DIR / "privacy.html")


BOT_KEY = web.AppKey("bot", Bot)


def make_web_app(bot):
    app = web.Application()
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
    app.router.add_get("/api/sos", api_sos)
    app.router.add_post("/api/sos/send", api_sos_send)
    app.router.add_delete("/api/sos/{contact_id:\\d+}", api_sos_remove)
    app.router.add_post("/api/rides/{ride_id:\\d+}/finish", api_finish)
    app.router.add_post("/api/rides/{ride_id:\\d+}/kind", api_ride_kind)
    app.router.add_post("/api/rides/{ride_id:\\d+}/share", api_share)
    app.router.add_post("/api/weight", api_set_weight)
    app.router.add_post("/api/kind", api_set_kind)
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
        # Кнопка «Мои тренировки» слева от поля ввода в чате с ботом.
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Мои тренировки", web_app=WebAppInfo(url=f"{WEBAPP_URL}/"))
        )
        await bot.set_my_commands(COMMANDS)
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
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        background.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
