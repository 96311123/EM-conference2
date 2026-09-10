#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render.py — 把爬到的年會資料算成 (1) 一頁儀表板 HTML (2) 一個 .ics 行事曆檔。

儀表板的主角是「季節格線」：橫軸十二個月、縱軸年份，一眼就看得出
四大學會的年度節奏（SAEM 五月、IFEM 六月、EuSEM 九月底、ACEP 十月），
這正是排投稿死線與出國預算時真正需要知道的事。
"""

from __future__ import annotations

import hashlib
import html
import json
from datetime import date, datetime, timezone
from pathlib import Path

SOCIETY_META = {
    "ACEP":  {"full": "American College of Emergency Physicians",
              "zh": "美國急診醫師學會",
              "meeting": "Scientific Assembly", "hue": "acep"},
    "SAEM":  {"full": "Society for Academic Emergency Medicine",
              "zh": "美國學術急診醫學會",
              "meeting": "Annual Meeting", "hue": "saem"},
    "IFEM":  {"full": "International Federation for Emergency Medicine",
              "zh": "國際急診醫學聯盟",
              "meeting": "Global Congress / ICEM", "hue": "ifem"},
    "EUSEM": {"full": "European Society for Emergency Medicine",
              "zh": "歐洲急診醫學會",
              "meeting": "European EM Congress", "hue": "eusem"},
    "ASEM":  {"full": "Asian Society for Emergency Medicine",
              "zh": "亞洲急診醫學會",
              "meeting": "Asian Conference（ACEM，兩年一次）", "hue": "asem"},
    "SEMS":  {"full": "Society for Emergency Medicine in Singapore",
              "zh": "新加坡急診",
              "meeting": "Annual Scientific Meeting", "hue": "sems"},
    "HKCEM": {"full": "Hong Kong College of Emergency Medicine",
              "zh": "香港急症科醫學院",
              "meeting": "學術活動", "hue": "hkcem"},
}

# 七個學會都有 logo 圖檔了
HAS_LOGO = {"acep", "saem", "ifem", "eusem", "asem", "sems", "hkcem"}


def zh_name(code: str) -> str:
    return SOCIETY_META.get(code, {}).get("zh", "")


def society_label(code: str) -> str:
    """『SEMS 新加坡急診』這種並排標示，中英文都給。"""
    zh = zh_name(code)
    return f"{code} {zh}" if zh else code



def legend_mark(code: str, hue: str) -> str:
    if hue in HAS_LOGO:
        return f'<img src="assets/{hue}.png" alt="{code}" loading="lazy">'
    return f"<b>{html.escape(code)}</b>"


def band(society: str) -> str:
    """卡片頂端：有 logo 就放圖，沒有就放學會縮寫色塊。"""
    hue = SOCIETY_META.get(society, {}).get("hue", "")
    if hue in HAS_LOGO:
        return (f'<img src="assets/{hue}.png" alt="{html.escape(society_label(society))}" '
                f'loading="lazy" width="360">')
    return f'<b class="{hue}">{html.escape(society)}</b>'

MONTH_ABBR = ["一", "二", "三", "四", "五", "六",
              "七", "八", "九", "十", "十一", "十二"]


# --------------------------------------------------------------------------
# 資料整理
# --------------------------------------------------------------------------

def _d(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def prepare(rows: list[dict]) -> list[dict]:
    """補上排序鍵、月份、是否已過期。無確切日期者用 date_text 的月份估位。"""
    out = []
    for r in rows:
        if r.get("name") == "(fetch failed)":
            continue
        start, end = _d(r.get("start", "")), _d(r.get("end", ""))
        month = start.month if start else _month_from_text(r.get("date_text", ""))
        r = dict(r)
        r["_start"] = start
        r["_end"] = end or start
        r["_month"] = month
        r["_confirmed"] = bool(start)
        r["_sort"] = (r.get("year") or 9999, month or 13)
        out.append(r)
    out.sort(key=lambda x: x["_sort"])
    return out


def _month_from_text(text: str) -> int | None:
    names = ["jan", "feb", "mar", "apr", "may", "jun",
             "jul", "aug", "sep", "oct", "nov", "dec"]
    low = (text or "").lower()
    for i, n in enumerate(names, start=1):
        if n in low:
            return i
    return None


def split_archive(rows: list[dict], today: date) -> tuple[list[dict], list[dict]]:
    """
    主頁只放今年與之後的場次；今年一月一日以前就結束的移到封存頁。
    沒有日期的（例如只公布月份的 IFEM 2027）一律留在主頁——那是未來的事，
    不該被當成歷史。
    """
    cut = date(today.year, 1, 1)
    current = [r for r in rows if not r["_end"] or r["_end"] >= cut]
    archive = [r for r in rows if r["_end"] and r["_end"] < cut]
    return current, archive


def next_up(rows: list[dict], today: date) -> dict | None:
    future = [r for r in rows if r["_end"] and r["_end"] >= today]
    return future[0] if future else None


def assign_lanes(rows_in_year: list[dict]) -> dict[str, int]:
    """同一年同一個月有兩場會時，把第二場排到下一條 lane，避免疊在一起。"""
    lanes: list[set[int]] = []
    result = {}
    for r in rows_in_year:
        m = r["_month"] or 13
        for i, taken in enumerate(lanes):
            if m not in taken:
                taken.add(m)
                result[_key(r)] = i
                break
        else:
            lanes.append({m})
            result[_key(r)] = len(lanes) - 1
    return result


def _key(r: dict) -> str:
    return f'{r["society"]}-{r.get("year")}-{r.get("start") or r.get("date_text")}'


# --------------------------------------------------------------------------
# ICS 行事曆
# --------------------------------------------------------------------------

def build_ics(rows: list[dict], deadlines: list[dict] | None = None) -> str:
    """全天事件的 .ics。訂閱後年會就會自動出現在 Google Calendar 裡。"""
    def esc(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//EM Conference Tracker//TW//ZH",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:急診國際年會",
             "X-WR-TIMEZONE:Asia/Taipei"]
    for r in rows:
        if not r["_confirmed"]:
            continue                       # 日期未定的不進行事曆，免得誤導
        uid = hashlib.md5(_key(r).encode()).hexdigest() + "@em-conf-tracker"
        place = ", ".join(x for x in (r.get("city"), r.get("country_or_state")) if x)
        dtend = date.fromordinal(r["_end"].toordinal() + 1)   # DTEND 為排他性
        desc = f'{SOCIETY_META.get(r["society"], {}).get("full", r["society"])}'
        if r.get("venue"):
            desc += f'\\n會場：{r["venue"]}'
        if r.get("note"):
            desc += f'\\n備註：{r["note"]}'
        desc += f'\\n來源：{r.get("source_url","")}'
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            f'DTSTART;VALUE=DATE:{r["_start"].strftime("%Y%m%d")}',
            f'DTEND;VALUE=DATE:{dtend.strftime("%Y%m%d")}',
            f'SUMMARY:{esc(r["society"] + " — " + r.get("name", ""))}',
            f"LOCATION:{esc(place)}",
            f"DESCRIPTION:{desc}",
            f'URL:{r.get("source_url","")}',
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    # 死線事件：加 30／7／1 天三段提醒。這是整套工具最實際的產出——
    # 死線會主動在你的日曆上敲門，不必記得去看網頁。
    for d in (deadlines or []):
        if d.get("kind") == "open" or not d.get("date_iso"):
            continue
        if d.get("confidence") == "low":
            continue                       # 信心不足的不設提醒，避免假死線誤導
        try:
            day = date.fromisoformat(d["date_iso"])
        except ValueError:
            continue
        uid = hashlib.md5(
            f'dl-{d.get("society")}-{d.get("track")}-{d["date_iso"]}'.encode()
        ).hexdigest() + "@em-conf-tracker"
        title = f'死線：{d.get("society","")} {d.get("track","")}'
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            f'DTSTART;VALUE=DATE:{day.strftime("%Y%m%d")}',
            f'DTEND;VALUE=DATE:{date.fromordinal(day.toordinal() + 1).strftime("%Y%m%d")}',
            f"SUMMARY:{esc(title)}",
            f'DESCRIPTION:{esc(d.get("cycle",""))}\\n{esc((d.get("note") or "")[:150])}'
            f'\\n來源：{d.get("source_url","")}',
            f'URL:{d.get("source_url","")}',
            "TRANSP:TRANSPARENT",
        ]
        for trigger, label in (("-P30D", "30 天"), ("-P7D", "7 天"), ("-P1D", "1 天")):
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY",
                      f"TRIGGER:{trigger}",
                      f"DESCRIPTION:{esc(title)}（還有 {label}）",
                      "END:VALARM"]
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# --------------------------------------------------------------------------
# HTML 儀表板
# --------------------------------------------------------------------------

CSS = """
:root{
  --paper:#e8edef; --surface:#fdfdfc; --ink:#13222a; --muted:#5f747e;
  --rule:#c6d1d6; --rule-soft:#dde5e8;
  --acep:#9d3a2e; --saem:#2f6a56; --ifem:#8a6820; --eusem:#33518c;
  --asem:#5c4b8a; --hkcem:#a8562b; --sems:#1f6f78;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:Newsreader,"Iowan Old Style","Source Serif 4",Georgia,
              "Noto Serif TC","PingFang TC","Microsoft JhengHei",serif;
  font-size:17px; line-height:1.55; font-variant-numeric:tabular-nums;
}
.wrap{max-width:1080px; margin:0 auto; padding:2.5rem 1.5rem 5rem}
a{color:inherit; text-decoration-thickness:1px; text-underline-offset:2px}
a:focus-visible,summary:focus-visible{outline:2px solid var(--ink); outline-offset:3px}

