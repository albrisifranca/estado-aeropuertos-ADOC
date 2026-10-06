// Avisos por mail (notificaciones de GitHub) y al celular con ntfy (https://ntfy.sh, gratis y sin registro).
// El mail sale de GitHub: el aviso se publica como issue del repositorio mencionando al usuario,
// y GitHub le manda el mail a la casilla de su cuenta. ntfy no permite mails sin cuenta.
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
  const ap = { icao: a[0], iata: a[1], name: a[2], city: a[3], cc: a[4], big: a[7] === "L", tz: a[9] };
  byIcao.set(ap.icao, ap); if (ap.iata) byIata.set(ap.iata, ap);
}
const FAA = new Map();
for (const e of (ST.faa && ST.faa.events) || []) { if (!FAA.has(e.icao)) FAA.set(e.icao, []); FAA.get(e.icao).push(e) }
const ZONES = new Map();
for (const z of ST.zones || []) for (const cc of z.countries) { if (!ZONES.has(cc)) ZONES.set(cc, []); ZONES.get(cc).push(z) }
const dn = new Intl.DisplayNames(["es"], { type: "region" });
const countryName = c => { try { return dn.of(c) || c } catch { return c } };

const ctx = vm.createContext({ ST, FAA, ZONES, byIcao, countryName });
vm.runInContext(code + "\n;globalThis.__L = { assess, avisable, motivo, LV, tafRisks, riskWhen };", ctx);
const { assess, avisable, motivo, LV, tafRisks, riskWhen } = ctx.__L;

let prev = {};
try { prev = JSON.parse(fs.readFileSync(prevPath, "utf8")) } catch {}
const next = {};

function leerLista(txt) {
  let canal = null, mail = null, resumen = { hora: 8, tz: "America/Argentina/Buenos_Aires" }; const icaos = [];
  for (let line of txt.split(/\r?\n/)) {
    line = line.replace(/#.*/, "").trim();
    if (!line) continue;
    const m = line.match(/^canal\s*:\s*([A-Za-z0-9_-]{6,64})$/i);
    if (m) { canal = m[1]; continue }
    const u = line.match(/^mail\s*:\s*@?([A-Za-z0-9-]{1,39})$/i);
    if (u) { mail = u[1]; continue }
    const r = line.match(/^resumen\s*:\s*(.+)$/i);
    if (r) {
      const [h, tz] = r[1].trim().split(/\s+/);
      if (/^(no|off|nunca)$/i.test(h)) resumen = null;
      else if (/^\d{1,2}$/.test(h) && +h < 24) resumen = { hora: +h, tz: tz && validTz(tz) ? tz : resumen ? resumen.tz : "America/Argentina/Buenos_Aires" };
      continue;
    }
    const c = line.split(/[\s,;]+/)[0].toUpperCase();
    const ap = byIcao.get(c) || byIata.get(c);
    if (ap && !icaos.includes(ap.icao)) icaos.push(ap.icao);
  }
  return { canal, mail, icaos, resumen };
}

function validTz(tz) { try { new Intl.DateTimeFormat("en", { timeZone: tz }); return true } catch { return false } }
// Fecha (AAAA-MM-DD) y hora locales en una zona horaria.
function local(tz, d = new Date()) {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23" }).formatToParts(d).map(x => [x.type, x.value]));
  return { fecha: `${p.year}-${p.month}-${p.day}`, hora: +p.hour };
}

async function enviar(msg) {
  const r = await fetch(NTFY, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(msg) });
  if (!r.ok) throw new Error(`ntfy ${r.status}`);
}

const GH = process.env.GITHUB_TOKEN, REPO = process.env.GITHUB_REPOSITORY;
async function gh(method, path, body) {
  const r = await fetch(`${process.env.GITHUB_API_URL || "https://api.github.com"}/repos/${REPO}${path}`, { method, body: JSON.stringify(body),
    headers: { Authorization: `Bearer ${GH}`, Accept: "application/vnd.github+json", "Content-Type": "application/json" } });
  if (!r.ok) throw new Error(`github ${r.status} ${(await r.text()).slice(0, 120)}`);
  return r.json();
}
const issues = { ...(prev._issues || {}) };
// Un issue por aeropuerto y persona: cada aviso es un comentario y el título muestra el estado,
// así el asunto del mail dice qué pasó y los mails de un mismo aeropuerto quedan juntos.
async function mandarMail(user, key, msg) {
  if (!GH || !REPO) throw new Error("sin GITHUB_TOKEN");
  const title = "✈ " + msg.title;
  const body = `@${user}\n\n**${msg.title}**\n\n${msg.message.replace(/\n/g, "  \n")}\n\n[Ver en Estado de Aeropuertos](${msg.click})\n\n_Aviso automático. Para dejar de recibirlos, borrá tu lista en la carpeta avisos._`;
  const n = issues[`${user}|${key}`];
  if (n) {
    try { await gh("PATCH", `/issues/${n}`, { title, state: "open" }); await gh("POST", `/issues/${n}/comments`, { body }); return } catch {}
  }
  const it = await gh("POST", "/issues", { title, body });
  issues[`${user}|${key}`] = it.number;
}

const nombre = ap => `${ap.iata || ap.icao} ${ap.city || ap.name}`;

let REF = null;
async function referencia() {
  if (REF) return REF;
  try { const r = await fetch(`https://raw.githubusercontent.com/${REPO || "albrisifranca/estado-aeropuertos-ADOC"}/referencia/referencia.json`); if (r.ok) REF = await r.json() } catch {}
  return REF || (REF = {});
}

