"""Тесты: python -m unittest -v"""
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

TOKEN = "123:test"
os.environ.update(BOT_TOKEN=TOKEN, WEBAPP_URL="https://example.test", DB_PATH=":memory:")
os.environ.pop("DEV_USER_ID", None)

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import bot  # noqa: E402
from auth import validate_init_data  # noqa: E402
from stats import compute_stats  # noqa: E402
from storage import SCHEMA, STALE_AFTER_S, Storage  # noqa: E402


def init_data(user_id, token=TOKEN, auth_date=None):
    """initData, подписанная так же, как это делает Telegram."""
    pairs = {"auth_date": str(int(auth_date or time.time())), "user": json.dumps({"id": user_id})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def line(n, t0=0.0):
    """n точек на север, ~55 м за 10 с (~20 км/ч)."""
    return [(t0 + i * 10, 52.0 + i * 0.0005, 21.0) for i in range(n)]


class AuthTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_init_data(init_data(5), TOKEN)["id"], 5)

    def test_wrong_token(self):
        self.assertIsNone(validate_init_data(init_data(5, token="1:other"), TOKEN))

    def test_tampered_user(self):
        forged = init_data(5).replace(
            urlencode({"user": json.dumps({"id": 5})}), urlencode({"user": json.dumps({"id": 6})})
        )
        self.assertIsNone(validate_init_data(forged, TOKEN))

    def test_expired(self):
        self.assertIsNone(validate_init_data(init_data(5, auth_date=time.time() - 2 * 86400), TOKEN))


