"""
wb_report.py — Генерация HTML еженедельного отчёта WB по образцу.

Использует данные из wb_api.collect_report_data().
"""
from __future__ import annotations

import html
from datetime import date
from typing import Iterable

from wb_api import ADVERT_TYPE


# ──────────────────────────────────────────────────────────────────────────────
# Форматирование чисел/дат
# ──────────────────────────────────────────────────────────────────────────────

MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def fmt_int(v: float | int | None) -> str:
    if v is None:
        return "—"
    return f"{int(round(v)):,}".replace(",", " ")


def fmt_money(v: float | int | None, suffix: str = " ₽") -> str:
    if v is None:
        return "—"
    return f"{int(round(v)):,}".replace(",", " ") + suffix


def fmt_pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}%"


def fmt_period(d_from: date, d_to: date) -> str:
    if d_from.month == d_to.month and d_from.year == d_to.year:
        return f"{d_from.day:02d} — {d_to.day:02d} {MONTHS[d_to.month-1]} {d_to.year}"
    return (
        f"{d_from.day:02d} {MONTHS[d_from.month-1]} — "
        f"{d_to.day:02d} {MONTHS[d_to.month-1]} {d_to.year}"
    )


def delta_pct(cur: float, prev: float) -> float | None:
    if not prev:
        return None
    return (cur - prev) / prev * 100


def delta_html(cur: float, prev: float, prev_label: str, unit: str = "") -> str:
    """KPI-дельта vs прошлый период."""
    if prev is None or prev == 0:
        return '<div class="kpi-delta neutral">— нет данных пред. периода</div>'
    pct = (cur - prev) / prev * 100
    arrow = "▲" if pct > 0 else "▼"
    cls = "up" if pct > 0 else ("down" if pct < 0 else "neutral")
    return (
        f'<div class="kpi-delta {cls}">{arrow} '
        f'{abs(pct):.0f}% &nbsp; пред: {prev_label}</div>'
    )


def drr_class(drr: float) -> str:
    if drr <= 0:
        return "drr-na"
    if drr < 5:
        return "drr-good"
    if drr < 10:
        return "drr-ok"
    return "drr-bad"


def ctr_class(ctr: float) -> str:
    """CTR в одежде: <3% плохо, 3–4.5% средне, ≥4.5% отлично."""
    if ctr <= 0:
        return "drr-na"
    if ctr < 3:
        return "drr-bad"
    if ctr < 4.5:
        return "drr-ok"
    return "drr-good"


def short_money(v: float) -> str:
    """4 429 769 ₽ -> 4 429 тыс."""
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f} млн".replace(".", ",")
    if v >= 1_000:
        return f"{v/1_000:.0f} тыс".replace(".", ",")
    return fmt_int(v)


def short_num(v: float) -> str:
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M".replace(".", ",")
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return fmt_int(v)


# ──────────────────────────────────────────────────────────────────────────────
# HTML
# ──────────────────────────────────────────────────────────────────────────────

