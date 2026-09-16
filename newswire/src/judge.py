"""The meaning-based pass that keyword scoring structurally cannot do.

WHAT IT IS FOR, precisely. Measured on eJP's own 429 published Major Gifts, only 173
(40.3%) contain any Jewish or Israel word in the headline. The other 60% are general
philanthropy — a gift to a secular university, hospital or museum — and they belong in
the section because the DONOR is Jewish or connected to the Jewish communal world. That
is knowledge about people, not vocabulary, and no word list will ever reach it. This is
the step eJP's team does by hand.

TWO DIRECTIONS, both of which fail safe.

  rescue()  promotes near-misses the scorer rejected. Failure costs the rescues.
  keep()    screens gifts that carry no Jewish signal at all, which measured at 81% of
            everything reaching the Major Gifts channel over seven live days. Failure
            returns None, and the caller then RELEASES the whole batch unscreened —
            exactly today's behaviour.

Neither direction can produce silence, and that is the design constraint rather than an
accident. An earlier version of this system posted "Nothing found today" when the model
had in fact returned 503, and a quality filter that turns into a silence filter during an
outage is worse than no filter at all.

QUOTA. Gemini's free tier is 20 requests per DAY, not per minute; that was learned the
expensive way. At a 15-minute cadence there are 96 runs a day, so a per-item call, or
even a per-run call, is impossible. Hence: one batched call per run at most, a hard
daily ceiling enforced before the call, and the counter kept in status.json — which is
already committed every run, so it survives the fresh checkout each CI run starts from.
A quota file of its own was tried in this repo before and caused rebase conflicts.

NEVER infer someone's background from a surname. It is unreliable and offensive when
wrong. The prompt says so, and an unsure verdict is expected to come back as `false`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

# Cheap, fast, and current. gemini-2.5-flash is closed to new API keys (a fresh key gets
# 404) and gemini-2.0-flash has no free tier at all — a limit of zero — which cost a
# day's quota to discover.
MODELS = ("gemini-3.5-flash", "gemini-3.6-flash")

MAX_CALLS_PER_DAY = 40
MAX_ITEMS_PER_CALL = 25

# Quota allocation, and the reason it has to exist.
#
# On 16 September the rescue pass spent the entire day's budget by mid-afternoon — it
# fired on nearly every run, because most runs have at least one near-miss, and 96 runs a
# day will exhaust any per-day budget. The screening pass, which is the one that fixes
# 81% of the channel being off-beat, never got a single call.
#
# So screening has first claim. Rescue may only spend while there is more than the
# reserve left, and only when it has a batch worth spending on rather than one headline.
RESERVE_FOR_KEEP = 20
MIN_RESCUE_BATCH = 6
MAX_OUTPUT_TOKENS = 4000     # thinking models spend the budget thinking; 1200 truncated

SYSTEM_PROMPT = """You screen news for "Major Gifts" in Your Daily Phil, the daily \
newsletter of eJewishPhilanthropy, read by Jewish foundation staff, federation \
executives and major donors.

Each item below was rejected by a keyword filter. Your job is to catch the ones that \
were rejected wrongly. Be selective: most of them were rejected correctly.

Say TRUE only if the item reports an actual philanthropic gift, grant, pledge, bequest, \
endowment or donation AND it would interest that readership. Measured from the real \
archive, that means:

- 57% of published gifts are general philanthropy with no Jewish angle on the recipient \
side. A large gift to a secular university, hospital, museum or theater BELONGS when the \
donor is Jewish or active in Jewish communal life. This is the main thing you are here \
to catch.
- There is NO dollar floor. The median is $5.3M but 16% are under $1M, the smallest is \
$9,500, and 18% carry no figure at all — donated medical supplies, a donated artwork, \
"an undisclosed sum". Shekels and NIS are normal.
- Capital projects, grant awards, endowed chairs, professorships, fellowships, \
scholarships, naming announcements, endowments, bequests and crowdfunding milestones all \
count.

Say FALSE for: political or campaign fundraising; investment rounds, earnings or \
acquisitions; government appropriations and budget lines; construction budgets; an \
institution's total annual fundraising; commentary or opinion about philanthropy; \
profiles; and anything where no gift actually happened.

DONOR IDENTIFICATION. Judge only on what you actually know about the person — their \
known philanthropy, their communal roles, their own public statements. NEVER infer \
someone's religion or ethnicity from their surname; it is unreliable and offensive when \
wrong. If you do not know, answer false. A missed item costs far less than a wrong \
guess about who someone is.

Return one entry per item id you were given a verdict for. Omit ids you would reject."""


KEEP_PROMPT = """You screen news for "Major Gifts" in Your Daily Phil, the daily \
newsletter of eJewishPhilanthropy, read by Jewish foundation staff, federation \
executives and major donors.

Every item below IS a real philanthropic gift — that part is already established, do not \
re-litigate it. Not one of them mentions anything Jewish or Israeli in its headline. Your \
only question is whether this particular gift would interest that readership anyway.

KEEP it if:
- The DONOR is Jewish or active in Jewish communal life. This is the main case. eJP runs \
gifts to secular universities, hospitals, museums and theaters all the time when a known \
Jewish philanthropist is behind them — 57% of the section is general philanthropy for \
exactly this reason.
- The recipient does work the Jewish communal world follows closely: Holocaust education \
or memory, antisemitism research or security, Israel studies, refugee resettlement.
- The gift is so large or unusual that it is news across the whole philanthropic sector \
regardless of who gave it — a nine-figure gift, a record for its field, a landmark bequest.

