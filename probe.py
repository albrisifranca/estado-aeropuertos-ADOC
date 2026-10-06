import json, subprocess, sys
r = subprocess.run([sys.executable, "scripts/referencia.py"])
print("exit", r.returncode)
d = json.load(open("data/referencia.json"))
print("errores", d["errors"])
print("feriados AR", d.get("feriados", {}).get("AR"), "US", d.get("feriados", {}).get("US"))
dh = d.get("desde_hub", {})
for c in ["SAEZ", "SBGR", "SCEL", "SKBO", "LEMD", "EGLL", "MPTO", "SBKP", "MMMX", "SPJC"]:
    print(c, dh.get(c))
ae = d.get("aerolineas", {})
for c in ["KMIA", "SAEZ", "SBGR", "EGLL", "OMDB", "KJFK"]:
    print(c, ae.get(c))
print("tam", len(json.dumps(d)))
