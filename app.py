import base64
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, quote

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
TOKENS_FILE = DATA_DIR / "tokens.json"
STATE_FILE = DATA_DIR / "oauth_states.json"
SPOTIFY_PLAYLIST_CACHE_FILE = DATA_DIR / "spotify_playlists_cache.json"
SPOTIFY_PLAYLIST_CACHE_SECONDS = int(os.getenv("SPOTIFY_PLAYLIST_CACHE_SECONDS", "900"))

load_dotenv(BASE_DIR / ".env")

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "5050"))
DEFAULT_CITY = os.getenv("DEFAULT_CITY", "Móstoles")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

SPOTIFY_SCOPES = " ".join([
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
    "user-read-email",
    "user-read-private",
    "streaming",
])
GOOGLE_SCOPES = "https://www.googleapis.com/auth/calendar.events"

app = Flask(__name__, static_folder="static", static_url_path="/static")


def _read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_tokens():
    return _read_json(TOKENS_FILE, {})


def save_tokens(tokens):
    _write_json(TOKENS_FILE, tokens)


def load_states():
    states = _read_json(STATE_FILE, {})
    # limpia estados antiguos
    now = time.time()
    states = {k: v for k, v in states.items() if now - v.get("created_at", 0) < 900}
    _write_json(STATE_FILE, states)
    return states


def save_states(states):
    _write_json(STATE_FILE, states)


def local_base_url():
    # Se fuerza 127.0.0.1 para que coincida con los redirect URI de Google/Spotify.
    return f"http://127.0.0.1:{PORT}"


def json_error(message, status=400, detail=None):
    payload = {"ok": False, "error": message}
    if detail is not None:
        payload["detail"] = detail
    return jsonify(payload), status



def clear_spotify_auth(reason="reauth_required", detail=None):
    """Descarta tokens de Spotify cuando el refresh token ya no sirve.

    Desde el cambio comunicado por Spotify para 2026, un refresh token puede
    caducar y el endpoint /api/token puede devolver invalid_grant. En ese caso
    no hay que seguir reintentando el mismo refresh_token: lo eliminamos y
    dejamos una marca para que el frontend/setup pidan reconectar.
    """
    tokens = load_tokens()
    tokens.pop("spotify", None)
    tokens["spotify_auth_error"] = {
        "reason": reason,
        "detail": detail or "",
        "at": int(time.time()),
    }
    save_tokens(tokens)
    try:
        SPOTIFY_PLAYLIST_CACHE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def spotify_reauth_detail():
    err = (load_tokens().get("spotify_auth_error") or {})
    return {
        "reauth_required": True,
        "error": {
            "message": "Spotify necesita reconexión. El token de actualización ha caducado o ha sido revocado.",
            "reason": err.get("reason") or "reauth_required",
        },
        "last_auth_error": err,
    }


# ─────────────────────────────────────────────────────────────
# Static pages
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "dashboard.html")


@app.route("/dashboard.html")
def dashboard():
    return send_from_directory(app.static_folder, "dashboard.html")


@app.route("/setup")
def setup_page():
    spotify_ok = bool(SPOTIFY_CLIENT_ID)
    google_ok = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
    tokens_now = load_tokens()
    spotify_token = bool(tokens_now.get("spotify", {}).get("refresh_token"))
    spotify_auth_error = tokens_now.get("spotify_auth_error") or {}
    google_token = bool(tokens_now.get("google", {}).get("refresh_token"))
    return f"""
<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>XENEON Dashboard - Setup</title>
<style>
body{{background:#0d0d0f;color:#f4f4f5;font-family:Segoe UI,Arial,sans-serif;margin:0;padding:30px;}}
.card{{max-width:760px;margin:0 auto 16px;background:#15151a;border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:22px;}}
h1{{font-size:24px;margin:0 0 8px}} h2{{font-size:16px;margin:0 0 10px;color:#fff}} p,li{{color:#aaa;line-height:1.5}} code{{background:#22232a;border:1px solid rgba(255,255,255,.08);padding:3px 6px;border-radius:6px;color:#e5e7eb}}
a.btn{{display:inline-flex;align-items:center;justify-content:center;padding:11px 18px;border-radius:999px;text-decoration:none;font-weight:700;margin-right:8px;margin-top:8px}}
.green{{background:#1db954;color:#07120a}} .blue{{background:#4285f4;color:white}} .dark{{background:#24242b;color:#ddd;border:1px solid rgba(255,255,255,.12)}}
.badge{{display:inline-block;border-radius:999px;padding:4px 9px;font-size:12px;font-weight:700;margin-left:8px}} .ok{{background:rgba(34,197,94,.15);color:#22c55e}} .bad{{background:rgba(245,158,11,.15);color:#f59e0b}}
</style></head><body>
<div class="card"><h1>Configuración XENEON Dashboard</h1><p>Esta página se abre en Chrome/Edge normal. La pantalla XENEON solo debe cargar el dashboard local, sin navegar a Google ni Spotify.</p></div>
<div class="card"><h2>Spotify <span class="badge {'ok' if spotify_ok else 'bad'}">{'configurado' if spotify_ok else 'falta Client ID'}</span> <span class="badge {'ok' if spotify_token else 'bad'}">{'conectado' if spotify_token else 'sin token'}</span></h2>
<p>Redirect URI que debes tener en Spotify Developer Dashboard:</p><p><code>{local_base_url()}/auth/spotify/callback</code></p>
<a class="btn green" href="/auth/spotify">Conectar Spotify</a>
<a class="btn dark" href="/auth/spotify/clear">Limpiar token Spotify</a>
{f'<p><b>Último aviso:</b> {spotify_auth_error.get("reason", "")} — vuelve a conectar Spotify si ves este mensaje.</p>' if spotify_auth_error else ''}</div>
<div class="card"><h2>Google Calendar <span class="badge {'ok' if google_ok else 'bad'}">{'configurado' if google_ok else 'faltan credenciales'}</span> <span class="badge {'ok' if google_token else 'bad'}">{'conectado' if google_token else 'sin token'}</span></h2>
<p>Redirect URI que debes tener en Google Cloud:</p><p><code>{local_base_url()}/auth/google/callback</code></p>
<a class="btn blue" href="/auth/google">Conectar Google Calendar</a></div>
<div class="card"><h2>URL para XENEON/iCUE</h2><p>Usa esta URL directa o dentro de un iframe:</p><p><code>{local_base_url()}/dashboard.html</code></p>
<a class="btn dark" href="/dashboard.html">Abrir dashboard</a></div>
</body></html>
"""



