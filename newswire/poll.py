#!/usr/bin/env python3
"""The eJP newswire: one run.

Reads every source in sources.yaml, routes what scores into one of two Daily Phil
sections, drops what has already been posted or is the same story someone else just
filed, and posts a bundled digest to each section's Slack channel. Single-shot — one
invocation per GitHub Actions tick, fired every 15 minutes by newswire/pinger.gs.

Two streams, unlike the Gulf newswire this is ported from:

    major_gift  did somebody GIVE something? A transaction.
    watching    what should the philanthropy world have its eye on today?

An item lands in exactly one; gifts win ties.

    python3 poll.py                      real run (needs SLACK_GIFTS and SLACK_WWW)
    python3 poll.py --dry-run            print the digests, post nothing, touch no state
    python3 poll.py --dry-run -v --window-hours 3
    python3 poll.py --audit              every source: alive, fresh, parseable
    python3 poll.py --selftest           recall + noise against the real archive
    python3 poll.py --score "headline"   score against both streams, show the routing
    python3 poll.py --baseline-source K  claim a source's items without posting
    python3 poll.py --test-webhook       assert Slack answers `ok`
    python3 poll.py --status             print status.json

Gate order matters — an item rejected at gate 3 is never seen by gate 4:

    1 fetch      every source, threaded (rss, gnews, gnews_entity, html)
    2 fresh      within that source's own window
    3 relevant   url-path genre veto, then score >= the source's threshold
    4 unseen     not already posted, by URL hash and by headline hash
    5 unique     not the story another outlet just filed
    6 post       one bundled message per stream, newest first, capped

Claim-before-send is the load-bearing rule: keys are written to state *before* the post
goes out, and rolled back if Slack refuses. Marking after sending means two overlapping
runs both read an empty store and both send the same digest; not rolling back means a
delivery outage eats the coverage instead of queueing it.

There is deliberately no concurrency lock. The workflow serializes runs, and in the
original two separate outages came from the guard rather than from concurrency — a real
lock wedged permanently when a run was killed while holding it, and its self-expiring
replacement then turned away two scheduled runs.

A live run refuses to start outside GitHub Actions unless --allow-local is passed: the
cloud poller owns the channels and the state file, and two schedulers exchanging state
through git pushes cannot be protected by claim-before-send.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src import dedup, digest, health, judge, pending, postlog, state, status
from src.fetch import Item, ParseFailure, fetch_all, now_utc
from src.score import Scorer
from src.slack_client import DeliveryError, SlackClient

load_dotenv()

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.yaml"
DISPATCH_MINUTES = 15


def load_sources(only: list[str] | None) -> tuple[list[dict], dict]:
    config = yaml.safe_load(SOURCES_FILE.read_text()) or {}
    sources = [s for s in config.get("sources", []) if s.get("enabled", True)]
    if only:
        wanted = set(only)
        sources = [s for s in sources if s["key"] in wanted]
        missing = wanted - {s["key"] for s in sources}
        if missing:
            sys.exit(f"unknown source key(s): {', '.join(sorted(missing))}")
    return sources, config


def check_windows(sources: list[dict]) -> list[str]:
    """A window shorter than the dispatch interval loses items in two ways.

    It leaves gaps (a story published between runs falls outside both windows), and it
    means anything trimmed by the digest cap is gone before the next run can pick it up.
    The cap only holds items over safely because the window outlives the interval.
    """
    return [
        s["key"] for s in sources
        if s.get("window_minutes", 360) <= DISPATCH_MINUTES
    ]


def run(args) -> int:
    scorer = Scorer()
    if scorer.config.get("judge"):
        scorer.judge_hook("", "")  # raises loudly — see score.py

    sources, config = load_sources(args.source)
    max_items = args.max_items or config.get("max_items", digest.MAX_ITEMS)
    default_window = config.get("default_window_minutes", 360)
    now = now_utc()
    run_status = status.Run()
    slack = SlackClient()
    mode = "DRY-RUN" if args.dry_run else "LIVE"

    if not args.dry_run:
        missing = [e for e in ("SLACK_GIFTS", "SLACK_WWW") if not os.environ.get(e, "").strip()]
        if missing:
            sys.exit(f"{' and '.join(missing)} not set. Use --dry-run to inspect "
                     f"without posting.")

    # Each source's own expected cadence, so a monthly funder is not reported dead.
    run_status.slow_sources = {x["key"]: int(x["stale_days"])
                               for x in sources if x.get("stale_days")}

    tight = check_windows(sources)
    if tight:
        print(f"  ! window_minutes <= dispatch interval for: {', '.join(tight)}")

    print(f"[{mode}] {len(sources)} sources, threshold {scorer.default_threshold}, "
          f"cap {max_items}")

    # ---- gate 1 + 2: fetch, within each source's own window -----------------
    candidates: list[Item] = []
    for source, items, entries, exc in fetch_all(sources, now, args.window_hours, default_window):
        key = source["key"]
        if exc is not None:
            if isinstance(exc, ParseFailure):
                run_status.source_parse_fail(key, str(exc))
                print(f"  ! {key:22} PARSE FAILURE: {exc}")
            else:
                run_status.source_error(key, exc)
                print(f"  ! {key:22} {type(exc).__name__}: {exc}")
            continue
        run_status.source_ok(key, len(items), entries)
        print(f"    {key:22} {len(items):3} in window / {entries:3} in feed")
        candidates.extend(items)

    run_status.gate("fetched", len(candidates))

    # ---- reset: forget everything and re-baseline ---------------------------
    # For getting an honest reading of the thing. Clears the claim store and marks
    # whatever is in the feeds right now as already-seen, so from the next tick onward
    # the channel contains only stories published after this moment. Deliberately does
    # NOT merge with origin's state — unioning would resurrect what is being cleared.
    if args.reset and not args.dry_run:
        fresh_state = state.blank()
        keys = [k for item in candidates for k in dedup.keys_for(item)]
        state.claim(fresh_state, keys)
        # Silent. A reset is an operator action, not news, and the channel gets only
        # stories.
        state.record(fresh_state, merge_remote=False)
        run_status.write()
        print(f"Reset — baselined {len(candidates)} items from a clean slate.")
        return 0

    # Introduce one source without dumping its backlog. Required before an `html`
    # source's first live run: those items are undated, so the whole listing reads as
    # new on first sight and would land in the channel at once.
    if args.baseline_source and not args.dry_run:
        wanted = set(args.baseline_source)
        live_state = state.latest()
        keys = [k for item in candidates if item.source_key in wanted
                for k in dedup.keys_for(item)]
        state.claim(live_state, keys)
        state.record(live_state)
        print(f"Baselined {len(keys) // 2} item(s) from {', '.join(sorted(wanted))}; nothing posted.")
        return 0

    # ---- first run: baseline silently rather than dumping the backlog -------
    live_state = state.blank() if args.dry_run else state.latest()
    if not args.dry_run and not state.exists():
        keys = [k for item in candidates for k in dedup.keys_for(item)]
        state.claim(live_state, keys)
        # Silent baseline. The old "watcher is live" marker was another message nobody
        # asked for.
        state.record(live_state)
        run_status.write()
        print(f"First run — baselined {len(candidates)} items, posted hello.")
        return 0

    # ---- gate 2b: undated items age on when we first saw them --------------
    # Some feeds carry no timestamp at all. The freshness gate then never applies to
    # them and they stay eligible forever, reposting endlessly.
    windows = {s["key"]: s.get("window_minutes", default_window) for s in sources}
    fresh: list[Item] = []
    for item in candidates:
        if item.published:
            item.effective_date = item.published
            fresh.append(item)
            continue
        first_seen = state.stamp_first_seen(live_state, dedup.url_key(item))
        item.effective_date = first_seen
        age_minutes = (now - first_seen).total_seconds() / 60
        # Age an undated item on its first sighting, generously — the point is only to
        # stop it living forever, not to race it out of the window on the next tick.
        allowance = max(windows.get(item.source_key, 25), DISPATCH_MINUTES * 2)
        if age_minutes <= allowance:
            fresh.append(item)
    run_status.gate("fresh", len(fresh))

    # ---- gate 3: relevance --------------------------------------------------
    # The URL says things the headline does not. "The cancellation of Macklemore: A Yom
    # Kippur reflection" is indistinguishable from news by its words alone, and is filed
    # at forward.com/OPINION/ — commentary is 0% of both archive sections, and the path
    # is a more reliable signal than guessing the genre from a title.
    url_veto = [p.lower() for p in (scorer.config.get("noise_url_paths") or [])]
    scored: list[Item] = []
    for item in fresh:
        path_hit = next((p for p in url_veto if p in (item.url or "").lower()), None)
        if path_hit:
            if args.dry_run and args.verbose:
                print(f"  · drop [url:{path_hit}] {item.outlet}: {item.title[:66]}")
            continue
        verdict = scorer.score(item.title, item.body, item.author)
        item.score, item.axes = verdict.score, verdict.axes
        if scorer.admits(verdict, item.threshold):
            item.category = scorer.categorize(item.title, item.body)
            scored.append(item)
        elif args.dry_run and args.verbose:
            why = f"veto:{verdict.vetoed}" if verdict.vetoed else f"score {verdict.score}"
            print(f"  · drop [{why}] {item.outlet}: {item.title[:70]}")
    # ---- gate 3b: the judge rescues near-misses -----------------------------
    # Rescue only — it can promote what the scorer rejected, never suppress what the
    # scorer accepted. A model outage therefore costs the rescues and nothing else, and
    # the run degrades to exactly its keyword behaviour. See src/judge.py.
    if judge.available() and not args.no_judge:
        near = judge.candidates(fresh, scorer)
        if near:
            rescued = judge.rescue(near, run_status, run_status.prev)
            for item in near:
                if item.url in rescued:
                    item.category = scorer.categorize(item.title, item.body) or "major_gift"
                    item.axes = list(item.axes) + ["judge"]
                    scored.append(item)

    run_status.gate("relevant", len(scored))

    # ---- gate 4: not already posted ---------------------------------------
    seen = live_state["seen"]
    if args.backfill:
        # Release what a baseline swallowed. A first run claims everything currently in
        # the feeds so the channel doesn't get a backlog dump — but afterwards "claimed"
        # is indistinguishable from "posted", so a re-baseline (or a first run on a busy
        # morning) silently buries real coverage. Backfill ignores the claim store for one
        # run and skips only what we can prove was posted: the remembered headlines of
        # past digests, matched near-identically.
        posted_words = [t.get("words", []) for t in live_state["titles"]]

        def already_shown(item: Item) -> bool:
            words = dedup.title_words(item.title)
            return any(dedup.overlap(words, w) >= 0.8 for w in posted_words)

        unseen = [i for i in scored if not already_shown(i)]
        print(f"  backfill: claim store ignored; skipped "
              f"{len(scored) - len(unseen)} already-posted item(s)")
    else:
        unseen = [i for i in scored if not dedup.already_posted(i, seen)]

    # Within-run dedup. Gate 4 above compares against saved state; it cannot catch the
    # same story arriving twice in ONE run, which happens whenever two queries cover the
    # same outlet — a site-scoped NYT query and an entity query that NYT also matched
    # return the same article under different Google redirect URLs, so the URL hashes
    # differ while the headline hash is identical. That posted "LIV Golf plans mass
    # layoffs" twice in a single digest. Keys are only claimed after the digest is built,
    # so nothing earlier in the pipeline notices.
    deduped: list[Item] = []
    run_keys: set[str] = set()
    for item in unseen:
        keys = dedup.keys_for(item)
        if any(k in run_keys for k in keys):
            continue
        run_keys.update(keys)
        deduped.append(item)
    if len(deduped) < len(unseen):
        print(f"  dropped {len(unseen) - len(deduped)} duplicate(s) within this run")
    unseen = deduped
    run_status.gate("unseen", len(unseen))

    # ---- gate 5: not the same story another outlet just filed -------------
    unique: list[Item] = []
    recent = list(live_state["titles"])
    log = postlog.load()
    for item in sorted(unseen, key=lambda i: i.effective_date or now):
        match = dedup.cross_outlet_match(item, recent) or dedup.signature_match(item, recent)
        if match:
            if args.dry_run and args.verbose:
                print(f"  · dup of {match.get('outlet')}: {item.title[:70]}")
            if not args.dry_run:
                # The suppressed copy is evidence about the story we did post: another
                # newsroom thought it worth filing. The briefing ranks on that.
                postlog.bump_corroboration(
                    log, dedup.title_words(item.title), dedup.overlap, dedup.SIMILARITY)
            continue
        unique.append(item)
        money, names = dedup.signature(item.title)
        recent.append({"words": dedup.title_words(item.title), "at": "",
                       "outlet": item.outlet, "money": sorted(money), "names": sorted(names)})
    run_status.gate("unique", len(unique))

    # ---- gate 6: post ------------------------------------------------------
    if not unique:
        # Save the log even with nothing to post: this run may still have recorded
        # corroboration for stories posted earlier, and that is the signal the briefing
        # ranks on most heavily. Discarding it here threw away evidence of exactly the
        # stories several newsrooms agreed were worth filing.
        if not args.dry_run:
            postlog.save(log)
        print("Nothing to post.")
        return _exit_code(_housekeeping(run_status, slack, live_state, args))

    # Two channels. Circuit posts one digest; eJP has two sections with different
    # tests and different readers, so each stream gets its own bundle and its own
    # webhook. An item is in exactly one — scorer.route() decides, gifts winning ties.
    # ---- gate 5b: hold generic gifts for batched screening ------------------
    # Measured over seven live days: 107 items reached Major Gifts and 87 of them (81%)
    # carried no Jewish or Israel signal at all — a Jesuit school's bequest, a Kiwanis
    # book donation, a trailer given to a veterans museum. All real gifts; none eJP's.
    #
    # The vocabulary fix does not exist: requiring a Jewish word drops gift recall from
    # 81.8% to 34.7%, because 60% of what eJP publishes is general philanthropy that
    # belongs on the strength of who the DONOR is. Only the judge can make that call, and
    # there is not enough quota to call it every run — so these wait in a queue until a
    # batch is worth a call. Gifts tolerate that; "What We're Watching" would not, which
    # is why only gifts are held. See src/pending.py.
    queue = pending.load()
    released: list[Item] = []
    if not args.dry_run and not args.no_judge and judge.available():
        generic = [i for i in unique
                   if getattr(i, "category", None) == "major_gift"
                   and "jewish" not in (getattr(i, "axes", None) or [])]
        if generic:
            held = {i.url for i in generic}
            unique = [i for i in unique if i.url not in held]
            queue = pending.add(queue, generic)
            print(f"  held {len(generic)} generic gift(s); queue now {len(queue)}")

        forced = pending.overdue(queue)
        if forced:
            # Waited past the hard limit. Out they go, screened or not — a gift nobody
            # sees is worse than a gift nobody wanted.
            print(f"  releasing {len(forced)} gift(s) unscreened (held too long)")
            released += pending.to_items(forced, Item)
            queue = pending.remove(queue, {e["url"] for e in forced})

        if pending.is_due(queue):
            batch = pending.to_items(queue, Item)
            verdict = judge.keep(batch, run_status, run_status.prev)
            if verdict is None:
                # No verdict available. Release everything rather than hold it hostage
                # to a model outage: this is exactly the pre-judge behaviour.
                print(f"  no verdict — releasing all {len(batch)} unscreened")
                released += batch
                queue = []
            else:
                kept = [i for i in batch if i.url in verdict]
                dropped = [e for e in queue if e["url"] not in verdict]
                released += kept
                # Claim the rejects so they do not re-enter the queue next run.
                if dropped:
                    state.claim(live_state,
                                [k for e in dropped
                                 for k in dedup.keys_for(pending.to_items([e], Item)[0])])
                queue = []
    pending.save(queue)
    unique = list(unique) + released

    streams = [
        ("major_gift", "Major Gifts", "SLACK_GIFTS"),
        ("watching", "What We're Watching", "SLACK_WWW"),
    ]
    bundles = []
    for stream, label, env in streams:
        items = [i for i in unique if getattr(i, "category", None) == stream]
        if not items:
            continue
        text, included = digest.build(items, now, max_items, heading=label)
        bundles.append((stream, label, env, text, included))

    total = sum(len(b[4]) for b in bundles)
    run_status.gate("posted", total)

    if args.dry_run:
        for stream, label, env, text, included in bundles:
            print("\n" + "-" * 72 + f"\n[{label} -> ${env}]\n" + text + "\n" + "-" * 72)
        print(f"\n[DRY-RUN] would post {total} item(s) across {len(bundles)} channel(s); "
              f"state untouched.")
        return 0

    if not bundles:
        print("Nothing to post.")
        return _exit_code(_housekeeping(run_status, slack, live_state, args))

    # Claim before sending, per bundle, so a failure in one channel does not suppress
    # the other's items on the next tick.
    for stream, label, env, text, included in bundles:
        channel = SlackClient(env)
        claimed = [k for item in included for k in dedup.keys_for(item)]
        remembered = [dedup.title_words(item.title) for item in included]
        sigs = [dedup.signature(item.title) for item in included]
        state.claim(live_state, claimed)
        for item, words, (money, names) in zip(included, remembered, sigs):
            state.remember_title(live_state, words, item.outlet, money, names)
        try:
            channel.post(text)
            print(f"  posted {len(included):2} to {label}")
        except DeliveryError as e:
            state.unclaim(live_state, claimed)
            state.forget_titles(live_state, remembered)
            run_status.delivered = False
            run_status.failed(e)
            print(f"  DELIVERY FAILED for {label}: {e}", file=sys.stderr)

    run_status.delivered = total > 0
    run_status.posted = total
    # Only after Slack confirmed. The log is what any later briefing reads, so an entry
    # in it must mean the desk actually saw the story.
    for stream, label, env, text, included in bundles:
        postlog.record(log, included, dedup.title_words)
    postlog.save(log)
    print(f"Posted {total} item(s) across {len(bundles)} channel(s).")
    return _exit_code(_housekeeping(run_status, slack, live_state, args))


def _exit_code(findings: list) -> int:
    """0 normally; 1 when something is actually broken.

    A non-zero exit turns the workflow run red, and GitHub emails the repo owner about a
    failed run. That is the one alerting channel that needs no setup at all, so it is
    worth using — but only for `critical` findings. Failing the run because the channel
    was quiet overnight would make a red run meaningless within a week, which is why
    health.py grades silence as a warning and never as critical.
    """
    if any(f.severity == "critical" for f in findings):
        print("Exiting non-zero: a critical health finding. See status.json.", file=sys.stderr)
        return 1
    return 0


def _housekeeping(run_status: status.Run, slack: SlackClient, live_state: dict, args) -> list:
    """Persist state and status. Posts NOTHING.

    Posts nothing to the STORY channels, which carry stories and nothing else. That is
    the rule this function exists to enforce: it used to send a silence alarm and a
    dead-feed warning into the live channel, and on 30 August the dead-feed alert fired
    every five minutes for an hour because it had no cooldown.

    Health alerting came back on 15 September, asked for, and under three constraints
    that the old version broke — a separate webhook (SLACK_HEALTH), a cooldown persisted
    in status.json so it survives between runs, and silence treated as a warning rather
    than a failure. With no SLACK_HEALTH set, findings stay in status.json, which is a
    public URL on this repo.
    """
    findings = []
    if not args.dry_run:
        # Before write(), so run_status.alerts_sent can record what went out and be
        # persisted in the same document the cooldown is read from next run.
        findings = health.check(run_status.snapshot())
        findings = health.due(findings, run_status.alerts_sent)
        if findings:
            _send_health(findings, run_status, args)
        run_status.write()
        state.record(live_state, files=("watcher_state.json", "status.json",
                                       "posted_log.json", "pending_gifts.json"))
    else:
        run_status.write()
    doc = status.load()
    print(f"Run took {doc.get('duration_seconds')}s.")
    return findings


def _send_health(findings, run_status, args) -> None:
    """Deliver health findings to SLACK_HEALTH, or say why they went nowhere."""
    for f in findings:
        print(f"  health [{f.severity}] {f.kind}: {f.text[:90]}", file=sys.stderr)
    if not os.environ.get("SLACK_HEALTH", "").strip():
        # Not an error. status.json still records everything, and a critical finding
        # still fails the run, which is what sends GitHub's own email.
        print("  (SLACK_HEALTH not set — findings recorded in status.json only)")
        return
    repo = os.environ.get("GITHUB_REPOSITORY")
    try:
        SlackClient("SLACK_HEALTH").post(health.format(findings, repo))
    except DeliveryError as e:
        # A health alert that cannot be delivered must not take down the run that was
        # otherwise fine. It is already in status.json and on stderr.
        print(f"  ! health alert undeliverable: {e}", file=sys.stderr)
        return
    stamp = status._now()
    for f in findings:
        run_status.alerts_sent[f.kind] = stamp


# --------------------------------------------------------------------- subcommands

def cmd_status() -> int:
    print(status.summarize(status.load()))
    return 0


def cmd_judge_test() -> int:
    """Exercise both judge directions against known headlines.

    The screening path is hard to observe in the wild — it only fires when a batch of
    generic gifts has accumulated — so this drives it directly with four gifts whose
    right answers are known: two that eJP would run (a known Jewish philanthropist, and
    Holocaust-related work) and two it would not (a local service club, a parochial
    school). Needs GEMINI_API_KEY, so in practice it runs in CI.
    """
    from src.fetch import Item

    def mk(title, outlet):
        return Item(source_key="test", outlet=outlet, title=title, url=f"https://x/{hash(title)}",
                    published=now_utc(), category="major_gift")

    should_keep = [
        mk("Michael Bloomberg gives $600 million to four historically Black medical schools", "AP"),
        mk("Anonymous donor gives $12 million to Holocaust museum for education wing", "Reuters"),
    ]
    should_drop = [
        mk("Lawrenceburg Kiwanis Donates 1,000 Books to Help Young Hospital Patients", "Main Street"),
        mk("Jesuit High School Receives Record $10 Million Bequest From Alumnus", "tbbwmag"),
    ]
    batch = should_keep + should_drop
    run_status = status.Run()
    print(f"calls left today: {judge.calls_left(run_status.prev)}")
    verdict = judge.keep(batch, run_status, run_status.prev)
    if verdict is None:
        print("NO VERDICT — the caller would release all four unscreened (correct failure mode).")
        return 0
    print()
    for i in batch:
        want = "keep" if i in should_keep else "drop"
        got = "keep" if i.url in verdict else "drop"
        mark = "ok  " if want == got else "MISS"
        print(f"  {mark} want={want} got={got}  {i.title[:66]}")
    hits = sum((i.url in verdict) == (i in should_keep) for i in batch)
    print(f"\n{hits}/4 as expected.")
    return 0


def cmd_score(text: str) -> int:
    """Score one headline against BOTH streams and show where it would land.

    This used to call scorer.score()/admits(), the single-stream methods inherited from
    the Gulf newswire, and so reported a verdict no live run would ever reach: it printed
    ADMIT for a headline scoring 2 against a threshold of 4, because the real decision is
    per stream and this was reading neither of them. Tuning is done by reading this
    output, so it has to be the same decision poll.py makes.
    """
    scorer = Scorer()
    for stream, label in (("major_gift", "Major Gifts"), ("watching", "What We're Watching")):
        v = scorer.score_stream(stream, text)
        admits = scorer.admits_stream(stream, v)
        print(f"{label:<22} score {v.score} / threshold {scorer.thresholds[stream]}"
              f"  -> {'ADMIT' if admits else 'drop'}")
        print(f"  axes     {', '.join(v.axes) or '(none)'}")
        print(f"  matched  {', '.join(v.matched) or '(none)'}")
        anchor = scorer.anchors[stream]
        if anchor and not (anchor & set(v.title_axes)):
            print(f"  no anchor: the headline needs one of {sorted(anchor)}")
        if v.vetoed:
            print(f"  vetoed   {v.vetoed}")
    stream, _ = scorer.route(text)
    print(f"\nroutes to: {stream or 'nothing — this headline would not post'}")
    return 0


def cmd_test_webhook() -> int:
    slack = SlackClient()
    try:
        slack.post(":wrench: eJP newswire webhook test — delivery confirmed.")
    except DeliveryError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    print("Slack accepted the message (`ok`).")
    return 0


def cmd_selftest() -> int:
    from tests.selftest import main as selftest_main
    return selftest_main()


def cmd_audit(args) -> int:
    from audit_sources import main as audit_main
    return audit_main(args.source)


def main() -> int:
    p = argparse.ArgumentParser(description="The eJP newswire — Major Gifts and What We're Watching")
    p.add_argument("--dry-run", action="store_true", help="print, don't post; don't touch state")
    p.add_argument("--verbose", "-v", action="store_true", help="show what was dropped and why")
    p.add_argument("--window-hours", type=float, default=None, help="override every source's window")
    p.add_argument("--source", action="append", help="limit to source key(s); repeatable")
    p.add_argument("--max-items", type=int, default=None, help="override the digest cap")
    p.add_argument("--reset", action="store_true",
                   help="clear state and re-baseline: only stories newer than now will post")
    p.add_argument("--backfill", action="store_true",
                   help="post what a baseline claimed but never showed (see gate 4)")
    p.add_argument("--status", action="store_true", help="print status.json and exit")
    p.add_argument("--score", metavar="HEADLINE", help="score one headline and exit")
    p.add_argument("--test-webhook", action="store_true", help="post a test message and exit")
    p.add_argument("--selftest", action="store_true", help="scoring recall/noise fixtures")
    p.add_argument("--audit", action="store_true", help="audit every source's feed health")
    p.add_argument("--baseline-source", action="append", metavar="KEY",
                   help="claim a source's current items without posting; run this "
                        "before an html source goes live")
    p.add_argument("--judge-test", action="store_true",
                   help="run both judge directions against known headlines")
    p.add_argument("--no-judge", action="store_true",
                   help="skip the Gemini rescue pass (keyword scoring only)")
    p.add_argument("--allow-local", action="store_true",
                   help="permit a live posting run outside GitHub Actions (see the guard below)")
    args = p.parse_args()

    if args.status:
        return cmd_status()
    if args.score:
        return cmd_score(args.score)
    if args.test_webhook:
        return cmd_test_webhook()
    if args.selftest:
        return cmd_selftest()
    if args.judge_test:
        return cmd_judge_test()
    if args.audit:
        return cmd_audit(args)

    # One poller, ever. Claim-before-send protects two overlapping runs of the same
    # poller; it cannot protect two schedulers that exchange state through git pushes
    # landing seconds apart, which is how a laptop run and a cloud run posted the same
    # digest twice ninety seconds apart. Verification from a laptop uses --dry-run; this
    # refuses anything that would post or write state.
    if not (args.dry_run or args.allow_local) and not os.environ.get("GITHUB_ACTIONS"):
        print("Refusing a live run outside GitHub Actions: the cloud poller owns the\n"
              "channels and the state file. Use --dry-run to see what would post, or\n"
              "--allow-local if you genuinely mean to post from here.", file=sys.stderr)
        return 2

    # Any unhandled failure must still leave a status record, or the file keeps showing
    # the last good run and the watcher looks healthy while it is broken — the precise
    # failure this project exists to avoid. Only DeliveryError was covered before.
    try:
        return run(args)
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 — recorded, then re-raised untouched
        try:
            failed = status.Run()
            failed.failed(e)
            failed.write()
            if not args.dry_run:
                state.record(state.load())
        except Exception as inner:  # noqa: BLE001
            print(f"  ! could not record failure: {inner}", file=sys.stderr)
        raise


if __name__ == "__main__":
    sys.exit(main())
