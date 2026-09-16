"""A holding queue for gifts that carry no Jewish signal of their own.

WHY THIS EXISTS. Measured over seven days of live output, 107 items reached the Major
Gifts channel and 87 of them — around 81% — had no Jewish or Israel connection at all:
a Jesuit school's bequest, a Kiwanis book donation, a trailer given to a veterans
museum. Every one is a real gift correctly identified by the scorer. None is eJP's.

The obvious fix does not work. Requiring a Jewish word in the headline drops gift recall
from 81.8% to 34.7%, losing 262 of 429 real published gifts, because 60% of what eJP runs
is general philanthropy that belongs on the strength of who the DONOR is. So the decision
cannot be made from vocabulary; it needs the judge.

WHY A QUEUE RATHER THAN A CALL PER RUN. There are 96 runs a day at a 15-minute cadence
and the free Gemini tier allows nowhere near 96 calls. Gifts are also the one thing here
that is not time-critical — "What We're Watching" is a same-day brief, but a gift holds.
So generic gifts wait here until there are enough to be worth a call, or until they have
waited long enough, and go out in a screened batch. That costs a gift up to an hour of
latency and brings the call count to roughly 24 a day.

FAILURE MODE. If the judge cannot be reached, or the queue has waited too long to keep
waiting, everything in it is RELEASED — posted unscreened, exactly as it is today.
Holding items hostage to a model outage would turn a quality problem into a silence
problem, and silence is the worse failure.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

QUEUE_FILE = Path(__file__).resolve().parent.parent / "pending_gifts.json"

MIN_BATCH = 4           # fewer than this is not worth a call...
MAX_HOLD_MINUTES = 60   # ...unless the oldest has waited this long
HARD_RELEASE_HOURS = 6  # never hold anything longer than this, screened or not
MAX_QUEUE = 60          # a runaway queue means something else is wrong; post and move on

FIELDS = ("title", "url", "outlet", "desk", "published", "body", "score", "source_key")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def load() -> list[dict]:
    if not QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(QUEUE_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 — a corrupt queue must not stop the newswire
        return []


def save(queue: list[dict]) -> None:
    QUEUE_FILE.write_text(json.dumps(queue[-MAX_QUEUE:], indent=1) + "\n")


def add(queue: list[dict], items) -> list[dict]:
    """Append items not already queued. Returns the new queue."""
    have = {e.get("url") for e in queue}
    stamp = _now().isoformat(timespec="seconds")
    for i in items:
        if i.url in have:
            continue
        entry = {f: getattr(i, f, None) for f in FIELDS}
        entry["published"] = i.published.isoformat() if getattr(i, "published", None) else None
        entry["queued_at"] = stamp
        queue.append(entry)
        have.add(i.url)
    return queue


def is_due(queue: list[dict], now: datetime | None = None) -> bool:
    """Is it worth spending a model call on this queue yet?"""
    if not queue:
        return False
    now = now or _now()
    if len(queue) >= MIN_BATCH or len(queue) >= MAX_QUEUE:
        return True
    oldest = min((_parse(e.get("queued_at")) or now) for e in queue)
    return now - oldest >= timedelta(minutes=MAX_HOLD_MINUTES)


def overdue(queue: list[dict], now: datetime | None = None) -> list[dict]:
    """Entries that have waited past the hard limit and must go out unscreened.

    A gift held forever because the judge is unreachable is a gift nobody sees, which is
    worse than a gift nobody wanted.
    """
    now = now or _now()
    cutoff = now - timedelta(hours=HARD_RELEASE_HOURS)
    return [e for e in queue if (_parse(e.get("queued_at")) or now) <= cutoff]


def remove(queue: list[dict], urls: set[str]) -> list[dict]:
    return [e for e in queue if e.get("url") not in urls]


def to_items(entries: list[dict], template_cls):
    """Rebuild Item objects from queue entries so digest.build can render them."""
    out = []
    for e in entries:
        out.append(template_cls(
            source_key=e.get("source_key") or "pending",
            outlet=e.get("outlet") or "",
            title=e.get("title") or "",
            url=e.get("url") or "",
            published=_parse(e.get("published")),
            body=e.get("body") or "",
            desk=e.get("desk"),
            category="major_gift",
            score=int(e.get("score") or 0),
        ))
    return out
