"""
wb_api.py — Клиент Wildberries API для еженедельных отчётов по кабинетам.

Реализованные эндпоинты:
    1. GET  /adv/v1/upd                           — история расходов на рекламу
    2. GET  /adv/v1/promotion/count               — списки кампаний (типы/статусы)
    3. GET  /adv/v3/fullstats                     — статистика кампаний
    4. POST /api/analytics/v3/sales-funnel/products — воронка по карточкам

Ключи API хранятся в переменных окружения вида:
    WB_API_KEY_<CABINET>      — например, WB_API_KEY_KLIFE, WB_API_KEY_KSF
    WB_API_KEY                — fallback на один общий ключ
"""
from __future__ import annotations

import os
import time
import logging
import calendar as _calendar
from datetime import datetime, date, timedelta
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)

ADV_BASE        = "https://advert-api.wildberries.ru"
STATS_BASE      = "https://seller-analytics-api.wildberries.ru"
STATISTICS_BASE = "https://statistics-api.wildberries.ru"
PRICES_BASE     = "https://discounts-prices-api.wildberries.ru"
CONTENT_BASE    = "https://content-api.wildberries.ru"

# Соответствие статусов рекламных кампаний (для UI)
ADVERT_STATUS = {
    -1: "В процессе удаления",
    4:  "Готова к запуску",
    7:  "Завершена",
    8:  "Отказался",
    9:  "Идут показы",
    11: "Приостановлена",
}

# Соответствие типов рекламных кампаний
ADVERT_TYPE = {
    4:  "Каталог",
    5:  "Карточка товара",
    6:  "Поиск",
    7:  "Рекомендации",
    8:  "Авто (АРК)",
    9:  "Поиск + Каталог",
}


# ──────────────────────────────────────────────────────────────────────────────
# Получение ключа кабинета
# ──────────────────────────────────────────────────────────────────────────────

# Маппинг отображаемого имени кабинета → суффикс для имени env-переменной.
# Нужен для кабинетов с кириллицей/пробелами/точками: они не могут быть частью
# имени env-переменной напрямую. Простые ASCII-имена (KLIFE, KSF, ...) сюда
# добавлять не нужно — для них используется .upper() как fallback.
CABINET_ENV_SLUGS: dict[str, str] = {
    "Визенкова А.Д": "VIZENKOVA_AD",
    "Визенков Д.А":  "VIZENKOV_DA",
    "Визенкова":     "VIZENKOVA",   # legacy, если такая переменная уже есть
}


def cabinet_env_slug(cabinet: str) -> str:
    """Превращает имя кабинета в часть имени env-переменной (только ASCII)."""
    if cabinet in CABINET_ENV_SLUGS:
        return CABINET_ENV_SLUGS[cabinet]
    # Обратное направление: если пришёл уже slug (например, vizenkova_ad)
    upper = cabinet.upper()
    if upper in CABINET_ENV_SLUGS.values():
        return upper
    return upper


# Невидимые символы, которые регулярно попадают в токен при копировании
# из браузера/Word/документации WB и ломают HTTP-заголовок Authorization.
_HIDDEN_CHARS = {
    "\xa0":      "",   # NBSP (non-breaking space, U+00A0)  — самая частая причина
    " ":    "",   # FIGURE SPACE
    " ":    "",   # NARROW NO-BREAK SPACE
    "​":    "",   # ZERO WIDTH SPACE
    "‌":    "",   # ZERO WIDTH NON-JOINER
    "‍":    "",   # ZERO WIDTH JOINER
    "﻿":    "",   # BOM
    " ":    "",   # LINE SEPARATOR
    " ":    "",   # PARAGRAPH SEPARATOR
}


def _sanitize_token(raw: str) -> str:
    """Удаляет невидимые символы, обрезает пробелы/переносы."""
    if not raw:
        return ""
    cleaned = raw.strip()
    for ch, repl in _HIDDEN_CHARS.items():
        if ch in cleaned:
            cleaned = cleaned.replace(ch, repl)
    # Снова обрезаем — на случай, если после замены остались крайние пробелы
    return cleaned.strip()


def get_cabinet_key(cabinet: str) -> str:
    """
    Возвращает API-ключ для указанного кабинета.
    Приоритет: WB_API_KEY_<CABINET> → WB_API_KEY.
    Автоматически вычищает невидимые символы (NBSP и т.п.), которые часто
    попадают в env-переменную при копировании из браузера.
    """
    # KLIFE → WB_API_KEY_KLIFE
    # Визенкова А.Д → WB_API_KEY_VIZENKOVA_AD (через cabinet_env_slug)
    key_name = f"WB_API_KEY_{cabinet_env_slug(cabinet)}"
    raw = os.environ.get(key_name) or os.environ.get("WB_API_KEY", "")
    if not raw:
        raise RuntimeError(
            f"Не задан WB API ключ: ни {key_name}, ни WB_API_KEY. "
            f"Добавьте переменную окружения в Railway."
        )

    val = _sanitize_token(raw)

    # Если после очистки токен пустой или содержит не-ASCII символы —
    # значит он повреждён (например, скопирован с кавычками или кириллицей).
    if not val:
        raise RuntimeError(
            f"WB API ключ для кабинета {cabinet} пустой после очистки. "
            f"Проверьте {key_name} в Railway."
        )
    try:
        val.encode("ascii")
    except UnicodeEncodeError:
        # Найдём проблемный символ и его позицию для понятного сообщения
        bad_pos = next(
            (i for i, ch in enumerate(val) if ord(ch) > 127), -1
        )
        bad_char = val[bad_pos] if bad_pos >= 0 else "?"
        raise RuntimeError(
            f"WB API ключ для кабинета {cabinet} содержит недопустимый символ "
            f"'{bad_char}' (U+{ord(bad_char):04X}) на позиции {bad_pos}. "
            f"Скорее всего токен скопирован с лишним символом. "
            f"Пересоздайте токен в ЛК WB и заново вставьте его в {key_name} "
            f"в Railway → Variables (не через буфер обмена из браузера, "
            f"а как plain text)."
        )
    return val


def list_configured_cabinets() -> list[str]:
    """Возвращает список кабинетов, для которых задан ключ WB_API_KEY_*."""
    prefix = "WB_API_KEY_"
    return sorted(
        k[len(prefix):].capitalize()
        for k in os.environ
        if k.startswith(prefix) and os.environ[k]
    )


# ──────────────────────────────────────────────────────────────────────────────
# Клиент с rate-limit и повторами
# ──────────────────────────────────────────────────────────────────────────────

