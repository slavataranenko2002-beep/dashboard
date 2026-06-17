"""
wb_report.py — 5-вкладочный HTML отчёт WB.
Вкладки: Сводка / Воронка / Реклама / Юнит / Рекомендации
"""
from __future__ import annotations

import html as _esc
import json
from datetime import date

from wb_api import ADVERT_TYPE


MONTHS = ["января","февраля","марта","апреля","мая","июня",
          "июля","августа","сентября","октября","ноября","декабря"]


# ─── форматирование ────────────────────────────────────────────────────────────

def fmt_int(v):
    if v is None: return "—"
    return f"{int(round(v)):,}".replace(",", " ")

def fmt_money(v, suffix=" ₽"):
    if v is None: return "—"
    return f"{int(round(v)):,}".replace(",", " ") + suffix

def fmt_pct(v, digits=1):
    if v is None: return "—"
    return f"{v:.{digits}f}%"

def fmt_period(d_from: date, d_to: date) -> str:
    if d_from.month == d_to.month and d_from.year == d_to.year:
        return f"{d_from.day:02d} — {d_to.day:02d} {MONTHS[d_to.month-1]} {d_to.year}"
    return (f"{d_from.day:02d} {MONTHS[d_from.month-1]} — "
            f"{d_to.day:02d} {MONTHS[d_to.month-1]} {d_to.year}")

def short_money(v):
    if v is None: return "—"
    if v >= 1_000_000: return f"{v/1_000_000:.1f} млн".replace(".", ",")
    if v >= 1_000: return f"{v/1_000:.0f} тыс"
    return fmt_int(v)

def delta_badge(cur, prev, unit=""):
    if not prev: return ""
    d = (cur - prev) / prev * 100
    if d > 0: return f'<span class="badge up">▲ {d:+.0f}%</span>'
    if d < 0: return f'<span class="badge dn">▼ {d:.0f}%</span>'
    return '<span class="badge nt">= 0%</span>'


# ─── CSS ──────────────────────────────────────────────────────────────────────

