#!/usr/bin/env python3
"""
Major Gifts finder for the Daily Phil.

Replaces the editor's morning routine — typing "university million dollar
donation", "hospital million dollar donation", "museum million dollar donation"
into Google — with about thirty standing searches run automatically, deduped,
and filtered.

The valuable case he described is a gift that ISN'T obviously Jewish news: a
Jewish donor giving to a secular university, hospital or museum. Big Jewish
organizations email him their own announcements anyway. So the filter is built
around identifying the donor, not the recipient.
"""
import json, os, re, html, time, urllib.request, urllib.parse, hashlib
import concurrent.futures as cf

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"}

# The editor's three searches, widened. Recipient types he named, plus the ones
# that produce the same kind of story, plus explicitly Jewish recipients.
RECIPIENTS = ["university", "college", "hospital", "medical center", "museum", "school",
              "library", "theater", "orchestra", "foundation", "cancer center", "research center"]
GIFT_WORDS = ['"million gift"', '"million donation"', '"donates $"', '"gives $"',
              '"pledges $"', '"largest gift"', '"record gift"', '"endowed"']
JEWISH_RECIPIENTS = ["jewish federation", "hillel", "JCC", "holocaust museum", "yeshiva",
                     "synagogue", "jewish day school", "israel", "brandeis", "hebrew union"]


def queries():
    q = []
    for r in RECIPIENTS:
        q.append(f'"million" (gift OR donation OR donates) {r}')
    for g in GIFT_WORDS:
        q.append(f"{g} philanthropy")
    for j in JEWISH_RECIPIENTS:
        q.append(f'"million" (gift OR donation) "{j}"')
    q += ['"anonymous donor" million gift', 'philanthropist "$" million pledge',
          '"naming gift" million', 'bequest million estate charity']
    return q


def google_news(q, days=2):
    url = ("https://news.google.com/rss/search?"
           + urllib.parse.urlencode({"q": f"{q} when:{days}d", "hl": "en-US",
                                     "gl": "US", "ceid": "US:en"}))
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as f:
            body = f.read().decode("utf8", "replace")
    except Exception:
        return []
    out = []
    for it in re.findall(r"<item>(.*?)</item>", body, re.S):
        def grab(tag):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", it, re.S)
            return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
        title = grab("title")
        if not title:
            continue
        out.append({"title": title, "link": grab("link"), "source": grab("source"),
                    "published": grab("pubDate"), "query": q})
    return out


AMT = re.compile(r"\$\s?([\d,.]+)\s*(billion|million|m\b|b\b)?", re.I)


def amount(text):
    """Largest dollar figure mentioned, in dollars."""
    best = 0
    for m in AMT.finditer(text):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        u = (m.group(2) or "").lower()
        if u.startswith("b"):
            v *= 1e9
        elif u.startswith("m"):
            v *= 1e6
        elif v < 1000:          # "$5" in a headline about millions
            continue
        best = max(best, v)
    # "$100M gift" written as "100 million" without the sign
    for m in re.finditer(r"\b([\d,.]+)\s*(million|billion)\b", text, re.I):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        v *= 1e9 if m.group(2).lower().startswith("b") else 1e6
        best = max(best, v)
    return best


def norm_title(t):
    t = re.sub(r"\s*[-–—|]\s*[^-–—|]{2,40}$", "", t)      # trailing " - Outlet"
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


# A dollar figure is not a gift. These separate philanthropy from business news.
GIFT_LANG = re.compile(
    r"\b(gift|gifts|donat\w+|donor|philanthrop\w+|bequest|bequeath\w*|endow\w+|"
    r"pledge[sd]?|gave|gives|giving|grant(?:s|ed|ing)?|benefactor|naming|"
    r"fundrais\w+|campaign|charitable|contribution)\b", re.I)
