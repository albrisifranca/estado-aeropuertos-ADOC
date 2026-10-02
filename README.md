# Estado de Aeropuertos

Página web gratuita para consultar en segundos el estado operativo de más de 5.000 aeropuertos del mundo con datos oficiales.

- **Clima operativo (todo el mundo):** METAR y TAF del Aviation Weather Center de NOAA, que reúne los reportes de los servicios meteorológicos de cada país.
- **Demoras y cierres (EE.UU.):** FAA NAS Status: ground stops, programas de demora, demoras de llegada y salida, cierres.
- **Semáforo:** Normal, Precaución, Afectado o Severo, calculado a partir de esos datos (categoría de vuelo, tormentas, viento, programas FAA).
- **Enlaces directos** a la fuente oficial de cada aeropuerto (NOAA, FAA, NOTAM, Eurocontrol, ANAC, DECEA).

## Cómo funciona

Una tarea de GitHub Actions corre cada 15 minutos, descarga los datos con `scripts/update_status.py` (solo Python estándar, sin claves) y publica el sitio en GitHub Pages. Todo es gratis en un repositorio público.

## Puesta en marcha

1. Settings → Pages → Source: **GitHub Actions**.
2. Actions → "Actualizar datos y publicar" → Run workflow.
3. La página queda en `https://<usuario>.github.io/<repositorio>/`. Se puede abrir un aeropuerto directo con `#EZE` o `#SAEZ` al final del enlace.

Datos de aeropuertos: OurAirports (dominio público), aeropuertos medianos y grandes.