.masthead{display:flex; justify-content:space-between; align-items:baseline;
  gap:1rem; flex-wrap:wrap; border-bottom:1px solid var(--ink); padding-bottom:.6rem}
.masthead h1{font-size:1.5rem; font-weight:600; margin:0; letter-spacing:.01em}
.masthead .stamp{color:var(--muted); font-size:.85rem}

.hero{margin:2.6rem 0 3rem}
.hero .lede{color:var(--muted); font-size:.95rem; margin:0 0 .5rem}
.hero .title{font-size:clamp(1.7rem,4.4vw,2.6rem); line-height:1.2;
  font-weight:300; margin:0 0 .7rem; max-width:22ch}
.hero .meta{font-size:1.05rem; margin:0}
.hero .count{color:var(--muted); font-size:.95rem; margin:.55rem 0 0}
.hero .count b{color:var(--ink); font-weight:600; font-size:1.25rem}

h2{font-size:1.05rem; font-weight:600; margin:0 0 1rem;
   padding-bottom:.35rem; border-bottom:1px solid var(--rule)}

/* 季節格線 */
.grid{background:var(--surface); border:1px solid var(--rule); padding:1rem .9rem 1.2rem;
      overflow-x:auto}
.gridrow{display:grid; grid-template-columns:3.4rem repeat(12,1fr);
  column-gap:2px; row-gap:3px; min-width:640px}
