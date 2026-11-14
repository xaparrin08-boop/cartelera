import base64, json, os, threading, time
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


CINES = {"G01LE": "O-Cine Quadernillos (Alcalá de Henares)", "E0291": "Yelmo Parque Corredor (Torrejón de Ardoz)"}


@app.get("/api/showtimes/{code}")
def showtimes(code: str):
    """Sesiones de HOY de un cine (Sensacine). Solo cines de la lista, para no ser un proxy abierto."""
    if code not in CINES:
        raise HTTPException(404, "Cine no disponible")
    out = cached(f"show:{code}", 3 * 3600, lambda: sources.showtimes(code))
    return {**out, "cinema": CINES[code], "code": code}


def _enc(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode()


def _dec(s):
    try:
        return json.loads(base64.urlsafe_b64decode(s.encode()).decode()) if s else {}
    except Exception:
        return {}


@app.get("/api/browse")
def browse(type: str = "all", platforms: str = "", order: str = "hype", cursor: str = "", minr: float = 0, ymin: int = 0, genre: str = ""):
    """Películas o series filtradas por varias plataformas a la vez.
    Las que la API de enlaces cubre van verificadas; Movistar+/Filmin/Rakuten van por TMDB (sin verificar)."""
    if (type not in ("movie", "tv", "all") or order not in logic.SA_ORDER or not 0 <= minr <= 9.5
            or not (ymin == 0 or 1900 <= ymin <= 2100) or (genre and genre not in logic.GENRES)):
        raise HTTPException(400, "Parámetros no válidos")
    sids = [s for s in dict.fromkeys(platforms.split(",")) if s in logic.ALIAS]
    if not sids:
        sids = sorted(logic.COVERED)
    cov = [s for s in sids if s in logic.COVERED]
    unc = [s for s in sids if s not in logic.COVERED]
    cur = _dec(cursor)
    key = f"browse2:{type}:{','.join(sorted(sids))}:{order}:{minr}:{ymin}:{genre}:{cursor}"

    def run():
        lanes = {}
        if cov and not cur.get("sa_done"):
            lanes["sa"] = lambda: sources.browse_sa(cov, type, order, cur.get("sa"), minr, ymin, genre)
        if unc and not cur.get("tm_done"):
            ids = cached("providerids:es", 7 * H24, sources.provider_ids)
            pids = [ids[s] for s in unc if s in ids]
            if pids:
                lanes["tm"] = lambda: sources.tmdb_catalog(pids, type, order, int(cur.get("tm") or 1), minr, ymin, genre)
        if not lanes:
            return {"items": [], "next": None, "verified": True}
        with ThreadPoolExecutor(len(lanes)) as ex:
            res = {k: f for k, f in zip(lanes, [ex.submit(fn) for fn in lanes.values()])}
            res = {k: f.result() for k, f in res.items()}
        a, b = (res.get("sa") or {}).get("items", []), (res.get("tm") or {}).get("items", [])
        items, seen = [], set()
        for i in range(max(len(a), len(b))):  # intercalados para que se mezclen
            for lane in (a, b):
                if i < len(lane):
                    x = lane[i]
                    if (x["type"], x["id"]) not in seen:
                        seen.add((x["type"], x["id"]))
                        items.append(x)
        nxt = {"sa": (res.get("sa") or {}).get("next"), "tm": ((res.get("tm") and res["tm"].get("next")) or None)}
        nxt = {"sa": nxt["sa"], "sa_done": "sa" not in res and bool(cov) or ("sa" in res and not nxt["sa"]),
               "tm": nxt["tm"], "tm_done": "tm" not in res or not nxt["tm"]}
        if not cov:
            nxt["sa_done"] = True
        more = not (nxt["sa_done"] and nxt["tm_done"])
        return {"items": items, "next": _enc(nxt) if more else None, "verified": not unc}
    return cached(key, H12, run)


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
        f_info = ex.submit(lambda: cached(f"det2:{kind}:{tid}", 7 * H24, lambda: sources.details(kind, tid)))
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
