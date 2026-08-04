#!/usr/bin/env python3
"""Slack delivery via incoming webhooks — same pattern as the other JI watchers."""
import json, os, urllib.request

class SlackClient:
    def __init__(self, env="SLACK_WEBHOOK_URL"):
        self.url = os.environ.get(env, "").strip()
        self.enabled = bool(self.url)

    def post(self, text, blocks=None):
        if not self.enabled:
            print(f"[slack disabled] would post {len(text)} chars"); return False
        payload = {"text": text, "unfurl_links": False, "unfurl_media": False}
        if blocks:
            payload["blocks"] = blocks
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as f:
            return f.status == 200


def gifts_digest(items, limit=14):
    if not items:
        return "*Major Gifts* — nothing above threshold in the last 24 hours."
    order = ["known donor", "explicitly Jewish", "large secular gift — check donor", "secular gift"]
    LABEL = {"known donor": "Donor eJP already covers",
             "explicitly Jewish": "Explicitly Jewish recipient",
             "large secular gift — check donor": "Large secular gift — worth checking the donor",
             "secular gift": "Other gifts"}
    judged = any("jewish_angle" in x for x in items)
    head = f"*Major Gifts* — {len(items)} found in the last 24 hours"
    if not judged:
        head += "  _(rule-based only — donor screening unavailable today)_"
    out = [head]
    shown = 0
    for b in order:
        rows = [x for x in items if x["bucket"] == b]
        if not rows:
            continue
        out.append(f"\n*{LABEL[b]}* ({len(rows)})")
        for x in rows:
            if shown >= limit:
                break
            amt = f"${x['amount']/1e6:,.1f}M" if x.get("amount") else "amount n/a"
            who = f" · matches *{', '.join(x['known_person'])}*" if x.get("known_person") else ""
            out.append(f"• <{x['link']}|{x['title'][:120]}>\n   _{amt} · {x['source']}_{who}")
            shown += 1
    if len(items) > shown:
        out.append(f"\n_+{len(items)-shown} more in the full run._")
    return "\n".join(out)


def events_digest(events, days=21, limit=25):
    import datetime as dt
    today = dt.date.today()
    horizon = today + dt.timedelta(days=days)
    up = [e for e in events
          if e.get("news_score", 0) >= 3
          and today <= dt.date.fromisoformat(e["date"]) <= horizon]
    up.sort(key=lambda e: (e["date"], -e["news_score"]))
    if not up:
        return f"*What We're Watching* — no notable events in the next {days} days."
    out = [f"*What We're Watching* — {len(up)} events in the next {days} days"]
    cur = None
    for e in up[:limit]:
        d = dt.date.fromisoformat(e["date"])
        wk = d.strftime("Week of %b %d")
        if wk != cur:
            out.append(f"\n*{wk}*"); cur = wk
        mark = "" if e["confidence"] == "high" else " ⟨unconfirmed⟩"
        place = f" · {e['place']}" if e.get("place") else ""
        out.append(f"• {d.strftime('%a %b %-d')} — <{e['url']}|{e['title'][:110]}>\n"
                   f"   _{e['org']}{place}_{mark}")
    return "\n".join(out)
