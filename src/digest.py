#!/usr/bin/env python3
"""Build and post both Daily Phil sections from the classified candidate pool."""
import json, os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm, classify
from slack_client import SlackClient

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def render(picks, section, title):
    rows = [p for p in picks if p["section"] == section]
    stamp = dt.date.today().strftime("%A, %B %-d")
    if not rows:
        return f"*{title}* — {stamp}\n_Nothing found today._", 0
    out = [f"*{title}* — {stamp}"]
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
    picks = []
    if llm.available():
        print(f"classifying {len(pool)} candidates ({llm.calls_left()} calls left)...")
        try:
            picks = classify.classify(pool, today)
        except Exception as e:
            print(f"[classifier failed: {type(e).__name__}: {str(e)[:160]}]")
    else:
        print("[no GEMINI_API_KEY]")
    json.dump(picks, open(f"{D}/picks.json", "w"), indent=1)

    www, n_w = render(picks, "watching", "What We're Watching")
    gifts, n_g = render(picks, "major_gift", "Major Gifts")
    print(f"\n=== {n_w} watching / {n_g} gifts ===\n")
    print(www); print(); print(gifts)
    if post:
        SlackClient("SLACK_WWW").post(www)
        SlackClient("SLACK_GIFTS").post(gifts)
        print("\nposted to both channels")