CSS = """
:root {
  --bg:#0e0f14; --surf:#161820; --surf2:#1d1f2d; --surf3:#252840;
  --brd:#2a2d3e; --brd2:#363a56;
  --acc:#7c6af7; --acc2:#4fd1c5;
  --warn:#f6ad55; --danger:#fc5c65; --ok:#48bb78;
  --txt:#e8eaf6; --txt2:#9aa1bf; --txt3:#4a4e6b;
  --tr:.2s;
}
:root.light {
  --bg:#f0f2f8; --surf:#fff; --surf2:#f4f6fb; --surf3:#eaecf4;
  --brd:#dde0ea; --brd2:#c8ccd8;
  --acc:#6450e6; --acc2:#2eb8ac;
  --warn:#c97a16; --danger:#d83a48; --ok:#2f9c5d;
  --txt:#1a1d2e; --txt2:#5a6072; --txt3:#9aa1b3;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:'Inter',sans-serif;font-size:14px;line-height:1.6;min-height:100vh}
body::before{content:'';position:fixed;inset:0;background:radial-gradient(circle at 18% 18%,rgba(124,106,247,.06) 0%,transparent 50%),radial-gradient(circle at 82% 82%,rgba(79,209,197,.04) 0%,transparent 50%);pointer-events:none;z-index:0}
:root.light body::before{opacity:.3}
a{color:inherit;text-decoration:none}

/* ── toolbar ── */
.toolbar{position:fixed;top:16px;right:16px;z-index:200;display:flex;gap:8px}
.tbtn{height:36px;padding:0 14px;border-radius:9px;border:1px solid var(--brd);background:var(--surf);color:var(--txt2);font-size:13px;font-family:inherit;font-weight:500;cursor:pointer;display:inline-flex;align-items:center;gap:6px;transition:var(--tr);box-shadow:0 2px 8px rgba(0,0,0,.18)}
.tbtn:hover{border-color:var(--acc);color:var(--acc)}
.tbtn.pri{background:var(--acc);color:#fff;border-color:var(--acc)}
.tbtn.pri:hover{opacity:.88;color:#fff}
.tbtn.ico{width:36px;padding:0;justify-content:center;font-size:16px}

/* ── page ── */
.page{position:relative;z-index:1;max-width:1160px;margin:0 auto;padding:32px 28px 64px}

/* ── report header ── */
.rep-header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:28px;padding-bottom:24px;border-bottom:1px solid var(--brd)}
.rep-badge{display:inline-flex;align-items:center;gap:7px;background:rgba(124,106,247,.12);border:1px solid rgba(124,106,247,.28);border-radius:6px;padding:4px 11px;font-size:11px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:#a89df8;margin-bottom:12px}
.rep-title{font-family:'Unbounded',sans-serif;font-size:24px;font-weight:900;line-height:1.2}
.rep-title span{color:var(--acc)}
.rep-meta{text-align:right}
.rep-meta .period{font-family:'Unbounded',sans-serif;font-size:13px;color:var(--txt2);margin-bottom:4px}
.rep-meta .sub{font-size:12px;color:var(--txt3)}
.rep-chips{display:flex;gap:8px;margin-top:10px;justify-content:flex-end;flex-wrap:wrap}
.rep-chip{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:5px;font-size:12px;background:var(--surf2);border:1px solid var(--brd);color:var(--txt2)}
.rep-chip b{color:var(--txt)}

/* ── tabs ── */
.tabs{display:flex;gap:2px;background:var(--surf);border:1px solid var(--brd);border-radius:12px;padding:4px;margin-bottom:28px;overflow-x:auto}
.tab-btn{flex:1;min-width:max-content;padding:9px 18px;border-radius:9px;border:none;background:transparent;color:var(--txt2);font-size:13px;font-weight:500;font-family:inherit;cursor:pointer;transition:var(--tr);white-space:nowrap;text-align:center}
.tab-btn:hover{color:var(--txt);background:var(--surf2)}
.tab-btn.active{background:var(--acc);color:#fff;font-weight:600;box-shadow:0 2px 8px rgba(124,106,247,.35)}
.tab-pane{display:none}
.tab-pane.active{display:block}

/* ── section title ── */
.sec{font-family:'Unbounded',sans-serif;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--txt);margin-bottom:16px;display:flex;align-items:center;gap:10px}
.sec::after{content:'';flex:1;height:1px;background:var(--brd)}

/* ── metric cards grid ── */
.kgrid{display:grid;gap:14px;margin-bottom:28px}
.kgrid-4{grid-template-columns:repeat(4,1fr)}
.kgrid-3{grid-template-columns:repeat(3,1fr)}
.kgrid-2{grid-template-columns:repeat(2,1fr)}
.kcard{background:var(--surf);border:1px solid var(--brd);border-radius:12px;padding:18px 18px 16px;position:relative;overflow:hidden}
.kcard::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--acc),var(--acc2));opacity:.6}
.kcard.green::before{background:linear-gradient(90deg,var(--ok),var(--acc2))}
.kcard.orange::before{background:linear-gradient(90deg,var(--warn),#f68e3a)}
.kcard.red::before{background:linear-gradient(90deg,var(--danger),#fc8f66)}
.kc-label{font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--txt2);margin-bottom:8px}
.kc-val{font-family:'Unbounded',sans-serif;font-size:20px;font-weight:700;color:var(--txt);margin-bottom:6px;line-height:1.2}
.kc-sub{font-size:12px;color:var(--txt3)}

/* ── badge / delta ── */
.badge{display:inline-flex;align-items:center;gap:3px;font-size:11px;font-weight:600;padding:2px 7px;border-radius:4px}
.badge.up{color:var(--ok);background:rgba(72,187,120,.1)}
.badge.dn{color:var(--danger);background:rgba(252,92,101,.1)}
.badge.nt{color:var(--txt2);background:rgba(123,127,158,.1)}
.badge.warn{color:var(--warn);background:rgba(246,173,85,.1)}

/* ── pill ── */
.pill{display:inline-block;padding:3px 9px;border-radius:5px;font-weight:600;font-size:12px}
.pill.good{background:rgba(72,187,120,.12);color:var(--ok)}
.pill.ok  {background:rgba(246,173,85,.12);color:var(--warn)}
.pill.bad {background:rgba(252,92,101,.12);color:var(--danger)}
.pill.na  {background:transparent;color:var(--txt3)}
.pill.urgent{background:rgba(252,92,101,.18);color:var(--danger)}

/* ── insight cards ── */
.ins-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:28px}
.ins-card{background:var(--surf);border:1px solid var(--brd);border-radius:12px;padding:18px}
.ins-card h3{font-family:'Unbounded',sans-serif;font-size:11px;font-weight:700;color:var(--txt);margin-bottom:14px;display:flex;align-items:center;gap:8px}
.ins-row{display:flex;gap:9px;margin-bottom:10px;font-size:13px;line-height:1.5}
.ins-row:last-child{margin-bottom:0}
.ins-num{width:22px;height:22px;flex-shrink:0;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;margin-top:2px}
.n-ok    {background:rgba(72,187,120,.18);color:var(--ok)}
.n-warn  {background:rgba(246,173,85,.18);color:var(--warn)}
.n-bad   {background:rgba(252,92,101,.18);color:var(--danger)}
.n-info  {background:rgba(124,106,247,.18);color:#a89df8}
.ins-txt {color:var(--txt2)}
.ins-txt strong{color:var(--txt)}

/* ── filter strip ── */
.fstrip{display:flex;gap:6px;margin-bottom:18px;flex-wrap:wrap;align-items:center}
.fbtn{height:32px;padding:0 14px;border-radius:7px;border:1px solid var(--brd);background:var(--surf);color:var(--txt2);font-size:12px;font-weight:500;font-family:inherit;cursor:pointer;transition:var(--tr)}
.fbtn:hover{border-color:var(--acc);color:var(--acc)}
.fbtn.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.fsearch{flex:1;min-width:160px;height:32px;background:var(--surf);border:1px solid var(--brd);border-radius:7px;padding:0 12px;color:var(--txt);font-size:13px;font-family:inherit;outline:none;transition:var(--tr)}
.fsearch:focus{border-color:var(--acc)}
.fsearch::placeholder{color:var(--txt3)}

/* ── article cards ── */
.art-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;margin-bottom:28px}
.art-card{background:var(--surf);border:1px solid var(--brd);border-radius:12px;padding:16px;position:relative;overflow:hidden;transition:border-color var(--tr)}
.art-card:hover{border-color:var(--brd2)}
.art-card.growth {border-left:3px solid var(--ok)}
.art-card.stable {border-left:3px solid var(--acc2)}
.art-card.decline{border-left:3px solid var(--warn)}
.art-card.alert  {border-left:3px solid var(--danger)}
.art-card[style*="display:none"]{display:none!important}

.art-head{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:10px;gap:8px}
.art-sku{font-family:'Unbounded',sans-serif;font-size:11px;font-weight:700;color:#c0beff}
:root.light .art-sku{color:#5048c8}
.art-name{font-size:11px;color:var(--txt2);display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden;margin-top:2px}
.art-status{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:4px}
.art-status.growth {background:var(--ok)}
.art-status.stable {background:var(--acc2)}
.art-status.decline{background:var(--warn)}
.art-status.alert  {background:var(--danger)}

.art-metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.art-m{background:var(--surf2);border-radius:7px;padding:8px 10px}
.art-m .lbl{font-size:10px;color:var(--txt3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px}
.art-m .val{font-family:'Unbounded',sans-serif;font-size:14px;font-weight:700;color:var(--txt)}
.art-m .sub{font-size:11px;color:var(--txt2);margin-top:1px}

.art-funnel{margin-bottom:10px}
.art-funnel-label{font-size:10px;color:var(--txt3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px}
.funnel-steps{display:flex;align-items:center;gap:3px}
.fstep{display:flex;flex-direction:column;align-items:center;flex:1}
.fstep-val{font-size:11px;font-weight:600;color:var(--txt);white-space:nowrap}
.fstep-lbl{font-size:9px;color:var(--txt3);text-transform:uppercase;margin-top:1px}
.fstep-arrow{color:var(--txt3);font-size:10px;flex-shrink:0;margin-top:-6px}
.fconv{font-size:9px;color:var(--acc2);font-weight:600}

.art-bar{margin-bottom:10px}
.bar-track{width:100%;height:4px;background:var(--surf3);border-radius:2px;overflow:hidden}
.bar-fill{height:100%;border-radius:2px;background:linear-gradient(90deg,var(--acc),var(--acc2))}

.art-footer{display:flex;gap:8px;flex-wrap:wrap;align-items:center}

/* ── tables ── */
.tbl-wrap{background:var(--surf);border:1px solid var(--brd);border-radius:12px;overflow-x:auto;margin-bottom:28px}
table{width:100%;border-collapse:collapse}
thead th{background:var(--surf2);padding:10px 14px;text-align:left;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--txt2);border-bottom:1px solid var(--brd);white-space:nowrap}
thead th.r{text-align:right}
tbody tr{border-bottom:1px solid var(--brd)}
tbody tr:last-child{border-bottom:none}
tbody td{padding:10px 14px;color:var(--txt);vertical-align:middle}
tbody td.r{text-align:right}
tbody td.dim{color:var(--txt2)}
.tot-row td{font-weight:700;background:var(--surf2)!important}
.sku-code{font-weight:600;font-family:'Unbounded',sans-serif;font-size:11px;color:#c0beff}
:root.light .sku-code{color:#5048c8}
.sku-name{font-size:11px;color:var(--txt2);display:block;margin-top:2px;max-width:220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sku-link{display:inline-block;color:inherit;position:relative;padding-right:14px}
.sku-link:hover .sku-code{color:var(--acc)}
.sku-link::after{content:'↗';position:absolute;top:0;right:0;font-size:9px;color:var(--txt3);opacity:0;transition:.15s}
.sku-link:hover::after{opacity:1;color:var(--acc)}
.camp-tag{display:inline-flex;align-items:center;gap:4px;font-size:11px;padding:2px 7px;border-radius:4px;font-weight:500}
.ct-ark   {background:rgba(124,106,247,.12);color:#a89df8}
.ct-search{background:rgba(79,209,197,.12);color:var(--acc2)}
.ct-auto  {background:rgba(246,173,85,.12);color:var(--warn)}
.buyout-bar{display:flex;align-items:center;gap:7px}
.bb-track{width:52px;height:4px;background:var(--surf3);border-radius:2px;overflow:hidden;flex-shrink:0}
.bb-fill{height:100%;border-radius:2px;background:linear-gradient(90deg,var(--acc),var(--acc2))}

/* ── unit health ── */
.health{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;padding:3px 9px;border-radius:5px}
.health.good  {background:rgba(72,187,120,.1);color:var(--ok)}
.health.ok    {background:rgba(246,173,85,.1);color:var(--warn)}
.health.bad   {background:rgba(252,92,101,.1);color:var(--danger)}
.health.urgent{background:rgba(252,92,101,.2);color:var(--danger)}

/* ── recommendations ── */
.rec-list{display:flex;flex-direction:column;gap:10px;margin-bottom:28px}
.rec-item{display:flex;gap:12px;background:var(--surf);border:1px solid var(--brd);border-left:3px solid var(--brd2);border-radius:8px;padding:14px 16px;font-size:13px;line-height:1.55}
.rec-item.urgent{border-left-color:var(--danger)}
.rec-item.warn  {border-left-color:var(--warn)}
.rec-item.ok    {border-left-color:var(--ok)}
.rec-item.info  {border-left-color:var(--acc)}
.rec-icon{font-size:18px;flex-shrink:0;margin-top:1px}
.rec-body{flex:1}
.rec-body b{color:var(--txt);display:block;margin-bottom:3px}
.rec-body span{color:var(--txt2)}

/* ── done tasks ── */
.dt-list{display:flex;flex-direction:column;gap:7px;margin-bottom:28px}
.dt-row{display:flex;align-items:baseline;gap:10px;padding:9px 14px;background:var(--surf);border:1px solid var(--brd);border-radius:8px}
.dt-date{flex-shrink:0;font-size:12px;color:var(--txt2);min-width:32px}
.dt-title{flex:1;font-size:13px;color:var(--txt)}
.dt-who{flex-shrink:0;font-size:11px;color:var(--txt2);background:rgba(124,106,247,.15);padding:2px 8px;border-radius:20px}

/* ── print ── */
@media print {
  @page{size:A4;margin:14mm 12mm}
  :root{--bg:#fff;--surf:#fff;--surf2:#f4f5f9;--brd:#d3d7e2;--txt:#111418;--txt2:#4a4f60;--txt3:#8a90a0}
  body{background:#fff!important;font-size:11pt}
  body::before{display:none!important}
  .toolbar,.tabs{display:none!important}
  .tab-pane{display:block!important}
  .page{padding:0!important;max-width:none!important}
  .kcard,.ins-card,.tbl-wrap,.art-card,.rec-item{page-break-inside:avoid;break-inside:avoid}
  .art-grid{grid-template-columns:repeat(2,1fr)!important}
}
"""

