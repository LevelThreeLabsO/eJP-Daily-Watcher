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
from src import state

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

    print("\n".join(report))
    print()
    if broke:
        print("FAIL: state merge is broken — dedup keys will be lost on every push race")
        return 1
    if failed:
        print("FAIL: noise rate above 25% — tighten scoring.yaml")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
