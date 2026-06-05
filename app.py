"""
app.py — Flask web dashboard (отдельный сервис).

Google OAuth авторизация, ролевой доступ:
  admin    — всё + /admin панель
  employee — всё (все проекты, WB-отчёты, "Сегодня")
  seller   — только свои проекты, без WB, без "Сегодня"
  pending  — ожидает одобрения
"""
import os
import json
import logging
import functools
from datetime import date, datetime, timedelta

from flask import (
    Flask, jsonify, request, Response,
    session, render_template, redirect,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from authlib.integrations.flask_client import OAuth

from config import DATABASE_URL, PROJECTS, PROJECT_EMOJI
from db import get_conn

# ─── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
logging.basicConfig(level=logging.INFO)

_secret = os.environ.get("SECRET_KEY", "")
if not _secret:
    raise RuntimeError("SECRET_KEY env var is not set — refusing to start")
app.secret_key = _secret

app.config.update(
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8),
    SESSION_COOKIE_HTTPONLY    = True,
    SESSION_COOKIE_SAMESITE    = "Lax",
    SESSION_COOKIE_SECURE      = os.environ.get("FLASK_ENV") != "development",
)

DASHBOARD_URL = os.environ.get(
    "DASHBOARD_URL", "https://web-production-1275f.up.railway.app"
).rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

# ─── Создаём таблицы дизайна при старте веб-процесса ─────────────────────────
def _ensure_design_tables():
    """Идемпотентная миграция — всё в одном соединении."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Таблица задач (project включён сразу)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS design_tasks (
                        id          SERIAL PRIMARY KEY,
                        title       TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        priority    TEXT DEFAULT 'med',
                        assignee    TEXT,
                        due         DATE,
                        project     TEXT,
                        done        BOOLEAN DEFAULT FALSE,
                        done_at     TIMESTAMPTZ,
                        created_by  TEXT DEFAULT '',
                        created_at  TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS design_attachments (
                        id          SERIAL PRIMARY KEY,
                        task_id     INTEGER NOT NULL REFERENCES design_tasks(id) ON DELETE CASCADE,
                        attach_role TEXT NOT NULL DEFAULT 'brief',
                        attach_type TEXT NOT NULL DEFAULT 'link',
                        name        TEXT NOT NULL,
                        url         TEXT DEFAULT '',
                        file_data   BYTEA,
                        mime_type   TEXT DEFAULT '',
                        created_by  TEXT DEFAULT '',
                        created_at  TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                # Добавляем колонки если таблицы уже существовали без них
                cur.execute("ALTER TABLE design_tasks ADD COLUMN IF NOT EXISTS project TEXT")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS design_access BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE design_tasks ADD COLUMN IF NOT EXISTS assignee_email TEXT DEFAULT ''")
                cur.execute("ALTER TABLE design_tasks ADD COLUMN IF NOT EXISTS created_by_email TEXT DEFAULT ''")
                # Автор задачи (email) для уведомлений
                cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS created_by_email TEXT DEFAULT ''")
                cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS assignee_email TEXT DEFAULT ''")
                # Таблица уведомлений
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS notifications (
                        id          SERIAL PRIMARY KEY,
                        user_email  TEXT NOT NULL,
                        type        TEXT NOT NULL,
                        title       TEXT NOT NULL,
                        message     TEXT NOT NULL DEFAULT '',
                        task_id     INTEGER,
                        read        BOOLEAN DEFAULT FALSE,
                        created_at  TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_notif_user_unread ON notifications(user_email, read) WHERE read = FALSE")
                # Комментарии, история и «сегодня» для design_tasks
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS design_comments (
                        id         SERIAL PRIMARY KEY,
                        task_id    INTEGER NOT NULL REFERENCES design_tasks(id) ON DELETE CASCADE,
                        author     TEXT NOT NULL DEFAULT '',
                        text       TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS design_task_history (
                        id         SERIAL PRIMARY KEY,
                        task_id    INTEGER NOT NULL REFERENCES design_tasks(id) ON DELETE CASCADE,
                        changed_by TEXT NOT NULL DEFAULT '',
                        field      TEXT NOT NULL DEFAULT '',
                        old_value  TEXT,
                        new_value  TEXT,
                        changed_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS design_today_claims (
                        id           SERIAL PRIMARY KEY,
                        task_id      INTEGER NOT NULL REFERENCES design_tasks(id) ON DELETE CASCADE,
                        user_name    TEXT NOT NULL,
                        claimed_date DATE NOT NULL DEFAULT CURRENT_DATE,
                        UNIQUE (task_id, claimed_date)
                    )
                """)
                cur.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS dismissed BOOLEAN DEFAULT FALSE")
                # Юнит-экономика
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS unit_economics (
                        id               SERIAL PRIMARY KEY,
                        brand            TEXT NOT NULL DEFAULT '',
                        wb_article       BIGINT,
                        seller_article   TEXT DEFAULT '',
                        cost_price       NUMERIC(12,2) DEFAULT 0,
                        logistics_to_wb  NUMERIC(12,2) DEFAULT 0,
                        packaging        NUMERIC(12,2) DEFAULT 0,
                        overhead         NUMERIC(12,2) DEFAULT 0,
                        defect_pct       NUMERIC(5,2) DEFAULT 0,
                        width_cm         NUMERIC(8,2),
                        length_cm        NUMERIC(8,2),
                        height_cm        NUMERIC(8,2),
                        liters           NUMERIC(8,3),
                        redemption_pct   NUMERIC(5,2) DEFAULT 100,
                        warehouse        TEXT DEFAULT '',
                        irp              NUMERIC(8,3) DEFAULT 1.0,
                        logistics_ktr    NUMERIC(12,2),
                        reception_coef   TEXT DEFAULT 'x0',
                        storage_per_day  NUMERIC(10,4) DEFAULT 0,
                        commission_pct   NUMERIC(5,2) DEFAULT 36,
                        wb_price         NUMERIC(12,2) DEFAULT 0,
                        drr_pct          NUMERIC(5,2) DEFAULT 0,
                        drr_external_rub NUMERIC(12,2) DEFAULT 0,
                        stock            INTEGER DEFAULT 0,
                        project          TEXT NOT NULL DEFAULT '',
                        created_at       TIMESTAMPTZ DEFAULT NOW(),
                        updated_at       TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("ALTER TABLE unit_economics ADD COLUMN IF NOT EXISTS project TEXT NOT NULL DEFAULT ''")
                # quantity → stock (безопасный переименование)
                cur.execute("""
                    DO $$ BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='unit_economics' AND column_name='quantity'
                        ) THEN
                            ALTER TABLE unit_economics RENAME COLUMN quantity TO stock;
                        END IF;
                    END $$
                """)
                cur.execute("ALTER TABLE unit_economics ADD COLUMN IF NOT EXISTS stock INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE unit_economics ADD COLUMN IF NOT EXISTS spp_pct NUMERIC(5,2) DEFAULT 0")
                cur.execute("ALTER TABLE unit_economics ADD COLUMN IF NOT EXISTS tax_system TEXT DEFAULT 'УСН 7%'")
                cur.execute("ALTER TABLE unit_economics ADD COLUMN IF NOT EXISTS tax_pct NUMERIC(5,2) DEFAULT 7")
                # Ежедневные бэкапы
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS unit_economics_backup (
                        id          SERIAL PRIMARY KEY,
                        backup_date DATE NOT NULL,
                        project     TEXT NOT NULL,
                        data        JSONB NOT NULL,
                        created_at  TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE (backup_date, project)
                    )
                """)
                # Логи активности пользователей
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_activity (
                        id          SERIAL PRIMARY KEY,
                        email       TEXT NOT NULL,
                        name        TEXT NOT NULL DEFAULT '',
                        date        DATE NOT NULL DEFAULT CURRENT_DATE,
                        first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        page_views  INT NOT NULL DEFAULT 1,
                        UNIQUE (email, date)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS article_ff_stocks (
                        id           SERIAL PRIMARY KEY,
                        project      TEXT NOT NULL,
                        vendor_code  TEXT NOT NULL,
                        ff_stock     INTEGER NOT NULL DEFAULT 0,
                        expected_stock INTEGER NOT NULL DEFAULT 0,
                        updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (project, vendor_code)
                    )
                """)
            conn.commit()
        logging.info("Design tables ready.")
        # Миграция created_by_email — в отдельной транзакции, ошибки не критичны
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE tasks t
                        SET created_by_email = u.email
                        FROM users u,
                             (SELECT DISTINCT ON (task_id) task_id, changed_by
                              FROM task_history
                              ORDER BY task_id, id ASC) first_hist
                        WHERE t.id = first_hist.task_id
                          AND u.name = first_hist.changed_by
                          AND (t.created_by_email IS NULL OR t.created_by_email = '')
                    """)
                conn.commit()
        except Exception as me:
            logging.warning(f"_ensure_design_tables migration: {me}")
    except Exception as e:
        logging.error(f"_ensure_design_tables error: {e}")

_ensure_design_tables()

# ─── Google OAuth ─────────────────────────────────────────────────────────────
oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ─── WB кабинеты ──────────────────────────────────────────────────────────────
def _get_wb_cabinets() -> list[str]:
    env_list = os.environ.get("WB_CABINETS", "")
    if env_list.strip():
        return [c.strip() for c in env_list.split(",") if c.strip()]
    from wb_api import CABINET_ENV_SLUGS
    slug_to_name = {v: k for k, v in CABINET_ENV_SLUGS.items()}
    prefix = "WB_API_KEY_"
    cabinets = []
    for key in sorted(os.environ):
        if key.startswith(prefix) and os.environ[key]:
            slug = key[len(prefix):]
            cabinets.append(slug_to_name.get(slug, slug))
    return cabinets

WB_CABINETS = _get_wb_cabinets()

# ─── DB helpers ───────────────────────────────────────────────────────────────
def _make_session_dict(user: dict) -> dict:
    return {
        "id":                 user["id"],
        "email":              user["email"],
        "name":               user.get("name") or user["email"],
        "picture":            user.get("picture") or "",
        "role":               user["role"],
        "projects":           list(user.get("projects") or []),
        "planfact_projects":  list(user.get("planfact_projects") or []),
        "planfact_edit":      bool(user.get("planfact_edit")),
        "design_access":      bool(user.get("design_access")),
    }

def _design_projects(u) -> list | None:
    """None = все проекты (admin/employee), [] = нет доступа, [list] = проекты селлера."""
    if u["role"] in ("admin", "employee"):
        return None
    if u["role"] == "seller" and u.get("design_access"):
        return list(u.get("projects") or [])
    return []

def _planfact_allowed(u) -> list | None:
    """None = доступ ко всем кабинетам, [] = нет доступа, [list] = разрешённые кабинеты."""
    if u["role"] in ("admin", "employee"):
        return None   # все кабинеты
    # seller — только явно назначенные кабинеты
    pf = list(u.get("planfact_projects") or [])
    return pf

def _planfact_can_edit(u) -> bool:
    """Admin всегда может редактировать; остальные — только если planfact_edit=True."""
    if u["role"] == "admin":
        return True
    return bool(u.get("planfact_edit"))

def get_user_by_id(user_id: int) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
            return dict(row) if row else None

def get_or_create_user(email: str, name: str, picture: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email=%s", (email,))
            user = cur.fetchone()
            if user:
                cur.execute(
                    "UPDATE users SET name=%s, picture=%s, last_login=NOW() "
                    "WHERE email=%s RETURNING *",
                    (name, picture, email),
                )
                user = cur.fetchone()
            else:
                role = "admin" if (ADMIN_EMAIL and email == ADMIN_EMAIL) else "pending"
                cur.execute(
                    "INSERT INTO users (email, name, picture, role) "
                    "VALUES (%s,%s,%s,%s) RETURNING *",
                    (email, name, picture, role),
                )
                user = cur.fetchone()
        conn.commit()
    return dict(user)

def get_tasks(project: str | None = None, projects: list | None = None):
    """
    project  — фильтр по одному проекту
    projects — фильтр по списку (для sellers)
    оба None — все задачи
    """
    base = """
        SELECT t.*, COALESCE(c.cnt,0) AS comment_count
        FROM tasks t
        LEFT JOIN (SELECT task_id, COUNT(*) AS cnt FROM comments GROUP BY task_id) c
               ON t.id = c.task_id
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            if project:
                cur.execute(
                    base + " WHERE t.project=%s ORDER BY t.created_at DESC",
                    (project,),
                )
            elif projects is not None:
                cur.execute(
                    base + " WHERE t.project = ANY(%s)"
                           " ORDER BY t.project NULLS LAST, t.created_at DESC",
                    (projects,),
                )
            else:
                cur.execute(
                    base + " ORDER BY t.project NULLS LAST, t.created_at DESC"
                )
            return cur.fetchall()

