# ejp-newswire

Watches the publications eJewishPhilanthropy actually reports from, keeps the small
fraction that is a **major gift** or an **event happening today**, and posts each stream
to its own Slack channel every 15 minutes. Runs on GitHub Actions; does not depend on
any laptop being awake.

A port of the Circuit newswire, which is itself a port of the JI Newswire. Two things
changed: the source list, and the scoring vocabulary. Both are data files. The one
structural change is that this routes into **two** streams rather than one beat.

## The two streams

|  | test | threshold | channel |
|---|---|---|---|
| **Major Gifts** | did somebody GIVE something? | 4 | `SLACK_GIFTS` |
| **What We're Watching** | did somebody CONVENE something happening today? | 5 | `SLACK_WWW` |

An item lands in at most one. Gifts win ties — a gala that raises money is a gift story
with an event attached, and that is where the desk expects it.

## How it decides

Additive keyword scoring, no AI. The weights come from eJP's own archive — 1,102
published "What We're Watching" items and 435 "Major Gifts" items, Jan 2021 to Sep 2026 —
not from instinct.

    Major Gifts        giving 3 · recipient 2 · gift form 2 · Jewish 2 · money 1
    What We're Watching  convening 3 · today 2 · Jewish 2 · scale 1

Two numbers carry the design:

**The Jewish axis is worth 2 and cannot admit a story alone.** 57% of the real Major
Gifts section is general philanthropy with no Jewish angle at all — a Gates Foundation
grant, a Boston Ballet endowment, a $6M gift to a Lompoc theatre. A filter requiring a
Jewish angle would delete more than half the section.

**The events threshold is 5, and convening + today is exactly 5.** So a scheduled event
happening today clears and general news about the same institutions does not. The
previous collector had no such gate and sent troop deployments and a soccer crowd's Nazi
salute to the events channel — a class that is 0 of 1,102 real items.

There is **no dollar floor anywhere.** 18% of real gift items carry no figure at all:
donated medical supplies, a donated artwork, donated organs, "an undisclosed sum". Money
is worth one point, never a requirement. An earlier version required "million" in every
search and was blind to "Rice receives major gift from Krafts".

## Measured

`python3 tests/selftest.py` scores the vocabulary against eJP's own published items and
against real headlines from the same feeds that eJP did not run:

    Major Gifts            recall  354/429    82.5%
    What We're Watching    recall  768/1085   70.8%
    Negatives              admitted 109/600   18.2%

Run it after any edit to `scoring.yaml`. A word you add shows its cost as well as its
benefit. Some "negatives" are genuinely borderline — items eJP simply had not published
yet — so the noise figure is a ceiling, not a verdict.

## Scheduling

`pinger.gs` — an Apps Script time-driven trigger on Google's servers, firing
`workflow_dispatch` every 15 minutes. **This is not optional.** GitHub's own cron was
measured on the predecessor repo dropping entire days and once firing 11.6 hours late;
the `schedule` block in `poll.yml` is an hourly fallback, nothing more.

Setup is one click: open the Apps Script project, select `setup`, press Run.
The token lives in Script Properties as `GH_TOKEN`, never in the file.

## Running it

    python3 poll.py                  real run (needs SLACK_GIFTS and SLACK_WWW)
    python3 poll.py --dry-run        print both digests, post nothing, touch no state
    python3 poll.py --audit          every source: alive, fresh, parseable
    python3 poll.py --score "text"   score one headline, see which axes hit and where it routes
    python3 tests/selftest.py        recall and noise against the archive

## Files

    poll.py           one run: fetch, freshness, relevance, dedup, post
    sources.yaml      61 sources — Jewish wires, haredi outlets, philanthropy trade,
                      local Jewish papers, diaspora, and 24 standing searches
    scoring.yaml      the two vocabularies; edit this, not the code
    src/score.py      two-stream scorer and the routing rule
    tests/selftest.py recall and noise, measured against eJP's archive
    pinger.gs         the Apps Script trigger that actually makes it run
