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


def events_digest(events, days=0, limit=25):
    """
    Today only. The section is a same-day brief — 63% of eJP's real items are
    anchored to today ("concludes today", "this evening", "kicked off last
    night") — so a forward calendar is the wrong product entirely.
    """
    import datetime as dt
    today = dt.date.today()

    def d(x):
        return dt.date.fromisoformat(x["date"])

    def ends(x):
        e = x.get("end_date")
        try:
            return dt.date.fromisoformat(e) if e else d(x)
        except Exception:
            return d(x)

    # A Zoom webinar is not a thing a funder plans a day around. Online-only
    # items are dropped unless they clear a much higher bar on their own.
    import re as _re
    ONLINE = _re.compile(r"\b(zoom|webinar|virtual|online|livestream|web ?cast)\b", _re.I)

    def online_only(e):
        blob = f"{e.get('place','')} {e.get('title','')} {e.get('kind','')} {e.get('scale','')}"
        return bool(ONLINE.search(blob))

    live = []
    for e in events:
        if e.get("news_score", 0) < 3:
            continue
        if online_only(e) and e.get("news_score", 0) < 6:
            continue
        try:
            start, end = d(e), ends(e)
        except Exception:
            continue
        if start <= today <= end:
            e["_phase"] = ("opens" if start == today else
                           "concludes" if end == today else "continues")
            live.append(e)
    live.sort(key=lambda e: (e["_phase"] != "opens", -e.get("news_score", 0)))

    stamp = today.strftime("%A, %B %-d")
    if not live:
        return f"*What We're Watching* — {stamp}\n_Nothing on the radar for today._"
    out = [f"*What We're Watching* — {stamp}"]
    VERB = {"opens": "begins today", "concludes": "concludes today", "continues": "is underway"}
    for e in live[:limit]:
        where = f" in {e['place'].split(',')[0].strip()}" if e.get("place") else ""
        note = f" {e['notable']}" if e.get("notable") else ""
        flag = "" if e.get("confidence") in ("high", "model") else "  ⟨unconfirmed⟩"
        src = e.get("url", "")
        host = _re.sub(r"^www\.", "", _re.sub(r"^https?://([^/]+).*$", r"\1", src)) if src else ""
        via = f"  <{src}|source: {host}>" if src else ""
        out.append(f"• *{e['org']}*'s {e['title'][:120]} "
                   f"{VERB[e['_phase']]}{where}.{note}{via}{flag}")
    return "\n".join(out)