@app.route("/auth/spotify/clear")
def auth_spotify_clear():
    clear_spotify_auth("manual_clear", "Token limpiado manualmente desde setup")
    return redirect("/setup")

# ─────────────────────────────────────────────────────────────
# Spotify OAuth + API
# ─────────────────────────────────────────────────────────────
def pkce_verifier():
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@app.route("/auth/spotify")
def auth_spotify():
    if not SPOTIFY_CLIENT_ID:
        return "Falta SPOTIFY_CLIENT_ID en .env", 500
    state = secrets.token_urlsafe(24)
    verifier = pkce_verifier()
    states = load_states()
    states[state] = {"provider": "spotify", "verifier": verifier, "created_at": time.time()}
    save_states(states)
    params = {
        "response_type": "code",
        "client_id": SPOTIFY_CLIENT_ID,
        "scope": SPOTIFY_SCOPES,
        "redirect_uri": f"{local_base_url()}/auth/spotify/callback",
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": pkce_challenge(verifier),
    }
    return redirect("https://accounts.spotify.com/authorize?" + urlencode(params))


@app.route("/auth/spotify/callback")
def auth_spotify_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    states = load_states()
    st = states.pop(state, None) if state else None
    save_states(states)
    if not code or not st or st.get("provider") != "spotify":
        return "Estado OAuth de Spotify inválido. Vuelve a iniciar conexión desde /setup", 400
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{local_base_url()}/auth/spotify/callback",
        "client_id": SPOTIFY_CLIENT_ID,
        "code_verifier": st["verifier"],
    }
    r = requests.post("https://accounts.spotify.com/api/token", data=data, timeout=20)
    if not r.ok:
        return f"Error token Spotify: {r.status_code}<pre>{r.text}</pre>", 400
    d = r.json()
    tokens = load_tokens()
    tokens.pop("spotify_auth_error", None)
    tokens["spotify"] = {
        "access_token": d.get("access_token"),
        "refresh_token": d.get("refresh_token"),
        "expires_at": time.time() + int(d.get("expires_in", 3600)) - 60,
        "refresh_token_issued_at": int(time.time()),
    }
    save_tokens(tokens)
    return success_page("Spotify conectado", "Ya puedes cerrar esta pestaña. XENEON usará el backend local para Spotify.")


