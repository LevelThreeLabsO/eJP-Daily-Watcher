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
    "Philanthropy News Digest": "https://philanthropynewsdigest.org/feeds/rss/news",
    "Nonprofit Quarterly":      "https://nonprofitquarterly.org/feed/",
    "Devex":                    "https://www.devex.com/news.rss",
    "Times of Israel":    "https://www.timesofisrael.com/feed/",
    "Haaretz":            "https://www.haaretz.com/cmlink/1.4605102",
    "Jewish Chronicle UK":"https://www.thejc.com/rss",
    "Jewish Insider":     "https://jewishinsider.com/feed/",
    # Local and regional Jewish papers. These are where "a JCC hosts a speaker",
    # "the federation runs a program", "the film festival opens" actually get
    # covered — 8 of last week's 11 misses were this kind of item, and the general
    # news feeds never carry them.
    "Washington Jewish Week":  "https://www.washingtonjewishweek.com/feed/",
    "Jewish Journal LA":       "https://jewishjournal.com/feed/",
    "Cleveland Jewish News":   "https://www.clevelandjewishnews.com/search/?f=rss&t=article&l=50&s=start_time&sd=desc",
    "Baltimore Jewish Times":  "https://www.jewishtimes.com/feed/",
    "Canadian Jewish News":    "https://thecjn.ca/feed/",
    "Jewish Exponent":         "https://www.jewishexponent.com/feed/",
    "Atlanta Jewish Times":    "https://www.atlantajewishtimes.com/feed/",
    "J. Bay Area":             "https://jweekly.com/feed/",
    "New York Jewish Week":    "https://www.jta.org/category/ny/feed",
    "Boulder Jewish News":     "https://boulderjewishnews.org/feed/",
    "Jewish Link":             "https://jewishlink.news/feed/",
    # Diaspora. Rachel scans Europe, South America and Australia explicitly, and the
    # archive backs her: diaspora-only items are 6.2% of Major Gifts and 3.3% of WWW.
    "Australian Jewish News":  "https://ajn.timesofisrael.com/feed/",
    "J-Wire Australia":        "https://www.jwire.com.au/feed/",
    "European Jewish Congress":"https://eurojewcong.org/feed/",
    "Jewish News UK":          "https://www.jewishnews.co.uk/feed/",
    "SA Jewish Report":        "https://www.sajr.co.za/feed/",
    # South America — Spanish-language. The archive's Latin American items
    # (Venezuela earthquake campaign, Buenos Aires commemorations) surface here first.
    "Enlace Judio (MX)":       "https://www.enlacejudio.com/feed/",
    "Agencia AJN (AR)":        "https://agenciaajn.com/feed/",
    "eJP":                "https://ejewishphilanthropy.com/feed/",   # for dedupe, not intake
}

