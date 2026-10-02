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
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "estado-aeropuertos/1.0 (herramienta de consulta; github pages)"
METAR_CACHE = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
TAF_CACHE = "https://aviationweather.gov/data/cache/tafs.cache.csv.gz"
AWC_API = "https://aviationweather.gov/api/data/{kind}?ids={ids}&format=json"
FAA_STATUS = "https://nasstatus.faa.gov/api/airport-status-information"


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
    for r in read_awc_csv(get(TAF_CACHE).decode("utf-8", "replace")):
        st = r.get("station_id", "")
        if st in wanted:
            t = iso(r.get("issue_time"))
            if st not in out or (t and t > (out[st]["time"] or "")):
                out[st] = {"raw": r.get("raw_text", ""), "time": t}
    return out


def api_fallback(kind, wanted):
    """Si la caché falla, pide por lotes a la API (máx. 400 por consulta)."""
    ids = sorted(wanted)
    out = {}
    for i in range(0, len(ids), 300):
        data = json.loads(get(AWC_API.format(kind=kind, ids=",".join(ids[i:i + 300]))) or b"[]")
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

    out = ROOT / "data" / "status.json"
    if not status["metar"] and out.exists():
        print("Sin METAR nuevos; se conserva el archivo anterior.", status["errors"], file=sys.stderr)
        sys.exit(1)
    out.write_text(json.dumps(status, ensure_ascii=False, separators=(",", ":")))
    print(f"METAR {len(status['metar'])}, TAF {len(status['taf'])}, FAA {len((status['faa'] or {}).get('events', []))}, errores {status['errors']}")


if __name__ == "__main__":
    main()
