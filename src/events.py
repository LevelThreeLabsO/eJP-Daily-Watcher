#!/usr/bin/env python3
"""
Events aggregator for "What We're Watching".

The editor's complaint: assembling this section means Googling "<date> Jewish
event" and hand-checking organizational calendars, and he wants the funky ones
(a Yiddish appreciation gala) as much as the obvious ones (AIPAC).

So breadth beats depth. Three extractors run against every calendar page, in
order of how trustworthy they are:
  1. schema.org Event markup  — exact, machine-published, no guessing
  2. iCalendar (.ics) feeds   — exact
  3. date-anchored HTML       — a heuristic pass for the ~70% of sites with neither

Anything the third extractor produces is marked lower-confidence, because a page
of text with dates in it is genuinely ambiguous — that is the layer a model
should read rather than a regex.
"""
import json, os, re, html, urllib.request, urllib.parse, datetime as dt
import concurrent.futures as cf

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
TODAY = dt.date.today()
HORIZON = TODAY + dt.timedelta(days=120)


def get(url, timeout=25, limit=1_200_000):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as f:
        raw = f.read(limit)
        enc = f.headers.get_content_charset() or "utf8"
    return raw.decode(enc, "replace")


def parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s[:len(dt.datetime.now().strftime(fmt))
                                          if "%z" not in fmt else len(s)], fmt).date()
        except Exception:
            pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def in_window(d):
    return d is not None and TODAY <= d <= HORIZON


# ---------- 1. schema.org ----------
def from_jsonld(body, base):
    out = []
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                         body, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x); continue
            if not isinstance(x, dict):
                continue
            t = x.get("@type")
            ts = [t] if isinstance(t, str) else (t or [])
            if any("Event" in str(k) for k in ts):
                d = parse_date(x.get("startDate"))
                name = x.get("name")
                if isinstance(name, dict):
                    name = name.get("@value")
                if d and name:
                    loc = x.get("location")
                    place = ""
                    if isinstance(loc, dict):
                        place = loc.get("name") or ""
                        addr = loc.get("address")
                        if isinstance(addr, dict):
                            place = ", ".join(filter(None, [place, addr.get("addressLocality"),
                                                            addr.get("addressRegion")]))
                        elif isinstance(addr, str):
                            place = ", ".join(filter(None, [place, addr]))
                    elif isinstance(loc, str):
                        place = loc
                    url = x.get("url") or ""
                    out.append({"date": d.isoformat(),
                                "title": re.sub(r"\s+", " ", html.unescape(str(name))).strip(),
                                "place": re.sub(r"\s+", " ", str(place)).strip()[:80],
                                "url": urllib.parse.urljoin(base, url) if url else base,
                                "how": "schema.org", "confidence": "high"})
            for v in x.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return out


# ---------- 2. iCalendar ----------
def find_ics(body, base):
    urls = set()
    for m in re.finditer(r'href=["\']([^"\']+\.ics[^"\']*)["\']', body, re.I):
        urls.add(urllib.parse.urljoin(base, html.unescape(m.group(1))))
    for m in re.finditer(r'href=["\']([^"\']*(?:ical|icalendar|calendar/export)[^"\']*)["\']', body, re.I):
        urls.add(urllib.parse.urljoin(base, html.unescape(m.group(1))))
    return list(urls)[:3]


def from_ics(text, base):
    out, cur = [], {}
    for line in text.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            d = parse_date(cur.get("DTSTART", ""))
            if d and cur.get("SUMMARY"):
                out.append({"date": d.isoformat(),
                            "title": cur["SUMMARY"].replace("\\,", ",").replace("\\n", " ")[:180],
                            "place": cur.get("LOCATION", "").replace("\\,", ",")[:80],
                            "url": cur.get("URL", base), "how": "ical", "confidence": "high"})
            cur = {}
        else:
            m = re.match(r"([A-Z\-]+)(?:;[^:]*)?:(.*)", line)
            if m:
                cur[m.group(1)] = m.group(2)
    return out


# ---------- 3. date-anchored HTML ----------
MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
          "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
DATE_RX = re.compile(rf"\b({MONTHS})\.?\s+(\d{{1,2}})(?:\s*[-–—]\s*\d{{1,2}})?(?:,?\s*(20\d{{2}}))?\b", re.I)
MON_NUM = {m[:3]: i + 1 for i, m in enumerate(
    ["january","february","march","april","may","june","july","august",
     "september","october","november","december"])}


