import subprocess, urllib.request, time
U = "https://albrisifranca.github.io/estado-aeropuertos-ADOC/"
for u in [U, U + "data/status.json", U + "data/airports.json", "https://albrisifranca.github.io/estado-aeropuertos-adoc/", "https://albrisifranca.github.io/estado-aeropuertos/"]:
    t = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as r:
            b = r.read(); print(u, r.status, len(b), round(time.time() - t, 2), "s", r.headers.get("last-modified"), r.headers.get("age"))
            if u == U: print("tiene Rutas:", b"tab-rut" in b, "huelgas:", b"huelgas-panel" in b, "head:", b[:150])
    except Exception as e: print(u, "ERROR", e, getattr(e, "read", lambda: b"")()[:200])
r = subprocess.run(["google-chrome", "--headless=new", "--no-sandbox", "--disable-gpu", "--enable-logging=stderr", "--v=0", "--virtual-time-budget=15000", "--dump-dom", U + "#EZE"], capture_output=True, text=True, timeout=120)
dom = r.stdout
import re
txt = re.sub(r"<style.*?</style>|<script.*?</script>", "", dom, flags=re.S)
txt = re.sub(r"<[^>]+>", " ", txt); txt = re.sub(r"\s+", " ", txt)
print("DOM", len(dom)); print(txt[:1500])
print("CONSOLE:", "\n".join(l for l in r.stderr.splitlines() if "CONSOLE" in l or "Uncaught" in l or "ERROR:" in l)[:3000])
