"""Duplicate suppression, at three levels.

1. **Already posted, by URL.** Canonical URL hash.
2. **Already posted, by headline.** Outlets revise a story and reissue it on a new link
   with a new timestamp; URL matching treats each revision as a new story, which is how
   one WSJ piece posted three times in a row.
3. **The same story, filed by someone else.** Four outlets each writing up the same
   Aramco announcement inside an hour is the commonest way this kind of digest gets
   noisy, and per-URL dedup cannot see it because every URL differs.

The third one is tuned narrowly, and the reason is worth keeping in view. In the JI
original this window started at twelve hours and caused a real miss: hours after "US
revokes Iran oil waivers" posted, Iran attacked shipping in the Strait of Hormuz and the
US struck back — a genuinely bigger, newer story. But its headline shared iran/attack/
strait/hormuz with the earlier one, scoring 0.83 overlap, so it would have been
suppressed as already covered. A fast-moving story legitimately reuses the same nouns
across different chapters. So the window is three hours: long enough to catch outlets
piling onto one fact, too short to reach into a story's next chapter.
"""
from __future__ import annotations

import re

from . import state
from .fetch import Item, canonical

# Overlap needed to call two headlines the same story. Lower than a same-outlet
# comparison would need: independent newsrooms phrase things more differently than a
# wire pickup does against the original.
#
# DO NOT LOWER THIS. It was measured against real pairs from a live run:
#
#   0.67  "ADNOC awards $1bn+ offshore EPCI contract to McDermott"
#         "McDermott wins $1 billion ADNOC offshore contract"            same story, caught
#   0.38  "UAE-Egypt activate $500mln five-year wheat supply agreement"
#         "UAE company begins wheat supplies to Egypt in $500m deal"     same story, missed
#   0.25  "Dubai's DXB Posts 31% Fall In H1 Passenger Traffic"
#         "War cuts Dubai airport passenger traffic by nearly a third"   same story, missed
#   0.25  "US revokes Iran oil waivers"
#         "U.S. strikes Iran after attacks on vessels in Strait of Hormuz"
#                                                        DIFFERENT stories, must not merge
#
# The looser rewrites sit at the same overlap as genuinely different stories, so no
# threshold separates them — word counting cannot tell a rephrasing from a next chapter.
# Given that, 0.5 is the right side to err on: a duplicate in the channel costs a reader
# three seconds, while a suppressed escalation costs them the story. Weighting by term
# rarity was tried against these same pairs and did not separate them either.
SIMILARITY = 0.5
MIN_WORDS = 3   # below this a headline is too short for overlap to mean anything

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "at", "by",
    "from", "as", "is", "are", "was", "were", "be", "been", "will", "would", "can",
    "could", "may", "might", "new", "its", "it", "their", "this", "that", "has", "have",
    "had", "after", "amid", "over", "into", "up", "down", "out", "more", "than", "but",
    "not", "no", "says", "said", "say", "about", "which", "who", "what", "when", "how",
    "first", "next", "last", "year", "years", "week", "month", "day", "days", "plans",
    "plan", "set", "sets", "amid", "ahead", "still", "now", "off", "per", "cent",
}


