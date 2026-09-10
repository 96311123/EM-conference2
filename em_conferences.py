#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
em_conferences.py
-----------------
擷取四大急診醫學會年會的「名稱 / 日期 / 地點」。

  ACEP  – American College of Emergency Physicians (Scientific Assembly)
  SAEM  – Society for Academic Emergency Medicine (Annual Meeting)
  IFEM  – International Federation for Emergency Medicine (ICEM / Global Congress)
  EUSEM – European Society for Emergency Medicine (European EM Congress)

設計原則
========
1. 不抓首頁。四個學會的首頁都沒有年會日期，真正的資料在專屬頁面
   （ACEP /sa/general-information/future-dates、SAEM /meetings-and-events/future-meetings
   等），先鎖定這些「來源頁」再解析。
2. 不綁 CSS class。這些都是 CMS（Sitefinity / NationBuilder / Optimizely / WordPress）
   產生的頁面，class 名稱改版就壞。改用 soup.get_text("\n") 轉純文字後做
   行導向 + 正規表示式解析 —— 對改版容忍度高很多。
3. parse_* 函式只吃「純文字」，所以可以離線用固定樣本做單元測試，
   不必每次連網。

用法
====
    python em_conferences.py                     # 印出表格
    python em_conferences.py --json out.json     # 存 JSON
    python em_conferences.py --csv  out.csv      # 存 CSV
    python em_conferences.py --only ACEP SAEM    # 只跑指定學會
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import date
from typing import Callable, Iterable

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# 基本設定
# --------------------------------------------------------------------------

# 有些學會網站（IFEM 就是）會擋掉自我宣告為 bot 的 User-Agent，
# 從 GitHub Actions 的資料中心 IP 連過去更容易被擋。用一般瀏覽器的
# 完整標頭組合可以通過大多數這類基本過濾。
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}
TIMEOUT = 30
POLITE_DELAY = 2.0          # 每次請求之間的間隔（秒），別把人家網站打爆

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
MONTHS.update({m[:3].lower(): i for m, i in list(MONTHS.items())})
MONTH_RE = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
DASH = r"[-–—]"             # hyphen / en dash / em dash 都要吃


