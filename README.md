# Be. Out!

[![CI](https://github.com/qlarens/beout/actions/workflows/ci.yml/badge.svg)](https://github.com/qlarens/beout/actions/workflows/ci.yml)
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

Дальше: `/start` в боте → принять условия → «Открыть трекер». Тестовая поездка: `python seed_demo.py <user_id>`.

Поездка: 📎 → «Геопозиция» → «Транслировать геопозицию». Конец — остановка трансляции, `/stop` или 2 часа без новых точек.

Команды: `/start`, `/stop`, `/weight 72`, `/privacy`, `/export` (все данные в JSON), `/delete` (удалить всё).

## Настройки (env)

| Переменная | Зачем |
|---|---|
| `BOT_TOKEN`, `WEBAPP_URL` | обязательные |
| `DB_PATH` | база, по умолчанию `beout.db` |
| `BACKUP_DIR` | ежедневные копии базы (хранятся 7 дней), по умолчанию `backups/` рядом с базой |
| `ADMIN_CHAT_ID` | твой Telegram ID: сюда бот пишет о запуске и ошибках |
| `DEV_USER_ID` | отключает проверку подписи — только для локальной отладки, не на публичном сервере |

## Данные пользователей

Бот ничего не записывает, пока пользователь не примет условия ([webapp/privacy.html](webapp/privacy.html)).
Изменил условия — увеличь `POLICY_VERSION` в `bot.py`, и бот попросит принять их заново.

## Файлы

- `bot.py` — бот (aiogram 3) + веб-сервер и API для Mini App
- `storage.py` — SQLite, миграции, бэкапы
- `stats.py` — дистанция, скорость, калории
- `auth.py` — проверка `initData`
- `webapp/` — Mini App и страница условий
- `tests.py` — тесты: `python -m unittest`
