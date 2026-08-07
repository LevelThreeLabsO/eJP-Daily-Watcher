#!/usr/bin/env python3
"""
News-feed spine for both Daily Phil sections.

Why feeds instead of a source list: 87% of the organizations eJP names in
"What We're Watching" appear exactly once in 1,082 items, and watching the top
400 orgs would still only touch 48% of them. There is no list to poll. The
editor works by scanning everything and recognizing what matters, so the tool
has to work the same way — wide intake, model judgment.
"""
import json, os, re, html, time, urllib.request, urllib.parse
import concurrent.futures as cf
import datetime as dt

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}

# Verified working (19 probed, 13 returned items). Israeli and haredi outlets are
# in here because they carry the items the previous build structurally could not see.
FEEDS = {
    "Jerusalem Post":     "https://www.jpost.com/rss/rssfeedsheadlines.aspx",
    "JNS":                "https://www.jns.org/feed/",
    "JTA":                "https://www.jta.org/feed",
    "Forward":            "https://forward.com/feed/",
    "Algemeiner":         "https://www.algemeiner.com/feed/",
    "Ynet":               "https://www.ynetnews.com/Integration/StoryRss3082.xml",
    "Matzav":             "https://matzav.com/feed/",
    "Yeshiva World News": "https://www.theyeshivaworld.com/feed",
    "Jewish Press":       "https://www.jewishpress.com/feed/",
    "Yated":              "https://yated.com/feed/",
    "Collive":            "https://collive.com/feed/",
    "Chronicle of Philanthropy": "https://www.philanthropy.com/feed",
    "eJP":                "https://ejewishphilanthropy.com/feed/",   # for dedupe, not intake
}

# Google News standing searches. Vocabulary comes from eJP's own archive, not guesses:
# Major Gifts is 57% general philanthropy, so the net stays wide on the secular side too.
QUERIES = [
    # gifts — Jewish / Israel
    '"million" (gift OR donation OR donates OR pledges) (Jewish OR Israel OR synagogue OR yeshiva)',
    '(NIS OR shekel OR shekels) (million OR donation OR gift) Israel',
    'philanthropist (Jewish OR Israeli) (donates OR pledges OR gives) million',
    'Israeli (hospital OR university OR nonprofit) donation million',
    'haredi OR Hasidic OR Orthodox philanthropist million fund',
    # gifts — general philanthropy (57% of the section)
    '"million gift" (university OR hospital OR museum OR foundation)',
    'philanthropy "largest gift" OR "record gift" million',
    'foundation commits million initiative launch',
    # events / what we're watching
    'Jewish conference OR summit OR "general assembly" OR gala this week',
    'Israel (conference OR summit OR delegation OR mission) begins OR opens',
    'Jewish federation OR JCC OR Hillel (conference OR summit OR retreat)',
]


def _get(url, timeout=25, limit=500_000):
    r = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return f.read(limit).decode("utf8", "replace")


def _strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def _parse_rss(body, source):
    out = []
    for it in re.findall(r"<item>(.*?)</item>", body, re.S) or \
              re.findall(r"<entry>(.*?)</entry>", body, re.S):
        def g(tag):
            m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", it, re.S)
            return _strip(m.group(1)) if m else ""
        title = g("title")
        if not title:
            continue
        link = g("link") or ""
        if not link:
            m = re.search(r'<link[^>]*href="([^"]+)"', it)
            link = m.group(1) if m else ""
        out.append({"title": title, "link": link, "source": source,
                    "published": g("pubDate") or g("updated"),
                    "summary": g("description")[:400]})
    return out


def from_feeds(workers=10):
    def one(kv):
        name, url = kv
        try:
            return _parse_rss(_get(url), name)
        except Exception:
            return []
    out = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(one, FEEDS.items()):
            out.extend(r)
    return out


def from_google(days=1, workers=8):
    def one(q):
        u = ("https://news.google.com/rss/search?"
             + urllib.parse.urlencode({"q": f"{q} when:{days}d", "hl": "en-US",
                                       "gl": "US", "ceid": "US:en"}))
        try:
            items = _parse_rss(_get(u), "")
        except Exception:
            return []
        for it in items:
            m = re.search(r"\s-\s([^-]+)$", it["title"])
            it["source"] = m.group(1).strip() if m else "Google News"
            it["query"] = q
        return items
    out = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(one, QUERIES):
            out.extend(r)
    return out


def norm(t):
    t = re.sub(r"\s*[-–—|]\s*[^-–—|]{2,40}$", "", t)
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def already_published(items):
    """Drop anything eJP has already run — their own feed is the check."""
    try:
        mine = {norm(x["title"]) for x in _parse_rss(_get(FEEDS["eJP"]), "eJP")}
    except Exception:
        mine = set()
    return [x for x in items if norm(x["title"]) not in mine]


def collect(days=1):
    items = from_feeds() + from_google(days=days)
    items = [x for x in items if x["source"] != "eJP"]
    seen, uniq = set(), []
    for x in items:
        k = norm(x["title"])
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(x)
    return already_published(uniq)


if __name__ == "__main__":
    got = collect()
    json.dump(got, open(f"{D}/feed_pool.json", "w"), indent=1)
    import collections
    print(f"candidate pool: {len(got)} unique items")
    print("by source:", dict(collections.Counter(x["source"] for x in got).most_common(12)))
    print("\nsample:")
    for x in got[:12]:
        print(f"  [{x['source'][:18]:<19}] {x['title'][:96]}")
