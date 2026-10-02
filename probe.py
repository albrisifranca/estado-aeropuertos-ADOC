import json, re, urllib.request, urllib.parse, http.cookiejar, html
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
def get(url, data=None, headers=None, opener=None):
    h = {"User-Agent": UA}; h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with (opener or urllib.request.build_opener()).open(req, timeout=60) as r:
        return r.status, r.read()
print("=== EASA json")
try:
    st, raw = get("https://www.easa.europa.eu/en/domains/air-operations/czibs/export-json?page&_format=json")
    d = json.loads(raw); print(st, type(d), len(d))
    print(json.dumps(d[:2] if isinstance(d, list) else d, ensure_ascii=False)[:3000])
    if isinstance(d, list):
        for x in d: print(" -", json.dumps(x, ensure_ascii=False)[:400])
except Exception as e: print("ERR", e)
print("=== FAA text")
try:
    st, raw = get("https://www.faa.gov/air_traffic/publications/us_restrictions")
    t = raw.decode("utf-8","replace"); b = t[t.find("<main"):] if "<main" in t else t
    txt = html.unescape(re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",re.sub(r"<script.*?</script>|<style.*?</style>","",b,flags=re.S))))
    print(txt[:7000])
except Exception as e: print("ERR", e)
print("=== NOTAM with cookies")
try:
    cj = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    st, raw = get("https://notams.aim.faa.gov/notamSearch/nsapp.html", opener=op); print("page", st, len(raw), [c.name for c in cj])
    body = urllib.parse.urlencode({"searchType": 0, "designatorsForLocation": "UKBB", "notamsOnly": "false", "offset": 0}).encode()
    st, raw = get("https://notams.aim.faa.gov/notamSearch/search", body, {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "Origin": "https://notams.aim.faa.gov", "Referer": "https://notams.aim.faa.gov/notamSearch/nsapp.html", "X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01"}, op)
    print(st, raw[:1500])
except Exception as e: print("ERR", e, getattr(e, "read", lambda: b"")()[:500])