def serialize(task):
    d = dict(task)
    for key in ("created_at", "done_at"):
        if d.get(key):
            d[key] = d[key].isoformat()
    return d

def serialize_comment(c):
    d = dict(c)
    if d.get("created_at"):
        d["created_at"] = d["created_at"].isoformat()
    return d

# ─── Auth helpers ─────────────────────────────────────────────────────────────
def _session_user() -> dict | None:
    return session.get("user")

def require_auth(f):
    """Декоратор API: нужна сессия, роль не pending."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        u = _session_user()
        if not u:
            return jsonify({"error": "unauthorized"}), 401
        if u["role"] == "pending":
            return jsonify({"error": "pending"}), 403
        return f(*args, **kwargs)
    return wrapper

def require_admin(f):
    """Декоратор страниц (redirect): только admin."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        u = _session_user()
        if not u:
            return redirect("/auth/login")
        if u["role"] != "admin":
            return Response("Доступ запрещён", status=403)
        return f(*args, **kwargs)
    return wrapper

def require_admin_api(f):
    """Декоратор API: только admin."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        u = _session_user()
        if not u or u["role"] != "admin":
            return jsonify({"error": "forbidden"}), 403
        return f(*args, **kwargs)
    return wrapper

def api_error(f):
    """Декоратор: ловит Exception → 500 JSON. Убирает try/except boilerplate."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.exception("%s failure", f.__name__)
            return jsonify({"error": str(e)}), 500
    return wrapper

# ─── Auth routes ──────────────────────────────────────────────────────────────
@app.route("/auth/login")
def auth_login():
    if _session_user():
        return redirect("/")
    redirect_uri = f"{DASHBOARD_URL}/auth/callback"
    return oauth.google.authorize_redirect(redirect_uri)

@app.route("/auth/callback")
def auth_callback():
    token = oauth.google.authorize_access_token()
    info = token.get("userinfo") or {}
    email = info.get("email", "")
    if not email:
        return Response("Не удалось получить email от Google", status=400)
    user = get_or_create_user(
        email=email,
        name=info.get("name", email),
        picture=info.get("picture", ""),
    )
    session.permanent = True
    session["user"] = _make_session_dict(user)
    return redirect("/")

@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect("/auth/login")

# ─── Main page ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    u = _session_user()
    if not u:
        return redirect("/auth/login")
    # Обновляем из БД при каждой загрузке страницы — чтобы изменения роли вступали в силу
    fresh = get_user_by_id(u["id"])
    if not fresh:
        session.clear()
        return redirect("/auth/login")
    session["user"] = _make_session_dict(fresh)
    u = session["user"]

    if u["role"] == "pending":
        return render_template("pending.html", user=u)

    role = u["role"]
    if role in ("admin", "employee"):
        visible = [{"name": p, "emoji": PROJECT_EMOJI.get(p, "⚪")} for p in PROJECTS]
    else:  # seller
        visible = [{"name": p, "emoji": PROJECT_EMOJI.get(p, "⚪")} for p in u["projects"]]

    return render_template(
        "dashboard.html",
        current_user=u,
        visible_projects=visible,
        show_wb=(role != "seller"),
        projects_json=json.dumps([p["name"] for p in visible]),
        show_design=_design_auth(u),
        design_projects_json=json.dumps(list(u.get("projects") or []) if role == "seller" else [p["name"] for p in visible]),
    )

# ─── Admin panel ──────────────────────────────────────────────────────────────
@app.route("/admin")
@require_admin
def admin_panel():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users ORDER BY (role='pending') DESC, created_at ASC"
            )
            users = [dict(r) for r in cur.fetchall()]
    return render_template("admin.html", users=users, all_projects=PROJECTS, wb_cabinets=WB_CABINETS)

@app.route("/api/admin/users/<int:user_id>", methods=["POST"])
@require_admin_api
def api_admin_update_user(user_id):
    d = request.json or {}
    role = d.get("role", "pending")
    projects = d.get("projects", [])
    planfact_projects = d.get("planfact_projects", [])
    planfact_edit = bool(d.get("planfact_edit", False))
    design_access = bool(d.get("design_access", False))
    if role not in ("pending", "employee", "seller", "admin"):
        return jsonify({"error": "invalid role"}), 400
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET role=%s, projects=%s, planfact_projects=%s, planfact_edit=%s, design_access=%s WHERE id=%s RETURNING id",
                (role, projects, planfact_projects, planfact_edit, design_access, user_id),
            )
            if not cur.fetchone():
                return jsonify({"error": "not found"}), 404
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@require_admin_api
def api_admin_delete_user(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
        conn.commit()
    return jsonify({"ok": True})

# ─── «Беру сегодня» API ───────────────────────────────────────────────────────
@app.route("/api/today")
@require_auth
def api_today():
    """Возвращает все заявки на сегодня со сведениями о задачах."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT tc.id, tc.task_id, tc.user_name,
                           t.title, t.project, t.priority, t.done
                    FROM today_claims tc
                    JOIN tasks t ON t.id = tc.task_id
                    WHERE tc.claimed_date = CURRENT_DATE
                    ORDER BY tc.user_name, tc.id
                """)
                rows = cur.fetchall()
        claims = [dict(r) for r in rows]
        return jsonify(claims)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/today/claim", methods=["POST"])
@require_auth
def api_today_claim():
    """Сотрудник берёт задачу на сегодня."""
    u = _session_user()
    if u["role"] not in ("admin", "employee"):
        return jsonify({"error": "forbidden"}), 403
    d = request.json or {}
    task_id = d.get("task_id")
    if not task_id:
        return jsonify({"error": "task_id required"}), 400
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Проверяем, что задача существует
                cur.execute("SELECT id FROM tasks WHERE id=%s AND done=FALSE", (task_id,))
                if not cur.fetchone():
                    return jsonify({"error": "task not found or already done"}), 404
                # INSERT OR IGNORE — задача могла быть взята другим
                cur.execute("""
                    INSERT INTO today_claims (task_id, user_name, claimed_date)
                    VALUES (%s, %s, CURRENT_DATE)
                    ON CONFLICT (task_id, claimed_date) DO NOTHING
                    RETURNING id
                """, (task_id, u["name"]))
                row = cur.fetchone()
            conn.commit()
        if row:
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "error": "already_claimed"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/today/unclaim", methods=["POST"])
@require_auth
def api_today_unclaim():
    """Сотрудник снимает свою заявку."""
    u = _session_user()
    d = request.json or {}
    task_id = d.get("task_id")
    if not task_id:
        return jsonify({"error": "task_id required"}), 400
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM today_claims
                    WHERE task_id=%s AND claimed_date=CURRENT_DATE AND user_name=%s
                """, (task_id, u["name"]))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/today/remove", methods=["POST"])
@require_auth
def api_today_remove():
    """Убирает задачу из списка сегодня (не удаляет и не закрывает задачу)."""
    u = _session_user()
    d = request.json or {}
    claim_id = d.get("claim_id")
    if not claim_id:
        return jsonify({"error": "claim_id required"}), 400
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if u["role"] == "admin":
                    cur.execute("DELETE FROM today_claims WHERE id=%s", (claim_id,))
                else:
                    cur.execute(
                        "DELETE FROM today_claims WHERE id=%s AND user_name=%s AND claimed_date=CURRENT_DATE",
                        (claim_id, u["name"])
                    )
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/today/<int:task_id>/done", methods=["POST"])
@require_auth
def api_today_done(task_id):
    """Закрывает задачу и в today_claims, и в основном списке."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET done=TRUE, done_at=NOW() WHERE id=%s", (task_id,)
                )
                cur.execute(
                    "DELETE FROM today_claims WHERE task_id=%s AND claimed_date=CURRENT_DATE",
                    (task_id,)
                )
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Notifications API ────────────────────────────────────────────────────────

def _first_name(full_name: str) -> str:
    """Возвращает первое слово из полного имени."""
    return (full_name or "").strip().split()[0] if (full_name or "").strip() else (full_name or "")

def _resolve_owner_email_design(cur, task_id: int, stored_email: str, created_by_name: str) -> str:
    """
    Email создателя design-задачи.
    Fallback: ищет по first_name через users.name (created_by хранит первое имя).
    """
    if stored_email:
        return stored_email
    if created_by_name:
        cur.execute(
            "SELECT email FROM users WHERE name LIKE %s LIMIT 1",
            (created_by_name + "%",)
        )
        row = cur.fetchone()
        if row:
            return row["email"]
    return ""

def _resolve_owner_email(cur, task_id: int, stored_email: str) -> str:
    """
    Возвращает email создателя задачи.
    Если stored_email пуст (старые задачи) — ищет первого автора в task_history,
    затем находит его email по имени в таблице users.
    """
    if stored_email:
        return stored_email
    cur.execute(
        "SELECT changed_by FROM task_history WHERE task_id=%s ORDER BY id ASC LIMIT 1",
        (task_id,)
    )
    row = cur.fetchone()
    if not row:
        return ""
    cur.execute("SELECT email FROM users WHERE name=%s LIMIT 1", (row["changed_by"],))
    u = cur.fetchone()
    return u["email"] if u else ""

def _push_notification(cur, user_email: str, notif_type: str, title: str, message: str = "", task_id: int | None = None):
    """Создаёт запись уведомления в БД (вызывать внутри уже открытого cursor)."""
    if not user_email:
        return
    cur.execute(
        "INSERT INTO notifications (user_email, type, title, message, task_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        (user_email, notif_type, title, message, task_id),
    )


