#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asia_societies.py — ASEM、香港急症科醫學院、新加坡 SEMS 的年會擷取。

這三個站跟原本四個大學會的性質差很多，值得先講清楚
====================================================

**ASEM（亞洲急診醫學會）** 最好處理。events 頁面把歷屆 ACEM 從第 1 屆
列到第 14 屆，格式固定。兩個要注意的地方：清單**不是按時間排序**的
（第 12 屆排在第 13 屆後面），以及 ACEM 是兩年一次而非每年。

**SEMS（新加坡）** 是 WordPress.com 部落格，年會資訊發在 sems-asm 與
conference 兩個分類的貼文裡。有 REST API，直接取結構化 JSON 比解析
HTML 可靠得多。

**HKCEM（香港）** 是自架 WordPress，同樣有 REST API。但要誠實說：
HKCEM 網站上**沒有一個固定的「年會」頁面**——首頁的消息流混合了 AHA
課程、工作坊、電子報、周年活動。程式能做的是從貼文裡篩出像學術會議的
項目，這本質上是猜測，所以一律標記為低信心。真正的年度學術會議資訊
常常掛在另一個活動網站（例如 30 週年的 hkcemevent.com）。

因為信心差異大，這個模組抓到的東西都會在 note 欄位註明來源與可信度，
不會跟 ACEP／SAEM 那種欄位式資料混為一談。
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from datetime import date

import requests

from em_conferences import (Conference, HEADERS, TIMEOUT, POLITE_DELAY,
                            fetch_text, parse_date_range, split_location, has_date)

ASEM_URL = "https://www.asiansem.org/events-and-sponsorship"
HKCEM_URL = "https://hkcem.org.hk/"

# SSEM（Scientific Symposium on Emergency Medicine）是 HKCEM 的年度旗艦會議，
# 每年十月底在香港醫學專科學院舉行，有獨立網域。
#
# 但 ssem.hk 的 robots.txt 禁止自動存取，所以這裡**只當連結用，不爬它**。
# 改從 HKCEM 官網的 WordPress API 找 SSEM 公告貼文——同樣的資訊，
# 而且是對方允許抓取的來源。
SSEM_SITE = "https://www.ssem.hk/index"
SEMS_URL = "https://sems-online.com/"
IFEM_EVENTS_URL = "https://www.ifem.cc/events"

# 台灣急診醫學會的「國際學術活動」頁：列出 TSEM 認可積分的國外會議，
# 每筆都有主辦學會與日期。這是一個很好的**補漏來源**——例如 HKCEM 的
# SSEM 日期在這裡查得到，而 HKCEM 自己的官網不一定發了公告。
#
# 但它只給開始日期、不給區間，所以定位是「補主要來源沒抓到的」，
# 不覆蓋已經有完整日期區間的資料。
TSEM_INTL_URL = "https://www.sem.org.tw/Activity/C/Index"

# 主辦單位縮寫 → 本專案的學會代號
TSEM_ABBR = {"ACEP": "ACEP", "SAEM": "SAEM", "IFEM": "IFEM", "EUSEM": "EUSEM",
             "EuSEM": "EUSEM", "HKCEM": "HKCEM", "ASEM": "ASEM", "SEMS": "SEMS"}

ASIA_SOCIETIES = ["ASEM", "HKCEM", "SEMS"]


# --------------------------------------------------------------------------
# ASEM：Asian Conference on Emergency Medicine（兩年一次）
# --------------------------------------------------------------------------

def parse_asem(text: str) -> list[Conference]:
    """
    版型：
        14TH ACEM
        20-25 Oct 2028
        Singapore
    歷屆與未來場次混在同一份清單，且順序不保證。這裡全部收下，
    交由上層依日期排序、由儀表板判斷哪些已結束。
    """
    out, lines = [], text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^(\d{1,2})(?:ST|ND|RD|TH)\s+ACEM$", ln.strip(), re.I)
        if not m:
            continue
        ordinal = int(m.group(1))
        start = end = raw = ""
        city = country = ""
        for w in lines[i + 1:i + 5]:
            if re.match(r"^\d{1,2}(?:ST|ND|RD|TH)\s+ACEM$", w.strip(), re.I):
                break
            if not start:
                start, end, raw = parse_date_range(w)
                if start:
                    continue
            if start and not city and not has_date(w) and len(w) < 70:
                city, country = split_location(w)
                break
        year = int(start[:4]) if start else None
        out.append(Conference(
            society="ASEM",
            name=f"{ordinal}{_ord_suffix(ordinal)} Asian Conference on Emergency Medicine",
            year=year, date_text=raw, start=start, end=end,
            city=city, country_or_state=country, source_url=ASEM_URL,
            note="ACEM 兩年一次"))
    return out


