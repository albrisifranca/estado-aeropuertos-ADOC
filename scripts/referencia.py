"""Datos de referencia para logística, una vez por día (fuentes públicas, sin registro).

- Feriados nacionales de cada país (Nager.Date).
- Aerolíneas que vuelan desde el hub (MIA) a cada destino, de pasajeros y de carga
  (sección "Airlines and destinations" de Wikipedia, códigos OACI de Wikidata).
- Principales aerolíneas de pasajeros y de carga de cada aeropuerto grande (misma fuente).

Escribe data/referencia.json.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "estado-aeropuertos/1.0 (https://github.com/albrisifranca/estado-aeropuertos-ADOC)"
HUB = "KMIA"
WP = "https://en.wikipedia.org/w/api.php"
WD = "https://www.wikidata.org/w/api.php"
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


# ---------- Wikipedia: aerolíneas y destinos ----------
REF = re.compile(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", re.S)
COMMENT = re.compile(r"<!--.*?-->", re.S)
LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")


def seccion_destinos(wt):
    """Devuelve [(tipo, texto)] con tipo 'pax' o 'cargo' de la sección de aerolíneas y destinos."""
    m = re.search(r"\n==\s*Airlines and destinations\s*==\s*\n", wt)
    if not m:
        return []
    rest = wt[m.end():]
    fin = re.search(r"\n==[^=]", rest)
    sec = rest[:fin.start()] if fin else rest
    partes = re.split(r"\n===+\s*(.*?)\s*===+\s*\n", "\n" + sec)
    out = []
    if len(partes) == 1:
        return [("pax", sec)]
    for i in range(1, len(partes), 2):
        t = partes[i].lower()
        if "cargo" in t or "freight" in t:
            out.append(("cargo", partes[i + 1]))
        elif "passenger" in t or "airlines" in t or "scheduled" in t:
            out.append(("pax", partes[i + 1]))
    if not out and partes[0].strip():
        out.append(("pax", partes[0]))
    return out


def celdas_de(texto):
    """Parte el texto en celdas por "|" sin cortar dentro de [[enlaces]] ni de plantillas internas.
    Sirve para tablas wiki y para la plantilla {{Airport destination list}}."""
    texto = COMMENT.sub("", REF.sub("", texto))
    prev = None
    while prev != texto:  # quita plantillas sin enlaces ({{cn}}, {{nowrap|texto}}, etc.)
        prev, texto = texto, re.sub(r"\{\{[^{}\[\]]*\}\}", "", texto)
    out, cur, link, tpl, i = [], [], 0, 0, 0
    while i < len(texto):
        two = texto[i:i + 2]
        if two == "[[":
            link += 1; cur.append(two); i += 2; continue
        if two == "]]" and link:
            link -= 1; cur.append(two); i += 2; continue
        if two == "{{":
            tpl += 1; cur.append(two); i += 2; continue
        if two == "}}" and tpl:
            tpl -= 1; cur.append(two); i += 2; continue
        ch = texto[i]
        # separador de celda: "|" fuera de enlaces y como mucho dentro de la plantilla de la lista
        if ch == "|" and not link and tpl <= 1 and not (tpl == 1 and _dentro_de_plantilla_interna(cur)):
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return [c.strip() for c in out]


def _dentro_de_plantilla_interna(cur):
    # Si la última "{{" abierta es de una plantilla con enlaces (p. ej. {{nowrap|[[X]]}}) y no la lista,
    # el "|" no separa celdas.
    txt = "".join(cur[-200:])
    k = txt.rfind("{{")
    return k >= 0 and "Airport destination list" not in txt[k:k + 40] and "}}" not in txt[k:]


def filas(texto):
    """Lista de (aerolínea, [destinos], [destinos de temporada])."""
    celdas = celdas_de(texto)
    out = []
    i, n = 0, len(celdas)
    while i < n:
        c = celdas[i]
        links = LINK.findall(c)
        if len(links) == 1 and "," not in LINK.sub("", c) and not re.search(r"seasonal|charter", c, re.I):
            j = i + 1
            while j < n and not LINK.findall(celdas[j]):
                j += 1
            if j < n:
                d = celdas[j]
                k = re.search(r"seasonal|charter", d, re.I)
                fijos = [t.strip() for t, _ in LINK.findall(d[:k.start()] if k else d)]
                temp = [t.strip() for t, _ in LINK.findall(d[k.start():])] if k else []
                aero = (links[0][1] or links[0][0]).strip()
                aero = re.sub(r"\s*\((airline|airlines|company|cargo airline|airline brand)\)$", "", aero)
                if fijos or temp:
                    out.append((aero, fijos, temp))
                i = j + 1
                continue
        i += 1
    return out


def wikitextos(titulos):
    """Contenido actual de varias páginas de Wikipedia, siguiendo redirecciones."""
    out = {}
    for grupo in chunks(titulos, 20):
        d = get(WP, {"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                     "titles": "|".join(grupo), "redirects": 1, "format": "json", "formatversion": 2})
        q = d.get("query", {})
        alias = {}
        for r in q.get("normalized", []) + q.get("redirects", []):
            alias[r["to"]] = alias.get(r["from"], r["from"])
        for p in q.get("pages", []):
            if p.get("missing") or not p.get("revisions"):
                continue
            orig = p["title"]
            while orig in alias:
                orig = alias[orig]
            out[orig] = p["revisions"][0]["slots"]["main"]["content"]
        time.sleep(1)
    return out


def oaci_de_titulos(titulos):
    """Título de Wikipedia → código OACI (P239 de Wikidata), siguiendo redirecciones."""
    qid = {}
    for grupo in chunks(list(titulos), 50):
        d = get(WP, {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item", "titles": "|".join(grupo),
                     "redirects": 1, "format": "json", "formatversion": 2})
        q = d.get("query", {})
        final = {}
        for p in q.get("pages", []):
            if p.get("pageprops", {}).get("wikibase_item"):
                final[p["title"]] = p["pageprops"]["wikibase_item"]
        paso = {}
        for r in q.get("normalized", []) + q.get("redirects", []):
            paso[r["from"]] = r["to"]
        for t in grupo:
            x = t
            for _ in range(4):
                if x in final:
                    break
                x = paso.get(x, x)
            if x in final:
                qid[t] = final[x]
        time.sleep(0.5)
    oaci = {}
    ids = sorted(set(qid.values()))
    for grupo in chunks(ids, 50):
        d = get(WD, {"action": "wbgetentities", "ids": "|".join(grupo), "props": "claims", "format": "json"})
        for q, ent in d.get("entities", {}).items():
            for cl in ent.get("claims", {}).get("P239", []):
                v = cl.get("mainsnak", {}).get("datavalue", {}).get("value")
                if v:
                    oaci[q] = v.upper()
                    break
        time.sleep(0.5)
    return {t: oaci[q] for t, q in qid.items() if q in oaci}


def titulos_por_oaci(codigos):
    """Código OACI → título en Wikipedia en inglés, con una consulta SPARQL a Wikidata."""
    q = """SELECT ?icao ?article WHERE { ?a wdt:P239 ?icao . VALUES ?icao { %s }
      ?article schema:about ?a ; schema:isPartOf <https://en.wikipedia.org/> . }""" % " ".join(f'"{c}"' for c in codigos)
    d = get(SPARQL, {"query": q, "format": "json"})
    out = {}
    for b in d["results"]["bindings"]:
        t = urllib.parse.unquote(b["article"]["value"].rsplit("/", 1)[1]).replace("_", " ")
        out.setdefault(b["icao"]["value"], t)
    return out


def main():
    airports = json.loads((ROOT / "data" / "airports.json").read_text())["airports"]
    grandes = [a[0] for a in airports if a[7] == "L" and re.fullmatch(r"[A-Z]{4}", a[0])]
    ref = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "hub": HUB, "errors": []}

    try:
        ref["feriados"] = feriados()
    except Exception as e:  # noqa: BLE001
        ref["errors"].append(f"feriados: {e}")

    try:
        titulos = {}
        for g in chunks(grandes, 150):
            titulos.update(titulos_por_oaci(g))
            time.sleep(1)
        print(f"Artículos de Wikipedia: {len(titulos)} de {len(grandes)} aeropuertos grandes", flush=True)
        textos = wikitextos(sorted(set(titulos.values())))
        aerolineas, sin_seccion = {}, 0
        filas_hub = []
        for icao, t in titulos.items():
            wt = textos.get(t)
            if not wt:
                continue
            secs = seccion_destinos(wt)
            if not secs:
                sin_seccion += 1
                continue
            pax, cargo = {}, {}
            for tipo, txt in secs:
                for aero, fijos, temp in filas(txt):
                    (cargo if tipo == "cargo" else pax)[aero] = (cargo if tipo == "cargo" else pax).get(aero, 0) + len(fijos) + len(temp)
                    if icao == HUB:
                        filas_hub.append((tipo, aero, fijos, temp))
            top = lambda m, n: [k for k, _ in sorted(m.items(), key=lambda kv: -kv[1])[:n]]
            aerolineas[icao] = {"pax": top(pax, 8), "cargo": top(cargo, 6)}
        ref["aerolineas"] = aerolineas
        print(f"Aerolíneas por aeropuerto: {len(aerolineas)} (sin sección de destinos: {sin_seccion})", flush=True)

        # Destinos del hub: título de cada destino → OACI
        dest = sorted({t for _, _, f, s in filas_hub for t in f + s})
        oaci = oaci_de_titulos(dest)
        desde = {}
        for tipo, aero, fijos, temp in filas_hub:
            for t in fijos + temp:
                c = oaci.get(t)
                if not c:
                    continue
                e = desde.setdefault(c, {"pax": [], "cargo": [], "temporada": []})
                if aero not in e[tipo]:
                    e[tipo].append(aero)
                if t in temp and aero not in e["temporada"]:
                    e["temporada"].append(aero)
        ref["desde_hub"] = desde
        print(f"Hub {HUB}: {len(filas_hub)} filas, {len(dest)} destinos, {len(oaci)} con OACI, {len(desde)} aeropuertos conectados", flush=True)
    except Exception as e:  # noqa: BLE001
        ref["errors"].append(f"wikipedia: {e}")

    if not ref.get("desde_hub") and not ref.get("feriados"):
        print("Sin datos nuevos.", ref["errors"], file=sys.stderr)
        sys.exit(1)
    (ROOT / "data" / "referencia.json").write_text(json.dumps(ref, ensure_ascii=False, separators=(",", ":")))
    print(f"Feriados de {len(ref.get('feriados', {}))} países; errores {ref['errors']}")


if __name__ == "__main__":
    main()
