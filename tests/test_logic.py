import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import logic
from fastapi.testclient import TestClient
import main, sources


def opt(sid, typ="subscription", link="https://x"):
    return {"service": {"id": sid, "name": sid.title()}, "type": typ, "link": link}


def test_sin_enlace_no_es_disponible():
    c = logic.normalize([opt("netflix", link=""), opt("prime")])
    assert [o["service"] for o in c] == ["prime"]


def test_split_mis_plataformas():
    c = logic.normalize([opt("netflix"), opt("prime"), opt("apple", "rent")])
    r = logic.split(c, ["netflix"])
    assert [o["service"] for o in r["on_mine"]] == ["netflix"]
    assert [o["service"] for o in r["other_subscription"]] == ["prime"]
    assert len(r["rent_buy"]) == 1


def test_unconfirmed_solo_lo_que_no_tiene_enlace():
    c = logic.normalize([opt("netflix")])
    assert logic.unconfirmed({"flatrate": [{"provider_name": "Netflix"}, {"provider_name": "Max"}]}, c) == ["Max"]


def test_endpoint_title_sin_fuente_de_enlaces(monkeypatch):
    monkeypatch.setattr(sources, "details", lambda k, i: {"id": i, "type": k, "title": "T"})
    monkeypatch.setattr(sources, "tmdb_providers", lambda k, i: {"flatrate": [{"provider_name": "Netflix"}]})
    def boom(k, i): raise RuntimeError("Falta RAPIDAPI_KEY")
    monkeypatch.setattr(sources, "streaming", boom)
    r = TestClient(main.app).get("/api/title/movie/999991").json()
    assert r["availability"]["verified"] is False and r["availability"]["on_mine"] == []
    assert r["availability"]["unconfirmed"] == ["Netflix"]


def test_distancia():
    assert 0 < logic.km(40.4, -3.7, 40.5, -3.7) < 12


def test_omdb_sin_clave(monkeypatch):
    monkeypatch.delenv("OMDB_API_KEY", raising=False)
    assert sources.omdb("tt1") == {}


def test_omdb_parseo(monkeypatch):
    monkeypatch.setenv("OMDB_API_KEY", "x")
    class R:
        def raise_for_status(self): pass
        def json(self): return {"imdbRating": "8.5", "Metascore": "N/A", "Ratings": [{"Source": "Rotten Tomatoes", "Value": "93%"}]}
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: R())
    assert sources.omdb("tt1") == {"imdb": "8.5", "rt": "93%"}


def test_logos_por_alias(monkeypatch):
    monkeypatch.setattr(sources, "tmdb", lambda p, **k: {"results": [
        {"provider_name": "Amazon Prime Video", "logo_path": "/a.jpg"}, {"provider_name": "Netflix", "logo_path": "/n.jpg"}]})
    r = sources.provider_logos()
    assert r["prime"].endswith("/a.jpg") and r["netflix"].endswith("/n.jpg") and "disney" not in r


def test_proximamente_ordenado(monkeypatch):
    def fake(path, **k):
        d = "2999-03-01" if path.endswith("movie") else "2999-01-15"
        return {"results": [{"id": 1, "title": "x", "name": "x", "release_date": d, "first_air_date": d, "poster_path": "/p"}]}
    monkeypatch.setattr(sources, "tmdb", fake)
    monkeypatch.setattr("datetime.date", __import__("datetime").date)
    r = sources.upcoming(days=36500 * 2)
    assert [x["date"] for x in r] == sorted(x["date"] for x in r)


