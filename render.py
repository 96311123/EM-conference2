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
import re
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
    "ERC":   {"full": "European Resuscitation Council",
              "zh": "歐洲復甦委員會",
              "meeting": "ERC Congress – Resuscitation", "hue": "erc"},
    "AHA":   {"full": "American Heart Association",
              "zh": "美國心臟協會",
              "meeting": "Resuscitation Science Symposium（ReSS）", "hue": "aha"},
    "AAEMS": {"full": "Asian Association for Emergency Medical Services",
              "zh": "亞洲緊急醫療救護協會",
              "meeting": "EMS Asia Conference（院前救護）", "hue": "aaems"},
    "TSEM":  {"full": "Taiwan Society of Emergency Medicine",
              "zh": "台灣急診醫學會",
              "meeting": "年會（六月最後一週週五、六）", "hue": "tsem"},
}

# 有 logo 圖檔（docs/assets/<hue>.png）的學會；新學會把 logo 放進 docs/assets 並加到這裡即可。
HAS_LOGO = {"acep", "saem", "ifem", "eusem", "asem", "sems", "hkcem", "erc", "aha", "tsem",
            "aaems"}

# 地圖旗子另用精簡版圖示（docs/assets/<hue>-flag.png）的學會：
# 卡片放完整 logo（含文字），旗面太小只放符號。
FLAG_LOGO = {"erc", "aha", "eusem"}


def zh_name(code: str) -> str:
    return SOCIETY_META.get(code, {}).get("zh", "")


# 會議本身的簡稱跟學會不同時，畫面上以會議簡稱為主。
# IFEM 主辦的世界大會一般稱 ICEM（International Conference on Emergency Medicine）。
# AHA 的復甦年會一般稱 ReSS（Resuscitation Science Symposium）。
MEETING_CODE = {"IFEM": "ICEM", "AHA": "ReSS"}


def meeting_code(code: str) -> str:
    """地圖、年度條、時間軸上用的會議簡稱：IFEM → ICEM，其餘不變。"""
    return MEETING_CODE.get(code, code)


def display_name(r: dict) -> str:
    """
    完整會議名稱。IFEM 的會議在名稱後補上『(ICEM 2027)』；官網名稱裡若已有
    『(IFEM2027)』這種括號簡稱，就直接換成 ICEM，不重複加。
    """
    name = r.get("name", "") or ""
    mc = meeting_code(r.get("society", ""))
    if mc == r.get("society") or not r.get("year"):
        return name
    tag = f"({mc} {r['year']})"
    pat = re.compile(r"\(\s*" + re.escape(r["society"]) + r"\s*" + str(r["year"]) + r"\s*\)")
    if pat.search(name):
        return pat.sub(tag, name)
    if mc in name:                     # 例如『… Symposium 2027 (ReSS)』已經有簡稱
        return name
    return f"{name} {tag}"


def society_label(code: str) -> str:
    """『SEMS 新加坡急診』這種並排標示，中英文都給；IFEM 顯示成『ICEM · IFEM 國際…』。"""
    zh = zh_name(code)
    base = f"{code} {zh}" if zh else code
    mc = meeting_code(code)
    return f"{mc} · {base}" if mc != code else base



def legend_mark(code: str, hue: str) -> str:
    if hue in HAS_LOGO:
        return f'<img src="assets/{hue}.png" alt="{code}" loading="lazy">'
    return f"<b>{html.escape(code)}</b>"


# 橫式 logo 圖檔不完整時，改用「圖示＋網頁文字」排版，避免文字被切掉。
# AHA 提供的 ReSS 橫式圖右側被截斷（只到 "Resuscitation Scie"）。
CARD_LOCKUP = {
    "aha": ("aha-flag.png", "American Heart Association", "Resuscitation Science Symposium"),
}


def band(society: str) -> str:
    """卡片頂端：有 logo 就放圖，沒有就放學會縮寫色塊。"""
    hue = SOCIETY_META.get(society, {}).get("hue", "")
    if hue in CARD_LOCKUP:
        img, top, main = CARD_LOCKUP[hue]
        return (f'<span class="lockup {hue}"><img src="assets/{img}" alt="" loading="lazy">'
                f'<span><small>{html.escape(top)}</small>{html.escape(main)}</span></span>')
    if hue in HAS_LOGO:
        return (f'<img src="assets/{hue}.png" alt="{html.escape(society_label(society))}" '
                f'loading="lazy" width="360">')
    mc = meeting_code(society)
    sub = f'<small>{html.escape(mc)}</small>' if mc != society else ""
    return f'<b class="{hue}">{html.escape(society)}{sub}</b>'

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
    # 同一個 (學會, 年份) 已經有確切日期時，丟掉沒有日期的那筆。
    # 例如 IFEM 2027 先只公布「JUNE 2027」，後來補上 6/10–14；或暫定的
    # SSEM 2027 被正式日期取代。封存檔只進不出，兩筆都還在，這裡要擇一。
    dated = {(r.get("society"), r.get("year")) for r in rows
             if _d(r.get("start", "")) and not r.get("tentative")}
    # 官網改期時（例如 ASEM 2028 從 10/20–25 改成 11/6–8），舊日期在封存檔裡
    # 會被標成 delisted。同一 (學會, 年份) 還有現行資料時，舊的那筆就不要再顯示。
    listed = {(r.get("society"), r.get("year")) for r in rows if not r.get("delisted")}
    out = []
    for r in rows:
        if r.get("name") == "(fetch failed)":
            continue
        if r.get("delisted") and (r.get("society"), r.get("year")) in listed:
            continue
        if ((not _d(r.get("start", "")) or r.get("tentative"))
                and (r.get("society"), r.get("year")) in dated):
            continue
        start, end = _d(r.get("start", "")), _d(r.get("end", ""))
        month = start.month if start else _month_from_text(r.get("date_text", ""))
        r = dict(r)
        r["_start"] = start
        r["_end"] = end or start
        r["_month"] = month
        # 暫定場次即使有推估日期（例如台灣急診醫學會依慣例推算），也不算確定
        r["_confirmed"] = bool(start) and not r.get("tentative")
        r["_sort"] = (r.get("year") or 9999, month or 13)
        out.append(r)
    out.sort(key=lambda x: x["_sort"])
    return out


def _month_from_text(text: str) -> int | None:
    names = ["jan", "feb", "mar", "apr", "may", "jun",
             "jul", "aug", "sep", "oct", "nov", "dec"]
    low = (text or "").lower()
    m = re.search(r"(\d{1,2})\s*月", low)          # 「2027 年 10 月（預計）」
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(1))
    for i, n in enumerate(names, start=1):
        if n in low:
            return i
    return None


def split_archive(rows: list[dict], today: date) -> tuple[list[dict], list[dict]]:
    """
    主頁只放還沒結束的場次；已經結束的（包含今年稍早開完的）移到封存頁。
    沒有日期的（例如只公布月份的 IFEM 2027）一律留在主頁——那是未來的事，
    不該被當成歷史。
    """
    cut = today

    def ended(r: dict) -> bool:
        if r["_end"]:
            return r["_end"] < cut
        # 沒有日期、但年份已經過去的暫定場次（例如一直沒公布的 SEMS ASM），
        # 不該永遠掛在主頁上。
        return bool(r.get("year")) and r["year"] < today.year

    current = [r for r in rows if not ended(r)]
    archive = [r for r in rows if ended(r)]
    return current, archive