def spotify_refresh():
    tokens = load_tokens()
    sp = tokens.get("spotify") or {}
    if not sp.get("refresh_token"):
        return None
    if sp.get("access_token") and sp.get("expires_at", 0) > time.time():
        return sp["access_token"]

    data = {
        "grant_type": "refresh_token",
        "refresh_token": sp["refresh_token"],
        "client_id": SPOTIFY_CLIENT_ID,
    }
    try:
        r = requests.post("https://accounts.spotify.com/api/token", data=data, timeout=20)
    except Exception as exc:
        # Error temporal de red: no borramos tokens, solo dejamos que la llamada falle.
        print("Spotify refresh network error:", exc)
        return None

    try:
        d = r.json() if r.text else {}
    except Exception:
        d = {"raw": r.text}

    if not r.ok:
        error_code = d.get("error") if isinstance(d, dict) else None
        error_desc = d.get("error_description") if isinstance(d, dict) else None
        err_text = (str(error_code or "") + " " + str(error_desc or "")).lower()

        # Cambio Spotify 2026: refresh_token caducado/revocado => invalid_grant.
        # No se debe reintentar indefinidamente el mismo token. Lo descartamos
        # y el usuario debe volver a autorizar desde /setup.
        #
        # Spotify también puede devolver temporalmente:
        #   {"error":"server_error", "error_description":"Failed to remove token"}
        # en el mismo escenario de token inválido/revocado. Si no lo limpiamos,
        # el backend se queda en un bucle de 401/refresh fallido y parece que
        # Spotify no conecta. Lo tratamos como reautorización obligatoria.
        if error_code == "invalid_grant" or "failed to remove token" in err_text:
            clear_spotify_auth(error_code or "refresh_failed", error_desc or "refresh_token expired or revoked")
            return None

        # Otros errores pueden ser de configuración/red/Spotify. No borramos el token
        # salvo que Spotify confirme claramente que no puede usarse.
        print("Spotify refresh error:", r.status_code, d)
        return None

    new_refresh = d.get("refresh_token")
    sp["access_token"] = d.get("access_token")
    if new_refresh:
        sp["refresh_token"] = new_refresh
        sp["refresh_token_issued_at"] = int(time.time())
    else:
        sp.setdefault("refresh_token_issued_at", int(time.time()))
    sp["expires_at"] = time.time() + int(d.get("expires_in", 3600)) - 60
    tokens["spotify"] = sp
    tokens.pop("spotify_auth_error", None)
    save_tokens(tokens)
    return sp["access_token"]


def spotify_api(endpoint, method="GET", payload=None, params=None):
    token = spotify_refresh()
    if not token:
        detail = spotify_reauth_detail() if (load_tokens().get("spotify_auth_error") or {}) else {"error": "Spotify no conectado"}
        return None, 401, detail
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.spotify.com/v1" + endpoint
    r = requests.request(method, url, headers=headers, json=payload, params=params, timeout=20)
    if r.status_code == 204:
        return {}, 204, None
    try:
        body = r.json() if r.text else {}
    except Exception:
        body = {"raw": r.text}
    if r.status_code == 429:
        body = body or {}
        body["retry_after"] = r.headers.get("Retry-After")
        body["rate_limited"] = True
    return body, r.status_code, None if r.ok else body



def spotify_error_message(err):
    if isinstance(err, dict):
        e = err.get("error")
        if isinstance(e, dict):
            return e.get("message") or e.get("reason") or json.dumps(err, ensure_ascii=False)
        if isinstance(e, str):
            return e
        return err.get("message") or json.dumps(err, ensure_ascii=False)
    return str(err) if err else "Error desconocido"


def spotify_list_devices():
    data, status, err = spotify_api("/me/player/devices")
    if status == 401:
        return [], status, err
    if not data or status >= 400:
        return [], status, err
    return data.get("devices") or [], status, None


def spotify_choose_device(preferred_id=None, transfer_if_needed=True):
    """Devuelve un device_id utilizable y, si no hay activo, intenta transferir la sesión.
    Spotify exige que exista al menos un cliente abierto: app de escritorio, móvil, web player, etc.
    """
    devices, status, err = spotify_list_devices()
    if status == 401:
        return None, "Spotify no conectado", err
    available = [d for d in devices if d.get("id") and not d.get("is_restricted")]
    if not available:
        return None, "No hay ningún dispositivo Spotify disponible. Abre Spotify en el PC o móvil y pulsa actualizar dispositivos.", err
    chosen = None
    if preferred_id:
        chosen = next((d for d in available if d.get("id") == preferred_id), None)
    if not chosen:
        chosen = next((d for d in available if d.get("is_active")), None)
    if not chosen:
        # Preferimos desktop/computer si existe; si no, el primero disponible.
        chosen = next((d for d in available if (d.get("type") or "").lower() in ("computer", "speaker")), None) or available[0]
    dev_id = chosen.get("id")
    if transfer_if_needed and dev_id and not chosen.get("is_active"):
        spotify_api("/me/player", "PUT", {"device_ids": [dev_id], "play": False})
    return dev_id, None, None


def spotify_control(endpoint, method="POST", payload=None, params=None, preferred_device_id=None, require_device=True):
    params = dict(params or {})
    if require_device and "device_id" not in params:
        device_id, msg, err = spotify_choose_device(preferred_device_id)
        if not device_id:
            return None, 409, {"error": {"message": msg}, "detail": err}
        params["device_id"] = device_id
    return spotify_api(endpoint, method, payload, params=params)