CSS = r"""
:root {
  --bg: #0e0f14; --surface: #161820; --surface2: #1e2030; --border: #2a2d3e;
  --accent: #7c6af7; --accent2: #4fd1c5; --warn: #f6ad55; --danger: #fc5c65;
  --success: #48bb78; --text: #e8eaf6; --text-muted: #7b7f9e; --text-dim: #4a4e6b;
  --transition: background .25s, color .25s, border-color .25s;
}
:root.light {
  --bg: #f3f5f9; --surface: #ffffff; --surface2: #f1f3f9; --border: #dde1ea;
  --accent: #6450e6; --accent2: #2eb8ac; --warn: #c97a16; --danger: #d83a48;
  --success: #2f9c5d; --text: #1a1d2e; --text-muted: #5a6072; --text-dim: #9aa1b3;
}
/* Светлая тема: точечно перекрашиваем элементы, у которых был светло-фиолетовый
   акцентный цвет — на белом фоне они становились нечитаемыми. */
:root.light .sku-code     { color: #4a3fb5; }
:root.light .brand-badge  { color: #4a3fb5; background: rgba(124,106,247,0.08); border-color: rgba(124,106,247,0.25); }
:root.light .camp-ark     { color: #4a3fb5; background: rgba(124,106,247,0.10); }
:root.light .camp-search  { color: #1a8d83; background: rgba(46,184,172,0.12); }
:root.light .num-info     { color: #4a3fb5; background: rgba(124,106,247,0.15); }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: 'Inter', sans-serif; font-size: 14px; line-height: 1.6; min-height: 100vh; transition: var(--transition); }
body::before {
  content: ''; position: fixed; inset: 0;
  background-image:
    radial-gradient(circle at 20% 20%, rgba(124,106,247,0.06) 0%, transparent 50%),
    radial-gradient(circle at 80% 80%, rgba(79,209,197,0.04) 0%, transparent 50%);
  pointer-events: none; z-index: 0;
}
:root.light body::before { opacity: .35; }

/* Floating toolbar (PDF / HTML / theme) */
.toolbar-fixed { position: fixed; top: 18px; right: 18px; z-index: 100; display: flex; gap: 8px; }
.tb-btn {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text-muted); height: 38px; padding: 0 14px;
  border-radius: 10px; cursor: pointer; font-size: 13px; font-weight: 500;
  display: inline-flex; align-items: center; gap: 6px;
  transition: var(--transition), transform .15s;
  box-shadow: 0 2px 12px rgba(0,0,0,.15); font-family: inherit;
}
.tb-btn:hover { border-color: var(--accent); color: var(--accent); transform: translateY(-1px); }
.tb-btn.icon-only { width: 38px; padding: 0; justify-content: center; font-size: 16px; }
.tb-btn.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.tb-btn.primary:hover { color: #fff; opacity: .9; }
.page { position: relative; z-index: 1; max-width: 1100px; margin: 0 auto; padding: 48px 32px; }

/* Header */
.report-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 48px; padding-bottom: 32px; border-bottom: 1px solid var(--border); }
.brand-badge { display: inline-flex; align-items: center; gap: 8px; background: rgba(124,106,247,0.12); border: 1px solid rgba(124,106,247,0.3); border-radius: 6px; padding: 4px 12px; font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: #a89df8; margin-bottom: 16px; }
.report-title { font-family: 'Unbounded', sans-serif; font-size: 28px; font-weight: 900; color: var(--text); line-height: 1.2; max-width: 600px; }
.report-title span { color: var(--accent); }
.report-meta { text-align: right; }
.report-meta .period { font-family: 'Unbounded', sans-serif; font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
.report-meta .date { font-size: 12px; color: var(--text-dim); }

/* KPI */
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 40px; }
.kpi-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; position: relative; overflow: hidden; }
.kpi-card::after { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: linear-gradient(90deg, var(--accent), var(--accent2)); opacity: 0.6; }
.kpi-label { font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 10px; }
.kpi-value { font-family: 'Unbounded', sans-serif; font-size: 22px; font-weight: 700; color: var(--text); margin-bottom: 6px; }
.kpi-delta { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 4px; }
.kpi-delta.up { color: var(--success); background: rgba(72,187,120,0.1); }
.kpi-delta.down { color: var(--danger); background: rgba(252,92,101,0.1); }
.kpi-delta.neutral { color: var(--text-muted); background: rgba(123,127,158,0.1); }

/* Section */
.section-title { font-family: 'Unbounded', sans-serif; font-size: 14px; font-weight: 700; color: var(--text); letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 16px; display: flex; align-items: center; gap: 10px; }
.section-title::after { content: ''; flex: 1; height: 1px; background: var(--border); }

/* Tables */
.table-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow-x: auto; overflow-y: hidden; margin-bottom: 40px; -webkit-overflow-scrolling: touch; }
table { width: 100%; border-collapse: collapse; }
thead th { background: var(--surface2); padding: 12px 16px; text-align: left; font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); border-bottom: 1px solid var(--border); white-space: nowrap; }
thead th.right { text-align: right; }
tbody tr { border-bottom: 1px solid var(--border); }
tbody tr:last-child { border-bottom: none; }
tbody td { padding: 11px 16px; color: var(--text); vertical-align: middle; }
tbody td.right { text-align: right; }
tbody td.muted { color: var(--text-muted); }
.sku-code { font-weight: 600; font-family: 'Unbounded', sans-serif; font-size: 11px; color: #c0beff; }
.sku-name { font-size: 12px; color: var(--text-muted); display: block; margin-top: 2px; max-width: 240px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sku-link { display: inline-block; text-decoration: none; color: inherit; transition: opacity .15s, color .15s; position: relative; padding-right: 14px; }
.sku-link:hover { opacity: .85; }
.sku-link:hover .sku-code { color: var(--accent); }
.sku-link::after { content: '↗'; position: absolute; top: 0; right: 0; font-size: 10px; color: var(--text-dim); opacity: 0; transition: opacity .15s; }
.sku-link:hover::after { opacity: 1; color: var(--accent); }
.drr-pill { display: inline-block; padding: 3px 9px; border-radius: 5px; font-weight: 600; font-size: 12px; }
.drr-good { background: rgba(72,187,120,0.12); color: var(--success); }
.drr-ok   { background: rgba(246,173,85,0.12); color: var(--warn); }
.drr-bad  { background: rgba(252,92,101,0.12); color: var(--danger); }
.drr-na   { background: transparent; color: var(--text-dim); }
.change-pos { color: var(--success); font-size: 11px; font-weight: 600; }
.change-neg { color: var(--danger); font-size: 11px; font-weight: 600; }
.change-new { color: var(--accent2); font-size: 11px; font-weight: 600; }
.buyout-bar { display: flex; align-items: center; gap: 8px; }
.bar-track { width: 60px; height: 5px; background: var(--border); border-radius: 3px; overflow: hidden; flex-shrink: 0; }
.bar-fill { height: 100%; border-radius: 3px; background: linear-gradient(90deg, var(--accent), var(--accent2)); }
.total-row { background: var(--surface2) !important; border-top: 2px solid var(--border) !important; }
.total-row td { font-weight: 700; color: var(--text) !important; }
.camp-tag { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 500; }
.camp-ark    { background: rgba(124,106,247,0.12); color: #a89df8; }
.camp-search { background: rgba(79,209,197,0.12); color: var(--accent2); }
.camp-auto   { background: rgba(246,173,85,0.12); color: var(--warn); }

/* Funnel */
.funnel-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 2px; background: var(--border); border-radius: 10px; overflow: hidden; margin-bottom: 40px; }
.funnel-step { background: var(--surface); padding: 20px 16px; text-align: center; }
.funnel-step-label { font-size: 10px; font-weight: 600; letter-spacing: 0.07em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 10px; }
.funnel-step-value { font-family: 'Unbounded', sans-serif; font-size: 18px; font-weight: 700; color: var(--text); margin-bottom: 4px; }
.funnel-conv { font-size: 11px; color: var(--accent2); font-weight: 600; }
.funnel-prev { font-size: 11px; color: var(--text-dim); margin-top: 4px; }

/* Note */
.note-block { background: rgba(246,173,85,0.06); border: 1px solid rgba(246,173,85,0.2); border-radius: 8px; padding: 12px 16px; font-size: 12px; color: var(--text-muted); margin-bottom: 40px; }
.note-block strong { color: var(--warn); }

/* Done tasks */
.done-tasks-section { margin-top: 32px; }
.done-tasks-list { display: flex; flex-direction: column; gap: 8px; }
.dt-row { display: flex; align-items: baseline; gap: 10px; padding: 10px 14px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }
.dt-date { flex-shrink: 0; font-size: 12px; color: var(--text-muted); min-width: 36px; }
.dt-title { flex: 1; color: var(--text); }
.dt-who { flex-shrink: 0; font-size: 12px; color: var(--text-muted); background: rgba(124,106,247,0.15); padding: 2px 8px; border-radius: 20px; }

/* Insights */
.insights-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 40px; }
.insight-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
.insight-card h3 { font-family: 'Unbounded', sans-serif; font-size: 12px; font-weight: 700; color: var(--text); margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
.insight-item { display: flex; gap: 10px; margin-bottom: 12px; font-size: 13px; line-height: 1.5; }
.insight-item:last-child { margin-bottom: 0; }
.insight-num { width: 22px; height: 22px; flex-shrink: 0; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; margin-top: 1px; }
.num-urgent { background: rgba(252,92,101,0.2); color: var(--danger); }
.num-ok     { background: rgba(72,187,120,0.2); color: var(--success); }
.num-info   { background: rgba(124,106,247,0.2); color: #a89df8; }
.num-warn   { background: rgba(246,173,85,0.2); color: var(--warn); }
.insight-text { color: var(--text-muted); }
.insight-text strong { color: var(--text); }

/* Print modal */
.print-modal-bg { position: fixed; inset: 0; background: rgba(0,0,0,.55); z-index: 200; display: none; align-items: center; justify-content: center; padding: 20px; }
.print-modal-bg.open { display: flex; }
.print-modal { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 14px; padding: 24px; max-width: 460px; width: 100%; box-shadow: 0 24px 60px rgba(0,0,0,.4); font-family: 'Inter', sans-serif; }
.print-modal h3 { font-family: 'Unbounded', sans-serif; font-size: 15px; margin-bottom: 14px; color: var(--text); }
.print-modal p { font-size: 13px; line-height: 1.55; color: var(--text-muted); margin-bottom: 12px; }
.print-modal ol { padding-left: 20px; font-size: 13px; color: var(--text-muted); margin: 10px 0 18px; }
.print-modal ol li { margin-bottom: 8px; }
.print-modal ol b { color: var(--text); }
.print-modal .actions { display: flex; gap: 10px; justify-content: flex-end; }
.print-modal button { padding: 9px 16px; font-size: 13px; border-radius: 8px; cursor: pointer; font-family: inherit; }
.print-modal .cancel { background: transparent; border: 1px solid var(--border); color: var(--text-muted); }
.print-modal .ok { background: var(--accent); color: #fff; border: none; font-weight: 500; }
.print-modal .ok:hover { opacity: .9; }
.print-modal .hint { font-size: 11px; color: var(--text-dim); margin-top: 8px; }

/* PDF / печать: чистая светлая вёрстка, без декора, без переносов внутри карточек */
@media print {
  @page { size: A4; margin: 14mm 12mm; }
  :root {
    --bg: #ffffff; --surface: #ffffff; --surface2: #f4f5f9; --border: #d3d7e2;
    --text: #111418; --text-muted: #4a4f60; --text-dim: #8a90a0;
  }
  body { background: #ffffff !important; color: #111418 !important; font-size: 11pt; }
  body::before { display: none !important; }
  .toolbar-fixed { display: none !important; }
  .page { padding: 0 !important; max-width: none !important; }
  .kpi-card, .insight-card, .table-wrap, .funnel-row, .note-block, .report-header {
    page-break-inside: avoid; break-inside: avoid;
  }
  .insights-grid { page-break-inside: auto; }
  thead { display: table-header-group; }   /* повтор заголовков таблиц на новой странице */
  tr { page-break-inside: avoid; }
  a { color: inherit; text-decoration: none; }
}
"""


