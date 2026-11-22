"""Проверка initData, которую Telegram передаёт в Mini App.

Без этой проверки любой мог бы запросить чужие поездки, подставив чужой user_id.
Алгоритм — из документации Telegram (Validating data received via the Mini App).
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_init_data(init_data, bot_token, max_age_s=24 * 3600):
    """Возвращает dict пользователя Telegram или None, если подпись неверна."""
    if not init_data:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        return None

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if max_age_s and time.time() - auth_date > max_age_s:
        return None

    try:
        return json.loads(pairs["user"])
    except (KeyError, ValueError):
        return None
