"""Велотрекер: Telegram-бот + сервер для Mini App в одном процессе.

Бот принимает трансляцию геопозиции (Live Location) и сохраняет точки маршрута.
Telegram присылает обновления, даже когда телефон заблокирован, поэтому
запись не обрывается. Mini App (webapp/index.html) показывает карту и статистику.
"""
import asyncio
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from aiohttp import web

from auth import validate_init_data
from ride_stats import compute_stats
from storage import Storage

from settings import TOKEN, URL

BOT_TOKEN = TOKEN
WEBAPP_URL = URL.rstrip("/")  # публичный HTTPS-адрес этого сервера
# cloudflared ходит на localhost; 0.0.0.0 открыл бы API всей локальной сети.
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH", "rides.db")
# Только для локальной отладки в обычном браузере, без Telegram. В проде не задавать!
DEV_USER_ID = os.environ.get("DEV_USER_ID")

WEBAPP_DIR = Path(__file__).parent / "webapp"

logging.basicConfig(level=logging.INFO)
db = Storage(DB_PATH)
dp = Dispatcher()


# ---------- вспомогательное ----------

def to_ts(value):
    if value is None:
        return time.time()
    if isinstance(value, datetime):
        return value.timestamp()
    return float(value)


def fmt_duration(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def open_app_keyboard(ride_id=None, text="🗺 Открыть трекер"):
    url = f"{WEBAPP_URL}/?ride={ride_id}" if ride_id else f"{WEBAPP_URL}/"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))]]
    )


def ride_summary(ride):
    stats = compute_stats(db.get_points(ride["id"]), db.get_weight(ride["user_id"]))
    return (
        "🏁 <b>Поездка завершена</b>\n\n"
        f"📏 Дистанция: <b>{stats['distance_km']} км</b>\n"
        f"⏱ Время: {fmt_duration(stats['duration_s'])} "
        f"(в движении {fmt_duration(stats['moving_s'])})\n"
        f"⚡ Средняя: {stats['avg_speed_kmh']} км/ч, макс: {stats['max_speed_kmh']} км/ч\n"
        f"🔥 Калории: ~<b>{stats['kcal']} ккал</b>"
    )


async def finish_and_report(message: Message, ride):
    db.finish_ride(ride["id"])
    ride = db.get_ride(ride["id"])
    await message.bot.send_message(
        ride["chat_id"],
        ride_summary(ride),
        parse_mode="HTML",
        reply_markup=open_app_keyboard(ride["id"], "🗺 Маршрут на карте"),
    )


# ---------- команды ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    weight = db.get_weight(message.from_user.id)
    await message.answer(
        "Привет! Я записываю велопоездки 🚴\n\n"
        "<b>Как начать поездку:</b>\n"
        "1. Нажми 📎 → «Геопозиция» → «Транслировать геопозицию» "
        "и выбери срок с запасом.\n"
        "2. Катайся — телефон можно убрать в карман.\n"
        "3. В конце останови трансляцию (или отправь /stop).\n\n"
        f"Вес для расчёта калорий: <b>{weight:g} кг</b>. Изменить: /weight 72",
        parse_mode="HTML",
        reply_markup=open_app_keyboard(),
    )


@dp.message(Command("weight"))
async def cmd_weight(message: Message, command: CommandObject):
    try:
        weight = float((command.args or "").replace(",", "."))
        if not 30 <= weight <= 250:
            raise ValueError
    except ValueError:
        await message.answer("Напиши вес в килограммах, например: /weight 72")
        return
    db.set_weight(message.from_user.id, weight)
    await message.answer(f"Готово, вес: {weight:g} кг ✅")


@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    ride = db.active_ride(message.from_user.id)
    if not ride:
        await message.answer("Сейчас нет активной поездки.")
        return
    await finish_and_report(message, ride)


# ---------- геопозиция ----------

@dp.message(F.location)
async def on_location(message: Message):
    loc = message.location
    if not loc.live_period:
        await message.answer(
            "Это обычная геопозиция, по ней маршрут не построить. "
            "Нужна именно «Трансляция геопозиции» 📡"
        )
        return

    ts = to_ts(message.date)
    ride_id = db.start_ride(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        ts=ts,
        live_until=ts + loc.live_period,
    )
    db.add_point(ride_id, ts, loc.latitude, loc.longitude, loc.horizontal_accuracy)
    await message.answer(
        "🚴 Поездка началась, записываю маршрут!\n"
        "Чтобы закончить, останови трансляцию или отправь /stop.",
        reply_markup=open_app_keyboard(ride_id, "📍 Смотреть в реальном времени"),
    )


@dp.edited_message(F.location)
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
        await finish_and_report(message, ride)


# ---------- API для Mini App ----------

def current_user_id(request):
    user = validate_init_data(request.headers.get("X-Init-Data", ""), BOT_TOKEN)
    if user:
        return int(user["id"])
    if DEV_USER_ID:
        return int(DEV_USER_ID)
    raise web.HTTPUnauthorized(text="Откройте приложение из Telegram")


def ride_to_json(ride, weight):
    stats = compute_stats(db.get_points(ride["id"]), weight)
    return {
        "id": ride["id"],
        "started_at": ride["started_at"],
        "ended_at": ride["ended_at"],
        "active": ride["ended_at"] is None,
        **stats,
    }


async def api_rides(request):
    user_id = current_user_id(request)
    db.close_expired(user_id)
    weight = db.get_weight(user_id)
    rides = [ride_to_json(r, weight) for r in db.list_rides(user_id)]
    totals = {
        "count": len(rides),
        "distance_km": round(sum(r["distance_km"] for r in rides), 1),
        "moving_s": sum(r["moving_s"] for r in rides),
        "kcal": sum(r["kcal"] for r in rides),
    }
    return web.json_response({"weight": weight, "rides": rides, "totals": totals})


async def api_ride(request):
    user_id = current_user_id(request)
    db.close_expired(user_id)
    ride = db.get_ride(int(request.match_info["ride_id"]))
    if not ride or ride["user_id"] != user_id:
        raise web.HTTPNotFound(text="Поездка не найдена")
    points = db.get_points(ride["id"])
    return web.json_response({
        "ride": ride_to_json(ride, db.get_weight(user_id)),
        "points": [[lat, lon, ts] for ts, lat, lon in points],
    })


async def api_set_weight(request):
    user_id = current_user_id(request)
    data = await request.json()
    try:
        weight = float(data["weight"])
        if not 30 <= weight <= 250:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="Вес должен быть от 30 до 250 кг")
    db.set_weight(user_id, weight)
    return web.json_response({"weight": weight})


async def index(_request):
    return web.FileResponse(WEBAPP_DIR / "index.html")


def make_web_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/rides", api_rides)
    app.router.add_get("/api/rides/{ride_id:\\d+}", api_ride)
    app.router.add_post("/api/weight", api_set_weight)
    return app


async def main():
    bot = Bot(BOT_TOKEN)
    # Кнопка «Мои поездки» слева от поля ввода в чате с ботом.
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="Мои поездки", web_app=WebAppInfo(url=f"{WEBAPP_URL}/"))
    )

    runner = web.AppRunner(make_web_app())
    await runner.setup()
    await web.TCPSite(runner, HOST, PORT).start()
    logging.info("Mini App: http://%s:%s (публично: %s)", HOST, PORT, WEBAPP_URL)

    try:
        await dp.start_polling(bot, allowed_updates=["message", "edited_message"])
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