# ──────────────────────────────────────────────────────────────────────────────
# Сборка HTML
# ──────────────────────────────────────────────────────────────────────────────

def render_html(data: dict, insights: dict | None = None, done_tasks: list | None = None) -> str:
    """
    Рендерит полный HTML отчёт.
    data — результат wb_api.collect_report_data()
    insights — опционально, {"leaders":[...], "problems":[...], "urgent":[...], "next_week":[...]}
    done_tasks — опционально, [{title, assignee, done_at}, ...] — задачи за период
    """
    cabinet  = data["cabinet"]
    d_from   = data["date_from"]
    d_to     = data["date_to"]
    prev_from = data["prev_from"]
    prev_to   = data["prev_to"]
    kpi      = data["kpi"]
    funnel   = data["funnel"]

    period_label  = fmt_period(d_from, d_to)
    prev_label    = fmt_period(prev_from, prev_to)
    generated_at  = d_to.strftime("%d.%m.%Y")
    # имя файла для скачивания HTML: wb_KLIFE_2026-05-09_2026-05-15.html
    filename_html = f"wb_{cabinet}_{d_from}_{d_to}.html"
    # Источник выкупов (для подсказки в шапке)
    buyouts_source = data.get("buyouts_source", "statistics_api")
    buyouts_note = (
        '<div class="date" style="margin-top:6px;color:var(--warn);font-size:11px">'
        '⚠ выкупы посчитаны из воронки (в Statistics API нет продаж за период)</div>'
        if buyouts_source == "funnel_fallback" else ""
    )

    kpi_html     = _render_kpi(kpi)
    note_html    = _render_note(kpi)
    funnel_html  = _render_funnel(funnel)
    stock_html   = _render_stock_block(kpi)
    products_html = _render_products_table(data)
    campaigns_html = _render_campaigns_table(data)
    insights_html = _render_insights(insights or _default_insights(data))
    done_tasks_html = _render_done_tasks(done_tasks or [])

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Еженедельный отчёт по рекламе — {html.escape(cabinet)} | {period_label}</title>
<link href="https://fonts.googleapis.com/css2?family=Unbounded:wght@400;700;900&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<div class="toolbar-fixed">
  <button class="tb-btn primary" onclick="downloadPDF()" title="Сохранить отчёт как PDF (Ctrl/Cmd + P)">📄 PDF</button>
  <button class="tb-btn" onclick="downloadHTML()" title="Скачать HTML-файл на компьютер">💾 HTML</button>
  <button class="tb-btn icon-only" id="theme-btn" onclick="toggleTheme()" title="Сменить тему">🌙</button>
