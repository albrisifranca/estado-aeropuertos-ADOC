import json, subprocess, sys, os
r = subprocess.run([sys.executable, "scripts/referencia.py"])
d = json.load(open("data/referencia.json")); R = json.load(open("data/rutas.json"))
print("errores", d["errors"], "tam rutas", os.path.getsize("data/rutas.json"))
A = R["aerolineas"]; red = R["red"]; m = red["KMIA"]
nm = lambda cs: sorted({A[c >> 2] + ("📦" if c & 1 else "") for c in cs})
for k in ["SAEZ", "SBGR", "SCEL", "SKBO", "LEMD", "EGLL", "KDFW", "MPTO", "SUMU"]: print("MIA>", k, nm(m.get(k, [])))
jp = [k for k in red if k[:2] in ("RJ", "RO")]
out = {}
for h, ls in m.items():
    for j in jp:
        if j in red.get(h, {}):
            for x in {c >> 2 for c in ls} & {c >> 2 for c in red[h][j]}: out.setdefault(A[x], []).append(h + ">" + j)
for k, v in sorted(out.items(), key=lambda x: -len(x[1]))[:15]: print(k, v[:4])
print("aerolineas MIA", d["aerolineas"].get("KMIA"), "EZE", d["aerolineas"].get("SAEZ"))
