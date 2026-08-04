# Daily Phil watchers

Two feeds into Slack for the Daily Phil, built on the same pattern as the other JI
watchers (GitHub Actions + incoming webhook + Gemini free tier).

## Status

| | state |
|---|---|
| **Major Gifts** | working end-to-end. Needs `SLACK_WEBHOOK_GIFTS`. |
| **What We're Watching** | plumbing done, blocked on `GEMINI_API_KEY` (see below). |

## Major Gifts

34 standing Google News searches replacing the editor's three manual ones. Free,
no key, no scraping. Typical day: ~208 stories in, ~37 out.

Filters, in order: gift language required; investment / construction / revenue /
campaign-total / political money rejected; non-USD currencies rejected; $1M floor.
Survivors are bucketed, with gifts involving one of 240 people drawn from eJP's own
archive ranked first.

    python3 src/gifts.py --days=1

## What We're Watching

107 curated sources — national convenings plus museums, film festivals, Yiddish
institutions and music festivals for the offbeat items. Local federation and JCC
calendars are deliberately excluded; they are overwhelmingly baby yoga and mahjong.

Three extractors run per page: schema.org `Event` markup, iCalendar feeds, then a
regex fallback.

**The finding that matters:** only 24 of 218 extracted events were machine-published,
and just 2 of those were actual convenings. Structured data turns out to exist where
the events aren't newsworthy (federation WordPress plugins) and to be absent where
they are (conference sites). The regex fallback is not salvageable — it returned
"Confirm that you are not a bot" and a list of Connecticut towns as event titles.

So this section needs `src/llm.py::extract_events` to be the primary path, not a
fallback. It is written and ready; it has not been run, because there is no Gemini
key in this repo yet.

    python3 src/events.py

## Deploy

1. Two Slack channels, one incoming webhook each.
2. Repo secrets: `SLACK_WEBHOOK_GIFTS`, `SLACK_WEBHOOK_EVENTS`, `GEMINI_API_KEY`.
3. `.github/workflows/daily.yml` runs both weekdays at 09:15 UTC (~5:15am ET).

## Files

    src/gifts.py            Google News → filters → buckets
    src/events.py           calendars → 3 extractors → newsworthiness
    src/sources_events.py   the 107 curated sources
    src/discover_events.py  finds each source's calendar page
    src/llm.py              Gemini: event extraction + donor identification
    src/slack_client.py     webhook + digest formatting