class WBClient:
    """Клиент к WB Advert & Seller-Analytics API."""

    def __init__(self, cabinet: str, timeout: float = 30.0):
        self.cabinet = cabinet
        self.api_key = get_cabinet_key(cabinet)
        self.timeout = timeout
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": self.api_key},
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ── HTTP helper с уважением к 429 ──
    # Максимальное ожидание по одному запросу. WB иногда отдаёт X-Ratelimit-Retry
    # в тысячи секунд (фактически бан на часы) — спать столько нельзя, иначе отчёт
    # висит бесконечно. Если бэкофф больше лимита — не ждём, отдаём 429 наверх
    # (вызывающий деградирует: get_stocks/get_sales вернут [], отчёт соберётся без них).
    MAX_429_WAIT = 45

    def _request(self, method: str, url: str, **kw):
        for attempt in range(4):
            r = self._client.request(method, url, **kw)
            if r.status_code == 429:
                wait = float(r.headers.get("X-Ratelimit-Retry", "5"))
                if wait > self.MAX_429_WAIT:
                    logger.warning(
                        f"[WB:{self.cabinet}] 429, retry-after {wait:.0f}с > "
                        f"{self.MAX_429_WAIT}с — пропускаем запрос {url.rsplit('/',1)[-1]}"
                    )
                    return r
                logger.warning(f"[WB:{self.cabinet}] 429 — ждём {wait:.0f}с (попытка {attempt+1})")
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                logger.warning(f"[WB:{self.cabinet}] {r.status_code}, retry {attempt+1}")
                time.sleep(2 + attempt)
                continue
            return r
        return r  # последний ответ как есть

    # ── 1. История расходов ──────────────────────────────────────────────
    def get_upd(self, date_from: str, date_to: str) -> list[dict]:
        """
        Возвращает историю списаний за период (max 31 день).
        Поля ответа: updNum, updTime, updSum, advertId, campName, advertType, paymentType, advertStatus
        """
        r = self._request(
            "GET",
            f"{ADV_BASE}/adv/v1/upd",
            params={"from": date_from, "to": date_to},
        )
        if not r.is_success:
            logger.error(f"[WB:{self.cabinet}] upd {r.status_code}: {r.text[:200]}")
            return []
        try:
            return r.json() or []
        except Exception:
            return []

    # ── 2. Списки кампаний ───────────────────────────────────────────────
    def get_promotion_count(self) -> dict:
        """
        Возвращает кампании, сгруппированные по типу и статусу.
        Ответ: {"adverts": [{type, status, count, advert_info: [...]}, ...], "all": N}
        """
        r = self._request("GET", f"{ADV_BASE}/adv/v1/promotion/count")
        if not r.is_success:
            logger.error(f"[WB:{self.cabinet}] promotion/count {r.status_code}: {r.text[:200]}")
            return {"adverts": [], "all": 0}
        try:
            return r.json() or {"adverts": [], "all": 0}
        except Exception:
            return {"adverts": [], "all": 0}

    def get_active_campaign_ids(self) -> list[int]:
        """Возвращает ID кампаний в статусах активных (9, 11) и приостановленных."""
        data = self.get_promotion_count()
        ids: list[int] = []
        for grp in data.get("adverts", []):
            status = grp.get("status")
            # 9 — идут показы, 11 — пауза, 7 — завершена; нас интересуют статусы с открученной статистикой
            if status in (7, 9, 11):
                for adv in grp.get("advert_info", []) or []:
                    if adv.get("advertId"):
                        ids.append(int(adv["advertId"]))
        return ids

    # ── 3. Статистика кампаний ───────────────────────────────────────────
    def get_fullstats(
        self,
        campaign_ids: list[int],
        date_from: str,
        date_to: str,
    ) -> list[dict]:
        """
        Возвращает статистику по кампаниям. Максимум 50 ID за запрос, максимум 31 день.
        Поля: advertId, views, clicks, ctr, cpc, sum, atbs, orders, cr, shks, sum_price, days[]
        """
        if not campaign_ids:
            return []

        result: list[dict] = []
        for i in range(0, len(campaign_ids), 50):
            chunk = campaign_ids[i : i + 50]
            r = self._request(
                "GET",
                f"{ADV_BASE}/adv/v3/fullstats",
                params={
                    "ids": ",".join(str(c) for c in chunk),
                    "beginDate": date_from,
                    "endDate": date_to,
                },
            )
            if not r.is_success:
                logger.error(
                    f"[WB:{self.cabinet}] fullstats {r.status_code}: {r.text[:200]}"
                )
                continue
            try:
                result.extend(r.json() or [])
            except Exception:
                pass
            # WB лимит: 3 запроса/мин для базы; делаем паузу
            time.sleep(20)
        return result

    # ── 4. Воронка продаж по карточкам ───────────────────────────────────
    def get_sales_funnel(
        self,
        date_from: str,
        date_to: str,
        past_from: str | None = None,
        past_to: str | None = None,
        nm_ids: list[int] | None = None,
        limit: int = 1000,
        offset: int = 0,
        order_field: str | None = "openCard",
    ) -> dict:
        """
        Возвращает агрегированную статистику по карточкам с разбиением на текущий
        и предыдущий период. Если past_* не заданы — будет тот же интервал, что
        и selectedPeriod.

        order_field: значение для orderBy.field. WB строго проверяет имена;
            гарантированно валидные по доке: "openCard", "addToCart", "orders",
            "cancel", "buyouts", "avgRubPrice".  В ответе при этом поля идут с
            суффиксами (openCardCount, ordersCount, …) — это особенность WB.
            Передайте None — orderBy не будет добавлен.
        """
        payload: dict = {
            "selectedPeriod": {"start": date_from, "end": date_to},
            "pastPeriod": {
                "start": past_from or date_from,
                "end":   past_to   or date_to,
            },
            "nmIds":       nm_ids or [],
            "brandNames":  [],
            "subjectIds":  [],
            "tagIds":      [],
            "skipDeletedNm": False,
            "limit":  limit,
            "offset": offset,
        }
        if order_field:
            payload["orderBy"] = {"field": order_field, "mode": "desc"}

        # Пробуем перечисленные orderBy.field по очереди, если WB не принимает.
        # Порядок: основной → "orders" → "openCard" → совсем без orderBy.
        fallbacks: list[str | None] = []
        if order_field:
            fallbacks = [order_field, "orders", "openCard", None]
        else:
            fallbacks = [None]

        last_resp = None
        for field in fallbacks:
            p = dict(payload)
            if field:
                p["orderBy"] = {"field": field, "mode": "desc"}
            else:
                p.pop("orderBy", None)

            r = self._request(
                "POST",
                f"{STATS_BASE}/api/analytics/v3/sales-funnel/products",
                json=p,
            )
            last_resp = r
            if r.is_success:
                try:
                    return r.json() or {"data": {"products": [], "currency": "RUB"}}
                except Exception:
                    return {"data": {"products": [], "currency": "RUB"}}
            # 400 — возможно проблема в orderBy, идём дальше; иначе выходим
            if r.status_code != 400:
                break

        logger.error(
            f"[WB:{self.cabinet}] sales-funnel "
            f"{last_resp.status_code if last_resp else '???'}: "
            f"{last_resp.text[:300] if last_resp else ''}"
        )
        return {"data": {"products": [], "currency": "RUB"}}

    # ── 5. Продажи (Statistics API) ──────────────────────────────────────
    def get_sales(self, date_from: str) -> list[dict]:
        """
        Загружает все продажи и возвраты с указанной даты до текущего момента.
        Эндпоинт: GET /api/v1/supplier/sales?dateFrom=YYYY-MM-DD&flag=0
        Rate limit: 1 запрос/минуту, до 80 000 строк за запрос.
        Если строк больше 80 000 — пагинируем по lastChangeDate, между страницами
        ждём 65 сек согласно официальной рекомендации WB.

        Структура каждой записи:
            date, lastChangeDate, supplierArticle, nmId,
            saleID (S... — продажа, R... — возврат),
            totalPrice, forPay, finishedPrice,
            IsStorno (1 — сторно/отмена)
        """
        all_rows: list[dict] = []
        current_from = date_from
        page = 1
        while True:
            r = self._request(
                "GET",
                f"{STATISTICS_BASE}/api/v1/supplier/sales",
                params={"dateFrom": current_from, "flag": "0"},
            )
            if not r.is_success:
                logger.error(
                    f"[WB:{self.cabinet}] sales {r.status_code}: {r.text[:300]}"
                )
                break
            try:
                batch = r.json() or []
            except Exception:
                logger.error(f"[WB:{self.cabinet}] sales: bad JSON")
                break
            if not batch:
                break
            all_rows.extend(batch)
            logger.info(
                f"[WB:{self.cabinet}] sales page {page}: +{len(batch)} "
                f"(total: {len(all_rows)})"
            )
            if len(batch) < 80_000:
                break
            # Пагинация по lastChangeDate последней записи
            next_from = batch[-1].get("lastChangeDate") or current_from
            current_from = next_from
            page += 1
            time.sleep(65)
        return all_rows

    # ── 6. Складские остатки ────────────────────────────────────────────
    @staticmethod
    def _normalize_stock_row(x: dict) -> dict:
        """Приводит строку нового метода wb-warehouses к формату старого stocks
        (supplierArticle, quantityFull, nmId, subject, brand) — под
        aggregate_stocks_by_article. Имена полей берём с запасом (WB могли назвать
        по-разному), реальные ключи видно в логе при первом запросе."""
        def g(*names, default=None):
            for n in names:
                v = x.get(n)
                if v is not None:
                    return v
            return default
        wh_qty  = float(g("quantity", "qty", "amount", "warehouseQuantity", default=0) or 0)
        in_to   = float(g("inWayToClient", "in_way_to_client", default=0) or 0)
        in_from = float(g("inWayFromClient", "in_way_from_client", default=0) or 0)
        qty_full = g("quantityFull", "quantityWbTotal", default=None)
        if qty_full is None:
            qty_full = wh_qty + in_to + in_from
        return {
            "supplierArticle": g("vendorCode", "supplierArticle", "article", default=""),
            "nmId":            g("nmID", "nmId", default=None),
            "quantity":        wh_qty,
            "quantityFull":    float(qty_full or 0),
            "warehouseName":   g("warehouseName", "warehouse", "officeName", default=""),
            "subject":         g("subjectName", "subject", "category", default=""),
            "brand":           g("brandName", "brand", default=""),
            "techSize":        g("techSize", "size", default=""),
        }

    def get_stocks(self, date_from: str) -> list[dict]:
        """
        Текущие остатки на складах WB.
        Старый GET /api/v1/supplier/stocks отключён WB (404, 2026-07-20).
        Новый метод: POST /api/analytics/v1/stocks-report/wb-warehouses
        (seller-analytics-api, категория «Аналитика», offset-пагинация, ~1 req/20с,
        обновление раз в 30 мин). Строка = один размер товара на одном складе.
        Нормализуем к формату старого метода. date_from не используется (метод отдаёт
        текущий срез), оставлен для совместимости сигнатуры.
        """
        url = f"{STATS_BASE}/api/analytics/v1/stocks-report/wb-warehouses"
        raw_rows: list[dict] = []
        limit = 100_000
        offset = 0
        for _ in range(50):
            r = self._request("POST", url, json={"limit": limit, "offset": offset})
            if not r.is_success:
                logger.error(f"[WB:{self.cabinet}] wb-warehouses {r.status_code}: {r.text[:400]}")
                break
            try:
                payload = r.json()
            except Exception:
                logger.error(f"[WB:{self.cabinet}] wb-warehouses: ответ не JSON: {r.text[:200]}")
                break
            # Ответ может быть [...], {"data":[...]} или {"data":{"rows":[...]}}
            rows = payload.get("data") if isinstance(payload, dict) else payload
            if isinstance(rows, dict):
                rows = rows.get("rows") or rows.get("list") or rows.get("items") or rows.get("stocks") or []
            if not isinstance(rows, list):
                rows = []
            if offset == 0:
                top = list(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload) if isinstance(payload, list) else '?'}]"
                logger.info(
                    f"[WB:{self.cabinet}] wb-warehouses: envelope={top}; строк={len(rows)}; "
                    f"ключи первой строки={list(rows[0].keys()) if rows else '—'}"
                )
            if not rows:
                break
            raw_rows.extend(rows)
            if len(rows) < limit:
                break
            offset += limit
        return [self._normalize_stock_row(x) for x in raw_rows]

    # ── 7. Цены и скидки ────────────────────────────────────────────────
    def get_goods_prices(self, nm_id: int | None = None) -> list[dict]:
        """
        Возвращает список товаров с ценами и скидками.
        GET /api/v2/list/goods/filter (discounts-prices-api.wildberries.ru)

        Если nm_id указан — возвращает только этот товар.
        Иначе — постранично получает все товары кабинета.

        Каждый элемент: {nmID, vendorCode, sizes: [{price, discountedPrice, ...}]}
        discountedPrice — цена после скидки продавца (= Цена ВБ для покупателя до СПП).

        Rate limit: 10 req / 6s → пауза 0.7 сек между страницами.
        """
        all_goods: list[dict] = []
        limit = 1000
        offset = 0

        while True:
            params: dict = {"limit": limit, "offset": offset}
            if nm_id is not None:
                params["filterNmID"] = nm_id

            r = self._request(
                "GET",
                f"{PRICES_BASE}/api/v2/list/goods/filter",
                params=params,
            )
            if not r.is_success:
                logger.error(
                    f"[WB:{self.cabinet}] goods/filter {r.status_code}: {r.text[:200]}"
                )
                break
            try:
                data = r.json()
                goods = (data.get("data") or {}).get("listGoods") or []
            except Exception:
                logger.error(f"[WB:{self.cabinet}] goods/filter: bad JSON")
                break

            if not goods:
                break

            all_goods.extend(goods)

            # Если запрашивали конкретный nmId или получили меньше лимита — всё
            if nm_id is not None or len(goods) < limit:
                break

            offset += limit
            time.sleep(0.7)  # соблюдаем rate-limit: 10 req/6 сек

        return all_goods

    # ── 8. Габариты карточек товаров (Content API) ──────────────────────
    def get_cards_dimensions(self) -> dict[int, float]:
        """
        Возвращает {nmID: liters} для всех карточек кабинета.
        Источник: POST /content/v2/get/cards/list (content-api.wildberries.ru)
        Литраж = length_cm × width_cm × height_cm / 1000.

        Пагинация по cursor.updatedAt + cursor.nmID.
        """
        result: dict[int, float] = {}
        cursor: dict = {"limit": 100}

        while True:
            payload = {
                "settings": {
                    "cursor": cursor,
                    "filter": {"withPhoto": -1},
                }
            }
            r = self._request(
                "POST",
                f"{CONTENT_BASE}/content/v2/get/cards/list",
                json=payload,
            )
            if not r.is_success:
                logger.error(
                    f"[WB:{self.cabinet}] cards/list {r.status_code}: {r.text[:200]}"
                )
                break
            try:
                data = r.json()
            except Exception:
                logger.error(f"[WB:{self.cabinet}] cards/list: bad JSON")
                break

            cards = data.get("cards") or []
            for card in cards:
                nm = card.get("nmID")
                dims = card.get("dimensions") or {}
                l = float(dims.get("length") or 0)
                w = float(dims.get("width")  or 0)
                h = float(dims.get("height") or 0)
                if nm and l > 0 and w > 0 and h > 0:
                    import math as _math
                    result[nm] = _math.ceil(l * w * h / 1000)

            # Проверяем курсор для следующей страницы
            resp_cursor = data.get("cursor") or {}
            total = resp_cursor.get("total", 0)
            if not cards or total < cursor.get("limit", 100):
                break

            # Обновляем курсор для следующего запроса
            cursor = {
                "limit":     100,
                "updatedAt": resp_cursor.get("updatedAt"),
                "nmID":      resp_cursor.get("nmID"),
            }
            time.sleep(0.3)

        return result

    def get_card_photo(self, nm_id: int) -> str | None:
        """
        Возвращает URL основного фото карточки товара (content-api), либо None.
        Источник: POST /content/v2/get/cards/list, поиск по артикулу WB.
        """
        payload = {
            "settings": {
                "cursor": {"limit": 10},
                "filter": {"withPhoto": -1, "textSearch": str(nm_id)},
            }
        }
        r = self._request(
            "POST",
            f"{CONTENT_BASE}/content/v2/get/cards/list",
            json=payload,
        )
        if not r.is_success:
            logger.error(
                f"[WB:{self.cabinet}] cards/list (photo) {r.status_code}: {r.text[:200]}"
            )
            return None
        try:
            data = r.json()
        except Exception:
            logger.error(f"[WB:{self.cabinet}] cards/list (photo): bad JSON")
            return None

        for card in data.get("cards") or []:
            if card.get("nmID") == nm_id:
                photos = card.get("photos") or []
                if photos:
                    photo = photos[0]
                    return photo.get("big") or photo.get("c246x328") or photo.get("square") or photo.get("tm")
        return None

    def raw_sales(self, date_from: str) -> dict:
        """Возвращает сырой ответ Statistics API (для отладки):
        {url, status, length, first_3, sample_keys, breakdown}."""
        url = f"{STATISTICS_BASE}/api/v1/supplier/sales"
        r = self._request(
            "GET", url,
            params={"dateFrom": date_from, "flag": "0"},
        )
        out = {
            "url":      f"{url}?dateFrom={date_from}&flag=0",
            "status":   r.status_code,
            "preview":  r.text[:600],
            "length":   None,
            "first_3":  None,
            "sample_keys": None,
        }
        if r.is_success:
            try:
                data = r.json() or []
            except Exception:
                data = []
            if isinstance(data, list):
                out["length"]  = len(data)
                out["first_3"] = data[:3]
                if data and isinstance(data[0], dict):
                    out["sample_keys"] = sorted(data[0].keys())

                # ── Группировка по типу (saleID prefix) ────────────────
                sales_S = [s for s in data
                           if isinstance(s.get("saleID"), str) and s["saleID"].startswith("S")]
                ret_R   = [s for s in data
                           if isinstance(s.get("saleID"), str) and s["saleID"].startswith("R")]
                other   = [s for s in data
                           if not isinstance(s.get("saleID"), str)
                              or not s["saleID"].startswith(("S","R"))]

                # ── Группировка по дате (YYYY-MM-DD) ───────────────────
                from collections import Counter
                dates = Counter(str(s.get("date", ""))[:10] for s in data)

                # ── Один сэмпл из продаж (S...), если есть ─────────────
                sample_sale   = sales_S[0] if sales_S else None
                sample_return = ret_R[0]   if ret_R   else None
                sample_other  = other[0]   if other   else None

                # ── IsStorno: есть ли вообще такое поле в данных? ──────
                with_storno     = sum(1 for s in data if "IsStorno" in s)
                storno_eq_1     = sum(1 for s in data if s.get("IsStorno") == 1)
                forpay_negative = sum(1 for s in data
                                      if isinstance(s.get("forPay"), (int, float))
                                         and s["forPay"] < 0)
                forpay_positive = sum(1 for s in data
                                      if isinstance(s.get("forPay"), (int, float))
                                         and s["forPay"] > 0)

                out["breakdown"] = {
                    "count_saleID_S":  len(sales_S),
                    "count_saleID_R":  len(ret_R),
                    "count_saleID_other": len(other),
                    "count_with_IsStorno_field": with_storno,
                    "count_IsStorno_eq_1": storno_eq_1,
                    "count_forPay_negative": forpay_negative,
                    "count_forPay_positive": forpay_positive,
                    "dates_distribution": dict(sorted(dates.items())),
                    "sample_sale_S":   sample_sale,
                    "sample_return_R": sample_return,
                    "sample_other":    sample_other,
                }
        return out

    def raw_sales_funnel(
        self,
        date_from: str,
        date_to: str,
        past_from: str | None = None,
        past_to: str | None = None,
    ) -> dict:
        """Возвращает сырой ответ API (для отладки): {status, text, json}."""
        payload = {
            "selectedPeriod": {"start": date_from, "end": date_to},
            "pastPeriod": {
                "start": past_from or date_from,
                "end":   past_to   or date_to,
            },
            "nmIds": [], "brandNames": [], "subjectIds": [], "tagIds": [],
            "skipDeletedNm": False, "limit": 50, "offset": 0,
            "orderBy": {"field": "openCard", "mode": "desc"},
        }
        r = self._request(
            "POST",
            f"{STATS_BASE}/api/analytics/v3/sales-funnel/products",
            json=payload,
        )
        result = {
            "url": f"{STATS_BASE}/api/analytics/v3/sales-funnel/products",
            "status": r.status_code,
            "request_payload": payload,
            "response_text_preview": r.text[:800],
        }
        try:
            result["response_json"] = r.json()
        except Exception:
            result["response_json"] = None
        return result


