"""Fuentes externas: TMDB (fichas, cartelera), Streaming Availability (enlaces reales) y OpenStreetMap (cines)."""
import os, requests

TMDB = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
SA = "https://streaming-availability.p.rapidapi.com"
OVERPASS = "https://overpass-api.de/api/interpreter"
COUNTRY = "es"


def tmdb(path, **params):
    key = os.environ.get("TMDB_API_KEY", "")
    if not key:
        raise RuntimeError("Falta TMDB_API_KEY")
    r = requests.get(f"{TMDB}{path}", params={"api_key": key, "language": "es-ES", **params}, timeout=10)
    r.raise_for_status()
    return r.json()


def card(x, kind=None):
    kind = kind or x.get("media_type") or ("movie" if "title" in x else "tv")
    return {"id": x["id"], "type": kind, "title": x.get("title") or x.get("name"),
            "year": (x.get("release_date") or x.get("first_air_date") or "")[:4],
            "poster": IMG + "w342" + x["poster_path"] if x.get("poster_path") else None,
            "rating": round(x.get("vote_average") or 0, 1),
            "date": x.get("release_date") or x.get("first_air_date") or ""}


def search(q):
    return [card(x) for x in tmdb("/search/multi", query=q).get("results", [])
            if x.get("media_type") in ("movie", "tv")]


def now_playing():
    out = []
    for p in (1, 2):
        out += tmdb("/movie/now_playing", region="ES", page=p).get("results", [])
    return [card(x, "movie") for x in out]


def trending():
    return [card(x) for x in tmdb("/trending/all/week").get("results", []) if x.get("media_type") in ("movie", "tv")]


def popular_series():
    return [card(x, "tv") for x in tmdb("/tv/popular").get("results", [])]


def airing_series():
    return [card(x, "tv") for x in tmdb("/tv/on_the_air").get("results", [])]


def upcoming(days=150):
    """Lo más esperado de los próximos meses (pelis y series), ordenado por fecha de estreno."""
    from datetime import date, timedelta
    a, b = date.today().isoformat(), (date.today() + timedelta(days=days)).isoformat()
    out = []
    for p in (1, 2, 3):
        out += [card(x, "movie") for x in tmdb("/discover/movie", region="ES", sort_by="popularity.desc", page=p,
                with_release_type="2|3", **{"primary_release_date.gte": a, "primary_release_date.lte": b}).get("results", [])]
        out += [card(x, "tv") for x in tmdb("/discover/tv", sort_by="popularity.desc", page=p,
                **{"first_air_date.gte": a, "first_air_date.lte": b}).get("results", [])]
    return sorted([x for x in out if x["date"] >= a and x["poster"]], key=lambda x: (x["date"], x["title"]))


from logic import ALIAS  # noqa: E402


def provider_logos():
    """{id_plataforma: url_logo} desde TMDB (región España)."""
    items = tmdb("/watch/providers/movie", watch_region="ES").get("results", [])
    by = {i["provider_name"].lower(): i for i in items if i.get("logo_path")}
    return {sid: IMG + "w92" + by[n]["logo_path"] for sid, names in ALIAS.items() for n in names if n in by}


def omdb(imdb_id):
    """IMDb, Rotten Tomatoes y Metacritic vía OMDb. Devuelve {} si no hay clave."""
    key = os.environ.get("OMDB_API_KEY", "")
    if not key or not imdb_id:
        return {}
    r = requests.get("https://www.omdbapi.com/", params={"i": imdb_id, "apikey": key}, timeout=10)
    r.raise_for_status()
    d = r.json()
    if d.get("Response") == "False":
        return {}
    out = {}
    if d.get("imdbRating") not in (None, "N/A"):
        out["imdb"] = d["imdbRating"]
    for x in d.get("Ratings", []):
        if x["Source"] == "Rotten Tomatoes":
            out["rt"] = x["Value"]
    if d.get("Metascore") not in (None, "N/A"):
        out["metacritic"] = d["Metascore"]
    return out


