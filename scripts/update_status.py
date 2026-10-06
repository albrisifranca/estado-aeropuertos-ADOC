#!/usr/bin/env python3
"""Descarga datos oficiales y genera data/status.json para la página.

Fuentes:
  - NOAA / Aviation Weather Center: METAR y TAF de todo el mundo (archivos de caché públicos).
  - FAA NAS Status: ground stops, programas de demora, demoras y cierres en EE.UU.

Solo usa la biblioteca estándar de Python, sin claves ni cuentas.
"""
import csv
import gzip
import io
import html
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "estado-aeropuertos/1.0 (herramienta de consulta; github pages)"
METAR_CACHE = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
TAF_CACHE = "https://aviationweather.gov/data/cache/tafs.cache.xml.gz"
AWC_API = "https://aviationweather.gov/api/data/{kind}?ids={ids}&format=json"
FAA_STATUS = "https://nasstatus.faa.gov/api/airport-status-information"
NOTAM_API = "https://external-api.faa.gov/notamapi/v1/notams?icaoLocation={icao}&pageSize=1000"
NOTAM_EVERY_MIN = 55
ISIGMET = "https://aviationweather.gov/api/data/isigmet?format=json"
USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
GDACS = "https://www.gdacs.org/xml/rss.xml"
GDACS_TYPES = {"TC": ("Ciclón tropical", 300), "FL": ("Inundación", 120), "VO": ("Erupción volcánica", 150),
               "EQ": ("Sismo", 200), "WF": ("Incendio forestal", 60), "TS": ("Tsunami", 200)}
NHC_STORMS = "https://www.nhc.noaa.gov/CurrentStorms.json"
NHC_CONE = ("https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather/MapServer/{layer}/query"
            "?where=1%3D1&outFields=stormname,stormtype,advdate,fcstprd&f=geojson")
NHC_LAYER = {"AT": 8, "EP": 138, "CP": 268}  # capa "Forecast Cone" del primer número de cada cuenca; cada número suma 26
NHC_TIPO = {"HU": "Huracán", "MH": "Huracán mayor", "TS": "Tormenta tropical", "TD": "Depresión tropical",
            "STS": "Tormenta subtropical", "SD": "Depresión subtropical", "PTC": "Posible ciclón tropical", "PC": "Posible ciclón tropical"}
NEWS = "https://news.google.com/rss/search?q={q}&hl={hl}"
NEWS_QUERIES = [("airport strike OR walkout when:3d", "en-US&gl=US&ceid=US:en"),
                ("air traffic controllers strike when:3d", "en-GB&gl=GB&ceid=GB:en"),
                ("aeropuerto huelga OR paro when:3d", "es-419&gl=AR&ceid=AR:es-419"),
                ("aeroporto greve when:3d", "pt-BR&gl=BR&ceid=BR:pt-419"),
                ("aéroport grève when:3d", "fr&gl=FR&ceid=FR:fr"),
                ("Flughafen Streik when:3d", "de&gl=DE&ceid=DE:de"),
                ("aeroporto sciopero when:3d", "it&gl=IT&ceid=IT:it")]
NEWS_EVERY_MIN = 30
EASA_CZIB = "https://www.easa.europa.eu/en/domains/air-operations/czibs/export-json?page&_format=json"
EASA_PAGE = "https://www.easa.europa.eu/en/domains/air-operations/czibs"
FAA_PRN = "https://www.faa.gov/air_traffic/publications/us_restrictions"
# Encabezados de la página de la FAA que corresponden a un país entero (los avisos sobre zonas oceánicas se ignoran).
FAA_HEADINGS = {"Afghanistan": "AF", "Belarus": "BY", "Haiti": "HT", "Iran": "IR", "Iraq": "IQ", "Korea, North": "KP", "Libya": "LY",
                "Mali": "ML", "Russian Federation": "RU", "Somalia": "SO", "Syria": "SY", "Ukraine": "UA", "Yemen": "YE",
                "Venezuela": "VE", "Sudan": "SD", "South Sudan": "SS", "Israel": "IL", "Lebanon": "LB", "Ethiopia": "ET",
                "Pakistan": "PK", "Niger": "NE", "Burkina Faso": "BF", "Egypt": "EG", "Kenya": "KE", "Saudi Arabia": "SA"}
