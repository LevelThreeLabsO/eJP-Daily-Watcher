#!/usr/bin/env python3
"""Build and post both Daily Phil sections from the classified candidate pool."""
import json, os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import re
import llm, classify
from slack_client import SlackClient

GIFT = re.compile(r"\b(gift|gifts|donat\w+|donor|philanthrop\w+|bequest|endow\w+|pledge[sd]?|"
                  r"gave|gives|grant(?:s|ed)?|benefactor|contribut\w+)\b", re.I)
NOTGIFT = re.compile(r"\b(invest\w+|revenue|earnings|IPO|acquisition|lawsuit|election|campaign account|"
                     r"Senate|Congress|indict\w+|arrest\w+|killed|wounded|strike|missile)\b", re.I)
EVENTY = re.compile(r"\b(conference|summit|convention|gala|retreat|assembly|festival|ceremony|"
                    r"opens|opening|begins|kicks off|concludes|underway|honor\w*)\b", re.I)


def rule_fallback(pool, limit=8):
    """No model? Surface the obvious candidates and say plainly they're unscreened."""
    out = []
    for x in pool:
        t = x["title"]
        if NOTGIFT.search(t):
            continue
        if GIFT.search(t):
            out.append({"section": "major_gift", "confidence": "low",
                        "line": t, "item": x, "why": "rule-based, unscreened"})
        elif EVENTY.search(t):
            out.append({"section": "watching", "confidence": "low",
                        "line": t, "item": x, "why": "rule-based, unscreened"})
    return out[:limit]

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def render(picks, section, title, degraded=False):
    rows = [p for p in picks if p["section"] == section]
    stamp = dt.date.today().strftime("%A, %B %-d")
    if not rows:
        why = ("_The model was unavailable, so nothing was screened today._" if degraded
               else "_Nothing found today._")
        return f"*{title}* — {stamp}\n{why}", 0
    head = f"*{title}* — {stamp}"
    if degraded:
        head += "  _(unscreened — model unavailable, treat as raw leads)_"
    out = [head]
    for p in rows:
        it = p["item"]
        line = (p.get("line") or it["title"]).strip()
        src = it.get("source", "")
        link = it.get("link", "")
        tag = "" if p.get("confidence") == "high" else f"  ⟨{p.get('confidence','?')} confidence⟩"
        out.append(f"• {line}  <{link}|source: {src}>{tag}")
    return "\n".join(out), len(rows)


if __name__ == "__main__":
    post = "--post" in sys.argv
    pool = json.load(open(f"{D}/feed_pool.json"))
    today = dt.date.today().isoformat()
    picks, degraded = [], False
    if llm.available():
        print(f"classifying {len(pool)} candidates ({llm.calls_left()} calls left)...")
        try:
            picks = classify.classify(pool, today)
        except Exception as e:
            print(f"[classifier failed: {type(e).__name__}: {str(e)[:200]}]")
            picks = rule_fallback(pool)
            degraded = True
    else:
        print("[no GEMINI_API_KEY]")
        picks = rule_fallback(pool)
        degraded = True
    json.dump(picks, open(f"{D}/picks.json", "w"), indent=1)

    www, n_w = render(picks, "watching", "What We're Watching", degraded)
    gifts, n_g = render(picks, "major_gift", "Major Gifts", degraded)
    print(f"\n=== {n_w} watching / {n_g} gifts ===\n")
    print(www); print(); print(gifts)
    if post:
        SlackClient("SLACK_WWW").post(www)
        SlackClient("SLACK_GIFTS").post(gifts)
        print("\nposted to both channels")
