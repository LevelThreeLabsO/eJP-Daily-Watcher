#!/usr/bin/env python3
"""Pull every Your Daily Phil edition and extract the two sections we replicate."""
import json, os, re, html, time, urllib.request, urllib.parse
D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UA = {"User-Agent": "ejp-dailyphil-watcher/1.0 (sruli@jewishinsider.com)"}
BASE = "https://ejewishphilanthropy.com/wp-json/wp/v2/posts"

def get(url):
    for a in range(4):
        try:
            r = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(r, timeout=90) as f:
                return json.loads(f.read()), {k.lower(): v for k, v in f.headers.items()}
        except Exception:
            if a == 3: raise
            time.sleep(2 * (a + 1))

def pull_all():
    posts, page = [], 1
    while True:
        b, h = get(BASE + "?" + urllib.parse.urlencode({
            "categories": 843, "per_page": 100, "page": page,
            "orderby": "date", "order": "desc",
            "_fields": "id,date,link,content"}))
        if not b: break
        posts += b
        total = int(h.get("x-wp-total", 0))
        print(f"  page {page}: {len(posts)}/{total}", flush=True)
        if len(posts) >= total: break
        page += 1
        time.sleep(0.3)
    return posts

TAG = re.compile(r"<[^>]+>")
def section(raw, pat):
    m = re.search(r'<h[23][^>]*>\s*' + pat + r'\s*</h[23]>(.*?)(?=<h[23]\b)', raw, re.S | re.I)
    if not m: return []
    out = []
    for para in re.findall(r"<p[^>]*>(.*?)</p>", m.group(1), re.S):
        t = re.sub(r"\s+", " ", html.unescape(TAG.sub("", para)).replace("\xa0", " ")).strip()
        if len(t) > 55 and not t.lower().startswith(("read more", "sign up", "subscribe")):
            out.append(t)
    return out

if __name__ == "__main__":
    posts = pull_all()
    gold = {"www": [], "gifts": []}
    for p in posts:
        raw, d = p["content"]["rendered"], p["date"][:10]
        for t in section(raw, r"What\s+We[’'&#8217;]*\s*re\s+Watching"):
            gold["www"].append({"date": d, "text": t, "link": p["link"]})
        for t in section(raw, r"Major\s+Gifts"):
            gold["gifts"].append({"date": d, "text": t, "link": p["link"]})
    json.dump(gold, open(f"{D}/ydp_goldset.json", "w"), indent=1)
    for k, lab in [("www", "What We're Watching"), ("gifts", "Major Gifts")]:
        it = gold[k]; eds = len(set(x["date"] for x in it))
        yrs = sorted({x["date"][:4] for x in it})
        print(f"\n{lab}: {len(it)} items / {eds} editions ({yrs[0]}–{yrs[-1]}), "
              f"{len(it)/max(eds,1):.1f} per edition")
    print(f"\ntotal editions pulled: {len(posts)}  ({posts[-1]['date'][:10]} → {posts[0]['date'][:10]})")
