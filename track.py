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

from em_conferences import scrape, scrape_acep_current, SCRAPERS
from asia_societies import scrape_asia, scrape_tsem, ASIA_SOCIETIES
from deadlines import collect_deadlines, days_left, Deadline
from render import write_all

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "conferences.json"
DL_DATA = ROOT / "data" / "deadlines.json"
ARCHIVE = ROOT / "data" / "archive.json"
HEALTH = ROOT / "data" / "health.json"
MANUAL = ROOT / "data" / "manual.json"
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


# ------------------------------------------------------------------
# 告警分級
# ------------------------------------------------------------------
# 「任何一個學會抓不到就整次標紅」是個壞設計：IFEM 被 403 擋、EuSEM 在兩個
# 會議週期之間沒有投稿頁、HKCEM 的 SSEM 每年年中才公告——這些都是已知且會
# 持續的狀態。每週紅一次，兩個月後你就會自動略過通知，等於沒有告警。
#
# 改成看「是否退步」：記錄每個學會最後一次成功的日期，
#   · 最近 90 天內成功過、現在卻失敗 → 真的壞了，標紅
#   · 從沒成功過，或已經很久沒成功 → 已知問題，只提示不中斷
REGRESSION_WINDOW = 90


def load_health() -> dict:
    if HEALTH.exists():
        try:
            return json.loads(HEALTH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def classify_failures(failed: list[str], health: dict, today: date
                      ) -> tuple[list[str], list[str]]:
    """把失敗的學會分成『退步』（要修）與『已知』（只提示）。"""
    regressions, known = [], []
    for society in failed:
        last = health.get(society)
        if not last:
            known.append(f"{society}：從未成功抓取過")
            continue
        try:
            age = (today - date.fromisoformat(last)).days
        except ValueError:
            known.append(f"{society}：健康紀錄異常（{last}）")
            continue
        if age <= REGRESSION_WINDOW:
            regressions.append(f"{society}：{last} 還抓得到，現在失敗了（{age} 天前）")
        else:
            known.append(f"{society}：已連續 {age} 天抓不到，屬已知狀態")
    return regressions, known


def apply_manual(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """
    人工指定的場次，優先於任何爬到的結果。

    存在的理由很實際：有些資訊爬蟲拿不到，也不該硬爬。ACEP 的 Future Dates
    頁不含當屆；SSEM 的官網禁止自動擷取。與其寫愈來愈脆弱的 parser 去猜，
    不如把已經人工查證過的日期直接寫進 data/manual.json——它不會因為對方
    改版而消失，也不需要等 parser 修好。

    同一個 (學會, 年份) 若爬蟲也抓到了，以人工這筆為準。
    """
    if not MANUAL.exists():
        return rows, []
    try:
        manual = json.loads(MANUAL.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return rows, [f"manual.json 格式錯誤，已略過：{exc}"]

    keys = {(m.get("society"), m.get("year")) for m in manual}
    kept = [r for r in rows if (r.get("society"), r.get("year")) not in keys]
    notes = [f'{m.get("society")} {m.get("year")}：使用人工指定的資料' for m in manual]
    return kept + manual, notes


def drop_placeholders(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """
    清掉沒有日期的佔位資料——但只在同一個學會已經有實際日期時才清。

    抓不到年會時，各 parser 會放一筆「尚未公布」的佔位資料，讓你知道
    程式有在看這個學會。問題是佔位資料沒有日期，永遠不會被判定為過期，
    會一直佔著主頁一張卡片；等真正的日期補上來之後，兩筆會並存。

    留著 date_text（例如 IFEM 只公布「JUNE 2027」）的不算佔位——
    那是真的場次，只是日期還沒定案。
    """
    dated = {r["society"] for r in rows if r.get("start")}
    kept, dropped = [], []
    for r in rows:
        if (r["society"] in dated and not r.get("start")
                and not r.get("date_text")):
            dropped.append(f'{r["society"]}：移除佔位資料「{r.get("name","")[:30]}」')
            continue
        kept.append(r)
    return kept, dropped


def fill_gaps(primary: list[dict], supplement: list[dict]) -> tuple[list[dict], list[str]]:
    """
    用補漏來源填主要來源沒抓到的 (學會, 年份)。

    刻意只填空缺、不覆蓋：主要來源給的是完整日期區間（例如 ACEP26 是
    10/5–10/8），補漏來源只有開始日期。拿單一天蓋掉完整區間是退步。
    """
    have = {(r.get("society"), r.get("year")) for r in primary}
    added, notes = [], []
    for r in supplement:
        k = (r.get("society"), r.get("year"))
        if k in have:
            continue
        have.add(k)
        added.append(r)
        notes.append(f'{r.get("society")} {r.get("year")} 由補漏來源填入')
    return primary + added, notes


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
    """
    只比對真正重要的欄位：日期與地點。

    已結束的場次不論新增或移除都不通知——歷史資料被補進來、或開完的會
    從官網下架，都是常態而非需要你知道的事，那些已經存進 archive.json。
    """
    fields = ("start", "end", "date_text", "city", "country_or_state", "venue")
    today = date.today()
    o = {key(r): r for r in old}
    n = {key(r): r for r in new}
    changes = []

    for k in sorted(n.keys() - o.keys()):
        r = n[k]
        if is_past(r, today):
            continue
        changes.append(f"新增　{r['society']} {r.get('year')}："
                       f"{r.get('start') or r.get('date_text') or '日期未定'}"
                       f" @ {r.get('city') or '地點未定'}")

    for k in sorted(o.keys() - n.keys()):
        if is_past(o[k], today):
            continue
        changes.append(f"移除　{k.replace('|', ' ')}（官網已不再列出）")

    for k in sorted(n.keys() & o.keys()):
        for f in fields:
            a, b = (o[k].get(f) or ""), (n[k].get(f) or "")
            if a != b:
                changes.append(f"異動　{k.replace('|', ' ')} {f}："
                               f"{a or '(空)'} → {b or '(空)'}")
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
        merged, gap_notes = apply_manual(merged)
        merged, dropped = drop_placeholders(merged)
        gap_notes.extend(dropped)
        deadlines, warnings, changes, dl_changes = old_dl, [], [], []
        ok_societies = set()
    else:
        fresh = [asdict(c) for c in scrape()] + [asdict(c) for c in scrape_asia()]
        # 補漏來源依可靠度排序：ACEP 官方當屆頁給完整區間，優先於
        # TSEM 只有開始日期的列表。fill_gaps 只填空缺，先到先得。
        supplement = ([asdict(c) for c in scrape_acep_current()]
                      + [asdict(c) for c in scrape_tsem()])
        fresh, gap_notes = fill_gaps(fresh, supplement)
        fresh, manual_notes = apply_manual(fresh)
        gap_notes.extend(manual_notes)
        fresh, dropped = drop_placeholders(fresh)
        gap_notes.extend(dropped)
        ok_societies = {r["society"] for r in fresh if r.get("name") != "(fetch failed)"}
        merged, warnings = merge_with_previous(fresh, old)
        try:
            deadlines = [asdict(d) for d in collect_deadlines()]
        except Exception as exc:
            print(f"[error] 死線擷取整體失敗：{type(exc).__name__}: {exc}")
            deadlines = old_dl

        # 通知計算包在 try 裡：它只是附加價值，不該有能力擋住資料落地與網頁更新。
        # 先前就是通知邏輯的一個 NameError 讓整站連續多次停止更新。
        try:
            changes = diff(merged, old)
            dl_changes = diff_deadlines(deadlines, old_dl)
        except Exception as exc:
            print(f"[error] 變動比對失敗（不影響資料與網頁）：{type(exc).__name__}: {exc}")
            changes, dl_changes = [], [f"變動比對發生錯誤：{exc}"]

    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    DL_DATA.write_text(json.dumps(deadlines, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    # 更新健康紀錄：這次成功的學會蓋上今天的日期
    health = load_health()
    for society in ok_societies:
        health[society] = date.today().isoformat()
    HEALTH.write_text(json.dumps(health, ensure_ascii=False, indent=2, sort_keys=True),
                      encoding="utf-8")

    failed = [w.split("：")[0] for w in warnings]
    regressions, known_issues = classify_failures(failed, health, date.today())

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
    if gap_notes:
        emit("### 由補漏來源填入\n")
        for g in gap_notes:
            emit(f"- {g}")
        emit("")

    if regressions:
        emit("### 需要修：原本抓得到，現在失敗\n")
        for w in regressions:
            emit(f"- {w}")
        emit("")
    if known_issues:
        emit("### 已知狀態（不影響其他學會）\n")
        for w in known_issues:
            emit(f"- {w}")
        emit("")
    if warnings:
        emit("<details><summary>本次未取得資料的學會</summary>\n")
        for w in warnings:
            emit(f"- {w}")
        emit("\n</details>\n")

    # 交棒給 workflow：是否有變動、是否有解析失敗
    set_output(urgent=str(bool(urgent)).lower(),
               changed=str(bool(changes or dl_changes)).lower(),
               stale=str(bool(regressions)).lower(),
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