DROP it if it is simply a gift somewhere in the world with no connection to any of that: \
a local service club's donation, a regional hospital's fundraiser, a community \
foundation's routine grant round, a parochial school's bequest with no Jewish tie.

DONOR IDENTIFICATION. Judge only on what you actually know about the person — their \
known philanthropy, their communal roles, their own public statements. NEVER infer \
someone's religion or ethnicity from their surname; it is unreliable and offensive when \
wrong. If you do not know who the donor is, DROP it. This section runs a handful of items \
a day and the editor would rather see five right ones than twenty maybes.

Return the ids to KEEP, with one short clause saying why. Omit everything else."""


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def calls_left(status_doc: dict) -> int:
    used = (status_doc.get("judge_calls") or {}).get(_today(), 0)
    return max(0, MAX_CALLS_PER_DAY - int(used))


def _spend(run_status) -> None:
    """Count a call only AFTER it succeeded.

    Counting before meant a run of failures burned a whole day's quota without a single
    usable answer, which is exactly how a previous version of this went dark.
    """
    calls = dict(getattr(run_status, "judge_calls", {}) or {})
    calls[_today()] = int(calls.get(_today(), 0)) + 1
    # Keep only the last few days; this rides in a file that is committed every run.
    run_status.judge_calls = dict(sorted(calls.items())[-3:])


def candidates(items, scorer, limit: int = MAX_ITEMS_PER_CALL) -> list:
    """Near-misses worth asking about.

    Not everything the scorer rejected — that is hundreds of items a run, most of them
    obviously off-beat. Only those that already look transactional: something scored on
    the giving axis, but not enough to clear the bar on its own.
    """
    out = []
    for item in items:
        v = scorer.score_stream("major_gift", item.title, item.body)
        if v.vetoed or scorer.admits_stream("major_gift", v, item.threshold):
            continue
        if "giving" not in v.axes and "giving_topic" not in v.axes:
            continue
        out.append((item, v.score))
    out.sort(key=lambda p: -p[1])
    return [i for i, _ in out[:limit]]


def _ask(items, run_status, status_doc, system_prompt, key) -> set[str] | None:
    """One batched call. Returns the chosen URLs, or None if the call could not be made.

    None and an empty set mean different things, and the callers depend on it: None is
    "no verdict" (degrade to whatever the scorer decided), empty set is "the judge
    looked and chose nothing".
    """
    if not items or not available():
        return None
    if calls_left(status_doc) <= 0:
        print(f"  judge: daily quota spent ({MAX_CALLS_PER_DAY} calls); no verdict this run")
        return None

    listing = "\n".join(
        f"{n}. {i.title}" + (f"\n   [{i.outlet}] {(i.body or '')[:140]}" if i.body
                             else f"   [{i.outlet}]")
        for n, i in enumerate(items))

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("  judge: google-genai not installed", file=os.sys.stderr)
        return None

    schema = {
        "type": "object",
        "properties": {
            key: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "why": {"type": "string"},
                    },
                    "required": ["id", "why"],
                },
            }
        },
        "required": [key],
    }

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    for model in MODELS:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=listing,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=schema,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
        except Exception as e:  # noqa: BLE001 — every failure degrades to keyword-only
            # Printed in full for quota errors specifically: MAX_CALLS_PER_DAY is a guess
            # at the free tier's real ceiling, and a RESOURCE_EXHAUSTED message is the
            # only thing that will ever tell us the true number.
            detail = str(e)
            width = 400 if "RESOURCE_EXHAUSTED" in detail or "429" in detail else 90
            print(f"  judge: {model} failed ({type(e).__name__}: {detail[:width]})")
            continue

        raw = (getattr(resp, "text", "") or "").strip()
        if not raw:
            print(f"  judge: {model} returned nothing")
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  judge: {model} returned unparseable JSON")
            continue

        _spend(run_status)
        chosen = set()
        for entry in data.get(key, []):
            idx = entry.get("id")
            if isinstance(idx, int) and 0 <= idx < len(items):
                chosen.add(items[idx].url)
                print(f"  judge {key.upper()}: {items[idx].title[:66]}")
                print(f"      {str(entry.get('why', ''))[:88]}")
        return chosen

    print("  judge: every model failed; no verdict this run")
    return None


def rescue(items, run_status, status_doc) -> set[str]:
    """URLs the judge promotes out of the near-miss pile. Never suppresses anything.

    Yields to the screening pass: it will not spend when the remaining budget is down to
    the reserve, and it will not spend a call on a handful of headlines.
    """
    if len(items) < MIN_RESCUE_BATCH:
        return set()
    if calls_left(status_doc) <= RESERVE_FOR_KEEP:
        print(f"  judge: holding the last {RESERVE_FOR_KEEP} calls for screening; "
              f"skipping rescue")
        return set()
    got = _ask(items, run_status, status_doc, SYSTEM_PROMPT, "rescue")
    if got is None:
        return set()
    if not got:
        print(f"  judge: reviewed {len(items)} near-miss(es), rescued none")
    return got


def keep(items, run_status, status_doc) -> set[str] | None:
    """URLs to keep out of a batch of gifts that carry no Jewish signal.

    Returns None when no verdict could be obtained, and the caller must then release the
    whole batch unscreened rather than hold or drop it. A model outage must never become
    a silence.
    """
    got = _ask(items, run_status, status_doc, KEEP_PROMPT, "keep")
    if got is not None:
        print(f"  judge: screened {len(items)} generic gift(s), kept {len(got)}")
    return got
