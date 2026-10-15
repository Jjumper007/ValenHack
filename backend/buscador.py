"""Buscador de trámites sobre el grafo de navegación generado por crawler.py.

Dada una pregunta en lenguaje natural ("quiero el certificado de empadronamiento")
y la página en la que está el usuario, devuelve la página del trámite y el
camino de clics para llegar a ella.

Modos de búsqueda:
  * "lexico": búsqueda local por similitud de texto (n-gramas de caracteres,
    tolera variantes como empadronarme / empadronamiento). Sin coste ni red.
  * "claude-code": Claude elige el trámite del catálogo entendiendo la intención
    ("me he mudado" -> cambio de domicilio en el padrón), a través de la CLI de
    Claude Code (`claude -p`). Consume del plan de tu cuenta de Claude, sin API.
  * "claude": lo mismo con la API de Anthropic (paquete `anthropic` y
    ANTHROPIC_API_KEY), para cuando la usen otras personas.
Por defecto ("auto") usa la API si hay credenciales, si no Claude Code si
está instalado, y si no el léxico.

En todos los casos el modelo solo elige el destino: el camino se calcula
siempre con una búsqueda en anchura sobre el grafo, así que nunca se inventan
enlaces.

Uso:
    python buscador.py "quiero el certificado de empadronamiento"
    python buscador.py "me he mudado" --desde https://www.valencia.es/cas/tramites
    python buscador.py "pagar una multa" --modo lexico --json
"""

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unicodedata
from collections import Counter, defaultdict, deque
from urllib.parse import urljoin

from crawler import TARGET_KINDS, normalize

DEFAULT_GRAPH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "graphify-out", "graph.json")
CLAUDE_MODEL = "claude-opus-5-5"

# Respuesta que se pide a Claude: ids del catálogo, de más a menos adecuado
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "opciones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "motivo": {"type": "string"}},
                "required": ["id", "motivo"],
                "additionalProperties": False,
            },
        },
        "aclaracion": {"type": ["string", "null"]},
    },
    "required": ["opciones", "aclaracion"],
    "additionalProperties": False,
}

# Textos de enlace que no describen el destino
GENERIC_ANCHOR = re.compile(
    r"^(\(\d{2}/\d{2}/\d{4}\)\s*)?(enlace al tr[aá]mite.*|m[aá]s informaci[oó]n|saltar al contenido.*"
    r"|enlace|enlla[cç]|web|aqu[ií]|ver m[aá]s|\+ ?info.*|\+ ?m[eé]s informaci[oó].*|acceder"
    r"|tr[aá]mite en la sede.*|sede electr[oó]nica)\.?$",
    re.IGNORECASE,
)
# Sufijos de <title> que repiten todas las páginas
TITLE_SUFFIX = re.compile(r"\s*[|-]\s*(Ayuntamiento de Valencia|València|Sede Electr[oó]nica).*$", re.IGNORECASE)
STOPWORDS = set(
    "a al algo como con de del el en es esta este hacer la las lo los me mi mis necesito para por puedo "
    "que quiero se sobre su sus tengo un una uno y yo donde cómo como hago tramite trámite".split()
)


def clave_pregunta(pregunta):
    """Pregunta normalizada para la caché: sin tildes, mayúsculas ni signos."""
    return " ".join(re.findall(r"[a-z0-9ñ]+", fold(pregunta)))


