"""Regla anti-mentiras: solo es 'disponible' lo que tiene enlace directo a la plataforma."""
from math import radians, sin, cos, asin, sqrt

LABELS = {"netflix": "Netflix", "prime": "Prime Video", "disney": "Disney+", "hbo": "Max", "max": "Max",
          "apple": "Apple TV+", "movistar": "Movistar+", "filmin": "Filmin", "skyshowtime": "SkyShowtime",
          "paramount": "Paramount+", "rakuten": "Rakuten TV", "mubi": "MUBI", "atresplayer": "Atresplayer"}


ALIAS = {"netflix": ["netflix"], "prime": ["amazon prime video", "prime video"], "disney": ["disney plus", "disney+"],
         "hbo": ["max", "hbo max"], "apple": ["apple tv plus", "apple tv+", "apple tv"], "movistar": ["movistar plus+", "movistar plus"],
         "filmin": ["filmin"], "skyshowtime": ["skyshowtime"], "paramount": ["paramount plus", "paramount+"],
         "rakuten": ["rakuten tv"], "mubi": ["mubi"]}


def canon(name):
    """Id de plataforma al que corresponde un nombre de TMDB (incluye variantes: with Ads, Amazon Channel...)."""
    n = name.lower().strip()
    for sid, names in ALIAS.items():
        if any(n == x or n.startswith(x + " ") for x in names):
            return sid
    return None


def normalize(options):
    seen, out = set(), []
    for o in options:
        sid = (o.get("service") or {}).get("id", "").lower()
        link = o.get("link") or ""
        key = (sid, o.get("type"))
        if not sid or not link or key in seen:
            continue
        seen.add(key)
        out.append({"service": sid, "name": (o.get("service") or {}).get("name") or LABELS.get(sid, sid),
                    "type": o.get("type"), "link": link, "quality": o.get("quality"),
                    "price": (o.get("price") or {}).get("formatted"),
                    "logo": ((o.get("service") or {}).get("imageSet") or {}).get("lightThemeImage")
                            or ((o.get("service") or {}).get("imageSet") or {}).get("whiteImage"),
                    "since": o.get("availableSince"), "expires": o.get("expiresOn")})
    return out


def split(confirmed, mine):
    """mine: ids de plataformas del usuario (vacío = todas)."""
    mine = {m.lower() for m in mine}
    sub = [o for o in confirmed if o["type"] in ("subscription", "free", "addon")]
    # un canal de pago extra (addon) no es "tuyo" solo por tener la plataforma anfitriona
    hit = [o for o in sub if o["type"] != "addon" and (not mine or o["service"] in mine)]
    pay = [o for o in confirmed if o["type"] in ("rent", "buy")]
    return {"on_mine": hit, "other_subscription": [o for o in sub if o not in hit], "rent_buy": pay}


def unconfirmed(tmdb_res, confirmed):
    """Plataformas que TMDB dice tener pero sin enlace verificado -> se muestran aparte, nunca como disponibles."""
    have_ids = {o["service"] for o in confirmed}
    have_names = {o["name"].lower() for o in confirmed}
    out = set()
    for p in tmdb_res.get("flatrate", []):
        n = p["provider_name"].strip()
        if n.lower() in have_names or canon(n) in have_ids:
            continue
        out.add(n)
    return sorted(out)


def km(a_lat, a_lon, b_lat, b_lon):
    p = radians
    h = sin(p(b_lat - a_lat) / 2) ** 2 + cos(p(a_lat)) * cos(p(b_lat)) * sin(p(b_lon - a_lon) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))
