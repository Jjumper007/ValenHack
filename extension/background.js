// Puente entre el content script y el servidor local (backend/servidor.py).
// Las peticiones se hacen desde aquí y no desde la página porque el service
// worker tiene permiso para 127.0.0.1 (host_permissions) y no le afectan el
// CORS ni la CSP de valencia.es.

const SERVIDOR = "http://127.0.0.1:8765";

// Atajo de teclado (manifest "commands"): se pide a la pestaña activa que escuche
chrome.commands.onCommand.addListener((comando, tab) => {
  if (comando === "escuchar" && tab?.id !== undefined) {
    // Falla si la pestaña no es de valencia.es (no hay content script): se ignora
    chrome.tabs.sendMessage(tab.id, { tipo: "escuchar" }).catch(() => {});
  }
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  // El content script no puede leer los atajos: se los da el service worker
  if (msg.tipo === "atajo") {
    chrome.commands.getAll().then((comandos) => {
      sendResponse({ atajo: comandos.find((c) => c.name === "escuchar")?.shortcut || "" });
    });
    return true;
  }
  if (msg.tipo !== "guiar" && msg.tipo !== "ruta") return false;

  fetch(`${SERVIDOR}/${msg.tipo}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(msg.datos),
  })
    .then((r) => r.json())
    .then(sendResponse)
    .catch(() =>
      sendResponse({
        ok: false,
        error: "No puedo conectar con el servidor local. ¿Está ejecutándose backend/servidor.py?",
      })
    );
  return true; // respuesta asíncrona
});