def test_streaming_prueba_autenticaciones(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY", "motn-key-v4-zzz")
    monkeypatch.setattr(sources, "_good", None)
    calls = []
    class R:
        def __init__(s, c): s.status_code = c
        def json(s): return {"streamingOptions": {"es": [{"x": 1}]}}
    def fake(url, params=None, headers=None, timeout=0):
        calls.append((url, tuple(headers)))
        return R(200 if "X-API-Key" in headers else 403)
    monkeypatch.setattr(sources.requests, "get", fake)
    assert sources.streaming("movie", 1) == [{"x": 1}]
    assert "X-API-Key" in calls[0][1]  # motn-key -> prueba primero la API directa
    sources._good = None


def test_streaming_clave_rechazada(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY", "mala")
    monkeypatch.setattr(sources, "_good", None)
    class R: status_code = 403
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: R())
    import pytest
    with pytest.raises(RuntimeError, match="no acepta"):
        sources.streaming("movie", 1)


def test_unconfirmed_ignora_variantes_de_lo_confirmado():
    c = logic.normalize([opt("prime"), opt("netflix")])
    r = {"flatrate": [{"provider_name": "Amazon Prime Video with Ads"}, {"provider_name": "Netflix Standard with Ads"},
                      {"provider_name": "HBO Max Amazon Channel"}, {"provider_name": "Movistar Plus+"}]}
    assert logic.unconfirmed(r, c) == ["HBO Max Amazon Channel", "Movistar Plus+"]


def test_addon_no_cuenta_como_tuyo():
    c = logic.normalize([opt("prime", "addon"), opt("prime")])
    r = logic.split(c, ["prime"])
    assert [o["type"] for o in r["on_mine"]] == ["subscription"]
    assert [o["type"] for o in r["other_subscription"]] == ["addon"]


def test_catalogo_solo_con_enlace(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY", "k")
    monkeypatch.setattr(sources, "_good", ("https://x", {"X-API-Key": "k"}))
    show = lambda tm, link: {"tmdbId": tm, "title": "T", "releaseYear": 2020, "rating": 85,
        "imageSet": {"verticalPoster": {"w240": "p"}},
        "streamingOptions": {"es": [{"service": {"id": "netflix"}, "type": "subscription", "link": link}]}}
    class R:
        status_code = 200
        def json(s): return {"shows": [show("movie/1", "https://n/1"), show("tv/2", ""), show("raro", "https://n/3")],
                             "hasMore": True, "nextCursor": "abc"}
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: R())
    r = sources.platform_catalog("netflix")
    assert [i["id"] for i in r["items"]] == [1] and r["items"][0]["rating"] == 8.5 and r["next"] == "abc"


def test_catalogo_tmdb_sin_verificar_y_ordenado(monkeypatch):
    def fake(path, **k):
        kind = path.rsplit("/", 1)[1]
        mk = lambda i, pop: {"id": i, "title": f"{kind}{i}", "name": f"{kind}{i}", "poster_path": "/p", "popularity": pop,
                             "release_date": "2026-01-01", "first_air_date": "2026-01-01", "vote_count": 5}
        return {"results": [mk(1, 10), mk(2, 99)] if kind == "movie" else [mk(3, 50)], "total_pages": 3}
    monkeypatch.setattr(sources, "tmdb", fake)
    r = sources.tmdb_catalog(149, "all", "hype", 1)
    assert r["verified"] is False and [i["id"] for i in r["items"]] == [2, 3, 1] and r["next"] == "2"


def test_orden_sa():
    assert logic.SA_ORDER["hype"] == "popularity_1week" and logic.SA_ORDER["new"] == "release_date"
    assert "movistar" not in logic.COVERED and "netflix" in logic.COVERED


SENSA = """
<div id="x" data-selected-date="2026-11-14">
<div class="card entity-card movie-card-theater cf"><a class="meta-title-link" href="/p/1">  Peli Uno </a>
 <div class="showtimes-version"><div class="text"> 14 de noviembre de 2026 -
   En Versión doblada </div><div class="hours">
  <span class="x showtimes-hour-item" data-experiences="[&quot;Format.Projection.3D&quot;]" data-showtime-id="1" data-showtime-time="2026-11-14T19:30:00+01:00"></span>
  <span class="x showtimes-hour-item" data-experiences="[]" data-showtime-id="2" data-showtime-time="2026-11-14T17:00:00+01:00"></span>
  <span class="x showtimes-hour-item" data-experiences="[]" data-showtime-id="3" data-showtime-time="2026-11-14T17:00:00+01:00"></span>
 </div></div>
 <div class="showtimes-version"><div class="text"> 15 de noviembre de 2026 - En V.O. </div><div class="hours">
  <span class="x showtimes-hour-item" data-experiences="[]" data-showtime-id="4" data-showtime-time="2026-11-15T11:00:00+01:00"></span></div></div>
</div>
<div class="card entity-card movie-card-theater cf"><a class="meta-title-link" href="/p/2">Sin horas</a></div>
</div>"""


def test_parser_sensacine_solo_hoy_sin_duplicados():
    r = sources.parse_showtimes(SENSA)
    assert r["date"] == "2026-11-14" and len(r["movies"]) == 1
    v = r["movies"][0]["versions"]
    assert r["movies"][0]["title"] == "Peli Uno" and len(v) == 1 and v[0]["label"] == "Doblada"
    assert [(t["t"], t["fmt"]) for t in v[0]["times"]] == [("17:00", []), ("19:30", ["3D"])]


def test_endpoint_cines_solo_lista_permitida():
    c = TestClient(main.app)
    assert c.get("/api/showtimes/ZZZZZ").status_code == 404


def test_browse_mezcla_verificadas_y_tmdb(monkeypatch):
    it = lambda i, v: {"id": i, "type": "tv", "title": f"t{i}", "verified": v, "on": []}
    monkeypatch.setattr(sources, "browse_sa", lambda s, k, o, c, *a: {"items": [it(1, True), it(2, True)], "next": "n", "verified": True})
    monkeypatch.setattr(sources, "provider_ids", lambda: {"movistar": 149})
    monkeypatch.setattr(sources, "tmdb_catalog", lambda p, k, o, pg, *a: {"items": [it(2, False), it(3, False)], "next": "2", "verified": False})
    r = TestClient(main.app).get("/api/browse?type=tv&platforms=netflix,movistar&order=hype&cursor=").json()
    assert [x["id"] for x in r["items"]] == [1, 2, 3] and r["verified"] is False and r["next"]
    assert [x["verified"] for x in r["items"]][0] is True


def test_filtros_llegan_a_la_api_de_enlaces(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY", "k")
    monkeypatch.setattr(sources, "_good", ("https://x", {"X-API-Key": "k"}))
    seen = {}
    class R:
        status_code = 200
        def json(s): return {"shows": [], "hasMore": False}
    def fake(url, params=None, headers=None, timeout=0):
        seen.update(params); return R()
    monkeypatch.setattr(sources.requests, "get", fake)
    sources.browse_sa(["netflix", "hbo"], "tv", "rate", None, 7.5, 2010, "drama")
    assert seen["catalogs"] == "netflix.subscription,hbo.subscription" and seen["show_type"] == "series"
    assert seen["rating_min"] == 75 and seen["year_min"] == 2010 and seen["genres"] == "drama" and seen["order_by"] == "rating"


def test_filtros_tmdb_y_genero_sin_equivalente(monkeypatch):
    calls = []
    def fake(path, **k):
        calls.append((path, k)); return {"results": [], "total_pages": 1}
    monkeypatch.setattr(sources, "tmdb", fake)
    sources.tmdb_catalog([149], "all", "hype", 1, 8, 2020, "horror")
    # terror: existe para película (27) pero no para serie -> solo se pide la de películas
    assert [c[0] for c in calls] == ["/discover/movie"]
    k = calls[0][1]
    assert k["with_genres"] == 27 and k["vote_average.gte"] == 8 and k["primary_release_date.gte"] == "2020-01-01"


def test_browse_rechaza_parametros_raros():
    c = TestClient(main.app)
    assert c.get("/api/browse?genre=nope").status_code == 400
    assert c.get("/api/browse?minr=99").status_code == 400
    assert c.get("/api/browse?ymin=5").status_code == 400
