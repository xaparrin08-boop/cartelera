"""Fuentes externas: TMDB (fichas, cartelera), Streaming Availability (enlaces reales) y OpenStreetMap (cines)."""
import os, requests
from concurrent.futures import ThreadPoolExecutor

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
            "date": x.get("release_date") or x.get("first_air_date") or "",
            "pop": round(x.get("popularity") or 0, 1), "votes": x.get("vote_count") or 0}


def search(q):
    return [card(x) for x in tmdb("/search/multi", query=q).get("results", [])
            if x.get("media_type") in ("movie", "tv")]


def now_playing():
    with ThreadPoolExecutor(2) as ex:
        pages = list(ex.map(lambda p: tmdb("/movie/now_playing", region="ES", page=p).get("results", []), (1, 2)))
    return [card(x, "movie") for r in pages for x in r]


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
    def job(args):
        kind, p = args
        if kind == "movie":
            r = tmdb("/discover/movie", region="ES", sort_by="popularity.desc", page=p, with_release_type="2|3",
                     **{"primary_release_date.gte": a, "primary_release_date.lte": b})
        else:
            r = tmdb("/discover/tv", sort_by="popularity.desc", page=p,
                     **{"first_air_date.gte": a, "first_air_date.lte": b})
        return [card(x, kind) for x in r.get("results", [])]
    with ThreadPoolExecutor(6) as ex:
        out = [x for r in ex.map(job, [(k, p) for p in (1, 2, 3) for k in ("movie", "tv")]) for x in r]
    return sorted([x for x in out if x["date"] >= a and x["poster"]], key=lambda x: (x["date"], x["title"]))


from logic import ALIAS, SA_ORDER  # noqa: E402


def provider_logos():
    """{id_plataforma: url_logo} desde TMDB (región España)."""
    items = tmdb("/watch/providers/movie", watch_region="ES").get("results", [])
    by = {i["provider_name"].lower(): i for i in items if i.get("logo_path")}
    return {sid: IMG + "w92" + by[n]["logo_path"] for sid, names in ALIAS.items() for n in names if n in by}


def provider_ids():
    """{id_plataforma: provider_id de TMDB} en España."""
    items = tmdb("/watch/providers/movie", watch_region="ES").get("results", [])
    by = {i["provider_name"].lower(): i["provider_id"] for i in items}
    return {sid: by[n] for sid, names in ALIAS.items() for n in names if n in by}


