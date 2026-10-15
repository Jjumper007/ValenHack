# Guía de trámites València

Asistente para la web del Ayuntamiento de València: preguntas qué trámite quieres
hacer y un icono te va llevando, botón a botón, hasta la página donde se hace.

```
backend/
  crawler.py       rastrea la web y genera el mapa (grafo de páginas y enlaces)
  buscador.py      pregunta -> trámite -> camino de clics sobre el grafo
  servidor.py      expone el buscador en http://127.0.0.1:8765 para la extensión
  graphify-out/graph.json   el mapa
extension/         extensión de Chrome (Manifest V3)
```

## Probarlo en local

1. **Arranca el servidor** (necesita Python 3.10+):
   ```
   cd backend
   python servidor.py
   ```
   Para entender preguntas en lenguaje natural usa Claude a través de Claude Code
   (`claude -p`, con tu cuenta) o de la API si hay `ANTHROPIC_API_KEY`; si no hay
   ninguno, usa una búsqueda léxica local. `python servidor.py --modelo haiku`
   gasta menos cuota del plan.

2. **Carga la extensión** en Chrome:
   `chrome://extensions` → activa *Modo de desarrollador* → *Cargar descomprimida*
   → elige la carpeta `extension/`.

3. Abre https://www.valencia.es/cas/inicio, pulsa el icono de la esquina inferior
   derecha y escribe, por ejemplo, «quiero el certificado de empadronamiento».

Tras cambiar el código de la extensión, pulsa *Actualizar* en `chrome://extensions`
y recarga la página.

## Cómo funciona

1. El icono envía la pregunta y la URL actual al servidor (vía `background.js`).
2. El servidor elige el trámite y calcula el camino más corto en el grafo:
   cada paso es «en la página X, pulsa el enlace Y».
3. La extensión guarda la guía en `chrome.storage` y, en cada página, busca el
   enlace del paso (por `href`, por destino o por texto), lo resalta y mueve el
   icono a su lado. El usuario hace el clic.
4. Si el usuario se sale del camino, se recalcula desde la página actual
   (`POST /ruta`, sin volver a llamar a Claude).

## Preparar una presentación

1. Escribe las preguntas de la demo en `backend/preguntas_demo.txt` y arranca el
   servidor precalentando la caché:
   ```
   python servidor.py --precalentar preguntas_demo.txt
   ```
   Cada búsqueda con Claude se guarda en `backend/cache_busquedas.json`: repetir la
   pregunta (aunque cambien mayúsculas, tildes o signos) responde al instante y sin
   gastar cuota. La caché se descarta sola si se regenera el grafo.
2. Haz la ruta una vez completa en la misma ventana de Chrome antes de presentar,
   para que imágenes, CSS y JS de la web queden en la caché del navegador.

Durante la guía, la extensión pide a Chrome que precargue la página del siguiente
paso (Speculation Rules): dentro de www.valencia.es se renderiza entera en segundo
plano y aparece al instante al pulsar; hacia sede.valencia.es solo se adelanta el
HTML. Se puede comprobar en DevTools → Application → Speculative loads.

## Preguntar por voz

Pulsa **Alt+G** en cualquier página de valencia.es (o el botón «Hablar» del panel),
di qué trámite quieres hacer y, al dejar de hablar, la guía lo busca. Pulsar el atajo
otra vez termina de escuchar; Escape cancela. El atajo se cambia en
`chrome://extensions/shortcuts`.

Usa la Web Speech API de Chrome: la primera vez pide permiso de micrófono (una vez
para www.valencia.es y otra para sede.valencia.es) y el audio lo transcriben los
servidores de Google, así que necesita conexión.

## Icono

`extension/icons/murcielago-base.svg` (parado) y `murcielago-vuelo1/2.svg` (alas
arriba / abajo, que se alternan al volar). Se generan a partir de los SVG originales
de diseño con el mismo encuadre cuadrado para los tres, y el fotograma 2 desplazado
para que la cabeza coincida con la del 1 (si no, el murciélago tiembla al aletear).
Si se sustituyen, hay que conservar esos dos requisitos.

## Regenerar el mapa

```
cd backend
python crawler.py https://www.valencia.es/cas/inicio
python crawler.py https://www.valencia.es/cas/inicio --cookies cookies.txt   # con sesión iniciada
```
La Sede Electrónica solo permite rastrear de 02:00 a 07:45 UTC (`Visit-time` de su
robots.txt); el crawler lo respeta. `cookies.txt` da acceso a tu cuenta: no lo subas.

## Pendiente

- Rastrear la Sede dentro de su horario y con sesión, para completar los pasos
  que requieren identificarse.
