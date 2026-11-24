# Be. Out!

[![CI](https://github.com/qlarens/beout/actions/workflows/ci.yml/badge.svg)](https://github.com/qlarens/beout/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?logo=telegram&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Telegram Mini App](https://img.shields.io/badge/Telegram-Mini%20App-26A5E4?logo=telegram&logoColor=white)
![Status](https://img.shields.io/badge/status-prototype-orange)

Трекер тренировок в Telegram — велосипед, бег, ходьба: бот записывает тренировку через «Трансляцию геопозиции», Mini App показывает маршрут, время, скорость или темп, сплиты и калории.

## Запуск

```bash
pip install -r requirements.txt
cloudflared tunnel --url http://localhost:8080   # Mini App нужен HTTPS
export BOT_TOKEN="123456:ABC..."                 # от @BotFather
export WEBAPP_URL="https://xxxx.trycloudflare.com"
python bot.py
```

Дальше: `/start` в боте → принять условия → «Открыть трекер». Тестовая поездка: `python seed_demo.py <user_id>`.

Один раз в @BotFather: **Bot Settings → Configure Mini App → Main Mini App** с адресом `WEBAPP_URL`. Тогда кнопка «Открыть трекер» на карточке тренировки (`t.me/<bot>?startapp`) открывает Mini App из любого чата, иначе — просто чат с ботом.

Тренировка: 📎 → «Геопозиция» → «Транслировать геопозицию». Тип (вело, бег, ходьба) — кнопками под сообщением о старте или в Mini App. Конец — кнопка «Финиш» (в чате или в Mini App), остановка трансляции, `/stop` или 2 часа без новых точек.

Совместная тренировка: Mini App → «Совместная тренировка» → ссылка другу; тренировки участников, начатые в течение 12 часов, видны на одной карте.

Рейтинг клуба: добавить бота в группу → `/top` → «Участвовать». По понедельникам бот сам постит итоги прошлой недели.

Команды (в личке): `/start`, `/stop`, `/weight 72`, `/privacy`, `/export` (все данные в JSON), `/delete` (удалить всё). В группе: `/top`.

## Настройки (env)

| Переменная | Зачем |
|---|---|
| `BOT_TOKEN`, `WEBAPP_URL` | обязательные |
| `DB_PATH` | база, по умолчанию `beout.db` |
| `BACKUP_DIR` | ежедневные копии базы (хранятся 7 дней), по умолчанию `backups/` рядом с базой |
| `ADMIN_CHAT_ID` | твой Telegram ID: сюда бот пишет о запуске и ошибках |
| `DEV_USER_ID` | отключает проверку подписи — только для локальной отладки, не на публичном сервере |

## Прод (VDS)

Бот работает на VDS под systemd: `beout` (бот, `127.0.0.1:8090`) и `beout-tunnel` (Cloudflare-туннель на `https://beout.lilaxy.com`).

- Код: `/srv/beout/releases/<sha>`, активный релиз — ссылка `/srv/beout/current`, venv — `/srv/beout/venv`.
- База и бэкапы: `/srv/beout/data` (только пользователь `beout`).
- Секреты: `/etc/beout/beout.env` (`BOT_TOKEN`, `WEBAPP_URL`, `HOST`, `PORT=8090`, `DB_PATH`, `ADMIN_CHAT_ID`), `/etc/beout/tunnel.env` (`TUNNEL_ID`), `/etc/beout/tunnel.json`.
- Логи: `journalctl -u beout -u beout-tunnel`.

**CD:** каждый push в `master` после зелёных тестов выкладывается сам ([ci.yml](.github/workflows/ci.yml)): `git archive | ssh beout-deploy@VDS <sha>`. Ключ `beout-deploy` умеет только запускать [deploy/beout-deploy](deploy/beout-deploy), который переключает релиз и откатывает его, если бот не поднялся. Секреты репозитория: `DEPLOY_HOST`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`.

Новый сервер: `sudo bash deploy/setup-vds.sh "<публичный ключ деплоя>"`, затем положить секреты и базу. `deploy/setup-vds.sh` не обновляется через CD: поменял юниты или `beout-deploy` — запусти его снова.

## Данные пользователей

Бот ничего не записывает, пока пользователь не примет условия ([webapp/privacy.html](webapp/privacy.html)).
Изменил условия — увеличь `POLICY_VERSION` в `bot.py`, и бот попросит принять их заново.

## Файлы

- `bot.py` — бот (aiogram 3) + веб-сервер и API для Mini App
- `storage.py` — SQLite, миграции, бэкапы
- `stats.py` — дистанция, скорость, темп, сплиты, калории, фильтры GPS, автопауза
- `cards.py` — карточка тренировки для чатов (Pillow)
- `assets/` — логотип и шрифт Inter (OFL) для карточки и Mini App
- `settings.py` — название приложения и все пороги (GPS, автопауза, таймауты)
- `auth.py` — проверка `initData`
- `webapp/` — Mini App и страница условий
- `tests.py` — тесты: `python -m unittest`
