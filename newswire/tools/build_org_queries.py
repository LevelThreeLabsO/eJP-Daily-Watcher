#!/usr/bin/env python3
"""Turn the 990 watchlist into a handful of Google News queries.

The watchlist is 1,292 Jewish nonprofits with $1M+ revenue, built from the IRS Business
Master File in ~/ejp-lead-engine. One query per organization would be 1,292 sources and
1,292 HTTP requests every fifteen minutes, which is absurd. Google honours a long
disjunction, so this batches many names into each query instead — the largest orgs by
revenue in groups, crossed with giving vocabulary.

Names need real work before they are searchable, and that part decides whether the idea
works at all:

  - The BMF stores names upper-cased and suffixed, so they must be recased carefully. A
    naive "keep short all-caps tokens as acronyms" rule produced "Conference ON Jewish
    Material Claims", "Jewish National FUND" and "SAN Diego".
  - Legal names are frequently not what anyone calls the organization. No story says
    "Conference on Jewish Material Claims Against Germany"; they say "Claims Conference".
    Searching the legal name finds nothing, so the common ones are aliased by hand.
  - Some names are too generic to quote. "Jewish Home" and "Jewish Community Foundation"
    are real organizations AND phrases dozens of unrelated bodies use in every city, so
    they are dropped. A query that matches everything finds nothing.

    python3 tools/build_org_queries.py             # YAML for sources.yaml
    python3 tools/build_org_queries.py --names     # just the cleaned names, to eyeball
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

WATCHLIST = Path.home() / "ejp-lead-engine" / "data" / "watchlist.json"

SUFFIXES = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|LLC|LTD|"
    r"A NJ NONPROFIT CORPORATION|A NY NOT FOR PROFIT CORP)\.?$", re.I)

TOO_GENERIC = {
    "jewish home", "jewish community foundation", "jewish federation",
    "jewish community center", "jewish family service", "jewish family services",
    "hebrew home", "jewish board", "jewish community council", "united jewish foundation",
    "jewish united fund", "jewish communal fund", "jewish home lifecare",
    "jewish child care association", "hebrew rehabilitation center",
}

ACRONYMS = {"JCC", "YMHA", "YWHA", "ADL", "AJC", "UJA", "JDC", "NY", "NYC", "LA", "SF",
            "USA", "US", "UK", "DC", "NJ", "PA", "MA", "IL", "FL", "CA"}
SMALL = {"of", "and", "the", "for", "in", "on", "at", "to", "a", "an"}

ALIASES = {
    "conference on jewish material claims against germany": "Claims Conference",
    "hebrew university association jerusalem palestine": "Hebrew University of Jerusalem",
    "hadassah the womens zionist organization of america": "Hadassah",
    "jewish national fund -keren kayemeth leisrael": "Jewish National Fund",
    "american jewish joint distribution committee": "JDC",
    "united jewish appeal federation of jewish philanthropies of ny":
        "UJA-Federation of New York",
    "the young mens and young womens hebrew association": "92nd Street Y",
}

MAX_WORDS = 6
ORGS_PER_QUERY = 10
GIVING = ("(gift OR donation OR grant OR endowment OR bequest OR pledge OR "
          "donates OR donated OR gave OR campaign)")


def clean(name):
    n = " ".join(str(name).split())
    n = SUFFIXES.sub("", n).strip(" ,.-")

    alias = ALIASES.get(n.lower().strip())
    if alias:
        return alias

    words = []
    for i, w in enumerate(n.split()):
        bare = w.strip(".,&-")
        if bare.upper() in ACRONYMS:
            words.append(bare.upper())
        elif bare.lower() in SMALL and i > 0:
            words.append(bare.lower())
        else:
            words.append(w.title())
    n = re.sub(r"^The ", "", " ".join(words)).strip(" ,.-&")

    parts = n.split()
    if len(parts) > MAX_WORDS:
        n = " ".join(parts[:MAX_WORDS]).strip(" ,.-&")

    # Truncation artifacts: a name cut mid-phrase ends on a preposition or article
    # ("Jewish Community Fdn of the Jewish", "...Association of New"). As a quoted
    # search string those are worse than useless.
    if re.search(r"\b(of|the|and|for|in|at|to|a|an|&)$", n, re.I):
        n = re.sub(r"\s+\b(of|the|and|for|in|at|to|a|an|&)$", "", n, flags=re.I).strip()
    if re.search(r"\b(of|the|and|for|&)$", n, re.I):
        return None
    if len(n) < 8 or n.lower() in TOO_GENERIC:
        return None
    if not re.search(r"[A-Z][a-z]+", n):
        return None
    return n


def build(top):
    orgs = json.loads(WATCHLIST.read_text())
    orgs.sort(key=lambda o: -(o.get("bmf_revenue") or 0))
    seen = set()
    names = []
    for o in orgs:
        n = clean(o.get("name", ""))
        if not n or n.lower() in seen:
            continue
        seen.add(n.lower())
        names.append(n)
        if len(names) >= top:
            break
    return [names[i:i + ORGS_PER_QUERY] for i in range(0, len(names), ORGS_PER_QUERY)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=120)
    ap.add_argument("--names", action="store_true")
    args = ap.parse_args()

    batches = build(args.top)
    if args.names:
        for b in batches:
            for n in b:
                print(n)
        return 0

    total = sum(len(b) for b in batches)
    print(f"  # {total} organizations from the 990 watchlist, in {len(batches)} queries.")
    print(f"  # Regenerate: python3 tools/build_org_queries.py --top {args.top}")
    for i, batch in enumerate(batches):
        disj = " OR ".join('\\"' + n + '\\"' for n in batch)
        print(f"  - key: org{i:02d}")
        print("    outlet: Google News")
        print("    method: gnews_entity")
        print("    window_minutes: 720")
        print("    threshold: 4")
        print(f'    query: "({disj}) {GIVING}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
