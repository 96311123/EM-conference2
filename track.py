#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
track.py — 每次排程執行的進入點。

流程：爬取 → 與上次結果比對 → 產出 docs/ 下的儀表板與行事曆 → 印出變動摘要。

一個重要的保護機制：如果某個學會這次「抓到頁面但解析不出東西」（通常是
對方改版），程式不會把該學會的舊資料清空，而是沿用上次的內容並標記為
stale。寧可顯示略舊的資料，也不要讓儀表板無聲地變成空白。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from em_conferences import scrape, SCRAPERS
from asia_societies import scrape_asia, ASIA_SOCIETIES
from deadlines import collect_deadlines, days_left, Deadline
from render import write_all

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "conferences.json"
DL_DATA = ROOT / "data" / "deadlines.json"
ARCHIVE = ROOT / "data" / "archive.json"
DOCS = ROOT / "docs"


# 歷史場次（ASEM 從 1998 年列到今天）全部保留在資料檔裡，供封存頁使用；
# 主頁只顯示今年起的場次，切分在 render 那一層做。
#
# 但通知要另外處理：一個 2004 年的會議被「新增」進資料庫，對你毫無意義，
# 只會把真正重要的變動淹掉。所以已經結束的場次不產生新增通知。
def is_past(r: dict, today: date) -> bool:
    end = r.get("end") or ""
    return bool(end) and end < today.isoformat()


# ------------------------------------------------------------------
# 累積式封存
# ------------------------------------------------------------------
# 學會官網只列「未來」的場次：SAEM27 開完就會從 Future Meetings 頁消失，
# ACEP 也一樣。如果資料檔只反映當下抓到的東西，歷史會隨時間流失，
# past.html 反而愈來愈空。
#
# 所以另外維護 archive.json：只進不出。每次抓到的場次都併進去，
# 官網撤下的仍然保留，只標記 delisted，並記下首末次看到的日期。
def akey(r: dict) -> str:
    return f'{r.get("society")}|{r.get("year")}|{r.get("start") or r.get("date_text") or ""}'