def stem(word: str) -> str:
    """Crude suffix stripping, ported from the original's stemWord.

    Not linguistics — just enough that "investment"/"investments" and
    "expands"/"expanding" collide, which is what overlap counting needs.
    """
    for suffix in ("ings", "ing", "ies", "ers", "er", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def title_words(title: str) -> list[str]:
    """Significant, stemmed words in a headline."""
    raw = re.findall(r"[a-z0-9']+", (title or "").lower().replace("’", "'"))
    return sorted({stem(w) for w in raw if len(w) > 2 and w not in STOPWORDS})


def overlap(a: list[str], b: list[str]) -> float:
    """Shared fraction of the shorter headline. Zero when either is too short to judge."""
    if len(a) < MIN_WORDS or len(b) < MIN_WORDS:
        return 0.0
    shared = len(set(a) & set(b))
    return shared / min(len(a), len(b))


# ---- keys -------------------------------------------------------------------

def url_key(item: Item) -> str:
    return "u:" + state.key_hash(canonical(item.url))


def title_key(item: Item) -> str:
    """Headline key, independent of outlet.

    Deliberately not scoped per outlet: the point is to catch a story reissued on a new
    URL, and a revision often changes outlet attribution too (a wire pickup replacing an
    agency line).
    """
    return "t:" + state.key_hash(" ".join(title_words(item.title)))


def keys_for(item: Item) -> list[str]:
    return [url_key(item), title_key(item)]


def already_posted(item: Item, seen: dict) -> bool:
    return any(k in seen for k in keys_for(item))


# ---- cross-outlet -----------------------------------------------------------

def cross_outlet_match(item: Item, recent: list[dict]) -> dict | None:
    """The recent entry this item duplicates, if any.

    Only compares against a different outlet — the same outlet reissuing is level 2's
    job, and treating it here would swallow legitimate follow-ups.
    """
    words = title_words(item.title)
    for entry in reversed(recent):
        if entry.get("outlet", "").lower() == item.outlet.lower():
            continue
        if overlap(words, entry.get("words", [])) >= SIMILARITY:
            return entry
    return None

# ---------------------------------------------------------------------------
# STORY SIGNATURE — the same event told in different words
#
# Word overlap alone does not catch this. Measured against 578 real posted items, the
# 0.5 overlap rule suppressed exactly ONE duplicate, while six outlets ran the Kraft $2
# million story and four ran the Macklemore $1 million response. "Robert Kraft Says Ed
# Sheeran Asked Him to Match $2M Donation" and "Patriots owner Robert Kraft pledges $2
# million donation" share only robert/kraft/donation — 0.43 overlap, just under the bar.
#
# Lowering the bar was tried and is worse: at 0.40 it starts merging genuinely different
# stories that share nouns ("Dollar General Awards $4.1M" with "Community Foundation
# funds grants to 70 organizations").
#
# A signature is more precise. Two headlines from different outlets naming the same
# person or institution AND the same amount of money, within a day, are the same story.
# Replayed over the same 578 items it suppresses 22, and each one is a genuine duplicate
# — the Kraft cluster collapses to one, Beyoncé's $2M to the Studio Museum to one,
# Knight's $1.1B to Providence to one.
# ---------------------------------------------------------------------------

SIGNATURE_HOURS = 24

_MONEY = re.compile(r"\$\s?([\d,.]+)\s*(m|mm|million|b|bn|billion|k|thousand)?", re.I)
_MULT = {"m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9,
         "k": 1e3, "thousand": 1e3}

# Capitalised words that are not names. Headlines are often title-cased, so every word
# looks like a proper noun; without this list "Donates" and "Million" would be treated as
# identifying an actor.
_NOT_A_NAME = {
    "The", "This", "That", "With", "From", "After", "Before", "About", "Their", "Says",
    "Said", "Donate", "Donates", "Donated", "Donation", "Donations", "Gift", "Gifts",
    "Grant", "Grants", "Million", "Billion", "Thousand", "Pledge", "Pledges", "Pledged",
    "Gives", "Give", "Gave", "Giving", "Fund", "Funds", "Center", "Centre", "Foundation",
    "Jewish", "Israel", "Israeli", "Palestinian", "Following", "More", "Over", "Here",
    "Receives", "Receive", "Received", "Announces", "Announced", "Launch", "Launches",
    "Record", "Historic", "Largest", "First", "New", "Major", "Campaign", "Community",
}


def money_amounts(title: str) -> set[int]:
    """Dollar figures in a headline, normalised so $2M and '$2 million' are one value."""
    out = set()
    for num, unit in _MONEY.findall(title or ""):
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        out.add(round(value * _MULT.get((unit or "").lower(), 1)))
    return out


def proper_nouns(title: str) -> set[str]:
    """Capitalised words that plausibly name a person or institution."""
    return {w for w in re.findall(r"\b([A-Z][a-z]{3,})\b", title or "")
            if w not in _NOT_A_NAME}


def signature(title: str) -> tuple[set[int], set[str]]:
    return money_amounts(title), proper_nouns(title)


def signature_match(item, recent: list[dict]):
    """The recent entry describing the same event, if any.

    Needs BOTH a shared amount and a shared name. Either alone is far too loose: a day's
    headlines are full of unrelated $1 million gifts, and "Trump" appears in a dozen
    stories that have nothing to do with each other.
    """
    amounts, names = signature(item.title)
    if not amounts or not names:
        return None
    for entry in reversed(recent):
        if (entry.get("outlet", "") or "").lower() == (item.outlet or "").lower():
            continue
        if not (amounts & set(entry.get("money") or ())):
            continue
        if names & set(entry.get("names") or ()):
            return entry
    return None
