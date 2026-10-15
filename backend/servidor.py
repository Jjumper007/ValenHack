"""Servidor local para la extensión de Chrome.

Expone buscador.py por HTTP en 127.0.0.1 (solo accesible desde este ordenador):

    GET  /salud                 -> {"ok": true, "nodos": N}
    POST /guiar  {"pregunta": "...", "desde": "<url actual>", "modo"?, "modelo"?}
                                -> JSON de buscador.a_json (destino + pasos)
    POST /ruta   {"desde": "<url actual>", "destino": "<url destino>"}
                                -> mismo formato, sin volver a buscar (para
                                   recalcular cuando el usuario se sale del camino)

Las búsquedas con Claude se guardan en cache_busquedas.json: repetir una
pregunta es instantáneo y no gasta cuota. Para una presentación, precalienta
la caché con las preguntas de la demo (una por línea):

    python servidor.py --precalentar preguntas_demo.txt

Uso:
    python servidor.py              # puerto 8765
    python servidor.py --puerto 9000 --modelo haiku
    python servidor.py --sin-cache
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from buscador import DEFAULT_GRAPH, Guia, a_json


def make_handler(guia, modelo_por_defecto):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _origen_permitido(self):
            # Solo la extensión (o herramientas locales sin Origin, como curl).
            # Así una web cualquiera no puede usar el servidor ni gastar tu cuota de Claude.
            origin = self.headers.get("Origin")
            return origin is None or origin.startswith("chrome-extension://")

        def do_GET(self):
            if self.path == "/salud":
                self._send(200, {"ok": True, "nodos": len(guia.nodes)})
            else:
                self._send(404, {"ok": False, "error": "Ruta no encontrada"})

        def do_POST(self):
            if not self._origen_permitido():
                self._send(403, {"ok": False, "error": "Origen no permitido"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._send(400, {"ok": False, "error": "JSON no válido"})
                return
            try:
                if self.path == "/guiar":
                    pregunta = (body.get("pregunta") or "").strip()
                    if not pregunta:
                        self._send(400, {"ok": False, "error": "Falta la pregunta"})
                        return
                    resultado = guia.guiar(pregunta, body.get("desde"), body.get("modo", "auto"),
                                           3, body.get("modelo") or modelo_por_defecto)
                elif self.path == "/ruta":
                    resultado = guia.ruta_hacia(body.get("desde"), body["destino"])
                else:
                    self._send(404, {"ok": False, "error": "Ruta no encontrada"})
                    return
                self._send(200, a_json(resultado))
            except Exception as exc:
                self._send(500, {"ok": False, "error": str(exc)})

        def log_message(self, fmt, *args):
            sys.stderr.write(f"[servidor] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--puerto", type=int, default=8765)
    ap.add_argument("--grafo", default=DEFAULT_GRAPH)
    ap.add_argument("--modelo", help='modelo por defecto para el modo claude-code (p. ej. "haiku")')
    ap.add_argument("--cache", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_busquedas.json"),
                    help="archivo donde se guardan las búsquedas con Claude")
    ap.add_argument("--sin-cache", action="store_true", help="no reutiliza ni guarda búsquedas")
    ap.add_argument("--precalentar", metavar="ARCHIVO",
                    help="antes de arrancar, busca cada pregunta del archivo (una por línea) para tenerla en caché")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    guia = Guia(args.grafo, None if args.sin_cache else args.cache)
    if guia.cache:
        print(f"Caché: {len(guia.cache)} búsquedas guardadas")
    if args.precalentar:
        with open(args.precalentar, encoding="utf-8") as f:
            preguntas = [linea.strip() for linea in f if linea.strip() and not linea.startswith("#")]
        for pregunta in preguntas:
            r = guia.guiar(pregunta, None, "auto", 3, args.modelo)
            destino = r["opciones"][0]["label"] if r["opciones"] else "(sin resultado)"
            print(f"  {'ya estaba' if r['cacheado'] else 'buscada '}  {pregunta}  ->  {destino}")
    server = ThreadingHTTPServer(("127.0.0.1", args.puerto), make_handler(guia, args.modelo))
    print(f"Servidor de la guía en http://127.0.0.1:{args.puerto} ({len(guia.nodes)} páginas en el mapa)")
    print("Ctrl+C para parar.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
