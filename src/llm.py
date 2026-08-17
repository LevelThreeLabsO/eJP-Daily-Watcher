#!/usr/bin/env python3
"""
Gemini judgment layer — the same free-tier setup as the govt-feeds watcher.

Two jobs the deterministic code provably cannot do:

  extract_events()  Reading a calendar page and returning real events. The regex
                    fallback returned "Confirm that you are not a bot" and a list
                    of Connecticut towns as event titles. Only ~11% of curated
                    convening sites publish schema.org markup, so this is the
                    main path for events, not a garnish.

  judge_gifts()     Identifying the donor behind a gift and deciding whether
                    there is a Jewish or Israel angle. The editor's valuable case
                    is a Jewish donor giving to a secular hospital or university —
                    which requires knowing who the person is.

Both degrade safely: with no GEMINI_API_KEY set, callers fall back to the
deterministic path and the digest says so.
"""
import json, os, re, time, threading

# Free-tier daily allowances, measured against this key:
#   gemini-3.5-flash  -> 20 requests/day
#   gemini-2.0-flash  -> 0 (no free tier at all)
# The batched design needs about 6 calls/day, so 20 is comfortable.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
# 503 "high demand" on one model is common; try the next rather than give up.
FALLBACKS = [m for m in os.environ.get(
    "GEMINI_FALLBACKS", "gemini-3.5-flash-lite,gemini-2.5-flash,gemini-3-flash-preview").split(",") if m]

_client = None

# The free tier is a low requests-per-minute allowance, and firing 76 calendar
# pages at it concurrently exhausts it instantly. One global valve, so every
# caller — threaded or not — shares the same budget.
_MIN_INTERVAL = float(os.environ.get("GEMINI_MIN_INTERVAL", "4.2"))

# The free tier is a DAILY cap (20 for the newest models). Rather than discover
# that with a 429 halfway through a run, the client refuses to exceed its own
# budget and callers fall back to cached or deterministic results.
DAILY_BUDGET = int(os.environ.get("GEMINI_DAILY_BUDGET", "18"))
_LEDGER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "quota.json")


class BudgetExhausted(RuntimeError):
    pass


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _ledger():
    try:
        d = json.load(open(_LEDGER))
    except Exception:
        d = {}
    if d.get("date") != _today():
        d = {"date": _today(), "used": 0}
    return d


def calls_used():
    return _ledger().get("used", 0)


def calls_left():
    return max(0, DAILY_BUDGET - calls_used())


def _spend():
    d = _ledger()
    d["used"] = d.get("used", 0) + 1
    try:
        os.makedirs(os.path.dirname(_LEDGER), exist_ok=True)
        json.dump(d, open(_LEDGER, "w"))
    except Exception:
        pass
_lock = threading.Lock()
_last = [0.0]


def _throttle():
    with _lock:
        wait = _MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def available():
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def _get_client():
    """Lazy so runs without a key need no dependency installed."""
    global _client
    if _client is None:
        from google import genai
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set")
        _client = genai.Client(api_key=key)
    return _client


def _call(prompt, schema=None, temperature=0.1, retries=6, max_tokens=8192):
    from google.genai import types
    cfg = types.GenerateContentConfig(temperature=temperature,
                                      max_output_tokens=max_tokens)
    if schema:
        cfg.response_mime_type = "application/json"
        cfg.response_schema = schema
    if calls_left() <= 0:
        raise BudgetExhausted(f"daily Gemini budget of {DAILY_BUDGET} already used")
    last = None
    models = [MODEL] + FALLBACKS
    for a in range(retries):
        model = models[min(a, len(models) - 1)]
        try:
            _throttle()
            r = _get_client().models.generate_content(
                model=model, contents=prompt, config=cfg)
            _spend()          # only a completed call counts against the daily cap
            txt = (r.text or "").strip()
            if not txt:
                return {} if schema else ""
            return json.loads(txt) if schema else txt
        except Exception as e:
            last = e
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                time.sleep(20 * (a + 1))     # quota needs real time, not a blink
            elif "503" in msg or "UNAVAILABLE" in msg or "high demand" in msg:
                print(f"  {model} unavailable, trying {models[min(a+1,len(models)-1)]}", flush=True)
                time.sleep(8 * (a + 1))
            else:
                time.sleep(2 * (a + 1))
    raise last


# ---------------- events ----------------
EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD, start date"},
                    "end_date": {"type": "string"},
                    "place": {"type": "string"},
                    "scale": {"type": "string", "enum": ["national", "regional", "local", "online"]},
                    "kind": {"type": "string",
                             "enum": ["conference", "gala", "festival", "exhibition", "webinar",
                                      "mission", "award", "lecture", "other"]},
                    "notable": {"type": "string",
                                "description": "Named speakers or honorees, empty if none"},
                    "newsworthy": {"type": "boolean"},
                    "why": {"type": "string", "description": "One clause. Empty if not newsworthy."},
                },
                "required": ["title", "date", "scale", "kind", "newsworthy"],
            },
        }
    },
    "required": ["events"],
}