@app.route("/api/users")
@require_auth
def api_users():
    """Список активных пользователей: email + first_name для выпадающего списка исполнителей."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email, name FROM users WHERE role != 'pending' ORDER BY name"
                )
                rows = cur.fetchall()
        return jsonify([
            {"email": r["email"], "first_name": _first_name(r["name"])}
            for r in rows
        ])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/notifications")
@require_auth
def api_notifications():
    """Возвращает непрочитанные уведомления текущего пользователя."""
    u = _session_user()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, type, title, message, task_id, created_at "
                    "FROM notifications WHERE user_email=%s AND read=FALSE "
                    "ORDER BY created_at ASC",
                    (u["email"],),
                )
                rows = cur.fetchall()
        return jsonify({"notifications": [
            {
                "id":         r["id"],
                "type":       r["type"],
                "title":      r["title"],
                "message":    r["message"],
                "task_id":    r["task_id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/notifications/all")
@require_admin_api
def api_notifications_all():
    """Debug: все уведомления + состояние задач (только для admin)."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, user_email, type, title, message, task_id, read, created_at "
                    "FROM notifications ORDER BY created_at DESC LIMIT 50"
                )
                notifs = cur.fetchall()
                cur.execute(
                    "SELECT id, title, assignee, assignee_email, created_by_email "
                    "FROM design_tasks ORDER BY created_at DESC LIMIT 20"
                )
                tasks = cur.fetchall()
        return jsonify({
            "notifications": [dict(r) | {"created_at": r["created_at"].isoformat()} for r in notifs],
            "design_tasks_emails": [dict(r) for r in tasks],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/notifications/read", methods=["POST"])
@require_auth
def api_notifications_read():
    """Помечает все уведомления текущего пользователя как прочитанные."""
    u = _session_user()
    ids = (request.json or {}).get("ids") or []
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if ids:
                    cur.execute(
                        "UPDATE notifications SET read=TRUE WHERE user_email=%s AND id = ANY(%s)",
                        (u["email"], ids),
                    )
                else:
                    cur.execute(
                        "UPDATE notifications SET read=TRUE WHERE user_email=%s",
                        (u["email"],),
                    )
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/notifications/dismiss", methods=["POST"])
@require_auth
def api_notifications_dismiss():
    """Скрывает все уведомления из панели (dismissed=TRUE), из БД не удаляет."""
    u = _session_user()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notifications SET dismissed=TRUE WHERE user_email=%s",
                    (u["email"],)
                )
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/notifications/inbox")
@require_auth
def api_notifications_inbox():
    """Последние 30 уведомлений (прочитанные + непрочитанные) для bell-панели."""
    u = _session_user()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, type, title, message, task_id, read, created_at "
                    "FROM notifications WHERE user_email=%s AND dismissed=FALSE "
                    "ORDER BY created_at DESC LIMIT 30",
                    (u["email"],),
                )
                rows = cur.fetchall()
                unread = sum(1 for r in rows if not r["read"])
        return jsonify({"unread": unread, "notifications": [
            {
                "id":         r["id"],
                "type":       r["type"],
                "title":      r["title"],
                "message":    r["message"],
                "task_id":    r["task_id"],
                "read":       r["read"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Activity API ─────────────────────────────────────────────────────────────

@app.route("/api/activity/ping", methods=["POST"])
@require_auth
def api_activity_ping():
    """Пинг активности — вызывается JS при загрузке страницы и каждую минуту."""
    u = _session_user()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_activity (email, name, date, first_seen, last_seen, page_views)
                    VALUES (%s, %s, CURRENT_DATE, NOW(), NOW(), 1)
                    ON CONFLICT (email, date) DO UPDATE
                      SET last_seen  = NOW(),
                          page_views = user_activity.page_views + 1,
                          name       = EXCLUDED.name
                """, (u["email"], u.get("name", "")))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/activity")
@require_admin_api
def api_admin_activity():
    """Логи активности пользователей — последние 30 дней."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT email, name, date,
                           first_seen, last_seen,
                           EXTRACT(EPOCH FROM (last_seen - first_seen))::INT AS duration_sec,
                           page_views
                    FROM user_activity
                    WHERE date >= CURRENT_DATE - INTERVAL '30 days'
                    ORDER BY date DESC, last_seen DESC
                """)
                rows = cur.fetchall()
        return jsonify([{
            "email":        r["email"],
            "name":         r["name"],
            "date":         r["date"].isoformat(),
            "first_seen":   r["first_seen"].isoformat() if r["first_seen"] else None,
            "last_seen":    r["last_seen"].isoformat() if r["last_seen"] else None,
            "duration_sec": r["duration_sec"] or 0,
            "page_views":   r["page_views"],
        } for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/activity")
@require_admin
def admin_activity():
    return render_template("activity.html")

# ─── Task API ─────────────────────────────────────────────────────────────────
@app.route("/api/tasks")
@require_auth
def api_tasks():
    u = _session_user()
    try:
        if u["role"] in ("admin", "employee"):
            tasks = get_tasks()
        else:  # seller
            tasks = get_tasks(projects=u["projects"] or [])
        return jsonify([serialize(t) for t in tasks])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/tasks", methods=["POST"])
@require_auth
def api_add():
    u = _session_user()
    try:
        d = request.json or {}
        project = d.get("project")
        # Seller может добавлять только в свои проекты
        if u["role"] == "seller" and project and project not in (u["projects"] or []):
            return jsonify({"error": "forbidden"}), 403
        creator_email  = u.get("email", "")
        assignee_email = d.get("assignee_email", "")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO tasks (chat_id,title,priority,assignee,assignee_email,due,project,created_by_email) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (0, d["title"], d.get("priority", "med"),
                     d.get("assignee"), assignee_email,
                     d.get("due"), project, creator_email),
                )
                # Уведомление назначенному исполнителю
                if assignee_email and assignee_email != creator_email:
                    _push_notification(
                        cur, assignee_email, "task_assigned",
                        f"Новая задача: {d['title']}",
                        f"Назначена вам в проекте {project or '—'}",
                    )
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/tasks/<int:task_id>/done", methods=["POST"])
@require_auth
def api_done(task_id):
    try:
        u = _session_user()
        changed_by = (u.get("name") or u.get("email", "unknown")) if u else "unknown"
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Проверяем наличие комментария — обязательное условие закрытия
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM comments WHERE task_id=%s", (task_id,)
                )
                row = cur.fetchone()
                if not row or int(row["cnt"]) == 0:
                    return jsonify({"error": "Необходимо добавить комментарий или ссылку перед закрытием задачи"}), 400
                cur.execute(
                    "UPDATE tasks SET done=TRUE,done_at=NOW() WHERE id=%s RETURNING title,created_by_email",
                    (task_id,)
                )
                task_row = cur.fetchone()
                cur.execute(
                    "INSERT INTO task_history (task_id,changed_by,field,old_value,new_value) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (task_id, changed_by, "Статус", "В работе", "Готово")
                )
                # Уведомление создателю задачи
                if task_row:
                    owner_email = _resolve_owner_email(cur, task_id, task_row["created_by_email"] or "")
                    u_email = (u.get("email") or "") if u else ""
                    if owner_email and owner_email != u_email:
                        _push_notification(
                            cur, owner_email, "status_changed",
                            f"Задача закрыта: {task_row['title']}",
                            f"Отметил(а): {changed_by}",
                            task_id,
                        )
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
@require_auth
def api_update(task_id):
    try:
        u = _session_user()
        changed_by = (u.get("name") or u.get("email", "unknown")) if u else "unknown"
        d = request.json or {}
        field_labels = {
            "title":    "Название",
            "priority": "Приоритет",
            "assignee": "Исполнитель",
            "due":      "Срок",
            "project":  "Проект",
        }
        u_email            = u.get("email", "") if u else ""
        new_assignee_email = d.get("assignee_email", "")
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Читаем старые значения
                cur.execute(
                    "SELECT title,priority,assignee,assignee_email,due,project,created_by_email FROM tasks WHERE id=%s",
                    (task_id,)
                )
                row = cur.fetchone()
                if row:
                    old = {k: row[k] for k in ["title","priority","assignee","due","project"]}
                    new = {
                        "title":    d.get("title"),
                        "priority": d.get("priority", "med"),
                        "assignee": d.get("assignee"),
                        "due":      d.get("due"),
                        "project":  d.get("project"),
                    }
                    changed_fields = []
                    for field, label in field_labels.items():
                        if str(old.get(field) or "") != str(new.get(field) or ""):
                            cur.execute(
                                "INSERT INTO task_history "
                                "(task_id,changed_by,field,old_value,new_value) "
                                "VALUES (%s,%s,%s,%s,%s)",
                                (task_id, changed_by, label,
                                 old.get(field), new.get(field))
                            )
                            changed_fields.append(label)

                    notified = set()

                    # Уведомление новому исполнителю при смене
                    old_assignee_email = row.get("assignee_email") or ""
                    if new_assignee_email and new_assignee_email != old_assignee_email and new_assignee_email != u_email:
                        _push_notification(
                            cur, new_assignee_email, "task_assigned",
                            f"Задача назначена вам: {new.get('title') or old.get('title')}",
                            f"Назначил(а): {changed_by}",
                            task_id,
                        )
                        notified.add(new_assignee_email)

                    # Уведомление создателю при любом изменении (кроме смены исполнителя)
                    owner_email = _resolve_owner_email(cur, task_id, row.get("created_by_email") or "")
                    non_assignee_changes = [f for f in changed_fields if f != "Исполнитель"]
                    if non_assignee_changes and owner_email and owner_email != u_email and owner_email not in notified:
                        _push_notification(
                            cur, owner_email, "task_updated",
                            f"Задача изменена: {new.get('title') or old.get('title')}",
                            f"{changed_by} изменил(а): {', '.join(non_assignee_changes)}",
                            task_id,
                        )

                cur.execute(
                    "UPDATE tasks SET title=%s,priority=%s,assignee=%s,assignee_email=%s,due=%s,project=%s "
                    "WHERE id=%s",
                    (d.get("title"), d.get("priority", "med"),
                     d.get("assignee"), new_assignee_email,
                     d.get("due"), d.get("project"), task_id),
                )
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/tasks/<int:task_id>/history")
@require_auth
def api_task_history(task_id):
    u = _session_user()
    if not u or u["role"] != "admin":
        return jsonify({"error": "forbidden"}), 403
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id,changed_by,field,old_value,new_value,changed_at "
                    "FROM task_history WHERE task_id=%s ORDER BY changed_at DESC",
                    (task_id,)
                )
                rows = cur.fetchall()
                return jsonify([{
                    "id":         r["id"],
                    "changed_by": r["changed_by"],
                    "field":      r["field"],
                    "old_value":  r["old_value"],
                    "new_value":  r["new_value"],
                    "changed_at": r["changed_at"].isoformat() if r["changed_at"] else None,
                } for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
@require_auth
def api_delete(task_id):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tasks WHERE id=%s", (task_id,))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/tasks/<int:task_id>/comments")
@require_auth
def api_get_comments(task_id):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM comments WHERE task_id=%s ORDER BY created_at ASC",
                    (task_id,),
                )
                return jsonify([serialize_comment(c) for c in cur.fetchall()])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/tasks/<int:task_id>/comments", methods=["POST"])
@require_auth
def api_add_comment(task_id):
    try:
        u      = _session_user()
        u_email = u.get("email", "") if u else ""
        author  = _first_name(u.get("name", "")) or "Аноним"
        d = request.json or {}
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO comments (task_id, author, text) VALUES (%s,%s,%s)",
                    (task_id, author, d["text"]),
                )
                # Уведомляем создателя и исполнителя (кроме самого комментатора)
                cur.execute(
                    "SELECT title, created_by_email, assignee_email FROM tasks WHERE id=%s",
                    (task_id,)
                )
                task = cur.fetchone()
                if task:
                    notified = set()
                    for email in [task["created_by_email"], task["assignee_email"]]:
                        if email and email != u_email and email not in notified:
                            _push_notification(
                                cur, email, "comment_added",
                                f"Новый комментарий: {task['title']}",
                                f"{author}: {d['text'][:80]}",
                                task_id,
                            )
                            notified.add(email)
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── WB routes ────────────────────────────────────────────────────────────────
def _check_wb_access():
    """Проверяет авторизацию и роль для WB-отчётов. Возвращает None если OK."""
    u = _session_user()
    if not u:
        return redirect("/auth/login")
    if u["role"] in ("pending", "seller"):
        return Response("Доступ запрещён", status=403)
    return None

def _parse_period(args) -> tuple[date, date]:
    """Парсит ?from=YYYY-MM-DD&to=YYYY-MM-DD. По умолчанию — прошлая Пн–Вс."""
    today = date.today()
    monday = today - timedelta(days=today.weekday() + 7)
    default_from = monday
    default_to   = monday + timedelta(days=6)
    try:
        f = datetime.strptime(args.get("from", ""), "%Y-%m-%d").date()
    except ValueError:
        f = default_from
    try:
        t = datetime.strptime(args.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        t = default_to
    return f, t

@app.route("/wb")
def wb_index():
    err = _check_wb_access()
    if err:
        return err
    today = date.today()
    monday = today - timedelta(days=today.weekday() + 7)
    sunday = monday + timedelta(days=6)
    from wb_api import cabinet_env_slug
    from urllib.parse import quote
    cab_options, cab_cards = [], []
    for cab in WB_CABINETS:
        env_name = f"WB_API_KEY_{cabinet_env_slug(cab)}"
        configured = bool(os.environ.get(env_name) or os.environ.get("WB_API_KEY"))
        status = '<div class="status ok">✓ ключ настроен</div>' if configured else \
                 f'<div class="status">⚠ нет {env_name}</div>'
        cab_url = quote(cab)
        href = f"/wb/loading?cabinet={cab_url}&from={monday}&to={sunday}"
        link = f'<a href="{href}">Сформировать</a>' if configured else \
               '<a class="dis">недоступно</a>'
        cab_cards.append(f'<div class="cab"><h3>{cab}</h3>{status}{link}</div>')
        cab_options.append(
            f'<option value="{cab}" {"disabled" if not configured else ""}>{cab}</option>'
        )
    return render_template(
        "wb_index.html",
        cabinet_options="".join(cab_options),
        default_from=str(monday),
        default_to=str(sunday),
        cab_cards="".join(cab_cards),
    )

@app.route("/wb/loading")
def wb_loading():
    err = _check_wb_access()
    if err:
        return err
    cabinet = request.args.get("cabinet", "").strip()
    if not cabinet:
        return Response("?cabinet= обязателен", status=400)
    d_from, d_to = _parse_period(request.args)
    report_url = f"/wb/report?cabinet={cabinet}&from={d_from}&to={d_to}"
    months = ["января","февраля","марта","апреля","мая","июня",
              "июля","августа","сентября","октября","ноября","декабря"]
    if d_from.month == d_to.month:
        period = f"{d_from.day:02d} — {d_to.day:02d} {months[d_to.month-1]} {d_to.year}"
    else:
        period = (f"{d_from.day:02d} {months[d_from.month-1]} — "
                  f"{d_to.day:02d} {months[d_to.month-1]} {d_to.year}")
    return render_template(
        "wb_loading.html", cabinet=cabinet, period=period, report_url=report_url
    )

@app.route("/wb/report")
def wb_report():
    err = _check_wb_access()
    if err:
        return err
    cabinet = request.args.get("cabinet", "").strip()
    if not cabinet:
        return Response("?cabinet= обязателен", status=400)
    d_from, d_to = _parse_period(request.args)
    if (d_to - d_from).days < 0:
        return Response("Неверный период: to < from", status=400)
    if (d_to - d_from).days > 31:
        return Response("Период не более 31 дня (ограничение WB API)", status=400)
    try:
        from wb_api import collect_report_data
        from wb_report import render_html
        data = collect_report_data(cabinet, d_from, d_to)
        # Выполненные задачи проекта за период отчёта
        done_tasks = []
        try:
            import datetime as _dt2
            d_to_dt = _dt2.datetime.combine(d_to, _dt2.time.max)
            d_from_dt = _dt2.datetime.combine(d_from, _dt2.time.min)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT title, assignee, done_at FROM tasks "
                        "WHERE project = %s AND done = TRUE "
                        "  AND done_at >= %s AND done_at <= %s "
                        "ORDER BY done_at DESC",
                        (cabinet, d_from_dt, d_to_dt)
                    )
                    done_tasks = [
                        {"title": r["title"], "assignee": r["assignee"],
                         "done_at": r["done_at"].strftime("%d.%m") if r["done_at"] else ""}
                        for r in cur.fetchall()
                    ]
        except Exception:
            done_tasks = []
        html_out = render_html(data, done_tasks=done_tasks)
        return Response(html_out, mimetype="text/html")
    except RuntimeError as e:
        return Response(f"Ошибка: {e}", status=400)
    except Exception as e:
        logging.exception("wb_report failure")
        return Response(f"Внутренняя ошибка: {e}", status=500)

@app.route("/api/wb/report")
@require_auth
def api_wb_report():
    u = _session_user()
    if u["role"] in ("pending", "seller"):
        return jsonify({"error": "forbidden"}), 403
    cabinet = request.args.get("cabinet", "").strip()
    if not cabinet:
        return jsonify({"error": "cabinet required"}), 400
    d_from, d_to = _parse_period(request.args)
    try:
        from wb_api import collect_report_data
        data = collect_report_data(cabinet, d_from, d_to)
        data["date_from"] = data["date_from"].isoformat()
        data["date_to"]   = data["date_to"].isoformat()
        data["prev_from"] = data["prev_from"].isoformat()
        data["prev_to"]   = data["prev_to"].isoformat()
        data["campaign_spend"] = {str(k): v for k, v in data["campaign_spend"].items()}
        data["campaign_stats"] = {str(k): v for k, v in data["campaign_stats"].items()}
        return jsonify(data)
    except Exception as e:
        logging.exception("api_wb_report failure")
        return jsonify({"error": str(e)}), 500

@app.route("/api/wb/debug")
@require_auth
def api_wb_debug():
    u = _session_user()
    if u["role"] in ("pending", "seller"):
        return jsonify({"error": "forbidden"}), 403
    cabinet = request.args.get("cabinet", "").strip()
    if not cabinet:
        return jsonify({"error": "cabinet required"}), 400
    d_from, d_to = _parse_period(request.args)
    try:
        from wb_api import WBClient, previous_week, fmt_date
        prev_from, prev_to = previous_week(d_from, d_to)
        out: dict = {
            "cabinet":     cabinet,
            "date_from":   str(d_from),
            "date_to":     str(d_to),
            "prev_from":   str(prev_from),
            "prev_to":     str(prev_to),
            "api_key_env": f"WB_API_KEY_{cabinet.upper()}",
            "api_key_set": bool(os.environ.get(f"WB_API_KEY_{cabinet.upper()}") or
                                os.environ.get("WB_API_KEY")),
        }
        with WBClient(cabinet) as wb:
            r = wb._request("GET", "https://advert-api.wildberries.ru/adv/v1/upd",
                            params={"from": fmt_date(d_from), "to": fmt_date(d_to)})
            out["upd"] = {
                "status": r.status_code,
                "rows_count": (len(r.json()) if r.is_success and isinstance(r.json(), list) else None),
                "preview": r.text[:500],
            }
            r = wb._request("GET", "https://advert-api.wildberries.ru/adv/v1/promotion/count")
            out["promotion_count"] = {"status": r.status_code, "preview": r.text[:500]}
            try:
                ids = list({int(row.get("advertId"))
                            for row in (wb.get_upd(fmt_date(d_from), fmt_date(d_to)) or [])
                            if row.get("advertId")})[:5]
            except Exception:
                ids = []
            out["fullstats"] = {"advert_ids_used": ids}
            if ids:
                r = wb._request("GET", "https://advert-api.wildberries.ru/adv/v3/fullstats",
                                params={"ids": ",".join(map(str, ids)),
                                        "beginDate": fmt_date(d_from),
                                        "endDate":   fmt_date(d_to)})
                out["fullstats"].update({"status": r.status_code, "preview": r.text[:500]})
            out["sales_funnel"] = wb.raw_sales_funnel(
                fmt_date(d_from), fmt_date(d_to),
                fmt_date(prev_from), fmt_date(prev_to),
            )
            out["sales"] = wb.raw_sales(fmt_date(prev_from))
            funnel_json = out["sales_funnel"].get("response_json") or {}
            products = ((funnel_json.get("data") or {}).get("products")) or []
            out["sales_funnel"]["products_count"] = len(products)
            if products:
                out["sales_funnel"]["first_product_keys"] = sorted(products[0].keys())
                stats = products[0].get("statistics") or {}
                out["sales_funnel"]["statistics_keys"] = sorted(stats.keys())
                first_period_key = next(iter(stats), None)
                if first_period_key:
                    period = stats[first_period_key] or {}
                    out["sales_funnel"]["statistics_period_keys"] = (
                        sorted(period.keys()) if isinstance(period, dict) else type(period).__name__
                    )
                    out["sales_funnel"]["first_product_sample"] = products[0]
        return jsonify(out)
    except Exception as e:
        logging.exception("api_wb_debug failure")
        return jsonify({"error": str(e), "cabinet": cabinet}), 500

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ─── План-факт ────────────────────────────────────────────────────────────────

@app.route("/plan-fact")
def plan_fact_page():
    u = _session_user()
    if not u:
        return redirect("/auth/login")
    allowed = _planfact_allowed(u)
    if allowed is not None and not allowed:   # seller без доступа
        return Response("Доступ запрещён", status=403)
    cabinets = WB_CABINETS if allowed is None else [c for c in allowed if c in WB_CABINETS]
    if not cabinets:
        return Response("Нет доступных кабинетов", status=403)
    return render_template(
        "plan_fact.html",
        projects=cabinets,
        project_emoji=PROJECT_EMOJI,
        current_user=u,
        can_edit=_planfact_can_edit(u),
    )

@app.route("/api/debug/cabinets")
@require_admin_api
def api_debug_cabinets():
    """Показывает какие env-переменные используются для каждого кабинета (без самих ключей)."""
    from wb_api import cabinet_env_slug, get_cabinet_key
    result = []
    for cab in WB_CABINETS:
        slug    = cabinet_env_slug(cab)
        env_var = f"WB_API_KEY_{slug}"
        key_val = os.environ.get(env_var, "")
        result.append({
            "cabinet":    cab,
            "env_var":    env_var,
            "key_set":    bool(key_val),
            "key_start":  key_val[:20]  if key_val else "",
            "key_end":    key_val[-10:] if key_val else "",
            "key_len":    len(key_val),
        })
    # Помечаем дубликаты ключей
    keys = [r["key_start"] + r["key_end"] for r in result]
    for i, r in enumerate(result):
        r["duplicate_of"] = [result[j]["cabinet"] for j in range(len(result))
                             if j != i and keys[j] == keys[i] and keys[i]]
    return jsonify(result)

@app.route("/api/debug/stocks")
@require_admin_api
def api_debug_stocks():
    """Диагностика: сколько записей/артикулов возвращает WB stocks API для кабинета."""
    from wb_api import WBClient, aggregate_stocks_by_article
    cabinet = request.args.get("cabinet", "").strip()
    if not cabinet or cabinet not in WB_CABINETS:
        return jsonify({"error": "unknown cabinet"}), 400
    date_from = request.args.get("date_from", "2019-01-01")
    with WBClient(cabinet) as wb:
        raw = wb.get_stocks(date_from)
    by_vc, total, meta = aggregate_stocks_by_article(raw)
    articles_with_stock = {vc: qty for vc, qty in by_vc.items() if qty > 0}
    sample = sorted(articles_with_stock.items(), key=lambda x: -x[1])[:20]
    return jsonify({
        "cabinet":            cabinet,
        "date_from":          date_from,
        "raw_rows":           len(raw),
        "unique_articles":    len(by_vc),
        "articles_stock_gt0": len(articles_with_stock),
        "stock_total":        total,
        "top20":              [{"vc": vc, "qty": qty} for vc, qty in sample],
        "sample_raw_row":     raw[0] if raw else None,
    })

@app.route("/api/plan-fact")
@require_auth
def api_plan_fact_get():
    u = _session_user()
    allowed = _planfact_allowed(u)
    if allowed is not None and not allowed:
        return jsonify({"error": "forbidden"}), 403
    cabinet    = request.args.get("cabinet", "").strip()
    month_str  = request.args.get("month", "").strip()   # "2026-05"
    if not cabinet or not month_str:
        return jsonify({"error": "cabinet and month required"}), 400
    if allowed is not None and cabinet not in allowed:
        return jsonify({"error": "forbidden"}), 403
    import calendar
    try:
        month_date = datetime.strptime(month_str, "%Y-%m").date()
    except ValueError:
        return jsonify({"error": "invalid month format"}), 400
    last_day = calendar.monthrange(month_date.year, month_date.month)[1]
    d_from   = month_date
    d_to     = min(date.today() - timedelta(days=1), month_date.replace(day=last_day))
    if d_to < d_from:
        d_to = d_from
    try:
        from wb_api import collect_planfact_data
        data = collect_planfact_data(cabinet, d_from, d_to)
        # Загружаем план + ярлыки сезонности из БД
        plans: dict = {}
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT vendor_code, orders_rub_plan, orders_qty_plan, "
                    "       sales_rub_plan, sales_qty_plan "
                    "FROM sales_plans WHERE project=%s AND month=%s",
                    (cabinet, month_date)
                )
                for r in cur.fetchall():
                    plans[r["vendor_code"]] = {
                        "orders_rub_plan": float(r["orders_rub_plan"] or 0),
                        "orders_qty_plan": int(r["orders_qty_plan"] or 0),
                        "sales_rub_plan":  float(r["sales_rub_plan"] or 0),
                        "sales_qty_plan":  int(r["sales_qty_plan"] or 0),
                    }
                # Постоянные ярлыки сезонности (не зависят от месяца)
                article_seasons: dict = {}
                cur.execute(
                    "SELECT vendor_code, season_name FROM article_seasons "
                    "WHERE project=%s",
                    (cabinet,)
                )
                for r in cur.fetchall():
                    article_seasons[r["vendor_code"]] = r["season_name"] or ""
                # Все известные сезоны проекта (для дропдауна)
                cur.execute(
                    "SELECT DISTINCT season_name FROM article_seasons "
                    "WHERE project=%s AND season_name != '' ORDER BY season_name",
                    (cabinet,)
                )
                all_season_names = [r["season_name"] for r in cur.fetchall()]
                # Коэффициенты сезонности (за текущий месяц)
                season_coefficients: dict = {}
                cur.execute(
                    "SELECT season_name, coefficient FROM season_coefficients "
                    "WHERE project=%s AND month=%s",
                    (cabinet, month_date)
                )
                for r in cur.fetchall():
                    season_coefficients[r["season_name"]] = float(r["coefficient"] or 0)
                # ФФ-остатки и ожидаемые
                ff_stocks: dict = {}
                cur.execute(
                    "SELECT vendor_code, ff_stock, expected_stock FROM article_ff_stocks "
                    "WHERE project=%s",
                    (cabinet,)
                )
                for r in cur.fetchall():
                    ff_stocks[r["vendor_code"]] = {
                        "ff_stock":       int(r["ff_stock"] or 0),
                        "expected_stock": int(r["expected_stock"] or 0),
                    }
        today        = date.today()
        days_elapsed = max(1, (d_to - d_from).days + 1)
        days_remaining = max(0, last_day - today.day)
        for a in data["articles"]:
            vc   = a["vendor_code"]
            plan = plans.get(vc, {})
            a.update({
                "orders_rub_plan": plan.get("orders_rub_plan", 0),
                "orders_qty_plan": plan.get("orders_qty_plan", 0),
                "sales_rub_plan":  plan.get("sales_rub_plan",  0),
                "sales_qty_plan":  plan.get("sales_qty_plan",  0),
                # Ярлык сезонности — из постоянной таблицы, не из плана
                "season_name":     article_seasons.get(vc, ""),
                "ff_stock":        ff_stocks.get(vc, {}).get("ff_stock", 0),
                "expected_stock":  ff_stocks.get(vc, {}).get("expected_stock", 0),
            })
            orq  = a["orders_qty_fact"]
            orqp = a["orders_qty_plan"]
            a["orders_per_day"]  = round(orq / days_elapsed, 1)
            a["needed_per_day"]  = round(max(0, (orqp - orq) / days_remaining), 1) if days_remaining > 0 else 0
            a["pct_orders"]      = round(a["orders_rub_fact"] / a["orders_rub_plan"] * 100) if a["orders_rub_plan"] else None
            a["pct_sales"]       = round(a["sales_rub_fact"]  / a["sales_rub_plan"]  * 100) if a["sales_rub_plan"]  else None
        t = data["totals"]
        t.update({
            "orders_rub_plan": sum(a["orders_rub_plan"] for a in data["articles"]),
            "orders_qty_plan": sum(a["orders_qty_plan"] for a in data["articles"]),
            "sales_rub_plan":  sum(a["sales_rub_plan"]  for a in data["articles"]),
            "sales_qty_plan":  sum(a["sales_qty_plan"]  for a in data["articles"]),
        })
        data["meta"] = {
            "d_from":          d_from.isoformat(),
            "d_to":            d_to.isoformat(),
            "days_elapsed":    days_elapsed,
            "days_remaining":  days_remaining,
            "days_in_month":   last_day,
            "pct_orders":      round(t["orders_rub_fact"] / t["orders_rub_plan"] * 100, 1) if t.get("orders_rub_plan") else None,
            "pct_sales":       round(t["sales_rub_fact"]  / t["sales_rub_plan"]  * 100, 1) if t.get("sales_rub_plan")  else None,
            "adv_spend":       t.get("adv_spend", 0),
            "drr_orders":      t.get("drr_orders"),
            "drr_sales":       t.get("drr_sales"),
        }
        data["season_coefficients"] = season_coefficients
        data["season_names"]        = all_season_names
        data["_debug"] = {
            "articles_count": len(data["articles"]),
            "d_from": d_from.isoformat(),
            "d_to":   d_to.isoformat(),
            "cabinet": cabinet,
        }
        return jsonify(data)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.exception("api_plan_fact_get failure")
        return jsonify({"error": str(e)}), 500

@app.route("/api/plan-fact", methods=["POST"])
@require_auth
def api_plan_fact_save():
    u = _session_user()
    if not _planfact_can_edit(u):
        return jsonify({"error": "forbidden"}), 403
    allowed = _planfact_allowed(u)
    if allowed is not None and not allowed:
        return jsonify({"error": "forbidden"}), 403
    d       = request.json or {}
    cabinet = d.get("cabinet", "").strip()
    if allowed is not None and cabinet not in allowed:
        return jsonify({"error": "forbidden"}), 403
    month_str = d.get("month", "").strip()
    plans   = d.get("plans", [])
    try:
        month_date = datetime.strptime(month_str, "%Y-%m").date()
    except ValueError:
        return jsonify({"error": "invalid month"}), 400
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for p in plans:
                    vc = str(p.get("vendor_code") or "").strip()
                    if not vc:
                        continue
                    cur.execute("""
                        INSERT INTO sales_plans
                            (project, month, vendor_code,
                             orders_rub_plan, orders_qty_plan,
                             sales_rub_plan,  sales_qty_plan)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (project, month, vendor_code) DO UPDATE SET
                            orders_rub_plan = EXCLUDED.orders_rub_plan,
                            orders_qty_plan = EXCLUDED.orders_qty_plan,
                            sales_rub_plan  = EXCLUDED.sales_rub_plan,
                            sales_qty_plan  = EXCLUDED.sales_qty_plan
                    """, (
                        cabinet, month_date, vc,
                        float(p.get("orders_rub_plan") or 0),
                        int(p.get("orders_qty_plan") or 0),
                        float(p.get("sales_rub_plan") or 0),
                        int(p.get("sales_qty_plan") or 0),
                    ))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Коэффициенты сезонности ──────────────────────────────────────────────────

@app.route("/api/season-coefficients", methods=["POST"])
@require_auth
def api_season_coeff_save():
    u = _session_user()
    if not _planfact_can_edit(u):
        return jsonify({"error": "forbidden"}), 403
    d        = request.json or {}
    cabinet  = d.get("cabinet", "").strip()
    month_str = d.get("month", "").strip()
    coefficients = d.get("coefficients", {})
    if not cabinet or not month_str:
        return jsonify({"error": "cabinet and month required"}), 400
    try:
        month_date = datetime.strptime(month_str, "%Y-%m").date()
    except ValueError:
        return jsonify({"error": "invalid month"}), 400
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for season_name, coeff in coefficients.items():
                    sn = str(season_name).strip()
                    if not sn:
                        continue
                    cur.execute("""
                        INSERT INTO season_coefficients
                            (project, month, season_name, coefficient)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (project, month, season_name) DO UPDATE SET
                            coefficient = EXCLUDED.coefficient
                    """, (cabinet, month_date, sn, float(coeff or 0)))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Ярлыки сезонности (постоянные, по артикулу) ─────────────────────────────

@app.route("/api/ff-stocks/upload", methods=["POST"])
@require_auth
def api_ff_stocks_upload():
    """Загрузка Excel с колонками: Артикул, Остаток на ФФ, Ожидаем остатки."""
    u = _session_user()
    if u["role"] not in ("admin", "employee"):
        return jsonify({"error": "forbidden"}), 403
    cabinet = request.args.get("cabinet", "").strip()
    if not cabinet or cabinet not in WB_CABINETS:
        return jsonify({"error": "unknown cabinet"}), 400
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    try:
        import openpyxl, io
        wb = openpyxl.load_workbook(io.BytesIO(f.read()), data_only=True)
        ws = wb.active
        # Определяем индексы нужных колонок по заголовку (первая строка)
        headers = [str(c.value or "").strip().lower() for c in ws[1]]
        def find_col(variants):
            for v in variants:
                for i, h in enumerate(headers):
                    if v in h:
                        return i
            return None
        idx_art  = find_col(["артикул"])
        idx_ff   = find_col(["итого остатков", "итого", "фф", "ff", "на складе", "склад"])
        idx_exp  = find_col(["ожидаем", "заказ", "приход", "ожид"])
        if idx_art is None:
            return jsonify({"error": "Колонка 'Артикул' не найдена"}), 400

        rows_upserted = 0
        with get_conn() as conn:
            with conn.cursor() as cur:
                for row in ws.iter_rows(min_row=2, values_only=True):
                    vc = str(row[idx_art] or "").strip()
                    if not vc:
                        continue
                    def _to_int(val):
                        try:
                            return int(float(val)) if val not in (None, "", "#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?") else 0
                        except (ValueError, TypeError):
                            return 0
                    ff  = _to_int(row[idx_ff]  if idx_ff  is not None else None)
                    exp = _to_int(row[idx_exp] if idx_exp is not None else None)
                    cur.execute("""
                        INSERT INTO article_ff_stocks (project, vendor_code, ff_stock, expected_stock, updated_at)
                        VALUES (%s, %s, %s, %s, NOW())
                        ON CONFLICT (project, vendor_code) DO UPDATE
                          SET ff_stock=EXCLUDED.ff_stock,
                              expected_stock=EXCLUDED.expected_stock,
                              updated_at=NOW()
                    """, (cabinet, vc, ff, exp))
                    rows_upserted += 1
            conn.commit()
        return jsonify({"ok": True, "rows": rows_upserted})
    except Exception as e:
        logging.exception("ff_stocks_upload error")
        return jsonify({"error": str(e)}), 500

@app.route("/api/article-seasons", methods=["POST"])
@require_auth
def api_article_seasons_save():
    u = _session_user()
    if not _planfact_can_edit(u):
        return jsonify({"error": "forbidden"}), 403
    d           = request.json or {}
    cabinet     = d.get("cabinet", "").strip()
    vendor_code = d.get("vendor_code", "").strip()
    season_name = d.get("season_name", "").strip()
    if not cabinet or not vendor_code:
        return jsonify({"error": "cabinet and vendor_code required"}), 400
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO article_seasons (project, vendor_code, season_name, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (project, vendor_code) DO UPDATE SET
                        season_name = EXCLUDED.season_name,
                        updated_at  = NOW()
                """, (cabinet, vendor_code, season_name))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Дизайнерские задачи ──────────────────────────────────────────────────────

def _design_auth(u):
    """True если пользователь имеет доступ к разделу дизайна."""
    if not u:
        return False
    if u["role"] in ("admin", "employee"):
        return True
    return u["role"] == "seller" and bool(u.get("design_access"))

def serialize_design_task(t):
    d = dict(t)
    for key in ("created_at", "done_at"):
        if d.get(key):
            d[key] = d[key].isoformat()
    return d

def serialize_design_attachment(a):
    d = dict(a)
    if d.get("created_at"):
        d["created_at"] = d["created_at"].isoformat()
    d.pop("file_data", None)   # не отдавать бинарь в JSON
    return d

@app.route("/api/design-tasks")
@require_auth
def api_design_tasks_list():
    u = _session_user()
    allowed = _design_projects(u)
    if allowed is not None and not allowed:
        return jsonify({"error": "forbidden"}), 403
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if allowed is None:
                    # admin/employee — все задачи
                    cur.execute("""
                        SELECT dt.*,
                               COUNT(da.id) FILTER (WHERE da.attach_role='brief')  AS brief_count,
                               COUNT(da.id) FILTER (WHERE da.attach_role='result') AS result_count,
                               COUNT(dc.id) AS comment_count
                        FROM design_tasks dt
                        LEFT JOIN design_attachments da ON da.task_id = dt.id
                        LEFT JOIN design_comments dc ON dc.task_id = dt.id
                        GROUP BY dt.id
                        ORDER BY dt.done ASC, dt.created_at DESC
                    """)
                else:
                    # seller — только свои проекты
                    cur.execute("""
                        SELECT dt.*,
                               COUNT(da.id) FILTER (WHERE da.attach_role='brief')  AS brief_count,
                               COUNT(da.id) FILTER (WHERE da.attach_role='result') AS result_count,
                               COUNT(dc.id) AS comment_count
                        FROM design_tasks dt
                        LEFT JOIN design_attachments da ON da.task_id = dt.id
                        LEFT JOIN design_comments dc ON dc.task_id = dt.id
                        WHERE dt.project = ANY(%s)
                        GROUP BY dt.id
                        ORDER BY dt.done ASC, dt.created_at DESC
                    """, (allowed,))
                return jsonify([serialize_design_task(dict(r)) for r in cur.fetchall()])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-tasks", methods=["POST"])
@require_auth
def api_design_tasks_create():
    u = _session_user()
    allowed = _design_projects(u)
    if allowed is not None and not allowed:
        return jsonify({"error": "forbidden"}), 403
    d = request.json or {}
    title = (d.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    project = d.get("project") or None
    # Для селлера проект должен быть из его списка
    if allowed is not None and project and project not in allowed:
        return jsonify({"error": "forbidden"}), 403
    try:
        creator_email  = u.get("email", "")
        assignee_email = d.get("assignee_email", "")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO design_tasks "
                    "(title, description, priority, assignee, assignee_email, due, project, created_by, created_by_email) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (title, d.get("description", ""), d.get("priority", "med"),
                     d.get("assignee") or None, assignee_email,
                     d.get("due") or None, project,
                     _first_name(u.get("name", "")), creator_email)
                )
                new_id = cur.fetchone()["id"]
                if assignee_email and assignee_email != creator_email:
                    _push_notification(
                        cur, assignee_email, "task_assigned",
                        f"Новая задача дизайнеру: {title}",
                        f"Назначена вам в проекте {project or '—'}",
                        new_id,
                    )
            conn.commit()
        return jsonify({"ok": True, "id": new_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-tasks/<int:task_id>", methods=["PUT"])
@require_auth
def api_design_tasks_update(task_id):
    u = _session_user()
    if not _design_auth(u):
        return jsonify({"error": "forbidden"}), 403
    d = request.json or {}
    project = d.get("project") or None
    allowed = _design_projects(u)
    if allowed is not None and project and project not in allowed:
        return jsonify({"error": "forbidden"}), 403
    try:
        u_email            = u.get("email", "") if u else ""
        changed_by         = _first_name(u.get("name", "")) if u else "unknown"
        new_assignee_email = d.get("assignee_email", "")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT title, assignee_email, created_by, created_by_email FROM design_tasks WHERE id=%s",
                    (task_id,)
                )
                row = cur.fetchone()
                new_title    = d.get("title","").strip() or None
                new_priority = d.get("priority","med")
                new_due      = d.get("due") or None
                cur.execute(
                    "UPDATE design_tasks SET title=%s, priority=%s, assignee=%s, assignee_email=%s, due=%s, project=%s "
                    "WHERE id=%s",
                    (new_title, new_priority, d.get("assignee") or None,
                     new_assignee_email, new_due, project, task_id)
                )
                if row:
                    # Пишем историю изменений
                    hist_fields = [
                        ("Название",    row.get("title"),          new_title),
                        ("Исполнитель", row.get("assignee_email"), new_assignee_email),
                    ]
                    for fname, old_v, new_v in hist_fields:
                        if str(old_v or "") != str(new_v or ""):
                            cur.execute(
                                "INSERT INTO design_task_history (task_id, changed_by, field, old_value, new_value) "
                                "VALUES (%s,%s,%s,%s,%s)",
                                (task_id, changed_by, fname, old_v, new_v)
                            )
                if row:
                    notified = set()
                    old_assignee_email = row.get("assignee_email") or ""
                    owner_email        = _resolve_owner_email_design(cur, task_id, row.get("created_by_email") or "", row.get("created_by") or "")
                    task_title         = d.get("title") or row.get("title") or ""
                    # Новый исполнитель
                    if new_assignee_email and new_assignee_email != old_assignee_email and new_assignee_email != u_email:
                        _push_notification(cur, new_assignee_email, "task_assigned",
                            f"Задача дизайнеру назначена вам: {task_title}",
                            f"Назначил(а): {changed_by}", task_id)
                        notified.add(new_assignee_email)
                    # Создателю при любом изменении
                    if owner_email and owner_email != u_email and owner_email not in notified:
                        _push_notification(cur, owner_email, "task_updated",
                            f"Задача дизайнеру изменена: {task_title}",
                            f"Изменил(а): {changed_by}", task_id)
                    logging.info(f"[notif] design update task={task_id} owner={owner_email} u={u_email}")
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-tasks/<int:task_id>/done", methods=["POST"])
@require_auth
def api_design_tasks_done(task_id):
    u = _session_user()
    if not _design_auth(u):
        return jsonify({"error": "forbidden"}), 403
    try:
        d          = request.json or {}
        comment    = (d.get("comment") or "").strip()
        url        = (d.get("url") or "").strip()
        u_email    = u.get("email", "") if u else ""
        changed_by = _first_name(u.get("name", "")) if u else "unknown"
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE design_tasks SET done=TRUE, done_at=NOW() WHERE id=%s RETURNING title, created_by, created_by_email",
                    (task_id,)
                )
                row = cur.fetchone()
                if row:
                    # Сохраняем комментарий закрытия
                    text = comment
                    if url:
                        text = (text + "\n\nСсылка: " + url) if text else "Ссылка: " + url
                    if text:
                        cur.execute(
                            "INSERT INTO design_comments (task_id, author, text) VALUES (%s,%s,%s)",
                            (task_id, changed_by, text)
                        )
                    owner_email = _resolve_owner_email_design(cur, task_id, row.get("created_by_email") or "", row.get("created_by") or "")
                    if owner_email and owner_email != u_email:
                        _push_notification(cur, owner_email, "status_changed",
                            f"Задача дизайнеру закрыта: {row['title']}",
                            f"Закрыл(а): {changed_by}", task_id)
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-tasks/<int:task_id>/undone", methods=["POST"])
@require_auth
def api_design_tasks_undone(task_id):
    u = _session_user()
    if not _design_auth(u):
        return jsonify({"error": "forbidden"}), 403
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE design_tasks SET done=FALSE, done_at=NULL WHERE id=%s", (task_id,)
                )
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-tasks/<int:task_id>", methods=["DELETE"])
@require_auth
def api_design_tasks_delete(task_id):
    u = _session_user()
    if not _design_auth(u):
        return jsonify({"error": "forbidden"}), 403
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM design_tasks WHERE id=%s", (task_id,))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-tasks/<int:task_id>/comments")
@require_auth
def api_design_get_comments(task_id):
    if not _design_auth(_session_user()):
        return jsonify({"error": "forbidden"}), 403
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, task_id, author, text, created_at FROM design_comments "
                    "WHERE task_id=%s ORDER BY created_at ASC", (task_id,)
                )
                return jsonify([serialize_comment(dict(r)) for r in cur.fetchall()])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-tasks/<int:task_id>/comments", methods=["POST"])
