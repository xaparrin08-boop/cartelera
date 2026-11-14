"""Caché con TTL: Supabase si está configurado, memoria si no."""
import os, time, requests

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
_mem: dict = {}


def _h():
    return {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def get(key: str, ttl_s: int):
    """ttl_s muy grande = aceptar valores caducados (si la fuente falla)."""
    hit = _mem.get(key)
    if hit and time.time() - hit[0] < ttl_s:
        return hit[1]
    if URL and KEY:
        try:
            r = requests.get(f"{URL}/rest/v1/cine_cache", headers=_h(), timeout=8,
                             params={"key": f"eq.{key}", "select": "data,fetched_at"})
            rows = r.json() if r.ok else []
            if rows:
                from datetime import datetime
                ts = datetime.fromisoformat(rows[0]["fetched_at"].replace("Z", "+00:00")).timestamp()
                if time.time() - ts < ttl_s:
                    _mem[key] = (ts, rows[0]["data"])
                    return rows[0]["data"]
        except Exception:
            pass
    return None


def put(key: str, data):
    _mem[key] = (time.time(), data)
    if URL and KEY:
        try:
            requests.post(f"{URL}/rest/v1/cine_cache", timeout=8,
                          headers={**_h(), "Prefer": "resolution=merge-duplicates"},
                          json={"key": key, "data": data, "fetched_at": "now()"})
        except Exception:
            pass
