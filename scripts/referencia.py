"""Datos de referencia para logística, una vez por día (fuentes públicas, sin registro).

- Feriados nacionales de cada país (Nager.Date).
- Rutas aéreas vistas por radar: base abierta de Virtual Radar Server (standing-data, CC0),
  armada con los vuelos que detectan los receptores ADS-B de sus usuarios y actualizada a diario.
  Cada número de vuelo (callsign) trae su aerolínea y los aeropuertos que une.
- Nombres actuales de las aerolíneas y cuáles dejaron de operar o son de carga (Wikidata).

Escribe data/referencia.json (feriados, aerolíneas por aeropuerto, destinos del hub) y data/rutas.json (red de rutas).
"""
import csv
import io
import json
import re
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "estado-aeropuertos/1.0 (https://github.com/albrisifranca/estado-aeropuertos-ADOC)"
HUB = "KMIA"
SPARQL = "https://query.wikidata.org/sparql"
NAGER = "https://date.nager.at/api/v3"


def get(url, params=None, tries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


# ---------- feriados ----------
def feriados():
    hoy = date.today()
    hasta = hoy + timedelta(days=45)
    out = {}
    for c in get(f"{NAGER}/AvailableCountries"):
        cc = c["countryCode"]
        lista = []
        for y in sorted({hoy.year, hasta.year}):
            try:
                lista += get(f"{NAGER}/PublicHolidays/{y}/{cc}")
            except Exception:  # noqa: BLE001
                pass
        sel = {}
        for h in lista:
            d = date.fromisoformat(h["date"])
            if hoy - timedelta(days=1) <= d <= hasta:
                k = (h["date"], h.get("name"))
                prev = sel.get(k)
                # [fecha, nombre local, nombre en inglés, nacional (True) o sólo en algunas regiones]
                sel[k] = [h["date"], h.get("localName") or h.get("name"), h.get("name"), bool(h.get("global", True)) or bool(prev and prev[3])]
        if sel:
            out[cc] = sorted(sel.values())
    return out


# ---------- rutas vistas por radar ----------
VRS = "https://codeload.github.com/vradarserver/standing-data/tar.gz/refs/heads/main"
# Operadores de carga conocidos, por si Wikidata no los marca como aerolínea de carga.
CARGA = {"FDX", "UPS", "GTI", "CKS", "ABX", "ATN", "CLX", "NCA", "DHK", "BCS", "DAE", "LCO", "TPA", "LTG", "AJT",
         "WGN", "MPH", "KYE", "CJT", "GEC", "BOX", "CAO", "CKK", "CSS", "AHK", "ABW", "SQC", "ICV", "NPT", "WRC",
         "SWN", "TAY", "PAC", "QAJ", "AZQ", "SRR"}
RE_CARGA = re.compile(r"cargo|freight|express|logistic|carga|frete|courier", re.I)


def rutas_radar():
    """[(aerolínea, [aeropuertos OACI en orden])] de todos los números de vuelo conocidos."""
    req = urllib.request.Request(VRS, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        t = tarfile.open(fileobj=io.BytesIO(r.read()))
    rutas, nombres = [], {}
    for m in t.getmembers():
        if not m.name.endswith(".csv"):
            continue
        f = io.TextIOWrapper(t.extractfile(m), encoding="utf-8-sig")
        if "/routes/schema-01/" in m.name:
            for row in csv.DictReader(f):
                aps = [a for a in row["AirportCodes"].split("-") if a]
                if len(aps) >= 2 and row["AirlineCode"]:
                    rutas.append((row["AirlineCode"], aps))
        elif m.name.endswith("/airlines/schema-01/airlines.csv"):
            for row in csv.DictReader(f):
                nombres[row["ICAO"] or row["Code"]] = row["Name"]
    return rutas, nombres


def aerolineas_wikidata():
    """OACI → (nombre actual o None, ¿sigue operando?, ¿es de carga?)."""
    q = """SELECT ?icao ?item ?itemLabel ?fin ?carga WHERE {
      ?item wdt:P230 ?icao .
      OPTIONAL { ?item wdt:P576 ?fin }
      OPTIONAL { ?item wdt:P31 ?t . ?t rdfs:label "cargo airline"@en . BIND(1 AS ?carga) }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en,es". } }"""
    d = get(SPARQL, {"query": q, "format": "json"})
    out = {}
    for b in d["results"]["bindings"]:
        c = b["icao"]["value"].strip().upper()
        activa = "fin" not in b
        nombre = b["itemLabel"]["value"]
        if re.fullmatch(r"Q\d+", nombre):
            nombre = None
        prev = out.get(c)
        carga = "carga" in b or (prev and prev[2])
        # Un código puede pasar de una aerolínea cerrada a una nueva: gana la que sigue operando.
        if not prev or (activa and not prev[1]) or (activa == prev[1] and nombre and not prev[0]):
            out[c] = (nombre, activa or bool(prev and prev[1]), carga)
        else:
            out[c] = (prev[0], prev[1] or activa, carga)
    return out


def main():
    airports = json.loads((ROOT / "data" / "airports.json").read_text())["airports"]
    ref = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "hub": HUB, "errors": []}

    try:
        ref["feriados"] = feriados()
    except Exception as e:  # noqa: BLE001
        ref["errors"].append(f"feriados: {e}")

    try:
        rutas, nombres_vrs = rutas_radar()
        print(f"Radar: {len(rutas)} números de vuelo, {len(nombres_vrs)} aerolíneas", flush=True)
        try:
            wd = aerolineas_wikidata()
            print(f"Wikidata: {len(wd)} códigos OACI, {sum(1 for v in wd.values() if not v[1])} sin operar", flush=True)
        except Exception as e:  # noqa: BLE001
            wd = {}
            ref["errors"].append(f"wikidata: {e}")

        conocidos = {a[0] for a in airports}
        nombres, idx, red, cerradas = [], {}, {}, set()
        def arista(x, y, cod):
            lst = red.setdefault(x, {}).setdefault(y, [])
            if cod not in lst:
                lst.append(cod)
        for code, aps in rutas:
            info = wd.get(code)
            if info and not info[1]:
                cerradas.add(code)
                continue
            if code not in idx:
                nombre = (info and info[0]) or nombres_vrs.get(code) or code
                carga = code in CARGA or bool(info and info[2]) or bool(RE_CARGA.search(nombre))
                idx[code] = (len(nombres), carga); nombres.append(nombre)
            i, carga = idx[code]
            cod = i * 4 + (1 if carga else 0)
            for x, y in zip(aps, aps[1:]):
                if x != y and x in conocidos and y in conocidos:
                    arista(x, y, cod); arista(y, x, cod)
        rutas_json = {"generated": ref["generated"], "fuente": "Virtual Radar Server (rutas vistas por radar ADS-B)", "aerolineas": nombres, "red": red}
        (ROOT / "data" / "rutas.json").write_text(json.dumps(rutas_json, ensure_ascii=False, separators=(",", ":")))
        print(f"Red de rutas: {len(red)} aeropuertos, {sum(len(v) for v in red.values()) // 2} tramos, "
              f"{len(nombres)} aerolíneas (descartadas {len(cerradas)} que dejaron de operar)", flush=True)

        # Principales aerolíneas de cada aeropuerto: las que más destinos tienen desde ahí.
        aerolineas = {}
        for ap, dests in red.items():
            pax, cargo = {}, {}
            for cods in dests.values():
                for cod in cods:
                    m = cargo if cod & 1 else pax
                    m[cod // 4] = m.get(cod // 4, 0) + 1
            top = lambda m, n: [nombres[k] for k, _ in sorted(m.items(), key=lambda kv: -kv[1])[:n]]
            aerolineas[ap] = {"pax": top(pax, 8), "cargo": top(cargo, 6)}
        ref["aerolineas"] = aerolineas

        # Destinos del hub
        desde = {}
        for d, cods in red.get(HUB, {}).items():
            e = desde.setdefault(d, {"pax": [], "cargo": []})
            for cod in cods:
                lst = e["cargo" if cod & 1 else "pax"]
                if nombres[cod // 4] not in lst:
                    lst.append(nombres[cod // 4])
        ref["desde_hub"] = desde
        print(f"Hub {HUB}: {len(desde)} aeropuertos conectados", flush=True)
    except Exception as e:  # noqa: BLE001
        ref["errors"].append(f"rutas: {e}")

    if not ref.get("desde_hub") and not ref.get("feriados"):
        print("Sin datos nuevos.", ref["errors"], file=sys.stderr)
        sys.exit(1)
    (ROOT / "data" / "referencia.json").write_text(json.dumps(ref, ensure_ascii=False, separators=(",", ":")))
    print(f"Feriados de {len(ref.get('feriados', {}))} países; errores {ref['errors']}")


if __name__ == "__main__":
    main()