# ─── JavaScript ───────────────────────────────────────────────────────────────

JS = r"""
// ── theme ──
function applyTheme(t){
  document.documentElement.classList.toggle('light',t==='light');
  var b=document.getElementById('theme-btn');
  if(b) b.textContent=t==='light'?'🌙':'☀️';
}
function toggleTheme(){
  var n=document.documentElement.classList.contains('light')?'dark':'light';
  try{localStorage.setItem('theme',n)}catch(e){}
  applyTheme(n);
}
try{applyTheme(localStorage.getItem('theme')||'dark')}catch(e){applyTheme('dark')}

// ── tabs ──
function showTab(id){
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.toggle('active',b.dataset.tab===id)});
  document.querySelectorAll('.tab-pane').forEach(function(p){p.classList.toggle('active',p.id==='pane-'+id)});
  try{localStorage.setItem('wb-tab',id)}catch(e){}
}
document.addEventListener('DOMContentLoaded',function(){
  var t=null;
  try{t=localStorage.getItem('wb-tab')}catch(e){}
  if(!t) t='summary';
  showTab(t);
});

// ── funnel filters ──
function funnelFilter(btn,status){
  document.querySelectorAll('#pane-funnel .fbtn[data-f]').forEach(function(b){b.classList.toggle('on',b===btn)});
  _applyFunnelFilter();
}
function _applyFunnelFilter(){
  var active=document.querySelector('#pane-funnel .fbtn[data-f].on');
  var f=active?active.dataset.f:'all';
  var q=(document.getElementById('funnel-search')||{}).value||'';
  q=q.toLowerCase().trim();
  document.querySelectorAll('.art-card').forEach(function(c){
    var ok=(f==='all'||c.dataset.status===f);
    if(ok&&q) ok=c.dataset.sku.toLowerCase().indexOf(q)>=0||c.dataset.name.toLowerCase().indexOf(q)>=0;
    c.style.display=ok?'':'none';
  });
}
document.addEventListener('DOMContentLoaded',function(){
  var s=document.getElementById('funnel-search');
  if(s) s.addEventListener('input',_applyFunnelFilter);
  var firstF=document.querySelector('#pane-funnel .fbtn[data-f]');
  if(firstF) firstF.classList.add('on');
});

// ── unit filters ──
function unitFilter(btn,range){
  document.querySelectorAll('#pane-unit .fbtn[data-mf]').forEach(function(b){b.classList.toggle('on',b===btn)});
  var rows=document.querySelectorAll('#unit-tbody tr');
  rows.forEach(function(r){
    var m=parseFloat(r.dataset.margin||'0');
    var ok=true;
    if(range==='25') ok=m>=25;
    else if(range==='10') ok=m>=10&&m<25;
    else if(range==='0') ok=m>=0&&m<10;
    else if(range==='neg') ok=m<0;
    r.style.display=ok?'':'none';
  });
}
document.addEventListener('DOMContentLoaded',function(){
  var f=document.querySelector('#pane-unit .fbtn[data-mf]');
  if(f) f.classList.add('on');
});

// ── ads filters ──
function adsFilter(btn,f){
  document.querySelectorAll('#pane-ads .fbtn[data-af]').forEach(function(b){b.classList.toggle('on',b===btn)});
  var rows=document.querySelectorAll('#ads-tbody tr');
  rows.forEach(function(r){
    var ok=f==='all'||(f==='active'&&r.dataset.status==='active')||(f===r.dataset.type);
    r.style.display=ok?'':'none';
  });
}
document.addEventListener('DOMContentLoaded',function(){
  var f=document.querySelector('#pane-ads .fbtn[data-af]');
  if(f) f.classList.add('on');
});

// ── pdf / html ──
var _filename=document.title.replace(/[^a-zA-Z0-9а-яёА-ЯЁ _\-]/g,'_')+'.html';
function downloadPDF(){window.print()}
function downloadHTML(){
  var h='<!DOCTYPE html>\n'+document.documentElement.outerHTML;
  var b=new Blob([h],{type:'text/html;charset=utf-8'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(b);a.download=_filename;
  document.body.appendChild(a);a.click();
  setTimeout(function(){URL.revokeObjectURL(a.href);a.remove()},100);
}
document.addEventListener('keydown',function(e){
  if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();downloadHTML()}
});
"""


# ─── calc unit economics ──────────────────────────────────────────────────────

def _calc_unit(row: dict) -> dict:
    wb_price     = float(row.get("wb_price") or 0)
    spp_pct      = float(row.get("spp_pct") or 0)
    comm_pct     = float(row.get("commission_pct") or 0)
    cost_price   = float(row.get("cost_price") or 0)
    log_to_wb    = float(row.get("logistics_to_wb") or 0)
    packaging    = float(row.get("packaging") or 0)
    overhead     = float(row.get("overhead") or 0)
    defect_pct   = float(row.get("defect_pct") or 0)
    log_ktr      = float(row.get("logistics_ktr") or 0)
    drr_pct      = float(row.get("drr_pct") or 0)
    drr_ext      = float(row.get("drr_external_rub") or 0)
    tax_pct      = float(row.get("tax_pct") or 7)

    net_price    = wb_price * (1 - spp_pct / 100)
    commission   = net_price * comm_pct / 100
    logistics    = log_ktr if log_ktr else 0.0
    defect_rub   = cost_price * defect_pct / 100
    drr_rub      = net_price * drr_pct / 100 + drr_ext
    tax_rub      = net_price * tax_pct / 100
    total_costs  = (cost_price + log_to_wb + packaging + overhead
                    + defect_rub + commission + logistics + drr_rub + tax_rub)
    profit       = net_price - total_costs
    margin_pct   = profit / net_price * 100 if net_price else 0
    return {
        "net_price":  net_price,
        "commission": commission,
        "logistics":  logistics,
        "drr_rub":    drr_rub,
        "tax_rub":    tax_rub,
        "cost_price": cost_price,
        "profit":     profit,
        "margin_pct": margin_pct,
    }


# ─── data helpers ─────────────────────────────────────────────────────────────

def _g(p: dict, field: str, period: str) -> float:
    from wb_api import _funnel_metric
    return _funnel_metric(p, field, period)

def _delta_orders(p: dict) -> float | None:
    cur  = _g(p, "orderCount", "selected")
    prev = _g(p, "orderCount", "past")
    if prev == 0: return None if cur == 0 else 999
    return (cur - prev) / prev * 100

def _art_status(p: dict) -> str:
    d = _delta_orders(p)
    orders = _g(p, "orderCount", "selected")
    if orders == 0 or d is None: return "alert"
    # ≥500% — скорее всего артефакт малой базы (1→6 = +500%), не «звезда»
    if 20 <= d < 500: return "growth"
    if d >= 500:      return "stable"
    if d >= -10:      return "stable"
    if d >= -50:      return "decline"
    return "alert"

