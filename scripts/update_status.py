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
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "estado-aeropuertos/1.0 (herramienta de consulta; github pages)"
METAR_CACHE = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
TAF_CACHE = "https://aviationweather.gov/data/cache/tafs.cache.xml.gz"
AWC_API = "https://aviationweather.gov/api/data/{kind}?ids={ids}&format=json"
FAA_STATUS = "https://nasstatus.faa.gov/api/airport-status-information"
NOTAM_API = "https://external-api.faa.gov/notamapi/v1/notams?icaoLocation={icao}&pageSize=1000"
NOTAM_EVERY_MIN = 55
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
    url = os.environ.get("SITE_URL")
    if not url:
        return {}
    try:
        return json.loads(get(url.rstrip("/") + "/data/status.json?prev=" + str(int(time.time()))))
    except Exception:  # noqa: BLE001
        return {}


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

    cid, secret = os.environ.get("FAA_CLIENT_ID"), os.environ.get("FAA_CLIENT_SECRET")
    status["notam"], status["notam_checked"] = {}, None
    if cid and secret:
        prev = previous_status()
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
    print(f"METAR {len(status['metar'])}, TAF {len(status['taf'])}, FAA {len((status['faa'] or {}).get('events', []))}, NOTAM cierres {len(status['notam'])}, zonas {[(z['source'], z['countries']) for z in status['zones']]}, errores {status['errors']}")


if __name__ == "__main__":
    main()
