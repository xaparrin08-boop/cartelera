import os, threading, time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import sources, store, logic

HERE = os.path.dirname(__file__)
H6, H12, H24 = 6 * 3600, 12 * 3600, 24 * 3600
FOREVER = 10 ** 10


def cached(key, ttl, fn):
    """Caché con TTL. Si la fuente falla se sirve lo último guardado aunque haya caducado."""
    hit = store.get(key, ttl)
    if hit is not None:
        return hit
    try:
        val = fn()
    except Exception as e:
        stale = store.get(key, FOREVER)
        if stale is not None:
            return stale
        if isinstance(e, RuntimeError):
            raise HTTPException(503, str(e))
        raise HTTPException(502, f"Fuente externa no disponible: {e}")
    store.put(key, val)
    return val


def _home_parts():
    return {
        "np": lambda: cached("nowplaying:es", H6, sources.now_playing),
        "tr": lambda: cached("trending:es", H6, sources.trending),
        "pop": lambda: cached("tv:popular", H6, sources.popular_series),
        "air": lambda: cached("tv:airing", H6, sources.airing_series),
        "logos": lambda: cached("providers:es", 7 * H24, sources.provider_logos),
    }


def warm():
    """Precarga lo que más se usa para que el primer usuario no espere a TMDB."""
    while True:
        for fn in list(_home_parts().values()) + [lambda: cached("upcoming:es", H12, sources.upcoming)]:
            try:
                fn()
            except Exception:
                pass
        time.sleep(5 * 3600)


@asynccontextmanager
async def lifespan(_):
    threading.Thread(target=warm, daemon=True).start()
    yield


app = FastAPI(title="Cartelera", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=400)


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    r = await call_next(request)
    p = request.url.path
    if p.startswith("/static/"):
        r.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
    elif p.startswith("/api/") and p not in ("/api/diag", "/api/health"):
        r.headers["Cache-Control"] = "public, max-age=120, stale-while-revalidate=900"
    return r


@app.get("/api/home")
def home():
    """Todo lo de la portada en una sola petición (y en paralelo)."""
    parts = _home_parts()

    def run(k):
        try:
            return parts[k]()
        except HTTPException as e:
            return {"error": e.detail}
    with ThreadPoolExecutor(len(parts)) as ex:
        res = dict(zip(parts, ex.map(run, parts)))
    return {"np": res["np"], "tr": res["tr"], "ser": {"popular": res["pop"], "airing": res["air"]}, "logos": res["logos"]}


@app.get("/api/search")
def search(q: str = Query(min_length=2)):
    return cached(f"search:{q.lower()}", H24, lambda: sources.search(q))


@app.get("/api/nowplaying")
def nowplaying():
    return cached("nowplaying:es", H6, sources.now_playing)


@app.get("/api/services")
def services():
    return cached("services:es", 7 * H24, sources.services)


@app.get("/api/platform/{sid}")
def platform(sid: str, type: str = "all", order: str = "hype", cursor: str = ""):
    if sid not in logic.ALIAS or type not in ("all", "movie", "tv") or order not in logic.SA_ORDER:
        raise HTTPException(400, "Parámetros no válidos")
    key = f"plat2:{sid}:{type}:{order}:{cursor}"
    if sid in logic.COVERED:
        return cached(key, H12, lambda: sources.platform_catalog(sid, type, order, cursor or None))
    ids = cached("providerids:es", 7 * H24, sources.provider_ids)
    if sid not in ids:
        raise HTTPException(404, "Esta plataforma no está disponible en España según TMDB")
    return cached(key, H12, lambda: sources.tmdb_catalog(ids[sid], type, order, int(cursor or 1)))


@app.get("/api/series")
def series():
    return {"popular": cached("tv:popular", H6, sources.popular_series), "airing": cached("tv:airing", H6, sources.airing_series)}


@app.get("/api/upcoming")
def upcoming():
    return cached("upcoming:es", H12, sources.upcoming)


@app.get("/api/providers")
def providers():
    return cached("providers:es", 7 * H24, sources.provider_logos)


@app.get("/api/trending")
def trending():
    return cached("trending:es", H6, sources.trending)


@app.get("/api/title/{kind}/{tid}")
def title(kind: str, tid: int, mine: str = ""):
    if kind not in ("movie", "tv"):
        raise HTTPException(404)

    def get_sa():
        try:
            return cached(f"sa:{kind}:{tid}", H12, lambda: sources.streaming(kind, tid)), True
        except HTTPException:
            return [], False  # sin fuente de enlaces: no se afirma nada como disponible
    with ThreadPoolExecutor(3) as ex:
        f_info = ex.submit(lambda: cached(f"det:{kind}:{tid}", 7 * H24, lambda: sources.details(kind, tid)))
        f_sa = ex.submit(get_sa)
        f_prov = ex.submit(lambda: cached(f"prov:{kind}:{tid}", H12, lambda: sources.tmdb_providers(kind, tid)))
        info, (opts, verified), prov = f_info.result(), f_sa.result(), f_prov.result()
    confirmed = logic.normalize(opts)
    res = logic.split(confirmed, [m for m in mine.split(",") if m])
    res["unconfirmed"] = logic.unconfirmed(prov, confirmed)
    res["verified"] = verified
    res["tmdb_link"] = prov.get("link")
    try:
        ratings = cached(f"omdb:{info.get('imdb_id')}", 7 * H24, lambda: sources.omdb(info.get("imdb_id")))
    except HTTPException:
        ratings = {}
    return {**info, "availability": res, "ratings": ratings}


@app.get("/api/cinemas")
def cinemas(lat: float, lon: float, radius_km: float = 10):
    key = f"cin:{round(lat, 2)}:{round(lon, 2)}:{int(radius_km)}"
    items = cached(key, 30 * H24, lambda: sources.cinemas_near(lat, lon, int(radius_km * 1000)))
    for c in items:
        c["km"] = round(logic.km(lat, lon, c["lat"], c["lon"]), 1)
    return sorted(items, key=lambda c: c["km"])


@app.get("/api/diag")
def diag():
    return sources.diag()


@app.get("/api/health")
def health():
    return {"ok": True, "tmdb": bool(os.environ.get("TMDB_API_KEY")), "streaming": bool(os.environ.get("RAPIDAPI_KEY"))}


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.get("/sw.js")
def sw():
    return FileResponse(os.path.join(HERE, "static", "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"), headers={"Cache-Control": "no-cache"})