@dataclass
class Conference:
    society: str
    name: str
    year: int | None = None
    date_text: str = ""      # 原文日期字串，保留供人工核對
    start: str = ""          # ISO YYYY-MM-DD
    end: str = ""
    city: str = ""
    country_or_state: str = ""
    venue: str = ""
    source_url: str = ""
    note: str = ""

    def as_row(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# 共用工具
# --------------------------------------------------------------------------

def fetch_text(url: str, session: requests.Session | None = None) -> str:
    """
    抓網頁 → 轉成乾淨的純文字（每個 block 一行）。

    被 403 擋下時會重試一次：先訪問該網域首頁取得 cookie，再回頭抓目標頁。
    很多網站的基本防爬是「沒有 session cookie 就擋」，先暖身通常就過了。
    """
    s = session or requests.Session()
    r = s.get(url, headers=HEADERS, timeout=TIMEOUT)

    if r.status_code in (403, 429):
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}/"
        try:
            s.get(root, headers=HEADERS, timeout=TIMEOUT)
            time.sleep(1.5)
            r = s.get(url, headers={**HEADERS, "Referer": root}, timeout=TIMEOUT)
        except Exception:
            pass

    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return html_to_text(r.text)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [re.sub(r"[\u00a0\u200b]", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def iso(y: int, m: int, d: int) -> str:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return ""


def parse_date_range(s: str, default_year: int | None = None) -> tuple[str, str, str]:
    """
    把常見的日期區間字串轉成 (start_iso, end_iso, matched_text)。
    支援：
        10/25/2027 - 10/28/2027        (ACEP)
        May 18-21, 2027                (SAEM)
        May 15-18                      (SAEM，年份缺漏 → 用 default_year)
        9-13 JUNE 2026                 (IFEM)
        23-27 September 2026           (EUSEM)
        28 September to 1 October 2025 (跨月)
        June 9, 2026 – June 13, 2026   (完整雙端)
    找不到就回傳 ("", "", "")。
    """
    # 1) M/D/YYYY - M/D/YYYY
    m = re.search(rf"(\d{{1,2}})/(\d{{1,2}})/(\d{{4}})\s*{DASH}\s*"
                  rf"(\d{{1,2}})/(\d{{1,2}})/(\d{{4}})", s)
    if m:
        m1, d1, y1, m2, d2, y2 = map(int, m.groups())
        return iso(y1, m1, d1), iso(y2, m2, d2), m.group(0)

    # 2) 跨月：28 September to 1 October 2025 / 28 Sep – 1 Oct 2025
    m = re.search(rf"(\d{{1,2}})\s+({MONTH_RE})\s*(?:to|{DASH})\s*"
                  rf"(\d{{1,2}})\s+({MONTH_RE})\s+(\d{{4}})", s, re.I)
    if m:
        d1, mon1, d2, mon2, y = m.group(1), m.group(2), m.group(3), m.group(4), int(m.group(5))
        return (iso(y, MONTHS[mon1[:3].lower()], int(d1)),
                iso(y, MONTHS[mon2[:3].lower()], int(d2)), m.group(0))

    # 3) 跨月（月在前）：September 28 - October 1, 2025
    m = re.search(rf"({MONTH_RE})\s+(\d{{1,2}})\s*{DASH}\s*"
                  rf"({MONTH_RE})\s+(\d{{1,2}}),?\s*(\d{{4}})", s, re.I)
    if m:
        mon1, d1, mon2, d2, y = m.group(1), int(m.group(2)), m.group(3), int(m.group(4)), int(m.group(5))
        return (iso(y, MONTHS[mon1[:3].lower()], d1),
                iso(y, MONTHS[mon2[:3].lower()], d2), m.group(0))

    # 4) 日-日 月 年：9-13 JUNE 2026 / 19 – 23 June 2024
    m = re.search(rf"(\d{{1,2}})\s*{DASH}\s*(\d{{1,2}})\s+({MONTH_RE})\.?,?\s*(\d{{4}})?",
                  s, re.I)
    if m:
        d1, d2, mon = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int(m.group(4)) if m.group(4) else default_year
        if y:
            mi = MONTHS[mon[:3].lower()]
            return iso(y, mi, d1), iso(y, mi, d2), m.group(0)

    # 5) 月 日-日, 年：May 18-21, 2027 / May 15-18（年份可缺）
    m = re.search(rf"({MONTH_RE})\.?\s+(\d{{1,2}})\s*{DASH}\s*(\d{{1,2}})(?:,?\s*(\d{{4}}))?",
                  s, re.I)
    if m:
        mon, d1, d2 = m.group(1), int(m.group(2)), int(m.group(3))
        y = int(m.group(4)) if m.group(4) else default_year
        if y:
            mi = MONTHS[mon[:3].lower()]
            return iso(y, mi, d1), iso(y, mi, d2), m.group(0)

    # 6) 單日：June 12, 2026
    m = re.search(rf"({MONTH_RE})\.?\s+(\d{{1,2}}),?\s*(\d{{4}})", s, re.I)
    if m:
        mon, d1, y = m.group(1), int(m.group(2)), int(m.group(3))
        mi = MONTHS[mon[:3].lower()]
        return iso(y, mi, d1), iso(y, mi, d1), m.group(0)

    return "", "", ""


def has_date(s: str) -> bool:
    return bool(parse_date_range(s, default_year=2000)[0])


def split_location(s: str) -> tuple[str, str]:
    """'San Francisco, CA' → ('San Francisco', 'CA')；'HAMBURG, GERMANY' 同理。"""
    if "," in s:
        a, b = s.rsplit(",", 1)
        return a.strip(), b.strip()
    return s.strip(), ""


# --------------------------------------------------------------------------
# 各學會的解析器（只吃純文字，方便離線測試）
# --------------------------------------------------------------------------

ACEP_URL = "https://www.acep.org/sa/general-information/future-dates"
SAEM_URL = "https://www.saem.org/meetings-and-events/future-meetings"
IFEM_URL = "https://www.ifem.cc/about_congress"
EUSEM_URL = "https://eusemcongress.org/"
EUSEM_FALLBACK_URL = "https://eusem.org/"


def parse_acep(text: str) -> list[Conference]:
    """
    ACEP Future Dates 版型：
        ACEP27 Scientific Assembly
        10/25/2027 - 10/28/2027
        Boston, Massachusetts
    """
    out, lines = [], text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^(ACEP\s?(\d{2})\s+Scientific Assembly)\s*$", ln.strip(), re.I)
        if not m:
            continue
        name, yy = m.group(1).strip(), int(m.group(2))
        window = lines[i + 1:i + 6]
        start = end = raw = ""
        city = state = ""
        for w in window:
            if not start:
                start, end, raw = parse_date_range(w)
                if start:
                    continue
            elif not city and "," in w and not has_date(w):
                city, state = split_location(w)
                break
        out.append(Conference(
            society="ACEP", name=name, year=2000 + yy,
            date_text=raw, start=start, end=end,
            city=city, country_or_state=state, source_url=ACEP_URL))
    return out


def parse_saem(text: str) -> list[Conference]:
    """
    SAEM Future Meetings 版型：
        SAEM27
        May 18-21, 2027 (Tuesday - Friday)
        San Francisco, CA
        San Francisco Marriott Marquis
    注意：SAEM 有時會漏掉年份（例如 'May 15-18'），用會議代號補回去。
    """
    out, lines = [], text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^SAEM\s?(\d{2})$", ln.strip(), re.I)
        if not m:
            continue
        yy = int(m.group(1))
        year = 2000 + yy
        start = end = raw = ""
        city = state = venue = ""
        for w in lines[i + 1:i + 7]:
            if re.match(r"^SAEM\s?\d{2}$", w.strip(), re.I):
                break
            if not start:
                start, end, raw = parse_date_range(w, default_year=year)
                if start:
                    continue
            if not city and "," in w:
                city, state = split_location(w)
                continue
            if city and not venue and not has_date(w):
                venue = w.strip()
                break
        out.append(Conference(
            society="SAEM", name=f"SAEM{yy} Annual Meeting", year=year,
            date_text=raw, start=start, end=end,
            city=city, country_or_state=state, venue=venue,
            source_url=SAEM_URL,
            note="year inferred from meeting code" if raw and not re.search(r"\d{4}", raw) else ""))
    return out


def parse_ifem(text: str, upcoming_only: bool = True) -> list[Conference]:
    """
    IFEM About Congress 版型：
        ICEM 2026 – HAMBURG, GERMANY
        9-13 JUNE 2026
        25th International Conference on Emergency Medicine
        Host: German Association for Emergency Medicine (DGINA)
    2027 起改名 IFEM Global Congress，且可能只寫 'JUNE 2027'（無日期）。
    """
    out, lines = [], text.splitlines()

    start_idx = 0
    if upcoming_only:
        for i, ln in enumerate(lines):
            if re.search(r"Upcoming\s+(IFEM|ICEM)", ln, re.I):
                start_idx = i
                break
    stop_idx = len(lines)
    if upcoming_only:
        for i in range(start_idx + 1, len(lines)):
            if re.search(r"Previous\s+Congress", lines[i], re.I):
                stop_idx = i
                break

    scope = lines[start_idx:stop_idx]
    for i, ln in enumerate(scope):
        m = re.match(rf"^(ICEM|IFEM)\s*(\d{{4}})\s*{DASH}\s*(.+)$", ln.strip(), re.I)
        if not m:
            continue
        brand, year, loc = m.group(1).upper(), int(m.group(2)), m.group(3)
        city, country = split_location(loc)
        start = end = raw = ""
        ordinal = host = ""
        for w in scope[i + 1:i + 6]:
            if re.match(rf"^(ICEM|IFEM)\s*\d{{4}}\s*{DASH}", w.strip(), re.I):
                break
            if not start:
                s_, e_, r_ = parse_date_range(w, default_year=year)
                if s_:
                    start, end, raw = s_, e_, r_
                    continue
                if re.match(rf"^{MONTH_RE}\s+\d{{4}}$", w.strip(), re.I):
                    raw = w.strip()          # 只有月份，尚未定案
                    continue
            if not ordinal and re.search(r"(Conference|Congress) on Emergency Medicine", w, re.I):
                ordinal = w.strip()
            if not host and w.strip().lower().startswith("host:"):
                host = w.split(":", 1)[1].strip()
        out.append(Conference(
            society="IFEM",
            name=ordinal or f"{brand} {year}",
            year=year, date_text=raw, start=start, end=end,
            city=city.title(), country_or_state=country.title(),
            source_url=IFEM_URL,
            note=f"Host: {host}" if host else ""))
    return out


def parse_eusem(text: str) -> list[Conference]:
    """
    EUSEM 的年會資訊在 eusemcongress.org（WordPress），不在 eusem.org 首頁。
    首頁上有 '25-27 September – Congress' 與 '23-24 September – Pre-courses'，
    年份從標題 'EUSEM Congress 2026' 取，城市多半只出現在行銷文案裡。
    """
    out = []
    ym = re.search(r"EUSEM\s+(?:Congress\s+)?(\d{4})", text, re.I)
    year = int(ym.group(1)) if ym else None

    start = end = raw = ""
    m = re.search(rf"(\d{{1,2}}\s*{DASH}\s*\d{{1,2}}\s+{MONTH_RE}[^\n]*)"
                  rf"{DASH}\s*Congress", text, re.I)
    if m:
        start, end, raw = parse_date_range(m.group(1), default_year=year)
    if not start:
        # 備援：找 'EUSEM2026 ... 23-27 September 2026' 這類敘述句
        m = re.search(rf"EUSEM\s*{year}[^\n]{{0,120}}", text, re.I) if year else None
        if m:
            start, end, raw = parse_date_range(m.group(0), default_year=year)

    # 城市：避開「EUSEM 2025 Congress was held in Vienna」這種回顧句，
    # 只收沒有「was held」且沒有其他年份的句子。
    city = ""
    patterns = [r"time in\s+([A-Z][\w\u00C0-\u017F'\- ]{2,25})",
                r"coming to\s+([A-Z][\w\u00C0-\u017F'\- ]{2,25})",
                r"will be held in\s+([A-Z][\w\u00C0-\u017F'\- ]{2,25})"]
    for pat in patterns:
        for cand in re.finditer(pat, text):
            ctx = text[max(0, cand.start() - 120): cand.end() + 40]
            if "was held" in ctx.lower():
                continue
            other_years = {int(y) for y in re.findall(r"\b(20\d{2})\b", ctx)}
            if year and other_years and year not in other_years:
                continue
            city = cand.group(1).strip().rstrip("!.,")
            break
        if city:
            break

    out.append(Conference(
        society="EUSEM",
        name=f"European Emergency Medicine Congress (EUSEM {year})" if year else "EUSEM Congress",
        year=year, date_text=raw, start=start, end=end, city=city,
        source_url=EUSEM_URL,
        note="城市欄位靠行銷文案推測，建議人工確認"))
    return out


# --------------------------------------------------------------------------
# EUSEM 加分做法：The Events Calendar REST API
# --------------------------------------------------------------------------

def eusem_via_wp_api(session: requests.Session | None = None) -> list[Conference]:
    """
    eusemcongress.org 是 WordPress + The Events Calendar 外掛
    （HTML meta 有 tec-api-version）。若外掛端點開著，直接拿結構化 JSON
    比解析 HTML 穩定得多。失敗就回空 list，讓 HTML parser 接手。
    """
    url = "https://eusemcongress.org/wp-json/tribe/events/v1/events"
    s = session or requests.Session()
    try:
        r = s.get(url, headers=HEADERS, timeout=TIMEOUT,
                  params={"per_page": 20, "status": "publish"})
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    out = []
    for ev in data.get("events", []):
        venue = ev.get("venue") or {}
        out.append(Conference(
            society="EUSEM", name=ev.get("title", ""),
            year=int(ev["start_date"][:4]) if ev.get("start_date") else None,
            date_text=f'{ev.get("start_date","")} – {ev.get("end_date","")}',
            start=(ev.get("start_date") or "")[:10],
            end=(ev.get("end_date") or "")[:10],
            city=venue.get("city", ""), country_or_state=venue.get("country", ""),
            venue=venue.get("venue", ""), source_url=url,
            note="from The Events Calendar REST API"))
    return out


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

SCRAPERS: dict[str, tuple[str, Callable[[str], list[Conference]]]] = {
    "ACEP": (ACEP_URL, parse_acep),
    "SAEM": (SAEM_URL, parse_saem),
    "IFEM": (IFEM_URL, parse_ifem),
    "EUSEM": (EUSEM_URL, parse_eusem),
}


def scrape(only: Iterable[str] | None = None) -> list[Conference]:
    session = requests.Session()
    results: list[Conference] = []
    targets = [k for k in SCRAPERS if not only or k in only]

    for i, society in enumerate(targets):
        url, parser = SCRAPERS[society]
        if i:
            time.sleep(POLITE_DELAY)
        try:
            if society == "EUSEM":
                api = eusem_via_wp_api(session)
                if api:
                    results.extend(api)
                    continue
            text = fetch_text(url, session)
            got = parser(text)
            if not got:
                print(f"[warn] {society}: 抓到頁面但沒解析出資料，版面可能改了 → {url}",
                      file=sys.stderr)
            results.extend(got)
        except Exception as exc:                      # 單一站失敗不影響其他站
            print(f"[error] {society}: {type(exc).__name__}: {exc}", file=sys.stderr)
            results.append(Conference(society=society, name="(fetch failed)",
                                      source_url=url, note=str(exc)))
    results.sort(key=lambda c: (c.start or "9999", c.society))
    return results


def print_table(rows: list[Conference]) -> None:
    hdr = ("SOCIETY", "CONFERENCE", "DATES", "PLACE")
    data = [(r.society,
             r.name[:46],
             (f"{r.start} → {r.end}" if r.start else (r.date_text or "TBA")),
             ", ".join(x for x in (r.city, r.country_or_state) if x) or "TBA")
            for r in rows]
    widths = [max(len(str(x[i])) for x in (list(data) + [hdr])) for i in range(4)]
    line = "  ".join("-" * w for w in widths)
    print("  ".join(h.ljust(w) for h, w in zip(hdr, widths)))
    print(line)
    for d in data:
        print("  ".join(str(c).ljust(w) for c, w in zip(d, widths)))


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape EM society annual conference info")
    ap.add_argument("--json", metavar="FILE")
    ap.add_argument("--csv", metavar="FILE")
    ap.add_argument("--only", nargs="*", choices=list(SCRAPERS))
    args = ap.parse_args()

    rows = scrape(args.only)
    print_table(rows)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([r.as_row() for r in rows], f, ensure_ascii=False, indent=2)
        print(f"\n[saved] {args.json}", file=sys.stderr)
    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].as_row())) if rows else None
            if w:
                w.writeheader()
                w.writerows(r.as_row() for r in rows)
        print(f"[saved] {args.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
