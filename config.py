"""
config.py — Общие константы для bot.py и dashboard.py.

Импортируй отсюда DATABASE_URL, MSK, PROJECTS, PROJECT_EMOJI, MONTHS_RU
вместо дублирования в каждом файле.
"""
import os
import pytz

# ─── База данных ───────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)

# ─── Временная зона ────────────────────────────────────────────
MSK = pytz.timezone("Europe/Moscow")

# ─── Проекты ──────────────────────────────────────────────────
PROJECTS = ["KSF", "KLIFE", "PA", "PIELE", "Визенкова"]

PROJECT_EMOJI = {
    "KSF": "🔵", "KLIFE": "🟣",
    "PA": "🟢",  "PIELE": "🟡", "Визенкова": "🟠",
}

# ─── Локализация дат ──────────────────────────────────────────
MONTHS_RU = ["янв", "фев", "мар", "апр", "май", "июн",
             "июл", "авг", "сен", "окт", "ноя", "дек"]
