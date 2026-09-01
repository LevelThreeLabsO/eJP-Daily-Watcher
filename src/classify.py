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

PROMPT = """You are compiling two sections of Your Daily Phil, eJewishPhilanthropy's daily newsletter \
for major Jewish philanthropists, foundation staff, federation executives and nonprofit leaders.

TODAY IS {today}.

Everything below is measured from eJP's own archive of 1,096 published "What We're Watching" items \
and 428 "Major Gifts" items. Match it.

════════ SECTION 1 — "What We're Watching" ════════

THE TEST: something a person or institution CONVENED, SCHEDULED or is FORMALLY OBSERVING, that is \
happening today. Not "something that happened." If nobody put it on a calendar, it does not belong.

What actually appears, by measured share of the archive:
  31%  conferences, summits, conventions, general assemblies, retreats, convenings
  16%  Israeli government and political set-pieces — Knesset votes, party primaries, ministry
       program launches, an official state visit or ceremony
  14%  galas, benefit dinners, award ceremonies, tributes
  10%  US political set-pieces — a scheduled Senate vote, primary day, a White House meeting
  10%  campus and education events
   9%  festivals, film screenings, exhibition openings, cultural premieres
   8%  commemorations, memorials, yahrzeits, religious observances at scale
   2%  scheduled court hearings and oral arguments
   1%  organized disaster-relief efforts by Jewish organizations

Timing, measured: 59% are anchored to today ("today", "tonight", "this evening"). 18% use span
framing ("kicks off today and runs through Sunday", "concludes this afternoon", "is underway").
8% are tomorrow. Prefer today; tomorrow is acceptable when notable.

GEOGRAPHY: the section covers Jewish communities in the US, Israel, Europe, South America,
Australia, Canada and South Africa. Measured: 45% Israel, 45% US, and roughly 6% is diaspora
outside both — a London gala, a Buenos Aires commemoration, a Melbourne conference, a Montreal
council vote. Actively look for those; they are easy to miss and they belong.

Scale: local is fine IF it is a real convened event with a named organization — a city film
festival's opening night, a 150-rabbi conference, a historical society hosting an author all
appear. What disqualifies a small event is being routine programming, not being small.

NEVER include (these are 0% or near-0% of the archive):
  - antisemitism incidents, vandalism, assaults, a crowd's behavior at a match — ZERO of 1,096
  - military operations, strikes, arrests, casualties, troop deployments
  - a politician's statement, accusation or opinion
  - obituaries, unless a formal memorial event is being held
  - routine local programming: classes, webinars, playgroups, support groups, mahjong, book clubs
  - Zoom-only events unless genuinely major
  - sports results, crime, weather, general world news

════════ SECTION 2 — "Major Gifts" ════════

Gifts, pledges, grants and philanthropic commitments.

  - 43% Jewish or Israel-related, 57% general philanthropy. BOTH BELONG. A large gift to a
    secular university, hospital, museum or theater is squarely in this section.
  - THERE IS NO DOLLAR FLOOR. Median is $5.3M, but 16% of gifts with a figure are UNDER $1M and
    the smallest is $9,500. A $430,000 grant to launch a campus Hillel ran; so did $255,000 in
    small community grants.
  - 18% carry NO dollar figure at all. "Hundreds of thousands of shekels", "an undisclosed sum",
    donated medical supplies, a donated artwork, donated organs. These count.
  - Shekels and NIS are normal. Write both currencies when the source gives both.
  - Donors can be individuals, couples, families, foundations, corporations, banks, embassies or
    governments. Crowdfunding campaigns for a named cause count.
  - These forms all count, with their measured share of the archive:
        19%  capital projects — a new wing, building, campus or center funded by a gift
        19%  grant awards
        15%  research awards, endowed chairs, professorships, fellowships, scholarships
         8%  NAMING announcements — a building, center or program named for a donor
         5%  endowments
         2%  crowdfunding campaigns that hit a milestone
         2%  bequests and estate gifts
  - Geography: about 6% of gifts are diaspora — a London gala's total, a Vancouver synagogue
    bequest, a Venezuelan community campaign. Include them.

NEVER include:
  - political fundraising, campaign money, PACs
  - investment rounds, company revenue, earnings, acquisitions
  - government appropriations and budget line items
  - construction budgets
  - an institution's total annual fundraising, unless one gift is the story
  - a foundation's routine small-grant roundup with no notable recipient or donor

════════ OUTPUT ════════

A typical edition runs about 3 watching items and 2 gifts. Be selective; do not pad. If nothing
qualifies, return nothing for that section.

Write each "line" as ONE sentence in eJP's voice. Name the organization in full and the people
involved. State the amount and recipient, or the time framing. Say plainly what is happening.

  "The Jewish Federations of North America's National Young Leadership Cabinet retreat concludes
   today in Minneapolis."
  "The second annual Sephardic Rabbinic Conference kicks off today in New York City, bringing
   together over 150 rabbis and communal figures."
  "The Nashville Jewish Film Festival will host its opening night celebrations this evening."
  "A three-year, $430,000 grant from The Leon Levine Foundation will underwrite the first executive
   director and early programming at Clemson University's new professionally staffed Hillel."
  "Bank Hapoalim pledged NIS 5 million ($1.66 million) toward Rimon Farms' project to build
   therapeutic agricultural farms in Nahal Oz and Holit for western Negev communities."

DONOR IDENTIFICATION (the step eJP's team currently does by hand):
For a gift with no obvious Jewish or Israel connection on the recipient side, consider whether the
DONOR is Jewish or connected to the Jewish communal world — that is what makes a gift to a secular
university or hospital belong here. Say so in "why" when it is the reason you picked it.

Judge this on what you actually know about the person: their known philanthropy, their communal
roles, their own public statements about their background. NEVER infer someone's religion or
ethnicity from a surname alone — it is unreliable and offensive when wrong. If you do not know,
set jewish_angle to unclear and confidence to low, and let the editor check. A flagged maybe is
useful; a confident guess is not.

FRESHNESS: each headline shows how many hours ago it was published. This section runs early in the
morning, before the newsletter is assembled, so prefer items that point FORWARD — something opening,
convening, concluding or being observed today — over a wire report of something that already
finished. A story filed 20 hours ago about a concluded negotiation is not "what we're watching."
If the only thing an item tells you is that something already happened, leave it out.

Never invent a fact not present in the headline or summary. If you cannot tell whether it is
happening today, mark confidence low and say why.

HEADLINES:
{items}
"""



def classify(pool, today, batch=200):
    picks = []
    for i in range(0, len(pool), batch):
        chunk = pool[i:i + batch]
        listing = "\n".join(
            f"{n}. {x['title']}"
            + (f"  ({x['age_hours']}h ago)" if x.get("age_hours") is not None else "")
            + (f"\n   [{x.get('source','')}] {x.get('summary','')[:110]}" if x.get("summary") else
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