def simplify_track(track):
    if not track:
        return None
    album = track.get("album") or {}
    images = album.get("images") or []
    return {
        "id": track.get("id"),
        "uri": track.get("uri"),
        "name": track.get("name") or "—",
        "artists": ", ".join(a.get("name", "") for a in (track.get("artists") or []) if a.get("name")),
        "album": album.get("name") or "",
        "image": images[0]["url"] if images else "",
        "duration_ms": track.get("duration_ms") or 0,
        "external_url": ((track.get("external_urls") or {}).get("spotify")),
    }


@app.route("/api/status")
def api_status():
    tokens = load_tokens()
    sp = tokens.get("spotify") or {}
    issued_at = sp.get("refresh_token_issued_at")
    days_left = None
    if issued_at:
        try:
            days_left = max(0, 183 - int((time.time() - float(issued_at)) // 86400))
        except Exception:
            days_left = None
    return jsonify({
        "spotify_configured": bool(SPOTIFY_CLIENT_ID),
        "spotify_connected": bool(sp.get("refresh_token")),
        "spotify_auth_error": tokens.get("spotify_auth_error") or None,
        "spotify_refresh_token_days_left_estimate": days_left,
        "google_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "google_connected": bool((tokens.get("google") or {}).get("refresh_token")),
        "default_city": DEFAULT_CITY,
    })


@app.route("/api/spotify/current")
def api_spotify_current():
    player, status, err = spotify_api("/me/player")
    devices, _, _ = spotify_list_devices()
    active_device = next((d for d in devices if d.get("is_active")), None)
    first_device = active_device or next((d for d in devices if d.get("id") and not d.get("is_restricted")), None)
    if status == 401:
        return json_error("Spotify no conectado", 401)
    if status == 204 or not player:
        return jsonify({"ok": True, "playing": False, "track": None, "device": first_device or {}, "devices": devices})
    item = player.get("item") if isinstance(player, dict) else None
    track = simplify_track(item) if item and item.get("type") == "track" else None
    return jsonify({
        "ok": True,
        "playing": bool(player.get("is_playing")),
        "progress_ms": player.get("progress_ms") or 0,
        "shuffle": bool(player.get("shuffle_state")),
        "repeat": player.get("repeat_state") or "off",
        "device": player.get("device") or first_device or {},
        "devices": devices,
        "track": track,
    })


@app.route("/api/spotify/devices")
def api_spotify_devices():
    devices, status, err = spotify_list_devices()
    if status == 401:
        return json_error("Spotify no conectado", 401)
    if status and status >= 400:
        return json_error("No se pudieron leer dispositivos", status, err)
    return jsonify({"ok": True, "devices": devices})


@app.route("/api/spotify/transfer", methods=["POST"])
def api_spotify_transfer():
    body = request.get_json(silent=True) or {}
    device_id = body.get("device_id")
    if not device_id:
        device_id, msg, err = spotify_choose_device(None, transfer_if_needed=False)
        if not device_id:
            return json_error(msg, 409, err)
    data, status, err = spotify_api("/me/player", "PUT", {"device_ids": [device_id], "play": False})
    if status in (200, 202, 204):
        return jsonify({"ok": True})
    return json_error("No se pudo activar ese dispositivo", status or 500, err)



@app.route("/api/spotify/playlists")
def api_spotify_playlists():
    """Lista playlists sin disparar el rate-limit de Spotify.

    En la v5 se intentaba completar el contador haciendo llamadas extra por
    cada playlist. Con 8-10 playlists + recargas de XENEON/Chrome era fácil
    recibir 429 y entonces no se pintaba ninguna lista. Aquí usamos el total
    que ya devuelve /me/playlists (`tracks.total`) y cacheamos el resultado.
    Si Spotify limita temporalmente, devolvemos la última cache válida.
    """
    def as_int(value):
        try:
            if value is None:
                return None
            return int(value)
        except Exception:
            return None

    def normalize_playlist(p):
        p = p or {}
        images = p.get("images") or []
        owner = p.get("owner") or {}
        pl_id = p.get("id") or ""
        tracks_total = as_int(((p.get("tracks") or {}).get("total")))
        # Compatibilidad con el HTML antiguo, por si llega items.total.
        if tracks_total is None:
            tracks_total = as_int(((p.get("items") or {}).get("total")))
        return {
            "id": pl_id,
            "uri": p.get("uri") or (f"spotify:playlist:{pl_id}" if pl_id else ""),
            "name": p.get("name") or "Playlist",
            "image": images[0]["url"] if images else "",
            "tracks_total": tracks_total,
            "tracks_count_known": tracks_total is not None,
            "owner": owner.get("display_name") or owner.get("id") or "",
            "external_url": ((p.get("external_urls") or {}).get("spotify")),
        }

    def cache_payload_ok(payload):
        return isinstance(payload, dict) and isinstance(payload.get("playlists"), list)

    force = request.args.get("force") == "1"
    cached_doc = _read_json(SPOTIFY_PLAYLIST_CACHE_FILE, {})
    cached_payload = cached_doc.get("payload") if isinstance(cached_doc, dict) else None
    cached_age = time.time() - float(cached_doc.get("ts", 0) or 0) if isinstance(cached_doc, dict) else 999999

    if not force and cache_payload_ok(cached_payload) and cached_age < SPOTIFY_PLAYLIST_CACHE_SECONDS:
        payload = dict(cached_payload)
        payload["from_cache"] = True
        payload["cache_age_seconds"] = int(cached_age)
        return jsonify(payload)

    all_items = []
    offset = 0
    limit = 50
    last_err = None
    last_status = None

    for _ in range(20):
        data, status, err = spotify_api("/me/playlists", params={"limit": limit, "offset": offset})
        last_status, last_err = status, err
        if status == 401:
            return json_error("Spotify no conectado", 401)
        if status == 429:
            if cache_payload_ok(cached_payload):
                payload = dict(cached_payload)
                payload["from_cache"] = True
                payload["rate_limited"] = True
                payload["retry_after"] = (err or {}).get("retry_after")
                return jsonify(payload)
            retry = (err or {}).get("retry_after") or "unos segundos"
            return json_error(f"Spotify ha limitado temporalmente las peticiones. Espera {retry} y recarga.", 429, err)
        if not data or status >= 400:
            break
        items = data.get("items") or []
        all_items.extend(items)
        if not data.get("next") or not items:
            break
        offset += limit

    if not all_items:
        if cache_payload_ok(cached_payload):
            payload = dict(cached_payload)
            payload["from_cache"] = True
            payload["stale"] = True
            payload["warning"] = "No se pudieron refrescar playlists; se muestra la cache anterior."
            return jsonify(payload)
        return json_error("No se pudieron leer playlists", last_status or 500, last_err)

    playlists = [normalize_playlist(p) for p in all_items if p]
    payload = {"ok": True, "playlists": playlists, "from_cache": False, "cached_at": int(time.time())}
    _write_json(SPOTIFY_PLAYLIST_CACHE_FILE, {"ts": time.time(), "payload": payload})
    return jsonify(payload)


@app.route("/api/spotify/playlists/<playlist_id>/tracks")
def api_spotify_playlist_tracks(playlist_id):
    """Devuelve canciones cuando Spotify permite leerlas por API.
    Primero usa /items, igual que el dashboard HTML anterior; si falla por endpoint antiguo,
    cae a /tracks. Si Spotify devuelve 403, el frontend mostrará el embed oficial.
    """
    tracks = []
    total = 0
    offset = 0
    last_err = None

    for _ in range(10):
        params = {
            "limit": 50,
            "offset": offset,
            "market": "ES",
            "additional_types": "track",
            "fields": "total,next,items(item(id,name,type,duration_ms,uri,artists(name),album(name,images),external_urls))",
        }
        data, status, err = spotify_api(f"/playlists/{quote(playlist_id)}/items", params=params)

        # Compatibilidad con cuentas/instalaciones donde /items pueda dar 404.
        if status == 404:
            params = {
                "limit": 50,
                "offset": offset,
                "market": "ES",
                "fields": "total,next,items(track(id,name,type,duration_ms,uri,artists(name),album(name,images),external_urls))",
            }
            data, status, err = spotify_api(f"/playlists/{quote(playlist_id)}/tracks", params=params)

        if status == 401:
            return json_error("Spotify no conectado", 401)
        if status == 403:
            return jsonify({"ok": False, "restricted": True, "error": "Spotify no permite leer las canciones de esta playlist con la API.", "tracks": [], "total": 0}), 403
        if not data or status >= 400:
            last_err = err
            break

        total = data.get("total") or total
        items = data.get("items") or []
        for item in items:
            tr = item.get("item") or item.get("track") or {}
            if tr and tr.get("type") == "track":
                simple = simplify_track(tr)
                if simple and simple.get("uri"):
                    tracks.append(simple)
        if not data.get("next") or not items:
            break
        offset += 50

    if not tracks and last_err:
        return json_error("No se pudieron leer canciones", 500, last_err)
    return jsonify({"ok": True, "tracks": tracks, "total": total or len(tracks)})


def spotify_current_snapshot(max_wait_seconds=0):
    """Lee el estado actual de Spotify con una espera corta opcional.
    Spotify Connect a veces tarda unos cientos de ms en reflejar un play/next.
    """
    deadline = time.time() + max_wait_seconds
    last_payload = None
    while True:
        player, status, err = spotify_api("/me/player")
        devices, _, _ = spotify_list_devices()
        active_device = next((d for d in devices if d.get("is_active")), None)
        first_device = active_device or next((d for d in devices if d.get("id") and not d.get("is_restricted")), None)
        if status == 401:
            return {"ok": False, "error": "Spotify no conectado"}
        if status == 204 or not player:
            last_payload = {"ok": True, "playing": False, "track": None, "device": first_device or {}, "devices": devices}
        else:
            item = player.get("item") if isinstance(player, dict) else None
            track = simplify_track(item) if item and item.get("type") == "track" else None
            last_payload = {
                "ok": True,
                "playing": bool(player.get("is_playing")),
                "progress_ms": player.get("progress_ms") or 0,
                "shuffle": bool(player.get("shuffle_state")),
                "repeat": player.get("repeat_state") or "off",
                "device": player.get("device") or first_device or {},
                "devices": devices,
                "track": track,
            }
            if track:
                return last_payload
        if time.time() >= deadline:
            return last_payload
        time.sleep(0.35)


@app.route("/api/spotify/play", methods=["POST"])
def api_spotify_play():
    body = request.get_json(silent=True) or {}
    payload = {}
    if body.get("context_uri"):
        payload["context_uri"] = body["context_uri"]
    if body.get("track_uri"):
        if body.get("context_uri"):
            payload["offset"] = {"uri": body["track_uri"]}
        else:
            payload["uris"] = [body["track_uri"]]
    if body.get("position_ms") is not None:
        payload["position_ms"] = int(body.get("position_ms") or 0)

    data, status, err = spotify_control("/me/player/play", "PUT", payload or None, preferred_device_id=body.get("device_id"))
    if status in (200, 202, 204):
        snapshot = spotify_current_snapshot(max_wait_seconds=2.2)
        return jsonify({"ok": True, "current": snapshot})
    msg = spotify_error_message(err)
    if status in (404, 409):
        msg = "No hay un dispositivo activo. Abre Spotify en el PC/móvil, selecciónalo en el desplegable y prueba otra vez."
    elif status == 403:
        msg = "Spotify no permite controlar la reproducción. Normalmente requiere Spotify Premium y permisos correctos."
    return json_error(msg or "No se pudo reproducir en Spotify", status or 500, err)


@app.route("/api/spotify/pause", methods=["POST"])
def api_spotify_pause():
    body = request.get_json(silent=True) or {}
    data, status, err = spotify_control("/me/player/pause", "PUT", preferred_device_id=body.get("device_id"))
    if status in (200, 202, 204):
        return jsonify({"ok": True})
    return json_error(spotify_error_message(err) or "No se pudo pausar", status or 500, err)


@app.route("/api/spotify/next", methods=["POST"])
def api_spotify_next():
    body = request.get_json(silent=True) or {}
    data, status, err = spotify_control("/me/player/next", "POST", preferred_device_id=body.get("device_id"))
    if status in (200, 202, 204):
        return jsonify({"ok": True})
    return json_error(spotify_error_message(err) or "No se pudo pasar a la siguiente", status or 500, err)


@app.route("/api/spotify/prev", methods=["POST"])
def api_spotify_prev():
    body = request.get_json(silent=True) or {}
    data, status, err = spotify_control("/me/player/previous", "POST", preferred_device_id=body.get("device_id"))
    if status in (200, 202, 204):
        return jsonify({"ok": True})
    return json_error(spotify_error_message(err) or "No se pudo volver a la anterior", status or 500, err)


@app.route("/api/spotify/seek", methods=["POST"])
def api_spotify_seek():
    body = request.get_json(silent=True) or {}
    ms = int(body.get("position_ms") or 0)
    data, status, err = spotify_control("/me/player/seek", "PUT", params={"position_ms": ms}, preferred_device_id=body.get("device_id"))
    if status in (200, 202, 204):
        return jsonify({"ok": True})
    return json_error(spotify_error_message(err) or "No se pudo mover la canción", status or 500, err)


@app.route("/api/spotify/volume", methods=["POST"])
def api_spotify_volume():
    body = request.get_json(silent=True) or {}
    vol = max(0, min(100, int(body.get("volume") or 0)))
    data, status, err = spotify_control("/me/player/volume", "PUT", params={"volume_percent": vol}, preferred_device_id=body.get("device_id"))
    if status in (200, 202, 204):
        return jsonify({"ok": True})
    return json_error(spotify_error_message(err) or "No se pudo cambiar el volumen", status or 500, err)


@app.route("/api/spotify/shuffle", methods=["POST"])
def api_spotify_shuffle():
    body = request.get_json(silent=True) or {}
    state = bool(body.get("state"))
    data, status, err = spotify_control("/me/player/shuffle", "PUT", params={"state": str(state).lower()}, preferred_device_id=body.get("device_id"))
    if status in (200, 202, 204):
        return jsonify({"ok": True})
    return json_error(spotify_error_message(err) or "No se pudo cambiar aleatorio", status or 500, err)


@app.route("/api/spotify/repeat", methods=["POST"])
def api_spotify_repeat():
    body = request.get_json(silent=True) or {}
    state = body.get("state") or "off"
    if state not in ("off", "track", "context"):
        state = "off"
    data, status, err = spotify_control("/me/player/repeat", "PUT", params={"state": state}, preferred_device_id=body.get("device_id"))
    if status in (200, 202, 204):
        return jsonify({"ok": True})
    return json_error(spotify_error_message(err) or "No se pudo cambiar repetición", status or 500, err)


# ─────────────────────────────────────────────────────────────
# Google OAuth + Calendar API
# ─────────────────────────────────────────────────────────────
@app.route("/auth/google")
def auth_google():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return "Faltan GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET en .env", 500
    state = secrets.token_urlsafe(24)
    states = load_states()
    states[state] = {"provider": "google", "created_at": time.time()}
    save_states(states)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{local_base_url()}/auth/google/callback",
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    }
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@app.route("/auth/google/callback")
def auth_google_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    states = load_states()
    st = states.pop(state, None) if state else None
    save_states(states)
    if not code or not st or st.get("provider") != "google":
        return "Estado OAuth de Google inválido. Vuelve a iniciar conexión desde /setup", 400
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": f"{local_base_url()}/auth/google/callback",
        "grant_type": "authorization_code",
    }
    r = requests.post("https://oauth2.googleapis.com/token", data=data, timeout=20)
    if not r.ok:
        return f"Error token Google: {r.status_code}<pre>{r.text}</pre>", 400
    d = r.json()
    tokens = load_tokens()
    tokens["google"] = {
        "access_token": d.get("access_token"),
        "refresh_token": d.get("refresh_token") or (tokens.get("google") or {}).get("refresh_token"),
        "expires_at": time.time() + int(d.get("expires_in", 3600)) - 60,
    }
    save_tokens(tokens)
    return success_page("Google Calendar conectado", "Ya puedes cerrar esta pestaña. XENEON usará el backend local para el calendario.")