# ──────────────────────────────────────────────────────────────────────────────
# Утилиты периода (понедельник–воскресенье)
# ──────────────────────────────────────────────────────────────────────────────

def week_range(reference: date | None = None) -> tuple[date, date]:
    """Возвращает (понедельник, воскресенье) недели, в которую попадает reference."""
    d = reference or date.today()
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def previous_week(monday: date, sunday: date) -> tuple[date, date]:
    """Та же длина периода, на 7 дней назад."""
    length = (sunday - monday).days
    prev_to   = monday - timedelta(days=1)
    prev_from = prev_to - timedelta(days=length)
    return prev_from, prev_to


def fmt_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


# ──────────────────────────────────────────────────────────────────────────────
# Высокоуровневая агрегация для отчёта
# ──────────────────────────────────────────────────────────────────────────────

def collect_report_data(
    cabinet: str,
    date_from: date,
    date_to: date,
) -> dict:
    """
    Собирает все данные для еженедельного отчёта по кабинету.
    Возвращает структуру, готовую для wb_report.render_html().
    """
    prev_from, prev_to = previous_week(date_from, date_to)

    with WBClient(cabinet) as wb:
        # 1) История расходов (суммы по advertId)
        upd_rows = wb.get_upd(fmt_date(date_from), fmt_date(date_to))
        upd_by_advert: dict[int, dict] = {}
        for row in upd_rows:
            aid = int(row.get("advertId") or 0)
            if not aid:
                continue
            cell = upd_by_advert.setdefault(aid, {
                "advertId":     aid,
                "campName":     row.get("campName", ""),
                "advertType":   row.get("advertType"),
                "advertStatus": row.get("advertStatus"),
                "sum":          0.0,
            })
            cell["sum"] += float(row.get("updSum") or 0)

        # 2) Активные кампании за период
        active_ids = list(upd_by_advert.keys()) or wb.get_active_campaign_ids()

        # 3) Статистика по кампаниям (показы, клики, CTR)
        stats = wb.get_fullstats(active_ids, fmt_date(date_from), fmt_date(date_to))
        stats_by_advert: dict[int, dict] = {int(s["advertId"]): s for s in stats}

        # 4) Воронка по карточкам — текущий и предыдущий период
        funnel = wb.get_sales_funnel(
            fmt_date(date_from), fmt_date(date_to),
            fmt_date(prev_from), fmt_date(prev_to),
        )

        # 5) Продажи (Statistics API) — реальные продажи и выкупы по supplierArticle.
        #    Воронка даёт ненадёжные buyoutCount, поэтому используем этот источник.
        #    Один запрос с самой ранней даты — затем фильтруем локально на 2 периода.
        sales_raw = wb.get_sales(fmt_date(prev_from))

        # 6) Складские остатки — текущий снимок на момент формирования отчёта
        stocks_raw = wb.get_stocks(fmt_date(date_from))

    products = (funnel.get("data") or {}).get("products") or []

    # Агрегация продаж по vendorCode (supplierArticle) для двух периодов
    sales_by_vendor, sales_total, sales_for_pay, sales_for_pay_by_vendor = (
        aggregate_sales_by_article(sales_raw, date_from, date_to)
    )
    sales_by_vendor_prev, sales_total_prev, _, _prev_fp = aggregate_sales_by_article(
        sales_raw, prev_from, prev_to
    )

    # Агрегация складских остатков. Новый метод остатков отдаёт nmId без vendorCode —
    # строим карту nmId→vendorCode из воронки для сопоставления.
    nmid_to_vc = {}
    for p in products:
        nm = get_product_field(p, "nmId")
        vc = get_product_field(p, "vendorCode")
        if nm and vc:
            nmid_to_vc[str(nm)] = str(vc)
    stock_by_vendor, stock_total, _stock_meta = aggregate_stocks_by_article(stocks_raw, nmid_to_vc)

    # Агрегаты кабинета — текущий период (воронка)
    cur_views   = sum(_funnel_metric(p, "openCount",  "selected") for p in products)
    cur_visits  = sum(_funnel_metric(p, "openCount",  "selected") for p in products)
    cur_basket  = sum(_funnel_metric(p, "cartCount",  "selected") for p in products)
    cur_orders  = sum(_funnel_metric(p, "orderCount", "selected") for p in products)
    cur_revenue = sum(_funnel_metric(p, "orderSum",   "selected") for p in products)

    # Агрегаты — предыдущий период (воронка)
    prv_views   = sum(_funnel_metric(p, "openCount",  "past") for p in products)
    prv_basket  = sum(_funnel_metric(p, "cartCount",  "past") for p in products)
    prv_orders  = sum(_funnel_metric(p, "orderCount", "past") for p in products)
    prv_revenue = sum(_funnel_metric(p, "orderSum",   "past") for p in products)

    # ВЫКУПЫ — из Statistics API (более точные данные).
    # Если по какой-то причине Statistics API вернул пусто (401 без права
    # «Статистика продаж», 429, обновление в WB не успело), делаем fallback
    # на воронку (она хотя бы что-то показывает).
    cur_buyouts    = sales_total
    cur_buyout_sum = sales_for_pay
    prv_buyouts    = sales_total_prev
    buyouts_source = "statistics_api"

    # Fallback срабатывает если в Statistics API нет продаж за ТЕКУЩИЙ период.
    # Чаще всего это значит, что WB задерживает индексацию для конкретного
    # кабинета (типично для новых/недавно подключённых). В таком случае
    # используем агрегаты из воронки и для текущего, и для прошлого периода —
    # чтобы сравнение «текущая vs прошлая неделя» оставалось согласованным.
    if sales_total == 0:
        cur_buyouts    = sum(_funnel_metric(p, "buyoutCount", "selected") for p in products)
        cur_buyout_sum = sum(_funnel_metric(p, "buyoutSum",   "selected") for p in products)
        prv_buyouts    = sum(_funnel_metric(p, "buyoutCount", "past")     for p in products)
        sales_by_vendor         = {}
        sales_by_vendor_prev    = {}
        sales_for_pay_by_vendor = {}
        for p in products:
            vendor = (p.get("product") or {}).get("vendorCode") or ""
            if not vendor:
                continue
            cur_b = int(_funnel_metric(p, "buyoutCount", "selected"))
            prv_b = int(_funnel_metric(p, "buyoutCount", "past"))
            if cur_b:
                sales_by_vendor[vendor] = cur_b
            if prv_b:
                sales_by_vendor_prev[vendor] = prv_b
        buyouts_source = "funnel_fallback"
        logger.warning(
            f"[WB:{cabinet}] Statistics API: получено {len(sales_raw)} строк, "
            f"но 0 продаж после фильтра (всё возвраты или вне периода). "
            f"Fallback на воронку: cur={int(cur_buyouts)}, prev={int(prv_buyouts)}."
        )

    # Расход
    total_spend = sum(c["sum"] for c in upd_by_advert.values())
    total_shows = sum(int(s.get("views") or 0) for s in stats_by_advert.values())

    # Сводка
    drr = (total_spend / cur_revenue * 100) if cur_revenue else 0
    # ДРР по продажам (выкупам): расход / фактические выплаты forPay
    drr_sales = (total_spend / sales_for_pay * 100) if sales_for_pay else 0
    ctr = (cur_basket / cur_views * 100) if cur_views else 0
    cr_basket_orders = (cur_orders / cur_basket * 100) if cur_basket else 0
    buyout_pct      = (cur_buyouts / cur_orders * 100) if cur_orders else 0
    buyout_pct_prev = (prv_buyouts / prv_orders * 100) if prv_orders else 0

    return {
        "cabinet":   cabinet,
        "date_from": date_from,
        "date_to":   date_to,
        "prev_from": prev_from,
        "prev_to":   prev_to,

        "kpi": {
            "spend":        total_spend,
            "revenue":      cur_revenue,
            "revenue_prev": prv_revenue,
            "orders":       cur_orders,
            "orders_prev":  prv_orders,
            "drr":          drr,
            "drr_sales":    drr_sales,
            "sales_for_pay": sales_for_pay,
            "views":        cur_views,
            "views_prev":   prv_views,
            "visits":       cur_visits,
            "basket":       cur_basket,
            "basket_prev":  prv_basket,
            "ctr":          ctr,
            "cr":           cr_basket_orders,
            "buyout_pct":   buyout_pct,
            "buyout_pct_prev": buyout_pct_prev,
            "total_shows_ads": total_shows,
            "stock_total":  stock_total,
        },

        "funnel": {
            "views":   cur_views,
            "visits":  cur_visits,
            "basket":  cur_basket,
            "orders":  cur_orders,
            "buyouts": cur_buyouts,
            "buyout_pct": buyout_pct,

            "views_prev":   prv_views,
            "basket_prev":  prv_basket,
            "orders_prev":  prv_orders,
            "buyout_pct_prev": buyout_pct_prev,
        },

        # Карточки (для таблицы по артикулам) + распределение расхода по nmId не из API,
        # поэтому в отчёте показываем расход на уровне кампании, а артикулы — из воронки
        "products":        products,
        "campaign_spend":  upd_by_advert,    # {advertId: {sum, campName, ...}}
        "campaign_stats":  stats_by_advert,  # {advertId: {views, ctr, ...}}

        # Продажи (выкупы) из Statistics API — для подсчёта реального % выкупа.
        # Если Statistics API не вернул данных, эти значения уже заменены
        # на агрегаты из воронки (см. fallback выше).
        "sales_by_vendor":         sales_by_vendor,       # {supplierArticle: count}
        "sales_by_vendor_prev":    sales_by_vendor_prev,
        "sales_for_pay_by_vendor": sales_for_pay_by_vendor, # {supplierArticle: forPay сумма}
        "sales_total":             cur_buyouts,
        "sales_total_prev":        prv_buyouts,
        "buyouts_source":          buyouts_source,         # "statistics_api" | "funnel_fallback"

        # Складские остатки
        "stock_by_vendor": stock_by_vendor,   # {supplierArticle: quantityFull}
        "stock_total":     stock_total,       # суммарный остаток по кабинету
    }


