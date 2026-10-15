// Asistente de trámites: icono flotante que pregunta qué quieres hacer y luego
// se desplaza hasta el botón que hay que pulsar en cada página.
//
// La guía activa (destino + pasos) se guarda en chrome.storage.local, así que
// sobrevive a la navegación: al cargar cada página se mira en qué paso está el
// usuario comparando la URL actual con la página de cada paso.

(() => {
  if (window.top !== window || document.getElementById("guia-tramites-host")) return;

  const CLAVE = "guia"; // clave del estado en chrome.storage.local
  const FAB = 96; // tamaño del icono (igual que en ui.css)
  const ESQUINA = 24; // distancia del icono a la esquina inferior derecha
  const MARGEN = 12;
  // Si el destino del icono se desplaza más que esto de un fotograma a otro (la
  // web hace scroll sola, cambia el objetivo...), el murciélago vuela hasta allí
  // aleteando en lugar de seguirlo pegado
  const SALTO = 90;

  // ---------------------------------------------------------------- utilidades

  // Misma URL canónica que crawler.normalize: https, host en minúsculas, sin
  // fragmento, sin parámetro de idioma y sin barra final.
  function normalizar(href) {
    try {
      const u = new URL(href, location.href);
      const params = u.search
        .slice(1)
        .split("&")
        .filter((p) => p && p.split("=")[0].toLowerCase() !== "lang");
      let ruta = u.pathname || "/";
      if (ruta.length > 1) ruta = ruta.replace(/\/+$/, "");
      return `https://${u.host.toLowerCase()}${ruta}${params.length ? "?" + params.join("&") : ""}`;
    } catch {
      return null;
    }
  }

  const limpiar = (t) => (t || "").replace(/\s+/g, " ").trim().toLowerCase();

  // Crea elementos sin innerHTML: los textos vienen del servidor y de la web
  function h(tag, props = {}, ...hijos) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v);
    }
    for (const hijo of hijos.flat()) if (hijo !== null && hijo !== undefined && hijo !== false) el.append(hijo);
    return el;
  }

  const leer = async () => (await chrome.storage.local.get(CLAVE))[CLAVE] || null;
  const guardar = (valor) => chrome.storage.local.set({ [CLAVE]: valor });
  const borrar = () => chrome.storage.local.remove(CLAVE);

  function pedir(tipo, datos) {
    return new Promise((resolve) =>
      chrome.runtime.sendMessage({ tipo, datos }, (respuesta) =>
        resolve(respuesta || { ok: false, error: chrome.runtime.lastError?.message || "Sin respuesta" })
      )
    );
  }

  // ---------------------------------------------------------------- interfaz

  const host = h("div", { id: "guia-tramites-host" });
  document.documentElement.append(host);
  const raiz = host.attachShadow({ mode: "open" });
  // Murciélago: "base" parado y dos fotogramas de alas que ui.css alterna al volar
  const fotograma = (archivo, clase) =>
    h("img", { class: clase, src: chrome.runtime.getURL(`icons/${archivo}.svg`), alt: "" });
  const fab = h(
    "button",
    { class: "fab", type: "button", "aria-label": "Guía de trámites", title: "Guía de trámites" },
    h(
      "span",
      { class: "giro" },
      h(
        "span",
        { class: "sprite" },
        fotograma("murcielago-base", "base"),
        fotograma("murcielago-vuelo1", "vuelo1"),
        fotograma("murcielago-vuelo2", "vuelo2")
      )
    )
  );
  const bocadillo = h("div", { class: "bocadillo", role: "dialog", "aria-live": "polite", hidden: "" });
  const hoja = h("link", { rel: "stylesheet", href: chrome.runtime.getURL("ui.css") });
  // Hasta que carga ui.css el icono no es fixed y las medidas no valen: no se
  // muestra nada antes (si no, el bocadillo se coloca fuera de la pantalla)
  const estilosListos = new Promise((resolve) => {
    hoja.addEventListener("load", resolve, { once: true });
    hoja.addEventListener("error", resolve, { once: true });
  });
  raiz.append(hoja, bocadillo, fab);

  let estado = null; // guía activa: {pregunta, destino, pasos}
  let objetivo = null; // elemento junto al que debe estar el icono (null = esquina)
  let reintento = null; // setInterval mientras el enlace no está visible

  // Todo el contenido va con la misma cabecera (nombre + cerrar)
  function mostrar(...contenido) {
    bocadillo.replaceChildren(
      h(
        "div",
        { class: "cabecera" },
        h("span", { class: "marca" }, "Guía de trámites"),
        h("button", { class: "cerrar", type: "button", "aria-label": "Cerrar", title: "Cerrar", onclick: ocultar }, "×")
      ),
      h("div", { class: "cuerpo" }, ...contenido.flat().filter(Boolean))
    );
    bocadillo.hidden = false;
    colocarBocadillo();
    requestAnimationFrame(colocarBocadillo); // con el contenido nuevo ya maquetado
  }

  function ocultar() {
    bocadillo.hidden = true;
  }

  // Encima del icono si cabe (si no, debajo), con la cola apuntando al murciélago
  function colocarBocadillo() {
    if (bocadillo.hidden) return;
    const f = fab.getBoundingClientRect();
    const b = bocadillo.getBoundingClientRect();
    const ancho = document.documentElement.clientWidth;
    let top = f.top - b.height - MARGEN;
    let lado = "arriba";
    if (top < MARGEN) {
      top = Math.min(f.bottom + MARGEN, innerHeight - b.height - MARGEN);
      lado = "abajo";
    }
    const left = Math.max(MARGEN, Math.min(f.left + f.width / 2 - b.width + 48, ancho - b.width - MARGEN));
    bocadillo.style.top = `${Math.max(MARGEN, top)}px`;
    bocadillo.style.left = `${left}px`;
    bocadillo.dataset.lado = lado;
    const cola = Math.max(24, Math.min(f.left + f.width / 2 - left, b.width - 24));
    bocadillo.style.setProperty("--cola-x", `${cola}px`);
  }

  // Barra de progreso de los pasos
  const progreso = (i) =>
    h(
      "div",
      { class: "paso-num" },
      h("span", {}, `Paso ${i + 1} de ${estado.pasos.length}`),
      h("span", { class: "progreso" }, h("span", { style: `width:${((i + 1) / estado.pasos.length) * 100}%` }))
    );

  const acciones = (...botones) => h("div", { class: "acciones" }, ...botones);
  const boton = (texto, onclick, clase = "secundario") => h("button", { class: clase, type: "button", onclick }, texto);

  // ------------------------------------------- mover el icono hasta el enlace
  //
  // El icono se mueve con un muelle con amortiguamiento crítico: acelera,
  // frena al llegar y nunca se pasa. Posado, el muelle es rígido y sigue al
  // enlace al hacer scroll; si el destino salta lejos, se vuelve blando y el
  // murciélago vuela aleteando. Así nunca se teletransporta.

  const mov = { x: null, y: null, vx: 0, vy: 0, volando: false, previo: null, t: null };

  function destinoActual() {
    if (!objetivo) {
      return {
        x: document.documentElement.clientWidth - ESQUINA - FAB,
        y: innerHeight - ESQUINA - FAB,
      };
    }
    // Junto al enlace: a su derecha, o a su izquierda si no cabe
    const r = objetivo.getBoundingClientRect();
    const ancho = document.documentElement.clientWidth;
    let x = r.right + MARGEN;
    if (x + FAB > ancho - MARGEN) x = r.left - FAB - MARGEN;
    return {
      x: Math.max(MARGEN, Math.min(x, ancho - FAB - MARGEN)),
      y: Math.max(MARGEN, Math.min(r.top + r.height / 2 - FAB / 2, innerHeight - FAB - MARGEN)),
    };
  }

  function despegar() {
    mov.volando = true;
    fab.classList.add("volando");
  }

  function aterrizar() {
    mov.volando = false;
    fab.classList.remove("volando", "izquierda");
  }

  function fotogramaMovimiento(ahora) {
    requestAnimationFrame(fotogramaMovimiento);
    if (objetivo && !objetivo.isConnected) {
      // La página ha rehecho ese trozo del DOM: se busca de nuevo el enlace
      objetivo = null;
      continuarGuia();
    }
    const dt = mov.t === null ? 0 : Math.min(0.05, (ahora - mov.t) / 1000);
    mov.t = ahora;
    const d = destinoActual();
    if (mov.x === null) {
      // Siempre aparece en la esquina: si ya hay un enlace que señalar, vuela hasta él
      mov.x = document.documentElement.clientWidth - ESQUINA - FAB;
      mov.y = innerHeight - ESQUINA - FAB;
      if (Math.hypot(d.x - mov.x, d.y - mov.y) > 20) despegar();
      fab.classList.add("listo");
    }
    if (mov.previo && Math.hypot(d.x - mov.previo.x, d.y - mov.previo.y) > SALTO) despegar();
    mov.previo = d;

    const dx = d.x - mov.x;
    const dy = d.y - mov.y;
    const w = mov.volando ? 3.6 : 16; // rad/s: vuelo suave (~1,5 s) o seguimiento ceñido
    mov.vx += (w * w * dx - 2 * w * mov.vx) * dt;
    mov.vy += (w * w * dy - 2 * w * mov.vy) * dt;
    mov.x += mov.vx * dt;
    mov.y += mov.vy * dt;

    if (mov.volando) {
      // Los fotogramas de vuelo miran a la derecha: se gira según la velocidad
      if (Math.abs(mov.vx) > 40) fab.classList.toggle("izquierda", mov.vx < 0);
      // Casi llegado y despacio: se posa (el muelle rígido termina el último tramo)
      if (Math.hypot(dx, dy) < 4 && Math.hypot(mov.vx, mov.vy) < 60) aterrizar();
    }
    fab.style.translate = `${mov.x.toFixed(1)}px ${mov.y.toFixed(1)}px`;
    colocarBocadillo();
  }

  function soltarObjetivo() {
    if (objetivo) objetivo.classList.remove("guia-tramites-resaltado");
    objetivo = null;
    clearInterval(reintento);
    reintento = null;
  }

  function volverALaEsquina() {
    soltarObjetivo();
  }

  function irA(el) {
    soltarObjetivo();
    objetivo = el;
    el.classList.add("guia-tramites-resaltado");
    el.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    const d = destinoActual();
    if (mov.x !== null && Math.hypot(d.x - mov.x, d.y - mov.y) > 20) despegar();
  }

  // ------------------------------------------ localizar el enlace de un paso

  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && parseFloat(s.opacity) > 0;
  }

  // Por selector (href exacto del rastreo), por destino del enlace y por texto,
  // por si la web ha cambiado el href o el enlace se genera con JavaScript
  function candidatos(paso) {
    const lista = [];
    try {
      lista.push(...document.querySelectorAll(paso.selector));
    } catch {
      // selector no válido en esta página: se sigue con los demás criterios
    }
    const siguiente = normalizar(paso.url_siguiente);
    const texto = limpiar(paso.texto);
    for (const a of document.querySelectorAll("a[href]")) {
      if (normalizar(a.href) === siguiente || limpiar(a.textContent) === texto) lista.push(a);
    }
    return [...new Set(lista)];
  }

  // Si el enlace está dentro de un bloque plegado (collapse, pestaña de
  // Bootstrap, aria-controls...), devuelve el control visible que lo despliega.
  // Se recorren los contenedores de fuera hacia dentro: primero hay que abrir
  // el más exterior que siga cerrado.
  function disparador(enlace) {
    const contenedores = [];
    for (let p = enlace.parentElement; p && p !== document.body; p = p.parentElement) {
      if (p.id) contenedores.unshift(p);
    }
    for (const c of contenedores) {
      const abierto = c.classList.contains("show") || c.classList.contains("active") || c.classList.contains("in");
      if (abierto && visible(c)) continue;
      const id = CSS.escape(c.id);
      const controles = document.querySelectorAll(
        `[href="#${id}"], [data-target="#${id}"], [data-bs-target="#${id}"], [aria-controls="${c.id}"]`
      );
      const control = [...controles].find(
        (t) => visible(t) && t.getAttribute("aria-expanded") !== "true"
      );
      if (control) return control;
    }
    return null;
  }

  // ------------------------------------------- precargar la siguiente página
  //
  // Speculation Rules: se le dice a Chrome a qué página va a ir el usuario y la
  // carga (y pinta) en segundo plano, así que al pulsar el enlace aparece al
  // instante. Prerender solo dentro del mismo origen; hacia otro (la Sede) Chrome
  // solo permite prefetch del HTML.

  let reglas = null; // <script type="speculationrules"> actual
  let precargada = null;

  function precargar(url) {
    if (url === precargada) return;
    reglas?.remove();
    reglas = null;
    precargada = url;
    if (!url || !HTMLScriptElement.supports?.("speculationrules")) return;
    const mismoOrigen = new URL(url, location.href).origin === location.origin;
    reglas = h("script", { type: "speculationrules" });
    reglas.textContent = JSON.stringify({
      [mismoOrigen ? "prerender" : "prefetch"]: [{ source: "list", urls: [url], eagerness: "immediate" }],
    });
    (document.head || document.documentElement).append(reglas);
  }

  // ---------------------------------------------------------- flujo de la guía

  async function continuarGuia(recalculado = false) {
    estado = await leer();
    if (!estado) return;
    const aqui = normalizar(location.href);
    // Una página puede verse con su URL o con la URL a la que redirige (alias)
    const esAqui = (pagina) => [pagina.url, ...(pagina.alias || [])].some((u) => normalizar(u) === aqui);
    if (esAqui(estado.destino)) return llegado();

    const i = estado.pasos.findLastIndex((p) => esAqui(p.pagina));
    if (i !== -1) return guiarPaso(i);

    // Página desconocida, pero con el enlace de algún paso a la vista (p. ej.
    // una redirección que el mapa no conoce): se sigue desde ese paso
    // Con run_at document_start la página puede estar aún cargando: para buscar
    // enlaces en ella hace falta al menos el HTML completo
    if (document.readyState === "loading") {
      await new Promise((resolve) => document.addEventListener("DOMContentLoaded", resolve, { once: true }));
      return continuarGuia(recalculado);
    }
    const j = estado.pasos.findLastIndex((p) => candidatos(p).some(visible));
    if (j !== -1) return guiarPaso(j);

    // El usuario no está en ninguna página del camino: se recalcula desde aquí
    // (sin volver a llamar a Claude, el destino ya se conoce)
    if (recalculado) return fueraDelMapa();
    volverALaEsquina();
    mostrar(h("p", { class: "texto" }, "Te has salido del camino. Recalculando…"));
    const r = await pedir("ruta", { desde: location.href, destino: estado.destino.url });
    if (!r.ok || !r.pasos) return error(r.error || "No encuentro un camino desde esta página.");
    await guardar({ ...estado, pasos: r.pasos });
    return continuarGuia(true);
  }

  function guiarPaso(i) {
    const paso = estado.pasos[i];
    // No se precargan páginas de login: abrirían una sesión de Cl@ve en segundo plano
    precargar(paso.requiere_login ? null : paso.url_siguiente);
    const contenido = [
      progreso(i),
      h("p", { class: "texto" }, "Pulsa ", h("span", { class: "boton-nombre" }, `«${paso.texto}»`)),
      paso.requiere_login &&
        h("div", { class: "login" }, "En la siguiente página tendrás que identificarte (Cl@ve o certificado digital)."),
    ];
    const pie = [h("p", { class: "suave" }, `Destino: ${estado.destino.titulo}`), acciones(boton("Cancelar guía", cancelar))];

    const encontrados = candidatos(paso);
    const enlace = encontrados.find(visible);
    if (enlace) {
      mostrar(contenido, pie);
      irA(enlace);
      return;
    }
    // Existe pero está plegado: se señala primero el control que lo despliega
    const control = encontrados.map(disparador).find(Boolean);
    if (control) {
      const nombre = limpiar(control.textContent) ? control.textContent.replace(/\s+/g, " ").trim() : "la sección";
      mostrar(
        progreso(i),
        h("p", { class: "texto" }, "Primero despliega ", h("span", { class: "boton-nombre" }, `«${nombre}»`)),
        h("p", { class: "suave" }, `Dentro está «${paso.texto}».`),
        pie
      );
      irA(control);
    } else {
      // No está en la página o aún no se ha cargado: se espera en la esquina
      volverALaEsquina();
      const ayuda = encontrados.length
        ? "Está dentro de un menú o una sección plegada: ábrelo y te lo señalo."
        : document.readyState === "loading"
          ? "La página aún se está cargando: en cuanto aparezca te lo señalo."
          : "Aún no lo veo en esta página. Si tarda, búscalo por su nombre.";
      mostrar(contenido, h("p", { class: "suave" }, ayuda), pie);
    }
    // Cuando aparezca el enlace (o cambie lo que hay que desplegar), se vuelve a guiar
    reintento = setInterval(() => {
      const ahora = candidatos(paso);
      if (ahora.some(visible) || (ahora.map(disparador).find(Boolean) || null) !== (control || null)) guiarPaso(i);
    }, 500);
  }

  function llegado() {
    precargar(null);
    volverALaEsquina();
    mostrar(
      h("p", { class: "titulo" }, "¡Has llegado!"),
      h("p", { class: "texto" }, estado.destino.titulo),
      h("p", { class: "suave" }, "Desde esta página puedes hacer el trámite."),
      acciones(boton("Terminar", cancelar, "primario"))
    );
  }

  function fueraDelMapa() {
    volverALaEsquina();
    const inicio = estado.pasos[0]?.pagina.url;
    mostrar(
      h("p", { class: "texto" }, "Esta página no está en mi mapa de la web."),
      h("p", { class: "suave" }, "Vuelve a la portada y te guío desde allí."),
      acciones(
        boton("Cancelar guía", cancelar),
        inicio && boton("Ir a la portada", () => (location.href = inicio), "primario")
      )
    );
  }

  async function cancelar() {
    precargar(null);
    await borrar();
    estado = null;
    volverALaEsquina();
    ocultar();
  }

  // --------------------------------------------------- pregunta y propuesta

  function pantallaPregunta() {
    const area = h("textarea", {
      placeholder: "Ej.: quiero el certificado de empadronamiento",
      "aria-label": "¿Qué trámite quieres hacer?",
    });
    const enviar = () => {
      const pregunta = area.value.trim();
      if (pregunta) buscar(pregunta);
      else area.focus();
    };
    area.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        enviar();
      }
    });
    const pista = h("p", { class: "suave pista" });
    mostrar(
      h("p", { class: "titulo" }, "¿Qué trámite quieres hacer?"),
      area,
      pista,
      acciones(
        Reconocimiento && h("button", { class: "secundario voz", type: "button", onclick: escuchar }, "🎙️ Hablar"),
        boton("Buscar", enviar, "primario")
      )
    );
    area.focus();
    // El atajo lo puede cambiar el usuario en chrome://extensions/shortcuts
    if (Reconocimiento) {
      pedir("atajo").then((r) => {
        if (r.atajo) pista.textContent = `Consejo: pulsa ${r.atajo} en cualquier página para preguntar por voz.`;
      });
    }
  }

  // ------------------------------------------------------------------- voz
  //
  // Dictado con la Web Speech API de Chrome. Se lanza con el atajo de la
  // extensión (background.js -> mensaje "escuchar") o con el botón «Hablar»;
  // al terminar de hablar se busca directamente lo que se ha entendido.

  const Reconocimiento = window.SpeechRecognition || window.webkitSpeechRecognition;
  let escucha = null; // reconocimiento en curso

  function alternarEscucha() {
    if (escucha) escucha.stop(); // segunda pulsación: termina y busca lo oído
    else escuchar();
  }

  function escuchar() {
    if (!Reconocimiento) {
      return error("Este navegador no permite el dictado por voz. Escribe la pregunta, por favor.");
    }
    if (escucha) return;
    const rec = new Reconocimiento();
    rec.lang = "es-ES";
    rec.interimResults = true; // se va mostrando lo que entiende mientras hablas
    rec.continuous = false; // termina solo cuando dejas de hablar
    escucha = rec;

    let texto = "";
    let fallo = null;
    let cancelado = false;
    const cancelar = () => {
      cancelado = true;
      rec.abort();
    };
    rec.cancelar = cancelar; // para Escape
    const vivo = h("p", { class: "transcripcion" }, "…");
    fab.classList.add("escuchando");
    mostrar(
      h("p", { class: "titulo" }, "Te escucho…"),
      h("p", { class: "suave" }, "Di qué trámite quieres hacer. Cuando dejes de hablar, lo busco."),
      vivo,
      acciones(
        boton("Cancelar", () => {
          cancelar();
          ocultar();
        }),
        boton("Escribir", () => {
          cancelar();
          pantallaPregunta();
        })
      )
    );

    rec.onresult = (e) => {
      texto = [...e.results].map((r) => r[0].transcript).join("").trim();
      vivo.textContent = texto || "…";
    };
    rec.onerror = (e) => {
      fallo = e.error;
    };
    rec.onend = () => {
      escucha = null;
      fab.classList.remove("escuchando");
      if (cancelado) return;
      if (texto) return buscar(texto);
      if (fallo === "not-allowed" || fallo === "service-not-allowed") {
        return error(
          "No tengo permiso para usar el micrófono. Pulsa el icono del candado de la barra de direcciones y permite el micrófono."
        );
      }
      if (fallo === "network") return error("El dictado por voz necesita conexión a internet. Escribe la pregunta, por favor.");
      mostrar(
        h("p", { class: "texto" }, "No te he oído."),
        acciones(boton("Escribir", pantallaPregunta), boton("Volver a intentarlo", escuchar, "primario"))
      );
    };
    rec.start();
  }

  async function buscar(pregunta) {
    fab.classList.add("buscando"); // aletea en su sitio mientras busca
    mostrar(h("p", { class: "texto" }, "Buscando el trámite…"), h("p", { class: "suave" }, pregunta));
    const r = await pedir("guiar", { pregunta, desde: location.href });
    fab.classList.remove("buscando");
    if (!r.ok) return error(r.error || r.aclaracion || "No he encontrado ningún trámite que encaje.");
    propuesta(r);
  }

  function propuesta(r) {
    const mejor = { ...r.destino, pasos: r.pasos };
    const otras = r.alternativas || [];
    const empezar = async (op) => {
      if (!op.pasos) return error("No encuentro un camino hasta esa página.");
      await guardar({
        pregunta: r.pregunta,
        destino: { url: op.url, alias: op.alias || [], titulo: op.titulo },
        pasos: op.pasos,
      });
      continuarGuia();
    };
    mostrar(
      h("p", { class: "titulo" }, mejor.titulo),
      mejor.motivo && h("p", { class: "texto" }, mejor.motivo),
      r.aclaracion && h("p", { class: "suave" }, r.aclaracion),
      otras.length &&
        h(
          "div",
          {},
          h("p", { class: "suave" }, "¿No es esto? Otras opciones:"),
          h(
            "ul",
            { class: "alternativas" },
            otras.map((op) =>
              h("li", {}, h("button", { type: "button", title: op.motivo || "", onclick: () => empezar(op) }, op.titulo))
            )
          )
        ),
      acciones(boton("Otra pregunta", pantallaPregunta), boton("Guíame", () => empezar(mejor), "primario"))
    );
  }

  function error(mensaje) {
    volverALaEsquina();
    mostrar(
      h("p", { class: "texto error" }, mensaje),
      acciones(boton("Cerrar", ocultar), boton("Preguntar de nuevo", pantallaPregunta, "primario"))
    );
  }

  // ------------------------------------------------------------------ eventos

  fab.addEventListener("click", () => {
    if (!bocadillo.hidden) return ocultar();
    if (estado) {
      bocadillo.hidden = false; // durante la guía, vuelve a mostrar el paso actual
      colocarBocadillo();
    } else {
      pantallaPregunta();
    }
  });

  raiz.addEventListener("keydown", (e) => {
    if (e.key === "Escape") ocultar();
  });

  // Mientras escucha, Escape cancela aunque el foco esté en la página
  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key === "Escape" && escucha) {
        escucha.cancelar();
        ocultar();
      }
    },
    true
  );

  // Atajo de teclado de la extensión (lo recibe background.js)
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.tipo === "escuchar") estilosListos.then(alternarEscucha);
  });

  window.addEventListener("resize", colocarBocadillo);

  // Si la guía se cancela o termina en otra pestaña, esta también la deja
  chrome.storage.onChanged.addListener((cambios, area) => {
    if (area === "local" && CLAVE in cambios && !cambios[CLAVE].newValue && estado) {
      estado = null;
      volverALaEsquina();
      ocultar();
    }
  });

  estilosListos.then(() => {
    requestAnimationFrame(fotogramaMovimiento);
    continuarGuia();
  });
})();
