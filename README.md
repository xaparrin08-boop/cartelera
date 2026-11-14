# Cartelera

Dónde ver una peli o serie (solo con enlace directo verificado), próximos estrenos, series, calificaciones y cines cercanos.

- **Fichas y búsqueda:** TMDB
- **Plataformas con enlace directo:** Streaming Availability (Movie of the Night)
- **Calificaciones:** OMDb (IMDb, Rotten Tomatoes, Metacritic)
- **Cines cercanos:** OpenStreetMap
- **Caché:** Supabase (tabla `cine_cache`, ver `schema.sql`)

Regla de oro: algo solo aparece como disponible si hay enlace directo a la plataforma. Lo que una fuente dice sin enlace va como "Sin confirmar".

## Variables de entorno

| Variable | Para qué |
|---|---|
| `TMDB_API_KEY` | Clave de TMDB (API Key v3) |
| `RAPIDAPI_KEY` | Clave de Streaming Availability. Sirve la de RapidAPI o la `motn-key-…` de la API directa; se detecta sola |
| `OMDB_API_KEY` | Opcional. Sin ella no salen IMDb/RT/Metacritic |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Opcional. Sin ellas la caché es solo en memoria |

## Desarrollo

    pip install -r requirements.txt
    python -m pytest tests
    uvicorn main:app --reload

`/api/diag` muestra qué fuentes responden (sin enseñar claves).