def _parse_sale_date(s: dict) -> date | None:
    raw = s.get("date") or ""
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def aggregate_sales_by_article(
    sales: list[dict],
    date_from: date,
    date_to: date,
) -> tuple[dict[str, int], int, float, dict[str, float]]:
    """
    Фильтрует сторно (IsStorno=1) и возвраты (saleID начинается на 'R'),
    оставляет только продажи (выкупы) в диапазоне [date_from, date_to].
    Возвращает:
        ({supplierArticle: count}, total_count, total_for_pay_sum, {supplierArticle: for_pay})
    """
    by_article: dict[str, int] = {}
    by_article_for_pay: dict[str, float] = {}
    total_count = 0
    total_for_pay = 0.0

    for s in sales:
        if s.get("IsStorno") == 1:
            continue
        sale_id = s.get("saleID") or ""
        # 'R...' — возврат, нас интересуют только продажи 'S...'
        if isinstance(sale_id, str) and sale_id.startswith("R"):
            continue

        d = _parse_sale_date(s)
        if d is None or d < date_from or d > date_to:
            continue

        key = str(s.get("supplierArticle") or "").strip()
        fp = 0.0
        try:
            fp = float(s.get("forPay") or 0)
        except (TypeError, ValueError):
            pass
        if key:
            by_article[key] = by_article.get(key, 0) + 1
            by_article_for_pay[key] = by_article_for_pay.get(key, 0.0) + fp
        total_count += 1
        total_for_pay += fp

    return by_article, total_count, total_for_pay, by_article_for_pay