def google_refresh():
    tokens = load_tokens()
    g = tokens.get("google") or {}
    if not g.get("refresh_token"):
        return None
    if g.get("access_token") and g.get("expires_at", 0) > time.time():
        return g["access_token"]
    data = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": g["refresh_token"],
        "grant_type": "refresh_token",
    }
    r = requests.post("https://oauth2.googleapis.com/token", data=data, timeout=20)
    if not r.ok:
        return None
    d = r.json()
    g["access_token"] = d.get("access_token")
    g["expires_at"] = time.time() + int(d.get("expires_in", 3600)) - 60
    tokens["google"] = g
    save_tokens(tokens)
    return g["access_token"]


def google_api(endpoint, method="GET", payload=None, params=None):
    token = google_refresh()
    if not token:
        return None, 401, {"error": "Google Calendar no conectado"}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://www.googleapis.com/calendar/v3" + endpoint
    r = requests.request(method, url, headers=headers, json=payload, params=params, timeout=20)
    if r.status_code == 204:
        return {}, 204, None
    try:
        body = r.json() if r.text else {}
    except Exception:
        body = {"raw": r.text}
    return body, r.status_code, None if r.ok else body


@app.route("/api/calendar/events")
def api_calendar_events():
    now = datetime.now(timezone.utc)
    time_min = request.args.get("timeMin") or now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    time_max = request.args.get("timeMax") or (now + timedelta(days=45)).isoformat()
    params = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 100,
    }
    data, status, err = google_api("/calendars/primary/events", params=params)
    if status == 401:
        return json_error("Google Calendar no conectado", 401)
    if status >= 400 or not data:
        return json_error("No se pudieron leer eventos", status or 500, err)
    events = []
    for e in data.get("items") or []:
        start = e.get("start") or {}
        end = e.get("end") or {}
        events.append({
            "id": e.get("id"),
            "summary": e.get("summary") or "(sin título)",
            "location": e.get("location") or "",
            "description": e.get("description") or "",
            "start": start.get("dateTime") or start.get("date"),
            "end": end.get("dateTime") or end.get("date"),
            "all_day": bool(start.get("date")),
            "htmlLink": e.get("htmlLink") or "",
        })
    return jsonify({"ok": True, "events": events})