def fold(text):
    """Minúsculas y sin tildes, para comparar textos."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def features(text):
    """Palabras y n-gramas de caracteres (4) de cada palabra, sin stopwords."""
    feats = Counter()
    for word in re.findall(r"[a-z0-9]+", fold(text)):
        if word in STOPWORDS or len(word) < 2:
            continue
        feats["w:" + word] += 1
        padded = f" {word} "
        for i in range(len(padded) - 3):
            feats[padded[i:i + 4]] += 1
    return feats


class Guia:
    def __init__(self, graph_path=DEFAULT_GRAPH, cache_path=None):
        with open(graph_path, encoding="utf-8") as f:
            graph = json.load(f)
        # Caché de búsquedas con Claude (pregunta -> trámites elegidos). Solo vale
        # para este grafo: si se regenera, se descarta.
        self.version = graph.get("generated_at")
        self.cache_path = cache_path
        self._cache_lock = threading.Lock()
        self.cache = {}
        if cache_path and os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                guardada = json.load(f)
            if guardada.get("grafo") == self.version:
                self.cache = guardada.get("busquedas", {})
        self.start = graph["start_url"]
        self.nodes = {n["id"]: n for n in graph["nodes"]}
        # URL a la que redirige una página -> página del grafo (el navegador
        # muestra la final, el grafo la guarda con la URL del enlace)
        self.alias = {n["redirects_to"]: n["id"] for n in graph["nodes"] if n.get("redirects_to")}
        self.out_edges = defaultdict(list)
        anchors = defaultdict(Counter)
        for e in graph["edges"]:
            self.out_edges[e["source"]].append(e)
            if e["text"] and not GENERIC_ANCHOR.match(e["text"]):
                anchors[e["target"]][e["text"]] += 1

        # Catálogo de destinos: trámites y procedimientos, en orden estable
        self.catalog = []
        for url in sorted(self.nodes):
            node = self.nodes[url]
            if node["kind"] not in TARGET_KINDS:
                continue
            label = self._label(node, anchors[url])
            if not label:
                continue
            index_text = " ".join([label, *anchors[url], re.sub(r"[/_.-]", " ", url.split("/", 3)[-1])])
            self.catalog.append({"url": url, "kind": node["kind"], "label": label, "text": index_text})
        self._build_index()

    @staticmethod
    def _label(node, anchors):
        """Nombre legible del destino: el título si es específico, si no el texto de enlace."""
        title = TITLE_SUFFIX.sub("", node.get("title") or "").strip()
        generic_titles = {"", "campañas municipales", "trámites", "sede electrónica"}
        if title.lower() not in generic_titles:
            return title
        if anchors:
            # El más corto suele ser el nombre; los largos son tarjetas con descripción
            return min(anchors, key=len)
        return title

    # --- Búsqueda léxica ------------------------------------------------------

    def _build_index(self):
        docs = [features(item["text"]) for item in self.catalog]
        df = Counter(f for doc in docs for f in doc)
        n = len(docs)
        self.idf = {f: math.log((n + 1) / (c + 1)) + 1 for f, c in df.items()}
        self.vectors = []
        for doc in docs:
            vec = {f: (1 + math.log(c)) * self.idf[f] for f, c in doc.items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1
            self.vectors.append({f: v / norm for f, v in vec.items()})

    def buscar_lexico(self, pregunta, k=5):
        query = {f: (1 + math.log(c)) * self.idf.get(f, 0) for f, c in features(pregunta).items()}
        norm = math.sqrt(sum(v * v for v in query.values())) or 1
        scores = []
        for i, vec in enumerate(self.vectors):
            score = sum(v * vec.get(f, 0) for f, v in query.items()) / norm
            if score > 0:
                scores.append((score, i))
        scores.sort(reverse=True)
        return [{**self.catalog[i], "score": round(s, 3)} for s, i in scores[:k]]

    # --- Búsqueda con Claude --------------------------------------------------

    def _catalog_prompt(self):
        lines = [f"{i}\t{item['kind']}\t{item['label']}" for i, item in enumerate(self.catalog)]
        return (
            "Eres el buscador de una guía de trámites del Ayuntamiento de Valencia. "
            "El usuario describe lo que quiere hacer y tú eliges del catálogo las páginas "
            "que le sirven, de más a menos adecuada (máximo 3).\n"
            "- \"procedimiento\" es la ficha de la Sede Electrónica donde se inicia el trámite; "
            "\"tramite\" es una página informativa (requisitos, ayudas, cita previa).\n"
            "- Elige solo ids que existan en el catálogo. Si ninguno encaja, devuelve la lista vacía "
            "y explica en \"aclaracion\" qué falta o qué pregunta harías al usuario.\n"
            "- \"motivo\" y \"aclaracion\" los lee el usuario: escríbelos en castellano sencillo y "
            "nombra los trámites por su nombre, nunca por su id.\n\n"
            "Catálogo (id, tipo, nombre):\n" + "\n".join(lines)
        )

    def _resultados(self, data, k):
        """Convierte la respuesta de Claude en opciones del catálogo."""
        results = []
        for option in data["opciones"][:k]:
            if 0 <= option["id"] < len(self.catalog):  # descarta ids inventados
                results.append({**self.catalog[option["id"]], "motivo": option["motivo"]})
        return results, data.get("aclaracion")

    def buscar_claude_code(self, pregunta, k=3, modelo=None):
        """Igual que buscar_claude, pero con la sesión de Claude Code (`claude -p`)."""
        # El catálogo no cabe en la línea de comandos de Windows: va en un archivo
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as f:
            f.write(self._catalog_prompt())
            prompt_file = f.name
        cmd = [
            shutil.which("claude"), "-p",
            "--system-prompt-file", prompt_file,
            "--output-format", "json",
            "--json-schema", json.dumps(RESPONSE_SCHEMA),
            # Solo tiene que elegir del catálogo: sin herramientas ni MCP
            "--tools", "", "--strict-mcp-config",
            "--no-session-persistence",
            "--effort", "low",
        ]
        if modelo:
            cmd += ["--model", modelo]
        try:
            proc = subprocess.run(cmd, input=pregunta, capture_output=True, text=True,
                                  encoding="utf-8", timeout=180)
        finally:
            os.unlink(prompt_file)
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"Claude Code no devolvió JSON: {(proc.stderr or proc.stdout)[:500]}")
        if out.get("is_error") or out.get("structured_output") is None:
            raise RuntimeError(f"Claude Code falló: {out.get('result') or out.get('subtype')}")
        return self._resultados(out["structured_output"], k)

    def buscar_claude(self, pregunta, k=3):
        import anthropic

        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
            # El catálogo es igual en todas las consultas: se cachea
            system=[{"type": "text", "text": self._catalog_prompt(), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": pregunta}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude rechazó la consulta")
        data = json.loads(next(b.text for b in response.content if b.type == "text"))
        return self._resultados(data, k)

    # --- Rutas ----------------------------------------------------------------

    def ruta(self, desde, hasta):
        """Lista de clics (aristas) desde la página `desde` hasta `hasta`, o None."""
        prev = {desde: None}
        queue = deque([desde])
        while queue:
            url = queue.popleft()
            if url == hasta:
                break
            for edge in self.out_edges.get(url, ()):
                if edge["target"] not in prev:
                    prev[edge["target"]] = edge
                    queue.append(edge["target"])
        if hasta not in prev:
            return None
        steps = []
        while prev[hasta]:
            steps.append(prev[hasta])
            hasta = prev[hasta]["source"]
        return steps[::-1]

    def _pasos(self, edges):
        pasos = []
        for e in edges:
            target = self.nodes[e["target"]]
            target_title = TITLE_SUFFIX.sub("", target.get("title") or "") or e["target"]
            pasos.append({
                "pagina": e["source"],
                "pagina_alias": self.nodes[e["source"]].get("redirects_to"),
                "titulo_pagina": TITLE_SUFFIX.sub("", self.nodes[e["source"]].get("title") or "") or None,
                # Enlaces sin texto (logos, iconos): se describen por su destino
                "pulsa": e["text"] or f"el enlace (logo o icono) que lleva a \"{target_title}\"",
                "href": e["href"],
                "llegas_a": e["target"],
                "requiere_login": bool(target.get("requires_login")),
            })
        return pasos

    def guiar(self, pregunta, desde=None, modo="auto", k=3, modelo=None):
        """Busca el trámite y devuelve las opciones con su camino desde `desde`.

        `modelo` solo se usa en modo claude-code (p. ej. "haiku" para gastar
        menos cuota del plan); si no se indica, el modelo por defecto de tu cuenta.
        """
        aclaracion = None
        if modo == "auto":
            modo = "claude" if api_disponible() else "claude-code" if shutil.which("claude") else "lexico"
        # Se cachea qué trámites eligió Claude, no el camino: así sirve desde cualquier página
        clave = f"{modo}|{modelo or ''}|{k}|{clave_pregunta(pregunta)}"
        cacheado = modo != "lexico" and clave in self.cache
        if cacheado:
            opciones = [dict(o) for o in self.cache[clave]["opciones"]]
            aclaracion = self.cache[clave]["aclaracion"]
        elif modo == "claude":
            opciones, aclaracion = self.buscar_claude(pregunta, k)
        elif modo == "claude-code":
            opciones, aclaracion = self.buscar_claude_code(pregunta, k, modelo)
        else:
            opciones = self.buscar_lexico(pregunta, k)
        if modo != "lexico" and not cacheado:
            self._guardar_en_cache(clave, opciones, aclaracion)
        resultado = self._con_rutas(pregunta, modo, desde, opciones, aclaracion)
        resultado["cacheado"] = cacheado
        return resultado

    def _guardar_en_cache(self, clave, opciones, aclaracion):
        with self._cache_lock:
            # Sin "text" (texto interno del índice léxico, largo y que no hace falta)
            self.cache[clave] = {
                "opciones": [{c: v for c, v in o.items() if c != "text"} for o in opciones],
                "aclaracion": aclaracion,
            }
            if not self.cache_path:
                return
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"grafo": self.version, "busquedas": self.cache}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.cache_path)

    def ruta_hacia(self, desde, destino_url):
        """Camino hasta un destino ya conocido (sin volver a buscar ni llamar a Claude).

        Para recalcular cuando el usuario se sale de la ruta.
        """
        destino_url = normalize(destino_url, destino_url)
        destino_url = self.alias.get(destino_url, destino_url)
        item = next((c for c in self.catalog if c["url"] == destino_url), None)
        if item is None:
            raise ValueError(f"El destino no está en el mapa: {destino_url}")
        return self._con_rutas(None, "ruta", desde, [dict(item)], None)

    def _con_rutas(self, pregunta, modo, desde, opciones, aclaracion):
        origen = normalize(desde, desde) if desde else self.start
        origen = self.alias.get(origen, origen)
        aviso = None
        if origen not in self.nodes:
            aviso = "La página actual no está en el mapa: la ruta empieza en la portada."
            origen = self.start

        for opcion in opciones:
            edges = self.ruta(origen, opcion["url"])
            if edges is None and origen != self.start:
                # Desde esta página no hay camino en el grafo podado: vía portada
                edges = self.ruta(self.start, opcion["url"])
                opcion["aviso"] = "Sin camino desde la página actual: ve primero a la portada."
            opcion.pop("text", None)
            opcion["pasos"] = self._pasos(edges) if edges is not None else None
            opcion["alias"] = self.nodes[opcion["url"]].get("redirects_to")
            opcion["requiere_login"] = any(p["requiere_login"] for p in opcion["pasos"] or [])
        return {"pregunta": pregunta, "modo": modo, "desde": origen, "aviso": aviso,
                "aclaracion": aclaracion, "opciones": opciones}


def css_attr(value):
    """Escapa un valor para usarlo entre comillas dobles en un selector CSS."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def a_json(resultado):
    """Formato de salida para la extensión de Chrome.

    {
      "ok": true,
      "pregunta": "...",
      "origen": {"url": "...", "en_mapa": true},
      "destino": {"url", "alias", "titulo", "tipo", "motivo"},  # mejor opción
      "pasos": [                                            # camino a seguir, en orden
        {"orden": 1,
         "pagina": {"url", "alias", "titulo"},  # página en la que debe estar el usuario;
                                            # alias = otras URLs suyas (redirecciones)
         "accion": "click",
         "texto": "...",                    # texto del enlace a pulsar
         "href": "...",                     # atributo href tal cual está en el HTML
         "selector": "a[href=\"...\"]",     # para localizar y resaltar el enlace
         "url_siguiente": "...",            # página a la que lleva el clic
         "requiere_login": false}
      ],
      "requiere_login": false,
      "alternativas": [{"url", "titulo", "tipo", "motivo", "pasos"}],
      "aclaracion": null,                   # pregunta o aclaración de Claude, si la hay
      "aviso": null
    }
    """
    def destino(op):
        return {"url": op["url"], "alias": [op["alias"]] if op.get("alias") else [],
                "titulo": op["label"], "tipo": op["kind"], "motivo": op.get("motivo")}

    def pasos(op):
        if op["pasos"] is None:
            return None
        return [
            {
                "orden": n,
                # alias: otras URLs con las que puede verse esta página (redirecciones)
                "pagina": {"url": p["pagina"], "alias": [p["pagina_alias"]] if p["pagina_alias"] else [],
                           "titulo": p["titulo_pagina"]},
                "accion": "click",
                "texto": p["pulsa"],
                "href": p["href"],
                "selector": f'a[href="{css_attr(p["href"])}"]',
                "url_siguiente": urljoin(p["pagina"], p["href"]),
                "requiere_login": p["requiere_login"],
            }
            for n, p in enumerate(op["pasos"], 1)
        ]

    opciones = resultado["opciones"]
    avisos = [a for a in [resultado["aviso"], *(op.get("aviso") for op in opciones[:1])] if a]
    mejor = opciones[0] if opciones else None
    return {
        "ok": mejor is not None,
        "pregunta": resultado["pregunta"],
        "modo": resultado["modo"],
        "cacheado": resultado.get("cacheado", False),  # respuesta guardada, sin llamar a Claude
        "origen": {"url": resultado["desde"], "en_mapa": resultado["aviso"] is None},
        "destino": destino(mejor) if mejor else None,
        "pasos": pasos(mejor) if mejor else None,
        "requiere_login": mejor["requiere_login"] if mejor else False,
        "alternativas": [{**destino(op), "pasos": pasos(op)} for op in opciones[1:]],
        "aclaracion": resultado["aclaracion"],
        "aviso": " ".join(avisos) or None,
    }


