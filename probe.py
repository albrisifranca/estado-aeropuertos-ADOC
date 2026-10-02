import json, re, urllib.request, urllib.parse, time
UA = "Mozilla/5.0 (estado-aeropuertos)"
def get(url, data=None, headers=None):
    h = {"User-Agent": UA}; h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.headers.get("content-type"), r.read()
print("=== NOTAM search")
for ids in ["UKBB", "SAEZ,UKBB,OIIE,OLBA,LLBG,OSDI,HSSS,OYSN,UUEE", "SAEZ KJFK"]:
    try:
        body = urllib.parse.urlencode({"searchType": 0, "designatorsForLocation": ids, "notamsOnly": "false", "offset": 0}).encode()
        st, ct, raw = get("https://notams.aim.faa.gov/notamSearch/search", body, {"Content-Type": "application/x-www-form-urlencoded"})
        print(ids, st, ct, len(raw))
        d = json.loads(raw)
        print(" keys", list(d.keys()), "total", d.get("totalNotamCount"), "n", len(d.get("notamList") or []))
        L = d.get("notamList") or []
        if L: print(" sample keys", list(L[0].keys())); print(" sample", json.dumps(L[0])[:1200])
        for n in L:
            m = (n.get("icaoMessage") or n.get("traditionalMessage") or "")
            if re.search(r"\b(AD|AP)\s+(AP\s+)?(CLSD|CLOSED)", m.upper()) or "QFALC" in m.upper():
                print(" CLOSURE", n.get("facilityDesignator"), n.get("icaoId"), n.get("startDate"), n.get("endDate"), m[:300].replace("\n"," | "))
        print(" by loc", {k: sum(1 for n in L if (n.get("icaoId") or n.get("facilityDesignator"))==k) for k in set((n.get("icaoId") or n.get("facilityDesignator")) for n in L)})
    except Exception as e:
        print(ids, "ERR", e)
    time.sleep(2)
print("=== EASA CZIB")
for u in ["https://www.easa.europa.eu/en/domains/air-operations/czibs", "https://www.easa.europa.eu/en/domains/air-operations/czibs?page=0"]:
    try:
        st, ct, raw = get(u); t = raw.decode("utf-8", "replace"); print(u, st, len(t))
        for m in list(re.finditer(r"CZIB", t))[:3]: print(" ...", re.sub(r"\s+"," ",t[max(0,m.start()-400):m.start()+600]))
        links = sorted(set(re.findall(r'href="([^"]*czib[^"]*)"', t, re.I)))
        print(" links", len(links), links[:60])
    except Exception as e: print(u, "ERR", e)
print("=== FAA prohibitions")
for u in ["https://www.faa.gov/air_traffic/publications/us_restrictions"]:
    try:
        st, ct, raw = get(u); t = raw.decode("utf-8","replace"); print(u, st, len(t))
        i = t.find("Prohibit"); print(re.sub(r"<[^>]+>"," ",re.sub(r"\s+"," ",t[i:i+6000]))[:4000])
    except Exception as e: print(u, "ERR", e)