@app.route("/api/calendar/events", methods=["POST"])
def api_calendar_create_event():
    b = request.get_json(silent=True) or {}
    title = (b.get("title") or "").strip()
    if not title:
        return json_error("El título es obligatorio", 400)
    all_day = bool(b.get("all_day"))
    if all_day:
        start_date = b.get("date")
        if not start_date:
            return json_error("La fecha es obligatoria", 400)
        # En Google Calendar la fecha de fin de eventos de todo el día es exclusiva.
        end_date = (datetime.fromisoformat(start_date) + timedelta(days=1)).date().isoformat()
        payload = {
            "summary": title,
            "start": {"date": start_date},
            "end": {"date": end_date},
        }
    else:
        start = b.get("start")
        end = b.get("end")
        if not start or not end:
            return json_error("Inicio y fin son obligatorios", 400)
        payload = {
            "summary": title,
            "start": {"dateTime": start, "timeZone": "Europe/Madrid"},
            "end": {"dateTime": end, "timeZone": "Europe/Madrid"},
        }
    if b.get("location"):
        payload["location"] = b.get("location")
    if b.get("description"):
        payload["description"] = b.get("description")
    data, status, err = google_api("/calendars/primary/events", "POST", payload)
    if status in (200, 201):
        return jsonify({"ok": True, "event": data})
    return json_error("No se pudo crear el evento", status or 500, err)


