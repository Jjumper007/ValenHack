"""Rastrea una web y genera un grafo de navegación orientado a trámites, en JSON.

Nodos = páginas, aristas = enlaces (<a href>) entre ellas. El grafo está
pensado para guiar a un usuario desde la página en la que está hasta la del
trámite que quiere hacer, así que:

  * Cada página se clasifica (campo "kind") como:
      - "procedimiento": ficha de un procedimiento en la Sede Electrónica
        (donde se inicia el trámite de verdad),
      - "tramite": página informativa de trámites, ayudas, cita previa...,
      - "login": página de identificación (Cl@ve, certificado...),
      - "navegacion": cualquier otra página (menús, portadas...).
  * Se descartan noticias, agenda, páginas legales, otro idioma, documentos,
    enlaces de cerrar sesión y webs no oficiales (ver DEFAULT_EXCLUDES).
  * Al final solo se conservan los trámites y las páginas que están en algún
    camino más corto desde la página inicial hasta un trámite (--keep-all
    desactiva esta poda).

Login: haz login en tu navegador, exporta las cookies en formato cookies.txt
(Netscape) y pásalas con --cookies. Cada página se pide primero sin sesión;
si redirige a la página de login se marca "requires_login": true y se vuelve
a pedir con tus cookies. El archivo de cookies da acceso a tu cuenta: no lo
compartas ni lo subas a ningún repositorio.

Uso:
    python crawler.py https://www.valencia.es/cas/inicio
    python crawler.py https://www.valencia.es/cas/inicio --cookies cookies.txt

Por defecto escribe graphify-out/graph.json, que el visor Graphify 3D de
VS Code encuentra automáticamente.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.cookiejar import MozillaCookieJar
from urllib.parse import quote, unquote, urljoin, urldefrag, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from urllib.robotparser import RobotFileParser

USER_AGENT = "Mozilla/5.0 (compatible; hackaton-link-graph/1.0)"
SKIP_EXTENSIONS = re.compile(
    r"\.(pdf|jpe?g|png|gif|svg|webp|ico|zip|rar|docx?|xlsx?|pptx?|odt|ods|mp[34]|avi|css|js|xml|json)$",
    re.IGNORECASE,
)

# --- Dominios ------------------------------------------------------------------
# Además del dominio de la URL inicial, dominios oficiales que entran en el
# grafo. Se pueden añadir más con --allow-domain. El resto se consideran webs
# no oficiales, igual que las páginas que al descargarlas redirigen fuera.
DEFAULT_EXTRA_DOMAINS = ["sede.valencia.es"]

# Parámetros de query que no cambian la página (solo el idioma): se quitan del
# id del nodo para no duplicar páginas.
DROP_QUERY_PARAMS = {"lang"}
# Query que se añade al descargar páginas de un dominio, para pedirlas en
# castellano (la Sede sirve valenciano por defecto).
FETCH_EXTRA_QUERY = {"sede.valencia.es": "lang=1"}

# --- Exclusiones ---------------------------------------------------------------
# Enlaces que no se añaden al grafo porque no ayudan a llegar a un trámite.
# Cada regla es una regex que se compara con la ruta + query (decodificada).
# Se pueden añadir más con --exclude o desactivar todas con --no-default-excludes.
DEFAULT_EXCLUDES = {
    # Secciones de noticias y sus artículos (castellano y valenciano)
    "noticias": r"/(actualidad|actualitat|noticias|noticies|news|prensa|premsa|sala-de-prensa)(/|$)",
    # Eventos de la agenda de la ciudad
    "agenda": r"/(agenda-de-la-ciudad|agenda-de-la-ciutat)(/|$)",
    # Enlaces de cambio de idioma: no son páginas, redirigen a la misma en otro idioma
    "cambio de idioma": r"/c/portal/update_language",
    # Versión en valenciano de www (la Sede se normaliza con DROP_QUERY_PARAMS)
    "otro idioma": r"(^|/)val(/|$)",
    # Repositorio de documentos (PDF, etc.), no son páginas de navegación
    "documentos": r"^/documents/",
    # Páginas legales que aparecen en el pie de todas las páginas
    "legal": r"/(aviso-legal|avis-legal|politica-(de-)?privacidad|politica-privacitat|politica-(de-)?cookies"
             r"|accesibilidad|accessibilitat)(/|$)",
    # El mapa web enlaza a todo: crearía atajos que no son la navegación real
    "mapa web": r"/(mapa-web|mapa-del-web)(/|$)",
    # Sitios de pruebas del CMS publicados por error
    "pruebas": r"/(sandbox|test-[^/]*)(/|$)",
    # Estados internos de portlets de Liferay (fichas de un listado, paginación,
    # filtros): son la misma página con otro contenido, el usuario los elige
    # dentro de ella. Sin esta regla, oposiciones y concursos inundan el grafo.
    "estado de portlet": r"[?&]p_p_id=",
    # Con sesión iniciada, visitar estos enlaces cerraría la sesión
    "cerrar sesión": r"(logout|logoff|log-out|cerrar-?sesion|tancar-?sessio|desconect)",
}

# --- Clasificación -------------------------------------------------------------
# Se comparan con dominio + ruta (decodificada); gana la primera que coincida.
LOGIN_PATTERN = r"(authclave|/login|/auth[^/]*\.xhtml|clave\.gob\.es|/c/portal/login)"
KIND_RULES = [
    ("procedimiento", r"^sede\.valencia\.es/sede/registro/procedimiento/"),
    ("login", LOGIN_PATTERN),
    ("tramite", r"/(tramites|tramits|ayudas-subvenciones|campañas-municipales|campanyes-municipals"
                r"|cita-previa)(/|$)"),
]
TARGET_KINDS = {"procedimiento", "tramite"}


class LinkParser(HTMLParser):
    """Extrae el <title> y los enlaces <a href> con su texto."""

    def __init__(self):
        super().__init__()
        self.links = []  # (href, texto)
        self.title = ""
        self._in_title = False
        self._current_href = None
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._current_href = href
                self._current_text = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._current_href is not None:
            text = " ".join("".join(self._current_text).split())
            self.links.append((self._current_href, text))
            self._current_href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._current_href is not None:
            self._current_text.append(data)


def normalize(url, base):
    """Convierte un href en URL absoluta canónica, o None si no interesa."""
    url, _ = urldefrag(urljoin(base, url.strip()))
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    if SKIP_EXTENSIONS.search(parsed.path):
        return None
    # Codifica caracteres no ASCII (ñ, tildes...) sin tocar los ya escapados
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if len(path) > 1:
        path = path.rstrip("/")
    params = [p for p in parsed.query.split("&")
              if p and p.split("=", 1)[0].lower() not in DROP_QUERY_PARAMS]
    query = "?" + quote("&".join(params), safe="=&%+/:,;@!$'()*-._~") if params else ""
    # http y https del mismo dominio se tratan como la misma página
    return f"https://{parsed.netloc.lower()}{path}{query}"


def fetch(url, timeout, opener, retries=2):
    """Descarga una página. Devuelve (status, html | None, url_final)."""
    extra = FETCH_EXTRA_QUERY.get(urlparse(url).netloc)
    if extra:
        url += ("&" if "?" in url else "?") + extra
    req = Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with opener.open(req, timeout=timeout) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if "text/html" not in ctype:
                    return resp.status, None, resp.url
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.status, resp.read().decode(charset, errors="replace"), resp.url
        except Exception as exc:  # HTTPError, URLError, timeout...
            status = getattr(exc, "code", None) or str(exc)
            # Solo se reintentan errores de servidor y de red
            if isinstance(status, int) and status < 500:
                break
            time.sleep(1 + attempt)
    return status, None, url


class SiteRules:
    """robots.txt de cada dominio, incluida la directiva Visit-time.

    Visit-time (p. ej. "0200-0745", en UTC) es la franja horaria en la que el
    sitio pide ser rastreado; fuera de ella no se descargan sus páginas.
    """

    def __init__(self, opener, timeout):
        self._opener = opener
        self._timeout = timeout
        self._sites = {}
        self._lock = threading.Lock()

    def _load(self, domain):
        robots = RobotFileParser()
        window = None
        try:
            req = Request(f"https://{domain}/robots.txt", headers={"User-Agent": USER_AGENT})
            with self._opener.open(req, timeout=self._timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            robots.parse(text.splitlines())
            m = re.search(r"^\s*Visit-time:\s*(\d{4})\s*-\s*(\d{4})", text, re.IGNORECASE | re.MULTILINE)
            if m:
                window = (m.group(1), m.group(2))
        except Exception:
            robots.parse([])  # sin robots.txt: todo permitido
        return robots, window

    def check(self, url):
        """None si se puede descargar la URL, o el motivo por el que no."""
        domain = urlparse(url).netloc
        with self._lock:
            if domain not in self._sites:
                self._sites[domain] = self._load(domain)
        robots, window = self._sites[domain]
        if not robots.can_fetch(USER_AGENT, url):
            return "bloqueada por robots.txt"
        if window:
            now = datetime.now(timezone.utc).strftime("%H%M")
            start, end = window
            inside = start <= now <= end if start <= end else (now >= start or now <= end)
            if not inside:
                return f"fuera del horario de rastreo de robots.txt (Visit-time {start}-{end} UTC)"
        return None


def host_path(url):
    parsed = urlparse(url)
    return parsed.netloc + unquote(parsed.path)


def is_login_page(url):
    return re.search(LOGIN_PATTERN, host_path(url), re.IGNORECASE) is not None


def classify(url):
    target = host_path(url)
    for kind, pattern in KIND_RULES:
        if re.search(pattern, target, re.IGNORECASE):
            return kind
    return "navegacion"


def excluded_by(url, exclude_rules):
    """Nombre de la regla de exclusión que cumple la URL, o None."""
    parsed = urlparse(url)
    target = unquote(parsed.path) + (f"?{unquote(parsed.query)}" if parsed.query else "")
    for name, pattern in exclude_rules.items():
        if pattern.search(target):
            return name
    return None


def bfs(adjacency, sources):
    """Distancias (en saltos) desde cualquiera de los nodos origen."""
    dist = {s: 0 for s in sources}
    queue = deque(sources)
    while queue:
        u = queue.popleft()
        for v in adjacency.get(u, ()):
            if v not in dist:
                dist[v] = dist[u] + 1
                queue.append(v)
    return dist


def keep_paths_to_targets(start, nodes, edges):
    """Nodos que están en algún camino más corto desde start hasta un trámite.

    Un nodo v está en un camino más corto start -> t si
    dist(start, v) + dist(v, t) == dist(start, t).
    """
    forward, backward = {}, {}
    for s, t in edges:
        forward.setdefault(s, []).append(t)
        backward.setdefault(t, []).append(s)
    from_start = bfs(forward, [start])
    keep = set()
    for target, node in nodes.items():
        if node["kind"] not in TARGET_KINDS or target not in from_start:
            continue
        to_target = bfs(backward, [target])
        total = from_start[target]
        keep.update(v for v, d in to_target.items() if from_start.get(v, total + 1) + d == total)
    return keep


def add_viewer_fields(nodes):
    """Añade label/community/community_name para el visor Graphify 3D.

    Las comunidades son los tipos de página (procedimiento, trámite...).
    """
    kinds = Counter(node["kind"] for node in nodes.values())
    community_ids = {kind: i for i, (kind, _) in enumerate(kinds.most_common())}
    for url, node in nodes.items():
        node["label"] = node.get("title") or unquote(urlparse(url).path)
        node["community"] = community_ids[node["kind"]]
        node["community_name"] = node["kind"]
        node["file_type"] = f"página (profundidad {node['depth']})"


def crawl(start_url, max_depth, workers, timeout, max_pages, exclude_rules,
          extra_domains=(), cookie_jar=None, keep_all=False):
    start = normalize(start_url, start_url)
    allowed_domains = {urlparse(start).netloc, *(d.lower() for d in extra_domains)}
    exclude_rules = {name: re.compile(p, re.IGNORECASE) for name, p in exclude_rules.items()}
    excluded = {}  # url -> motivo, para las estadísticas

    anon_opener = build_opener()
    session_opener = build_opener(HTTPCookieProcessor(cookie_jar)) if cookie_jar is not None else None
    site_rules = SiteRules(anon_opener, timeout)

    nodes = {start: {"id": start, "depth": 0}}
    edges = {}  # (source, target) -> {"text", "href"}
    frontier = [start]

    def visit(url):
        """Devuelve (url, status, html, url_final, requires_login, motivo_no_descargada)."""
        reason = site_rules.check(url)
        if reason:
            return url, None, None, url, False, reason
        status, html, final = fetch(url, timeout, anon_opener)
        requires_login = is_login_page(final) and not is_login_page(url)
        if requires_login:
            if not session_opener:
                return url, status, None, final, True, None
            status, html, final = fetch(url, timeout, session_opener)
            if is_login_page(final):
                print(f"AVISO: la sesión no da acceso a {url} (¿cookies caducadas?)", file=sys.stderr)
                return url, status, None, final, True, None
        return url, status, html, final, requires_login, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Se descargan las páginas de profundidad < max_depth; sus enlaces
        # generan los nodos de la siguiente profundidad.
        for depth in range(max_depth):
            next_frontier = []
            for url, status, html, final, requires_login, skipped in pool.map(visit, frontier):
                node = nodes[url]
                if skipped:
                    node["not_fetched"] = skipped
                    continue
                node["status"] = status
                if requires_login:
                    node["requires_login"] = True
                # Si la URL redirige, el usuario verá la final en su navegador:
                # se guarda para poder reconocer la página por cualquiera de las dos
                final_norm = normalize(final, final)
                if final_norm and final_norm != url and not requires_login:
                    node["redirects_to"] = final_norm
                if not is_login_page(final) and urlparse(final).netloc.lower() not in allowed_domains:
                    # Enlace interno que en realidad lleva a una web externa
                    excluded[url] = "web no oficial"
                    continue
                if html is None:
                    continue
                parser = LinkParser()
                parser.feed(html)
                node["title"] = " ".join(parser.title.split())
                for href, text in parser.links:
                    target = normalize(href, final)
                    if not target or target == url:
                        continue
                    if urlparse(target).netloc not in allowed_domains and not is_login_page(target):
                        excluded.setdefault(target, "web no oficial")
                        continue
                    reason = excluded.get(target) or excluded_by(target, exclude_rules)
                    if reason:
                        excluded[target] = reason
                        continue
                    edges.setdefault((url, target), {"text": text, "href": href})
                    if target not in nodes:
                        if len(nodes) >= max_pages:
                            continue
                        nodes[target] = {"id": target, "depth": depth + 1}
                        # Las páginas de login no se rastrean: son el final del camino
                        if not is_login_page(target):
                            next_frontier.append(target)
            frontier = next_frontier
            print(f"profundidad {depth}: {len(nodes)} nodos, {len(edges)} aristas", file=sys.stderr)

    # Quita las páginas que resultaron redirigir fuera de los dominios oficiales
    nodes = {url: node for url, node in nodes.items() if url not in excluded}
    edges = {(s, t): e for (s, t), e in edges.items() if s in nodes and t in nodes}
    for url, node in nodes.items():
        node["kind"] = classify(url)
    crawled_nodes = len(nodes)

    if not keep_all:
        keep = keep_paths_to_targets(start, nodes, edges)
        nodes = {url: node for url, node in nodes.items() if url in keep}
        edges = {(s, t): e for (s, t), e in edges.items() if s in nodes and t in nodes}

    add_viewer_fields(nodes)
    return {
        "directed": True,
        "start_url": start,
        "max_depth": max_depth,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "logged_in": cookie_jar is not None,
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "nodes_before_pruning": crawled_nodes,
            "kinds": dict(Counter(n["kind"] for n in nodes.values())),
            "requires_login": sum(1 for n in nodes.values() if n.get("requires_login")),
            "not_fetched": dict(Counter(n["not_fetched"] for n in nodes.values() if "not_fetched" in n)),
            "excluded_urls": dict(Counter(excluded.values())),
            # Dominios externos descartados, por si alguno es oficial (--allow-domain)
            "excluded_domains": dict(Counter(
                urlparse(u).netloc for u, reason in excluded.items() if reason == "web no oficial"
            ).most_common()),
        },
        "nodes": list(nodes.values()),
        "edges": [
            {"source": s, "target": t, "text": e["text"], "relation": e["text"], "href": e["href"]}
            for (s, t), e in edges.items()
        ],
    }


def load_cookies(path):
    jar = MozillaCookieJar(path)
    # Las cookies de sesión no tienen caducidad: hay que cargarlas igualmente
    jar.load(ignore_discard=True, ignore_expires=True)
    print(f"Cookies cargadas: {len(jar)} ({', '.join(sorted({c.domain for c in jar}))})", file=sys.stderr)
    return jar


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=15)
    ap.add_argument("--max-pages", type=int, default=5000)
    # graphify-out/graph.json es la ruta que el visor Graphify 3D detecta solo
    ap.add_argument("-o", "--output", default=os.path.join("graphify-out", "graph.json"))
    ap.add_argument("--exclude", action="append", default=[], metavar="REGEX",
                    help="excluye las URLs cuya ruta cumpla esta regex (repetible)")
    ap.add_argument("--no-default-excludes", action="store_true",
                    help="no aplica las exclusiones por defecto (DEFAULT_EXCLUDES)")
    ap.add_argument("--allow-domain", action="append", default=[], metavar="DOMINIO",
                    help="otro dominio oficial que incluir en el grafo (repetible)")
    ap.add_argument("--cookies", metavar="COOKIES_TXT",
                    help="cookies de tu sesión en formato Netscape, para rastrear páginas con login")
    ap.add_argument("--keep-all", action="store_true",
                    help="no poda el grafo: conserva también páginas que no llevan a trámites")
    args = ap.parse_args()

    exclude_rules = {} if args.no_default_excludes else dict(DEFAULT_EXCLUDES)
    exclude_rules.update({f"--exclude {p}": p for p in args.exclude})
    cookie_jar = load_cookies(args.cookies) if args.cookies else None

    graph = crawl(args.url, args.depth, args.workers, args.timeout, args.max_pages,
                  exclude_rules, DEFAULT_EXTRA_DOMAINS + args.allow_domain,
                  cookie_jar, args.keep_all)
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    stats = {k: v for k, v in graph["stats"].items() if k != "excluded_domains"}
    print(f"Guardado en {args.output}: {stats}", file=sys.stderr)


if __name__ == "__main__":
    main()