def aggregate_stocks_by_article(
    stocks: list[dict], nmid_to_vc: dict | None = None
) -> tuple[dict[str, int], int, dict[str, dict]]:
    """
    Агрегирует складские остатки по vendorCode (артикулу продавца).
    Возвращает: ({supplierArticle: quantityFull}, total_quantity_full, {supplierArticle: {nmId, title}})

    Новый метод WB (wb-warehouses) отдаёт остатки по nmId и БЕЗ vendorCode, поэтому
    для сопоставления передаём nmid_to_vc = {nmId: vendorCode} (из воронки/карточек).
    Если vendorCode так и не нашёлся — остаток учитывается в total, но не привязывается
    к артикулу (в разбивке по артикулам его не будет).
    """
    nmid_to_vc = nmid_to_vc or {}
    by_article: dict[str, int] = {}
    meta: dict[str, dict] = {}
    total = 0
    for s in stocks:
        qty = int(s.get("quantityFull") or 0)
        key = str(s.get("supplierArticle") or "").strip()
        if not key:
            nm = s.get("nmId")
            key = str(nmid_to_vc.get(nm) or nmid_to_vc.get(str(nm)) or "").strip()
        if key:
            by_article[key] = by_article.get(key, 0) + qty
            if key not in meta:
                subject = str(s.get("subject") or s.get("category") or "")
                brand   = str(s.get("brand") or "")
                title   = f"{brand} {subject}".strip() if (brand or subject) else key
                meta[key] = {
                    "nmId": s.get("nmId"),
                    "title": title,
                }
        total += qty
    return by_article, total, meta


