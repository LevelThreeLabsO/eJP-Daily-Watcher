#!/usr/bin/env python3
"""Scoring recall and noise, measured against eJP's own published archive.

Positives are real published items (429 Major Gifts, 1,085 What We're Watching).
Negatives are real headlines from the same feeds that eJP did not run.

Recall is the number that matters: a word removed from scoring.yaml shows up here as
items the desk would no longer see. Noise is the cost side — some negatives are
genuinely borderline, so this reports the rate rather than demanding zero.

Read the noise rate as a ceiling, not a defect count. The negative set is "headlines eJP
did not publish", and the section runs about two gifts a day out of dozens that qualify,
so it is full of items that are squarely on-beat and simply did not make the edition —
"Boston Ballet receives $10M donation, largest endowment gift in its history" sits in
there. A newswire surfacing that for an editor is working, not failing. Chase a rise in
this number only after reading which headlines caused it; roughly a fifth of what it
counts is the tool doing its job.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.score import Scorer
from src import state, health, pending, dedup

HERE = os.path.dirname(os.path.abspath(__file__))


def merge_test():
    """State merge must actually run.

    It did not for a week: `words` is a list, the title key tupled it with `at` into a
    dict key, and every call raised TypeError into a bare `except: pass` at both call
    sites. Nothing looked broken — pushes succeeded, state saved, and each push race
    quietly dropped the other run's dedup keys. This is the only thing that would have
    caught it, so it runs on every change.
    """
    a = {"seen": {"aaa": "2026-09-01T00:00:00Z"}, "first_seen": {"aaa": "2026-09-01T00:00:00Z"},
         "titles": [{"words": ["gift", "hillel"], "at": "2026-09-01T00:00:00Z", "outlet": "JTA"}]}
    b = {"seen": {"aaa": "2026-09-02T00:00:00Z", "bbb": "2026-09-02T00:00:00Z"},
         "first_seen": {}, "titles": [{"words": ["gala"], "at": "2026-09-02T00:00:00Z",
                                       "outlet": "Forward"}]}
    try:
        m = state.merge(a, b)
    except Exception as e:
        return [f"{'State merge':<22} FAILED  {type(e).__name__}: {e}"], True

    problems = []
    if set(m["seen"]) != {"aaa", "bbb"}:
        problems.append(f"      merge lost keys: {sorted(m['seen'])}")
    if m["seen"].get("aaa") != "2026-09-01T00:00:00Z":
        problems.append("      merge kept the later stamp; it must keep the earliest")
    if len(m["titles"]) != 2:
        problems.append(f"      merge lost remembered titles: {len(m['titles'])} of 2")
    if problems:
        return [f"{'State merge':<22} FAILED"] + problems, True
    return [f"{'State merge':<22} ok       union + earliest-stamp + titles"], False


def load(name):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return []
    return [l.strip() for l in open(p) if l.strip() and not l.startswith("#")]


def health_test():
    """Health findings, cooldowns, and the disabled-source filter.

    The cooldown is the part worth testing. A dead-feed alert with no cooldown once
    posted twelve identical messages into a live channel in an hour, and a cooldown that
    lives in process memory is no cooldown at all, because every run is a new process —
    it has to survive in status.json.
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    doc = {"sources": {"a": {}, "b": {}},
           "consecutive_empty": {"a": 999, "turned_off": 999},
           "consecutive_parse_fail": {"b": 99},
           "last_posted_at": (now - timedelta(hours=40)).isoformat(),
           "delivered": False, "error": None}
    problems = []
    found = health.check(doc, now)
    kinds = {f.kind for f in found}
    if kinds != {"delivery", "silence", "dead_sources"}:
        problems.append(f"      expected delivery/silence/dead_sources, got {sorted(kinds)}")
    if not any(f.severity == "critical" for f in found):
        problems.append("      a refused delivery must be critical (it fails the run)")
    if any(f.severity == "critical" and f.kind == "silence" for f in found):
        problems.append("      silence must NOT be critical — a quiet night is not a failure")
    if "turned_off" in health.format(found):
        problems.append("      a disabled source must not raise a finding")
    sent = {f.kind: now.isoformat() for f in found}
    if health.due(found, sent, now):
        problems.append("      cooldown did not suppress an immediate repeat")
    if not health.due(found, sent, now + timedelta(hours=25)):
        problems.append("      cooldown never expires")
    if problems:
        return [f"{'Health checks':<22} FAILED"] + problems, True
    return [f"{'Health checks':<22} ok       findings + cooldown + disabled filter"], False


