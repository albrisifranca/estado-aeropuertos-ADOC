import sys
sys.path.insert(0, "scripts")
import referencia as r
t = r.wikitextos(["Miami International Airport", "Heathrow Airport", "Ministro Pistarini International Airport"])
for k, wt in t.items():
    secs = r.seccion_destinos(wt)
    print("=====", k, len(wt), [(a, len(b)) for a, b in secs])
    for a, b in secs[:2]:
        print("-----", a)
        print(b[:1800])