.months{margin-bottom:.4rem; border-bottom:1px solid var(--rule-soft); padding-bottom:.3rem}
.months span{font-size:.72rem; color:var(--muted); text-align:center}
.months span:first-child{text-align:left}
.yr{font-size:.9rem; color:var(--muted); align-self:center}
.yr.now{color:var(--ink); font-weight:600}
.bar{grid-row:auto; border-left:3px solid currentColor; background:#fff;
  padding:.18rem .35rem; font-size:.73rem; line-height:1.25; min-width:0;
  border-top:1px solid var(--rule-soft); border-right:1px solid var(--rule-soft);
  border-bottom:1px solid var(--rule-soft)}
.bar b{display:block; font-weight:600; font-size:.78rem}
.bar span{color:var(--muted); display:block; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis}
.bar.tba{border-left-style:dashed; background:transparent}
.bar.past{opacity:.4}
.acep{color:var(--acep)} .saem{color:var(--saem)}
.ifem{color:var(--ifem)} .eusem{color:var(--eusem)}
.asem{color:var(--asem)} .hkcem{color:var(--hkcem)} .sems{color:var(--sems)}
.yearband{grid-column:1/-1; height:1px; background:var(--rule-soft); margin:.25rem 0}

.legend{display:flex; gap:.6rem 1.4rem; flex-wrap:wrap; margin:1rem 0 0; font-size:.8rem}
/* 左側色條刻意沿用格線上 .bar 的樣式，讓「顏色→學會」的對應一眼成立 */
.legend span{display:flex; align-items:center; gap:.55rem;
  border-left:3px solid currentColor; padding:.2rem 0 .2rem .55rem}
