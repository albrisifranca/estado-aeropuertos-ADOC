// Avisos al celular con ntfy (https://ntfy.sh, gratis y sin registro).
// Lee las listas de avisos/*.txt, calcula el semáforo con la misma lógica de la página
// (bloque LOGICA de index.html) y avisa cuando un aeropuerto entra o sale de Afectado/Severo.
// Uso: node scripts/avisos.mjs <estado-anterior.json> <estado-nuevo.json>
import fs from "node:fs";
import vm from "node:vm";

const [prevPath, outPath] = process.argv.slice(2);
const SITE = (process.env.SITE_URL || "https://albrisifranca.github.io/estado-aeropuertos-ADOC").replace(/\/$/, "") + "/";
const NTFY = process.env.NTFY_URL || "https://ntfy.sh";
const MAX_POR_CANAL = 10;

const html = fs.readFileSync("index.html", "utf8");
const code = html.split("/* LOGICA:INICIO")[1].split("/* LOGICA:FIN */")[0].replace(/^[^\n]*\n/, "");
const ST = JSON.parse(fs.readFileSync("data/status.json", "utf8"));
const AP = JSON.parse(fs.readFileSync("data/airports.json", "utf8")).airports;

const byIcao = new Map(), byIata = new Map();
for (const a of AP) {
  const ap = { icao: a[0], iata: a[1], name: a[2], city: a[3], cc: a[4], big: a[7] === "L" };
  byIcao.set(ap.icao, ap); if (ap.iata) byIata.set(ap.iata, ap);
}
const FAA = new Map();
for (const e of (ST.faa && ST.faa.events) || []) { if (!FAA.has(e.icao)) FAA.set(e.icao, []); FAA.get(e.icao).push(e) }
const ZONES = new Map();
for (const z of ST.zones || []) for (const cc of z.countries) { if (!ZONES.has(cc)) ZONES.set(cc, []); ZONES.get(cc).push(z) }
const dn = new Intl.DisplayNames(["es"], { type: "region" });
const countryName = c => { try { return dn.of(c) || c } catch { return c } };

const ctx = vm.createContext({ ST, FAA, ZONES, byIcao, countryName });
vm.runInContext(code + "\n;globalThis.__L = { assess, avisable, motivo, LV };", ctx);
const { assess, avisable, motivo, LV } = ctx.__L;

let prev = {};
try { prev = JSON.parse(fs.readFileSync(prevPath, "utf8")) } catch {}
const next = {};

function leerLista(txt) {
  let canal = null; const icaos = [];
  for (let line of txt.split(/\r?\n/)) {
    line = line.replace(/#.*/, "").trim();
    if (!line) continue;
    const m = line.match(/^canal\s*:\s*([A-Za-z0-9_-]{6,64})$/i);
    if (m) { canal = m[1]; continue }
    const c = line.split(/[\s,;]+/)[0].toUpperCase();
    const ap = byIcao.get(c) || byIata.get(c);
    if (ap && !icaos.includes(ap.icao)) icaos.push(ap.icao);
  }
  return { canal, icaos };
}

async function enviar(msg) {
  const r = await fetch(NTFY, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(msg) });
  if (!r.ok) throw new Error(`ntfy ${r.status}`);
}

const nombre = ap => `${ap.iata || ap.icao} ${ap.city || ap.name}`;
let enviados = 0, errores = [];
const archivos = fs.existsSync("avisos") ? fs.readdirSync("avisos").filter(f => f.endsWith(".txt")) : [];

for (const f of archivos) {
  const { canal, icaos } = leerLista(fs.readFileSync(`avisos/${f}`, "utf8"));
  if (!canal || !icaos.length) continue;
  const antes = prev[canal] || null, ahora = {}, avisos = [];
  for (const icao of icaos) {
    const s = assess(icao), ap = byIcao.get(icao), p = antes && antes[icao];
    ahora[icao] = s.lv >= 0 ? s.lv : (p ?? -1);
    if (!antes || p == null || !avisable(p, s.lv)) continue;
    // Si una fuente falló, una "mejora" puede ser falsa: sólo se avisan empeoramientos.
    if (s.lv < p && (ST.errors || []).length) { ahora[icao] = p; continue }
    const peor = s.lv > p;
    avisos.push({
      icao, prevLv: p,
      msg: {
        title: `${nombre(ap)}: ${peor ? LV[s.lv] : "se normalizó (" + LV[s.lv] + ")"}`,
        message: (peor ? s.why.filter(w => w.l >= 2).map(w => "• " + w.t).slice(0, 4).join("\n") : `Antes estaba ${LV[p]}.` + (motivo(s) ? `\nAhora: ${motivo(s)}` : ""))
          || motivo(s) || LV[s.lv],
        priority: peor ? (s.lv >= 3 ? 5 : 4) : 3,
        tags: [peor ? (s.lv >= 3 ? "rotating_light" : "warning") : "white_check_mark"],
        click: SITE + "#" + (ap.iata || ap.icao),
      },
    });
  }
  if (!antes) {
    // Lista nueva: un primer aviso con el estado actual, así se sabe que funciona.
    const lineas = icaos.map(i => { const s = assess(i); return `${s.lv >= 2 ? "🔴" : s.lv === 1 ? "🟡" : s.lv === 0 ? "🟢" : "⚪"} ${nombre(byIcao.get(i))}: ${s.lv >= 0 ? LV[s.lv] : "sin reporte"}` });
    avisos.push({ msg: { title: "Avisos activados ✈", message: "Te voy a avisar cuando alguno pase a Afectado o Severo, y cuando se normalice.\n\n" + lineas.join("\n"), priority: 3, tags: ["airplane"], click: SITE } });
  }
  let bienvenidaFallida = false;
  for (const a of avisos.slice(0, MAX_POR_CANAL)) {
    try { await enviar({ topic: canal, ...a.msg }); enviados++ }
    catch (e) {
      errores.push(`${canal}: ${e.message}`);
      // Si no se pudo avisar, se reintenta en la próxima corrida.
      if (a.icao) ahora[a.icao] = a.prevLv; else bienvenidaFallida = true;
    }
  }
  if (!bienvenidaFallida) next[canal] = ahora;
}

fs.writeFileSync(outPath, JSON.stringify(next));
console.log(`Avisos: ${archivos.length} listas, ${enviados} enviados, errores ${JSON.stringify(errores)}`);
