import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import sources, store, logic

app = FastAPI(title="Cartelera")
HERE = os.path.dirname(__file__)
H12, H6, H24 = 12 * 3600, 6 * 3600, 24 * 3600


def cached(key, ttl, fn):
    hit = store.get(key, ttl)
    if hit is not None:
        return hit
    try:
        val = fn()
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"Fuente externa no disponible: {e}")
    store.put(key, val)
    return val


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
def platform(sid: str, type: str = "all", order: str = "popularity_1week", cursor: str = ""):
    if sid not in logic.ALIAS or type not in ("all", "movie", "tv") or order not in (
            "popularity_1week", "popularity_1month", "release_date", "rating"):
        raise HTTPException(400, "Parámetros no válidos")
    return cached(f"plat:{sid}:{type}:{order}:{cursor}", H12, lambda: sources.platform_catalog(sid, type, order, cursor or None))


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
    info = cached(f"det:{kind}:{tid}", 7 * H24, lambda: sources.details(kind, tid))
    verified = True
    try:
        opts = cached(f"sa:{kind}:{tid}", H12, lambda: sources.streaming(kind, tid))
    except HTTPException:
        opts, verified = [], False  # sin fuente de enlaces: no se afirma nada como disponible
    confirmed = logic.normalize(opts)
    prov = cached(f"prov:{kind}:{tid}", H12, lambda: sources.tmdb_providers(kind, tid))
    res = logic.split(confirmed, [m for m in mine.split(",") if m])
    res["unconfirmed"] = logic.unconfirmed(prov, confirmed)
    res["verified"] = verified
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


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))
