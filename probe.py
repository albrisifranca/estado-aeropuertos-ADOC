import json, urllib.request, datetime
UA = "estado-aeropuertos/1.0"
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=60) as r: return r.status, r.read()
def show(name, u, n=2500):
    try:
        st, raw = get(u); print("=====", name, st, len(raw)); print(raw[:n].decode("utf-8","replace"))
        return raw
    except Exception as e: print("=====", name, "ERR", e)
r = show("isigmet", "https://aviationweather.gov/api/data/isigmet?format=json", 1500)
if r:
    d = json.loads(r); print("count", len(d)); import collections; print(collections.Counter(x.get("hazard") for x in d)); print(list(d[0].keys()))
    for x in d:
        if x.get("hazard") in ("VA","TC"): print(json.dumps(x)[:800])
r = show("airsigmet", "https://aviationweather.gov/api/data/airsigmet?format=json", 600)
if r:
    d = json.loads(r); import collections; print("count", len(d), collections.Counter(x.get("hazard") for x in d), list(d[0].keys()) if d else None)
show("usgs", "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson", 1500)
show("gdacs list", "https://www.gdacs.org/gdacsapi/api/events/geteventlist/MAP", 3000)
show("gdacs rss", "https://www.gdacs.org/xml/rss.xml", 3000)