def next_up(rows: list[dict], today: date) -> dict | None:
    future = [r for r in rows if r["_confirmed"] and r["_end"] and r["_end"] >= today]
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
            f'SUMMARY:{esc(meeting_code(r["society"]) + " — " + display_name(r))}',
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
  --erc:#2b74ad; --aha:#c4283c; --aaems:#d4601a; --tsem:#2c3e95;
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
.erc{color:var(--erc)} .aha{color:var(--aha)} .aaems{color:var(--aaems)}
.tsem{color:var(--tsem)}
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
.card .band b{font-size:1.35rem; font-weight:600; letter-spacing:.05em; text-align:center}
.card .band .lockup{display:flex; align-items:center; gap:.7rem; padding:0 .4rem}
.card .band .lockup img{height:4.2rem; width:auto; flex:0 0 auto}
.card .band .lockup>span{display:flex; flex-direction:column; font-family:"Inter","Helvetica Neue",Arial,sans-serif;
  font-size:1.18rem; line-height:1.12; font-weight:400; letter-spacing:-.01em}
.card .band .lockup small{font-size:.62rem; color:#222; letter-spacing:0; margin-bottom:.2rem}
.card .band b small{display:block; font-size:.8rem; letter-spacing:.08em; opacity:.8}
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
.card.tba .band{border-style:dashed; border-color:#9aa9b0}
.card .done{font-size:.72rem; white-space:nowrap; display:inline-block; border:1px solid var(--rule); padding:0 .3rem;
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
            when = (r["_start"].isoformat() if r["_start"] == r["_end"]
                    else f'{r["_start"].isoformat()} – {r["_end"].isoformat()}')
        else:
            when = html.escape(r.get("date_text") or "日期未定")
        place = ", ".join(x for x in (r.get("city"), r.get("country_or_state")) if x) or "地點未定"
        tba = "" if r["_confirmed"] else " tba"
        tag = ("<span class='done'>暫定</span>" if r.get("tentative")
               else ("<span class='done'>日期未定</span>" if tba else ""))
        cards.append(
            f'<a class="card{" past" if past else ""}{tba}" '
            f'href="{html.escape(r.get("source_url",""))}">'
            f'<span class="band">{band(r["society"])}'
            f'<em>{r.get("year") or ""}</em></span>'
            f'<span class="soc-line {hue}">{html.escape(society_label(r["society"]))}</span>'
            f'<span class="card-title">{html.escape(display_name(r))}'
            f'{"<span class=\'done\'>已結束</span>" if past else tag}</span>'
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
                    f'{r["_end"].month}/{r["_end"].day}') if r["_start"] else "日期未定"
            if r["_start"] and not r["_confirmed"]:
                when += "（暫定）"
            place = r.get("city") or r.get("country_or_state") or "—"
            cells.append(
                f'<div class="bar {hue}{past}{tba}" '
                f'style="grid-column:{m + 1};grid-row:{lanes[_key(r)] + 1}" '
                f'title="{html.escape(display_name(r))}">'
                f'<b>{html.escape(meeting_code(r["society"]))}</b>'
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


# --------------------------------------------------------------------------
# 世界地圖（頁首）
# --------------------------------------------------------------------------
# 仿照「Explore the World」那面軟木板：深色世界地圖、金色圖釘、白色標籤卡
# 拉一條線指到城市，底部再列一排當年場次。一次只顯示一個年份，可切換。
#
# 底圖與投影參數在 world_map.py（由 Natural Earth 產生）；這裡只負責把
# 城市經緯度換成圖上座標，以及替標籤找一個不互相重疊的位置。

import math

from world_map import W as MAP_W, H as MAP_H, LAT_TOP, COUNTRIES_PATH

# 城市座標（緯度, 經度）。查不到的城市會退回國家／州的位置；
# 兩者都查不到就不放圖釘，但底部清單照樣列出。新的主辦城市出現時加在這裡。
CITY_COORDS = {
    "san francisco": (37.77, -122.42), "boston": (42.36, -71.06),
    "chicago": (41.88, -87.63), "austin": (30.27, -97.74),
    "las vegas": (36.17, -115.14), "philadelphia": (39.95, -75.17),
    "dallas": (32.78, -96.80), "new orleans": (29.95, -90.07),
    "denver": (39.74, -104.99), "seattle": (47.61, -122.33),
    "washington": (38.91, -77.04), "san diego": (32.72, -117.16),
    "orlando": (28.54, -81.38), "phoenix": (33.45, -112.07),
    "atlanta": (33.75, -84.39), "new york": (40.71, -74.01),
    "salt lake city": (40.76, -111.89), "anaheim": (33.84, -117.91),
    "helsinki": (60.17, 24.94), "barcelona": (41.39, 2.17),
    "vienna": (48.21, 16.37), "copenhagen": (55.68, 12.57),
    "lisbon": (38.72, -9.14), "prague": (50.08, 14.44),
    "amsterdam": (52.37, 4.90), "glasgow": (55.86, -4.25),
    "berlin": (52.52, 13.40), "hamburg": (53.55, 9.99),
    "budapest": (47.50, 19.04), "athens": (37.98, 23.73),
    "rome": (41.90, 12.50), "madrid": (40.42, -3.70),
    "paris": (48.86, 2.35), "london": (51.51, -0.13),
    "manchester": (53.48, -2.24), "dublin": (53.35, -6.26),
    "stockholm": (59.33, 18.07), "oslo": (59.91, 10.75),
    "kuala lumpur": (3.14, 101.69), "singapore": (1.35, 103.82),
    "hong kong": (22.32, 114.17), "taipei": (25.03, 121.57),
    "seoul": (37.57, 126.98), "tokyo": (35.68, 139.69),
    "bangkok": (13.76, 100.50), "manila": (14.60, 120.98),
    "bali": (-8.65, 115.22), "jakarta": (-6.21, 106.85),
    "dubai": (25.20, 55.27), "abu dhabi": (24.45, 54.38),
    "montreal": (45.50, -73.57), "toronto": (43.65, -79.38),
    "vancouver": (49.28, -123.12), "halifax": (44.65, -63.58),
    "melbourne": (-37.81, 144.96), "sydney": (-33.87, 151.21),
    "brisbane": (-27.47, 153.03), "perth": (-31.95, 115.86),
    "auckland": (-36.85, 174.76), "san antonio": (29.42, -98.49),
    "fort lauderdale": (26.12, -80.14), "milan": (45.46, 9.19),
    "hua hin": (12.57, 99.96), "new taipei": (25.01, 121.46),
    "taichung": (24.15, 120.67), "changhua": (24.08, 120.54), "kaohsiung": (22.63, 120.30),
    "chiayi": (23.48, 120.45), "pingtung": (22.67, 120.49), "tainan": (22.99, 120.21), "antalya": (36.90, 30.70),
    "new delhi": (28.61, 77.21), "busan": (35.18, 129.08),
    "clark freeport, angeles city": (15.19, 120.55), "angeles city": (15.15, 120.59),
    "clark": (15.19, 120.55), "indianapolis": (39.77, -86.16),
    "torino": (45.07, 7.69), "turin": (45.07, 7.69), "ljubljana": (46.06, 14.51),
    "bologna": (44.49, 11.34), "freiburg im breisgau": (47.99, 7.84),
    "freiburg": (47.99, 7.84), "antwerp": (51.22, 4.40), "cape town": (-33.92, 18.42),
    "rotterdam": (51.92, 4.48), "edinburgh": (55.95, -3.19), "cape town": (-33.92, 18.42),
    "rio de janeiro": (-22.91, -43.17), "sao paulo": (-23.55, -46.63),
    "são paulo": (-23.55, -46.63), "buenos aires": (-34.60, -58.38),
    "mexico city": (19.43, -99.13),
}
COUNTRY_COORDS = {
    "malaysia": (3.14, 101.69), "brazil": (-12.0, -50.0),
    "singapore": (1.35, 103.82), "hong kong": (22.32, 114.17),
    "finland": (61.9, 25.7), "germany": (51.2, 10.4), "spain": (40.4, -3.7),
    "austria": (47.5, 14.6), "denmark": (56.0, 10.0), "portugal": (39.6, -8.0),
    "italy": (42.8, 12.6), "france": (46.6, 2.4), "netherlands": (52.2, 5.3),
    "united kingdom": (53.0, -1.5), "uk": (53.0, -1.5), "ireland": (53.3, -7.7),
    "canada": (56.0, -100.0), "australia": (-25.3, 133.8), "japan": (36.2, 138.3),
    "korea": (36.5, 127.8), "taiwan": (23.7, 121.0), "thailand": (15.0, 101.0),
    "uae": (24.0, 54.0), "united arab emirates": (24.0, 54.0),
    "usa": (39.8, -98.6), "united states": (39.8, -98.6),
}

US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il",
    "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt",
    "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
    "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
}

SOCIETY_HEX = {"ACEP": "#9d3a2e", "SAEM": "#2f6a56", "IFEM": "#8a6820",
               "EUSEM": "#33518c", "ASEM": "#5c4b8a", "HKCEM": "#a8562b",
               "SEMS": "#1f6f78", "ERC": "#2b74ad", "AHA": "#c4283c",
               "AAEMS": "#d4601a", "TSEM": "#2c3e95"}

CONTINENTS = [("NORTH AMERICA", 47, -103), ("SOUTH AMERICA", -33, -66),
              ("EUROPE", 52, 18), ("AFRICA", 6, 20), ("ASIA", 54, 92),
              ("OCEANIA", -26, 134)]

MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def project(lat: float, lon: float) -> tuple[float, float]:
    """與 world_map.py 相同的等距長方投影。"""
    x = (lon + 180) / 360 * MAP_W
    y = (LAT_TOP - lat) / 360 * MAP_W
    return x, y


def coords_for(r: dict) -> tuple[float, float] | None:
    city = (r.get("city") or "").strip().lower()
    region = (r.get("country_or_state") or "").strip().lower()
    if city in CITY_COORDS:
        return CITY_COORDS[city]
    if city in COUNTRY_COORDS:
        return COUNTRY_COORDS[city]
    if region in COUNTRY_COORDS:
        return COUNTRY_COORDS[region]
    return None


def map_place(r: dict) -> str:
    """『Boston, USA』『Kuala Lumpur, Malaysia』這種短地名；沒有地點寫『地點未定』。"""
    city = (r.get("city") or "").strip()
    region = (r.get("country_or_state") or "").strip()
    if region.lower() in US_STATES:
        region = "USA"
    if region and region.lower() != city.lower():
        return f"{city}, {region}" if city else region
    return city or "地點未定"


def map_when(r: dict) -> str:
    """『May 18 – 21, 2027』；日期未定時寫『Jun 2027（暫定）』。"""
    s, e = r["_start"], r["_end"]
    if s and e:
        tent = "" if r["_confirmed"] else "（暫定）"
        if s == e:
            return f"{MON[s.month-1]} {s.day}, {s.year}{tent}"
        if s.month == e.month:
            return f"{MON[s.month-1]} {s.day} – {e.day}, {s.year}{tent}"
        return f"{MON[s.month-1]} {s.day} – {MON[e.month-1]} {e.day}, {e.year}{tent}"
    m = r.get("_month")
    y = r.get("year")
    # 已經過去、只知道年份的歷史場次（例如 ICEM 2016 開普敦）就只寫年份，不標暫定
    tent = "（暫定）" if r.get("tentative") or (y and y >= date.today().year) else ""
    return f"{MON[m-1]} {y}{tent}" if m else f"{y}{tent}"


def _text_w(s: str, size: float) -> float:
    """估計文字寬度（SVG 沒有排版引擎可問）。CJK 一字算一個字寬。"""
    w = 0.0
    for ch in s:
        w += size * (1.0 if ord(ch) > 0x2E80 else (0.36 if ch in " ,.'()–-" else 0.6))
    return w


def _overlap(a, b, pad=4.0) -> bool:
    return not (a[0] + a[2] + pad <= b[0] or b[0] + b[2] + pad <= a[0] or
                a[1] + a[3] + pad <= b[1] or b[1] + b[3] + pad <= a[1])


def place_labels(items: list[dict], obstacles: list[tuple]) -> None:
    """
    替每張標籤卡找位置：在圖釘四周依距離由近到遠試，挑第一個不出界、
    不壓到其他標籤或圖釘的位置。找不到就退而求其次，選重疊最少的。
    就地寫入 item["box"] = (x, y, w, h)。
    """
    pins = [(it["px"] - 7, it["py"] - 7, 14, 14) for it in items]
    placed: list[tuple] = []
    # 擠在一起的圖釘先排（例如吉隆坡、新加坡、香港），它們的選擇最少。
    def crowd(it):
        return -sum(1 for o in items if o is not it and
                    abs(o["px"] - it["px"]) < 160 and abs(o["py"] - it["py"]) < 90)
    for it in sorted(items, key=crowd):
        w, h, px, py = it["w"], it["h"], it["px"], it["py"]
        cands = []
        for d in (22, 45, 75, 110, 150, 200):
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (-1, 0), (1, 0),
                           (0, -1), (0, 1)):
                x = px + (d if dx > 0 else (-w - d if dx < 0 else -w / 2))
                y = py + (d * .6 if dy > 0 else (-h - d * .6 if dy < 0 else -h / 2))
                cands.append((x, y))
        best, best_bad = None, None
        for x, y in cands:
            box = (x, y, w, h)
            if x < 6 or y < 6 or x + w > MAP_W - 6 or y + h > MAP_H - 6:
                continue
            bad = sum(_overlap(box, o) for o in placed + obstacles + pins)
            if bad == 0:
                best = box
                break
            if best_bad is None or bad < best_bad[0]:
                best_bad = (bad, box)
        it["box"] = best or (best_bad[1] if best_bad else
                             (min(max(px - w / 2, 6), MAP_W - w - 6), 6, w, h))
        placed.append(it["box"])


FLAG_W, FLAG_H = 44, 28


def place_flags(items: list[dict]) -> list[tuple]:
    """
    每顆圖釘插一支小旗子，旗面是學會 logo。旗子預設朝右、旗桿 40 高；
    跟已經插好的旗子打架時（例如吉隆坡和新加坡只差幾公分），改朝左或
    把旗桿拉高。就地寫入 item["flag"] = (朝向, 旗桿高)，回傳所有旗面框，
    讓標籤卡避開。
    """
    boxes: list[tuple] = []
    for it in sorted(items, key=lambda i: i["px"]):
        px, py = it["px"], it["py"]
        choice = None
        for ph in (40, 74, 108, 142):          # 每階高出一面旗＋間隙
            for side in (1, -1):
                x = px + 0.8 if side > 0 else px - 0.8 - FLAG_W
                box = (x, py - ph, FLAG_W, FLAG_H)      # 只看旗面；旗桿很細，交錯無妨
                if x < 2 or x + FLAG_W > MAP_W - 2 or py - ph < 2:
                    continue
                if not any(_overlap(box, b, pad=3) for b in boxes):
                    choice = (side, ph, box)
                    break
            if choice:
                break
        if not choice:
            side = 1 if px + FLAG_W < MAP_W else -1
            x = px + 0.8 if side > 0 else px - 0.8 - FLAG_W
            choice = (side, 40, (x, py - 40, FLAG_W, FLAG_H))
        it["flag"] = choice[:2]
        boxes.append(choice[2])
    return boxes


def _leader_end(box, px, py) -> tuple[float, float]:
    x, y, w, h = box
    return min(max(px, x), x + w), min(max(py, y), y + h)


ICON_CAL_SVG = ('<symbol id="mi-cal" viewBox="0 0 16 16"><g fill="none" stroke="#3b4650" '
                'stroke-width="1.6"><rect x="1.8" y="3.2" width="12.4" height="11" rx="1"/>'
                '<path d="M1.8 6.6h12.4M5 1.8v2.6M11 1.8v2.6"/></g></symbol>')
ICON_PIN_SVG = ('<symbol id="mi-pin" viewBox="0 0 16 16"><path d="M8 15s5.2-4.8 5.2-8.6A5.2 '
                '5.2 0 0 0 2.8 6.4C2.8 10.2 8 15 8 15Z" fill="#1d2b35"/>'
                '<circle cx="8" cy="6.3" r="2" fill="#fff"/></symbol>')


def flag_svg(it: dict, r: dict, col: str) -> str:
    """旗桿＋旗面（白底、學會色框、logo）。"""
    side, ph = it["flag"]
    px, py = it["px"], it["py"]
    fx = px + 0.8 if side > 0 else px - 0.8 - FLAG_W
    fy = py - ph
    hue = SOCIETY_META.get(r["society"], {}).get("hue", "")
    if hue in HAS_LOGO:
        img = f"{hue}-flag.png" if hue in FLAG_LOGO else f"{hue}.png"
        face = (f'<image href="assets/{img}" x="{fx + 3:.1f}" y="{fy + 3:.1f}" '
                f'width="{FLAG_W - 6}" height="{FLAG_H - 6}" '
                f'preserveAspectRatio="xMidYMid meet"/>')
    else:
        face = (f'<text x="{fx + FLAG_W / 2:.1f}" y="{fy + FLAG_H / 2 + 3.5:.1f}" '
                f'text-anchor="middle" class="m-flag-txt" fill="{col}">'
                f'{html.escape(r["society"])}</text>')
    return (f'<g class="m-flag" style="transform-origin:{px:.1f}px {py:.1f}px">'
            f'<ellipse cx="{px:.1f}" cy="{py + 0.6:.1f}" rx="4" ry="1.6" class="m-base"/>'
            f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{px:.1f}" y2="{fy - 1.5:.1f}" class="m-pole"/>'
            f'<circle cx="{px:.1f}" cy="{fy - 2:.1f}" r="1.8" class="m-knob"/>'
            f'<rect x="{fx:.1f}" y="{fy:.1f}" width="{FLAG_W}" height="{FLAG_H}" rx="1.5" '
            f'class="m-flagface" style="stroke:{col}"/>{face}</g>')


def world_map_section(rows: list[dict], today: date) -> str:
    years = sorted({r["year"] for r in rows if r.get("year") and r["year"] >= today.year})
    if not years:
        return ""
    nxt = next_up(rows, today)
    default = nxt["year"] if nxt else years[0]

    cont = []
    for name, lat, lon in CONTINENTS:
        x, y = project(lat, lon)
        tw = _text_w(name, 10.5)
        cont.append(((x - tw / 2, y - 10, tw, 14),
                     f'<g class="m-z" data-x="{x:.1f}" data-y="{y:.1f}">'
                     f'<text x="{x:.1f}" y="{y:.1f}" class="m-cont" '
                     f'text-anchor="middle">{name}</text>'
                     f'<line x1="{x - tw/2:.1f}" x2="{x + tw/2:.1f}" y1="{y + 3:.1f}" '
                     f'y2="{y + 3:.1f}" class="m-cont-ul"/></g>'))
    obstacles: list[tuple] = []

    groups, strips, tabs = [], [], []
    for y in years:
        in_year = sorted([r for r in rows if r.get("year") == y],
                         key=lambda r: (r["_month"] or 13, r["_start"] or date.max))
        items = []
        for r in in_year:
            c = coords_for(r)
            if not c:
                continue
            px, py = project(*c)
            title = f'{meeting_code(r["society"])} {y}'
            when, place = map_when(r), map_place(r)
            w = max(_text_w(title, 14) + 4, _text_w(when, 10.5) + 17,
                    _text_w(place, 10.5) + 17) + 22
            items.append({"r": r, "px": px, "py": py, "w": w, "h": 56,
                          "title": title, "when": when, "place": place})
        # 同城市同年的圖釘稍微錯開，免得疊成一顆
        seen = {}
        for it in items:
            k = (round(it["px"]), round(it["py"]))
            n = seen.get(k, 0)
            it["px"] += n * 7
            seen[k] = n + 1
        flag_boxes = place_flags(items)
        place_labels(items, obstacles + flag_boxes)

        # 大洲名稱是背景資訊：被旗子蓋到就不畫
        g = [svg for box, svg in cont
             if not any(_overlap(box, fb, pad=2) for fb in flag_boxes)]
        for it in items:
            r, (bx, by, bw, bh) = it["r"], it["box"]
            col = SOCIETY_HEX.get(r["society"], "#555")
            lx, ly = _leader_end(it["box"], it["px"], it["py"])
            tba = "" if r["_confirmed"] else " m-tba"
            href = html.escape(r.get("source_url") or "#")
            g.append(
                f'<a href="{href}" class="m-item{tba}">'
                f'<title>{html.escape(display_name(r))}</title>'
                f'<g class="m-z" data-x="{it["px"]:.1f}" data-y="{it["py"]:.1f}">'
                f'<line x1="{it["px"]:.1f}" y1="{it["py"]:.1f}" x2="{lx:.1f}" y2="{ly:.1f}" '
                f'class="m-lead"/>'
                f'<g class="m-callout">'
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh}" rx="4" '
                f'class="m-card"/>'
                f'<rect x="{bx + 7:.1f}" y="{by + 8:.1f}" width="4" height="15" rx="1" '
                f'fill="{col}"/>'
                f'<text x="{bx + 16:.1f}" y="{by + 21:.1f}" class="m-t">'
                f'{html.escape(it["title"])}</text>'
                f'<use href="#mi-cal" x="{bx + 16:.1f}" y="{by + 27.5:.1f}" width="9" height="9"/>'
                f'<text x="{bx + 29:.1f}" y="{by + 35.5:.1f}" class="m-s">'
                f'{html.escape(it["when"])}</text>'
                f'<use href="#mi-pin" x="{bx + 16:.1f}" y="{by + 40.5:.1f}" width="9" height="9"/>'
                f'<text x="{bx + 29:.1f}" y="{by + 48.5:.1f}" class="m-s">'
                f'{html.escape(it["place"])}</text></g>'
                f'{flag_svg(it, r, col)}</g></a>')
        hidden = "" if y == default else " hidden"
        groups.append(f'<g class="m-year" data-year="{y}"{hidden}>{"".join(g)}</g>')

        cells = []
        for r in in_year:
            hue = SOCIETY_META.get(r["society"], {}).get("hue", "")
            tba = "" if r["_confirmed"] else " tba"
            cells.append(
                f'<a class="ms-cell{tba}" href="{html.escape(r.get("source_url") or "#")}" '
                f'style="--c:var(--{hue})">'
                f'<b>{html.escape(meeting_code(r["society"]))} {y}</b>'
                f'<span>{ICON_PIN}{html.escape(map_place(r))}</span>'
                f'<span>{ICON_CAL}{html.escape(map_when(r))}</span></a>')
        strips.append(
            f'<div class="ms-strip" data-year="{y}"{hidden}>'
            f'<div class="ms-flag">{ICON_CAL}<span>Upcoming <br>EM Conferences'
            f'<b>{y}</b></span></div>'
            f'<div class="ms-cells">{"".join(cells)}</div></div>')
        tabs.append(f'<button type="button" data-year="{y}" '
                    f'aria-pressed="{"true" if y == default else "false"}">{y}</button>')

    svg = (f'<svg class="m-svg" viewBox="0 0 {MAP_W} {MAP_H:.0f}" role="img" '
           f'aria-label="全球急診醫學會議地圖">'
           f'<defs>{ICON_CAL_SVG}{ICON_PIN_SVG}'
           f'<filter id="m-shadow" x="-10%" y="-10%" width="120%" height="140%">'
           f'<feDropShadow dx="0" dy="1.5" stdDeviation="1.6" flood-opacity=".28"/>'
           f'</filter></defs>'
           f'<g class="m-layer"><path d="{COUNTRIES_PATH}" class="m-land"/>'
           f'{"".join(groups)}</g></svg>')

    return (
        '<section class="worldmap" aria-labelledby="wm-title">'
        '<div class="wm-head">'
        f'<h2 id="wm-title">全球急診醫學會議地圖 <span class="wm-year">{default}</span></h2>'
        f'<div class="wm-tabs" role="group" aria-label="選擇年份">{"".join(tabs)}</div>'
        '</div>'
        f'<div class="wm-board"><div class="m-stage">{ZOOM_CONTROLS}{svg}</div>{"".join(strips)}</div>'
        '<p class="wm-note">滑鼠移到旗子上可看會議名稱、日期與地點；點旗子或下方卡片前往官方頁面。虛線代表日期尚未公布。'
        f'{ZOOM_HINT}</p>'
        '</section>')


ZOOM_HINT = "縮放：右上角＋／−，或按住 Ctrl（Mac 為 ⌘）捲動滾輪、雙擊地圖、手機雙指開合；放大後可拖曳移動。"

ZOOM_CONTROLS = ('<div class="m-zoom" role="group" aria-label="地圖縮放">'
                 '<button type="button" data-z="in" aria-label="放大">+</button>'
                 '<button type="button" data-z="out" aria-label="縮小">−</button>'
                 '<button type="button" data-z="reset" aria-label="還原" title="還原">⤢</button></div>')

# 地圖縮放／拖曳：整張圖放在 .m-layer 裡做 translate+scale；每個標記（.m-z）再以自己的
# 位置為中心反向縮放，所以放大時地圖變大，但圖釘、旗子、標籤維持原本大小。
# 滾輪需按住 Ctrl／⌘（觸控板雙指縮放也會送出 ctrl+wheel），免得捲動網頁時誤觸。
ZOOM_JS = """
(function(){
  document.querySelectorAll('.m-stage').forEach(function(stage){
    var svg = stage.querySelector('svg.m-svg'), layer = svg.querySelector('.m-layer');
    var vb = svg.viewBox.baseVal, W = vb.width, H = vb.height;
    var k = 1, tx = 0, ty = 0, KMAX = 10;
    var marks = Array.prototype.slice.call(svg.querySelectorAll('.m-z'));
    function clamp(){
      k = Math.min(KMAX, Math.max(1, k));
      tx = Math.min(0, Math.max(W - W * k, tx));
      ty = Math.min(0, Math.max(H - H * k, ty));
    }
    function apply(){
      clamp();
      layer.setAttribute('transform', 'translate(' + tx + ',' + ty + ') scale(' + k + ')');
      var inv = 1 / k;
      marks.forEach(function(m){
        var x = +m.dataset.x, y = +m.dataset.y;
        m.setAttribute('transform', 'translate(' + x + ',' + y + ') scale(' + inv + ') translate(' + (-x) + ',' + (-y) + ')');
      });
      stage.classList.toggle('zoomed', k > 1.001);
    }
    function pt(cx, cy){            // 螢幕座標 → SVG 座標
      var p = svg.createSVGPoint(); p.x = cx; p.y = cy;
      return p.matrixTransform(svg.getScreenCTM().inverse());
    }
    function zoomAt(f, sx, sy){     // 以 SVG 座標 (sx, sy) 為中心縮放 f 倍
      var nk = Math.min(KMAX, Math.max(1, k * f)); f = nk / k;
      tx = sx - (sx - tx) * f; ty = sy - (sy - ty) * f; k = nk; apply();
    }
    stage.querySelectorAll('.m-zoom button').forEach(function(b){
      b.addEventListener('click', function(){
        if (b.dataset.z === 'reset') { k = 1; tx = ty = 0; apply(); return; }
        zoomAt(b.dataset.z === 'in' ? 1.6 : 1 / 1.6, W / 2, H / 2);
      });
    });
    svg.addEventListener('wheel', function(e){
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      var p = pt(e.clientX, e.clientY);
      zoomAt(Math.exp(-e.deltaY * 0.01), p.x, p.y);
    }, {passive: false});
    svg.addEventListener('dblclick', function(e){
      e.preventDefault(); var p = pt(e.clientX, e.clientY); zoomAt(e.shiftKey ? 1 / 2 : 2, p.x, p.y);
    });
    // 拖曳平移與雙指縮放
    var pts = {}, last = null, moved = 0, pinch = null;
    svg.addEventListener('pointerdown', function(e){
      pts[e.pointerId] = {x: e.clientX, y: e.clientY}; moved = 0;
      var ids = Object.keys(pts);
      if (ids.length === 2) {
        var a = pts[ids[0]], b = pts[ids[1]];
        pinch = {d: Math.hypot(a.x - b.x, a.y - b.y), k: k};
      }
      last = {x: e.clientX, y: e.clientY};
    });
    svg.addEventListener('pointermove', function(e){
      if (!pts[e.pointerId]) return;
      pts[e.pointerId] = {x: e.clientX, y: e.clientY};
      var ids = Object.keys(pts);
      if (ids.length === 2 && pinch) {
        var a = pts[ids[0]], b = pts[ids[1]];
        var c = pt((a.x + b.x) / 2, (a.y + b.y) / 2);
        zoomAt(pinch.k * Math.hypot(a.x - b.x, a.y - b.y) / pinch.d / k, c.x, c.y);
        moved += 10; return;
      }
      if (k <= 1.001 || !last) return;
      var p0 = pt(last.x, last.y), p1 = pt(e.clientX, e.clientY);
      tx += p1.x - p0.x; ty += p1.y - p0.y; moved += Math.abs(e.clientX - last.x) + Math.abs(e.clientY - last.y);
      last = {x: e.clientX, y: e.clientY}; apply();
      if (moved > 4 && !svg.hasPointerCapture(e.pointerId)) svg.setPointerCapture(e.pointerId);
    });
    function up(e){ delete pts[e.pointerId]; if (Object.keys(pts).length < 2) pinch = null; last = null; }
    svg.addEventListener('pointerup', up); svg.addEventListener('pointercancel', up);
    // 拖曳之後放開滑鼠，不要順便點進連結
    svg.addEventListener('click', function(e){ if (moved > 4) { e.preventDefault(); e.stopPropagation(); } }, true);
    apply();
  });
})();
"""

ZOOM_CSS = """
.m-stage{position:relative; overflow:hidden; border-radius:6px}
.m-stage .m-svg{touch-action:pan-y; user-select:none; -webkit-user-select:none}
.m-stage.zoomed .m-svg{touch-action:none; cursor:grab}
.m-stage.zoomed .m-svg:active{cursor:grabbing}
.m-land,.p-leg,.m-lead,.m-pole,.m-cont-ul{vector-effect:non-scaling-stroke}
.m-zoom{position:absolute; right:.5rem; top:.5rem; z-index:2; display:flex; flex-direction:column;
  border-radius:6px; overflow:hidden; box-shadow:0 1px 4px rgba(0,0,0,.3)}
.m-zoom button{font:600 1.05rem/1 "Inter","Helvetica Neue",Arial,sans-serif; width:2rem; height:2rem;
  border:0; border-top:1px solid #ddd6cc; background:#fbfaf7; color:#13222a; cursor:pointer}
.m-zoom button:first-child{border-top:0}
.m-zoom button:hover{background:#fff}
.m-zoom button:focus-visible{outline:2px solid #1d3550; outline-offset:-2px}
"""


MAP_JS = """
(function(){
  var box = document.querySelector('.worldmap');
  if(!box) return;
  var btns = box.querySelectorAll('.wm-tabs button');
  function show(y){
    box.querySelectorAll('[data-year]').forEach(function(el){
      if(el.tagName === 'BUTTON'){ el.setAttribute('aria-pressed', el.dataset.year === y); return; }
      if(el.dataset.year === y) el.removeAttribute('hidden'); else el.setAttribute('hidden','');
    });
    box.querySelector('.wm-year').textContent = y;
  }
  btns.forEach(function(b){ b.addEventListener('click', function(){ show(b.dataset.year); }); });
  // SVG 沒有 z-index：滑到哪支旗子，就把它移到最上層，標籤卡才不會被別的旗子壓住
  box.querySelectorAll('.m-item').forEach(function(a){
    function lift(){ if(a.parentNode.lastElementChild !== a) a.parentNode.appendChild(a); }
    a.addEventListener('mouseenter', lift); a.addEventListener('focus', lift);
  });
})();
"""

MAP_CSS = """
/* 世界地圖 */
.worldmap{margin:2.2rem 0 2.6rem}
.wm-head{display:flex; justify-content:space-between; align-items:flex-end; gap:1rem;
  flex-wrap:wrap; border-bottom:1px solid var(--rule); margin-bottom:1rem; padding-bottom:.35rem}
.worldmap h2{border:0; margin:0; padding:0; font-size:1.35rem; letter-spacing:.02em}
.wm-tabs{display:flex; gap:.3rem; flex-wrap:wrap}
.wm-tabs button{font:inherit; font-size:.82rem; padding:.18rem .6rem; cursor:pointer;
  border:1px solid var(--rule); background:var(--surface); color:var(--muted); border-radius:2px}
.wm-tabs button[aria-pressed="true"]{background:#1d3550; border-color:#1d3550; color:#fff}
.wm-board{background:#b0a497; border-radius:14px; padding:1.1rem 1.1rem 1.2rem;
  background-image:radial-gradient(rgba(255,255,255,.09) 1px,transparent 1.3px),
    radial-gradient(rgba(0,0,0,.07) 1px,transparent 1.3px);
  background-size:5px 5px,7px 7px; background-position:0 0,2px 3px;
  box-shadow:inset 0 0 0 1px rgba(0,0,0,.08), 0 2px 10px rgba(19,34,42,.12);
  font-family:"Inter","Helvetica Neue",Arial,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
.m-svg{display:block; width:100%; height:auto}
.m-year[hidden]{display:none}
.m-land{fill:#1e262d; stroke:#b0a497; stroke-width:.55; stroke-linejoin:round}
.m-cont{font-size:10.5px; font-weight:600; fill:#f4efe8; letter-spacing:.03em}
.m-cont-ul{stroke:#f4efe8; stroke-width:.8}
.m-lead{stroke:#fbf8f3; stroke-width:1.3}
.m-card{fill:#fbfaf7; filter:url(#m-shadow)}
.m-tba .m-card{stroke:#7a858c; stroke-dasharray:4 3; stroke-width:1}
.m-t{font-size:14px; font-weight:700; fill:#13222a}
.m-s{font-size:10.5px; fill:#26333b}
.m-pole{stroke:#e9e2d6; stroke-width:1.6}
.m-knob{fill:#d9ab33; stroke:#6e5414; stroke-width:.6}
.m-base{fill:rgba(0,0,0,.45)}
.m-flagface{fill:#fff; stroke-width:1.8; filter:url(#m-shadow)}
.m-flag-txt{font-size:10px; font-weight:700}
.m-flag{cursor:pointer; transition:transform .12s}
/* 平常只顯示圖釘；滑鼠移到圖釘（或鍵盤聚焦）才顯示標籤卡 */
.m-callout,.m-lead{opacity:0; pointer-events:none; transition:opacity .12s}
.m-item:hover .m-callout,.m-item:hover .m-lead,
.m-item:focus .m-callout,.m-item:focus .m-lead{opacity:1}
.m-item:hover .m-flag,.m-item:focus .m-flag{transform:scale(1.25)}
.m-item:hover .m-flagface,.m-item:focus .m-flagface{stroke-width:2.6}
.m-item:focus{outline:none}
.ms-strip{display:flex; gap:.9rem; margin-top:.9rem; align-items:stretch}
.ms-strip[hidden]{display:none}
.ms-flag{flex:0 0 auto; display:flex; align-items:center; gap:.6rem; color:#fff;
  background:#1d3550; padding:.6rem 1.6rem .6rem .9rem; font-size:.82rem; line-height:1.2;
  clip-path:polygon(0 0,calc(100% - 14px) 0,100% 50%,calc(100% - 14px) 100%,0 100%);
  border-radius:6px 0 0 6px}
.ms-flag svg{width:1.7rem; height:1.7rem; stroke:#fff}
.ms-flag b{display:block; font-size:1.55rem; font-weight:700; margin-top:.1rem}
.ms-cells{flex:1; display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
  background:#fbfaf7; border-radius:6px; box-shadow:0 1px 4px rgba(0,0,0,.18)}
.ms-cell{display:block; padding:.55rem .8rem .6rem; text-decoration:none; color:#13222a;
  border-left:1px solid #ddd6cc; min-width:0}
.ms-cell:first-child{border-left:0}
.ms-cell b{display:block; font-size:.95rem; font-weight:700; padding-left:.55rem;
  border-left:4px solid var(--c); margin-bottom:.25rem; line-height:1.15}
.ms-cell span{display:flex; align-items:center; gap:.35rem; font-size:.78rem; color:#33414a;
  line-height:1.3; margin-top:.12rem}
.ms-cell span svg{flex:0 0 auto; width:.8rem; height:.8rem}
.ms-cell.tba span:last-child{font-style:italic; color:#5f6a71}
.ms-cell:hover b{text-decoration:underline}
.wm-note{font-size:.78rem; color:var(--muted); margin:.5rem 0 0}
@media (max-width:700px){
  .wm-board{padding:.6rem; border-radius:10px}
  .m-callout,.m-lead{display:none}
  .m-flag{transform:scale(1.6)}
  .m-cont{display:none} .m-cont-ul{display:none}
  .ms-strip{flex-direction:column}
  .ms-flag{clip-path:none; border-radius:6px; padding:.5rem .8rem}
  .ms-flag b{display:inline; font-size:1.1rem; margin-left:.3rem}
  .ms-flag br{display:none}
  .ms-cell{border-left:0; border-top:1px solid #ddd6cc}
  .ms-cell:first-child{border-top:0}
}
"""


def render_html(rows: list[dict], today: date, generated: str,
                deadlines: list[dict] | None = None,
                archive_count: int = 0) -> str:
    nxt = next_up(rows, today)
    years = sorted({r["year"] for r in rows if r.get("year")})

    grid_html = season_grid(rows, today)
    cards_html = conference_cards(rows, today)
    archive_link = (f'<p class="archive-link"><a href="past.html">'
                    f'查看已結束的 {archive_count} 場歷屆會議 →</a></p>'
                    if archive_count else "")
    dl_html = deadline_section(deadlines or [], today)
    map_html = world_map_section(rows, today)

    # --- Hero ---
    if nxt:
        place = ", ".join(x for x in (nxt.get("city"), nxt.get("country_or_state")) if x)
        hero = (
            '<section class="hero">'
            '<p class="lede">下一場</p>'
            f'<p class="title">{html.escape(display_name(nxt))}</p>'
            f'<p class="meta">{nxt["_start"].isoformat()}'
            f'{"" if nxt["_start"] == nxt["_end"] else " – " + nxt["_end"].isoformat()}'
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
<style>{CSS}{MAP_CSS}{ZOOM_CSS}</style>
</head>
<body>
<div class="wrap">

<header class="masthead">
  <h1>急診國際年會追蹤</h1>
  <p class="stamp">最後更新 {generated}</p>
</header>

{map_html}

{hero}

<section>
  <h2>即將舉行的場次!!!</h2>
  {cards_html}
  {archive_link}
</section>

<section style="margin-top:3rem">
  <h2>年度節奏</h2>
  {grid_html}
</section>

<section style="margin-top:3rem">
  <h2>投稿死線</h2>
  {dl_html}
</section>

<footer>
  <p>每週一自動擷取自各學會官方頁面：</p>
  <ul>
    <li>ACEP 美國急診醫師學會 — acep.org/sa/general-information/future-dates</li>
    <li>SAEM 美國學術急診醫學會 — saem.org/meetings-and-events/future-meetings</li>
    <li>IFEM 國際急診醫學聯盟 — ifem.cc/about_congress</li>
    <li>EuSEM 歐洲急診醫學會 — eusemcongress.org</li>
    <li>ASEM 亞洲急診醫學會 — asiansem.org/events-and-sponsorship</li>
    <li>SEMS 新加坡急診 — sems-online.com（WordPress API）</li>
    <li>HKCEM 香港急症科醫學院 — hkcem.org.hk（WordPress API）</li>
  </ul>
  <p>虛線框代表學會只公布了月份、尚未定案確切日期。已從官網下架的場次仍
     保留在<a href="past.html">歷屆場次</a>裡。訂閱
     <a href="conferences.ics">行事曆檔</a>，或下載
     <a href="conferences.csv">CSV</a>／<a href="conferences.json">JSON</a>。</p>
  <p>SEMS 與 HKCEM 的資料是從網站貼文推斷的，可靠度較低；報名與投稿死線
     一律請以官方公告為準。</p>
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
{MAP_JS}
{ZOOM_JS}
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# 歷屆會議地圖（past.html）
# --------------------------------------------------------------------------
# 跟主頁的世界地圖共用底圖與投影，但不插旗子：每場會議一顆圖釘，顏色代表
# 學會。同一城市辦過好幾次（例如新加坡、香港）時，圖釘會被輕輕推開，
# 再用細線連回真正的位置，避免疊成一顆。

# 城市欄空白時的預設地點：只用在學會自己的年會（名稱裡有學會簡稱）。
SOCIETY_HOME = {"SEMS": (1.35, 103.82), "HKCEM": (22.32, 114.17), "TSEM": (25.03, 121.57)}

PIN_R = 4.6          # 圖釘半徑（地圖座標）
PIN_GAP = 10.2       # 兩顆圖釘中心最少相距


def past_coords(r: dict) -> tuple[float, float] | None:
    soc = r.get("society", "")
    city = (r.get("city") or "").strip().lower()
    if soc in SOCIETY_HOME:
        # 學會自己的年會一律放在學會所在地的同一點（疊在一起）。城市欄空白時，
        # SEMS 只收名稱裡有 SEMS 的，避免把 SEMS 官網轉貼的他會公告算進來。
        if not city and soc == "SEMS" and "sems" not in (r.get("name") or "").lower():
            return None
        if city in {"", "singapore", "hong kong", "taiwan"} | TAIWAN_CITIES:
            return SOCIETY_HOME[soc]
    return coords_for(r)


TAIWAN_CITIES = {"taipei", "new taipei", "taichung", "changhua", "kaohsiung", "chiayi",
                 "pingtung", "tainan", "hsinchu", "taoyuan", "hualien"}

# 這幾個學會的年會固定在自己的城市（台灣、新加坡、香港），圖釘直接疊在
# 同一點、不錯開；其他學會的圖釘照樣會避開它們。
STACK_SOCIETIES = {"TSEM", "SEMS", "HKCEM"}


def spread_pins(anchors: list[tuple[float, float]],
                fixed: list[bool] | None = None) -> list[tuple[float, float]]:
    """
    把太近的圖釘推開。完全同點的先排成一圈，再反覆做「互相排斥＋輕輕拉回
    原位」，直到任兩顆至少相距 PIN_GAP。回傳每顆圖釘最後的位置。
    """
    fixed = fixed or [False] * len(anchors)
    pos = [list(a) for a in anchors]
    groups: dict[tuple, list[int]] = {}
    for i, (x, y) in enumerate(anchors):
        if not fixed[i]:
            groups.setdefault((round(x, 1), round(y, 1)), []).append(i)
    for idx in groups.values():
        if len(idx) > 1:
            rad = PIN_GAP * len(idx) / (2 * math.pi) + 1.5
            for k, i in enumerate(idx):
                a = -math.pi / 2 + 2 * math.pi * k / len(idx)
                pos[i][0] += rad * math.cos(a)
                pos[i][1] += rad * math.sin(a)
    for _ in range(300):
        moved = False
        for i in range(len(pos)):
            for j in range(i + 1, len(pos)):
                if fixed[i] and fixed[j]:
                    continue                    # 疊放的圖釘彼此不推開
                dx, dy = pos[j][0] - pos[i][0], pos[j][1] - pos[i][1]
                d = math.hypot(dx, dy)
                if d < PIN_GAP:
                    if d < 1e-6:
                        dx, dy, d = 1.0, 0.0, 1.0
                    push = (PIN_GAP - d) + 0.05
                    ux, uy = dx / d, dy / d
                    if fixed[i]:                # 只推動不固定的那一顆
                        pos[j][0] += ux * push; pos[j][1] += uy * push
                    elif fixed[j]:
                        pos[i][0] -= ux * push; pos[i][1] -= uy * push
                    else:
                        pos[i][0] -= ux * push / 2; pos[i][1] -= uy * push / 2
                        pos[j][0] += ux * push / 2; pos[j][1] += uy * push / 2
                    moved = True
        for i, (ax, ay) in enumerate(anchors):          # 輕輕拉回原位
            if fixed[i]:
                continue
            pos[i][0] += (ax - pos[i][0]) * 0.02
            pos[i][1] += (ay - pos[i][1]) * 0.02
        if not moved:
            break
    return [(x, y) for x, y in pos]


def past_map_section(rows: list[dict]) -> str:
    items = []
    for r in sorted(rows, key=lambda r: (r["_start"] or date.min)):
        c = past_coords(r)
        if c:
            items.append((r, project(*c)))
    if not items:
        return ""
    pins = spread_pins([xy for _, xy in items],
                       [r["society"] in STACK_SOCIETIES for r, _ in items])

    legs, dots, g = [], [], []
    for (r, (ax, ay)), (px, py) in zip(items, pins):
        col = SOCIETY_HEX.get(r["society"], "#555")
        if math.hypot(px - ax, py - ay) > 1.5:
            legs.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{px:.1f}" y2="{py:.1f}" class="p-leg"/>')
            dots.append((round(ax, 1), round(ay, 1)))
        title = f'{meeting_code(r["society"])} {r.get("year") or ""}'.strip()
        when, place = map_when(r), map_place(r)
        w = max(_text_w(title, 14) + 4, _text_w(when, 10.5) + 17, _text_w(place, 10.5) + 17) + 22
        h = 56
        bx = px + 12 if px + 12 + w < MAP_W - 4 else px - 12 - w
        by = min(max(py - h - 8, 4), MAP_H - h - 4)
        href = html.escape(r.get("source_url") or "#")
        g.append(
            f'<a href="{href}" class="m-item">'
            f'<title>{html.escape(display_name(r))}</title>'
            f'<g class="m-z" data-x="{px:.1f}" data-y="{py:.1f}">'
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{PIN_R}" class="p-pin" style="fill:{col}"/>'
            f'<g class="m-callout">'
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{w:.1f}" height="{h}" rx="4" class="m-card"/>'
            f'<rect x="{bx + 7:.1f}" y="{by + 8:.1f}" width="4" height="15" rx="1" fill="{col}"/>'
            f'<text x="{bx + 16:.1f}" y="{by + 21:.1f}" class="m-t">{html.escape(title)}</text>'
            f'<use href="#mi-cal" x="{bx + 16:.1f}" y="{by + 27.5:.1f}" width="9" height="9"/>'
            f'<text x="{bx + 29:.1f}" y="{by + 35.5:.1f}" class="m-s">{html.escape(when)}</text>'
            f'<use href="#mi-pin" x="{bx + 16:.1f}" y="{by + 40.5:.1f}" width="9" height="9"/>'
            f'<text x="{bx + 29:.1f}" y="{by + 48.5:.1f}" class="m-s">{html.escape(place)}</text>'
            f'</g></g></a>')
    # 疊放的圖釘：在同一點加一個小數字，顯示疊了幾場
    stacks: dict[tuple, list] = {}
    for (r, _), (px, py) in zip(items, pins):
        if r["society"] in STACK_SOCIETIES:
            stacks.setdefault((round(px, 1), round(py, 1), r["society"]), []).append(r)
    badges = "".join(
        f'<g class="m-z" data-x="{x:.1f}" data-y="{y:.1f}"><g class="p-stack"><circle cx="{x + 4.6:.1f}" cy="{y - 4.6:.1f}" r="4.4"/>'
        f'<text x="{x + 4.6:.1f}" y="{y - 2.6:.1f}" text-anchor="middle">{len(v)}</text></g></g>'
        for (x, y, _), v in stacks.items() if len(v) > 1)
    anchor_dots = "".join(f'<g class="m-z" data-x="{x}" data-y="{y}">'
                          f'<circle cx="{x}" cy="{y}" r="1.6" class="p-anchor"/></g>'
                          for x, y in sorted(set(dots)))

    present = [s for s in SOCIETY_META if any(r["society"] == s for r, _ in items)]
    legend = "".join(
        f'<span class="p-key"><i style="background:{SOCIETY_HEX.get(s, "#555")}"></i>'
        f'{html.escape(meeting_code(s))}'
        f'<small>{sum(1 for r, _ in items if r["society"] == s)}</small></span>'
        for s in present)
    missing = len(rows) - len(items)
    note = (f"另有 {missing} 場沒有可辨識的地點（例如線上會議），只列在下方清單。"
            if missing else "")

    svg = (f'<svg class="m-svg" viewBox="0 0 {MAP_W} {MAP_H:.0f}" role="img" '
           f'aria-label="歷屆會議地圖">'
           f'<defs>{ICON_CAL_SVG}{ICON_PIN_SVG}'
           f'<filter id="m-shadow" x="-10%" y="-10%" width="120%" height="140%">'
           f'<feDropShadow dx="0" dy="1.5" stdDeviation="1.6" flood-opacity=".28"/>'
           f'</filter></defs>'
           f'<g class="m-layer"><path d="{COUNTRIES_PATH}" class="m-land"/>'
           f'{"".join(legs)}{anchor_dots}<g class="p-pins">{"".join(g)}</g>{badges}</g></svg>')
    return (
        '<section class="worldmap pastmap" aria-labelledby="pm-title">'
        '<div class="wm-head">'
        f'<h2 id="pm-title">歷屆會議地圖 <span class="wm-year">{len(items)} 場</span></h2>'
        '</div>'
        f'<div class="wm-board"><div class="m-stage">{ZOOM_CONTROLS}{svg}</div><div class="p-legend">{legend}</div></div>'
        '<p class="wm-note">每顆圖釘是一場會議，顏色代表學會；同一城市辦過多次時圖釘會稍微錯開，'
        f'細線連回實際位置。台灣、新加坡、香港的學會年會固定在當地舉辦，圖釘疊在同一點，右上角數字是場數。'
        f'滑鼠移到圖釘上可看名稱、日期與地點。{ZOOM_HINT}{note}</p>'
        '</section>')


PAST_MAP_CSS = """
.p-pin{stroke:#fff; stroke-width:1.3; cursor:pointer; transition:r .1s}
.m-item:hover .p-pin,.m-item:focus .p-pin{r:6.4; stroke:#13222a}
.p-leg{stroke:#efe8dc; stroke-width:.9; opacity:.85}
.p-anchor{fill:#efe8dc}
.p-stack{pointer-events:none}
.p-stack circle{fill:#13222a; stroke:#fff; stroke-width:.8}
.p-stack text{font-size:6px; font-weight:700; fill:#fff}
.p-legend{display:flex; flex-wrap:wrap; gap:.35rem .9rem; margin-top:.8rem; padding:.55rem .8rem;
  background:#fbfaf7; border-radius:6px; box-shadow:0 1px 4px rgba(0,0,0,.18); font-size:.82rem; color:#13222a}
.p-key{display:inline-flex; align-items:center; gap:.35rem; white-space:nowrap}
.p-key i{width:.8rem; height:.8rem; border-radius:50%; border:1.5px solid #fff;
  box-shadow:0 0 0 1px rgba(0,0,0,.25)}
.p-key small{color:#7a858c; font-size:.72rem}
@media (max-width:700px){ .p-pin{r:7} }
"""

PAST_MAP_JS = """
(function(){
  document.querySelectorAll('.pastmap .m-item').forEach(function(a){
    function lift(){ if(a.parentNode.lastElementChild !== a) a.parentNode.appendChild(a); }
    a.addEventListener('mouseenter', lift); a.addEventListener('focus', lift);
  });
})();
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
<style>{CSS}{MAP_CSS}{ZOOM_CSS}{PAST_MAP_CSS}</style>
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
    尚未結束的場次與投稿死線都在主頁。</p>
</section>

{past_map_section(rows)}

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
<script>{PAST_MAP_JS}{ZOOM_JS}</script>
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