@require_auth
def api_design_add_comment(task_id):
    u = _session_user()
    if not _design_auth(u):
        return jsonify({"error": "forbidden"}), 403
    try:
        u_email    = u.get("email", "") if u else ""
        author     = _first_name(u.get("name", "")) or "Аноним"
        d          = request.json or {}
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO design_comments (task_id, author, text) VALUES (%s,%s,%s)",
                    (task_id, author, d.get("text",""))
                )
                cur.execute(
                    "SELECT title, created_by_email, assignee_email FROM design_tasks WHERE id=%s",
                    (task_id,)
                )
                task = cur.fetchone()
                if task:
                    notified = set()
                    for email in [task["created_by_email"], task["assignee_email"]]:
                        if email and email != u_email and email not in notified:
                            _push_notification(cur, email, "comment_added",
                                f"Новый комментарий: {task['title']}",
                                f"{author}: {d.get('text','')[:80]}", task_id)
                            notified.add(email)
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-tasks/<int:task_id>/history")
@require_auth
def api_design_task_history(task_id):
    u = _session_user()
    if not u or u["role"] != "admin":
        return jsonify({"error": "forbidden"}), 403
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, changed_by, field, old_value, new_value, changed_at "
                    "FROM design_task_history WHERE task_id=%s ORDER BY changed_at DESC",
                    (task_id,)
                )
                return jsonify([{
                    "id":         r["id"],
                    "changed_by": r["changed_by"],
                    "field":      r["field"],
                    "old_value":  r["old_value"],
                    "new_value":  r["new_value"],
                    "changed_at": r["changed_at"].isoformat() if r["changed_at"] else None,
                } for r in cur.fetchall()])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-today")