.legend .lg-txt{display:flex; flex-direction:column; line-height:1.25}
.legend .lg-txt b{font-weight:600; font-size:.82rem}
.legend img{height:18px; width:auto; max-width:64px; object-fit:contain}
.legend em{font-style:normal; color:var(--muted)}

/* 會議卡片 */
.cards{display:grid; grid-template-columns:repeat(auto-fill,minmax(232px,1fr));
  gap:1.6rem 1.3rem; margin-top:.3rem}
.card{display:block; color:inherit; text-decoration:none; min-width:0}
.card .band{aspect-ratio:16/10; display:flex; align-items:center;
  justify-content:center; padding:1.1rem; position:relative;
  border:1px solid var(--rule); background:#fff}
.card .band img{max-width:100%; max-height:100%; width:auto; height:auto;
  object-fit:contain}
.card .band b{font-size:1.35rem; font-weight:600; letter-spacing:.05em}
.card .band em{position:absolute; right:.45rem; bottom:.35rem; font-style:normal;
  font-size:.72rem; color:var(--muted)}
.card .soc-line{display:block; font-size:.76rem; font-weight:600;
  margin:.6rem 0 -.35rem}
.card .card-title{display:block; font-weight:600; font-size:1rem; line-height:1.3;
  margin:.7rem 0 .45rem}
.card:hover .card-title,.card:focus-visible .card-title{text-decoration:underline}
.card .meta{display:flex; align-items:flex-start; gap:.4rem;
  font-size:.88rem; color:var(--muted); margin:.18rem 0}
.card .meta svg{flex:0 0 auto; width:.9rem; height:.9rem; margin-top:.22rem}
.card .meta .place{color:var(--ink)}
.card.past{opacity:.5}
.card .done{font-size:.72rem; border:1px solid var(--rule); padding:0 .3rem;
  margin-left:.4rem; color:var(--muted); font-weight:400}
.card.past .band img{filter:grayscale(1)}

/* 明細表 */
table{width:100%; border-collapse:collapse; margin-top:.2rem; font-size:.92rem}
th{text-align:left; font-weight:600; font-size:.78rem; color:var(--muted);
   border-bottom:1px solid var(--rule); padding:.4rem .5rem .4rem 0}
td{padding:.55rem .5rem .55rem 0; border-bottom:1px solid var(--rule-soft);
   vertical-align:top}
tr.past td{color:var(--muted)}
td.soc{white-space:nowrap; font-weight:600}
td.soc::before{content:""; display:inline-block; width:3px; height:.85em;
  background:currentColor; margin-right:.45rem; vertical-align:-1px}
td.when{white-space:nowrap}
.tba-tag{color:var(--muted); font-style:italic}

/* 投稿死線 */
.dl{background:var(--surface); border:1px solid var(--rule); padding:.2rem 1rem .8rem}
.dl ol{list-style:none; margin:0; padding:0}
.dl li{display:grid; grid-template-columns:5.2rem 1fr auto; gap:.8rem;
  align-items:baseline; padding:.6rem 0; border-bottom:1px solid var(--rule-soft)}
.dl li:last-child{border-bottom:0}
.dl .when{font-size:.88rem; color:var(--muted); white-space:nowrap}
.dl .what b{font-weight:600}
.dl .what span{color:var(--muted); font-size:.85rem; display:block}
.dl .left{font-size:.85rem; white-space:nowrap; text-align:right}
.dl .left b{font-size:1.05rem; font-weight:600}
.dl li.urgent .left{color:#a3251c} .dl li.urgent .left b{font-size:1.25rem}
.dl li.soon .left{color:#8a5a11}
.dl li.tba .when,.dl li.tba .left{color:var(--muted); font-style:italic}
.dl .flag{font-size:.72rem; color:var(--muted); border:1px solid var(--rule);
  padding:0 .3rem; margin-left:.4rem; white-space:nowrap}
.dl .note{margin:.7rem 0 0; font-size:.8rem; color:var(--muted)}

.archive-link{margin:2rem 0 0; font-size:.9rem}
.archive-link a{color:var(--muted)}
.archive-link a:hover{color:var(--ink)}

footer{margin-top:3.5rem; padding-top:1rem; border-top:1px solid var(--rule);
  font-size:.82rem; color:var(--muted)}
footer p{margin:.35rem 0}
footer ul{margin:.4rem 0; padding-left:1.1rem}

@media (max-width:640px){
  body{font-size:16px}
  .wrap{padding:1.6rem 1rem 3rem}
  table{font-size:.85rem}
}
@media (prefers-reduced-motion:no-preference){
  .hero .count b{transition:none}
}
"""


TRACK_ZH = {
    "Abstracts": "摘要",
    "Late-breaking Abstracts": "Late-breaking 摘要",
    "Research Forum Abstracts": "Research Forum 摘要",
    "Clinical Images": "臨床影像",
    "Didactics": "教學課程",
    "Innovations": "創新",
    "IGNITE!": "IGNITE!",
    "Advanced EM Workshops": "進階工作坊",
}
CONF_ZH = {"high": "", "medium": "來源為敘述句", "low": "需人工確認"}


def deadline_section(deadlines: list[dict], today: date) -> str:
    """只顯示還沒過期的死線，最近的排最前面；已公布的排在未定的前面。"""
    live, tba = [], []
    for d in deadlines:
        if d.get("kind") == "open":
            continue                       # 開放日不是死線，不佔版面
        iso = d.get("date_iso") or ""
        if iso:
            try:
                left = (date.fromisoformat(iso) - today).days
            except ValueError:
                continue
            if left < 0:
                continue
            live.append((left, d))
        elif d.get("status") == "tba":
            tba.append(d)

    live.sort(key=lambda x: x[0])
    if not live and not tba:
        return ('<div class="dl"><p class="note">目前四個學會都沒有開放中的投稿。'
                '死線一公布就會出現在這裡。</p></div>')

    items = []
    for left, d in live:
        cls = "urgent" if left <= 7 else ("soon" if left <= 30 else "")
        track = TRACK_ZH.get(d.get("track", ""), d.get("track", "投稿"))
        flag = CONF_ZH.get(d.get("confidence", "low"), "")
        flag_html = f'<span class="flag">{flag}</span>' if flag else ""
        items.append(
            f'<li class="{cls}">'
            f'<span class="when">{iso_short(d["date_iso"])}</span>'
            f'<span class="what"><b>{html.escape(society_label(d.get("society","")))} · '
            f'{html.escape(track)}</b>{flag_html}'
            f'<span>{html.escape(d.get("cycle") or "")}</span></span>'
            f'<span class="left"><b>{left}</b> 天</span></li>')

    for d in tba:
        track = TRACK_ZH.get(d.get("track", ""), d.get("track", "投稿"))
        items.append(
            f'<li class="tba"><span class="when">未公布</span>'
            f'<span class="what"><b>{html.escape(society_label(d.get("society","")))} · '
            f'{html.escape(track)}</b>'
            f'<span>{html.escape(d.get("note") or d.get("cycle") or "")[:70]}</span></span>'
            f'<span class="left">—</span></li>')

    return ('<div class="dl"><ol>' + "".join(items) + "</ol>"
            '<p class="note">日期以各學會官方公告為準。標記「需人工確認」者為從自由'
            '文字擷取，不會寫入行事曆提醒。</p></div>')


def iso_short(s: str) -> str:
    d = _d(s)
    return f"{d.month}/{d.day}<br>{d.year}" if d else html.escape(s)


ICON_CAL = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" '
            'stroke-width="1.4" aria-hidden="true"><rect x="1.8" y="3.2" width="12.4" '
            'height="11" rx="1"/><path d="M1.8 6.6h12.4M5 1.8v2.6M11 1.8v2.6"/></svg>')
ICON_PIN = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" '
            'stroke-width="1.4" aria-hidden="true"><path d="M8 14.5s5-4.6 5-8.2A5 5 0 0 0 '
            '3 6.3c0 3.6 5 8.2 5 8.2Z"/><circle cx="8" cy="6.2" r="1.9"/></svg>')


def conference_cards(rows: list[dict], today: date) -> str:
    """
    ACEP 官網 Future Dates 的卡片版型：色塊、標題、行事曆圖示＋日期、
    定位圖示＋地點，整張卡連到該學會的原始頁面。
    """
    cards = []
    for r in rows:
        hue = SOCIETY_META.get(r["society"], {}).get("hue", "")
        past = r["_end"] and r["_end"] < today
        if r["_confirmed"]:
            when = f'{r["_start"].isoformat()} – {r["_end"].isoformat()}'
        else:
            when = html.escape(r.get("date_text") or "日期未定")
        place = ", ".join(x for x in (r.get("city"), r.get("country_or_state")) if x) or "地點未定"
        cards.append(
            f'<a class="card{" past" if past else ""}" '
            f'href="{html.escape(r.get("source_url",""))}">'
            f'<span class="band">{band(r["society"])}'
            f'<em>{r.get("year") or ""}</em></span>'
            f'<span class="soc-line {hue}">{html.escape(society_label(r["society"]))}</span>'
            f'<span class="card-title">{html.escape(r.get("name",""))}'
            f'{"<span class=\'done\'>已結束</span>" if past else ""}</span>'
            f'<span class="meta {hue}">{ICON_CAL}<span>{when}</span></span>'
            f'<span class="meta {hue}">{ICON_PIN}'
            f'<span class="place">{html.escape(place)}</span></span>'
            f'</a>')
    return '<div class="cards">' + "".join(cards) + "</div>"


def season_grid(rows: list[dict], today: date) -> str:
    """年度節奏格線：橫軸十二個月、縱軸年份。"""
    years = sorted({r["year"] for r in rows if r.get("year")})
    grid = ['<div class="grid"><div class="gridrow months"><span>月份</span>'
            + "".join(f"<span>{m}</span>" for m in MONTH_ABBR) + "</div>"]
    for y in years:
        in_year = [r for r in rows if r.get("year") == y]
        lanes = assign_lanes(in_year)
        n_lanes = max(lanes.values()) + 1 if lanes else 1
        cls = "yr now" if y == today.year else "yr"
        cells = [f'<span class="{cls}" style="grid-row:span {n_lanes}">{y}</span>']
        for r in in_year:
            m = r["_month"] or 12
            hue = SOCIETY_META.get(r["society"], {}).get("hue", "")
            past = " past" if r["_end"] and r["_end"] < today else ""
            tba = "" if r["_confirmed"] else " tba"
            when = (f'{r["_start"].month}/{r["_start"].day}–'
                    f'{r["_end"].month}/{r["_end"].day}') if r["_confirmed"] else "日期未定"
            place = r.get("city") or r.get("country_or_state") or "—"
            cells.append(
                f'<div class="bar {hue}{past}{tba}" '
                f'style="grid-column:{m + 1};grid-row:{lanes[_key(r)] + 1}" '
                f'title="{html.escape(r.get("name",""))}">'
                f'<b>{html.escape(r["society"])}</b>'
                f'<span>{html.escape(when)}</span>'
                f'<span>{html.escape(place)}</span></div>')
        grid.append('<div class="gridrow">' + "".join(cells) + "</div>")
    grid.append("</div>")

    legend = '<p class="legend">' + "".join(
        f'<span class="{v["hue"]}">{legend_mark(k, v["hue"])}'
        f'<span class="lg-txt"><b>{k} {v.get("zh","")}</b>'
        f'<em>{v["meeting"]}</em></span></span>'
        for k, v in SOCIETY_META.items()) + "</p>"

    return "".join(grid) + legend


def render_html(rows: list[dict], today: date, generated: str,
                deadlines: list[dict] | None = None,
                archive_count: int = 0) -> str:
    nxt = next_up(rows, today)
    years = sorted({r["year"] for r in rows if r.get("year")})

    grid_html = season_grid(rows, today)
    cards_html = conference_cards(rows, today)
    archive_link = (f'<p class="archive-link"><a href="past.html">'
                    f'查看 {today.year} 年以前的 {archive_count} 場歷屆會議 →</a></p>'
                    if archive_count else "")
    dl_html = deadline_section(deadlines or [], today)

    # --- Hero ---
    if nxt:
        place = ", ".join(x for x in (nxt.get("city"), nxt.get("country_or_state")) if x)
        hero = (
            '<section class="hero">'
            '<p class="lede">下一場</p>'
            f'<p class="title">{html.escape(nxt.get("name", ""))}</p>'
            f'<p class="meta">{nxt["_start"].isoformat()} – {nxt["_end"].isoformat()}'
            f'{"　·　" + html.escape(place) if place else ""}</p>'
            f'<p class="count" data-start="{nxt["_start"].isoformat()}">'
            f'<b>—</b> 天後開幕</p></section>')
    else:
        hero = ('<section class="hero"><p class="lede">下一場</p>'
                '<p class="title">四個學會目前都沒有公布未來場次。</p>'
                '<p class="meta">下次自動更新時會重新檢查。</p></section>')

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>急診國際年會追蹤</title>
<meta name="description" content="ACEP、SAEM、IFEM、EuSEM 四大急診醫學會年會的日期與地點，每月自動更新。">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

<header class="masthead">
  <h1>急診國際年會追蹤</h1>
  <p class="stamp">最後更新 {generated}</p>
</header>

{hero}

<section>
  <h2>投稿死線</h2>
  {dl_html}
</section>

<section style="margin-top:3rem">
  <h2>年度節奏</h2>
  {grid_html}
</section>

<section style="margin-top:3rem">
  <h2>{today.year} 年起的場次</h2>
  {cards_html}
  {archive_link}
</section>

<footer>
  <p>資料每月自動擷取自各學會官方頁面：</p>
  <ul>
    <li>ACEP — acep.org/sa/general-information/future-dates</li>
    <li>SAEM — saem.org/meetings-and-events/future-meetings</li>
    <li>IFEM — ifem.cc/about_congress</li>
    <li>EuSEM — eusemcongress.org</li>
  </ul>
  <p>虛線框代表學會只公布了月份、尚未定案確切日期。訂閱
     <a href="conferences.ics">行事曆檔</a>，或下載
     <a href="conferences.csv">CSV</a>／<a href="conferences.json">JSON</a>。</p>
  <p>報名與投稿死線請以官方公告為準；本頁只追蹤日期與地點。</p>
</footer>

</div>
<script>
(function(){{
  var el = document.querySelector('.count[data-start]');
  if(!el) return;
  var start = new Date(el.dataset.start + 'T00:00:00');
  var days = Math.ceil((start - new Date()) / 86400000);
  el.querySelector('b').textContent = days > 0 ? days : 0;
  if (days <= 0) el.innerHTML = '<b>進行中</b>';
}})();
</script>
</body>
</html>
"""


def render_archive(rows: list[dict], today: date, generated: str) -> str:
    """歷屆會議封存頁。刻意做得比主頁安靜——這裡是查資料的地方，不是待辦清單。"""
    years = sorted({r["year"] for r in rows if r.get("year")})
    span = f"{years[0]}–{years[-1]}" if years else ""
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>歷屆會議 — 急診國際年會追蹤</title>
<meta name="robots" content="noindex">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

<header class="masthead">
  <h1>歷屆會議</h1>
  <p class="stamp"><a href="index.html">← 回到主頁</a></p>
</header>

<section style="margin-top:2.4rem">
  <p style="color:var(--muted);max-width:60ch">
    {len(rows)} 場已結束的會議{f"，{span}" if span else ""}。
    今年與之後的場次、投稿死線都在主頁。</p>
</section>

<section style="margin-top:2rem">
  <h2>年度節奏</h2>
  {season_grid(rows, today)}
</section>

<section style="margin-top:3rem">
  <h2>全部歷屆場次</h2>
  {conference_cards(rows, today)}
</section>

<footer>
  <p>最後更新 {generated}　·　<a href="index.html">回到主頁</a></p>
</footer>

</div>
</body>
</html>
"""


def write_all(rows_raw: list[dict], outdir: Path, today: date | None = None,
              deadlines: list[dict] | None = None) -> None:
    today = today or date.today()
    rows = prepare(rows_raw)
    deadlines = deadlines or []
    outdir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    current, archive = split_archive(rows, today)
    (outdir / "index.html").write_text(
        render_html(current, today, generated, deadlines, len(archive)),
        encoding="utf-8")
    if archive:
        (outdir / "past.html").write_text(
            render_archive(archive, today, generated), encoding="utf-8")
    (outdir / "conferences.ics").write_text(build_ics(rows, deadlines), encoding="utf-8")
    (outdir / "deadlines.json").write_text(
        json.dumps(deadlines, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "conferences.json").write_text(
        json.dumps(rows_raw, ensure_ascii=False, indent=2), encoding="utf-8")