def _funnel_metric(product: dict, field: str, period: str) -> float:
    """
    Достаёт метрику из карточки воронки.
    Реальная структура ответа WB /sales-funnel/v3:
        product["statistic"]["selected"|"past"][field]
    period: "selected" | "past"
    """
    stats = product.get("statistic") or product.get("statistics") or {}
    block_key = "selected" if period == "selected" else "past"
    block = stats.get(block_key) or {}
    val = block.get(field, 0) or 0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def get_product_field(p: dict, field: str, default=None):
    """Поля карточки лежат во вложенном product: nmId, vendorCode, title и т.д."""
    inner = p.get("product") or {}
    return inner.get(field, p.get(field, default))


# ──────────────────────────────────────────────────────────────────────────────
# Данные для план-факта
# ──────────────────────────────────────────────────────────────────────────────

def collect_daily_stats(cabinet: str, d: date) -> dict:
    """
    Лёгкий дневной срез для утреннего отчёта в бот.
    Только get_upd (расход) + get_sales_funnel (заказы/выручка).
    Без get_fullstats — чтобы не упираться в rate-limit 3 req/min.
    Возвращает: {cabinet, date, spend, orders, revenue, drr}
    """
    d_str   = fmt_date(d)
    prev_str = fmt_date(d - timedelta(days=1))  # WB API требует разные периоды

    with WBClient(cabinet) as wb:
        upd_rows = wb.get_upd(d_str, d_str)
        funnel   = wb.get_sales_funnel(d_str, d_str, prev_str, prev_str)

    spend    = sum(float(r.get("updSum") or 0) for r in upd_rows)
    products = (funnel.get("data") or {}).get("products") or []
    orders   = sum(_funnel_metric(p, "orderCount", "selected") for p in products)
    revenue  = sum(_funnel_metric(p, "orderSum",   "selected") for p in products)
    drr      = (spend / revenue * 100) if revenue and spend else 0.0

    return {
        "cabinet": cabinet,
        "date":    d,
        "spend":   spend,
        "orders":  orders,
        "revenue": revenue,
        "drr":     drr,
    }