</div>

<!-- Модалка с инструкцией перед печатью в PDF: убрать URL/дату из колонтитулов -->
<div class="print-modal-bg" id="print-modal" onclick="if(event.target===this)closePrintModal()">
  <div class="print-modal">
    <h3>📄 Сохранение в PDF — важно!</h3>
    <p>В открывшемся окне печати <b>обязательно отключите колонтитулы</b>, иначе на каждой странице будет напечатан URL дашборда и дата.</p>
    <ol>
      <li>В диалоге печати раскройте <b>«Ещё настройки»</b> (или «More settings»)</li>
      <li>Снимите галочку <b>«Колонтитулы»</b> (или «Headers and footers»)</li>
      <li>В поле «Назначение» выберите <b>«Сохранить как PDF»</b> и нажмите «Сохранить»</li>
    </ol>
    <div class="actions">
      <button class="cancel" onclick="closePrintModal()">Отмена</button>
      <button class="ok" onclick="confirmPrint()">Открыть диалог печати</button>
    </div>
    <div class="hint">Эту подсказку можно отключить, нажав «Запомнить выбор»</div>
  </div>
</div>
<script>
function applyTheme(t){{
  document.documentElement.classList.toggle('light', t==='light');
  var b = document.getElementById('theme-btn');
  if (b) b.textContent = t==='light' ? '🌙' : '☀️';
}}
function toggleTheme(){{
  var n = document.documentElement.classList.contains('light') ? 'dark' : 'light';
  try {{ localStorage.setItem('theme', n); }} catch(e){{}}
  applyTheme(n);
}}
try {{ applyTheme(localStorage.getItem('theme')||'dark'); }} catch(e){{ applyTheme('dark'); }}

function downloadPDF(){{
  // Сначала показываем напоминалку: в Chrome/Safari/Firefox по умолчанию
  // печатается URL страницы и дата в колонтитулах. Нужно их вручную снять.
  try {{
    if (localStorage.getItem('skip-pdf-hint') === '1') {{ window.print(); return; }}
  }} catch(e){{}}
  document.getElementById('print-modal').classList.add('open');
}}
function closePrintModal(){{
  document.getElementById('print-modal').classList.remove('open');
}}
function confirmPrint(){{
  closePrintModal();
  // Микро-пауза чтобы модалка успела закрыться визуально перед открытием print
  setTimeout(function(){{ window.print(); }}, 50);
}}
function downloadHTML(){{
  // Сохраняем HTML текущей страницы как файл на компьютер.
  // Включаем сам DOCTYPE и тег <html> — файл откроется автономно в любом браузере.
  var html = '<!DOCTYPE html>\\n' + document.documentElement.outerHTML;
  var blob = new Blob([html], {{type: 'text/html;charset=utf-8'}});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '{filename_html}';
  document.body.appendChild(a); a.click();
  setTimeout(function(){{ URL.revokeObjectURL(a.href); a.remove(); }}, 100);
}}
// Ctrl/Cmd + S → скачать HTML
document.addEventListener('keydown', function(e){{
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {{ e.preventDefault(); downloadHTML(); }}
}});
</script>
<div class="page">

  <div class="report-header">
    <div>
      <div class="brand-badge">🛍 Wildberries · {html.escape(cabinet)}</div>
      <h1 class="report-title">Еженедельный<br>отчёт по <span>рекламе</span></h1>
    </div>
    <div class="report-meta">
      <div class="period">{period_label}</div>
      <div class="date">Отчёт сформирован: {generated_at}</div>
      <div class="date" style="margin-top:6px; color: var(--text-muted)">Сравнение: {prev_label}</div>
      {buyouts_note}
    </div>
  </div>

  <div class="section-title">Общая сводка по кабинету</div>
  {kpi_html}

  {note_html}

  <div class="section-title">Воронка продаж</div>
  {funnel_html}
  {stock_html}

  <div class="section-title">ДРР и эффективность по артикулам — текущая неделя</div>
  {products_html}

  <div class="section-title">Активные рекламные кампании ({fmt_period(d_from, d_to)})</div>
  {campaigns_html}

  <div class="section-title">Выводы и рекомендации</div>
  {insights_html}

  {done_tasks_html}