def load_archive() -> list[dict]:
    if ARCHIVE.exists():
        try:
            return json.loads(ARCHIVE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def update_archive(archive: list[dict], current: list[dict],
                   ok_societies: set[str], today: date) -> tuple[list[dict], int]:
    stamp = today.isoformat()
    idx = {akey(r): r for r in archive}
    added = 0

    for r in current:
        k = akey(r)
        if k in idx:
            keep = idx[k]
            first = keep.get("first_seen", stamp)
            keep.update(r)                    # 官網若更正了地點或會場，跟著更新
            keep["first_seen"] = first
            keep["last_seen"] = stamp
            keep["delisted"] = False
        else:
            r = dict(r, first_seen=stamp, last_seen=stamp, delisted=False)
            idx[k] = r
            added += 1

    # 這次成功抓取的學會，若某筆沒再出現，代表官網撤下了——保留但標記。
    # 只對成功抓取的學會這樣做：連線失敗時不能把整個學會誤判為全部撤下。
    live = {akey(r) for r in current}
    for k, r in idx.items():
        if r.get("society") in ok_societies and k not in live:
            r["delisted"] = True

    out = list(idx.values())
    out.sort(key=lambda r: (r.get("start") or "9999", r.get("society", "")))
    return out, added


def key(r: dict) -> str:
    return f'{r["society"]}|{r.get("year")}'


def load_previous() -> list[dict]:
    if DATA.exists():
        try:
            return json.loads(DATA.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def merge_with_previous(new: list[dict], old: list[dict]) -> tuple[list[dict], list[str]]:
    """某學會這次沒抓到任何資料時，沿用舊資料並標記。"""
    warnings = []
    got = {r["society"] for r in new if r.get("name") != "(fetch failed)"}
    merged = [r for r in new if r.get("name") != "(fetch failed)"]
    for society in list(SCRAPERS) + ASIA_SOCIETIES:
        if society in got:
            continue
        carried = [dict(r, note=(r.get("note", "") + " ｜ 本次擷取失敗，沿用上次結果").strip(" ｜"))
                   for r in old if r["society"] == society]
        if carried:
            merged.extend(carried)
            warnings.append(f"{society}：本次沒抓到資料，已沿用上次的 {len(carried)} 筆")
        else:
            warnings.append(f"{society}：本次沒抓到資料，且無舊資料可用")
    merged.sort(key=lambda r: (r.get("start") or "9999", r["society"]))
    return merged, warnings


def dl_key(d: dict) -> str:
    return f'{d.get("society")}|{d.get("track")}|{d.get("kind")}'


def diff_deadlines(new: list[dict], old: list[dict]) -> list[str]:
    """
    死線的變動類型跟年會日期不一樣，值得分開處理：
      · 未公布 → 公布：最重要的事件，代表投稿開跑了
      · 日期往後：延期，通常是好消息但要知道
      · 日期往前：極少見，但真的發生就必須立刻知道
    """
    o = {dl_key(d): d for d in old}
    n = {dl_key(d): d for d in new}
    out = []

    for k in sorted(n.keys() - o.keys()):
        d = n[k]
        when = d.get("date_iso") or "尚未公布"
        out.append(f'新死線　{d.get("society")} {d.get("track")}：{when}')

    for k in sorted(n.keys() & o.keys()):
        a, b = o[k].get("date_iso") or "", n[k].get("date_iso") or ""
        if a == b:
            continue
        d = n[k]
        if not a and b:
            out.append(f'已公布　{d.get("society")} {d.get("track")}：{b}（原為未公布）')
        elif a and not b:
            out.append(f'撤下　{d.get("society")} {d.get("track")}：原為 {a}，官網已移除')
        elif b > a:
            out.append(f'延期　{d.get("society")} {d.get("track")}：{a} → {b}')
        else:
            out.append(f'提前　{d.get("society")} {d.get("track")}：{a} → {b}　⚠ 請盡快確認')
    return out


def urgent_list(deadlines: list[dict], today: date, within: int = 30) -> list[str]:
    """列出 within 天內的死線，放進通知信裡，不必點進儀表板才看得到。"""
    rows = []
    for d in deadlines:
        if d.get("kind") == "open" or not d.get("date_iso"):
            continue
        try:
            left = (date.fromisoformat(d["date_iso"]) - today).days
        except ValueError:
            continue
        if 0 <= left <= within:
            mark = "🔴" if left <= 7 else "🟠"
            rows.append((left, f'{mark} 還有 {left} 天　{d.get("society")} '
                               f'{d.get("track")}　{d["date_iso"]}'))
    rows.sort()
    return [t for _, t in rows]


def diff(new: list[dict], old: list[dict]) -> list[str]:
    """只比對真正重要的欄位：日期與地點。"""
    fields = ("start", "end", "date_text", "city", "country_or_state", "venue")
    o = {key(r): r for r in old}
    n = {key(r): r for r in new}
    changes = []

    for k in sorted(n.keys() - o.keys()):
        r = n[k]
        changes.append(f"新增　{r['society']} {r.get('year')}："
                       f"{r.get('start') or r.get('date_text') or '日期未定'}"
                       f" @ {r.get('city') or '地點未定'}")
    for k in sorted(o.keys() - n.keys()):
        if is_past(o[k], today):
            continue          # 開完的會從官網下架是常態，已存進 archive.json
        changes.append(f"移除　{k.replace('|', ' ')}（官網已不再列出）")
    for k in sorted(n.keys() & o.keys()):
        for f in fields:
            a, b = (o[k].get(f) or ""), (n[k].get(f) or "")
            if a != b:
                changes.append(f"異動　{k.replace('|', ' ')} {f}：{a or '(空)'} → {b or '(空)'}")
    return changes


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    cols = ["society", "name", "year", "date_text", "start", "end",
            "city", "country_or_state", "venue", "source_url", "note"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def emit(text: str) -> None:
    """同時輸出到 console 與 GitHub Actions 的執行摘要頁。"""
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def set_output(**kw) -> None:
    """把旗標交給後續的 workflow step 使用。"""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in kw.items():
            f.write(f"{k}={v}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", metavar="JSON",
                    help="跳過爬取，直接用既有 JSON 重繪（測版面用）")
    args = ap.parse_args()

    old = load_previous()

    old_dl = (json.loads(DL_DATA.read_text(encoding="utf-8"))
              if DL_DATA.exists() else [])

    if args.offline:
        merged = json.loads(Path(args.offline).read_text(encoding="utf-8"))
        deadlines, warnings, changes, dl_changes = old_dl, [], [], []
        ok_societies = set()
    else:
        fresh = [asdict(c) for c in scrape()] + [asdict(c) for c in scrape_asia()]
        ok_societies = {r["society"] for r in fresh if r.get("name") != "(fetch failed)"}
        merged, warnings = merge_with_previous(fresh, old)
        changes = diff(merged, old)
        deadlines = [asdict(d) for d in collect_deadlines()]
        dl_changes = diff_deadlines(deadlines, old_dl)

    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    DL_DATA.write_text(json.dumps(deadlines, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    archive, n_new = update_archive(load_archive(), merged, ok_societies, date.today())
    ARCHIVE.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")

    # 網頁用累積封存而非當次抓取結果，這樣 past.html 只會愈來愈完整
    write_all(archive, DOCS, today=date.today(), deadlines=deadlines)
    write_csv(archive, DOCS / "conferences.csv")

    emit(f"## 追蹤結果 {date.today().isoformat()}\n")
    emit(f"本次抓到 {len(merged)} 筆場次、{len(deadlines)} 筆死線。"
         f"封存累計 {len(archive)} 筆"
         + (f"（新增 {n_new} 筆）" if n_new else "") + "。\n")

    urgent = urgent_list(deadlines, date.today())
    if urgent:
        emit("### 30 天內的死線\n")
        for u in urgent:
            emit(f"- {u}")
        emit("")

    if dl_changes:
        emit("### 死線變動\n")
        for c in dl_changes:
            emit(f"- {c}")
        emit("")
    if changes:
        emit("### 本次變動\n")
        for c in changes:
            emit(f"- {c}")
        emit("")
    else:
        emit("與上次相同，無變動。\n")
    if warnings:
        emit("### 需要注意\n")
        for w in warnings:
            emit(f"- {w}")
        emit("")

    # 交棒給 workflow：是否有變動、是否有解析失敗
    set_output(urgent=str(bool(urgent)).lower(),
               changed=str(bool(changes or dl_changes)).lower(),
               stale=str(bool(warnings)).lower(),
               count=len(merged))
    body = []
    if urgent:
        body += ["## 30 天內的死線", ""] + [f"- {u}" for u in urgent] + [""]
    if dl_changes:
        body += ["## 死線變動", ""] + [f"- {c}" for c in dl_changes] + [""]
    if changes:
        body += ["## 年會日期變動", ""] + [f"- {c}" for c in changes] + [""]
    (ROOT / "CHANGES.md").write_text("\n".join(body) or "（無變動）", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
