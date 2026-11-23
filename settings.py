"""Секреты и адрес берутся из окружения, чтобы токен не лежал в коде.

На сервере их задаёт C:\\srv\\beout\\start.ps1 (токен — из secrets\\bot_token).
"""
import os

APP_NAME = "Be. Out!"
TOKEN = os.environ["BOT_TOKEN"]
URL = os.environ["WEBAPP_URL"]  # публичный HTTPS-адрес (туннель cloudflared)