@require_auth
def api_design_today():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT dtc.id, dtc.task_id, dtc.user_name,
                           dt.title, dt.project, dt.priority, dt.done
                    FROM design_today_claims dtc
                    JOIN design_tasks dt ON dt.id = dtc.task_id
                    WHERE dtc.claimed_date = CURRENT_DATE
                    ORDER BY dtc.user_name, dtc.id
                """)
                return jsonify([dict(r) for r in cur.fetchall()])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-today/claim", methods=["POST"])
@require_auth
def api_design_today_claim():
    u = _session_user()
    if u["role"] not in ("admin", "employee"):
        return jsonify({"error": "forbidden"}), 403
    d = request.json or {}
    task_id = d.get("task_id")
    if not task_id:
        return jsonify({"error": "task_id required"}), 400
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM design_tasks WHERE id=%s AND done=FALSE", (task_id,))
                if not cur.fetchone():
                    return jsonify({"error": "task not found or already done"}), 404
                cur.execute("""
                    INSERT INTO design_today_claims (task_id, user_name, claimed_date)
                    VALUES (%s, %s, CURRENT_DATE)
                    ON CONFLICT (task_id, claimed_date) DO NOTHING
                    RETURNING id
                """, (task_id, u["name"]))
                row = cur.fetchone()
            conn.commit()
        return jsonify({"ok": True}) if row else jsonify({"ok": False, "error": "already_claimed"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-today/unclaim", methods=["POST"])
@require_auth
def api_design_today_unclaim():
    u = _session_user()
    d = request.json or {}
    task_id = d.get("task_id")
    if not task_id:
        return jsonify({"error": "task_id required"}), 400
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM design_today_claims WHERE task_id=%s AND claimed_date=CURRENT_DATE AND user_name=%s",
                    (task_id, u["name"])
                )
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-tasks/<int:task_id>/attachments")
@require_auth
def api_design_attachments_list(task_id):
    u = _session_user()
    if not _design_auth(u):
        return jsonify({"error": "forbidden"}), 403
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, task_id, attach_role, attach_type, name, url, mime_type, created_by, created_at "
                    "FROM design_attachments WHERE task_id=%s ORDER BY created_at ASC",
                    (task_id,)
                )
                return jsonify([serialize_design_attachment(dict(r)) for r in cur.fetchall()])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-tasks/<int:task_id>/attachments", methods=["POST"])
@require_auth
def api_design_attachments_add(task_id):
    u = _session_user()
    if not _design_auth(u):
        return jsonify({"error": "forbidden"}), 403
    try:
        ct = request.content_type or ""
        if "multipart" in ct:
            # Загрузка файла
            f = request.files.get("file")
            if not f or not f.filename:
                return jsonify({"error": "no file"}), 400
            attach_role = request.form.get("role", "brief")
            name = f.filename
            mime_type = f.content_type or "application/octet-stream"
            file_data = f.read()
            if len(file_data) > 20 * 1024 * 1024:
                return jsonify({"error": "file too large (max 20MB)"}), 400
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO design_attachments "
                        "(task_id, attach_role, attach_type, name, mime_type, file_data, created_by) "
                        "VALUES (%s,%s,'file',%s,%s,%s,%s)",
                        (task_id, attach_role, name, mime_type, file_data, u.get("name", ""))
                    )
                conn.commit()
            return jsonify({"ok": True})
        else:
            # Ссылка
            d = request.json or {}
            url = (d.get("url") or "").strip()
            name = (d.get("name") or url).strip()
            attach_role = d.get("role", "brief")
            if not url:
                return jsonify({"error": "url required"}), 400
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO design_attachments "
                        "(task_id, attach_role, attach_type, name, url, created_by) "
                        "VALUES (%s,%s,'link',%s,%s,%s)",
                        (task_id, attach_role, name, url, u.get("name", ""))
                    )
                conn.commit()
            return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/design-attachments/<int:att_id>/download")
@require_auth
def api_design_attachment_download(att_id):
    u = _session_user()
    if not _design_auth(u):
        return Response("Доступ запрещён", status=403)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, mime_type, file_data FROM design_attachments "
                    "WHERE id=%s AND attach_type='file'",
                    (att_id,)
                )
                row = cur.fetchone()
        if not row or not row["file_data"]:
            return Response("Файл не найден", status=404)
        name      = row["name"]
        mime_type = row["mime_type"]
        file_data = row["file_data"]
        raw = bytes(file_data)
        from urllib.parse import quote
        encoded_name = quote(name)
        return Response(
            raw,
            mimetype=mime_type or "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
                "Content-Length": str(len(raw)),
            }
        )
    except Exception as e:
        return Response(f"Ошибка: {e}", status=500)

@app.route("/api/design-attachments/<int:att_id>", methods=["DELETE"])
@require_auth
def api_design_attachment_delete(att_id):
    u = _session_user()
    if not _design_auth(u):
        return jsonify({"error": "forbidden"}), 403
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM design_attachments WHERE id=%s", (att_id,))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Юнит-экономика ───────────────────────────────────────────────────────────
@app.route("/unit")
@require_auth
def unit_page():
    u = _session_user()
    return render_template(
        "unit.html",
        current_user=u,
        projects=WB_CABINETS,
        project_emoji=PROJECT_EMOJI,
    )


def _unit_row_params(d):
    return {
        "project":          d.get("project", ""),
        "brand":            d.get("brand", ""),
        "wb_article":       d.get("wb_article") or None,
        "seller_article":   d.get("seller_article", ""),
        "cost_price":       d.get("cost_price") or 0,
        "logistics_to_wb":  d.get("logistics_to_wb") or 0,
        "packaging":        d.get("packaging") or 0,
        "overhead":         d.get("overhead") or 0,
        "defect_pct":       d.get("defect_pct") or 0,
        "width_cm":         d.get("width_cm") or None,
        "length_cm":        d.get("length_cm") or None,
        "height_cm":        d.get("height_cm") or None,
        "liters":           d.get("liters") or None,
        "redemption_pct":   d.get("redemption_pct") or 100,
        "warehouse":        d.get("warehouse", ""),
        "irp":              d.get("irp") or 1.0,
        "logistics_ktr":    d.get("logistics_ktr") or None,
        "reception_coef":   d.get("reception_coef", "x0"),
        "storage_per_day":  0,
        "commission_pct":   d.get("commission_pct") or 36,
        "wb_price":         d.get("wb_price") or 0,
        "drr_pct":          d.get("drr_pct") or 0,
        "drr_external_rub": d.get("drr_external_rub") or 0,
        "stock":            d.get("stock") or 0,
        "spp_pct":          d.get("spp_pct") or 0,
        "tax_system":       d.get("tax_system") or "УСН 7%",
        "tax_pct":          d.get("tax_pct") or 7,
    }


@app.route("/api/unit-rows")
@require_auth
def api_unit_rows_get():
    project = request.args.get("project", "")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, project, brand, wb_article, seller_article,
                           cost_price, logistics_to_wb, packaging, overhead,
                           defect_pct, width_cm, length_cm, height_cm, liters,
                           redemption_pct, warehouse, irp, logistics_ktr,
                           reception_coef, storage_per_day, commission_pct,
                           wb_price, drr_pct, drr_external_rub, stock, spp_pct,
                           tax_system, tax_pct
                    FROM unit_economics
                    WHERE project = %s
                    ORDER BY id ASC
                """, (project,))
                rows = [dict(r) for r in cur.fetchall()]  # dict_row → copy
        for row in rows:
            for k, v in row.items():
                if hasattr(v, '__float__') and not isinstance(v, (int, bool)):
                    row[k] = float(v) if v is not None else None
        # Ежедневный бэкап (ON CONFLICT DO NOTHING = не дублируем)
        if project and rows:
            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO unit_economics_backup (backup_date, project, data)
                            SELECT CURRENT_DATE, %s, jsonb_agg(row_to_json(ue))
                            FROM unit_economics ue WHERE project = %s
                            ON CONFLICT (backup_date, project) DO NOTHING
                        """, (project, project))
                    conn.commit()
            except Exception:
                pass
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/unit-rows", methods=["POST"])
@require_auth
def api_unit_rows_create():
    p = _unit_row_params(request.get_json(silent=True) or {})
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO unit_economics
                        (project, brand, wb_article, seller_article,
                         cost_price, logistics_to_wb, packaging, overhead,
                         defect_pct, width_cm, length_cm, height_cm, liters,
                         redemption_pct, warehouse, irp, logistics_ktr,
                         reception_coef, storage_per_day, commission_pct,
                         wb_price, drr_pct, drr_external_rub, stock, spp_pct,
                         tax_system, tax_pct)
                    VALUES
                        (%(project)s, %(brand)s, %(wb_article)s, %(seller_article)s,
                         %(cost_price)s, %(logistics_to_wb)s, %(packaging)s, %(overhead)s,
                         %(defect_pct)s, %(width_cm)s, %(length_cm)s, %(height_cm)s, %(liters)s,
                         %(redemption_pct)s, %(warehouse)s, %(irp)s, %(logistics_ktr)s,
                         %(reception_coef)s, %(storage_per_day)s, %(commission_pct)s,
                         %(wb_price)s, %(drr_pct)s, %(drr_external_rub)s, %(stock)s, %(spp_pct)s,
                         %(tax_system)s, %(tax_pct)s)
                    RETURNING id
                """, p)
                new_id = cur.fetchone()[0]
            conn.commit()
        return jsonify({"id": new_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/unit-rows/<int:row_id>", methods=["PUT"])
@require_auth
def api_unit_rows_update(row_id):
    p = _unit_row_params(request.get_json(silent=True) or {})
    p["id"] = row_id
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE unit_economics SET
                        project=%(project)s, brand=%(brand)s,
                        wb_article=%(wb_article)s, seller_article=%(seller_article)s,
                        cost_price=%(cost_price)s, logistics_to_wb=%(logistics_to_wb)s,
                        packaging=%(packaging)s, overhead=%(overhead)s,
                        defect_pct=%(defect_pct)s, width_cm=%(width_cm)s,
                        length_cm=%(length_cm)s, height_cm=%(height_cm)s, liters=%(liters)s,
                        redemption_pct=%(redemption_pct)s, warehouse=%(warehouse)s,
                        irp=%(irp)s, logistics_ktr=%(logistics_ktr)s,
                        reception_coef=%(reception_coef)s, storage_per_day=%(storage_per_day)s,
                        commission_pct=%(commission_pct)s, wb_price=%(wb_price)s,
                        drr_pct=%(drr_pct)s, drr_external_rub=%(drr_external_rub)s,
                        stock=%(stock)s, spp_pct=%(spp_pct)s,
                        tax_system=%(tax_system)s, tax_pct=%(tax_pct)s, updated_at=NOW()
                    WHERE id=%(id)s
                """, p)
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/unit-rows/<int:row_id>", methods=["DELETE"])
@require_auth
def api_unit_rows_delete(row_id):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM unit_economics WHERE id=%s", (row_id,))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/unit-import-articles")
@require_auth
def api_unit_import_articles():
    """Загружает список артикулов из воронки WB за последние 30 дней."""
    project = request.args.get("project", "")
    if not project:
        return jsonify({"error": "project required"}), 400
    try:
        from datetime import date, timedelta
        from wb_api import WBClient, fmt_date
        today    = date.today()
        d_from   = fmt_date(today - timedelta(days=30))
        d_to     = fmt_date(today)
        p_from   = fmt_date(today - timedelta(days=60))
        p_to     = fmt_date(today - timedelta(days=31))
        with WBClient(project) as wb:
            raw = wb.raw_sales_funnel(d_from, d_to, past_from=p_from, past_to=p_to)
        status = raw.get("status")
        if status != 200:
            msg = raw.get("response_text_preview") or f"HTTP {status}"
            logging.error(f"unit-import-articles {project}: WB вернул {status}: {msg[:200]}")
            return jsonify({"error": f"WB API вернул {status}: {msg[:150]}"}), 502
        rj = raw.get("response_json") or {}
        products = (rj.get("data") or {}).get("products") or []
        result, seen = [], set()
        for p in products:
            prod  = p.get("product") or {}
            nm_id = prod.get("nmId")
            if not nm_id or nm_id in seen:
                continue
            seen.add(nm_id)
            result.append({
                "wb_article":     nm_id,
                "seller_article": prod.get("vendorCode", ""),
                "name":           prod.get("name", ""),
                "brand":          prod.get("brandName", ""),
            })
        return jsonify(result)
    except Exception as e:
        logging.error(f"unit-import-articles {project}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/unit-export-excel")
@require_auth
def api_unit_export_excel():
    """Выгрузка таблицы юнит-экономики в Excel."""
    project = request.args.get("project", "")
    if not project:
        return jsonify({"error": "project required"}), 400
    try:
        import io
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT wb_article, seller_article, brand,
                           cost_price, packaging, logistics_to_wb, overhead, defect_pct,
                           liters, redemption_pct, warehouse, irp, logistics_ktr,
                           commission_pct, wb_price, spp_pct,
                           drr_pct, drr_external_rub, stock
                    FROM unit_economics WHERE project=%s ORDER BY id ASC
                """, (project,))
                rows = [dict(r) for r in cur.fetchall()]

        wb = Workbook()
        ws = wb.active
        ws.title = project

        headers = [
            "WB артикул", "Арт. продавца", "Бренд",
            "Себест.₽", "Упак.₽", "Лог.ВБ₽", "Накл.₽", "Брак%",
            "Литраж", "%выкупа", "Склад", "ИРП", "Лог+КТР",
            "Комис.%", "Цена ВБ₽", "СПП%",
            "ДРР%", "ДРР₽", "Остаток"
        ]
        keys = [
            "wb_article", "seller_article", "brand",
            "cost_price", "packaging", "logistics_to_wb", "overhead", "defect_pct",
            "liters", "redemption_pct", "warehouse", "irp", "logistics_ktr",
            "commission_pct", "wb_price", "spp_pct",
            "drr_pct", "drr_external_rub", "stock"
        ]

        hdr_font = Font(bold=True, color="FFFFFF")
        hdr_fill = PatternFill("solid", fgColor="1F3A5F")
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = Alignment(horizontal="center")

        for ri, row in enumerate(rows, 2):
            for ci, key in enumerate(keys, 1):
                v = row.get(key)
                ws.cell(row=ri, column=ci, value=float(v) if hasattr(v, '__float__') and not isinstance(v, (int, bool)) else v)

        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 14

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        fname = f"unit_{project}.xlsx"
        return Response(
            buf.read(),
            headers={
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "Content-Disposition": f'attachment; filename="{fname}"',
            }
        )
    except Exception as e:
        logging.error(f"unit-export-excel {project}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/unit-refresh", methods=["POST"])
@require_auth
def api_unit_refresh():
    """Обновляет Остаток и %выкупа из WB API для всех артикулов кабинета."""
    project = request.args.get("project", "")
    if not project:
        return jsonify({"error": "project required"}), 400
    try:
        from wb_api import WBClient, fmt_date, aggregate_stocks_by_article
        today  = date.today()
        d_from = fmt_date(today - timedelta(days=30))
        d_to   = fmt_date(today)
        p_from = fmt_date(today - timedelta(days=60))
        p_to   = fmt_date(today - timedelta(days=31))

        # Получаем текущие артикулы из БД
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, wb_article FROM unit_economics WHERE project=%s AND wb_article IS NOT NULL",
                    (project,)
                )
                db_rows = {r["wb_article"]: r["id"] for r in cur.fetchall()}

        if not db_rows:
            return jsonify({"updated": 0})

        with WBClient(project) as wb:
            # Остатки — по nmId
            stocks_raw = wb.get_stocks(fmt_date(today - timedelta(days=180)))
            # Воронка — %выкупа
            funnel = wb.get_sales_funnel(d_from, d_to, past_from=p_from, past_to=p_to, limit=1000)

        # Агрегируем остатки по nmId
        stock_by_nm = {}
        for s in stocks_raw:
            nm = s.get("nmId")
            if nm:
                stock_by_nm[nm] = stock_by_nm.get(nm, 0) + (s.get("quantityFull") or 0)

        # %выкупа по nmId из воронки
        from wb_api import _funnel_metric, get_product_field
        redemption_by_nm = {}
        for p in (funnel.get("data") or {}).get("products") or []:
            nm = get_product_field(p, "nmId")
            if not nm:
                continue
            orders  = _funnel_metric(p, "orderCount",  "selected")
            buyouts = _funnel_metric(p, "buyoutCount", "selected")
            if orders > 0:
                redemption_by_nm[nm] = round(buyouts / orders * 100, 1)

        # Обновляем в БД
        updated = 0
        with get_conn() as conn:
            with conn.cursor() as cur:
                for nm_id, row_id in db_rows.items():
                    stock   = stock_by_nm.get(nm_id)
                    red_pct = redemption_by_nm.get(nm_id)
                    if stock is None and red_pct is None:
                        continue
                    sets, vals = [], []
                    if stock is not None:
                        sets.append("stock=%s"); vals.append(stock)
                    if red_pct is not None:
                        sets.append("redemption_pct=%s"); vals.append(red_pct)
                    sets.append("updated_at=NOW()")
                    vals.append(row_id)
                    cur.execute(f"UPDATE unit_economics SET {', '.join(sets)} WHERE id=%s", vals)
                    updated += 1
            conn.commit()

        return jsonify({"updated": updated, "stock_found": len(stock_by_nm), "redemption_found": len(redemption_by_nm)})
    except Exception as e:
        logging.error(f"unit-refresh {project}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/unit-auto-import", methods=["POST"])
