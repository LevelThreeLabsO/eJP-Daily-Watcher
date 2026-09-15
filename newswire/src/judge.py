"""The meaning-based pass that keyword scoring structurally cannot do.

WHAT IT IS FOR, precisely. Measured on eJP's own 429 published Major Gifts, only 173
(40.3%) contain any Jewish or Israel word in the headline. The other 60% are general
philanthropy — a gift to a secular university, hospital or museum — and they belong in
the section because the DONOR is Jewish or connected to the Jewish communal world. That
is knowledge about people, not vocabulary, and no word list will ever reach it. This is
the step eJP's team does by hand.

RESCUE ONLY. The judge can promote an item the scorer rejected. It can never suppress
one the scorer accepted. That asymmetry is deliberate: a model outage then costs nothing
but the rescues, and the newswire degrades to exactly its keyword behaviour rather than
going quiet. The failure this is written against is real — an earlier version of this
system posted "Nothing found today" when the model had in fact returned 503.

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

MAX_CALLS_PER_DAY = 16       # under the 20/day free cap, with headroom for retries
MAX_ITEMS_PER_CALL = 25
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


def screen(items, run_status, status_doc) -> set[str]:
    """Return the URLs of items the judge rescues. Never raises."""
    if not items or not available():
        return set()
    if calls_left(status_doc) <= 0:
        print(f"  judge: daily quota spent ({MAX_CALLS_PER_DAY} calls); keyword-only this run")
        return set()

    listing = "\n".join(
        f"{n}. {i.title}" + (f"\n   [{i.outlet}] {(i.body or '')[:140]}" if i.body
                             else f"   [{i.outlet}]")
        for n, i in enumerate(items))

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("  judge: google-genai not installed; keyword-only", file=os.sys.stderr)
        return set()

    schema = {
        "type": "object",
        "properties": {
            "rescue": {
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
        "required": ["rescue"],
    }

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    for model in MODELS:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=listing,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=schema,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
        except Exception as e:  # noqa: BLE001 — every failure degrades to keyword-only
            print(f"  judge: {model} failed ({type(e).__name__}: {str(e)[:90]})")
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
        rescued = set()
        for entry in data.get("rescue", []):
            idx = entry.get("id")
            if isinstance(idx, int) and 0 <= idx < len(items):
                rescued.add(items[idx].url)
                print(f"  judge RESCUE: {items[idx].title[:70]}")
                print(f"                {str(entry.get('why', ''))[:88]}")
        if not rescued:
            print(f"  judge: reviewed {len(items)} near-miss(es), rescued none")
        return rescued

    print("  judge: every model failed; keyword-only this run")
    return set()
