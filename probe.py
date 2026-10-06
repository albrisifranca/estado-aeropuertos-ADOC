import json, urllib.request, tarfile, io, csv, collections
H = {"User-Agent": "estado-aeropuertos"}
def get(u): return urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=120).read()
c = json.loads(get("https://api.github.com/repos/vradarserver/standing-data/commits?per_page=5&path=routes"))
for x in c: print(x["commit"]["committer"]["date"], x["commit"]["message"][:70])
r = json.loads(get("https://api.github.com/repos/vradarserver/standing-data")); print("license", r.get("license"), r.get("description"))
t = tarfile.open(fileobj=io.BytesIO(get("https://codeload.github.com/vradarserver/standing-data/tar.gz/refs/heads/main")))
rutas = 0; mia = collections.defaultdict(set); jp = collections.defaultdict(set); pares = set(); airlines = set()
for m in t.getmembers():
    if "/routes/schema-01/" not in m.name or not m.name.endswith(".csv"): continue
    for row in csv.DictReader(io.TextIOWrapper(t.extractfile(m), encoding="utf-8-sig")):
        aps = row["AirportCodes"].split("-"); rutas += 1; airlines.add(row["AirlineCode"])
        for a, b in zip(aps, aps[1:]):
            pares.add((a, b))
            if a == "KMIA": mia[b].add(row["AirlineCode"])
            if b in ("RJAA", "RJTT"): jp[a].add(row["AirlineCode"])
print("rutas", rutas, "aerolineas", len(airlines), "pares", len(pares), "destinos MIA", len(mia))
for k in ["SAEZ", "SBGR", "SCEL", "SKBO", "LEMD", "EGLL", "KDFW", "KORD", "MPTO"]: print("MIA>", k, sorted(mia.get(k, [])))
print("a Japón desde", len(jp), {k: sorted(v) for k, v in list(jp.items()) if k.startswith("K")})
print("cargo MIA", {k: sorted(v & {"GTI", "FDX", "UPS", "CKS", "LCO", "TPA", "QTR", "CLX"}) for k, v in mia.items() if v & {"GTI", "FDX", "UPS", "CKS", "LCO", "TPA", "QTR", "CLX"}})
