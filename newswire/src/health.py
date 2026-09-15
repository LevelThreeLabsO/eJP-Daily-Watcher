"""Does this watcher look healthy, and is that worth telling somebody?

Everything here is derived from status.json, which every run writes. Nothing here
decides where a finding goes — poll.py does that, and the rules it follows are the two
that the earlier version of health alerting broke:

  1. Findings NEVER go to the story channels. A newswire's channels carry stories and
     nothing else. Health goes to SLACK_HEALTH if that webhook exists, and otherwise
     stays in status.json, which is a public URL on this repo.
  2. Every finding has a cooldown that survives between runs. On 30 August a dead-feed
     alert with no cooldown posted twelve identical messages in an hour into a live
     channel. A finding that repeats every 15 minutes is not information.

Severity decides escalation, not wording. A `critical` finding also fails the workflow
run, which is the one alert that needs no setup at all: GitHub emails the repo owner
when a run fails. That is deliberately reserved for "this thing is broken", never for
"it is quiet", because a run that fails on every quiet night trains you to ignore it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# A source that has returned nothing this many runs in a row is probably not quiet.
# At a 15-minute cadence 192 runs is roughly two days.
EMPTY_RUNS_BEFORE_ALERT = 192
PARSE_FAILS_BEFORE_ALERT = 8

# How long the channels may be silent before that is itself news. Deliberately long:
# overnight silence is normal on this beat, and a 6-hour alarm would fire every night.
SILENCE_HOURS = 18

# Per-kind cooldowns. A dead feed stays dead; saying so daily is enough.
COOLDOWN_HOURS = {
    "delivery": 1,      # Slack refusing a post is urgent and usually brief
    "run_error": 3,
    "silence": 12,
    "dead_sources": 24,
}


@dataclass
class Finding:
    kind: str
    severity: str        # "critical" fails the run; "warning" only reports
    text: str


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def check(doc: dict, now: datetime | None = None) -> list[Finding]:
    """Read a status document and say what is wrong with it."""
    now = now or datetime.now(timezone.utc)
    out: list[Finding] = []

    if doc.get("error"):
        out.append(Finding("run_error", "critical",
                           f"Last run raised: {str(doc['error'])[:300]}"))

    # delivered is None when there was simply nothing to post, which is not a failure.
    if doc.get("delivered") is False:
        out.append(Finding("delivery", "critical",
                           "Slack refused a post. Items were rolled back and will be "
                           "retried, but a revoked or mistyped webhook will not "
                           "recover on its own."))

    last = _parse(doc.get("last_posted_at"))
    if last and now - last > timedelta(hours=SILENCE_HOURS):
        hours = (now - last).total_seconds() / 3600
        out.append(Finding("silence", "warning",
                           f"Nothing has posted in {hours:.0f} hours. The pipeline is "
                           f"running, so this is either a genuinely quiet stretch or "
                           f"something upstream stopped producing."))

    # Only sources this run actually fetched. A disabled or deleted source keeps its
    # streak in status.json forever — nothing clears it, because nothing fetches it — so
    # without this filter the first alert is about a source that was deliberately turned
    # off, which is precisely the kind of noise that gets alerting muted.
    live_keys = set(doc.get("sources") or {})
    dead = []
    for key, n in (doc.get("consecutive_empty") or {}).items():
        if key in live_keys and n >= EMPTY_RUNS_BEFORE_ALERT:
            dead.append(f"{key} (empty {n} runs)")
    for key, n in (doc.get("consecutive_parse_fail") or {}).items():
        if key in live_keys and n >= PARSE_FAILS_BEFORE_ALERT:
            dead.append(f"{key} (unparseable {n} runs)")
    if dead:
        out.append(Finding("dead_sources", "warning",
                           "Sources that have stopped producing: " + ", ".join(sorted(dead))
                           + ". Run `poll.py --audit` — a dead feed answers HTTP 200 and "
                             "serves a web page, so nothing else will notice."))
    return out


def due(findings: list[Finding], sent: dict, now: datetime | None = None) -> list[Finding]:
    """Drop findings whose cooldown has not elapsed. `sent` is {kind: iso timestamp}."""
    now = now or datetime.now(timezone.utc)
    fresh = []
    for f in findings:
        last = _parse(sent.get(f.kind))
        if last and now - last < timedelta(hours=COOLDOWN_HOURS.get(f.kind, 12)):
            continue
        fresh.append(f)
    return fresh


def format(findings: list[Finding], repo: str | None = None) -> str:
    lines = ["*eJP newswire — health*"]
    for f in findings:
        mark = ":rotating_light:" if f.severity == "critical" else ":warning:"
        lines.append(f"{mark} {f.text}")
    if repo:
        lines.append(f"_status: https://raw.githubusercontent.com/{repo}/main/newswire/status.json_")
    return "\n".join(lines)