def tmdb_catalog(pid, kind="all", order="hype", page=1, minr=0, ymin=0, genre=""):
    pids = pid if isinstance(pid, (list, tuple)) else [pid]
    """Catálogo por TMDB (datos tipo JustWatch): NO verificado, sin enlace directo."""
    from datetime import date
    today = date.today().isoformat()
    kinds = ["movie", "tv"] if kind == "all" else [kind]

    def job(k):
        sort = {"hype": "popularity.desc", "pop": "vote_count.desc", "rate": "vote_average.desc",
                "new": "primary_release_date.desc" if k == "movie" else "first_air_date.desc"}[order]
        p = {"watch_region": "ES", "with_watch_providers": "|".join(str(x) for x in pids), "with_watch_monetization_types": "flatrate",
             "sort_by": sort, "page": page}
        if order == "new":
            p["primary_release_date.lte" if k == "movie" else "first_air_date.lte"] = today
        if order == "rate":
            p["vote_count.gte"] = 200
        if minr:
            p["vote_average.gte"] = minr
            p.setdefault("vote_count.gte", 50)
        if ymin:
            p["primary_release_date.gte" if k == "movie" else "first_air_date.gte"] = f"{int(ymin)}-01-01"
        if genre:
            from logic import GENRES
            g = GENRES.get(genre)
            gid = g and (g[1] if k == "movie" else g[2])
            if not gid:
                return [], 1  # ese género no existe para este tipo en TMDB
            p["with_genres"] = gid
        r = tmdb(f"/discover/{k}", **p)
        return [dict(card(x, k), on=[], verified=False) for x in r.get("results", []) if x.get("poster_path")], r.get("total_pages", 1)
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(job, kinds))
    items = [x for r, _ in res for x in r]
    key = {"hype": lambda x: -x["pop"], "pop": lambda x: -x["votes"], "rate": lambda x: -x["rating"], "new": lambda x: x["date"]}
    items.sort(key=key[order], reverse=(order == "new"))
    more = page < max(t for _, t in res) and page < 5
    return {"items": items, "next": str(page + 1) if more else None, "verified": False}


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
    d = tmdb(f"/{kind}/{tid}", append_to_response="external_ids,credits,recommendations")
    imdb = d.get("imdb_id") or (d.get("external_ids") or {}).get("imdb_id")
    cr = d.get("credits") or {}
    cast = [{"name": c["name"], "role": c.get("character") or "", "photo": IMG + "w185" + c["profile_path"] if c.get("profile_path") else None}
            for c in cr.get("cast", [])[:12]]
    directors = [c["name"] for c in cr.get("crew", []) if c.get("job") == "Director"][:3] or [c["name"] for c in d.get("created_by", [])][:3]
    similar = [card(x, kind) for x in (d.get("recommendations") or {}).get("results", []) if x.get("poster_path")][:14]
    return {**card(d, kind), "imdb_id": imdb, "cast": cast, "directors": directors, "similar": similar,
            "countries": [c["name"] for c in d.get("production_countries", [])], "overview": d.get("overview"), "genres": [g["name"] for g in d.get("genres", [])],
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


def _sa_base_headers(key):
    """(base, headers) que ya funciona; si no hay, prueba con una ficha conocida."""
    global _good
    if not _good:
        _sa_get("movie", 603, key)
    if not _good:
        raise RuntimeError("Streaming Availability no acepta la clave o no responde")
    return _good


def services():
    """Plataformas que cubre la API en España: [{id, name, logo}]."""
    key = os.environ.get("RAPIDAPI_KEY", "")
    if not key:
        raise RuntimeError("Falta RAPIDAPI_KEY")
    base, h = _sa_base_headers(key)
    r = requests.get(f"{base}/countries/{COUNTRY}", headers=h, timeout=12)
    if r.status_code != 200:
        raise RuntimeError(f"Servicios no disponibles ({r.status_code})")
    out = []
    for x in r.json().get("services", []):
        im = x.get("imageSet") or {}
        out.append({"id": x["id"], "name": x.get("name"), "logo": im.get("lightThemeImage") or im.get("whiteImage"),
                    "types": sorted((x.get("supportedStreamingTypes") or {}).keys()) if isinstance(x.get("supportedStreamingTypes"), dict) else x.get("supportedStreamingTypes")})
    return out


def browse_sa(sids, kind="all", order="hype", cursor=None, minr=0, ymin=0, genre=""):
    """Catálogo de una o varias plataformas en España, con enlace directo en cada título (verificado)."""
    key = os.environ.get("RAPIDAPI_KEY", "")
    if not key:
        raise RuntimeError("Falta RAPIDAPI_KEY")
    base, h = _sa_base_headers(key)
    sids = list(sids)
    p = {"country": COUNTRY, "catalogs": ",".join(f"{x}.subscription" for x in sids),
         "order_by": SA_ORDER.get(order, "popularity_1week"), "order_direction": "desc",
         "series_granularity": "show", "output_language": "es"}
    if kind in ("movie", "tv"):
        p["show_type"] = "movie" if kind == "movie" else "series"
    if minr:
        p["rating_min"] = int(minr * 10)
    if ymin:
        p["year_min"] = int(ymin)
    if genre:
        p["genres"] = genre
    if cursor:
        p["cursor"] = cursor
    r = requests.get(f"{base}/shows/search/filters", params=p, headers=h, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Catálogo no disponible ({r.status_code}): {r.text[:160]}")
    d = r.json()
    items = []
    for x in d.get("shows", []):
        k, _, i = str(x.get("tmdbId", "")).partition("/")
        if not i.isdigit():
            continue
        opts = [o for o in x.get("streamingOptions", {}).get(COUNTRY, [])
                if o.get("service", {}).get("id") in sids and o.get("type") == "subscription" and o.get("link")]
        if not opts:
            continue  # sin enlace directo no lo enseñamos
        ps = (x.get("imageSet") or {}).get("verticalPoster") or {}
        items.append({"id": int(i), "type": "movie" if k == "movie" else "tv", "title": x.get("title"),
                      "year": str(x.get("releaseYear") or x.get("firstAirYear") or ""),
                      "poster": ps.get("w360") or ps.get("w240") or ps.get("w480"),
                      "rating": round((x.get("rating") or 0) / 10, 1), "date": "", "link": opts[0]["link"],
                      "expires": opts[0].get("expiresOn"), "on": sorted({o["service"]["id"] for o in opts}), "verified": True})
    return {"items": items, "next": d.get("nextCursor") if d.get("hasMore") else None, "verified": True}


def platform_catalog(sid, kind="all", order="hype", cursor=None):
    return browse_sa([sid], kind, order, cursor)


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


# ---------------- Horarios de cine (Sensacine, solo "hoy") ----------------
import re, html as _html
SENSACINE = "https://www.sensacine.com/cines/cine/{code}/"
UA = "Mozilla/5.0 (compatible; CarteleraPersonal/1.0)"


def _exp(raw):
    t = _html.unescape(raw or "").lower()
    return [n for k, n in (("imax", "IMAX"), ("3d", "3D"), ("4dx", "4DX"), ("screenx", "ScreenX"), ("dolby", "Dolby"), ("premium", "Premium")) if k in t]


def parse_showtimes(page):
    """HTML de la página de un cine de Sensacine -> películas con sus sesiones de HOY (sin duplicados)."""
    sel = re.search(r'data-selected-date="([^"]+)"', page)
    day = sel.group(1) if sel else None
    movies = []
    for chunk in page.split('movie-card-theater')[1:]:
        t = re.search(r'meta-title-link[^>]*>\s*([^<]+?)\s*<', chunk)
        if not t:
            continue
        versions = {}
        for vb in chunk.split('class="showtimes-version')[1:]:
            lab = re.search(r'<div class="text">\s*(.*?)\s*</div>', vb, re.S)
            text = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", lab.group(1)))).strip() if lab else ""
            label = text.split(" - ", 1)[-1].strip().replace("En Versión doblada", "Doblada").replace("En V.O.", "V.O.")
            for tag in re.findall(r'<span[^>]*data-showtime-time="[^"]+"[^>]*>', vb):
                iso = re.search(r'data-showtime-time="([^"]+)"', tag).group(1)
                m = re.match(r"(\d{4}-\d\d-\d\d)T(\d\d:\d\d)", iso)
                if not m or (day and m.group(1) != day):
                    continue  # solo hoy
                ex = re.search(r'data-experiences="([^"]*)"', tag)
                fmt = tuple(_exp(ex.group(1) if ex else ""))
                versions.setdefault(label, set()).add((m.group(2), fmt))
        vs = [{"label": lb, "times": [{"t": a, "fmt": list(f)} for a, f in sorted(ts)]} for lb, ts in versions.items() if ts]
        if vs:
            movies.append({"title": _html.unescape(t.group(1)), "versions": vs})
    return {"date": day, "movies": movies}


def showtimes(code):
    r = requests.get(SENSACINE.format(code=code), headers={"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Sensacine respondió {r.status_code}")
    out = parse_showtimes(r.text)
    if not out["movies"]:
        raise RuntimeError("No he encontrado sesiones en la página (¿cambió el formato?)")
    # enlazar cada título con su ficha de TMDB (para póster y para abrir la ficha)
    def match(m):
        try:
            res = [x for x in search(m["title"]) if x["type"] == "movie"]
            if res:
                m.update(id=res[0]["id"], poster=res[0]["poster"], rating=res[0]["rating"])
        except Exception:
            pass
        return m
    with ThreadPoolExecutor(6) as ex:
        out["movies"] = list(ex.map(match, out["movies"]))
    out["source"] = "Sensacine"
    out["url"] = SENSACINE.format(code=code)
    return out
