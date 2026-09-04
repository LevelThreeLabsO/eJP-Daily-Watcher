#!/usr/bin/env python3
"""Scoring recall and noise, measured against eJP's own published archive.

Positives are real published items (429 Major Gifts, 1,085 What We're Watching).
Negatives are real headlines from the same feeds that eJP did not run.

Recall is the number that matters: a word removed from scoring.yaml shows up here as
items the desk would no longer see. Noise is the cost side — some negatives are
genuinely borderline, so this reports the rate rather than demanding zero.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.score import Scorer

HERE = os.path.dirname(os.path.abspath(__file__))


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

    print("\n".join(report))
    print()
    if failed:
        print("FAIL: noise rate above 25% — tighten scoring.yaml")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