class StatsTest(unittest.TestCase):
    def test_gps_jump_ignored(self):
        base = line(10)
        jumpy = base[:5] + [(45, 53.0, 21.0)] + base[5:]  # скачок на 100+ км
        self.assertEqual(compute_stats(jumpy, 75), compute_stats(base, 75))
        self.assertAlmostEqual(compute_stats(base, 75)["distance_km"], 0.5, places=1)

    def test_noisy_track(self):
        """Едем 300 с, стоим 300 с с дрожанием ±2 м и скачком GPS, едем ещё 300 с."""
        go1 = line(31)
        t0, lat0 = go1[-1][0], go1[-1][1]
        stop = [(t0 + i * 5, lat0 + (-1) ** i * 0.00002, 21.0) for i in range(1, 60)]
        stop[30] = (stop[30][0], 52.3, 21.0)  # скачок на ~30 км
        go2 = [(t0 + 300 + ts, lat0 + lat - 52.0, lon) for ts, lat, lon in line(31)]
        stats = compute_stats(go1 + stop + go2, 75)
        self.assertAlmostEqual(stats["distance_km"], 60 * 0.0556, delta=0.01)  # только езда
        self.assertEqual(stats["moving_s"], 600)
        self.assertEqual(stats["duration_s"], 900)

    def test_jitter_does_not_add_distance(self):
        still = [(i * 5, 52.0 + (-1) ** i * 0.00001, 21.0) for i in range(120)]  # 10 мин, ±1 м
        stats = compute_stats(still, 75, "walk")
        self.assertEqual((stats["distance_km"], stats["moving_s"]), (0, 0))

    def test_autopause(self):
        def track(crawl_s):  # 100 с едем, crawl_s ползём 2 км/ч, 100 с едем
            go = line(11)
            t0, lat0 = go[-1][0], go[-1][1]
            crawl = [(t0 + i * 10, lat0 + i * 0.00005, 21.0) for i in range(1, crawl_s // 10 + 1)]
            t1, lat1 = crawl[-1][0], crawl[-1][1]
            return go + crawl + [(t1 + ts, lat1 + lat - 52.0, lon) for ts, lat, lon in line(11)[1:]]

        self.assertEqual(compute_stats(track(10), 75)["moving_s"], 210)  # короткое замедление — в движении
        long_crawl = compute_stats(track(60), 75)
        self.assertEqual(long_crawl["duration_s"], 260)
        self.assertEqual(long_crawl["moving_s"], 210)  # после 12 с медленной езды — пауза

    def test_speed_limit_depends_on_activity(self):
        fast = [(i * 10, 52.0 + i * 0.001, 21.0) for i in range(10)]  # ~40 км/ч
        self.assertAlmostEqual(compute_stats(fast, 75, "ride")["distance_km"], 1.0, places=1)
        self.assertEqual(compute_stats(fast, 75, "run")["distance_km"], 0)  # для бега это скачки GPS


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.db = Storage(":memory:")
        self.addCleanup(self.db.conn.close)

    def ride(self, user_id=1, points=line(10), live_until=None):
        ride_id = self.db.start_ride(user_id, user_id, 100, points[0][0], live_until)
        for ts, lat, lon in points:
            self.db.add_point(ride_id, ts, lat, lon, 10)
        return ride_id

    def test_finish_saves_stats_once(self):
        ride_id = self.ride()
        self.assertTrue(self.db.finish_ride(ride_id))
        self.assertFalse(self.db.finish_ride(ride_id))  # второй итог не придёт
        self.assertGreater(self.db.get_ride(ride_id)["distance_km"], 0.4)

    def test_inaccurate_points_dropped(self):
        ride_id = self.db.start_ride(1, 1, 100, 0)
        self.db.add_point(ride_id, 0, 52.0, 21.0, accuracy=500)
        self.assertEqual(self.db.get_points(ride_id), [])

    def test_stale_rides(self):
        now = 1_000_000.0
        fresh = self.ride(1, line(3, now - 60))
        silent = self.ride(2, line(3, now - STALE_AFTER_S - 100))
        expired = self.ride(3, line(3, now - 600), live_until=now - 120)
        stale = dict(self.db.stale_rides(now))
        self.assertNotIn(fresh, stale)
        self.assertEqual(stale[silent], now - STALE_AFTER_S - 100 + 20)  # закрыта по последней точке
        self.assertIn(expired, stale)

    def test_delete_user_removes_everything(self):
        self.db.set_consent(1, 1)
        self.db.set_weight(1, 80)
        mine, other = self.ride(1), self.ride(2)
        self.db.delete_user(1)
        self.assertIsNone(self.db.get_ride(mine))
        self.assertEqual(self.db.get_points(mine), [])
        self.assertEqual(self.db.consent_version(1), 0)
        self.assertEqual(self.db.export_user(1), {"user": {"user_id": 1}, "rides": []})
        self.assertEqual(len(self.db.get_points(other)), 10)

    def test_export(self):
        self.db.set_consent(1, 1)
        self.db.finish_ride(self.ride(1))
        data = self.db.export_user(1)
        self.assertEqual(data["user"]["consent_version"], 1)
        self.assertEqual(len(data["rides"][0]["points"]), 10)
        json.dumps(data)  # должно сериализоваться для /export

    def test_totals_count_finished_only(self):
        self.db.finish_ride(self.ride(1, line(10, 0)))
        self.ride(1, line(10, 1000))  # идёт сейчас
        totals = self.db.totals(1)
        self.assertEqual(totals["count"], 1)
        self.assertAlmostEqual(totals["distance_km"], 0.5, places=1)

    def test_migrates_old_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "old.db")
            old = sqlite3.connect(path)
            old.executescript(SCHEMA)  # схема первой версии
            old.execute("INSERT INTO rides(user_id, chat_id, message_id, started_at, ended_at) VALUES (1, 1, 1, 0, 90)")
            old.executemany("INSERT INTO points(ride_id, ts, lat, lon) VALUES (1, ?, ?, ?)", line(10))
            old.commit()
            old.close()
            db = Storage(path)
            self.assertAlmostEqual(db.get_ride(1)["distance_km"], 0.5, places=1)
            self.assertEqual(db.consent_version(1), 0)  # старые пользователи принимают условия заново
            db.conn.close()

    def test_backup_keeps_last_7(self):
        with tempfile.TemporaryDirectory() as tmp:
            for day in range(1, 10):
                Path(tmp, f"beout-2000-01-0{day}.db").touch()
            self.ride()
            self.db.backup(tmp)
            files = sorted(Path(tmp).glob("beout-*.db"))
            self.assertEqual(len(files), 7)
            copy = sqlite3.connect(files[-1])  # сегодняшняя — самая новая
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM points").fetchone()[0], 10)
            copy.close()


class ApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bot.db = Storage(":memory:")
        self.tg = SimpleNamespace(send_message=AsyncMock())
        self.client = TestClient(TestServer(bot.make_web_app(self.tg)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        bot.db.conn.close()

    def consent(self, *user_ids):
        for user_id in user_ids:
            bot.db.set_consent(user_id, bot.POLICY_VERSION)

    async def call(self, method, path, user_id=None, **kwargs):
        headers = {"X-Init-Data": init_data(user_id)} if user_id else {}
        return await self.client.request(method, path, headers=headers, **kwargs)

    async def test_requires_signature(self):
        self.assertEqual((await self.call("GET", "/api/rides")).status, 401)

    async def test_requires_consent(self):
        self.assertEqual((await self.call("GET", "/api/rides", 1)).status, 403)
        self.consent(1)
        self.assertEqual((await self.call("GET", "/api/rides", 1)).status, 200)

    async def test_cannot_see_others_rides(self):
        self.consent(1, 2)
        ride_id = bot.db.start_ride(2, 2, 1, time.time())
        self.assertEqual((await self.call("GET", f"/api/rides/{ride_id}", 1)).status, 404)
        self.assertEqual((await self.call("GET", f"/api/rides/{ride_id}", 2)).status, 200)
        self.assertEqual((await (await self.call("GET", "/api/rides", 1)).json())["rides"], [])

    async def test_finish_from_mini_app(self):
        self.consent(1, 2)
        ride_id = bot.db.start_ride(1, 1, 1, time.time())
        self.assertEqual((await self.call("POST", f"/api/rides/{ride_id}/finish", 2)).status, 404)
        response = await self.call("POST", f"/api/rides/{ride_id}/finish", 1)
        self.assertEqual(response.status, 200)
        self.assertFalse((await response.json())["ride"]["active"])
        self.tg.send_message.assert_awaited_once()  # итог пришёл в чат

    async def test_weight_validation(self):
        self.consent(1)
        self.assertEqual((await self.call("POST", "/api/weight", 1, data="not json")).status, 400)
        self.assertEqual((await self.call("POST", "/api/weight", 1, json={"weight": 500})).status, 400)
        self.assertEqual((await self.call("POST", "/api/weight", 1, json={"weight": 70})).status, 200)
        self.assertEqual(bot.db.get_weight(1), 70)

    async def test_pagination(self):
        self.consent(1)
        for i in range(bot.PAGE_SIZE + 1):
            bot.db.start_ride(1, 1, i, ts=1000.0 + i)
        page = await (await self.call("GET", "/api/rides", 1)).json()
        self.assertEqual(len(page["rides"]), bot.PAGE_SIZE)
        self.assertTrue(page["has_more"])
        last = page["rides"][-1]["started_at"]
        page2 = await (await self.call("GET", f"/api/rides?before={last}", 1)).json()
        self.assertEqual([r["started_at"] for r in page2["rides"]], [1000.0])
        self.assertFalse(page2["has_more"])

    async def test_privacy_page(self):
        response = await self.call("GET", "/privacy")
        self.assertEqual(response.status, 200)
        self.assertIn("/delete", await response.text())


class HandlersTest(unittest.IsolatedAsyncioTestCase):
    """Обработчики бота с поддельными сообщениями Telegram."""

    def setUp(self):
        bot.db = Storage(":memory:")
        self.addCleanup(bot.db.conn.close)
        self.tg = SimpleNamespace(send_message=AsyncMock())

    def location(self, live_period=900):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=1), chat=SimpleNamespace(id=1), message_id=7,
            date=time.time(), edit_date=time.time(), answer=AsyncMock(), bot=self.tg,
            location=SimpleNamespace(latitude=52.0, longitude=21.0, horizontal_accuracy=5,
                                     live_period=live_period),
        )

    def callback(self, data):
        return SimpleNamespace(from_user=SimpleNamespace(id=1), message=None, data=data,
                               bot=self.tg, answer=AsyncMock())

    async def test_nothing_recorded_without_consent(self):
        msg = self.location()
        await bot.on_location(msg)
        self.assertIsNone(bot.db.active_ride(1))
        self.assertIn("Принимаю", msg.answer.call_args.args[0])

    async def test_ride_lifecycle_and_delete(self):
        await bot.on_consent(self.callback("consent"))
        await bot.on_location(self.location())
        ride = bot.db.active_ride(1)
        self.assertIsNotNone(ride)

        stop = self.location(live_period=None)  # трансляцию остановили
        self.tg.send_message.reset_mock()
        await bot.on_location_update(stop)
        await bot.on_location_update(stop)
        self.assertEqual(self.tg.send_message.await_count, 1)  # итог — ровно один раз
        self.assertIsNotNone(bot.db.get_ride(ride["id"])["ended_at"])

        await bot.on_delete(self.callback("delete:yes"))
        self.assertIsNone(bot.db.get_ride(ride["id"]))
        self.assertFalse(bot.has_consent(1))

    async def test_finish_button(self):
        await bot.on_consent(self.callback("consent"))
        msg = self.location()
        await bot.on_location(msg)
        ride = bot.db.active_ride(1)
        markup = msg.answer.call_args.kwargs["reply_markup"]
        self.assertIn(f"finish:{ride['id']}", [b.callback_data for row in markup.inline_keyboard for b in row])

        self.tg.send_message.reset_mock()
        stranger = self.callback(f"finish:{ride['id']}")
        stranger.from_user = SimpleNamespace(id=2)
        await bot.on_finish(stranger)
        self.assertIsNone(bot.db.get_ride(ride["id"])["ended_at"])  # чужую не завершить

        await bot.on_finish(self.callback(f"finish:{ride['id']}"))
        await bot.on_finish(self.callback(f"finish:{ride['id']}"))
        self.assertIsNotNone(bot.db.get_ride(ride["id"])["ended_at"])
        self.assertEqual(self.tg.send_message.await_count, 1)  # итог — ровно один раз


if __name__ == "__main__":
    unittest.main()
