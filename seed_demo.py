"""Добавляет тестовую поездку, чтобы посмотреть Mini App, не выходя на улицу.

Запуск:  python seed_demo.py <ваш_telegram_user_id>
Свой user_id можно узнать, например, у @userinfobot.
"""
import math
import sys
import time

from storage import Storage


def main():
    if len(sys.argv) != 2:
        sys.exit("Использование: python seed_demo.py <telegram_user_id>")
    user_id = int(sys.argv[1])
    db = Storage("beout.db")

    # Круг ~10 км вокруг центра Варшавы, ~22 км/ч, точка каждые 20 секунд.
    center_lat, center_lon, radius_m = 52.2297, 21.0122, 1600
    start = time.time() - 3600
    ride_id = db.start_ride(user_id, chat_id=user_id, message_id=-1, ts=start)
    n = 82
    for i in range(n + 1):
        angle = 2 * math.pi * i / n
        lat = center_lat + (radius_m * math.sin(angle)) / 111_320
        lon = center_lon + (radius_m * math.cos(angle)) / (111_320 * math.cos(math.radians(center_lat)))
        db.add_point(ride_id, start + i * 20, lat, lon)
    db.finish_ride(ride_id, start + n * 20)
    print(f"Готово: тестовая поездка #{ride_id} для пользователя {user_id}")


if __name__ == "__main__":
    main()