COUNTRY_ALIASES = {"russian federation": "RU", "russia": "RU", "north korea": "KP", "korea, north": "KP", "united arab emirate": "AE",
                   "united arab emirates": "AE", "uae": "AE", "palestine": "PS", "west bank": "PS", "gaza": "PS", "syria": "SY",
                   "iran": "IR", "türkiye": "TR", "turkey": "TR", "moldova": "MD", "south sudan": "SS", "sudan": "SD"}


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def read_awc_csv(text):
    """Los CSV de AWC traen líneas de aviso antes del encabezado y columnas repetidas (sky_cover)."""
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("raw_text"))
    rows = csv.reader(io.StringIO("\n".join(lines[start:])))
    header = next(rows)
    for row in rows:
        rec, sky = {}, []
        for name, val in zip(header, row):
            if name == "sky_cover":
                sky.append([val, None])
            elif name == "cloud_base_ft_agl" and sky:
                sky[-1][1] = val
            elif name not in rec:
                rec[name] = val
        rec["_sky"] = [[c, int(b) if b else None] for c, b in sky if c]
        yield rec


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def iso(t):
    return t.replace(".000Z", "Z") if t else None


def metars(wanted):
    out = {}
    for r in read_awc_csv(get(METAR_CACHE).decode("utf-8", "replace")):
        st = r.get("station_id", "")
        if st not in wanted:
            continue
        prev = out.get(st)
        t = iso(r.get("observation_time"))
        if prev and prev["time"] and t and prev["time"] >= t:
            continue
        out[st] = {
            "raw": r.get("raw_text", ""),
            "time": t,
            "cat": r.get("flight_category") or None,
            "temp": num(r.get("temp_c")),
            "dew": num(r.get("dewpoint_c")),
            "wdir": r.get("wind_dir_degrees") or None,
            "wspd": num(r.get("wind_speed_kt")),
            "gust": num(r.get("wind_gust_kt")),
            "vis": r.get("visibility_statute_mi") or None,
            "alt": num(r.get("altim_in_hg")),
            "wx": r.get("wx_string") or None,
            "sky": r["_sky"],
            "vv": num(r.get("vert_vis_ft")),
        }
    return out


def tafs(wanted):
    out = {}
    root = ET.fromstring(get(TAF_CACHE))
    for t in root.iter("TAF"):
        st = (t.findtext("station_id") or "").strip()
        if st in wanted:
            it = iso((t.findtext("issue_time") or "").strip())
            if st not in out or (it and it > (out[st]["time"] or "")):
                out[st] = {"raw": (t.findtext("raw_text") or "").strip(), "time": it}
    return out