# ─── Нормы % выкупа по категориям (мин, макс) ────────────────────────────────
# Ключ — подстрока из subjectName товара (нижний регистр)
BUYOUT_NORMS: dict[str, tuple[int, int]] = {
    "рюкзак":         (30, 80),
    "сумк":           (30, 80),   # сумка / сумки / сумочка
    "мужск":          (30, 65),   # мужская одежда
    "брюки мужск":    (30, 65),
    "джинсы мужск":   (30, 65),
    "костюм мужск":   (30, 65),
    "женск":          (25, 40),   # женская одежда
    "платье":         (25, 40),
    "блузк":          (25, 40),
    "пальто":         (25, 40),
    "костюм":         (25, 40),   # не уточнён пол — по умолчанию женский
    "брюки":          (25, 40),
    "юбк":            (25, 40),
    "джемпер":        (25, 40),
    "свитер":         (25, 40),
}
_BUYOUT_DEFAULT = (30, 65)  # если категория не распознана


def _detect_buyout_norm(products: list) -> tuple[int, int]:
    """Определяет норму выкупа кабинета по subjectName товаров."""
    from collections import Counter
    matches: list[tuple[int, int]] = []
    for p in products:
        prod = p.get("product") or {}
        subj = (prod.get("subjectName") or "").lower()
        if not subj:
            continue
        for kw, norm in BUYOUT_NORMS.items():
            if kw in subj:
                matches.append(norm)
                break
    if not matches:
        return _BUYOUT_DEFAULT
    # Берём наиболее частую норму
    counter = Counter(matches)
    return counter.most_common(1)[0][0]


def _buyout_status(buyout_pct: float, norm: tuple[int, int]) -> str:
    """good / ok / bad относительно нормы категории."""
    lo, hi = norm
    if buyout_pct >= hi:   return "good"
    if buyout_pct >= lo:   return "ok"
    return "bad"


STATUS_LABEL = {
    "growth":  "Рост",
    "stable":  "Стабильно",
    "decline": "Падение",
    "alert":   "Тревога",
    "all":     "Все",
}

# ─── main entry point ─────────────────────────────────────────────────────────

def render_html(data: dict, done_tasks: list | None = None, unit_data: list | None = None) -> str:
    done_tasks = done_tasks or []
    unit_data  = unit_data  or []

    cabinet    = data["cabinet"]
    d_from     = data["date_from"]
    d_to       = data["date_to"]
    prev_from  = data["prev_from"]
    prev_to    = data["prev_to"]
    kpi        = data["kpi"]

    period_label = fmt_period(d_from, d_to)
    prev_label   = fmt_period(prev_from, prev_to)
    generated_at = d_to.strftime("%d.%m.%Y")

    n_articles  = len([p for p in data["products"] if _g(p,"orderCount","selected") >= 1])
    n_campaigns = len(data["campaign_spend"])
    buyouts_src = data.get("buyouts_source","")
    buyouts_warn = (
        '<span class="badge warn">⚠ выкупы из воронки</span>'
        if buyouts_src == "funnel_fallback" else ""
    )

    summary_html  = _render_summary(data, unit_data)
    funnel_html   = _render_funnel_tab(data)
    ads_html      = _render_ads_tab(data)
    unit_html     = _render_unit_tab(unit_data, data)
    recs_html     = _render_recs_tab(data, unit_data)
    done_html     = _render_done_tasks(done_tasks)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WB Отчёт — {_esc.escape(cabinet)} | {period_label}</title>
<link href="https://fonts.googleapis.com/css2?family=Unbounded:wght@400;700;900&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<div class="toolbar">
  <button class="tbtn pri" onclick="downloadPDF()">📄 PDF</button>
  <button class="tbtn" onclick="downloadHTML()">💾 HTML</button>
  <button class="tbtn ico" id="theme-btn" onclick="toggleTheme()">☀️</button>
</div>
<div class="page">

  <!-- header -->
  <div class="rep-header">
    <div>
      <div class="rep-badge">🛍 Wildberries · {_esc.escape(cabinet)}</div>
      <h1 class="rep-title">Аналитический<br>отчёт <span>WB</span></h1>
    </div>
    <div class="rep-meta">
      <div class="period">{period_label}</div>
      <div class="sub">Сформирован: {generated_at}</div>
      <div class="sub">Сравнение: {prev_label}</div>
      <div class="rep-chips">
        <span class="rep-chip">📦 <b>{n_articles}</b> артикулов</span>
        <span class="rep-chip">📣 <b>{n_campaigns}</b> кампаний</span>
        {buyouts_warn}
      </div>
    </div>
  </div>

  <!-- tabs -->
  <div class="tabs">
    <button class="tab-btn" data-tab="summary"  onclick="showTab('summary')">📊 Сводка</button>
    <button class="tab-btn" data-tab="funnel"   onclick="showTab('funnel')">🔽 Воронка</button>
    <button class="tab-btn" data-tab="ads"      onclick="showTab('ads')">📣 Реклама</button>
    <button class="tab-btn" data-tab="unit"     onclick="showTab('unit')">💰 Юнит</button>
    <button class="tab-btn" data-tab="recs"     onclick="showTab('recs')">💡 Рекомендации</button>
  </div>

  <!-- Сводка -->
  <div class="tab-pane" id="pane-summary">{summary_html}{done_html}</div>

  <!-- Воронка -->
  <div class="tab-pane" id="pane-funnel">{funnel_html}</div>

  <!-- Реклама -->
  <div class="tab-pane" id="pane-ads">{ads_html}</div>

  <!-- Юнит -->
  <div class="tab-pane" id="pane-unit">{unit_html}</div>

  <!-- Рекомендации -->
  <div class="tab-pane" id="pane-recs">{recs_html}</div>