def pending_test():
    """The generic-gift holding queue.

    The property that matters is the failure mode: when the judge gives no verdict the
    queue must RELEASE, never hold. A quality filter that becomes a silence filter during
    a model outage is worse than no filter, and this system has shipped that bug before.
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    problems = []

    q = []
    if pending.is_due(q, now):
        problems.append("      an empty queue must never be due")

    q = [{"url": f"u{n}", "queued_at": now.isoformat()} for n in range(2)]
    if pending.is_due(q, now):
        problems.append(f"      {len(q)} items is under MIN_BATCH and should not be due")

    q = [{"url": f"u{n}", "queued_at": now.isoformat()} for n in range(pending.MIN_BATCH)]
    if not pending.is_due(q, now):
        problems.append("      a full batch must be due")

    old = (now - timedelta(minutes=pending.MAX_HOLD_MINUTES + 5)).isoformat()
    if not pending.is_due([{"url": "u", "queued_at": old}], now):
        problems.append("      one item held past MAX_HOLD_MINUTES must be due")

    ancient = (now - timedelta(hours=pending.HARD_RELEASE_HOURS + 1)).isoformat()
    forced = pending.overdue([{"url": "old", "queued_at": ancient},
                              {"url": "new", "queued_at": now.isoformat()}], now)
    if [e["url"] for e in forced] != ["old"]:
        problems.append(f"      overdue() should release only the stale one, got {forced}")

    left = pending.remove([{"url": "a"}, {"url": "b"}], {"a"})
    if [e["url"] for e in left] != ["b"]:
        problems.append("      remove() did not drop the named url")

    if problems:
        return [f"{'Pending queue':<22} FAILED"] + problems, True
    return [f"{'Pending queue':<22} ok       batching + hold limits + release"], False


def dedup_test():
    """Cross-outlet duplicate detection, and the rollback that undoes it.

    The signature rule exists because word overlap could not do this job: replayed over
    578 real posted items, the 0.5 overlap rule caught exactly one duplicate while six
    outlets ran the Kraft $2 million story. These are the real headlines.
    """
    problems = []

    class FakeItem:
        def __init__(self, title, outlet):
            self.title, self.outlet = title, outlet

    first = FakeItem("Robert Kraft Says Ed Sheeran Asked Him to Match $2M Donation", "People.com")
    money, names = dedup.signature(first.title)
    recent = [{"words": dedup.title_words(first.title), "outlet": first.outlet,
               "money": sorted(money), "names": sorted(names)}]

    same_story = [
        ("Patriots owner Robert Kraft pledges $2 million donation after Macklemore", "New York Times"),
        ("Robert Kraft says Ed Sheeran asked him to donate $2m in aid", "BBC"),
        ("Robert Kraft donates $2 million for humanitarian crisis in Palestine", "Jerusalem Post"),
    ]
    for title, outlet in same_story:
        if not dedup.signature_match(FakeItem(title, outlet), recent):
            problems.append(f"      missed duplicate: {title[:58]}")

    # Same outlet is level 2's job, not this one.
    if dedup.signature_match(FakeItem(same_story[0][0], "People.com"), recent):
        problems.append("      matched within the same outlet; that is level 2's job")

    # Different amount, same person: a follow-up, not a duplicate.
    other = FakeItem("Robert Kraft endows new Jewish life center at Brandeis with $10 million",
                     "Boston Globe")
    if dedup.signature_match(other, recent):
        problems.append("      merged two different Kraft gifts ($2M and $10M)")

    # A shared amount with no shared name must not match: a day is full of $1M gifts.
    unrelated = FakeItem("Anonymous donor gives $2 million to Cleveland Clinic", "Crain's")
    if dedup.signature_match(unrelated, recent):
        problems.append("      matched on the amount alone")

    # Rollback must remove what was remembered.
    st = state.blank()
    state.remember_title(st, dedup.title_words(first.title), first.outlet, money, names)
    if len(st["titles"]) != 1:
        problems.append("      remember_title did not store the headline")
    state.forget_titles(st, [dedup.title_words(first.title)])
    if st["titles"]:
        problems.append("      forget_titles left the headline behind after a failed send")

    if problems:
        return [f"{'Cross-outlet dedup':<22} FAILED"] + problems, True
    return [f"{'Cross-outlet dedup':<22} ok       signature + same-outlet + rollback"], False


def main():
    s = Scorer()
    report, failed = [], False

    for stream, fixture, label in (("major_gift", "positives_gifts.txt", "Major Gifts"),
                                   ("watching", "positives_www.txt", "What We're Watching")):
        items = load(fixture)
        if not items:
            continue
        hit = [t for t in items if s.admits_stream(stream, s.score_stream(stream, t))]
        routed = [t for t in items if s.route(t)[0] == stream]
        rec = 100 * len(hit) / len(items)
        rte = 100 * len(routed) / len(items)
        report.append(f"{label:<22} recall {len(hit):>4}/{len(items):<5} {rec:>5.1f}%"
                      f"    routed to this stream {rte:>5.1f}%")
        misses = [t for t in items if not s.admits_stream(stream, s.score_stream(stream, t))]
        for m in misses[:4]:
            report.append(f"      miss: {m[:104]}")

    neg = load("negatives.txt")
    if neg:
        admitted = [(t, s.route(t)) for t in neg]
        bad = [(t, r) for t, r in admitted if r[0] is not None]
        rate = 100 * len(bad) / len(neg)
        report.append(f"{'Negatives':<22} admitted {len(bad):>4}/{len(neg):<5} {rate:>5.1f}%"
                      f"    (lower is better)")
        for t, (stream, v) in bad[:6]:
            report.append(f"      admitted as {stream}: {t[:88]}")
        if rate > 25:
            failed = True

    lines, broke = merge_test()
    report += lines
    failed = failed or broke

    hlines, hbroke = health_test()
    report += hlines
    broke = broke or hbroke
    failed = failed or hbroke

    plines, pbroke = pending_test()
    report += plines
    broke = broke or pbroke
    failed = failed or pbroke

    dlines, dbroke = dedup_test()
    report += dlines
    broke = broke or dbroke
    failed = failed or dbroke

    print("\n".join(report))
    print()
    if broke:
        print("FAIL: state merge or health checking is broken")
        return 1
    if failed:
        print("FAIL: noise rate above 25% — tighten scoring.yaml")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
