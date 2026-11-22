# Be. Ride!

[![CI](https://github.com/qlarens/beride/actions/workflows/ci.yml/badge.svg)](https://github.com/qlarens/beride/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?logo=telegram&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Telegram Mini App](https://img.shields.io/badge/Telegram-Mini%20App-26A5E4?logo=telegram&logoColor=white)
![Status](https://img.shields.io/badge/status-prototype-orange)

Велотрекер в Telegram: бот записывает поездку через «Трансляцию геопозиции», Mini App показывает маршрут, время, скорость и калории.

## Запуск

```bash
pip install -r requirements.txt
cloudflared tunnel --url http://localhost:8080   # Mini App нужен HTTPS
export BOT_TOKEN="123456:ABC..."                 # от @BotFather
export WEBAPP_URL="https://xxxx.trycloudflare.com"
python bot.py
```

Дальше: `/start` в боте → «Открыть трекер». Тестовая поездка: `python seed_demo.py <user_id>`.

Поездка: 📎 → «Геопозиция» → «Транслировать геопозицию». Конец — остановка трансляции или `/stop`.

## Файлы

- `bot.py` — бот (aiogram 3) + веб-сервер и API для Mini App
- `storage.py` — SQLite
- `ride_stats.py` — дистанция, скорость, калории
- `auth.py` — проверка `initData`
- `webapp/index.html` — Mini App

> `DEV_USER_ID` отключает проверку подписи — только для локальной отладки, не на публичном сервере.
