#!/usr/bin/env python3
"""
Sort the candidate pool into the two Daily Phil sections, and write each item
the way eJP writes it.

The style rules below are lifted from the archive, not invented: 63% of "What
We're Watching" items are anchored to today, 21% use span framing ("kicked off",
"runs through", "concludes today"), and Major Gifts is 57% general philanthropy
with 18% carrying no dollar figure at all.
"""
import json, os, sys, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "section": {"type": "string",
                                "enum": ["watching", "major_gift", "neither"]},
                    "line": {"type": "string",
                             "description": "The item written in eJP house style, one sentence."},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "why": {"type": "string", "description": "Short reason for the editor."},
                },
                "required": ["id", "section", "confidence"],
            },
        }
    },
    "required": ["picks"],
}

PROMPT = """You are helping compile Your Daily Phil, eJewishPhilanthropy's daily newsletter. Its \
readers are major Jewish philanthropists, foundation staff, federation executives and nonprofit \
leaders. You are filling two sections from a pool of today's news headlines.

TODAY IS {today}.

SECTION 1 — "What We're Watching"
Things happening TODAY. Not next week, not next month. An item qualifies if it starts today, \
concludes today, or is underway today. Typical items:
  - a national or international convening beginning, running or ending today
  - a government or institutional announcement made today (Israeli ministries especially)
  - a court hearing, vote, election or ruling happening today
  - a notable cultural or commemorative moment today
Roughly 44% of real items are Israel-related, 35% American Jewish, 21% general interest. \
Include American items generously — this is not an Israel-only section.
EXCLUDE: routine local programming, chapter events, webinars, Zoom classes, admissions sessions, \
support groups, anything a national readership of funders would not plan a day around.

SECTION 2 — "Major Gifts"
Gifts, pledges, grants and major philanthropic commitments. IMPORTANT: only about 43% of these are \
Jewish or Israel-related — the rest is general philanthropy (a big university gift, a hospital \
donation, a foundation's new commitment, a billionaire's giving plans). Include both.
  - There is NO dollar minimum. 18% of real items carry no figure at all ("hundreds of thousands", \
"an undisclosed sum", donated equipment or supplies).
  - Amounts in shekels/NIS are completely normal and must be included.
  - Corporate and institutional donors count, not only individuals.
EXCLUDE: political fundraising, investment rounds, company revenue, construction budgets, \
government appropriations, and institutional campaign totals that aren't a single gift.

Anything not clearly fitting either section is "neither". Be selective — a typical edition runs \
about 3 watching items and 2 gifts. Do not pad.

For anything you pick, write "line" as ONE sentence in eJP's voice: name the organization and \
people, state the amount and recipient or the time framing, and say plainly what is happening. \
Examples of the register:
  "The Jewish Federations of North America's National Young Leadership Cabinet retreat concludes \
today in Minneapolis."
  "The Rohr Jewish Learning Institute's annual National Jewish Retreat kicked off last night and \
runs through Sunday in Miami."
  "Bank Hapoalim pledged NIS 5 million ($1.66 million) toward Rimon Farms' project to build \
therapeutic agricultural farms in Nahal Oz and Holit for western Negev communities affected by the \
Oct. 7 attacks."

Never invent a fact that isn't in the headline or summary. If the date is unclear, say so in "why" \
and mark confidence low rather than guessing.

HEADLINES:
{items}
"""


def classify(pool, today, batch=90):
    picks = []
    for i in range(0, len(pool), batch):
        chunk = pool[i:i + batch]
        listing = "\n".join(
            f"{n}. {x['title']}"
            + (f"\n   [{x.get('source','')}] {x.get('summary','')[:190]}" if x.get("summary") else
               f"  [{x.get('source','')}]")
            for n, x in enumerate(chunk, start=i))
        out = llm._call(PROMPT.format(today=today, items=listing),
                        schema=SCHEMA, max_tokens=16384)
        for p in out.get("picks", []):
            idx = p.get("id")
            if isinstance(idx, int) and 0 <= idx < len(pool) and p.get("section") != "neither":
                picks.append({**p, "item": pool[idx]})
    return picks


if __name__ == "__main__":
    import datetime as dt
    pool = json.load(open(f"{D}/feed_pool.json"))
    today = dt.date.today().isoformat()
    if not llm.available():
        print("no GEMINI_API_KEY"); sys.exit(1)
    print(f"classifying {len(pool)} candidates ({llm.calls_left()} model calls left today)...")
    picks = classify(pool, today)
    json.dump(picks, open(f"{D}/picks.json", "w"), indent=1)
    for sec, lab in [("watching", "WHAT WE'RE WATCHING"), ("major_gift", "MAJOR GIFTS")]:
        rows = [p for p in picks if p["section"] == sec]
        print(f"\n=== {lab} ({len(rows)}) ===")
        for p in rows:
            print(f"  • {p.get('line') or p['item']['title']}")
            print(f"    [{p['confidence']}] {p['item'].get('source','')} — {p['item'].get('link','')[:80]}")