def _ord_suffix(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# --------------------------------------------------------------------------
# WordPress REST API：SEMS 與 HKCEM 共用
# --------------------------------------------------------------------------

CONF_WORDS = re.compile(
    r"\bSSEM\b|scientific symposium|"
    r"annual scientific (meeting|congress|conference)|\bASM\b|"
    r"scientific meeting|annual meeting|conference|congress|symposium|"
    r"年會|學術研討會|周年.*(大會|會議)", re.I)

# 這些字樣代表是課程或行政公告，不是年會。先排除可以大幅減少雜訊。
NOISE_WORDS = re.compile(
    r"newsletter|bulletin|\bAHA\b|\bBLS\b|\bACLS\b|\bPALS\b|enrol|course|"
    r"workshop|examination|exam\b|tutorial|password|通訊|電子報", re.I)


def wp_posts(base: str, session: requests.Session, per_page: int = 30,
             search: str | None = None) -> list[dict]:
    """
    抓 WordPress REST API 的貼文。WordPress.com 與自架站都支援
    /wp-json/wp/v2/posts，回傳結構化 JSON，比解析佈景主題產生的 HTML
    穩定得多——換佈景不會影響 API。
    """
    url = base.rstrip("/") + "/wp-json/wp/v2/posts"
    params = {"per_page": per_page, "orderby": "date", "order": "desc"}
    if search:
        params["search"] = search
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT, params=params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()


def parse_wp_conferences(posts: list[dict], society: str, fallback_url: str,
                         note: str) -> list[Conference]:
    """從貼文標題與摘要裡篩出像年會的項目並抽出日期。"""
    out = []
    for p in posts:
        title = strip_html((p.get("title") or {}).get("rendered", ""))
        excerpt = strip_html((p.get("excerpt") or {}).get("rendered", ""))
        blob = f"{title} {excerpt}"
        if not CONF_WORDS.search(blob) or NOISE_WORDS.search(title):
            continue
        post_year = int((p.get("date") or "0000")[:4]) or None
        start, end, raw = parse_date_range(blob, default_year=post_year)
        if not start:
            continue
        is_ssem = re.search(r"\bSSEM\b|scientific symposium", blob, re.I)
        out.append(Conference(
            society=society,
            name=(f"Scientific Symposium on Emergency Medicine (SSEM {start[:4]})"
                  if is_ssem and society == "HKCEM" else title[:110]),
            year=int(start[:4]), date_text=raw, start=start, end=end,
            city="Hong Kong" if is_ssem and society == "HKCEM" else "",
            source_url=SSEM_SITE if (is_ssem and society == "HKCEM")
                       else (p.get("link") or fallback_url),
            note=note))
    return dedupe_conferences(out)


def dedupe_conferences(rows: list[Conference]) -> list[Conference]:
    seen, out = set(), []
    for r in rows:
        k = (r.society, r.start, r.end)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def scrape_asia(session: requests.Session | None = None) -> list[Conference]:
    import time
    s = session or requests.Session()
    out: list[Conference] = []

    # ASEM
    try:
        got = parse_asem(fetch_text(ASEM_URL, s))
        if not got:
            print(f"[warn] ASEM：抓到頁面但沒解析出資料 → {ASEM_URL}")
        out.extend(got)
    except Exception as exc:
        print(f"[error] ASEM: {type(exc).__name__}: {exc}")
        out.append(Conference(society="ASEM", name="(fetch failed)",
                              source_url=ASEM_URL, note=str(exc)))

    time.sleep(POLITE_DELAY)

    # SEMS（WordPress.com）
    posts = wp_posts(SEMS_URL, s)
    got = parse_wp_conferences(
        posts, "SEMS", SEMS_URL,
        "取自 SEMS 網站貼文（sems-asm／conference 分類），信心中等，建議人工確認")
    if not got:
        out.append(Conference(society="SEMS", name="年會資訊未在貼文中偵測到",
                              source_url=SEMS_URL + "category/sems-asm/",
                              note="SEMS ASM 公告不定期發布；請直接看 SEMS ASM 分類頁"))
    out.extend(got)

    time.sleep(POLITE_DELAY)

    # HKCEM（自架 WordPress）。年會公告可能發布於數月前、已掉出最近貼文，
    # 所以額外用關鍵字查詢把它撈回來。
    posts = wp_posts(HKCEM_URL, s)
    for kw in ("SSEM", "symposium"):
        posts.extend(wp_posts(HKCEM_URL, s, per_page=10, search=kw))
    got = parse_wp_conferences(
        posts, "HKCEM", HKCEM_URL,
        "取自 HKCEM 官網貼文；年會為 SSEM，詳情見 ssem.hk（該站禁止自動擷取）")
    if not got:
        out.append(Conference(society="HKCEM",
                              name="SSEM 下一屆日期尚未在 HKCEM 官網公布",
                              city="Hong Kong", source_url=SSEM_SITE,
                              note="HKCEM 年會為 SSEM，每年十月底；"
                                   "詳情見 ssem.hk（該站禁止自動擷取，需人工查看）"))
    out.extend(got)

    out.sort(key=lambda c: (c.start or "9999", c.society))
    return out


# --------------------------------------------------------------------------
# IFEM /events：補充 about_congress 沒有的區域性活動
# --------------------------------------------------------------------------

def parse_ifem_events(text: str) -> list[Conference]:
    """
    IFEM 的 events 頁列出各國成員學會的活動。這裡只收明確帶日期區間、
    且標題像大會的項目，作為 about_congress 的補充。
    """
    out, lines = [], text.splitlines()
    for i, ln in enumerate(lines):
        if not CONF_WORDS.search(ln) or NOISE_WORDS.search(ln):
            continue
        if len(ln.strip()) < 8 or len(ln.strip()) > 120:
            continue
        for w in lines[i:i + 4]:
            start, end, raw = parse_date_range(w)
            if start:
                out.append(Conference(
                    society="IFEM-events", name=ln.strip()[:110],
                    year=int(start[:4]), date_text=raw, start=start, end=end,
                    source_url=IFEM_EVENTS_URL,
                    note="取自 IFEM events 頁，為成員學會活動，信心低"))
                break
    return dedupe_conferences(out)


# --------------------------------------------------------------------------
# 台灣急診醫學會的國際學術活動列表（補漏用）
# --------------------------------------------------------------------------

def parse_tsem_intl(text: str) -> list[Conference]:
    """
    每筆的格式是：
        <會議名稱> <主辦單位全名 (縮寫)> YYYY/MM/DD 國際學術活動

    以「國際學術活動」這個固定結尾切開，再從每段的尾巴往回取縮寫與日期，
    比整行做正規表示式穩定——會議名稱裡常有括號和數字，容易誤配。
    """
    out = []
    for chunk in text.split("國際學術活動")[:-1]:
        tail = chunk.strip()[-200:]
        m = re.search(r"\(([A-Za-z]{3,8})\)\s*(\d{4})/(\d{1,2})/(\d{1,2})\s*$", tail)
        if not m:
            continue
        abbr, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        society = TSEM_ABBR.get(abbr) or TSEM_ABBR.get(abbr.upper())
        if not society:
            continue
        try:
            start = date(y, mo, d).isoformat()
        except ValueError:
            continue
        # 會議名稱在主辦單位全名之前。先只取最後一行（避免把前一筆的尾巴
        # 或頁面標題吃進來），再從主辦單位全名處切斷。
        ORG = (r"(American College|Society for|Hong Kong College|"
               r"International Federation|European Society|Asian Society|"
               r"Taiwan Society|Japanese Association|Korean Society)")
        lines = [x.strip() for x in tail[:m.start()].strip().splitlines() if x.strip()]
        name = lines[-1] if lines else ""
        cut = re.search(r"\s" + ORG, name)
        if cut:
            name = name[:cut.start()].strip()
        # get_text 會把各欄位拆成不同行，這時最後一行是主辦單位全名而非會議名，
        # 要再往前一行取。（用真實頁面文字測出來的情況，不是假設。）
        if not name or re.match(r"^" + ORG, name):
            name = lines[-2] if len(lines) >= 2 else name
        name = re.sub(r"\s{2,}", " ", name)[:110].strip()
        out.append(Conference(
            society=society, name=name or f"{society} {y}", year=y,
            date_text=f"{y}/{mo}/{d}", start=start, end=start,
            source_url=TSEM_INTL_URL,
            note="日期取自台灣急診醫學會國際學術活動列表；該表只列開始日期"))
    return dedupe_conferences(out)


def scrape_tsem(session: requests.Session | None = None) -> list[Conference]:
    s = session or requests.Session()
    try:
        return parse_tsem_intl(fetch_text(TSEM_INTL_URL, s))
    except Exception as exc:
        print(f"[warn] TSEM 國際學術活動列表擷取失敗：{type(exc).__name__}: {exc}")
        return []
