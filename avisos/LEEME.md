# Avisos al celular

Cada archivo `.txt` de esta carpeta es la lista de una persona. La página lo arma solo con el botón
**Guardar mi lista en GitHub** (en "Mis aeropuertos en operación" → "Configurar avisos").

Formato:

```
canal: adoc-xxxxxxxx      ← el canal al que te suscribís en la app ntfy
SAEZ                      ← un aeropuerto por línea (IATA u OACI); lo que va después de # se ignora
EZE
```

Cada 15 minutos se revisa el semáforo de esos aeropuertos y se manda un aviso por
[ntfy](https://ntfy.sh) (gratis, sin registro) cuando uno pasa a Afectado o Severo, o se normaliza.
Para dejar de recibir avisos, borrá el archivo o desuscribite en la app.