# Google News standing searches. Vocabulary comes from eJP's own archive, not guesses:
# Major Gifts is 57% general philanthropy, so the net stays wide on the secular side too.
QUERIES = [
    # Gifts. Deliberately NOT keyed on "million" — every query used to require that
    # word, which made "Rice receives major gift from Krafts" and "Embassy Donates
    # Medical Supplies" invisible. Search for the ACT of giving; let the model judge.
    'philanthropy gift donation',
    '"major gift" OR "largest gift" OR "record gift"',
    '(donates OR donated OR gifts OR gifted) to (university OR hospital OR museum OR college)',
    'foundation (grant OR grants OR awards OR commits) nonprofit',
    '(pledged OR pledges OR commits) (to build OR to launch OR to fund)',
    'philanthropist (gives OR donates OR pledges)',
    '"relief fund" OR "emergency fund" launched donations',
    'donor (gives OR gave OR donates) center OR institute OR program',
    'bequest OR estate gift OR "planned giving" million',
    # Jewish / Israel — same principle, no amount required
    'Jewish (donation OR gift OR grant OR philanthropy OR endowment)',
    'Israel (donation OR gift OR grant OR pledge) nonprofit OR hospital OR university',
    '(NIS OR shekel OR shekels) (donation OR gift OR grant OR pledge)',
    'haredi OR Hasidic OR Orthodox (philanthropist OR donor OR fund)',
    'embassy OR bank OR corporation donates (supplies OR equipment OR services)',
    'federation OR JCC OR Hillel OR yeshiva (gift OR grant OR donation)',
    # Events happening now
    'Jewish (conference OR summit OR "general assembly" OR gala OR retreat) opens OR begins OR concludes',
    'Israel (conference OR summit OR delegation OR mission OR ceremony) begins OR opens OR concludes',
    '(Jewish OR Israeli) organization (announces OR launches OR unveils) program OR initiative',
    # Event-shaped, not transaction-shaped. Last week 8 of 11 misses were a local
    # institution hosting something; none of the queries above would ever find them.
    '(JCC OR "Jewish Community Center" OR federation OR synagogue) (hosts OR hosting OR presents OR welcomes)',
    '"Jewish film festival" OR "Jewish book festival" OR "Jewish music festival"',
    '(Jewish OR Israeli) author OR speaker OR survivor (speaks OR speaking OR appears) event',
    'former hostage OR lone soldier (speaks OR event OR community)',
    '"Jewish community" (event OR program OR series OR launch) this week',
    'rabbi OR rabbinic (conference OR convention OR gathering OR mission)',
    'Israeli (film OR play OR exhibit OR documentary) premiere OR opening OR screening',
    'city council OR state legislature (resolution OR vote) Israel OR Gaza OR antisemitism',
    'Hillel OR Chabad OR Birthright (scholarship OR award OR program OR launch)',
    'synagogue OR temple (bequest OR endowment OR legacy gift OR estate)',

    # --- Rachel's own Google searches, verbatim. She runs these daily, news only,
    # past 24 hours, then screens the donor names by hand.
    '"Jewish donor"',
    '"Jewish philanthropist"',
    '"Jewish community" endowment',
    'donor Jewish gift',

    # --- Gift categories she names that the archive confirms and I was not querying:
    # naming announcements 8%, research awards/chairs 15%, capital projects 19%.
    '(named for OR "will bear the name" OR "naming gift" OR renamed) (center OR building OR wing OR school OR institute)',
    '(endowed OR endowment) (chair OR professorship OR fund OR scholarship)',
    '"research award" OR "research grant" OR "endowed chair" OR professorship university',
    '(new OR renovated) (wing OR pavilion OR building OR campus OR center) (gift OR donor OR donated)',
    'crowdfunding campaign raised (community OR synagogue OR family)',

    # --- Diaspora, by region. Rachel asks for national or international interest in
    # Jewish communities across the US, Europe, South America and Australia.
    'Jewish community (Europe OR London OR Paris OR Berlin OR Vienna OR Budapest) event OR gift OR opening',
    'Jewish community (Australia OR Sydney OR Melbourne) event OR donation OR conference',
    'Jewish community (Argentina OR Brazil OR Mexico OR Chile OR "Buenos Aires") event OR donation',
    'Jewish community Canada (Toronto OR Montreal OR Vancouver) event OR gift OR synagogue',
    'Jewish (federation OR community) (South Africa OR Johannesburg OR India OR Morocco)',
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


def from_google(days=2, workers=10):
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


def collect(days=2):
    items = from_feeds() + from_google(days=days)
    items = [x for x in items if x["source"] != "eJP"]
    seen, uniq = set(), []
    for x in items:
        k = norm(x["title"])
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(x)
    return prefilter(already_published(uniq))


# ---------------------------------------------------------------------------
# Deterministic pre-filter. Every pattern below was tested against eJP's full
# archive of 1,096 WWW items and 428 gift items; none removes more than 1% of
# real published items. Anything stricter belongs in the model, not here.
# ---------------------------------------------------------------------------
HARD_DROP = re.compile(
    r"\b("
    r"arrested|indicted|sentenced|convicted|manhunt|shooting|stabbing|assault(?:ed|s)?|"
    r"murder\w*|homicide|robbery|burglar\w*|"                     # crime
    r"air ?strike|missiles? (?:hit|struck|fired)|rocket fire|troops? (?:enter|deploy)|"
    r"casualt\w+|wounded|killed in|death toll|"                    # combat reporting
    r"swastika|nazi salute|vandaliz\w+|defac\w+|graffiti|"          # incidents — 0% of archive
    r"box ?score|final score|defeats?|beat the|playoff|touchdown|"  # sport
    r"weather forecast|storm warning|earthquake struck"             # raw weather/disaster
    r")\b", re.I)

OPINION = re.compile(
    r"\b(opinion|op-?ed|analysis|commentary|editorial|column|"
    r"slams?|blasts?|accuses?|condemns?|denounces?|criticiz\w+|hits? back|"
    r"why (?:we|i|you) |what .{0,20} gets wrong)\b", re.I)


# A convened event beats the hard-drop list. eJP runs memorial services for people
# who were killed, ceremonies after attacks, conferences on security — the subject
# matter is grim but the ITEM is a scheduled gathering. Without this exemption the
# filter removes 2.2% of real published items; with it, 0.5%.
CONVENED = re.compile(
    r"\b(memorial service|memorial ceremony|ceremony|ceremonies|will hold|is holding|will host|"
    r"is hosting|conference|summit|convention|gala|benefit|retreat|convening|assembly|vigil|"
    r"commemorat\w+|service (?:for|honoring)|tribute|dedication|opening night|festival|"
    r"kicks? off|concludes?|underway|honor(?:s|ing|ed)|marks? the|"
    r"march on|rally|state visit|official visit|delegation|mission to|"
    r"will (?:be held|deliver|be at|present|address|speak)|is on a |has dispatched|"
    r"dispatch\w+|marks? \w+ years|anniversary|sentencing|victim impact|"
    r"keeping an eye|we.re monitoring|we.re (?:also )?(?:watching|tracking))\b", re.I)


def prefilter(items):
    """Drop what the archive shows never runs, before spending model tokens."""
    out = []
    for x in items:
        blob = f"{x['title']} {x.get('summary','')[:160]}"
        if CONVENED.search(blob):
            out.append(x)                 # a scheduled gathering always survives
            continue
        if HARD_DROP.search(blob):
            continue
        if OPINION.search(x["title"]):
            continue
        out.append(x)
    return out


if __name__ == "__main__":
    got = collect()
    json.dump(got, open(f"{D}/feed_pool.json", "w"), indent=1)
    import collections
    print(f"candidate pool: {len(got)} unique items")
    print("by source:", dict(collections.Counter(x["source"] for x in got).most_common(12)))
    print("\nsample:")
    for x in got[:12]:
        print(f"  [{x['source'][:18]:<19}] {x['title'][:96]}")
