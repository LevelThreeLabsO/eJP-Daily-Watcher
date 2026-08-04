#!/usr/bin/env python3
"""Find the calendar/events page on each curated source."""
import json, os, re, urllib.request, urllib.parse, concurrent.futures as cf, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sources_events import ALL
D = os.path.expanduser("~/ji-dailyphil-watcher/data")
UA = {"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36","Accept-Language":"en-US,en;q=0.9"}
PATS = [r"event", r"calendar", r"conference", r"convention", r"summit", r"programs?/?$",
        r"whats-?on", r"upcoming", r"festival", r"exhibitions?", r"performances?",
        r"biennial", r"convening", r"gathering", r"kinus", r"assembly"]
def get(url, t=20):
    r = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(r, timeout=t) as f:
        return f.read(500_000).decode("utf8","replace"), f.geturl()
def find(item):
    name, domain, kind = item
    body = base = None
    for pre in ("https://www.","https://"):
        try: body, base = get(pre+domain); break
        except Exception: continue
    if not body: return {"org":name,"domain":domain,"kind":kind,"ok":False}
    host = urllib.parse.urlparse(base).netloc.replace("www.","")
    cands = []
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', body, re.I|re.S):
        u = urllib.parse.urljoin(base, m.group(1))
        if urllib.parse.urlparse(u).netloc.replace("www.","") != host: continue
        if re.search(r"\.(pdf|jpe?g|png|gif|zip|mp4|docx?)$", u, re.I): continue
        text = re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",m.group(2))).strip().lower()[:50]
        path = urllib.parse.urlparse(u).path.lower()
        score = 0
        for i,p in enumerate(PATS):
            if re.search(p, path): score += 10-i*0.2
            if re.search(p, text): score += 6-i*0.2
        if score: cands.append((score, u.rstrip("/"), text))
    if not cands: return {"org":name,"domain":domain,"kind":kind,"ok":True,"url":None}
    cands.sort(reverse=True)
    return {"org":name,"domain":domain,"kind":kind,"ok":True,"url":cands[0][1],
            "anchor":cands[0][2],"alts":[c[1] for c in cands[1:4]]}
with cf.ThreadPoolExecutor(max_workers=12) as ex:
    res = list(ex.map(find, ALL))
json.dump(res, open(f"{D}/event_sources.json","w"), indent=1)
ok = [r for r in res if r.get("ok") and r.get("url")]
print(f"sources: {len(ALL)} | reachable: {sum(1 for r in res if r.get('ok'))} | calendar page found: {len(ok)}")
print("\nunreachable:", [r["domain"] for r in res if not r.get("ok")])
print("no calendar page:", [r["domain"] for r in res if r.get("ok") and not r.get("url")])
print("\nsample:")
for r in ok[:12]: print(f"  {r['org'][:34]:<36} {r['url'][:74]}")
