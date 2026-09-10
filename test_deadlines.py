# -*- coding: utf-8 -*-
"""離線測試死線解析器，樣本取自 2026-09 各學會頁面的真實文字。"""
from deadlines import (parse_saem_deadlines, parse_eusem_deadlines,
                       parse_acep_deadlines, parse_one_date, dedupe)

SAEM_TXT = """SAEM27 Submission Calendar
Great Ideas Deserve to Be Shared.  Bring Your Expertise to SAEM27.
Explore the submission opportunities below and submit your work for SAEM27 in San Francisco, May 18-21, 2027.
Advanced EM Workshops
Submissions Open: Monday, August 3, 2026
Submissions Close: Wednesday, September 16, 2026
Learn More
Didactics
Submissions Open: Monday, August 3, 2026
Submissions Close: Wednesday, September 23, 2026
Learn More
Innovations
Learn More
Submissions Open: Wednesday, September 2, 2026
Submissions Close: Monday, November 9, 2026
IGNITE!
Learn More
Submissions Open: Wednesday, September 2, 2026
Submissions Close: Monday, November 9, 2026
Abstracts
Learn more
Submissions Open: Monday, November 2, 2026
Submissions Close: Tuesday, January 5, 2027
Clinical Images
Learn more
Submissions Open: Monday, November 2, 2026
Submissions Close: Tuesday, January 12, 2027
Disclosure Deadlines
Presenter Disclosures & Bios due: April 1, 2027"""

EUSEM_TXT = """Abstract Submission - EUSEM 2026
Abstracts submission will open from 23 February to 15 April 2026, 17:00 CEST.
Late Breaking Abstracts submission : TBA (Please note that the late-breaking abstract deadline is not an extension of the general abstract submission).
Abstract submission unique deadline: 15 April, 17:00 CEST.
Decision notifications: 1st June 2026.
The Standard Rate Registration Deadline is 7 September 2026."""

ACEP_TXT = """ACEP Research Forum
The Research Forum, held annually in conjunction with the ACEP Scientific Assembly, is emergency medicine's premier research event.
Abstract submission for the 2026 ACEP Research Forum, taking place October 5-8 in Chicago, opens March 4, 2026.
We are thrilled to announce the official list of accepted abstracts for ACEP Research Forum 2026!"""


def show(rows):
    for r in sorted(rows, key=lambda x: (x.date_iso or "9999", x.track)):
        flag = {"high": "高", "medium": "中", "low": "低"}[r.confidence]
        print(f"  {r.society:6} {r.track:26} {r.kind:5} "
              f"{r.date_iso or '未公布':12} 信心{flag}  ← {r.date_text}")


print("SAEM"); show(parse_saem_deadlines(SAEM_TXT))
print("EUSEM"); show(dedupe(parse_eusem_deadlines(EUSEM_TXT)))
print("ACEP"); show(dedupe(parse_acep_deadlines(ACEP_TXT)))

print("\n單一日期解析")
for s, dy in [("Wednesday, September 16, 2026", None),
              ("15 April 2026, 17:00 CEST", None),
              ("15 April, 17:00 CEST", 2026),
              ("March 4, 2026", None),
              ("Tuesday, January 5, 2027", None),
              ("TBA", 2027)]:
    print(f"  {s!r:35} -> {parse_one_date(s, dy)}")