def from_html(body, base):
    s = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", body, flags=re.S | re.I)
    s = re.sub(r"</?(p|div|li|tr|h[1-6]|section|article|br|td|a)\b[^>]*>", "\n", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    lines = [re.sub(r"\s+", " ", x).strip() for x in s.split("\n")]
    lines = [x for x in lines if x]
    out = []
    for i, line in enumerate(lines):
        m = DATE_RX.search(line)
        if not m:
            continue
        mon = MON_NUM.get(m.group(1)[:3].lower())
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else TODAY.year
        try:
            d = dt.date(year, mon, day)
        except ValueError:
            continue
        if not m.group(3) and d < TODAY - dt.timedelta(days=30):
            d = dt.date(year + 1, mon, day)      # undated "March 4" means next March
        if not in_window(d):
            continue
        # Title: the longest nearby line that isn't the date itself
        window = [lines[j] for j in range(max(0, i - 2), min(len(lines), i + 3)) if j != i]
        cands = [w for w in window if 12 < len(w) < 140 and not DATE_RX.search(w)
                 and len(w.split()) >= 3]
        if not cands:
            rest = DATE_RX.sub("", line).strip(" -–—,|")
            if len(rest) > 12:
                cands = [rest]
        if not cands:
            continue
        out.append({"date": d.isoformat(), "title": max(cands, key=len)[:180],
                    "place": "", "url": base, "how": "html-heuristic", "confidence": "low"})
    return out


# ---------- newsworthiness ----------
# A federation calendar is mostly local programming — baby yoga, grief support,
# a walking club. The Daily Phil wants the convenings a national readership would
# care about, plus the odd genuinely odd one. These two lists sort that out before
# a model ever sees the list.
BIG = re.compile(
    r"\b(conference|summit|convention|general assembly|\bGA\b|gala|benefit|awards?|"
    r"symposium|forum|plenary|congress|assembly|convening|colloquium|keynote|"
    r"annual (?:meeting|dinner|luncheon)|mission to|delegation|commemorat\w+|"
    r"festival|premiere|opening|installation|inauguration|dedication|groundbreaking|"
    r"town hall|briefing|policy)\b", re.I)
LOCAL_PROGRAM = re.compile(
    r"\b(yoga|playgroup|play date|playdate|story ?time|tot |toddler|baby|jbaby|PJ Library|"
    r"support group|grief|drop-?in|walking club|meetup|meet-?up|book club|knitting|"
    r"mah ?jongg|bingo|game (?:night|watch)|coffee|schmooze|happy hour|"
    r"class|workshop|lesson|tutorial|swim|soccer|basketball|camp day|day camp|"
    r"food (?:bank|pantry)|sorting|packing|clothing swap|blood drive|"
    r"minyan|shabbat (?:services|playgroup|on the|under)|services|torah study|"
    r"religious school|hebrew school|carpool|senior lunch|exercise|fitness|"
    r"art (?:class|studio)|music together|abrakadoodle|silly)\b", re.I)
NOTABLE = re.compile(r"\b(with|featuring|presented by|keynote|in conversation with)\b\s+[A-Z]", re.I)

def newsworthy(e):
    t = e["title"]
    if LOCAL_PROGRAM.search(t):
        return 0
    score = 0
    if BIG.search(t): score += 3
    if NOTABLE.search(t): score += 1
    if len(t.split()) >= 4: score += 1
    # national/umbrella organizations matter more than a single community
    if e.get("sector") in ("umbrella", "advocacy", "funder", "israel", "education", "campus",
                           "denomination", "professional", "national", "security"):
        score += 2
    if e.get("sector") in ("museum", "archive", "theater", "yiddish", "music", "film",
                           "books", "ideas", "culture"):
        score += 2          # the offbeat half the editor asked for
    if re.search(r"\b(20\d{2}|annual|national|international|global)\b", t, re.I): score += 1
    return score


# ---------- model extraction (primary path) ----------
LLM_ERRORS = []
CACHE_PATH = os.path.join(D, "event_cache.json")
_cache = json.load(open(CACHE_PATH)) if os.path.exists(CACHE_PATH) else {}
_cache_hits = [0]
# Free-tier quota is the binding constraint, so cap model calls per run. Sources
# whose pages changed are always read; the rest rotate across days via the cache.
LLM_BUDGET = [int(os.environ.get("GEMINI_MAX_CALLS", "45"))]


def save_cache():
    json.dump(_cache, open(CACHE_PATH, "w"), indent=1)


def page_text(body):
    s = re.sub(r"<(script|style|noscript|svg)\b.*?</\1>", " ", body, flags=re.S | re.I)
    s = re.sub(r"</?(p|div|li|tr|h[1-6]|section|article|br|td)\b[^>]*>", "\n", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    lines = [re.sub(r"\s+", " ", x).strip() for x in s.split("\n")]
    return "\n".join(x for x in lines if len(x) > 2)


def from_llm(body, base, org, sector):
    import llm, hashlib
    if not llm.available():
        return []
    text = page_text(body)
    sig = hashlib.sha1(text.encode("utf8", "replace")).hexdigest()[:20]
    hit = _cache.get(base)
    if hit and hit.get("sig") == sig:
        _cache_hits[0] += 1
        return hit.get("events", [])          # page hasn't changed since last read
    if LLM_BUDGET[0] <= 0:
        return hit.get("events", []) if hit else []
    LLM_BUDGET[0] -= 1
    try:
        evs = llm.extract_events(org, base, text, TODAY.isoformat())
    except Exception as ex:
        LLM_ERRORS.append(f"{org}: {type(ex).__name__}: {str(ex)[:120]}")
        return []
    out = []
    for x in evs:
        d = parse_date(x.get("date"))
        if not in_window(d) or not x.get("title"):
            continue
        out.append({"date": d.isoformat(), "title": x["title"][:180],
                    "place": (x.get("place") or "")[:80], "url": base,
                    "how": "gemini", "confidence": "model",
                    "scale": x.get("scale", ""), "kind": x.get("kind", ""),
                    "notable": x.get("notable", ""),
                    "llm_newsworthy": bool(x.get("newsworthy")),
                    "why": x.get("why", "")})
    _cache[base] = {"sig": sig, "events": out, "org": org}
    return out


# ---------- driver ----------
def scrape(target):
    url = target["url"]
    ev = []
    try:
        body = get(url)
    except Exception as e:
        return {**target, "events": [], "error": type(e).__name__}
    # Exact sources first — schema.org and iCal are machine-published and never wrong.
    ev += from_jsonld(body, url)
    for ics in find_ics(body, url):
        try:
            ev += from_ics(get(ics, timeout=25), url)
        except Exception:
            pass
    # Then the model, which is the only thing that works on hand-built conference
    # pages. Falls back to the regex pass only if there is no key.
    import llm as _llm
    if not _llm.available():
        if not ev:
            ev += from_html(body, url)          # no key: heuristic is all we have
    # keep only future-dated, dedupe by (date, title)
    seen, keep = set(), []
    for e in ev:
        d = parse_date(e["date"])
        if not in_window(d):
            continue
        k = (e["date"], re.sub(r"[^a-z0-9]", "", e["title"].lower())[:60])
        if k in seen:
            continue
        seen.add(k)
        e["title"] = re.sub(r"\s+", " ", html.unescape(e["title"])).strip()
        e["org"] = target["org"]
        e["sector"] = target.get("sector", "")
        e["news_score"] = newsworthy(e)
        if e.get("how") == "gemini":
            # the model read the whole page; trust its call over the keyword score
            e["news_score"] = 6 if e.get("llm_newsworthy") else 0
        keep.append(e)
    return {**target, "events": keep, "text": page_text(body)}


def targets():
    """Curated convening + culture calendars (see sources_events.py)."""
    src = json.load(open(f"{D}/event_sources.json"))
    out = []
    for o in src:
        if not o.get("ok") or not o.get("url"):
            continue
        out.append({"org": o["org"], "sector": o["kind"], "url": o["url"]})
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    post, preview = "--post" in sys.argv, "--preview" in sys.argv
    tg = targets()
    print(f"scanning {len(tg)} organizational calendars...")
    import llm as _llm
    import hashlib
    allev, errs, pages = [], 0, []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(scrape, tg):
            if r.get("error"):
                errs += 1
                continue
            allev.extend(r["events"])
            if r.get("text"):
                pages.append(r)

    # --- model pass, batched and budgeted -------------------------------------
    if _llm.available():
        # Only pages that (a) changed since last run and (b) actually contain a
        # date are worth spending a call on.
        DATED = re.compile(rf"\b({MONTHS})\.?\s+\d{{1,2}}\b|\b20\d{{2}}-\d{{2}}-\d{{2}}\b", re.I)
        todo = []
        for p in pages:
            sig = hashlib.sha1(p["text"].encode("utf8", "replace")).hexdigest()[:20]
            hit = _cache.get(p["url"])
            if hit and hit.get("sig") == sig:
                _cache_hits[0] += 1
                allev.extend(hit.get("events", []))
                continue
            if not DATED.search(p["text"][:20000]):
                continue
            p["sig"] = sig
            todo.append(p)

        per_call = int(os.environ.get("GEMINI_PAGES_PER_CALL", "10"))
        budget = min(_llm.calls_left(), LLM_BUDGET[0])
        batches = [todo[i:i + per_call] for i in range(0, len(todo), per_call)][:budget]
        print(f"model pass: {len(todo)} changed pages -> {len(batches)} calls "
              f"(budget {budget}, {_llm.calls_left()} left today)")
        for bi, batch in enumerate(batches):
            try:
                evs = _llm.extract_events_batch(batch, TODAY.isoformat())
            except Exception as ex_:
                LLM_ERRORS.append(f"batch {bi}: {type(ex_).__name__}: {str(ex_)[:110]}")
                break                                  # budget or quota gone; stop cleanly
            per_page = {i: [] for i in range(len(batch))}
            for x in evs:
                si = x.get("source")
                if not isinstance(si, int) or si not in per_page:
                    continue
                d = parse_date(x.get("date"))
                if not in_window(d) or not x.get("title"):
                    continue
                per_page[si].append({
                    "date": d.isoformat(), "title": str(x["title"])[:180],
                    "place": (x.get("place") or "")[:80], "url": batch[si]["url"],
                    "how": "gemini", "confidence": "model",
                    "scale": x.get("scale", ""), "kind": x.get("kind", ""),
                    "notable": x.get("notable", ""),
                    "llm_newsworthy": bool(x.get("newsworthy")), "why": x.get("why", ""),
                    "org": batch[si]["org"], "sector": batch[si].get("sector", ""),
                    "news_score": 6 if x.get("newsworthy") else 0})
            for i, p in enumerate(batch):
                _cache[p["url"]] = {"sig": p["sig"], "events": per_page[i], "org": p["org"]}
                allev.extend(per_page[i])
    # global dedupe
    seen, uniq = set(), []
    for e in sorted(allev, key=lambda x: (x["date"], x["org"])):
        k = (e["date"], re.sub(r"[^a-z0-9]", "", e["title"].lower())[:50])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    json.dump(uniq, open(f"{D}/events_latest.json", "w"), indent=1)
    import collections
    save_cache()
    print(f"fetch errors: {errs}   cache hits: {_cache_hits[0]}   "
          f"model budget left: {LLM_BUDGET[0]}")
    import llm as _llm
    if _llm.available():
        n_model = sum(1 for e in uniq if e.get("how") == "gemini")
        print(f"model-extracted events: {n_model}   model errors: {len(LLM_ERRORS)}")
        for m in LLM_ERRORS[:5]:
            print(f"    ! {m}")
        if n_model == 0 and LLM_ERRORS:
            quota = any("RESOURCE_EXHAUSTED" in m or "429" in m or "budget" in m
                        for m in LLM_ERRORS)
            if quota:
                # Out of daily allowance: ship what the free structured sources and
                # the cache gave us and say so, rather than failing the run.
                print("::warning::Gemini daily quota exhausted — digest built from "
                      "schema.org/iCal and cache only.")
            else:
                print("::error::Gemini errored on every page for a non-quota reason.")
                raise SystemExit(1)
    print(f"events found: {len(uniq)}  ({dict(collections.Counter(e['how'] for e in uniq))})")
    hi = [e for e in uniq if e["confidence"] == "high"]
    print(f"high-confidence (machine-published): {len(hi)}\n")
    if post or preview:
        from slack_client import SlackClient, events_digest
        msg = events_digest(uniq, days=30, limit=25)
        if preview:
            print("=" * 78 + "\nDRY RUN — this is what would post to the events channel\n" + "=" * 78)
            print(msg)
            print("=" * 78)
        else:
            sc = SlackClient("SLACK_WWW")
            sc.post(msg)
            print(f"posted to Slack (enabled={sc.enabled})")
    nxt = [e for e in uniq if parse_date(e["date"]) <= TODAY + dt.timedelta(days=60)]
    keep = [e for e in nxt if e["news_score"] >= 3]
    dropped = len(nxt) - len(keep)
    print(f"{'='*96}\nNEXT 60 DAYS — {len(keep)} newsworthy of {len(nxt)} total "
          f"({dropped} local-programming items filtered out)\n{'='*96}")
    for e in sorted(keep, key=lambda x: (x["date"], -x["news_score"]))[:40]:
        d = parse_date(e["date"])
        flag = " " if e["confidence"] == "high" else "?"
        print(f" {flag} {d.strftime('%a %b %d')}  [{e['news_score']}]  {e['title'][:58]:<60} {e['org'][:24]}")