NOT_GIFT = re.compile(
    r"\b(invest\w+|AUM|assets under management|venture|IPO|acquisition|acquire[sd]?|merger|"
    r"revenue|profit|earnings|loan|debt|bond sale|construction|expansion project|"
    r"breaks? ground|budget|lawsuit|settlement|fine[sd]?|penalt\w+|salary|contract award|"
    r"stake|shares|valuation|raises? \$[\d.,]+ ?(million|billion) (?:in )?(?:seed|series|round|funding))\b",
    re.I)
# Non-USD amounts masquerading as dollars
FOREIGN = re.compile(r"[₦₹£€¥₪]|\b(naira|rupee|shekel|pound|euro|yen|peso|rand|dirham|won|yuan|ringgit|baht|zloty|krona|krone|AFN|CAD|AUD|KRW|CNY)\b", re.I)
# Institutional campaign totals aren't a gift; political money isn't philanthropy.
CAMPAIGN_TOTAL = re.compile(
    r"\b(surpass\w*|exceed\w*|reach\w*|tops?|hits?|closes?|completes?|wraps?)\b.{0,30}"
    r"\b(goal|campaign|target)\b|\bfundraising (year|total|record|goal)\b", re.I)
POLITICAL = re.compile(
    r"\b(senate|congress\w*|campaign account|PAC\b|super PAC|election|candidate|"
    r"Trump|Biden|Harris|governor|re-?election|ballot)\b", re.I)

JEWISH_HINT = re.compile(
    r"\b(jewish|jew|israel|israeli|hebrew|synagogue|temple|rabbi|holocaust|shoah|yeshiva|"
    r"hillel|chabad|zionist|kosher|torah|judaism|federation|JCC|brandeis|yad vashem|"
    r"antisemit|anti-semit|birthright|maccabi|hadassah|ADL)\b", re.I)
JEWISH_OUTLET = re.compile(
    r"(jewish|jta|forward|haaretz|times of israel|jns|ynet|jerusalem post|tablet|algemeiner|"
    r"jewish insider|ejewish)", re.I)


def load_people():
    p = f"{D}/ejp_people.json"
    return json.load(open(p)) if os.path.exists(p) else {}


def collect(days=2, workers=8):
    qs = queries()
    items = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(lambda q: google_news(q, days), qs):
            items.extend(res)
    # dedupe on normalized headline
    seen, uniq = {}, []
    for it in items:
        k = norm_title(it["title"])
        if k in seen:
            seen[k]["queries"] = seen[k].get("queries", 1) + 1
            continue
        it["queries"] = 1
        seen[k] = it
        uniq.append(it)
    return uniq


def triage(items, min_amount=1_000_000):
    """Cheap deterministic pass. Everything surviving goes to the model."""
    people = load_people()
    plower = {n.lower(): c for n, c in people.items()}
    out = []
    for it in items:
        t = it["title"]
        # Must read like a gift, must not read like business news, must be dollars.
        if not GIFT_LANG.search(t):
            continue
        if NOT_GIFT.search(t):
            continue
        if FOREIGN.search(t):
            continue
        if POLITICAL.search(t):
            continue
        if CAMPAIGN_TOTAL.search(t):
            continue
        amt = amount(t)
        if amt and amt < min_amount:
            continue
        if not amt and not JEWISH_HINT.search(t):
            continue          # no amount and no Jewish angle = nothing to go on
        hits = [n for n in plower if n in t.lower()]
        it["amount"] = amt
        it["known_person"] = hits[:3]
        it["jewish_hint"] = bool(JEWISH_HINT.search(t))
        it["jewish_outlet"] = bool(JEWISH_OUTLET.search(it.get("source", "")))
        # Route: obviously-Jewish recipient is likely already in his inbox; the
        # interesting bucket is a big secular gift that may have a Jewish donor.
        if it["known_person"]:
            it["bucket"] = "known donor"
        elif it["jewish_hint"] or it["jewish_outlet"]:
            it["bucket"] = "explicitly Jewish"
        elif amt >= 5_000_000:
            it["bucket"] = "large secular gift — check donor"
        else:
            it["bucket"] = "secular gift"
        out.append(it)
    order = {"known donor": 0, "explicitly Jewish": 1, "large secular gift — check donor": 2,
             "secular gift": 3}
    out.sort(key=lambda x: (order[x["bucket"]], -(x["amount"] or 0)))
    return out