// Resumen diario: estado de cada aeropuerto, lo que pronostica el TAF para las próximas 24 h y feriados de hoy y mañana.
async function resumenDiario(icaos) {
  const ref = await referencia(), en24 = Date.now() + 24 * 3600000;
  const filas = icaos.map(icao => {
    const ap = byIcao.get(icao), s = assess(icao), taf = ST.taf && ST.taf[icao];
    const fc = taf ? tafRisks(taf.raw).filter(r => r.from.getTime() < en24) : [];
    const hoy = ap.tz ? local(ap.tz).fecha : new Date().toISOString().slice(0, 10);
    const man = ap.tz ? local(ap.tz, new Date(Date.now() + 864e5)).fecha : "";
    const fer = ((ref.feriados && ref.feriados[ap.cc]) || []).filter(h => h[3] && (h[0] === hoy || h[0] === man));
    return { ap, s, fc, fer, peor: Math.max(s.lv, ...fc.map(r => r.l)) };
  }).sort((a, b) => b.peor - a.peor || b.s.lv - a.s.lv);
  const bola = l => l >= 2 ? "🔴" : l === 1 ? "🟡" : l === 0 ? "🟢" : "⚪";
  const n = l => filas.filter(f => f.s.lv === l).length;
  const conRiesgo = filas.filter(f => f.fc.some(r => r.l >= 2)).length;
  const lineas = filas.map(f => {
    const out = [`${bola(f.s.lv)} **${nombre(f.ap)}**: ${f.s.lv >= 0 ? LV[f.s.lv] : "sin reporte"}${motivo(f.s) ? " · " + motivo(f.s) : ""}`];
    if (f.fc.length) out.push(`   Pronóstico 24 h: ${f.fc.map(r => `${r.l >= 2 ? "⚠️ " : ""}${r.t} (${riskWhen(r)})`).join("; ")}`);
    else if (ST.taf && ST.taf[f.ap.icao]) out.push("   Pronóstico 24 h: sin complicaciones");
    for (const h of f.fer) out.push(`   🎌 Feriado ${h[0] === local(f.ap.tz || "UTC").fecha ? "hoy" : "mañana"}: ${h[1]} (aduana y handling pueden andar lentos)`);
    return out.join("\n");
  });
  const total = filas.length, mal = n(2) + n(3);
  return {
    title: `Resumen del día: ${mal ? `${mal} de ${total} con problemas` : `${total} aeropuertos sin problemas`}${conRiesgo ? `, ${conRiesgo} con riesgo pronosticado` : ""}`,
    message: `🟢 ${n(0)} normales · 🟡 ${n(1)} con precaución · 🔴 ${mal} afectados o severos\n\n${lineas.join("\n\n")}`,
    priority: mal ? 4 : 3, tags: ["clipboard"], click: SITE + "#activos=" + icaos.map(i => byIcao.get(i).iata || i).join(","),
  };
}
let enviados = 0, errores = [];
const resumenes = {};
const archivos = fs.existsSync("avisos") ? fs.readdirSync("avisos").filter(f => f.endsWith(".txt")) : [];

for (const f of archivos) {
  const { canal, mail, icaos, resumen } = leerLista(fs.readFileSync(`avisos/${f}`, "utf8"));
  if (!canal || !icaos.length) continue;
  const clave = mail ? `${canal}|${mail}` : canal;
  const antes = prev[clave] || null, ahora = {}, avisos = [];
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
    avisos.push({ msg: { title: "Avisos activados", message: "Te voy a avisar cuando alguno pase a Afectado o Severo, y cuando se normalice.\n\n" + lineas.join("\n"), priority: 3, tags: ["airplane"], click: SITE } });
  }
  let bienvenidaFallida = false;
  for (const a of avisos.slice(0, MAX_POR_CANAL)) {
    // Cuenta como avisado si salió por al menos un medio; si no, se reintenta en la próxima corrida.
    let ok = false;
    try { await enviar({ topic: canal, ...a.msg }); ok = true } catch (e) { errores.push(`${canal} ntfy: ${e.message}`) }
    if (mail) try { await mandarMail(mail, a.icao || "bienvenida", a.msg); ok = true } catch (e) { errores.push(`${canal} mail: ${e.message}`) }
    if (ok) enviados++;
    else if (a.icao) ahora[a.icao] = a.prevLv; else bienvenidaFallida = true;
  }
  if (!bienvenidaFallida) next[clave] = ahora;

  // Resumen diario por mail a la hora elegida (por defecto 8 de la mañana de Argentina), una vez por día.
  if (mail && resumen && antes) {
    const { fecha, hora } = local(resumen.tz);
    const ultimo = (prev._resumen || {})[clave];
    resumenes[clave] = ultimo;
    if (hora >= resumen.hora && ultimo !== fecha) {
      try {
        await mandarMail(mail, "resumen", await resumenDiario(icaos));
        resumenes[clave] = fecha; enviados++;
      } catch (e) { errores.push(`${canal} resumen: ${e.message}`) }
    }
  }
}

next._issues = issues;
next._resumen = resumenes;
fs.writeFileSync(outPath, JSON.stringify(next));
console.log(`Avisos: ${archivos.length} listas, ${enviados} enviados, errores ${JSON.stringify(errores)}`);
