"""
db.py — Connection pool к PostgreSQL, общий для bot.py и dashboard.py.

Используй get_conn() как контекстный менеджер:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(...)
"""
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from config import DATABASE_URL

_db_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _db_pool
    if _db_pool is None:
        _db_pool = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=5,          # 2 воркера × 5 < лимита Postgres (+ бот на той же БД)
            timeout=15,          # не ждать соединение 30 сек — быстрее отдавать ошибку
            max_idle=300,        # закрывать простаивающие соединения, освобождать слоты
            max_lifetime=1800,   # раз в 30 мин пересоздавать соединение (профилактика)
            # Проверять соединение перед выдачей: если Postgres закрыл его (idle-timeout,
            # рестарт, сетевой обрыв) — пул отбросит битое и выдаст живое. Иначе первый
            # запрос падает с "SSL SYSCALL error: EOF detected".
            check=ConnectionPool.check_connection,
            kwargs={"row_factory": dict_row, "connect_timeout": 10},
        )
    return _db_pool


def get_conn():
    """Возвращает соединение из пула. Использовать как context manager."""
    return _get_pool().connection()
