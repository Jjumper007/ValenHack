# Guía de trámites València

Extensión de Chrome que te guía, botón a botón, hasta el trámite que quieres hacer
en la web del Ayuntamiento de València.

La web municipal es grande y los trámites están repartidos entre www.valencia.es y la
Sede Electrónica. Con la guía basta con decir qué necesitas («me he mudado de piso»,
«quiero pagar los tributos municipales»). Un murciélago aparece en la esquina, vuela
hasta el enlace que tienes que pulsar en cada página y te acompaña hasta la ficha
del trámite.

> **Proyecto de hackathon.** Es un prototipo hecho en pocas horas para demostrar la
> idea: funciona en local, en tu propio ordenador y con tu cuenta de Claude. Varias
> decisiones (el servidor local, el rastreo de la web, el dictado de Chrome…) se
> tomaron por rapidez y no son las que usaríamos para ponerlo en marcha de verdad.
> Ver [Si se llevara a producción](#si-se-llevara-a-producción).

## Qué hace

- **Entiende la pregunta.** Claude elige, de entre los ~950 trámites y páginas
  informativas de la web, los que encajan con lo que pides, aunque no uses las
  palabras exactas de la web.
- **Calcula el camino.** Sobre un mapa de la web (un grafo de páginas y enlaces
  generado con un rastreador), busca la ruta más corta desde la página en la que
  estás hasta la del trámite. El camino sale siempre del mapa: no se inventan enlaces.
- **Te lleva de la mano.** En cada página resalta el enlace que hay que pulsar y el
  icono vuela hasta él. Si el enlace está dentro de un menú plegado, primero te
  señala el menú. Si te sales del camino, recalcula la ruta desde donde estés.
- **Por voz.** Pulsa **Alt+G**, di qué trámite buscas y empieza sola.
- **Rápida.** Precarga en segundo plano la página del siguiente paso y guarda las
  respuestas de Claude, así que repetir una pregunta es instantáneo.

## Cómo está hecho

```
backend/                     Python, solo biblioteca estándar (+ anthropic opcional)
  crawler.py                 rastrea la web y genera el mapa
  buscador.py                pregunta -> trámite -> camino de clics
  servidor.py                expone el buscador en http://127.0.0.1:8765
  graphify-out/graph.json    el mapa (páginas, enlaces y tipo de cada página)
  preguntas_demo.txt         preguntas para precalentar la caché
extension/                   extensión de Chrome (Manifest V3)
  content.js                 el asistente: icono, panel, voz y guía paso a paso
  background.js              habla con el servidor local y recibe el atajo de teclado
  ui.css, page.css           estilos del asistente y del resaltado
  icons/                     fotogramas del murciélago
```

1. La extensión envía la pregunta y la URL actual al servidor local.
2. El servidor pide a Claude que elija el trámite y calcula la ruta en el grafo.
   Cada paso es «en la página X, pulsa el enlace Y».
3. La extensión guarda la guía en `chrome.storage`. En cada página localiza el enlace
   del paso (por su `href`, por su destino o por su texto), lo resalta y mueve el
   icono a su lado. El clic lo hace el usuario.

## Instalación

### Requisitos

- **Google Chrome** (o Edge u otro navegador basado en Chromium).
- **Python 3.10 o superior.** Compruébalo con `python --version`.
- **Claude**, para entender preguntas en lenguaje natural. Vale cualquiera de las dos
  opciones; sin ninguna, funciona con una búsqueda por palabras, más limitada:
  - [Claude Code](https://claude.com/claude-code) instalado y con sesión iniciada
    (usa tu plan de Claude; no necesita clave de API), o
  - una clave de la API de Anthropic en la variable `ANTHROPIC_API_KEY` y el paquete
    `anthropic` (`pip install -r backend/requirements.txt`).

### 1. Descargar el proyecto

```
git clone <url-del-repositorio>
cd <carpeta-del-repositorio>
```

### 2. Arrancar el servidor

```
cd backend
python servidor.py
```

Debe mostrar `Servidor de la guía en http://127.0.0.1:8765 (941 páginas en el mapa)`.
Déjalo abierto mientras uses la extensión. Para comprobar que responde, abre
http://127.0.0.1:8765/salud en el navegador.

Opciones útiles:

| Opción | Para qué |
|---|---|
| `--modelo haiku` | gasta menos cuota del plan de Claude |
| `--precalentar preguntas_demo.txt` | busca esas preguntas antes de arrancar (ver *Presentaciones*) |
| `--sin-cache` | no reutiliza búsquedas anteriores |
| `--puerto 9000` | otro puerto (hay que cambiarlo también en `extension/background.js` y `manifest.json`) |

### 3. Cargar la extensión en Chrome

1. Abre `chrome://extensions`.
2. Activa **Modo de desarrollador** (arriba a la derecha).
3. Pulsa **Cargar descomprimida** y elige la carpeta `extension/` del proyecto.

Aparecerá «Guía de trámites València» en la lista. Si cambias el código de la
extensión, pulsa su botón **Actualizar** y recarga la página de valencia.es.

### 4. Probarla

1. Abre https://www.valencia.es/cas/inicio. Verás el murciélago en la esquina
   inferior derecha.
2. Púlsalo y escribe, por ejemplo, «quiero el certificado de empadronamiento», o
   pulsa **Alt+G** y dilo en voz alta.
3. Pulsa **Guíame** y sigue al murciélago.

## Uso

- **Por texto:** pulsa el murciélago, escribe y pulsa *Buscar* (o Intro).
- **Por voz:** pulsa **Alt+G** en cualquier página de valencia.es (o *Hablar* en el
  panel). Al dejar de hablar, busca lo que ha entendido. Pulsar Alt+G otra vez
  termina de escuchar; Escape cancela.
  - La primera vez Chrome pide permiso de micrófono, una vez para www.valencia.es y
    otra para sede.valencia.es.
  - El dictado usa la Web Speech API de Chrome: el audio lo transcriben servidores de
    Google, así que necesita conexión.
  - El atajo se cambia en `chrome://extensions/shortcuts`.
- **Durante la guía:** pulsa el murciélago para ver u ocultar el paso actual, o
  *Cancelar guía* para dejarla. La guía sigue aunque cambies de página o abras el
  enlace en otra pestaña.

## Presentaciones

Para que la demo vaya fluida:

1. Escribe las preguntas de la demo en `backend/preguntas_demo.txt` (una por línea)
   y arranca el servidor con
   ```
   python servidor.py --precalentar preguntas_demo.txt
   ```
   Las respuestas de Claude se guardan en `backend/cache_busquedas.json`. En la demo,
   la misma pregunta (aunque cambien mayúsculas, tildes o signos) responde al
   instante. La caché se descarta sola si se regenera el mapa.
2. Haz cada ruta una vez en la misma ventana de Chrome antes de presentar, para que
   las imágenes de la web queden en la caché del navegador.

Durante la guía, la extensión ya precarga la página del siguiente paso. Dentro de
www.valencia.es la página aparece al instante; hacia la Sede solo se adelanta el
HTML. Se puede comprobar en DevTools → *Application* → *Speculative loads*.

## Regenerar el mapa

El mapa incluido es una foto de la web en un momento dado. Para actualizarlo:

```
cd backend
python crawler.py https://www.valencia.es/cas/inicio
```

Tarda unos minutos y escribe `graphify-out/graph.json` (reinicia el servidor después).

- **Qué guarda:** solo las páginas útiles para llegar a un trámite. Descarta
  noticias, agenda, páginas legales, la versión en valenciano, documentos y webs no
  oficiales (ver `DEFAULT_EXCLUDES` en `crawler.py`).
- **Horario de la Sede:** la Sede Electrónica pide en su robots.txt que solo se la
  rastree de 02:00 a 07:45 UTC, y el crawler lo respeta. Fuera de ese horario sus
  fichas entran en el mapa como destinos, pero no se exploran sus páginas internas.
- **Páginas con login:** para incluirlas, inicia sesión en el navegador, exporta las
  cookies en formato `cookies.txt` (por ejemplo con la extensión «Get cookies.txt
  LOCALLY») y ejecuta
  `python crawler.py https://www.valencia.es/cas/inicio --cookies cookies.txt`.
  Ese archivo da acceso a tu cuenta: no lo compartas ni lo subas al repositorio (ya
  está en `.gitignore`).
- **Contenido variable:** la web cambia de contenido entre visitas (portada,
  campañas), así que dos rastreos seguidos pueden dar mapas algo distintos.

## Problemas frecuentes

| Síntoma | Solución |
|---|---|
| «No puedo conectar con el servidor local» | Arranca `python servidor.py` en `backend/`. |
| No aparece el murciélago | Comprueba que la extensión está activada en `chrome://extensions` y recarga la página. Solo funciona en www.valencia.es y sede.valencia.es. |
| Alt+G no hace nada | Puede que otra extensión ya use ese atajo: revísalo en `chrome://extensions/shortcuts`. |
| «No tengo permiso para usar el micrófono» | Candado de la barra de direcciones → permitir micrófono. |
| La búsqueda tarda 6-8 s | Es la llamada a Claude. Usa `--modelo haiku` o precalienta la caché. |
| «Esta página no está en mi mapa» | La página no está en el grafo: vuelve a la portada o regenera el mapa. |

## Si se llevara a producción

Este prototipo prioriza tener algo funcionando para la demo. Para ofrecerlo a la
ciudadanía lo plantearíamos de otra forma:

- **Integrado en la web, sin extensión.** Un asistente incrustado por el propio
  Ayuntamiento en valencia.es y la Sede llega a todo el mundo, también en el móvil,
  sin instalar nada.
- **Servidor en la nube, no en local.** El buscador correría en un servidor del
  proyecto con un llm instructor local. El modo actual con Claude Code usa la cuenta personal de quien lo ejecuta,
  que solo sirve para desarrollar y hacer demos.
- **Datos oficiales en lugar de rastrear.** El mapa se construiría a partir del
  catálogo de procedimientos de la Sede y de la estructura de la web que mantiene el
  Ayuntamiento, no adivinándolo con un rastreador. Hoy el mapa depende de cómo se ve
  la web en el momento del rastreo y de su horario de rastreo permitido.
- **Enlaces estables.** Para señalar botones se acordarían con quien mantiene la web
  identificadores fijos en los enlaces importantes. Ahora se localizan por `href` o
  por texto, y un rediseño de la web los rompería.
- **Zona privada sin cookies personales.** Los pasos que requieren identificarse
  (Cl@ve, certificado) se mapearían con una cuenta de pruebas o con información del
  propio Ayuntamiento, nunca con la sesión de una persona.
- **Voz con privacidad.** El dictado de Chrome envía el audio a Google. En un
  servicio público usaríamos reconocimiento de voz en el propio dispositivo o en un
  servidor propio, con la información de protección de datos correspondiente.
- **Medir la calidad.** Un conjunto de preguntas reales con su trámite correcto
  para comprobar cuántas acierta el buscador antes de cada cambio.

## Icono

`extension/icons/murcielago-base.svg` es el murciélago parado, y
`murcielago-vuelo1.svg` / `murcielago-vuelo2.svg` los dos fotogramas de alas que se
alternan al volar. Los tres comparten el mismo encuadre cuadrado, y el fotograma 2
está desplazado para que la cabeza coincida con la del 1 (si no, el murciélago
tiembla al aletear). Si se sustituyen, hay que conservar esos dos requisitos.
