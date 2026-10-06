import json, urllib.request, urllib.parse, time, re
UA = {"User-Agent": "estado-aeropuertos/1.0 (https://github.com/albrisifranca/estado-aeropuertos-ADOC)"}
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r: return r.read()
def t(n, u, k=1200):
    try: b = get(u); print(f"== {n}: {len(b)}B"); print(b[:k].decode("utf8", "replace")); return b
    except Exception as e: print(f"== {n}: ERROR {e}")
b = t("cone EP3", "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather/MapServer/190/query?where=1%3D1&outFields=*&f=geojson", 800)
if b:
    d = json.loads(b); f = d["features"]; print("feat", len(f), f and f[0]["properties"], f and f[0]["geometry"]["type"], f and len(f[0]["geometry"]["coordinates"][0]))
t("cone AT1", "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather/MapServer/8/query?where=1%3D1&outFields=*&f=geojson", 300)
for q, hl in [("aeropuerto (huelga OR paro)", "es-419&gl=AR&ceid=AR:es-419"), ("airport strike", "en-US&gl=US&ceid=US:en"), ("aeroporto greve", "pt-BR&gl=BR&ceid=BR:pt-419")]:
    b = t("gnews " + q, f"https://news.google.com/rss/search?q={urllib.parse.quote(q + ' when:3d')}&hl={hl}", 200)
    if b:
        for m in re.findall(r"<item>.*?<title>(.*?)</title>.*?<pubDate>(.*?)</pubDate>", b.decode(), re.S)[:15]: print("  ", m)
time.sleep(10)
t("gdelt", "https://api.gdeltproject.org/api/v2/doc/doc?query=" + urllib.parse.quote('airport strike') + "&mode=artlist&format=json&timespan=3d&maxrecords=10", 600)