@require_auth
def api_unit_auto_import():
    """Первичный авто-импорт: добавляет артикулы из WB только если их нет в БД.
    Удаляет «пустые» строки (wb_article IS NULL). Возвращает все строки после вставки."""
    project = request.args.get("project", "")
    if not project:
        return jsonify({"error": "project required"}), 400
    try:
        from wb_api import WBClient, fmt_date
        today  = date.today()
        d_from = fmt_date(today - timedelta(days=30))
        d_to   = fmt_date(today)
        p_from = fmt_date(today - timedelta(days=60))
        p_to   = fmt_date(today - timedelta(days=31))

        # Сначала чистим мусорные строки (независимо от результата WB)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM unit_economics WHERE project=%s AND (wb_article IS NULL OR wb_article=0)",
                    (project,)
                )
            conn.commit()

        with WBClient(project) as wb:
            raw = wb.raw_sales_funnel(d_from, d_to, past_from=p_from, past_to=p_to)

        status = raw.get("status")
        if status != 200:
            msg = raw.get("response_text_preview") or f"HTTP {status}"
            logging.error(f"unit-auto-import {project}: WB вернул {status}: {msg[:200]}")
            return jsonify({"error": f"WB API вернул {status}: {msg[:150]}"}), 502

        products = ((raw.get("response_json") or {}).get("data") or {}).get("products") or []
        if not products:
            return jsonify({"error": "WB API вернул пустой список артикулов — попробуйте «Загрузить из WB»"}), 502

        # Собираем уникальные артикулы из WB
        wb_articles = {}
        for p in products:
            prod  = p.get("product") or {}
            nm_id = prod.get("nmId")
            if nm_id and nm_id not in wb_articles:
                wb_articles[nm_id] = {
                    "wb_article":     nm_id,
                    "seller_article": prod.get("vendorCode", ""),
                    "brand":          prod.get("brandName", "") or project,
                }

        with get_conn() as conn:
            with conn.cursor() as cur:
                # Узнаём какие wb_article уже есть
                cur.execute(
                    "SELECT wb_article FROM unit_economics WHERE project=%s AND wb_article IS NOT NULL",
                    (project,)
                )
                existing = {r[0] for r in cur.fetchall()}

                # Вставляем только новые
                to_insert = [a for nm, a in wb_articles.items() if nm not in existing]
                for a in to_insert:
                    cur.execute("""
                        INSERT INTO unit_economics
                            (project, brand, wb_article, seller_article,
                             commission_pct, redemption_pct, irp, stock)
                        VALUES (%s,%s,%s,%s, 36, 100, 1.0, 0)
                    """, (project, a["brand"], a["wb_article"], a["seller_article"]))

                # Читаем все строки
                cur.execute("""
                    SELECT id, project, brand, wb_article, seller_article,
                           cost_price, logistics_to_wb, packaging, overhead,
                           defect_pct, width_cm, length_cm, height_cm, liters,
                           redemption_pct, warehouse, irp, logistics_ktr,
                           reception_coef, storage_per_day, commission_pct,
                           wb_price, drr_pct, drr_external_rub, stock
                    FROM unit_economics WHERE project=%s ORDER BY id ASC
                """, (project,))
                rows = [dict(r) for r in cur.fetchall()]  # dict_row → copy
            conn.commit()

        for row in rows:
            for k, v in row.items():
                if hasattr(v, '__float__') and not isinstance(v, (int, bool)):
                    row[k] = float(v) if v is not None else None

        return jsonify({"inserted": len(to_insert), "rows": rows})
    except Exception as e:
        logging.error(f"unit-auto-import {project}: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