</div>
</body>
</html>"""


# ── KPI блок ──────────────────────────────────────────────────────────────────
def _render_kpi(kpi: dict) -> str:
    def card(label: str, value: str, delta_html_str: str) -> str:
        return f"""
        <div class="kpi-card">
          <div class="kpi-label">{label}</div>
          <div class="kpi-value">{value}</div>
          {delta_html_str}
        </div>
        """

    cards = [
        card("Расход на рекламу", fmt_money(kpi["spend"]),
             '<div class="kpi-delta neutral">— нет данных пред. периода</div>'),

        card("Выручка заказов", fmt_money(kpi["revenue"]),
             delta_html(kpi["revenue"], kpi["revenue_prev"],
                        f"{short_money(kpi['revenue_prev'])}.")),

        card("Заказы", f'{fmt_int(kpi["orders"])} шт',
             delta_html(kpi["orders"], kpi["orders_prev"],
                        f"{fmt_int(kpi['orders_prev'])} шт")),

        card("ДРР по заказам",
             fmt_pct(kpi["drr"]),
             ('<div class="kpi-delta up">✦ Отличный показатель</div>' if kpi["drr"] < 5 and kpi["drr"] > 0 else
              '<div class="kpi-delta neutral">средний</div>' if kpi["drr"] < 10 else
              '<div class="kpi-delta down">высокий</div>') +
             (f'<div class="kpi-delta neutral" style="margin-top:4px">ДРР продаж: {fmt_pct(kpi["drr_sales"])}</div>'
              if kpi.get("drr_sales") else "")),

        card(
            "CPO (cтоимость заказа)",
            f'{fmt_int(kpi["spend"] / kpi["orders"])} ₽' if kpi.get("orders") else "—",
            '<div class="kpi-delta neutral">расход / заказы</div>',
        ),

        card("Переходы в карточку", fmt_int(kpi["visits"]),
             delta_html(kpi["visits"], kpi["views_prev"],
                        f"{fmt_int(kpi['views_prev'])}")),

        card("В корзину / CTR", f'{fmt_int(kpi["basket"])} шт',
             f'<div class="kpi-delta neutral">CTR {kpi["ctr"]:.0f}%  ·  Конв. {kpi["cr"]:.0f}%</div>'),

        card("% Выкупа (кабинет)", fmt_pct(kpi["buyout_pct"], 0),
             (lambda diff: (
                 f'<div class="kpi-delta up">▲ +{diff:.0f}пп &nbsp; пред: {kpi["buyout_pct_prev"]:.0f}%</div>'
                 if diff > 0 else
                 f'<div class="kpi-delta down">▼ {diff:.0f}пп &nbsp; пред: {kpi["buyout_pct_prev"]:.0f}%</div>'
                 if diff < 0 else
                 f'<div class="kpi-delta neutral">= пред: {kpi["buyout_pct_prev"]:.0f}%</div>'
             ))(kpi["buyout_pct"] - kpi["buyout_pct_prev"])),
    ]
    return '<div class="kpi-grid">' + "".join(cards) + "</div>"


# ── Note блок ─────────────────────────────────────────────────────────────────
def _render_note(kpi: dict) -> str:
    parts = []
    rev_d   = delta_pct(kpi["revenue"], kpi["revenue_prev"])
    ord_d   = delta_pct(kpi["orders"],  kpi["orders_prev"])
    views_d = delta_pct(kpi["views"],   kpi["views_prev"])

    if rev_d is not None and abs(rev_d) >= 10:
        parts.append(
            f"Выручка vs прошлая неделя — {rev_d:+.0f}%, "
            f"заказы — {ord_d:+.0f}%, показы — {views_d:+.0f}%."
        )
    if kpi["drr"] and kpi["drr"] < 5:
        parts.append(f"<strong>ДРР кабинета {kpi['drr']:.1f}%</strong> — отличный результат.")
    elif kpi["drr"] >= 10:
        parts.append(f"<strong>ДРР {kpi['drr']:.1f}%</strong> — требует внимания.")

    if kpi["buyout_pct"] and kpi["buyout_pct_prev"]:
        diff = kpi["buyout_pct"] - kpi["buyout_pct_prev"]
        if abs(diff) >= 1:
            verb = "вырос" if diff > 0 else "снизился"
            parts.append(
                f"Выкуп {verb} с {kpi['buyout_pct_prev']:.0f}% до {kpi['buyout_pct']:.0f}%."
            )

    if not parts:
        parts.append("Метрики стабильны относительно предыдущей недели.")

    return (
        '<div class="note-block"><strong>⚠ Важно:</strong> ' +
        " ".join(parts) + "</div>"
    )


# ── Funnel блок ───────────────────────────────────────────────────────────────
def _render_funnel(f: dict) -> str:
    ctr   = (f["basket"] / f["views"] * 100) if f["views"] else 0
    cr    = (f["orders"] / f["basket"] * 100) if f["basket"] else 0

    return f"""
    <div class="funnel-row">
      <div class="funnel-step">
        <div class="funnel-step-label">Переходы в карточку</div>
        <div class="funnel-step-value">{fmt_int(f["visits"])}</div>
        <div class="funnel-prev">пред: {fmt_int(f["views_prev"])}</div>
      </div>
      <div class="funnel-step">
        <div class="funnel-step-label">В корзину</div>
        <div class="funnel-step-value">{fmt_int(f["basket"])}</div>
        <div class="funnel-conv">конв. {ctr:.0f}%</div>
        <div class="funnel-prev">пред: {fmt_int(f["basket_prev"])}</div>
      </div>
      <div class="funnel-step">
        <div class="funnel-step-label">Заказано</div>
        <div class="funnel-step-value">{fmt_int(f["orders"])}</div>
        <div class="funnel-conv">конв. {cr:.0f}%</div>
        <div class="funnel-prev">пред: {fmt_int(f["orders_prev"])}</div>
      </div>
      <div class="funnel-step">
        <div class="funnel-step-label">Выкуп</div>
        <div class="funnel-step-value">{f["buyout_pct"]:.0f}%</div>
        <div class="funnel-conv">{fmt_int(f.get("buyouts", 0))} шт</div>
        <div class="funnel-prev">пред: {f["buyout_pct_prev"]:.0f}%</div>
      </div>
    </div>
    """


# ── Блок складских остатков ───────────────────────────────────────────────────
def _render_stock_block(kpi: dict) -> str:
    total = kpi.get("stock_total", 0)
    drr_s = kpi.get("drr_sales", 0)
    fp    = kpi.get("sales_for_pay", 0)

    stock_val = fmt_int(total) + " шт" if total else "—"
    drr_s_val = fmt_pct(drr_s) if drr_s else "—"
    fp_val    = fmt_money(fp)  if fp    else "—"

    drr_cls = drr_class(drr_s) if drr_s else "drr-na"
    drr_color = {"drr-good": "var(--success)", "drr-ok": "var(--warn)",
                 "drr-bad": "var(--danger)"}.get(drr_cls, "var(--text-muted)")

    return f"""
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:40px">
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px">
        <div class="kpi-label">В наличии (кабинет)</div>
        <div class="kpi-value" style="font-size:20px">{stock_val}</div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px">складской остаток на сейчас</div>
      </div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px">
        <div class="kpi-label">ДРР по продажам</div>
        <div class="kpi-value" style="font-size:20px;color:{drr_color}">{drr_s_val}</div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px">расход / фактические выплаты</div>
      </div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px">
        <div class="kpi-label">Выручка продаж (к получению)</div>
        <div class="kpi-value" style="font-size:20px">{fp_val}</div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px">сумма forPay по выкупам</div>
      </div>
    </div>
    """


# ── Products таблица ──────────────────────────────────────────────────────────
def _render_products_table(data: dict) -> str:
    products = data["products"]
    if not products:
        return (
            '<div class="table-wrap"><div style="padding:24px; '
            'color:var(--text-muted); text-align:center">Нет данных по карточкам '
            'за выбранный период.</div></div>'
        )

    # Фильтруем: только артикулы с хотя бы 1 заказом за период
    products = [p for p in products if _g(p, "orderCount", "selected") >= 1]

    if not products:
        return (
            '<div class="table-wrap"><div style="padding:24px; '
            'color:var(--text-muted); text-align:center">За выбранный период '
            'нет артикулов с заказами.</div></div>'
        )

    # Сортируем по выручке убыв.
    products = sorted(
        products,
        key=lambda p: _g(p, "orderSum", "selected"),
        reverse=True,
    )[:25]

    rows = []
    tot_spend = tot_orders = tot_rev = 0.0
    tot_bought = 0.0
    tot_stock = 0
    tot_for_pay = 0.0

    # Расход на уровне кабинета (на артикулы не разбивается — это ограничение API).
    # В таблице покажем расход кампании, ассоциированной с артикулом — если
    # в campName кампании встречается код артикула. Иначе — прочерк.
    spend_by_keyword = {}
    for adv_id, cell in data["campaign_spend"].items():
        name = (cell.get("campName") or "").upper()
        spend_by_keyword[name] = spend_by_keyword.get(name, 0.0) + cell["sum"]

    # Источник выкупов — Statistics API (sales), а не воронка: данные ВБ в воронке ненадёжные
    sales_by_vendor         = data.get("sales_by_vendor", {}) or {}
    sales_by_vendor_prev    = data.get("sales_by_vendor_prev", {}) or {}
    sales_for_pay_by_vendor = data.get("sales_for_pay_by_vendor", {}) or {}
    stock_by_vendor         = data.get("stock_by_vendor", {}) or {}

    for p in products:
        prod    = p.get("product") or {}
        nm_id   = prod.get("nmId") or prod.get("nmID") or ""
        name    = prod.get("title") or prod.get("subjectName") or prod.get("brandName") or ""
        vendor  = prod.get("vendorCode") or ""

        orders     = _g(p, "orderCount", "selected")
        orders_prev= _g(p, "orderCount", "past")
        revenue    = _g(p, "orderSum", "selected")

        # Выкупы по артикулу из Statistics API; ключ — supplierArticle == vendorCode
        buyouts        = int(sales_by_vendor.get(str(vendor), 0))
        buyouts_prev   = int(sales_by_vendor_prev.get(str(vendor), 0))
        buyout_pct     = (buyouts / orders * 100) if orders else 0
        buyout_pct_prev= (buyouts_prev / orders_prev * 100) if orders_prev else 0

        # ищем расход по vendorCode в campName
        spend = 0.0
        if vendor:
            v_up = vendor.upper()
            for name_key, val in spend_by_keyword.items():
                if v_up in name_key:
                    spend += val
        drr = (spend / revenue * 100) if revenue and spend else 0
        cpo = (spend / orders) if orders and spend else 0

        # ДРР по продажам — расход / фактические выплаты (forPay)
        art_for_pay = float(sales_for_pay_by_vendor.get(str(vendor), 0))
        drr_sales_art = (spend / art_for_pay * 100) if art_for_pay and spend else 0

        # Складской остаток по артикулу
        stock = int(stock_by_vendor.get(str(vendor), 0))

        # дельта заказов
        if orders_prev == 0 and orders > 0:
            delta_lbl = '<span class="change-pos change-new">new</span>'
        elif orders_prev > 0:
            dp = (orders - orders_prev) / orders_prev * 100
            cls = "change-pos" if dp >= 0 else "change-neg"
            delta_lbl = f'<span class="{cls}">{dp:+.0f}%</span>'
        else:
            delta_lbl = ""

        # дельта выкупа
        if buyout_pct_prev:
            db = buyout_pct - buyout_pct_prev
            cls = "change-pos" if db >= 0 else "change-neg"
            buyout_delta = f' <span class="{cls}">{db:+.0f}пп</span>'
        elif buyout_pct > 0:
            buyout_delta = ' <span class="change-new">new</span>'
        else:
            buyout_delta = ""

        # Цвет ДРР
        drr_cls = drr_class(drr)
        drr_label = fmt_pct(drr) if spend > 0 else "без рекл."

        # Ячейка артикула: если есть nmId — оборачиваем в кликабельную ссылку на WB
        sku_inner = (
            f'<span class="sku-code">{html.escape(str(vendor or nm_id))}</span>'
            f'<span class="sku-name">{html.escape(name[:60])}</span>'
        )
        if nm_id:
            sku_cell = (
                f'<a class="sku-link" '
                f'href="https://www.wildberries.ru/catalog/{nm_id}/detail.aspx?targetUrl=GP" '
                f'target="_blank" rel="noopener noreferrer" '
                f'title="Открыть карточку на Wildberries">{sku_inner}</a>'
            )
        else:
            sku_cell = sku_inner

        drr_s_cls   = drr_class(drr_sales_art)
        drr_s_label = fmt_pct(drr_sales_art) if drr_sales_art else '<span class="muted">—</span>'

        rows.append(f"""
        <tr>
          <td>{sku_cell}</td>
          <td class="right">{fmt_int(spend) if spend else '<span class="muted">—</span>'}</td>
          <td class="right">{fmt_int(orders)} {delta_lbl}</td>
          <td class="right">{fmt_int(revenue)}</td>
          <td class="right"><span class="drr-pill {drr_cls}">{drr_label}</span></td>
          <td class="right"><span class="drr-pill {drr_s_cls}">{drr_s_label}</span></td>
          <td class="right">
            <div class="buyout-bar">
              <div class="bar-track"><div class="bar-fill" style="width:{min(100,buyout_pct):.0f}%"></div></div>
              {buyout_pct:.0f}%{buyout_delta}
            </div>
          </td>
          <td class="right">{fmt_int(stock) if stock else '<span class="muted">—</span>'}</td>
          <td class="right">{fmt_int(cpo) if cpo else '<span class="muted">—</span>'}</td>
        </tr>
        """)

        tot_spend  += spend
        tot_orders += orders
        tot_rev    += revenue
        tot_bought += buyouts
        tot_stock  += stock
        tot_for_pay += art_for_pay

    tot_drr      = (tot_spend / tot_rev * 100)      if tot_rev     else 0
    tot_drr_s    = (tot_spend / tot_for_pay * 100)  if tot_for_pay else 0
    tot_buy      = (tot_bought / tot_orders * 100)  if tot_orders  else 0
    tot_cpo      = (tot_spend / tot_orders)         if tot_orders  else 0

    return f"""
    <div class="table-wrap">
      <table style="min-width:860px">
        <thead><tr>
          <th>Артикул</th>
          <th class="right">Расход ₽</th>
          <th class="right">Заказы шт</th>
          <th class="right">Выручка заказов ₽</th>
          <th class="right">ДРР заказ %</th>
          <th class="right">ДРР продаж %</th>
          <th class="right">% Выкупа</th>
          <th class="right">Остаток шт</th>
          <th class="right">CPO ₽</th>
        </tr></thead>
        <tbody>
          {''.join(rows)}
          <tr class="total-row">
            <td><strong>ИТОГО</strong></td>
            <td class="right">{fmt_int(tot_spend)}</td>
            <td class="right">{fmt_int(tot_orders)}</td>
            <td class="right">{fmt_int(tot_rev)}</td>
            <td class="right"><span class="drr-pill {drr_class(tot_drr)}">{fmt_pct(tot_drr)}</span></td>
            <td class="right"><span class="drr-pill {drr_class(tot_drr_s)}">{fmt_pct(tot_drr_s) if tot_for_pay else '—'}</span></td>
            <td class="right">{tot_buy:.0f}%</td>
            <td class="right">{fmt_int(tot_stock) if tot_stock else '—'}</td>
            <td class="right">{fmt_int(tot_cpo)}</td>
          </tr>
        </tbody>
      </table>
    </div>
    """


def _g(p: dict, field: str, period: str) -> float:
    """Локальный shortcut к _funnel_metric."""
    from wb_api import _funnel_metric
    return _funnel_metric(p, field, period)


# ── Campaigns таблица ─────────────────────────────────────────────────────────
def _render_campaigns_table(data: dict) -> str:
    spend = data["campaign_spend"]
    stats = data["campaign_stats"]

    rows = []
    total_spend = total_shows = 0.0

    # Сортируем по расходу убыв.
    items = sorted(spend.items(), key=lambda kv: kv[1]["sum"], reverse=True)
    for adv_id, cell in items:
        s = stats.get(adv_id, {})
        views = float(s.get("views") or 0)
        ctr   = float(s.get("ctr") or 0)
        a_type = ADVERT_TYPE.get(cell.get("advertType"), "—")
        tag_cls = "camp-auto" if cell.get("advertType") == 8 else (
            "camp-search" if cell.get("advertType") in (6, 9) else "camp-ark"
        )
        ctr_cell = (
            f'<span class="drr-pill {ctr_class(ctr)}">{fmt_pct(ctr)}</span>'
            if ctr else '<span class="muted">—</span>'
        )
        rows.append(f"""
        <tr>
          <td>{html.escape(str(cell.get("campName") or "—"))} <span style="color:var(--text-dim);font-size:11px">#{adv_id}</span></td>
          <td><span class="camp-tag {tag_cls}">{a_type}</span></td>
          <td class="right">{fmt_int(cell["sum"])}</td>
          <td class="right">{fmt_int(views) if views else '<span class="muted">—</span>'}</td>
          <td class="right">{ctr_cell}</td>
        </tr>
        """)
        total_spend += cell["sum"]
        total_shows += views

    if not rows:
        rows.append(
            '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--text-muted)">'
            'Активных кампаний с расходом за период не найдено.</td></tr>'
        )

    return f"""
    <div class="table-wrap" style="margin-bottom:40px">
      <table>
        <thead><tr>
          <th>Кампания</th>
          <th>Тип</th>
          <th class="right">Расход ₽</th>
          <th class="right">Показы</th>
          <th class="right">CTR %</th>
        </tr></thead>
        <tbody>
          {''.join(rows)}
          <tr class="total-row">
            <td><strong>ИТОГО ({len(spend)} кампаний)</strong></td>
            <td></td>
            <td class="right">{fmt_int(total_spend)}</td>
            <td class="right">{fmt_int(total_shows) if total_shows else '—'}</td>
            <td class="right">—</td>
          </tr>
        </tbody>
      </table>
    </div>
    """


# ── Insights блок ─────────────────────────────────────────────────────────────
def _default_insights(data: dict) -> dict:
    """Базовые автоматические выводы (без AI)."""
    products = data["products"]
    kpi = data["kpi"]

    # ТОП-3 по росту заказов
    leaders = []
    sorted_growth = sorted(
        [(p, _delta_orders(p)) for p in products],
        key=lambda x: (x[1] if x[1] is not None else -999),
        reverse=True,
    )
    for p, growth in sorted_growth[:3]:
        if growth is None or growth <= 0:
            continue
        prod = p.get("product") or {}
        name = prod.get("vendorCode") or prod.get("nmId") or "артикул"
        leaders.append({
            "num": "ok",
            "text": f"<strong>{html.escape(str(name))}</strong> — рост заказов {growth:+.0f}%."
        })

    # Проблемы
    problems = []
    if kpi["drr"] >= 10:
        problems.append({
            "num": "urgent",
            "text": f"<strong>ДРР кабинета {kpi['drr']:.1f}%</strong> — выше нормы. Снижать ставки в дорогих кампаниях."
        })

    # топ-1 по падению заказов
    fallers = sorted(
        [(p, _delta_orders(p)) for p in products],
        key=lambda x: (x[1] if x[1] is not None else 0),
    )
    for p, d in fallers[:2]:
        if d is None or d >= -20:
            continue
        prod = p.get("product") or {}
        name = prod.get("vendorCode") or prod.get("nmId") or ""
        problems.append({
            "num": "warn",
            "text": f"<strong>{html.escape(str(name))}</strong> — заказы {d:+.0f}%. Проверить ставки/карточку."
        })

    # Срочные действия
    urgent = []
    for p in products:
        spend = 0  # неизвестно по артикулу
        rev   = _g(p, "orderSum", "selected")
        ords  = _g(p, "orderCount", "selected")
        if rev and ords and ords < 10 and rev < 30_000:
            prod = p.get("product") or {}
            urgent.append({
                "num": "warn",
                "text": f"<strong>{html.escape(str(prod.get('vendorCode') or prod.get('nmId')))}</strong> — мало заказов ({int(ords)}). Оценить целесообразность РК."
            })
            if len(urgent) >= 3:
                break

    next_week = [
        {"num": "info", "text": "Сравнить эту неделю с прошлой по топ-5 артикулам и принять решение по бюджетам."},
        {"num": "info", "text": "Проверить карточки с низким выкупом — фото, размерная сетка, отзывы."},
        {"num": "info", "text": "При ДРР < 5% — масштабировать рабочие кампании."},
    ]

    return {
        "leaders":   leaders or [{"num": "info", "text": "Стабильная неделя без явных лидеров роста."}],
        "problems":  problems or [{"num": "info", "text": "Критичных проблем в кабинете не обнаружено."}],
        "urgent":    urgent or [{"num": "info", "text": "Срочных мер не требуется."}],
        "next_week": next_week,
    }


def _delta_orders(p: dict) -> float | None:
    cur  = _g(p, "orderCount", "selected")
    prev = _g(p, "orderCount", "past")
    if prev == 0:
        return None if cur == 0 else 999
    return (cur - prev) / prev * 100


def _render_done_tasks(tasks: list[dict]) -> str:
    """Блок выполненных задач проекта за период отчёта."""
    if not tasks:
        return ""
    rows = ""
    for t in tasks:
        assignee = f'<span class="dt-who">@{html.escape(t["assignee"])}</span>' if t.get("assignee") else ""
        rows += (
            f'<div class="dt-row">'
            f'<span class="dt-date">{html.escape(t["done_at"])}</span>'
            f'<span class="dt-title">{html.escape(t["title"])}</span>'
            f'{assignee}'
            f'</div>'
        )
    return f"""
    <section class="section done-tasks-section">
      <h2 class="section-title">✅ Выполненные задачи за период</h2>
      <div class="done-tasks-list">{rows}</div>
    </section>
    """


def _render_insights(ins: dict) -> str:
    def block(title: str, items: list[dict]) -> str:
        lis = []
        for i, item in enumerate(items, 1):
            cls = f'num-{item.get("num", "info")}'
            label = "!" if item.get("num") == "urgent" else str(i)
            lis.append(
                f'<div class="insight-item">'
                f'<span class="insight-num {cls}">{label}</span>'
                f'<span class="insight-text">{item["text"]}</span>'
                f'</div>'
            )
        return f'<div class="insight-card"><h3>{title}</h3>{"".join(lis)}</div>'

    return f"""
    <div class="insights-grid">
      {block("🟢 Лидеры недели",  ins["leaders"])}
      {block("🔴 Проблемы",       ins["problems"])}
      {block("⚡ Срочные действия", ins["urgent"])}
      {block("📊 На следующую неделю", ins["next_week"])}
    </div>
    """
