import json, urllib.request, random, string
topic = "adoc-sondeo-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
def post(body):
    req = urllib.request.Request("https://ntfy.sh", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: print("OK", r.status, r.read()[:300])
    except urllib.error.HTTPError as e: print("HTTP", e.code, e.read()[:400])
    except Exception as e: print("ERR", e)
post({"topic": topic, "title": "prueba", "message": "sin mail"})
post({"topic": topic, "title": "prueba", "message": "con mail", "email": "prueba@example.com"})