@app.route("/api/calendar/events/<path:event_id>", methods=["DELETE"])
def api_calendar_delete_event(event_id):
    data, status, err = google_api(f"/calendars/primary/events/{quote(event_id, safe='')}", "DELETE")
    if status in (200, 204):
        return jsonify({"ok": True})
    return json_error("No se pudo borrar el evento", status or 500, err)


# ─────────────────────────────────────────────────────────────
# Weather through local backend
# ─────────────────────────────────────────────────────────────
WEATHER_CODES = {
    0: "Despejado", 1: "Mayormente despejado", 2: "Parcialmente nublado", 3: "Nublado",
    45: "Niebla", 48: "Niebla", 51: "Llovizna", 53: "Llovizna", 55: "Llovizna intensa",
    61: "Lluvia", 63: "Lluvia", 65: "Lluvia intensa", 71: "Nieve", 73: "Nieve", 75: "Nieve intensa",
    80: "Chubascos", 81: "Chubascos", 82: "Chubascos fuertes", 95: "Tormenta", 96: "Tormenta", 99: "Tormenta fuerte",
}


@app.route("/api/weather")
def api_weather():
    city = request.args.get("city") or DEFAULT_CITY
    try:
        geo = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={
            "name": city, "count": 1, "language": "es", "format": "json"
        }, timeout=15).json()
        results = geo.get("results") or []
        if not results:
            return json_error("Ciudad no encontrada", 404)
        g = results[0]
        lat, lon = g["latitude"], g["longitude"]
        forecast = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,uv_index_max",
            "timezone": "Europe/Madrid",
            "forecast_days": 7,
        }, timeout=15).json()
        current = forecast.get("current") or {}
        daily = forecast.get("daily") or {}
        days = []
        for i, date in enumerate(daily.get("time") or []):
            code = (daily.get("weather_code") or [None])[i]
            days.append({
                "date": date,
                "code": code,
                "desc": WEATHER_CODES.get(code, "Tiempo"),
                "min": round((daily.get("temperature_2m_min") or [0])[i]),
                "max": round((daily.get("temperature_2m_max") or [0])[i]),
                "uv": round((daily.get("uv_index_max") or [0])[i], 1),
            })
        code = current.get("weather_code")
        return jsonify({
            "ok": True,
            "city": g.get("name") or city,
            "country": g.get("country_code") or "",
            "current": {
                "temp": round(current.get("temperature_2m", 0)),
                "feels": round(current.get("apparent_temperature", 0)),
                "humidity": round(current.get("relative_humidity_2m", 0)),
                "wind": round(current.get("wind_speed_10m", 0)),
                "code": code,
                "desc": WEATHER_CODES.get(code, "Tiempo"),
            },
            "daily": days,
        })
    except Exception as e:
        return json_error("No se pudo consultar el tiempo", 500, str(e))



