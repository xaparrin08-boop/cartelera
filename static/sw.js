// Service worker: la app abre al instante aunque el servidor esté despertando.
const V = "cartelera-v3", IMG = "cartelera-img";
const SHELL = ["/", "/static/logo.svg", "/static/manifest.json", "/static/icon-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(V).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V && k !== IMG).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

// HTML: red primero, pero si tarda más de 1,8 s (servidor dormido) se sirve la copia guardada.
async function netFirst(r, ms) {
  const c = await caches.open(V);
  const net = fetch(r).then(res => { if (res.ok) c.put("/", res.clone()); return res; });
  const first = await Promise.race([net.catch(() => null), new Promise(res => setTimeout(() => res(null), ms))]);
  if (first) return first;
  return (await c.match("/")) || net;
}
async function swr(r) {
  const c = await caches.open(V), hit = await c.match(r);
  const net = fetch(r).then(res => { if (res.ok) c.put(r, res.clone()); return res; }).catch(() => hit);
  return hit || net;
}
async function cacheFirst(r) {
  const c = await caches.open(IMG), hit = await c.match(r.url);
  if (hit) return hit;
  try {  // CORS para poder guardarla sin ocupar cuota de "opaca"
    const res = await fetch(r.url, { mode: "cors" });
    if (res.ok) {
      c.put(r.url, res.clone());
      c.keys().then(ks => { if (ks.length > 400) ks.slice(0, ks.length - 400).forEach(k => c.delete(k)); });
    }
    return res;
  } catch { return fetch(r); }
}

self.addEventListener("fetch", e => {
  const r = e.request;
  if (r.method !== "GET") return;
  const u = new URL(r.url);
  if (u.origin === location.origin) {
    if (r.mode === "navigate") return e.respondWith(netFirst(r, 1800));
    if (u.pathname.startsWith("/static/")) return e.respondWith(swr(r));
    return;
  }
  if (/image\.tmdb\.org|cdn\.movieofthenight\.com/.test(u.host)) e.respondWith(cacheFirst(r));
});