def api_disponible():
    """True si se puede usar la API de Anthropic (paquete instalado y credenciales)."""
    if importlib.util.find_spec("anthropic") is None:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def imprimir(resultado):
    print(f"Pregunta: {resultado['pregunta']}  (modo {resultado['modo']})")
    if resultado["aviso"]:
        print(f"Aviso: {resultado['aviso']}")
    if resultado["aclaracion"]:
        print(f"Aclaración: {resultado['aclaracion']}")
    if not resultado["opciones"]:
        print("No he encontrado ningún trámite que encaje.")
    for i, op in enumerate(resultado["opciones"], 1):
        extra = f" — {op['motivo']}" if op.get("motivo") else f" (similitud {op['score']})" if "score" in op else ""
        print(f"\n{i}. {op['label']} [{op['kind']}]{extra}")
        print(f"   {op['url']}")
        if op.get("aviso"):
            print(f"   {op['aviso']}")
        if op["pasos"] is None:
            print("   No hay camino en el mapa hasta esta página.")
            continue
        if not op["pasos"]:
            print("   Ya estás en esta página.")
        for n, paso in enumerate(op["pasos"], 1):
            login = "  (te pedirá identificarte)" if paso["requiere_login"] else ""
            print(f"   Paso {n}: en «{paso['titulo_pagina'] or paso['pagina']}» pulsa «{paso['pulsa']}»{login}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pregunta")
    ap.add_argument("--desde", help="URL de la página en la que está el usuario (por defecto, la portada)")
    ap.add_argument("--modo", choices=["auto", "lexico", "claude-code", "claude"], default="auto")
    ap.add_argument("--modelo", help='modelo para --modo claude-code (p. ej. "haiku", "sonnet", "opus")')
    ap.add_argument("--grafo", default=DEFAULT_GRAPH)
    ap.add_argument("-k", type=int, default=3, help="número de opciones")
    ap.add_argument("--json", action="store_true", help="salida en JSON para la extensión (ver a_json)")
    args = ap.parse_args()
    # Tildes correctas aunque la consola de Windows no esté en UTF-8
    sys.stdout.reconfigure(encoding="utf-8")

    if not args.json:
        imprimir(Guia(args.grafo).guiar(args.pregunta, args.desde, args.modo, args.k, args.modelo))
        return
    try:
        salida = a_json(Guia(args.grafo).guiar(args.pregunta, args.desde, args.modo, args.k, args.modelo))
    except Exception as exc:
        salida = {"ok": False, "pregunta": args.pregunta, "error": str(exc)}
    json.dump(salida, sys.stdout, ensure_ascii=False, indent=2)
    print()
    if "error" in salida:
        sys.exit(1)


if __name__ == "__main__":
    main()
