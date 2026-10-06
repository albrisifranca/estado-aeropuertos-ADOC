import json, urllib.request, urllib.parse, time
UA = {"User-Agent": "estado-aeropuertos/1.0 (https://github.com/albrisifranca/estado-aeropuertos-ADOC)"}
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r: return r.read()
def t(n, u, k=1500):
    try: b = get(u); print(f"== {n}: {len(b)}B"); print(b[:k].decode("utf8", "replace")); return b
    except Exception as e: print(f"== {n}: ERROR {e}")
t("nhc current", "https://www.nhc.noaa.gov/CurrentStorms.json")
b = t("nhc mapserver", "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather/MapServer?f=json", 300)
if b:
    d = json.loads(b); print([(l["id"], l["name"]) for l in d.get("layers", [])])
t("nhc layers summary", "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather/MapServer/layers?f=json", 200)
q = urllib.parse.quote('(airport OR aeropuerto OR aeroporto) (strike OR huelga OR paro OR greve OR walkout)')
b = t("gdelt", f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&format=json&timespan=3d&maxrecords=50&sort=datedesc", 3000)
time.sleep(6)
q = urllib.parse.quote('"air traffic control" strike')
t("gdelt atc", f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&format=json&timespan=3d&maxrecords=20", 1500)