def api_fallback(kind, wanted):
    """Si la caché falla, pide por lotes a la API (máx. 400 por consulta)."""
    ids = sorted(i for i in wanted if len(i) == 4 and i.isalnum())
    out = {}
    for i in range(0, len(ids), 100):
        data = json.loads(get(AWC_API.format(kind=kind, ids=",".join(ids[i:i + 100]))) or b"[]")
        for r in data:
            st = r.get("icaoId")
            if kind == "metar":
                out[st] = {"raw": r.get("rawOb", ""), "time": datetime.fromtimestamp(r["obsTime"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if r.get("obsTime") else None,
                           "cat": r.get("fltCat"), "temp": r.get("temp"), "dew": r.get("dewp"), "wdir": str(r.get("wdir")) if r.get("wdir") is not None else None,
                           "wspd": r.get("wspd"), "gust": r.get("wgst"), "vis": str(r.get("visib")) if r.get("visib") is not None else None,
                           "alt": round(r["altim"] / 33.8639, 2) if r.get("altim") else None, "wx": r.get("wxString"),
                           "sky": [[c.get("cover"), c.get("base")] for c in r.get("clouds") or []], "vv": None}
            else:
                out[st] = {"raw": r.get("rawTAF", ""), "time": r.get("issueTime")}
        time.sleep(1)
    return out


def element_to_obj(el):
    kids = list(el)
    if not kids:
        return (el.text or "").strip()
    obj = {}
    for k in kids:
        v = element_to_obj(k)
        if k.tag in obj:
            if not isinstance(obj[k.tag], list):
                obj[k.tag] = [obj[k.tag]]
            obj[k.tag].append(v)
        else:
            obj[k.tag] = v
    return obj


def faa(local_to_icao):
    root = ET.fromstring(get(FAA_STATUS))
    updated = (root.findtext("Update_Time") or "").strip()
    events = []
    for dt in root.findall("Delay_type"):
        name = (dt.findtext("Name") or "").strip()
        for lst in dt:
            if lst.tag == "Name":
                continue
            for item in lst:
                o = element_to_obj(item)
                if not isinstance(o, dict):
                    continue
                arpt = (o.get("ARPT") or "").strip().upper()
                ev = {"faa": arpt, "icao": local_to_icao.get(arpt, ("K" + arpt) if len(arpt) == 3 else arpt), "program": name, "reason": o.get("Reason", "")}
                if lst.tag == "Ground_Stop_List":
                    ev.update(kind="ground_stop", until=o.get("End_Time", ""))
                elif lst.tag == "Ground_Delay_List":
                    ev.update(kind="ground_delay", avg=o.get("Avg", ""), max=o.get("Max", ""))
                elif lst.tag == "Arrival_Departure_Delay_List":
                    ev["kind"] = "delay"
                    ev["delays"] = [{"type": d.get("Type", ""), "min": (d.findtext("Min") or "").strip(),
                                     "max": (d.findtext("Max") or "").strip(), "trend": (d.findtext("Trend") or "").strip()}
                                    for d in item.findall("Arrival_Departure")]
                elif lst.tag == "Airport_Closure_List":
                    ev.update(kind="closure", start=o.get("Start", ""), reopen=o.get("Reopen", ""))
                elif lst.tag == "Deicing_List" or "Deic" in lst.tag:
                    ev["kind"] = "deicing"
                else:
                    ev["kind"] = "other"
                    ev["detail"] = o
                events.append(ev)
    return {"updated": updated, "events": events}


def parse_time(t):
    if not t or not t[0].isdigit():
        return None
    try:
        return datetime.fromisoformat(t[:19]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


CLOSED = re.compile(r"\b(AD|AP|AD AP|AERODROME|AIRPORT)\s+(IS\s+)?(CLSD|CLOSED)\b")
PARTIAL = re.compile(r"\bEXC\b|EXCEPT|\bPPR\b|\bTO\s+(ALL\s+)?(NON|ACFT|GA|VFR|IFR|TFC|TRAINING|PRIVATE|UNSCHEDULED|ARR|DEP)|"
                     r"\bDLY\b|DAILY|\b(MON|TUE|WED|THU|FRI|SAT|SUN)\b|\b\d{4}-\d{4}\b|\bBTN\b")


def classify_notam(feature, now):
    """Devuelve un aviso de cierre si el NOTAM cierra el aeródromo y está vigente ahora."""
    core = (feature.get("properties") or {}).get("coreNOTAMData") or {}
    n = core.get("notam") or {}
    texts = [n.get("text") or ""] + [t.get("formattedText") or t.get("simpleText") or "" for t in core.get("notamTranslation") or []]
    body = " ".join(texts).upper().replace("\n", " ")
    qcode = (n.get("selectionCode") or "").upper()
    if not (qcode.startswith("QFALC") or CLOSED.search(body)):
        return None
    start, end = parse_time(n.get("effectiveStart")), n.get("effectiveEnd") or ""
    end_t = parse_time(end)
    if start and start > now:
        return None
    if end_t and end_t < now:
        return None
    main = (n.get("text") or texts[-1]).strip()
    return {"id": f"{n.get('number') or n.get('id') or ''}", "text": main[:600], "start": n.get("effectiveStart"),
            "end": end or None, "full": not PARTIAL.search(main.upper())}


def notam_closures(icaos, cid, secret):
    now = datetime.now(timezone.utc)

    def one(icao):
        req = urllib.request.Request(NOTAM_API.format(icao=icao), headers={"client_id": cid, "client_secret": secret, "User-Agent": UA})
        for i in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    items = json.loads(r.read()).get("items") or []
                return icao, [c for c in (classify_notam(f, now) for f in items) if c]
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise
                time.sleep(5 * (i + 1))
            except Exception:  # noqa: BLE001
                time.sleep(5 * (i + 1))
        return icao, None

    out, failed = {}, 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for icao, res in pool.map(one, icaos):
            if res is None:
                failed += 1
            elif res:
                out[icao] = res
    return out, failed


def previous_status():
    """Último status.json publicado (rama "datos"), para no consultar en cada corrida las fuentes lentas."""
    repo = os.environ.get("GITHUB_REPOSITORY", "albrisifranca/estado-aeropuertos-ADOC")
    try:
        return json.loads(get(f"https://raw.githubusercontent.com/{repo}/datos/status.json"))
    except Exception:  # noqa: BLE001
        return {}


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(a)))


def in_poly(lat, lon, poly):
    inside, n = False, len(poly)
    for i in range(n):
        (y1, x1), (y2, x2) = poly[i], poly[(i + 1) % n]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def poly_km(lat, lon, poly):
    """0 si el punto está dentro del polígono; si no, distancia aproximada al borde."""
    if in_poly(lat, lon, poly):
        return 0
    best = min(km(lat, lon, y, x) for y, x in poly)
    for (y1, x1), (y2, x2) in zip(poly, poly[1:] + poly[:1]):
        for t in (0.25, 0.5, 0.75):
            best = min(best, km(lat, lon, y1 + (y2 - y1) * t, x1 + (x2 - x1) * t))
    return best


def natural_events():
    """Eventos naturales vigentes de fuentes oficiales: SIGMET de NOAA, sismos de USGS y alertas GDACS (ONU/UE)."""
    now = time.time()
    events, errors = [], []
    try:
        seen = set()
        for sg in json.loads(get(ISIGMET)):
            hz = sg.get("hazard")
            if hz not in ("VA", "TC") or not sg.get("coords"):
                continue
            if not (sg.get("validTimeFrom", 0) - 3600 <= now <= sg.get("validTimeTo", 0)):
                continue
            key = (hz, sg.get("qualifier"), str(sg.get("seriesId", "")).rstrip("F"))
            if key in seen:
                continue
            seen.add(key)
            poly = [(c["lat"], c["lon"]) for c in sg["coords"]]
            name = (sg.get("qualifier") or "").replace("ERUPTION", "").replace("MT ", "").strip().title()
            events.append({"src": "SIGMET", "type": hz, "name": name, "poly": poly, "near": 60 if hz == "VA" else 150,
                           "lat": sum(p[0] for p in poly) / len(poly), "lon": sum(p[1] for p in poly) / len(poly),
                           "level": 3, "fir": sg.get("firName"), "until": sg.get("validTimeTo"),
                           "raw": (sg.get("rawSigmet") or "")[:500],
                           "url": "https://aviationweather.gov/api/data/isigmet?format=raw&hazard=" + hz.lower()})
    except Exception as e:  # noqa: BLE001
        errors.append(f"sigmet: {e}")
    try:
        for f in json.loads(get(USGS)).get("features") or []:
            pr, (lon, lat, _depth) = f["properties"], f["geometry"]["coordinates"]
            mag = pr.get("mag") or 0
            if mag < 5.5 or now - pr.get("time", 0) / 1000 > 24 * 3600:
                continue
            events.append({"src": "USGS", "type": "EQ", "name": f"M{mag:.1f} · {pr.get('place', '')}", "lat": lat, "lon": lon,
                           "near": 300 if mag >= 7 else 200 if mag >= 6 else 60, "level": 3 if mag >= 7 else 2 if mag >= 6 else 1,
                           "time": pr.get("time"), "mag": mag, "tsunami": pr.get("tsunami"), "url": pr.get("url")})
    except Exception as e:  # noqa: BLE001
        errors.append(f"usgs: {e}")
    try:
        for st in json.loads(get(NHC_STORMS)).get("activeStorms") or []:
            b = st.get("binNumber") or ""
            if b[:2] not in NHC_LAYER or not b[2:].isdigit():
                continue
            fc = json.loads(get(NHC_CONE.format(layer=NHC_LAYER[b[:2]] + 26 * (int(b[2:]) - 1)))).get("features") or []
            if not fc:
                continue
            g = fc[0]["geometry"]
            ring = g["coordinates"][0] if g["type"] == "Polygon" else max((p[0] for p in g["coordinates"]), key=len)
            paso = max(1, len(ring) // 240)
            poly = [(y, x) for x, y in ring[::paso]]
            cls, kt = st.get("classification", ""), int(st.get("intensity") or 0)
            events.append({"src": "NHC", "type": "TC5", "name": f"{NHC_TIPO.get(cls, 'Ciclón tropical')} {st.get('name', '')}".strip(),
                           "poly": poly, "near": 150, "lat": st.get("latitudeNumeric", 0), "lon": st.get("longitudeNumeric", 0),
                           "level": 2 if cls in ("HU", "MH") else 1, "kmh": round(kt * 1.852), "adv": (st.get("publicAdvisory") or {}).get("issuance"),
                           "url": "https://www.nhc.noaa.gov/"})
    except Exception as e:  # noqa: BLE001
        errors.append(f"nhc: {e}")
    try:
        root = ET.fromstring(get(GDACS))
        ns = {"gdacs": "http://www.gdacs.org", "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#"}
        for it in root.iter("item"):
            g = lambda tag: (it.findtext(tag, namespaces=ns) or "").strip()  # noqa: E731
            et, al = g("gdacs:eventtype"), g("gdacs:alertlevel")
            if et not in GDACS_TYPES or al not in ("Orange", "Red") or g("gdacs:iscurrent").lower() != "true":
                continue
            if et == "EQ":  # los sismos ya vienen de USGS
                continue
            label, near = GDACS_TYPES[et]
            events.append({"src": "GDACS", "type": et, "name": g("gdacs:eventname") or g("gdacs:country") or g("title"),
                           "country": g("gdacs:country"), "lat": float(g("geo:Point/geo:lat") or 0), "lon": float(g("geo:Point/geo:long") or 0),
                           "near": near, "level": 3 if al == "Red" else 2, "alert": al, "title": g("title"), "url": g("link")})
    except Exception as e:  # noqa: BLE001
        errors.append(f"gdacs: {e}")
    return events, errors


def hazards_by_airport(rows, events):
    out = {}
    for ev in events:
        if "poly" in ev:
            m = ev["near"] / 100 + 1
            ev["_bb"] = (min(p[0] for p in ev["poly"]) - m, max(p[0] for p in ev["poly"]) + m,
                         min(p[1] for p in ev["poly"]) - m * 1.5, max(p[1] for p in ev["poly"]) + m * 1.5)
    for r in rows:
        icao, lat, lon = r[0], r[5], r[6]
        for i, ev in enumerate(events):
            bb = ev.get("_bb")
            if bb and not (bb[0] <= lat <= bb[1] and bb[2] <= lon <= bb[3]):
                continue
            if not bb and (abs(lat - ev["lat"]) > 12 or abs(((lon - ev["lon"] + 180) % 360) - 180) > 20):
                continue
            d = poly_km(lat, lon, ev["poly"]) if "poly" in ev else km(lat, lon, ev["lat"], ev["lon"])
            if d <= ev["near"]:
                lvl = ev["level"] if (d == 0 or "poly" not in ev and d <= ev["near"] / 2) else max(1, ev["level"] - 1)
                out.setdefault(icao, []).append([i, round(d), lvl])
    for ev in events:
        ev.pop("_bb", None)
    return out


# ---------- huelgas y paros (titulares de Google Noticias) ----------
LABOR = re.compile(r"\b(strikes?|striking|walkouts?|industrial action|huelgas?|paros?|greves?|grèves?|sciopero|scioperi|streiks?|warnstreiks?|ausstand)\b", re.I)
MILITAR = re.compile(r"missile|drone|air ?strike|houthi|attack|bomb|ataque|misil|bombarde|frappe|raid|kill|muert|military|militar|forces|fuerzas|rocket|cohete|gaza|yemen|bird ?strike|lightning|rail|train|tren|metro|subway|hunger", re.I)
AVIACION = re.compile(r"airport|aeropuerto|aeroporto|aéroport|flughafen|flights?|vuelos?|voos?|\bvols?\b|flüge|voli|air traffic|controlador|controller|contrôleur|fluglots|aerol[ií]nea|airline|compagnie|handling|ground staff|aduana|customs|douane|zoll|aviaci|aviation|aéreo|aereo|luftverkehr", re.I)
CIUDADES_ES = {"londres": "London", "bruselas": "Brussels", "nueva york": "New York", "múnich": "Munich", "munich": "Munich", "ámsterdam": "Amsterdam",
               "fráncfort": "Frankfurt", "francfort": "Frankfurt", "frankfurt am main": "Frankfurt", "milán": "Milan", "milano": "Milan", "roma": "Rome",
               "lisboa": "Lisbon", "atenas": "Athens", "estambul": "Istanbul", "moscú": "Moscow", "pekín": "Beijing", "tokio": "Tokyo", "bruxelles": "Brussels",
               "brüssel": "Brussels", "londra": "London", "parigi": "Paris", "parís": "Paris", "colonia": "Cologne", "köln": "Cologne", "düsseldorf": "Dusseldorf",
               "génova": "Genoa", "nápoles": "Naples", "napoli": "Naples", "venecia": "Venice", "venezia": "Venice", "florencia": "Florence", "firenze": "Florence",
               "copenhague": "Copenhagen", "estocolmo": "Stockholm", "varsovia": "Warsaw", "praga": "Prague", "viena": "Vienna", "wien": "Vienna",
               "ginebra": "Geneva", "genève": "Geneva", "zúrich": "Zurich", "sevilla": "Seville", "zaragoza": "Zaragoza", "san pablo": "Sao Paulo",
               "são paulo": "Sao Paulo", "rio de janeiro": "Rio De Janeiro", "ciudad de méxico": "Mexico City", "nueva delhi": "New Delhi"}
# Nombres con los que se conoce a los aeropuertos en las noticias.
APODOS = {"ezeiza": "SAEZ", "aeroparque": "SABE", "guarulhos": "SBGR", "galeao": "SBGL", "congonhas": "SBSP", "viracopos": "SBKP",
          "barajas": "LEMD", "el prat": "LEBL", "heathrow": "EGLL", "gatwick": "EGKK", "stansted": "EGSS", "schiphol": "EHAM",
          "zaventem": "EBBR", "fiumicino": "LIRF", "malpensa": "LIMC", "orly": "LFPO", "roissy": "LFPG", "charles de gaulle": "LFPG",
          "narita": "RJAA", "haneda": "RJTT", "jfk": "KJFK", "o'hare": "KORD", "ohare": "KORD", "el dorado": "SKBO", "tocumen": "MPTO",
          "jorge chavez": "SPJC", "carrasco": "SUMU", "silvio pettirossi": "SGAS", "viru viru": "SLVR", "juan santamaria": "MROC",
          "aicm": "MMMX", "felipe angeles": "NLU", "arturo merino benitez": "SCEL", "pudahuel": "SCEL", "tegel": "EDDB", "kastrup": "EKCH",
          "arlanda": "ESSA", "gardermoen": "ENGM", "vantaa": "EFHK", "changi": "WSSS", "incheon": "RKSI", "hamad": "OTHH", "logan": "KBOS"}
PAISES_ES = {"bélgica": "BE", "belgique": "BE", "belgien": "BE", "francia": "FR", "frança": "FR", "frankreich": "FR", "alemania": "DE", "alemanha": "DE",
             "deutschland": "DE", "allemagne": "DE", "italia": "IT", "itália": "IT", "italie": "IT", "españa": "ES", "espanha": "ES", "espagne": "ES",
             "spanien": "ES", "reino unido": "GB", "inglaterra": "GB", "portugal": "PT", "grecia": "GR", "grèce": "GR", "países bajos": "NL", "holanda": "NL",
             "argentina": "AR", "brasil": "BR", "chile": "CL", "perú": "PE", "colombia": "CO", "méxico": "MX", "ecuador": "EC", "uruguay": "UY",
             "paraguay": "PY", "bolivia": "BO", "venezuela": "VE", "estados unidos": "US", "canadá": "CA", "finlandia": "FI", "noruega": "NO",
             "suecia": "SE", "dinamarca": "DK", "irlanda": "IE", "suiza": "CH", "austria": "AT", "polonia": "PL", "nigeria": "NG", "kenia": "KE",
             "sudáfrica": "ZA", "india": "IN", "japón": "JP", "corea": "KR", "australia": "AU", "nueva zelanda": "NZ", "israel": "IL", "turquía": "TR"}


def huelgas(rows, countries, prev):
    """Titulares de los últimos 3 días sobre huelgas o paros que afectan aeropuertos, aerolíneas o controladores,
    con los aeropuertos (por ciudad) y países que nombran. Se consulta cada NEWS_EVERY_MIN minutos."""
    last = parse_time((prev or {}).get("huelgas_checked") or "")
    if last and (datetime.now(timezone.utc) - last).total_seconds() < NEWS_EVERY_MIN * 60 and "huelgas" in prev:
        return prev["huelgas"], prev["huelgas_checked"], []
    norm = lambda t: "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")  # noqa: E731
    por_ciudad = {}
    genericas = re.compile(r"\b(international|intl|airport|aeropuerto|aeroporto|aeroport|aéroport|flughafen|regional|airfield|air base|"
                           r"internacional|international|de|del|da|do|d)\b")
    for r in rows:
        if r[7] == "L" and r[3] and r[1]:
            por_ciudad.setdefault(norm(r[3]), []).append((r[0], r[4]))
    for r in rows:
        # Nombre propio del aeropuerto ("Brussels Airport" → "brussels", "Leeds Bradford Airport" → "leeds bradford").
        if r[7] in ("L", "M") and r[1]:
            k = re.sub(r"\s+", " ", genericas.sub(" ", norm(r[2]).replace("-", " "))).strip()
            if len(k) >= 5 and (r[0], r[4]) not in por_ciudad.get(k, []):
                por_ciudad.setdefault(k, []).append((r[0], r[4]))
    for es, en in CIUDADES_ES.items():
        if norm(en) in por_ciudad:
            por_ciudad[norm(es)] = por_ciudad[norm(en)]
    for apodo, icao in APODOS.items():
        r = next((r for r in rows if r[0] == icao or r[1] == icao), None)
        if r:
            por_ciudad[norm(apodo)] = [(r[0], r[4])]
    paises = {norm(v): k for k, v in countries.items() if len(v) > 3}
    paises.update({norm(k): v for k, v in COUNTRY_ALIASES.items()})
    paises.update({norm(k): v for k, v in PAISES_ES.items()})
    pat_c = re.compile(r"\b(" + "|".join(sorted(map(re.escape, por_ciudad), key=len, reverse=True)) + r")\b")
    pat_p = re.compile(r"\b(" + "|".join(sorted(map(re.escape, paises), key=len, reverse=True)) + r")\b")
    out, errores, vistos = [], [], set()
    now = datetime.now(timezone.utc)
    for q, hl in NEWS_QUERIES:
        try:
            root = ET.fromstring(get(NEWS.format(q=urllib.parse.quote(q), hl=hl)))
        except Exception as e:  # noqa: BLE001
            errores.append(f"noticias: {e}")
            continue
        for it in root.iter("item"):
            titulo = (it.findtext("title") or "").strip()
            fuente = (it.findtext("source") or "").strip()
            if fuente and titulo.endswith(" - " + fuente):
                titulo = titulo[: -len(fuente) - 3]
            if not LABOR.search(titulo) or not AVIACION.search(titulo) or MILITAR.search(titulo):
                continue
            try:
                fecha = parsedate_to_datetime(it.findtext("pubDate"))
            except Exception:  # noqa: BLE001
                continue
            if (now - fecha).total_seconds() > 3 * 86400:
                continue
            nt = norm(titulo).replace("-", " ")
            clave = re.sub(r"[^a-z0-9]", "", nt)[:60]
            if clave in vistos:
                continue
            vistos.add(clave)
            ccs = sorted({paises[m] for m in pat_p.findall(nt)})
            icaos = []
            for m in pat_c.findall(nt):
                cands = por_ciudad[m]
                # Si el titular nombra un país, se queda con los aeropuertos de esa ciudad en ese país.
                elegidos = [i for i, cc in cands if not ccs or cc in ccs] or ([i for i, _ in cands] if len({cc for _, cc in cands}) == 1 else [])
                icaos += [i for i in elegidos if i not in icaos]
            if icaos:
                ccs = sorted({r[4] for r in rows if r[0] in icaos})
            if not ccs:
                continue
            out.append({"t": titulo, "src": fuente, "url": it.findtext("link") or "", "fecha": fecha.strftime("%Y-%m-%dT%H:%MZ"),
                        "icaos": icaos[:6], "ccs": ccs[:4]})
    if not out and errores and prev.get("huelgas"):
        return prev["huelgas"], prev.get("huelgas_checked"), errores
    out.sort(key=lambda h: h["fecha"], reverse=True)
    # La misma noticia sale en muchos medios: se quedan las 2 más nuevas por aeropuerto (o por país si no nombra aeropuerto).
    por_tema, final = {}, []
    for h in out:
        k = ",".join(h["icaos"]) or "|" + ",".join(h["ccs"])
        por_tema[k] = por_tema.get(k, 0) + 1
        if por_tema[k] <= 2:
            final.append(h)
    return final[:30], now.strftime("%Y-%m-%dT%H:%M:%SZ"), errores


def country_codes(text, names):
    """Convierte 'Bahrain, Kuwait, Qatar' en códigos ISO usando los nombres de OurAirports."""
    out = []
    for part in re.split(r",|\band\b|/", text or ""):
        k = part.strip().lower().rstrip(".")
        if not k:
            continue
        cc = COUNTRY_ALIASES.get(k) or names.get(k)
        if not cc:
            cc = next((v for n, v in names.items() if k.startswith(n) or n.startswith(k)), None)
        if cc and cc not in out:
            out.append(cc)
    return out


def easa_zones(names):
    data = json.loads(get(EASA_CZIB))
    today = datetime.now(timezone.utc).date()
    zones = []
    for z in data.get("conflict_zones") or []:
        if (z.get("status") or "").lower() != "active":
            continue
        try:
            until = datetime.strptime(z.get("valid_until_date", ""), "%d/%m/%Y").date()
            if until < today:
                continue
        except ValueError:
            pass
        ccs = country_codes(z.get("country"), names)
        if not ccs:
            continue
        upd = re.search(r'datetime="([^"]+)"', z.get("updated") or "")
        zones.append({"source": "EASA", "title": html.unescape(z.get("name") or ""), "countries": ccs,
                      "level": 2 if len(ccs) <= 2 else 1, "until": z.get("valid_until_date"),
                      "updated": upd.group(1) if upd else None,
                      "url": f"https://www.easa.europa.eu/en/node/{z['Nid']}" if z.get("Nid") else EASA_PAGE})
    return zones


def faa_zones():
    t = get(FAA_PRN).decode("utf-8", "replace")
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    txt = html.unescape(re.sub(r"<[^>]+>", " ", t)).replace("\u200b", "")
    txt = re.sub(r"\s+", " ", txt.replace("\xa0", " "))
    zones = []
    for chunk in txt.split("Back to top")[1:]:
        chunk = chunk.strip()
        head = next((h for h in sorted(FAA_HEADINGS, key=len, reverse=True) if chunk.startswith(h + " ")), None)
        if not head:
            continue
        body = chunk[len(head):]
        prohib = bool(re.search(r"SFAR|Special Federal Aviation Regulation|Prohibition", body, re.I))
        if not prohib and re.search(r"Overwater|Ocean|Gulf of", body, re.I):
            continue
        if not prohib and not re.search(r"Advisory|Security", body, re.I):
            continue
        sfar = re.search(r"SFAR\W{0,3}(\d+)", body)
        kicz = re.search(r"KICZ NOTAM [A-Z]\d{4}/\d{2}", body)
        ref = f"SFAR {sfar.group(1)}" if sfar else (kicz.group(0) if kicz else "")
        zones.append({"source": "FAA", "title": f"{'Prohibición' if prohib else 'Advertencia'} de la FAA" + (f" ({ref})" if ref else ""),
                      "countries": [FAA_HEADINGS[head]], "level": 2 if prohib else 1, "until": None, "updated": None,
                      "url": FAA_PRN})
    return zones


def main():
    ap = json.loads((ROOT / "data" / "airports.json").read_text())
    rows = ap["airports"]
    wanted = {r[0] for r in rows}
    local_to_icao = {r[8]: r[0] for r in rows if r[4] in ("US", "PR", "GU", "VI", "AS", "MP") and r[8]}
    status = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": {}, "errors": []}

    for key, fn, kind in (("metar", metars, "metar"), ("taf", tafs, "taf")):
        try:
            status[key] = fn(wanted)
            status["sources"][key] = "cache"
        except Exception as e:  # noqa: BLE001
            status["errors"].append(f"{key} cache: {e}")
            try:
                status[key] = api_fallback(kind, wanted)
                status["sources"][key] = "api"
            except Exception as e2:  # noqa: BLE001
                status["errors"].append(f"{key} api: {e2}")
                status[key] = {}
    try:
        status["faa"] = faa(local_to_icao)
    except Exception as e:  # noqa: BLE001
        status["errors"].append(f"faa: {e}")
        status["faa"] = None

    names = {v.lower(): k for k, v in ap.get("countries", {}).items()}
    status["zones"] = []
    for key, fn in (("easa", lambda: easa_zones(names)), ("faa_prn", faa_zones)):
        try:
            status["zones"] += fn()
            status["sources"][key] = "ok"
        except Exception as e:  # noqa: BLE001
            status["errors"].append(f"{key}: {e}")

    events, ev_err = natural_events()
    status["errors"] += ev_err
    for ev in events:
        if "poly" in ev:
            ev["poly"] = [[round(a, 3), round(b, 3)] for a, b in ev["poly"]]
    status["events"] = events
    status["hazards"] = hazards_by_airport(rows, events)

    prev = previous_status()
    status["huelgas"], status["huelgas_checked"], h_err = huelgas(rows, ap.get("countries", {}), prev)
    status["errors"] += h_err

    cid, secret = os.environ.get("FAA_CLIENT_ID"), os.environ.get("FAA_CLIENT_SECRET")
    status["notam"], status["notam_checked"] = {}, None
    if cid and secret:
        last = parse_time(prev.get("notam_checked") or "")
        if last and (datetime.now(timezone.utc) - last).total_seconds() < NOTAM_EVERY_MIN * 60:
            status["notam"], status["notam_checked"] = prev.get("notam") or {}, prev.get("notam_checked")
        else:
            big = sorted(r[0] for r in rows if r[7] == "L" and len(r[0]) == 4 and r[0].isalpha())
            try:
                closures, failed = notam_closures(big, cid, secret)
                status["notam"], status["notam_checked"] = closures, status["generated"]
                status["notam_count"] = len(big) - failed
                if failed:
                    status["errors"].append(f"notam: {failed} aeropuertos sin respuesta")
            except Exception as e:  # noqa: BLE001
                status["errors"].append(f"notam: {e}")
                status["notam"], status["notam_checked"] = prev.get("notam") or {}, prev.get("notam_checked")
        status["sources"]["notam"] = "faa-notam-api"

    out = ROOT / "data" / "status.json"
    if not status["metar"] and out.exists():
        print("Sin METAR nuevos; se conserva el archivo anterior.", status["errors"], file=sys.stderr)
        sys.exit(1)
    out.write_text(json.dumps(status, ensure_ascii=False, separators=(",", ":")))
    print(f"METAR {len(status['metar'])}, TAF {len(status['taf'])}, FAA {len((status['faa'] or {}).get('events', []))}, NOTAM cierres {len(status['notam'])}, zonas {len(status['zones'])}, eventos {[(e['src'], e['type'], e['name'][:30]) for e in status['events']]}, aeropuertos afectados {len(status['hazards'])}, huelgas {len(status['huelgas'])}, errores {status['errors']}")


if __name__ == "__main__":
    main()