def collect_planfact_data(cabinet: str, date_from: date, date_to: date) -> dict:
    """
    Собирает фактические данные за период для план-факт таблицы:
    заказы и продажи по артикулу из WB API + текущие остатки.

    Возвращает:
        {
          "articles": [
            {vendor_code, nm_id, orders_qty_fact, orders_rub_fact,
             sales_qty_fact, sales_rub_fact, stocks, avg_price, buyout_pct}
          ],
          "totals": {orders_qty_fact, orders_rub_fact, sales_qty_fact,
                     sales_rub_fact, stocks}
        }
    """
    # Прошлый период — те же даты предыдущего месяца (нужно для WB API: past != selected)
    def _prev_month(d: date) -> date:
        if d.month == 1:
            return d.replace(year=d.year - 1, month=12)
        return d.replace(month=d.month - 1)

    past_from = _prev_month(date_from)
    # Берём последний день прошлого месяца (полный месяц, а не matching-день),
    # чтобы orders_qty_past отражал весь прошлый месяц целиком.
    past_to   = past_from.replace(
        day=_calendar.monthrange(past_from.year, past_from.month)[1]
    )

    # Если весь запрошенный период (date_from) ещё не наступил (планирование на
    # будущий месяц), WB API не отдаёт данные по ненаступившим дням — запрос с
    # selected=date_from/date_to вернул бы пустые/нулевые товары, из-за чего
    # past-период (тот же ответ) тоже обнулялся бы для ВСЕХ артикулов и ломал
    # авто-расчёт плана. Решение: меняем периоды местами — просим WB отдать
    # selected=past_from/past_to (прошлый месяц, дни уже наступили), а past =
    # месяц перед ним (нужен только чтобы периоды отличались). Один HTTP-вызов
    # вместо двух — у sales-funnel свой rate-limit, второй вызов в эту же
    # загрузку страницы либо тормозит на 429, либо проваливается тихо.
    is_future_month = date_from > date.today()
    if is_future_month:
        funnel_sel_from, funnel_sel_to = past_from, past_to
        funnel_past_from = _prev_month(past_from)
        funnel_past_to   = funnel_past_from.replace(
            day=_calendar.monthrange(funnel_past_from.year, funnel_past_from.month)[1]
        )
    else:
        funnel_sel_from, funnel_sel_to = date_from, date_to
        funnel_past_from, funnel_past_to = past_from, past_to

    with WBClient(cabinet) as wb:
        # 1. Воронка продаж — все артикулы кабинета с метриками за текущий период.
        #    WB API требует различающиеся периоды, иначе возвращает пустой список.
        funnel = wb.get_sales_funnel(
            fmt_date(funnel_sel_from), fmt_date(funnel_sel_to),
            fmt_date(funnel_past_from), fmt_date(funnel_past_to),
        )
        products = (funnel.get("data") or {}).get("products") or []

        # 2. Продажи/выкупы из Statistics API
        sales_raw = wb.get_sales(fmt_date(date_from))

        # 3. Складские остатки.
        #    WB фильтрует по дате изменения, поэтому берём с запасом — 6 месяцев назад,
        #    чтобы захватить артикулы без движения в текущем месяце.
        stocks_date = (date_from.replace(day=1) - timedelta(days=180)).strftime("%Y-%m-%d")
        stocks_raw = wb.get_stocks(stocks_date)

        # 4. Расход на рекламу за период
        upd_rows = wb.get_upd(fmt_date(date_from), fmt_date(date_to))

    sales_by_vc, _, _, sales_rub_by_vc = aggregate_sales_by_article(
        sales_raw, date_from, date_to
    )
    # Новый метод остатков отдаёт nmId без vendorCode — карта nmId→vendorCode из воронки
    nmid_to_vc = {}
    for p in products:
        nm = get_product_field(p, "nmId")
        vc = get_product_field(p, "vendorCode")
        if nm and vc:
            nmid_to_vc[str(nm)] = str(vc)
    stock_by_vc, stock_total, stock_meta = aggregate_stocks_by_article(stocks_raw, nmid_to_vc)

    from datetime import date as _today_cls
    _today = _today_cls.today()
    _is_current_month = (date_from.year == _today.year and date_from.month == _today.month)
    _early_month = _is_current_month and _today.day <= 7

    def _build_article(vc: str, p, stocks: int) -> dict:
        m = stock_meta.get(vc, {})
        nm_id = (get_product_field(p, "nmId") if p else None) or m.get("nmId")
        title = (str(get_product_field(p, "title") or "") if p else "") or m.get("title", vc)

        if is_future_month:
            # Периоды в запросе поменяны местами (см. выше): "selected" в ответе —
            # это факт прошлого месяца, "past" — месяц перед ним (не используется).
            # Факт текущего/будущего месяца — заведомо 0, дни ещё не наступили.
            ord_qty      = 0
            ord_rub      = 0.0
            ord_qty_past = int(_funnel_metric(p, "orderCount", "selected")) if p else 0
            ord_rub_past = float(_funnel_metric(p, "orderSum",  "selected")) if p else 0.0
            buyout_past  = int(_funnel_metric(p, "buyoutCount", "selected")) if p else 0
        else:
            ord_qty      = int(_funnel_metric(p, "orderCount", "selected")) if p else 0
            ord_qty_past = int(_funnel_metric(p, "orderCount", "past"))     if p else 0
            ord_rub      = float(_funnel_metric(p, "orderSum",  "selected")) if p else 0.0
            ord_rub_past = float(_funnel_metric(p, "orderSum",  "past"))     if p else 0.0
            buyout_past  = int(_funnel_metric(p, "buyoutCount", "past"))     if p else 0
        sal_qty  = int(sales_by_vc.get(vc, 0))
        sal_rub  = float(sales_rub_by_vc.get(vc, 0.0))

        avg_price_cur  = round(ord_rub / ord_qty) if ord_qty else 0
        avg_price_prev = round(ord_rub_past / ord_qty_past) if ord_qty_past else 0
        buyout_pct_cur  = round(sal_qty / ord_qty * 100, 1) if ord_qty else 0.0
        buyout_pct_prev = round(buyout_past / ord_qty_past * 100, 1) if ord_qty_past else 0.0

        use_prev_avg    = _early_month or (avg_price_cur == 0 and avg_price_prev > 0)
        use_prev_buyout = _early_month or (buyout_pct_cur == 0 and buyout_pct_prev > 0)

        avg_price          = avg_price_prev if use_prev_avg and avg_price_prev else avg_price_cur
        avg_price_is_prev  = use_prev_avg and avg_price_prev > 0
        buyout_pct         = buyout_pct_prev if use_prev_buyout and buyout_pct_prev else buyout_pct_cur
        buyout_pct_is_prev = use_prev_buyout and buyout_pct_prev > 0

        return {
            "vendor_code":        vc,
            "nm_id":              nm_id,
            "title":              title,
            "orders_qty_fact":    ord_qty,
            "orders_qty_past":    ord_qty_past,
            "orders_rub_fact":    ord_rub,
            "sales_qty_fact":     sal_qty,
            "sales_rub_fact":     sal_rub,
            "stocks":             stocks,
            "avg_price":          avg_price,
            "avg_price_is_prev":  avg_price_is_prev,
            "buyout_pct":         buyout_pct,
            "buyout_pct_is_prev": buyout_pct_is_prev,
        }

    # Доступны ли данные по остаткам. WB отключил метод остатков (404) — тогда
    # stock_by_vc пуст. В этом случае НЕЛЬЗЯ фильтровать артикулы по остатку > 0,
    # иначе отфильтруется вообще всё и план-факт покажет «нет данных».
    stocks_available = bool(stock_by_vc)

    # Проход 1: артикулы из воронки. Если данные по остаткам есть — берём только с
    # остатком > 0 (как раньше). Если остатков нет (метод WB отключён) — берём все
    # артикулы воронки, чтобы страница не была пустой.
    articles = []
    seen_vcs: set[str] = set()
    for p in products:
        vc = str(get_product_field(p, "vendorCode") or "").strip()
        if not vc:
            continue
        stocks = int(stock_by_vc.get(vc, 0))
        if stocks_available and stocks == 0:
            continue
        seen_vcs.add(vc)
        articles.append(_build_article(vc, p, stocks))

    # Проход 2: артикулы с остатком > 0, которых нет в воронке (без заказов в периоде,
    # но нужны для планирования). Работает только когда данные по остаткам доступны.
    for vc, stocks in stock_by_vc.items():
        if vc in seen_vcs or stocks <= 0:
            continue
        articles.append(_build_article(vc, None, stocks))

    articles.sort(key=lambda x: x["orders_rub_fact"], reverse=True)

    adv_spend = sum(float(r.get("updSum") or 0) for r in upd_rows)
    orders_rub_total = sum(a["orders_rub_fact"] for a in articles)
    sales_rub_total  = sum(a["sales_rub_fact"]  for a in articles)

    return {
        "articles": articles,
        "totals": {
            "orders_qty_fact": sum(a["orders_qty_fact"] for a in articles),
            "orders_rub_fact": orders_rub_total,
            "sales_qty_fact":  sum(a["sales_qty_fact"]  for a in articles),
            "sales_rub_fact":  sales_rub_total,
            "stocks":          stock_total,
            "adv_spend":       adv_spend,
            "drr_orders":      round(adv_spend / orders_rub_total * 100, 1) if orders_rub_total else None,
            "drr_sales":       round(adv_spend / sales_rub_total  * 100, 1) if sales_rub_total  else None,
        },
    }