def enrich(items):
    """Have the model identify donors and Jewish angle. No-ops without a key."""
    try:
        import llm
    except ImportError:
        import sys as _s, os as _o
        _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
        import llm
    if not llm.available():
        print("[no GEMINI_API_KEY — deterministic buckets only]")
        return items
    try:
        verdicts = llm.judge_gifts(items)
    except Exception as ex:
        # Quota exhausted or the API is down: fall back to the rule-based buckets
        # rather than failing the run. The digest is worse, not absent.
        print(f"[model unavailable: {type(ex).__name__}: {str(ex)[:700]}]")
        print("[falling back to deterministic buckets]")
        return items
    by_id = {v["id"]: v for v in verdicts}
    keep = []
    for i, it in enumerate(items):
        v = by_id.get(i)
        if not v:
            keep.append(it); continue
        if not v.get("is_gift"):
            continue                     # model says it isn't a gift at all
        it["donor"] = v.get("donor", "")
        it["recipient"] = v.get("recipient", "")
        it["jewish_angle"] = v.get("jewish_angle", "unclear")
        it["confidence"] = v.get("confidence", "low")
        it["note"] = v.get("note", "")
        if it.get("known_person"):
            it["bucket"] = "known donor"
        elif v["jewish_angle"] == "donor":
            it["bucket"] = "Jewish donor, secular recipient"
        elif v["jewish_angle"] in ("recipient", "both"):
            it["bucket"] = "explicitly Jewish"
        elif v["jewish_angle"] == "unclear":
            it["bucket"] = "large secular gift — check donor"
        else:
            it["bucket"] = "secular gift"
        keep.append(it)
    order = {"known donor": 0, "Jewish donor, secular recipient": 1, "explicitly Jewish": 2,
             "large secular gift — check donor": 3, "secular gift": 4}
    keep.sort(key=lambda x: (order.get(x["bucket"], 9), -(x.get("amount") or 0)))
    return keep


if __name__ == "__main__":
    import sys, os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    days, post, preview = 2, "--post" in sys.argv, "--preview" in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--days="):
            days = int(a.split("=")[1])
    raw = collect(days=days)
    tri = triage(raw)
    tri = enrich(tri)
    json.dump(tri, open(f"{D}/gifts_latest.json", "w"), indent=1)
    if post or preview:
        from slack_client import SlackClient, gifts_digest
        msg = gifts_digest(tri)
        if preview:
            print("=" * 78 + "\nDRY RUN — this is what would post to the gifts channel\n" + "=" * 78)
            print(msg)
            print("=" * 78)
        else:
            sc = SlackClient("SLACK_GIFTS")
            sc.post(msg)
            print(f"posted {len(tri)} gifts to Slack (enabled={sc.enabled})")
    print(f"{len(queries())} standing searches -> {len(raw)} unique stories -> {len(tri)} above threshold\n")
    import collections
    print(dict(collections.Counter(x["bucket"] for x in tri)))
    for b in ["known donor", "explicitly Jewish", "large secular gift — check donor", "secular gift"]:
        rows = [x for x in tri if x["bucket"] == b]
        if not rows:
            continue
        print(f"\n{'='*92}\n{b.upper()}  ({len(rows)})\n{'='*92}")
        for x in rows[:14]:
            amt = f"${x['amount']/1e6:,.1f}M" if x["amount"] else "—"
            print(f"  {amt:>10}  {x['title'][:78]}")
            print(f"              {x['source'][:34]:<36} {x['published'][:16]}"
                  + (f"  MATCH: {', '.join(x['known_person'])}" if x["known_person"] else ""))