@app.route("/api/image")
def api_image_proxy():
    """Proxy sencillo de imágenes para que XENEON solo cargue recursos locales."""
    url = request.args.get("u") or ""
    if not (url.startswith("https://") or url.startswith("http://")):
        return "", 404
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "XENEON-Dashboard/1.0"})
        if not r.ok:
            return "", 404
        ctype = r.headers.get("content-type", "image/jpeg")
        return r.content, 200, {"Content-Type": ctype, "Cache-Control": "public, max-age=3600"}
    except Exception:
        return "", 404

def success_page(title, msg):
    return f"""
<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{background:#0d0d0f;color:#fff;font-family:Segoe UI,Arial,sans-serif;display:grid;place-items:center;height:100vh;margin:0}}.box{{background:#15151a;border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:28px;max-width:520px;text-align:center}}h1{{margin:0 0 10px;color:#22c55e}}p{{color:#aaa}}</style></head>
<body><div class="box"><h1>✓ {title}</h1><p>{msg}</p><p><a style="color:#60a5fa" href="/setup">Volver a setup</a></p></div></body></html>
"""


if __name__ == "__main__":
    print(f"XENEON Dashboard local arrancado en http://{HOST}:{PORT}/dashboard.html")
    print(f"Setup: http://127.0.0.1:{PORT}/setup")
    app.run(host=HOST, port=PORT, debug=False)