def details(kind, tid):
    d = tmdb(f"/{kind}/{tid}", append_to_response="external_ids")
    imdb = d.get("imdb_id") or (d.get("external_ids") or {}).get("imdb_id")
    return {**card(d, kind), "imdb_id": imdb, "overview": d.get("overview"), "genres": [g["name"] for g in d.get("genres", [])],
            "backdrop": IMG + "w780" + d["backdrop_path"] if d.get("backdrop_path") else None,
            "runtime": d.get("runtime")}


_good = None  # (base, headers_fn) que ya funcionó


def _candidates(key):
    rapid = ("https://streaming-availability.p.rapidapi.com",
             {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "streaming-availability.p.rapidapi.com"})
    direct = [("https://api.movieofthenight.com/v4", {"X-API-Key": key}),
              ("https://api.movieofthenight.com/v4", {"Authorization": f"Bearer {key}"})]
    return direct + [rapid] if key.startswith("motn-key") else [rapid] + direct


def _sa_get(kind, tid, key):
    """Devuelve (respuesta, intentos). Prueba las formas de autenticación hasta que una funciona."""
    global _good
    tried = []
    for base, h in ([_good] if _good else []) + _candidates(key):
        try:
            r = requests.get(f"{base}/shows/{kind}/{tid}", params={"country": COUNTRY}, headers=h, timeout=12)
        except requests.RequestException as e:
            tried.append((base.split("//")[1], type(e).__name__))
            continue
        tried.append((base.split("//")[1], r.status_code))
        if r.status_code == 200 or (_good and r.status_code == 404):
            _good = (base, h)
            return r, tried
    return None, tried


def streaming(kind, tid):
    """Opciones con enlace directo (Streaming Availability). Lanza si no hay clave o es rechazada."""
    key = os.environ.get("RAPIDAPI_KEY", "")
    if not key:
        raise RuntimeError("Falta RAPIDAPI_KEY")
    r, tried = _sa_get(kind, tid, key)
    if r is None:
        raise RuntimeError(f"Streaming Availability no acepta la clave o no responde: {tried}")
    if r.status_code == 404:
        return []
    return r.json().get("streamingOptions", {}).get(COUNTRY, [])


def diag():
    """Estado de cada fuente, sin mostrar claves."""
    out = {}
    try:
        out["tmdb"] = {"ok": bool(tmdb("/configuration"))}
    except Exception as e:
        out["tmdb"] = {"ok": False, "error": str(e)[:200].replace(os.environ.get("TMDB_API_KEY", "x"), "***")}
    key = os.environ.get("RAPIDAPI_KEY", "")
    if not key:
        out["streaming"] = {"ok": False, "error": "falta la clave"}
    else:
        r, tried = _sa_get("movie", 603, key)
        out["streaming"] = {"ok": bool(r), "intentos": tried, "usando": _good[0] if _good else None}
        if r is not None:
            out["streaming"]["campos"] = sorted(r.json().keys())
    out["omdb"] = {"clave": bool(os.environ.get("OMDB_API_KEY"))}
    return out


def tmdb_providers(kind, tid):
    res = tmdb(f"/{kind}/{tid}/watch/providers").get("results", {}).get("ES", {})
    return res


def cinemas_near(lat, lon, radius_m=10000):
    q = f'[out:json][timeout:20];(node["amenity"="cinema"](around:{radius_m},{lat},{lon});' \
        f'way["amenity"="cinema"](around:{radius_m},{lat},{lon}););out center tags;'
    r = requests.post(OVERPASS, data={"data": q}, timeout=25)
    r.raise_for_status()
    out = []
    for e in r.json().get("elements", []):
        t = e.get("tags", {})
        la = e.get("lat") or e.get("center", {}).get("lat")
        lo = e.get("lon") or e.get("center", {}).get("lon")
        if not (t.get("name") and la and lo):
            continue
        out.append({"id": f'{e["type"][0]}{e["id"]}', "name": t["name"], "lat": la, "lon": lo,
                    "website": t.get("website") or t.get("contact:website"),
                    "address": " ".join(filter(None, [t.get("addr:street"), t.get("addr:housenumber"), t.get("addr:city")]))})
    return out
