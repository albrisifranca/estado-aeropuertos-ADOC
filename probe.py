import json, subprocess, sys, time
t = time.time(); r = subprocess.run([sys.executable, "scripts/update_status.py"]); print("seg", round(time.time() - t))
d = json.load(open("data/status.json"))
print("errores", d["errors"])
for i, e in enumerate(d["events"]):
    if e["src"] == "NHC": print("NHC", e["name"], e["level"], e.get("kmh"), len(e["poly"]), [k for k, v in d["hazards"].items() if any(x[0] == i for x in v)][:30])
for h in d["huelgas"]: print("H", h["fecha"], h["ccs"], h["icaos"], "|", h["t"][:110], "|", h["src"])
