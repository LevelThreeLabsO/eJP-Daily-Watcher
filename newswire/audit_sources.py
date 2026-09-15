#!/usr/bin/env python3
"""Is every source alive, fresh and parseable?

Run this monthly, and weekly while the list stays Google-News-heavy.

It exists because of how a feed dies. It does not return an error. It answers HTTP 200
and serves a web page, or a feed whose newest item is sixteen days old, or the same feed
under a publisher label Google quietly renamed — and the only symptom is a channel that
is slightly quieter than it was. Nothing in a normal run reports that: poll.py prints
"0 in window" for a dead feed and for a genuinely quiet Tuesday alike.

What this adds over a normal run is the wide window. Each source is fetched with a
14-day horizon, so "nothing published in the last six hours" and "nothing published this
month" stop looking the same.

    python poll.py --audit                 every source
    python poll.py --audit --source jta    one, repeatable

Read the columns as: ENTRIES is what the feed served, KEPT is what survived the publisher
and language checks, and FRESHEST is the age of the newest item. A source with entries
but nothing kept is usually a publisher-label drift, not a dead feed — Google relabels
outlets from masthead to domain without warning, and that reads as total silence.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import yaml

from src.fetch import ParseFailure, fetch_source, now_utc

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.yaml"

HORIZON_DAYS = 14
STALE_DAYS = 7          # a feed whose newest item is older than this is not working
MAX_WORKERS = 12


def _age(item, now):
    when = getattr(item, "published", None) or getattr(item, "effective_date", None)
    if not when:
        return None
    return now - when


def _fmt_age(delta) -> str:
    if delta is None:
        return "undated"
    # Some feeds stamp items slightly in the future; a negative age is noise, not news.
    hours = max(0.0, delta.total_seconds() / 3600)
    if hours < 48:
        return f"{hours:4.0f}h"
    return f"{hours / 24:4.0f}d"


def audit_one(source: dict, now):
    """Fetch one source over a wide horizon. Never raises — the verdict is the return.

    For an RSS feed the horizon is ten years, not fourteen days, and the reason is worth
    keeping. With a 14-day window a feed frozen in June 2025 returns thirty entries and
    zero items, which is indistinguishable from a publisher label that drifted — and the
    first version of this file reported the Jerusalem Post, dead for fifteen months, as a
    label problem. Taking everything the feed has and measuring the newest item separates
    the two.

    For Google News the horizon cannot be widened, because there it is part of the query:
    `when:3650d` asks a different question than `when:14d` and Google ranks a different
    hundred results back. Auditing the Australian Jewish News at ten years reported it
    stale at twelve days while a three-day search returned thirteen fresh items. So these
    are audited at the horizon they actually run against.
    """
    wide = source.get("method") == "rss"
    since = now - timedelta(days=3650 if wide else HORIZON_DAYS)
    try:
        items, entries = fetch_source(source, since)
    except ParseFailure as e:
        return {"key": source["key"], "verdict": "UNPARSEABLE", "note": str(e)[:70],
                "entries": 0, "kept": 0, "freshest": None}
    except Exception as e:  # noqa: BLE001 — a failure here is a finding, not a crash
        return {"key": source["key"], "verdict": "ERROR",
                "note": f"{type(e).__name__}: {e}"[:70],
                "entries": 0, "kept": 0, "freshest": None}

    ages = [a for a in (_age(i, now) for i in items) if a is not None]
    freshest = min(ages) if ages else None
    # KEPT is what a normal run would actually have to work with.
    recent = sum(1 for a in ages if a <= timedelta(days=HORIZON_DAYS))

    if entries == 0:
        verdict, note = "EMPTY", "feed answered but served no entries"
    elif not items:
        # The feed is alive and every single entry was thrown away. Almost always the
        # publisher label, not the source: Google relabels outlets from masthead to
        # domain without warning, and that reads as total silence.
        verdict, note = "ALL DROPPED", "publisher/language check rejected every entry"
    elif freshest is None:
        verdict, note = "ok", "undated (ages from first sighting)"
    elif freshest > timedelta(days=STALE_DAYS):
        verdict, note = "STALE", f"newest item is {freshest.days} days old"
    else:
        verdict, note = "ok", ""

    return {"key": source["key"], "verdict": verdict, "note": note,
            "entries": entries, "kept": recent, "freshest": freshest}


def main(only: list[str] | None = None) -> int:
    from concurrent.futures import ThreadPoolExecutor

    config = yaml.safe_load(SOURCES_FILE.read_text()) or {}
    sources = [s for s in config.get("sources", []) if s.get("enabled", True)]
    if only:
        wanted = set(only)
        sources = [s for s in sources if s["key"] in wanted]
        missing = wanted - {s["key"] for s in sources}
        if missing:
            print(f"unknown source key(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2

    now = now_utc()
    print(f"Auditing {len(sources)} sources over a {HORIZON_DAYS}-day horizon "
          f"(stale = newest item older than {STALE_DAYS} days)\n")
    print(f"  {'SOURCE':22} {'METHOD':13} {'ENTRIES':>7} {'KEPT':>5} {'FRESHEST':>9}  VERDICT")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda s: (s, audit_one(s, now)), sources))

    bad = []
    for source, r in results:
        flag = " " if r["verdict"] == "ok" else "!"
        print(f"{flag} {r['key']:22} {source.get('method', ''):13} {r['entries']:>7} "
              f"{r['kept']:>5} {_fmt_age(r['freshest']):>9}  {r['verdict']}"
              + (f" — {r['note']}" if r["note"] else ""))
        if r["verdict"] != "ok":
            bad.append(r)

    healthy = len(results) - len(bad)
    print(f"\n{healthy}/{len(results)} healthy.")
    if bad:
        print("\nNeeds attention:")
        for r in bad:
            print(f"  {r['key']:22} {r['verdict']:12} {r['note']}")
        print("\n  ERROR 403/429 from a cloud runner but fine from a laptop means the host\n"
              "  blocks datacenter IPs — the source needs a different route, not a fix.\n"
              "  ALL DROPPED is usually a publisher label that changed; check what the feed\n"
              "  now calls itself and add it to that source's `publisher:` list.")
    # Findings are not failures: this is a report, and a non-zero exit would turn every
    # scheduled audit red over one blocked host.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or None))