EVENT_PROMPT = """You are reading one organization's calendar page for the editor of Your Daily Phil, \
a daily newsletter for the Jewish philanthropy and nonprofit sector. He compiles a section called \
"What We're Watching": upcoming events his national readership of funders, federation executives and \
nonprofit leaders would want on their radar.

He wants two kinds of item:
  - the significant institutional convenings (conferences, general assemblies, biennials, summits, \
major galas, missions, award ceremonies)
  - genuinely distinctive or offbeat cultural events (a Yiddish appreciation gala, a klezmer retreat, \
a notable museum opening or film premiere)

He does NOT want routine local programming: chapter book clubs, mahjong afternoons, baby playgroups, \
support groups, walking tours, trivia nights, fitness classes, congregational services, admissions \
webinars, or staff training sessions — even when a national organization hosts them.

Today is {today}. Only return events dated today or later. Resolve relative or partial dates \
("March 4", "next Tuesday") into YYYY-MM-DD using that. If a date is genuinely ambiguous, omit the \
event rather than guessing.

Ignore page furniture entirely: navigation, cookie banners, bot-check text, address lists, donation \
appeals, newsletter signups.

Organization: {org}
Page: {url}

--- PAGE TEXT ---
{text}
--- END ---

Return every real upcoming event you find, with newsworthy=true only for those meeting the bar above."""


def extract_events(org, url, page_text, today, max_chars=24000):
    out = _call(EVENT_PROMPT.format(org=org, url=url, today=today,
                                    text=page_text[:max_chars]),
                schema=EVENT_SCHEMA)
    return out.get("events", [])


BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "integer", "description": "index of the PAGE it came from"},
                    "title": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD start date"},
                    "place": {"type": "string"},
                    "scale": {"type": "string", "enum": ["national", "regional", "local", "online"]},
                    "kind": {"type": "string",
                             "enum": ["conference", "gala", "festival", "exhibition", "webinar",
                                      "mission", "award", "lecture", "other"]},
                    "notable": {"type": "string"},
                    "newsworthy": {"type": "boolean"},
                    "why": {"type": "string"},
                },
                "required": ["source", "title", "date", "scale", "kind", "newsworthy"],
            },
        }
    },
    "required": ["events"],
}

BATCH_PROMPT = """You are reading several organizations' calendar pages at once for the editor of \
Your Daily Phil, a daily newsletter for the Jewish philanthropy and nonprofit sector. He compiles \
"What We're Watching": upcoming events his national readership of funders, federation executives and \
nonprofit leaders would want on their radar.

He wants two kinds of item:
  - significant institutional convenings (conferences, general assemblies, biennials, summits, major \
galas, missions, award ceremonies)
  - genuinely distinctive or offbeat cultural events (a Yiddish appreciation gala, a klezmer retreat, \
a notable museum opening or film premiere)

He does NOT want routine local programming: chapter book clubs, mahjong afternoons, baby playgroups, \
support groups, walking tours, trivia nights, fitness classes, congregational services, admissions \
webinars or staff training — even when a national organization hosts them.

Today is {today}. Only return events dated today or later, as YYYY-MM-DD. If a date is genuinely \
ambiguous, omit the event rather than guessing. Ignore navigation, cookie banners, bot-check text, \
address lists and donation appeals entirely — they are not events.

Set "source" to the PAGE number the event came from.

{pages}

Return every real upcoming event across all pages, with newsworthy=true only for those meeting the \
bar above."""


def extract_events_batch(pages, today, max_chars_each=9000):
    """pages: [{'org','url','text'}]. One API call for the whole batch."""
    blocks = []
    for i, p in enumerate(pages):
        blocks.append(f"=== PAGE {i} — {p['org']} — {p['url']} ===\n"
                      f"{p['text'][:max_chars_each]}")
    out = _call(BATCH_PROMPT.format(today=today, pages="\n\n".join(blocks)),
                schema=BATCH_SCHEMA, max_tokens=16384)
    return out.get("events", [])


# ---------------- gifts ----------------
GIFT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "donor": {"type": "string", "description": "Donor name(s), or empty if unnamed"},
                    "recipient": {"type": "string"},
                    "amount_usd": {"type": "number"},
                    "is_gift": {"type": "boolean",
                                "description": "A charitable gift, not an investment or budget item"},
                    "jewish_angle": {"type": "string",
                                     "enum": ["donor", "recipient", "both", "none", "unclear"]},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "note": {"type": "string", "description": "One clause for the editor."},
                },
                "required": ["id", "is_gift", "jewish_angle", "confidence"],
            },
        }
    },
    "required": ["results"],
}

GIFT_PROMPT = """You are screening philanthropy headlines for the editor of Your Daily Phil, a daily \
newsletter covering Jewish philanthropy. He writes a "Major Gifts" section.

What he most wants and cannot easily find: a large gift where the DONOR is Jewish or connected to the \
Jewish communal world, but the RECIPIENT is secular — a university, hospital, museum or arts \
institution. Big Jewish organizations already email him their own gift announcements, so those are \
useful but less valuable.

For each headline decide:
  is_gift        — a charitable gift or pledge, not an investment, construction budget, revenue \
figure, campaign total, or political contribution.
  jewish_angle   — "donor" if the giver is Jewish or Jewish-communally connected; "recipient" if the \
recipient is a Jewish institution; "both"; "none"; "unclear" if you cannot tell from the headline.
  donor          — the person, couple, family or foundation giving. Empty if anonymous or unnamed.

Be honest about uncertainty. Use "unclear" and confidence "low" rather than guessing someone's \
background from a surname alone — a wrong call wastes the editor's time and could be offensive. \
Only say "donor" when you actually recognize the person or the headline makes the connection explicit.

Headlines:
{items}

Return one result per id."""


def judge_gifts(items, batch=120):
    """items: list of dicts with title/source. Returns list of verdicts."""
    out = []
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        listing = "\n".join(
            f"{n}. {x['title']}  [source: {x.get('source','')}]"
            for n, x in enumerate(chunk, start=i))
        res = _call(GIFT_PROMPT.format(items=listing), schema=GIFT_SCHEMA, max_tokens=16384)
        out.extend(res.get("results", []))
    return out