</div>
<script>{JS}</script>
</body>
</html>"""


# ─── Сводка ───────────────────────────────────────────────────────────────────

def _render_summary(data: dict, unit_data: list) -> str:
    kpi = data["kpi"]
    f   = data["funnel"]
    buyout_norm = _detect_buyout_norm(data["products"])

    # avg margin from unit_data
    margins = []
    for row in unit_data:
        u = _calc_unit(row)
        if u["net_price"] > 0:
            margins.append(u["margin_pct"])
    avg_margin = sum(margins) / len(margins) if margins else None

    spend = kpi["spend"]
    rev   = kpi["revenue"]
    orders= kpi["orders"]
    drr   = kpi["drr"]
    ctr   = kpi.get("ctr", 0) or 0
    cpo   = spend / orders if orders else 0

    views = f.get("visits") or f.get("views") or 0
    basket= f.get("basket") or 0
    b_prev= f.get("basket_prev") or 0
    o_prev= f.get("orders_prev") or 0
    v_prev= f.get("views_prev") or 0
    buyout= f.get("buyout_pct") or 0

    rev_prev = kpi.get("revenue_prev") or 0
    ord_prev = kpi.get("orders_prev") or 0
    views_prev = kpi.get("views_prev") or 0

    drr_cls  = {"good":"green","ok":"orange","bad":"red","na":""}.get(
        "good" if drr and drr<5 else "ok" if drr and drr<10 else "bad" if drr else "na", "")
    margin_color = ("green" if avg_margin and avg_margin >= 25
                    else "orange" if avg_margin and avg_margin >= 10
                    else "red" if avg_margin is not None else "")

    # sections
    sections = []

    # Воронка
    ctr_f  = (basket / views * 100) if views else 0
    cr_f   = (orders / basket * 100) if basket else 0
    sections.append(f"""
    <div class="sec">Воронка продаж</div>
    <div class="kgrid kgrid-4">
      {_kcard("Переходы в карточку", fmt_int(views), delta_badge(views, views_prev), "")}
      {_kcard("Добавили в корзину",   fmt_int(basket), delta_badge(basket, b_prev), f"CTR {ctr_f:.1f}%")}
      {_kcard("Заказов",    fmt_int(orders) + " шт", delta_badge(orders, ord_prev), "")}
      {_kcard("% Выкупа",   fmt_pct(buyout, 0), _buyout_delta_badge(f),
              f"норма {buyout_norm[0]}–{buyout_norm[1]}%",
              accent={"good":"green","ok":"orange","bad":"red"}.get(_buyout_status(buyout, buyout_norm),""))}
    </div>
    """)

    # Реклама
    sections.append(f"""
    <div class="sec">Реклама</div>
    <div class="kgrid kgrid-4">
      {_kcard("Расход",           fmt_money(spend),     '<span class="badge nt">расход на рекламу</span>', "")}
      {_kcard("ДРР по заказам",   fmt_pct(drr),         _drr_badge(drr), "расход / выручка заказов", accent=drr_cls)}
      {_kcard("CPO (цена заказа)", fmt_money(cpo) if cpo else "—", '<span class="badge nt">расход / заказы</span>', "")}
      {_kcard("CTR",              fmt_pct(ctr) if ctr else "—", '<span class="badge nt">клики / показы</span>', "")}
    </div>
    """)

    # Юнит / итог
    stock = kpi.get("stock_total", 0) or 0
    fp    = kpi.get("sales_for_pay", 0) or 0
    drr_s = kpi.get("drr_sales") or 0
    margin_val = fmt_pct(avg_margin) if avg_margin is not None else "нет данных"
    sections.append(f"""
    <div class="sec">Итоги кабинета</div>
    <div class="kgrid kgrid-4">
      {_kcard("Выручка заказов",  fmt_money(rev), delta_badge(rev, rev_prev), "", accent="green" if rev > rev_prev else "")}
      {_kcard("К получению",      fmt_money(fp) if fp else "—", '<span class="badge nt">фактические выплаты</span>', "")}
      {_kcard("Остаток на складе",fmt_int(stock) + " шт" if stock else "—", "", "в наличии")}
      {_kcard("Средняя маржа",    margin_val, "", "по данным юнит-экономики", accent=margin_color)}
    </div>
    """)

    ins = _build_insights(data, unit_data)
    ins_html = f"""
    <div class="sec">Выводы недели</div>
    <div class="ins-grid">
      {_ins_card("🟢 Что улучшается", ins["leaders"])}
      {_ins_card("🔴 Главные проблемы", ins["problems"])}
    </div>
    """

    return "".join(sections) + ins_html


def _kcard(label, val, badge_html, sub, accent=""):
    cls = f" {accent}" if accent else ""
    return f"""
    <div class="kcard{cls}">
      <div class="kc-label">{label}</div>
      <div class="kc-val">{val}</div>
      {badge_html}
      {f'<div class="kc-sub">{sub}</div>' if sub else ''}
    </div>"""

def _drr_badge(drr):
    if not drr: return '<span class="badge nt">нет данных</span>'
    if drr < 5:   return '<span class="badge up">✦ отличный</span>'
    if drr < 10:  return '<span class="badge warn">средний</span>'
    return '<span class="badge dn">высокий</span>'

def _buyout_delta_badge(f):
    cur  = f.get("buyout_pct") or 0
    prev = f.get("buyout_pct_prev") or 0
    if not prev: return ""
    d = cur - prev
    if d > 0: return f'<span class="badge up">▲ +{d:.0f}пп</span>'
    if d < 0: return f'<span class="badge dn">▼ {d:.0f}пп</span>'
    return '<span class="badge nt">= 0</span>'

def _ins_card(title, items):
    rows = ""
    for i, it in enumerate(items, 1):
        cls_map = {"ok":"n-ok","warn":"n-warn","urgent":"n-bad","info":"n-info"}
        nc = cls_map.get(it.get("num","info"), "n-info")
        lbl = "!" if it.get("num") == "urgent" else str(i)
        rows += (f'<div class="ins-row">'
                 f'<span class="ins-num {nc}">{lbl}</span>'
                 f'<span class="ins-txt">{it["text"]}</span>'
                 f'</div>')
    return f'<div class="ins-card"><h3>{title}</h3>{rows}</div>'


# ─── Воронка tab ──────────────────────────────────────────────────────────────

def _render_funnel_tab(data: dict) -> str:
    products = data["products"]
    buyout_norm = _detect_buyout_norm(products)
    products = sorted(
        [p for p in products if _g(p,"orderCount","selected") >= 1 or _g(p,"orderCount","past") >= 1],
        key=lambda p: _g(p,"orderSum","selected"), reverse=True
    )

    spend_by_kw = {}
    for adv_id, cell in data["campaign_spend"].items():
        name = (cell.get("campName") or "").upper()
        spend_by_kw[name] = spend_by_kw.get(name, 0.0) + cell["sum"]

    sales_by_v    = data.get("sales_by_vendor") or {}
    sales_prev_v  = data.get("sales_by_vendor_prev") or {}
    stock_by_v    = data.get("stock_by_vendor") or {}

    counts = {"all": 0, "growth": 0, "stable": 0, "decline": 0, "alert": 0}
    cards  = []
    for p in products:
        prod    = p.get("product") or {}
        nm_id   = prod.get("nmId") or prod.get("nmID") or ""
        name    = prod.get("title") or prod.get("subjectName") or prod.get("brandName") or ""
        vendor  = prod.get("vendorCode") or ""

        orders      = _g(p, "orderCount", "selected")
        orders_prev = _g(p, "orderCount", "past")
        revenue     = _g(p, "orderSum",   "selected")
        clicks_p    = _g(p, "openCardCount", "selected") or 0   # переходы в карточку
        basket_p    = _g(p, "addToCartCount", "selected") or 0

        buyouts     = int(sales_by_v.get(str(vendor), 0))
        buyout_pct  = (buyouts / orders * 100) if orders else 0
        stock       = int(stock_by_v.get(str(vendor), 0))

        spend_art = 0.0
        if vendor:
            v_up = vendor.upper()
            for kw, val in spend_by_kw.items():
                if v_up in kw:
                    spend_art += val
        drr_art = (spend_art / revenue * 100) if revenue and spend_art else 0

        status = _art_status(p)
        counts["all"] += 1
        counts[status] = counts.get(status, 0) + 1

        d = _delta_orders(p)
        if d is None:
            d_badge = '<span class="badge nt">нет пред.</span>'
        elif d >= 999:
            # prev=0, cur>0 — новый артикул в периоде
            d_badge = '<span class="badge nt">new</span>'
        elif d >= 500:
            # артефакт малой базы — не выделяем как рост
            d_badge = f'<span class="badge nt">▲ {d:.0f}%*</span>'
        else:
            d_badge = (f'<span class="badge up">▲ {d:+.0f}%</span>' if d >= 0
                       else f'<span class="badge dn">▼ {d:.0f}%</span>')

        ctr_p = (basket_p / clicks_p * 100) if clicks_p else 0
        cr_p  = (orders   / basket_p * 100) if basket_p else 0
        b_status = _buyout_status(buyout_pct, buyout_norm)
        b_color  = {"good":"var(--ok)","ok":"var(--warn)","bad":"var(--danger)"}.get(b_status,"var(--txt2)")

        # Строим воронку только из шагов с ненулевыми данными
        fsteps = []
        if clicks_p > 0:
            fsteps.append(
                f'<div class="fstep"><div class="fstep-val">{_short_num(clicks_p)}</div>'
                f'<div class="fstep-lbl">клики</div></div>'
                f'<div class="fstep-arrow">›</div>'
            )
        if basket_p > 0:
            ctr_lbl = f'<div class="fconv">{ctr_p:.1f}%</div>' if clicks_p > 0 else ""
            fsteps.append(
                f'<div class="fstep"><div class="fstep-val">{_short_num(basket_p)}</div>'
                f'<div class="fstep-lbl">корзина</div>{ctr_lbl}</div>'
                f'<div class="fstep-arrow">›</div>'
            )
        cr_lbl = f'<div class="fconv">{cr_p:.1f}%</div>' if basket_p > 0 else ""
        fsteps.append(
            f'<div class="fstep"><div class="fstep-val">{fmt_int(orders)}</div>'
            f'<div class="fstep-lbl">заказ</div>{cr_lbl}</div>'
            f'<div class="fstep-arrow">›</div>'
        )
        fsteps.append(
            f'<div class="fstep"><div class="fstep-val" style="color:{b_color}">{buyout_pct:.0f}%</div>'
            f'<div class="fstep-lbl">выкуп</div>'
            f'<div class="fconv" style="color:{b_color}">{buyout_norm[0]}–{buyout_norm[1]}%</div>'
            f'</div>'
        )

        funnel_steps = (
            f'<div class="art-funnel-label">Конверсия воронки</div>'
            f'<div class="funnel-steps">{"".join(fsteps)}</div>'
        )

        sku_inner = f'<span class="art-sku">{_esc.escape(str(vendor or nm_id))}</span>'
        if nm_id:
            sku_inner = (f'<a class="sku-link" href="https://www.wildberries.ru/catalog/{nm_id}/detail.aspx" '
                         f'target="_blank" rel="noopener">{sku_inner}</a>')

        footer_parts = []
        if stock:
            footer_parts.append(f'<span class="badge nt">📦 {fmt_int(stock)} шт</span>')
        if spend_art:
            dcls = {"good":"up","ok":"warn","bad":"dn","na":"nt"}.get(
                "good" if drr_art<5 else "ok" if drr_art<10 else "bad", "dn")
            footer_parts.append(f'<span class="badge {dcls}">ДРР {drr_art:.1f}%</span>')
        if orders_prev:
            footer_parts.append(f'<span class="badge nt">пред: {fmt_int(orders_prev)} зак</span>')

        bar_pct = min(100, buyout_pct)
        cards.append(f"""
        <div class="art-card {status}" data-status="{status}" data-sku="{_esc.escape(str(vendor))}" data-name="{_esc.escape(name[:80])}">
          <div class="art-head">
            <div>
              {sku_inner}
              <div class="art-name">{_esc.escape(name[:70])}</div>
            </div>
            <div class="art-status {status}"></div>
          </div>
          <div class="art-metrics">
            <div class="art-m">
              <div class="lbl">Заказы</div>
              <div class="val">{fmt_int(orders)} {d_badge}</div>
              <div class="sub">{fmt_money(revenue)}</div>
            </div>
            <div class="art-m">
              <div class="lbl">Выкуп</div>
              <div class="val">{buyout_pct:.0f}%</div>
              <div class="sub">{fmt_int(buyouts)} шт</div>
            </div>
          </div>
          <div class="art-funnel">{funnel_steps}</div>
          <div class="art-bar">
            <div class="bar-track"><div class="bar-fill" style="width:{bar_pct:.0f}%"></div></div>
          </div>
          <div class="art-footer">{' '.join(footer_parts)}</div>
        </div>
        """)

    filter_btns = ""
    for key in ["all", "growth", "stable", "decline", "alert"]:
        cnt = counts.get(key, 0)
        label = STATUS_LABEL[key]
        filter_btns += f'<button class="fbtn" data-f="{key}" onclick="funnelFilter(this,\'{key}\')">{label} ({cnt})</button>'

    return f"""
    <div class="fstrip">
      {filter_btns}
      <input class="fsearch" id="funnel-search" type="text" placeholder="Поиск по артикулу...">
    </div>
    <div class="art-grid">{''.join(cards) if cards else '<div style="color:var(--txt2);padding:24px">Нет данных по артикулам за период.</div>'}</div>
    """


def _short_num(v):
    if not v: return "0"
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000: return f"{v/1_000:.0f}K"
    return str(int(v))


# ─── Реклама tab ──────────────────────────────────────────────────────────────

def _render_ads_tab(data: dict) -> str:
    spend  = data["campaign_spend"]
    stats  = data["campaign_stats"]

    if not spend:
        return '<div style="color:var(--txt2);padding:24px">Нет активных кампаний за период.</div>'

    items = sorted(spend.items(), key=lambda kv: kv[1]["sum"], reverse=True)

    total_spend = total_views = total_orders_est = 0.0
    rows = []
    for adv_id, cell in items:
        s     = stats.get(adv_id, {})
        views = float(s.get("views") or 0)
        ctr   = float(s.get("ctr") or 0)
        clicks= float(s.get("clicks") or 0)
        a_type_id = cell.get("advertType")
        a_type = ADVERT_TYPE.get(a_type_id, "—")
        a_status = str(cell.get("advertStatus") or "").lower()
        tag_cls = ("ct-auto" if a_type_id == 8
                   else "ct-search" if a_type_id in (6, 9)
                   else "ct-ark")
        ctr_pill = (f'<span class="pill {("good" if ctr>=4.5 else "ok" if ctr>=3 else "bad")}">{ctr:.2f}%</span>'
                    if ctr else '<span class="dim">—</span>')
        cpc = (cell["sum"] / clicks) if clicks else 0
        is_active  = a_status in ("9", "активный", "active")
        status_dot = "🟢" if is_active else "🔴"
        row_status = "active" if is_active else "paused"
        row_type   = ("ark" if tag_cls == "ct-ark"
                      else "search" if tag_cls == "ct-search" else "auto")
        views_cell  = fmt_int(views) if views else '<span class=dim>—</span>'
        clicks_cell = fmt_int(clicks) if clicks else '<span class=dim>—</span>'
        cpc_cell    = fmt_money(cpc) if cpc else '<span class=dim>—</span>'
        rows.append((
            f'<tr data-status="{row_status}" data-type="{row_type}">'
            f'<td><span class="sku-code">{_esc.escape(str(cell.get("campName") or "—"))}</span>'
            f'<span class="sku-name">#{adv_id}</span></td>'
            f'<td><span class="camp-tag {tag_cls}">{a_type}</span></td>'
            f'<td class="dim">{status_dot}</td>'
            f'<td class="r">{fmt_money(cell["sum"])}</td>'
            f'<td class="r">{views_cell}</td>'
            f'<td class="r">{ctr_pill}</td>'
            f'<td class="r">{clicks_cell}</td>'
            f'<td class="r">{cpc_cell}</td>'
            f'</tr>'
        ))
        total_spend += cell["sum"]
        total_views += views

    filter_btns = (
        '<button class="fbtn" data-af="all"    onclick="adsFilter(this,\'all\')">Все</button>'
        '<button class="fbtn" data-af="active" onclick="adsFilter(this,\'active\')">Активные</button>'
        '<button class="fbtn" data-af="ark"    onclick="adsFilter(this,\'ark\')">АРК</button>'
        '<button class="fbtn" data-af="search" onclick="adsFilter(this,\'search\')">Поиск</button>'
        '<button class="fbtn" data-af="auto"   onclick="adsFilter(this,\'auto\')">Авто</button>'
    )

    summary_cards = f"""
    <div class="kgrid kgrid-3" style="margin-bottom:20px">
      {_kcard("Общий расход", fmt_money(total_spend), "", f"{len(spend)} кампаний")}
      {_kcard("Показов всего", fmt_int(total_views) if total_views else "—", "", "")}
      {_kcard("ДРР кабинета", fmt_pct(data['kpi']['drr']), _drr_badge(data['kpi']['drr']), "расход / выручка заказов")}
    </div>
    """

    return f"""
    {summary_cards}
    <div class="sec">Рекламные кампании</div>
    <div class="fstrip">{filter_btns}</div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>Кампания</th><th>Тип</th><th>Статус</th>
            <th class="r">Расход ₽</th><th class="r">Показы</th>
            <th class="r">CTR</th><th class="r">Клики</th><th class="r">CPC ₽</th>
          </tr>
        </thead>
        <tbody id="ads-tbody">
          {''.join(rows)}
          <tr class="tot-row">
            <td colspan="3"><strong>ИТОГО</strong></td>
            <td class="r">{fmt_money(total_spend)}</td>
            <td class="r">{fmt_int(total_views) if total_views else "—"}</td>
            <td class="r">—</td><td class="r">—</td><td class="r">—</td>
          </tr>
        </tbody>
      </table>
    </div>
    """


# ─── Юнит tab ─────────────────────────────────────────────────────────────────

def _render_unit_tab(unit_data: list, wb_data: dict) -> str:
    if not unit_data:
        return '<div style="color:var(--txt2);padding:24px">Нет данных юнит-экономики для этого кабинета. Добавьте артикулы в раздел «Юнит-экономика».</div>'

    rows = []
    profits = []
    margins = []
    for row in unit_data:
        u = _calc_unit(row)
        net = u["net_price"]
        if not net:
            continue
        m = u["margin_pct"]
        margins.append(m)
        profits.append(u["profit"])

        wb_art = row.get("wb_article") or ""
        s_art  = row.get("seller_article") or row.get("brand") or ""
        price  = row.get("wb_price") or 0
        spp    = row.get("spp_pct") or 0

        m_cls = ("good" if m >= 25 else "ok" if m >= 10 else "bad" if m >= 0 else "urgent")
        health_icon = "✅" if m >= 25 else "🟡" if m >= 10 else "🔴" if m >= 0 else "💀"
        health_lbl  = ("Прибыльно" if m >= 25 else "Умеренно" if m >= 10
                       else "Мало" if m >= 0 else "Убыток")

        sku_inner = (f'<span class="sku-code">{_esc.escape(str(wb_art))}</span>'
                     f'<span class="sku-name">{_esc.escape(str(s_art)[:50])}</span>')
        if wb_art:
            sku_inner = (f'<a class="sku-link" href="https://www.wildberries.ru/catalog/{wb_art}/detail.aspx" '
                         f'target="_blank" rel="noopener">{sku_inner}</a>')

        rows.append(f"""
        <tr data-margin="{m:.2f}">
          <td>{sku_inner}</td>
          <td class="r">{fmt_money(net)}</td>
          <td class="r dim">{fmt_money(price)} / {spp:.0f}%</td>
          <td class="r">{fmt_money(u['cost_price'])}</td>
          <td class="r">{fmt_money(u['commission'])}</td>
          <td class="r">{fmt_money(u['logistics'])}</td>
          <td class="r">{fmt_money(u['drr_rub'])}</td>
          <td class="r">{fmt_money(u['tax_rub'])}</td>
          <td class="r"><strong>{fmt_money(u['profit'])}</strong></td>
          <td class="r"><span class="pill {m_cls}">{m:.1f}%</span></td>
          <td><span class="health {m_cls}">{health_icon} {health_lbl}</span></td>
        </tr>
        """)

    if not rows:
        return '<div style="color:var(--txt2);padding:24px">Нет данных для расчёта.</div>'

    avg_m  = sum(margins) / len(margins)
    avg_p  = sum(profits) / len(profits)
    pos_m  = sum(1 for m in margins if m > 0)
    good_m = sum(1 for m in margins if m >= 25)

    summary = f"""
    <div class="kgrid kgrid-4" style="margin-bottom:20px">
      {_kcard("Средняя маржа", fmt_pct(avg_m), "", f"{len(margins)} артикулов",
              accent="green" if avg_m>=25 else "orange" if avg_m>=10 else "red")}
      {_kcard("Средняя прибыль", fmt_money(avg_p), "", "с единицы товара")}
      {_kcard("Прибыльных", f"{pos_m} шт",  "", f"из {len(margins)} артикулов")}
      {_kcard("Маржа ≥25%",  f"{good_m} шт", "", "здоровые артикулы", accent="green" if good_m>0 else "")}
    </div>
    """

    filter_btns = (
        '<button class="fbtn" data-mf="all" onclick="unitFilter(this,\'all\')">Все</button>'
        '<button class="fbtn" data-mf="25"  onclick="unitFilter(this,\'25\')">Маржа ≥25%</button>'
        '<button class="fbtn" data-mf="10"  onclick="unitFilter(this,\'10\')">10–25%</button>'
        '<button class="fbtn" data-mf="0"   onclick="unitFilter(this,\'0\')">0–10%</button>'
        '<button class="fbtn" data-mf="neg" onclick="unitFilter(this,\'neg\')">Убыток</button>'
    )

    return f"""
    {summary}
    <div class="sec">Юнит-экономика по артикулам</div>
    <div class="fstrip">{filter_btns}</div>
    <div class="tbl-wrap">
      <table style="min-width:900px">
        <thead><tr>
          <th>Артикул</th>
          <th class="r">Цена нетто</th><th class="r">Цена / СПП</th>
          <th class="r">Себест.</th><th class="r">Комиссия</th>
          <th class="r">Логист.</th><th class="r">Реклама</th>
          <th class="r">Налог</th><th class="r">Прибыль</th>
          <th class="r">Маржа</th><th>Статус</th>
        </tr></thead>
        <tbody id="unit-tbody">{''.join(rows)}</tbody>
      </table>
    </div>
    """


# ─── Рекомендации ─────────────────────────────────────────────────────────────

def _render_recs_tab(data: dict, unit_data: list) -> str:
    recs = _build_recs(data, unit_data)
    items = []
    for r in recs:
        items.append(f"""
        <div class="rec-item {r['lvl']}">
          <div class="rec-icon">{r['icon']}</div>
          <div class="rec-body">
            <b>{_esc.escape(r['title'])}</b>
            <span>{_esc.escape(r['body'])}</span>
          </div>
        </div>
        """)
    if not items:
        items.append('<div style="color:var(--txt2);padding:24px">Рекомендаций нет.</div>')
    return f'<div class="sec">Рекомендации по результатам периода</div><div class="rec-list">{"".join(items)}</div>'


def _build_recs(data: dict, unit_data: list) -> list:
    recs = []
    kpi  = data["kpi"]
    prods = data["products"]
    f    = data["funnel"]
    buyout_norm = _detect_buyout_norm(prods)
    norm_lo, norm_hi = buyout_norm

    drr  = kpi.get("drr") or 0
    rev  = kpi.get("revenue") or 0
    rev_prev = kpi.get("revenue_prev") or 0
    orders   = kpi.get("orders") or 0
    buyout   = (f.get("buyout_pct") or 0)
    buyout_prev = (f.get("buyout_pct_prev") or 0)

    # ДРР
    if drr > 15:
        recs.append({"lvl":"urgent","icon":"🚨","title":f"ДРР {drr:.1f}% — критически высокий",
                     "body":"Снизить ставки в наиболее дорогих кампаниях, оптимизировать бюджет."})
    elif drr > 10:
        recs.append({"lvl":"warn","icon":"⚠️","title":f"ДРР {drr:.1f}% — выше нормы",
                     "body":"Проверить эффективность кампаний с высоким расходом и низким выхлопом."})
    elif 0 < drr < 5:
        recs.append({"lvl":"ok","icon":"✅","title":f"ДРР {drr:.1f}% — отличный результат",
                     "body":"Масштабируйте рабочие кампании при наличии складских остатков."})

    # выкуп — vs норма категории
    if buyout >= norm_hi and orders > 5:
        recs.append({"lvl":"ok","icon":"✅","title":f"Выкуп {buyout:.0f}% — выше нормы ({norm_lo}–{norm_hi}%)",
                     "body":"Отличный показатель для категории. Следите за остатками."})
    elif buyout < norm_lo and orders > 10:
        recs.append({"lvl":"warn","icon":"📉","title":f"Выкуп {buyout:.0f}% — ниже нормы ({norm_lo}–{norm_hi}%)",
                     "body":"Проверить фотографии, размерную сетку, описание и отзывы товаров."})
    if buyout_prev and (buyout - buyout_prev) < -5:
        recs.append({"lvl":"warn","icon":"📉","title":f"Выкуп снизился с {buyout_prev:.0f}% до {buyout:.0f}%",
                     "body":"Проанализировать причины возвратов. Проверить карточки товаров."})

    # выручка
    if rev_prev and rev < rev_prev * 0.85:
        d = (rev - rev_prev) / rev_prev * 100
        recs.append({"lvl":"urgent","icon":"📉","title":f"Выручка упала на {abs(d):.0f}%",
                     "body":"Проверить наличие остатков, ставки рекламы, позиции в поиске."})
    elif rev_prev and rev > rev_prev * 1.15:
        d = (rev - rev_prev) / rev_prev * 100
        recs.append({"lvl":"ok","icon":"📈","title":f"Выручка выросла на {d:.0f}%",
                     "body":"Проверьте хватит ли остатков поддерживать темп. При ДРР < 5% — масштабируйте."})

    # Лидеры роста (до +500% — выше считаем артефактом малой базы)
    sorted_growth = sorted(prods, key=lambda p: (_delta_orders(p) or 0), reverse=True)
    for p in sorted_growth[:2]:
        d = _delta_orders(p)
        if d is None or d < 20 or d >= 500: continue
        prod = p.get("product") or {}
        vendor = prod.get("vendorCode") or prod.get("nmId") or "артикул"
        recs.append({"lvl":"ok","icon":"🚀","title":f"{_esc.escape(str(vendor))}: рост заказов {d:+.0f}%",
                     "body":"Убедитесь что хватает остатков и рекламный бюджет не ограничен."})

    # Падения
    for p in sorted(prods, key=lambda p: (_delta_orders(p) or 0))[:2]:
        d = _delta_orders(p)
        if d is None or d > -25: continue
        prod = p.get("product") or {}
        vendor = prod.get("vendorCode") or prod.get("nmId") or "артикул"
        orders_n = _g(p,"orderCount","selected")
        recs.append({"lvl":"warn","icon":"⚠️","title":f"{_esc.escape(str(vendor))}: заказы {d:.0f}%",
                     "body":f"Текущая неделя: {fmt_int(orders_n)} заказов. Проверить ставки и карточку."})

    # Юнит: убыточные
    for row in unit_data:
        u = _calc_unit(row)
        if u["net_price"] > 0 and u["margin_pct"] < 0:
            art = row.get("seller_article") or row.get("wb_article") or "артикул"
            recs.append({"lvl":"urgent","icon":"💀","title":f"{art}: убыточный товар (маржа {u['margin_pct']:.1f}%)",
                         "body":"Пересмотреть ценообразование или себестоимость. Прекратить рекламу."})
        elif u["net_price"] > 0 and u["margin_pct"] < 10:
            art = row.get("seller_article") or row.get("wb_article") or "артикул"
            recs.append({"lvl":"warn","icon":"🟡","title":f"{art}: низкая маржа {u['margin_pct']:.1f}%",
                         "body":"Рассмотреть повышение цены или снижение затрат."})

    # Стоки
    stock_total = kpi.get("stock_total") or 0
    if stock_total < 20 and orders > 5:
        recs.append({"lvl":"urgent","icon":"📦","title":f"Критически мало остатков: {fmt_int(stock_total)} шт",
                     "body":"Срочно пополнить склад чтобы не потерять позиции."})

    # Стандартные советы
    recs.append({"lvl":"info","icon":"💡","title":"Сравнить с прошлой неделей по топ-5 артикулам",
                 "body":"Принять решения по рекламным бюджетам на следующую неделю."})
    recs.append({"lvl":"info","icon":"💡","title":"Проверить карточки с CTR < 3%",
                 "body":"Обновить главное фото, A/B тест на изображении."})

    return recs


# ─── insights для Сводки ──────────────────────────────────────────────────────

def _build_insights(data: dict, unit_data: list) -> dict:
    kpi   = data["kpi"]
    prods = data["products"]
    f     = data["funnel"]
    buyout_norm = _detect_buyout_norm(prods)
    norm_lo, norm_hi = buyout_norm

    leaders = []
    sorted_g = sorted(prods, key=lambda p: (_delta_orders(p) or 0), reverse=True)
    for p in sorted_g[:3]:
        d = _delta_orders(p)
        if d is None or d <= 0 or d >= 500: continue
        prod   = p.get("product") or {}
        vendor = prod.get("vendorCode") or prod.get("nmId") or "артикул"
        leaders.append({"num":"ok",
            "text": f"<strong>{_esc.escape(str(vendor))}</strong> — рост заказов {d:+.0f}%."})

    rev_d = (kpi["revenue"] - kpi["revenue_prev"]) / kpi["revenue_prev"] * 100 if kpi.get("revenue_prev") else None
    if rev_d is not None and rev_d > 10:
        leaders.append({"num":"ok","text": f"Выручка выросла на <strong>{rev_d:+.0f}%</strong> vs прошлый период."})

    buyout = f.get("buyout_pct") or 0
    if buyout >= norm_hi and kpi.get("orders", 0) > 5:
        leaders.append({"num":"ok","text": f"Выкуп <strong>{buyout:.0f}%</strong> — выше нормы ({norm_lo}–{norm_hi}%) для категории."})

    avg_m_list = [_calc_unit(r)["margin_pct"] for r in unit_data if _calc_unit(r)["net_price"] > 0]
    if avg_m_list:
        avg_m = sum(avg_m_list) / len(avg_m_list)
        if avg_m >= 25:
            leaders.append({"num":"ok","text": f"Средняя маржа кабинета <strong>{avg_m:.1f}%</strong> — здоровая."})

    problems = []
    drr = kpi.get("drr") or 0
    if drr > 15:
        problems.append({"num":"urgent","text": f"<strong>ДРР {drr:.1f}%</strong> — критически высокий! Снизить ставки."})
    elif drr > 10:
        problems.append({"num":"urgent","text": f"<strong>ДРР {drr:.1f}%</strong> — выше нормы. Оптимизировать кампании."})

    if rev_d is not None and rev_d < -10:
        problems.append({"num":"warn","text": f"Выручка упала на <strong>{abs(rev_d):.0f}%</strong> vs прошлой недели."})

    buyout_prev = f.get("buyout_pct_prev") or 0
    if buyout < norm_lo and kpi.get("orders", 0) > 5:
        problems.append({"num":"warn","text": f"Выкуп <strong>{buyout:.0f}%</strong> ниже нормы ({norm_lo}–{norm_hi}%). Проверить карточки."})

    for p in sorted(prods, key=lambda p: (_delta_orders(p) or 0))[:2]:
        d = _delta_orders(p)
        if d is None or d > -25: continue
        prod = p.get("product") or {}
        vendor = prod.get("vendorCode") or prod.get("nmId") or "артикул"
        problems.append({"num":"warn","text": f"<strong>{_esc.escape(str(vendor))}</strong>: заказы {d:.0f}%."})

    if avg_m_list and sum(avg_m_list) / len(avg_m_list) < 10:
        problems.append({"num":"warn","text": "Средняя маржа ниже 10%. Пересмотреть ценообразование."})

    stock = kpi.get("stock_total") or 0
    if stock < 20 and kpi.get("orders", 0) > 5:
        problems.append({"num":"urgent","text": f"Критически мало остатков: <strong>{fmt_int(stock)} шт</strong>."})

    return {
        "leaders":  leaders  or [{"num":"info","text":"Данных для выводов о росте недостаточно."}],
        "problems": problems or [{"num":"info","text":"Критичных проблем не обнаружено."}],
    }


# ─── done tasks ───────────────────────────────────────────────────────────────

def _render_done_tasks(tasks: list) -> str:
    if not tasks:
        return ""
    rows = "".join(
        f'<div class="dt-row">'
        f'<span class="dt-date">{_esc.escape(t["done_at"])}</span>'
        f'<span class="dt-title">{_esc.escape(t["title"])}</span>'
        f'{"<span class=dt-who>@" + _esc.escape(t["assignee"]) + "</span>" if t.get("assignee") else ""}'
        f'</div>'
        for t in tasks
    )
    return f"""
    <div class="sec" style="margin-top:28px">Выполненные задачи за период</div>
    <div class="dt-list">{rows}</div>
    """
