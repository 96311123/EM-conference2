# -*- coding: utf-8 -*-
"""
離線測試：ERC、AHA ReSS 解析器。

文字片段取自 2026-11 各官網頁面（ERC General Information、AHA ReSS 頁的 Key Dates、
AHA 會議總覽）。
"""
from em_conferences import parse_erc, parse_ress

ERC_TXT = """General Information: Save The Date - Resuscitation 2027
We are delighted to welcome you to the ERC Congress - Resuscitation 2027 in Glasgow!
The ERC Congress - Resuscitation 2027 will take place from September 30 to October 2, 2027
at the Scottish Event Campus in Glasgow, United Kingdom.
Resuscitation 2026, the European Resuscitation Council Congress, held in Milan from
17–19 September 2026, was a highly successful international event, bringing together
1,362 participants from 75 countries."""

RESS_KEYDATES_TXT = """Key Dates
Key Dates for #ReSS27
Date
Activity
Spring 2027
Watch for abstract submission
November 6–7, 2027
Resuscitation Science Symposium 2027 in Boston, Massachusetts
American Heart Association ECC Open Nominations"""

RESS_MEETINGS_TXT = """Resuscitation Science Symposium 2026
November 7–8, 2026
McCormick Place Convention Center
Chicago, Illinois
Scientific Sessions 2026
November 6–9, 2026
McCormick Place Convention Center
Chicago, Illinois"""

def test_erc():
    rows = {c.year: c for c in parse_erc(ERC_TXT)}
    assert rows[2027].start == "2027-09-30" and rows[2027].end == "2027-10-02"
    assert rows[2027].city == "Glasgow"
    assert rows[2027].country_or_state == "United Kingdom"
    assert rows[2027].venue == "Scottish Event Campus"
    assert rows[2026].start == "2026-09-17" and rows[2026].city == "Milan"


def test_ress_keydates():
    (c,) = parse_ress(RESS_KEYDATES_TXT)
    assert (c.year, c.start, c.end) == (2027, "2027-11-06", "2027-11-07")
    assert (c.city, c.country_or_state) == ("Boston", "Massachusetts")


def test_ress_meetings_list():
    (c,) = parse_ress(RESS_MEETINGS_TXT)          # Scientific Sessions 不能被當成 ReSS
    assert (c.year, c.start, c.city) == (2026, "2026-11-07", "Chicago")
    assert c.venue == "McCormick Place Convention Center"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
