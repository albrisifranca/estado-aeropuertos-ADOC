import json, time, urllib.request
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36", "Accept": "application/json,text/html,*/*"}
def t(name, url, data=None, h=None):
    try:
        req = urllib.request.Request(url, data=data, headers={**UA, **(h or {})})
        with urllib.request.urlopen(req, timeout=30) as r:
            b = r.read()
            print(f"== {name}: {r.status} {len(b)}B"); print(b[:900].decode("utf8", "replace")); return b
    except Exception as e:
        body = getattr(e, "read", lambda: b"")()[:300]
        print(f"== {name}: ERROR {e} {body!r}")
now = int(time.time())
b = t("fr24 schedule", "https://api.flightradar24.com/common/v1/airport.json?code=mia&plugin[]=schedule&plugin-setting[schedule][mode]=departures&page=1&limit=100")
if b:
    try:
        d = json.loads(b); s = d["result"]["response"]["airport"]["pluginData"]["schedule"]["departures"]
        print("fr24 deps", s["item"], [ (x["flight"]["airline"]["name"] if x["flight"].get("airline") else None, x["flight"]["airport"]["destination"]["code"]["iata"]) for x in s["data"][:15]])
    except Exception as e: print("parse", e)
t("fr24 routes page", "https://www.flightradar24.com/data/airports/mia/routes")
t("fr24 airline routes", "https://www.flightradar24.com/data/airlines/aa-aal/routes")
for d in (2, 4):
    end = (now // 86400 - d) * 86400 + 86400; beg = end - 86400 // 2
    b = t(f"opensky dep -{d}d", f"https://opensky-network.org/api/flights/departure?airport=KMIA&begin={end-7200}&end={end}")
b = t("opensky states", "https://opensky-network.org/api/states/all?lamin=25.7&lomin=-80.4&lamax=25.9&lomax=-80.2")
t("adsb.lol routeset", "https://api.adsb.lol/api/0/routeset", json.dumps({"planes": [{"callsign": "AAL926", "lat": 25.8, "lng": -80.3}, {"callsign": "GTI8301", "lat": 25.8, "lng": -80.3}]}).encode(), {"Content-Type": "application/json"})
t("adsbdb callsign", "https://api.adsbdb.com/v0/callsign/AAL926")
t("aa route map", "https://www.aa.com/i18n/travel-info/destinations.jsp")
t("aviationstack-free?", "https://www.flightaware.com/live/airport/KMIA/departures")
