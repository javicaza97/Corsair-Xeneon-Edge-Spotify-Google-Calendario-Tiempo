import base64
import hashlib
import json
import os
import secrets
import time
from threading import Lock
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
SETTINGS_FILE = DATA_DIR / "settings.json"
PINNED_PLAYLISTS_FILE = DATA_DIR / "pinned_playlists.json"
PLAYLIST_ORDER_FILE = DATA_DIR / "playlist_order.json"
SPOTIFY_ERRORS_LOG_FILE = DATA_DIR / "spotify_errors.log"
SPOTIFY_PLAYLIST_CACHE_SECONDS = int(os.getenv("SPOTIFY_PLAYLIST_CACHE_SECONDS", "900"))
# Spotify limita bastante el endpoint /me/player/devices si se llama muchas veces
# al cargar el dashboard. Cacheamos el resultado y respetamos Retry-After para
# evitar falsos "no hay dispositivos" cuando realmente es rate limit HTTP 429.
SPOTIFY_DEVICES_CACHE_SECONDS = int(os.getenv("SPOTIFY_DEVICES_CACHE_SECONDS", "45"))
SPOTIFY_DEVICES_RATE_LIMIT_FALLBACK_SECONDS = int(os.getenv("SPOTIFY_DEVICES_RATE_LIMIT_FALLBACK_SECONDS", "25"))
SPOTIFY_DEVICES_CACHE = {
    "devices": [],
    "fetched_at": 0.0,
    "rate_limited_until": 0.0,
    "last_error": None,
}
SPOTIFY_DEVICES_LOCK = Lock()

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
    "playlist-modify-private",
    "playlist-modify-public",
    "user-library-read",
    "user-library-modify",
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


def load_settings():
    settings = _read_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        settings = {}
    return settings


def save_settings(settings):
    if not isinstance(settings, dict):
        settings = {}
    _write_json(SETTINGS_FILE, settings)


def current_weather_city():
    return (load_settings().get("weather_city") or DEFAULT_CITY or "Móstoles").strip()


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


def log_spotify_error(context, data):
    """Guarda errores de Spotify para depurar 403/429 sin exponer tokens."""
    try:
        safe = dict(data or {})
        safe.pop("access_token", None)
        safe.pop("refresh_token", None)
        line = json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "context": context, **safe}, ensure_ascii=False)
        with SPOTIFY_ERRORS_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass



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
        # Fuerza a Spotify a mostrar de nuevo la pantalla de permisos.
        # Es importante cuando añadimos scopes nuevos como playlist-modify-private/public.
        "show_dialog": "true",
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
        "scope": d.get("scope") or SPOTIFY_SCOPES,
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
    if d.get("scope"):
        sp["scope"] = d.get("scope")
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


def spotify_retry_after_seconds(err, fallback=None):
    fallback = SPOTIFY_DEVICES_RATE_LIMIT_FALLBACK_SECONDS if fallback is None else fallback
    if isinstance(err, dict):
        value = err.get("retry_after") or err.get("Retry-After")
        try:
            return max(1, int(float(value)))
        except Exception:
            pass
    return fallback


def spotify_list_devices(force=False, allow_stale=True):
    """Lee dispositivos Spotify con caché y backoff anti-429.

    El dashboard consulta estado, dispositivos y listas casi a la vez al arrancar.
    Si cada vista llama directamente a /me/player/devices, Spotify responde 429
    y el usuario ve falsamente que no hay dispositivos. Con esta caché, una
    respuesta válida se reutiliza unos segundos y, si Spotify marca rate limit,
    se devuelven los últimos dispositivos conocidos mientras dure el Retry-After.
    """
    now = time.time()
    with SPOTIFY_DEVICES_LOCK:
        cached_devices = SPOTIFY_DEVICES_CACHE.get("devices") or []
        fetched_at = float(SPOTIFY_DEVICES_CACHE.get("fetched_at") or 0)
        limited_until = float(SPOTIFY_DEVICES_CACHE.get("rate_limited_until") or 0)

        if not force and cached_devices and now - fetched_at < SPOTIFY_DEVICES_CACHE_SECONDS:
            return cached_devices, 200, {"cached": True}

        if limited_until > now:
            retry_after = max(1, int(limited_until - now))
            detail = {
                "rate_limited": True,
                "cached": bool(cached_devices),
                "retry_after": retry_after,
                "message": f"Spotify está limitando temporalmente la lectura de dispositivos. Reintenta en {retry_after} s.",
            }
            if allow_stale and cached_devices:
                return cached_devices, 200, detail
            return [], 429, detail

        data, status, err = spotify_api("/me/player/devices")

        if status == 401:
            return [], status, err

        if status == 429:
            retry_after = spotify_retry_after_seconds(err)
            SPOTIFY_DEVICES_CACHE["rate_limited_until"] = time.time() + retry_after
            detail = {
                "rate_limited": True,
                "cached": bool(cached_devices),
                "retry_after": retry_after,
                "spotify_error": err,
                "message": f"Spotify está limitando temporalmente la lectura de dispositivos. Reintenta en {retry_after} s.",
            }
            SPOTIFY_DEVICES_CACHE["last_error"] = detail
            if allow_stale and cached_devices:
                return cached_devices, 200, detail
            return [], 429, detail

        if not data or status >= 400:
            SPOTIFY_DEVICES_CACHE["last_error"] = err
            return [], status, err

        devices = data.get("devices") or []
        SPOTIFY_DEVICES_CACHE.update({
            "devices": devices,
            "fetched_at": time.time(),
            "rate_limited_until": 0.0,
            "last_error": None,
        })
        return devices, status, None


def spotify_choose_device(preferred_id=None, transfer_if_needed=True):
    """Devuelve un device_id utilizable y, si no hay activo, intenta transferir la sesión.
    Spotify exige que exista al menos un cliente abierto: app de escritorio, móvil, web player, etc.
    """
    devices, status, err = spotify_list_devices()
    if status == 401:
        return None, "Spotify no conectado", err
    # Si la UI ya conoce un device_id válido desde /me/player pero Spotify está
    # rate-limitando /devices, probamos directamente con ese dispositivo.
    if preferred_id and (status == 429 or (isinstance(err, dict) and err.get("rate_limited"))):
        return preferred_id, None, None
    available = [d for d in devices if d.get("id") and not d.get("is_restricted")]
    if not available:
        if status == 429 or (isinstance(err, dict) and err.get("rate_limited")):
            retry_after = spotify_retry_after_seconds(err)
            return None, f"Spotify está limitando temporalmente la lectura de dispositivos. Espera {retry_after} segundos y pulsa actualizar dispositivos.", err
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
        "default_city": current_weather_city(),
        "weather_city": current_weather_city(),
    })


@app.route("/api/spotify/current")
def api_spotify_current():
    player, status, err = spotify_api("/me/player")
    if status == 401:
        return json_error("Spotify no conectado", 401)

    player_device = (player.get("device") if isinstance(player, dict) else None) or {}
    if player_device.get("id"):
        # /me/player ya devuelve el dispositivo activo; así evitamos llamar a
        # /me/player/devices en cada refresco de estado y reducimos los 429.
        devices = [player_device]
        first_device = player_device
    else:
        devices, _, _ = spotify_list_devices()
        active_device = next((d for d in devices if d.get("is_active")), None)
        first_device = active_device or next((d for d in devices if d.get("id") and not d.get("is_restricted")), None)

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
        "device": player_device or first_device or {},
        "devices": devices,
        "track": track,
    })


@app.route("/api/spotify/devices")
def api_spotify_devices():
    force = (request.args.get("force") or "").lower() in ("1", "true", "yes")
    devices, status, err = spotify_list_devices(force=force, allow_stale=True)
    if status == 401:
        return json_error("Spotify no conectado", 401)
    if status == 429:
        retry_after = spotify_retry_after_seconds(err)
        return json_error(
            f"Spotify está limitando temporalmente la lectura de dispositivos. Espera {retry_after} segundos y vuelve a actualizar.",
            429,
            err,
        )
    if status and status >= 400:
        return json_error("No se pudieron leer dispositivos", status, err)
    payload = {"ok": True, "devices": devices}
    if isinstance(err, dict):
        payload.update({k: v for k, v in err.items() if k in ("cached", "rate_limited", "retry_after", "message")})
    return jsonify(payload)


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





@app.route("/api/spotify/liked")
def api_spotify_liked():
    track_id = (request.args.get("track_id") or "").strip()
    track_uri = (request.args.get("track_uri") or "").strip()
    if not track_uri and track_id:
        track_uri = "spotify:track:" + track_id
    if not track_uri.startswith("spotify:track:"):
        return json_error("Falta track_id", 400)
    # Endpoint nuevo de biblioteca (el antiguo /me/tracks/contains está deprecado).
    data, status, err = spotify_api("/me/library/contains", params={"uris": track_uri})
    if status == 401:
        return json_error("Spotify no conectado", 401)
    if status and status >= 400:
        return json_error("No se pudo comprobar Me gusta", status, err)
    liked = bool(data[0]) if isinstance(data, list) and data else False
    return jsonify({"ok": True, "liked": liked, "track_id": track_uri.split(":")[-1]})


@app.route("/api/spotify/like", methods=["POST"])
def api_spotify_like():
    body = request.get_json(silent=True) or {}
    track_uri = (body.get("track_uri") or "").strip()
    track_id = (body.get("track_id") or "").strip()
    if not track_uri and track_id:
        track_uri = "spotify:track:" + track_id
    if not track_uri.startswith("spotify:track:"):
        return json_error("No hay canción seleccionada", 400)

    target_liked = body.get("liked")
    if target_liked is None:
        current, status, err = spotify_api("/me/library/contains", params={"uris": track_uri})
        if status == 401:
            return json_error("Spotify no conectado", 401)
        if status and status >= 400:
            return json_error("No se pudo comprobar Me gusta", status, err)
        target_liked = not (bool(current[0]) if isinstance(current, list) and current else False)

    # Guardar/quitar de "Canciones que me gustan" con el endpoint nuevo de biblioteca.
    method = "PUT" if bool(target_liked) else "DELETE"
    _, status, err = spotify_api("/me/library", method, params={"uris": track_uri})
    if status == 401:
        return json_error("Spotify no conectado", 401)
    if status == 403:
        return json_error(
            "Spotify ha rechazado guardar el Me gusta",
            403,
            {"error": {"message": "Forbidden de Spotify. Si acabas de añadir permisos, limpia el token en /setup y reconecta Spotify para conceder user-library-modify."}},
        )
    if status in (200, 201, 202, 204):
        return jsonify({"ok": True, "liked": bool(target_liked), "track_id": track_uri.split(":")[-1]})
    return json_error("No se pudo actualizar Me gusta", status or 500, err)


@app.route("/api/spotify/liked-tracks")
def api_spotify_liked_tracks():
    """Lista las canciones de 'Canciones que me gustan' (biblioteca del usuario)."""
    tracks = []
    total = 0
    offset = 0
    last_err = None
    for _ in range(20):  # hasta 1000 canciones
        data, status, err = spotify_api("/me/tracks", params={"limit": 50, "offset": offset, "market": "ES"})
        if status == 401:
            return json_error("Spotify no conectado", 401)
        if not data or status >= 400:
            last_err = err
            break
        total = data.get("total") or total
        items = data.get("items") or []
        for item in items:
            tr = (item or {}).get("track") or {}
            if tr and tr.get("type") == "track":
                simple = simplify_track(tr)
                if simple and simple.get("uri"):
                    tracks.append(simple)
        if not data.get("next") or not items:
            break
        offset += 50
    if not tracks and last_err:
        return json_error("No se pudieron leer tus Me gusta", 500, last_err)
    return jsonify({"ok": True, "tracks": tracks, "total": total or len(tracks)})


@app.route("/api/spotify/resolve-playlist")
def api_spotify_resolve_playlist():
    """Resuelve una playlist a partir de su enlace, URI o ID y devuelve sus metadatos.
    Sirve para fijar listas que no aparecen en /me/playlists (p.ej. Radar de novedades),
    aunque Spotify ya no deje leer sus canciones por API: el frontend usará el embed.
    """
    raw = (request.args.get("id") or "").strip()
    # Acepta: URL completa, spotify:playlist:ID o el ID pelado. Quita query (?si=...).
    pid = raw.split("?")[0].rstrip("/").split("/")[-1].split(":")[-1]
    if not pid:
        return json_error("Falta el enlace o id de la playlist", 400)
    # 1) Intento normal por la Web API (vale para listas de usuario).
    data, status, err = spotify_api(
        f"/playlists/{quote(pid)}",
        params={"fields": "id,uri,name,images,public,collaborative,owner(display_name),tracks(total)"},
    )
    if status == 401:
        return json_error("Spotify no conectado", 401)

    if data and status < 400:
        images = (data or {}).get("images") or []
        pl = {
            "id": data.get("id") or pid,
            "uri": data.get("uri") or ("spotify:playlist:" + pid),
            "name": data.get("name") or "Playlist",
            "image": images[0]["url"] if images else "",
            "public": data.get("public"),
            "collaborative": bool(data.get("collaborative")),
            "tracks_total": ((data.get("tracks") or {}).get("total")),
        }
        return jsonify({"ok": True, "playlist": pl})

    # 2) Fallback oEmbed: las listas propias de Spotify (Radar de novedades, Discover
    #    Weekly, editoriales 37i9...) devuelven 404 en la Web API, pero oEmbed sí da
    #    nombre e imagen sin auth. No podremos leer las canciones por API, pero el
    #    frontend la abrirá con el reproductor incrustado.
    name = "Playlist de Spotify"
    image = ""
    try:
        oe = requests.get(
            "https://open.spotify.com/oembed",
            params={"url": f"https://open.spotify.com/playlist/{pid}"},
            timeout=12,
        )
        if oe.ok:
            j = oe.json()
            name = j.get("title") or name
            image = j.get("thumbnail_url") or ""
        elif oe.status_code == 404:
            return json_error("No se encontró esa playlist. ¿El enlace es correcto y es de una playlist (no un álbum o perfil)?", 404)
    except Exception:
        pass

    pl = {
        "id": pid,
        "uri": "spotify:playlist:" + pid,
        "name": name,
        "image": image,
        "public": True,
        "collaborative": False,
        "tracks_total": None,
    }
    return jsonify({"ok": True, "playlist": pl})


def load_pinned_playlists():
    data = _read_json(PINNED_PLAYLISTS_FILE, [])
    return data if isinstance(data, list) else []


def save_pinned_playlists(items):
    _write_json(PINNED_PLAYLISTS_FILE, items)


@app.route("/api/spotify/pinned", methods=["GET"])
def api_spotify_pinned_list():
    return jsonify({"ok": True, "playlists": load_pinned_playlists()})


@app.route("/api/spotify/pinned", methods=["POST"])
def api_spotify_pinned_add():
    b = request.get_json(silent=True) or {}
    pid = (b.get("id") or "").strip()
    if not pid:
        return json_error("Falta la playlist", 400)
    item = {
        "id": pid,
        "uri": b.get("uri") or ("spotify:playlist:" + pid),
        "name": b.get("name") or "Playlist",
        "image": b.get("image") or "",
        "public": b.get("public"),
        "collaborative": bool(b.get("collaborative")),
        "tracks_total": b.get("tracks_total"),
    }
    items = [x for x in load_pinned_playlists() if x.get("id") != pid]
    items.insert(0, item)
    save_pinned_playlists(items)
    return jsonify({"ok": True, "playlists": items})


@app.route("/api/spotify/pinned/<pid>", methods=["DELETE"])
def api_spotify_pinned_remove(pid):
    items = [x for x in load_pinned_playlists() if x.get("id") != (pid or "").strip()]
    save_pinned_playlists(items)
    return jsonify({"ok": True, "playlists": items})


@app.route("/api/spotify/liked-count")
def api_spotify_liked_count():
    data, status, err = spotify_api("/me/tracks", params={"limit": 1})
    if status == 401:
        return json_error("Spotify no conectado", 401)
    if not data or status >= 400:
        return json_error("No se pudo leer el número de Me gusta", status or 500, err)
    return jsonify({"ok": True, "total": (data or {}).get("total")})


@app.route("/api/spotify/follow-playlist", methods=["POST", "DELETE"])
def api_spotify_follow_playlist():
    """Seguir / dejar de seguir una playlist (añadir o quitar de tu biblioteca).
    Tras febrero 2026 esto va por el endpoint genérico PUT/DELETE /me/library.
    """
    b = request.get_json(silent=True) or {}
    uri = (b.get("uri") or "").strip()
    pid = (b.get("id") or "").strip()
    if not uri and pid:
        uri = "spotify:playlist:" + pid
    if not uri.startswith("spotify:playlist:"):
        return json_error("Falta la playlist", 400)
    action = b.get("action") or ("remove" if request.method == "DELETE" else "add")
    method = "PUT" if action == "add" else "DELETE"
    _, status, err = spotify_api("/me/library", method, params={"uris": uri})
    if status == 401:
        return json_error("Spotify no conectado", 401)
    if status == 403:
        return json_error("Spotify ha rechazado la operación. Si acabas de añadir permisos, reconecta en /setup.", 403, err)
    if status in (200, 201, 202, 204):
        # Invalidamos la cache de playlists para que el cambio se vea al recargar.
        try:
            _write_json(SPOTIFY_PLAYLIST_CACHE_FILE, {})
        except Exception:
            pass
        return jsonify({"ok": True, "followed": action == "add"})
    return json_error("No se pudo actualizar la biblioteca", status or 500, err)


@app.route("/api/spotify/playlist-order", methods=["GET"])
def api_spotify_playlist_order_get():
    data = _read_json(PLAYLIST_ORDER_FILE, [])
    return jsonify({"ok": True, "order": data if isinstance(data, list) else []})


@app.route("/api/spotify/playlist-order", methods=["POST"])
def api_spotify_playlist_order_set():
    b = request.get_json(silent=True) or {}
    order = b.get("order")
    if not isinstance(order, list):
        return json_error("Orden inválido", 400)
    order = [str(x) for x in order if x]
    _write_json(PLAYLIST_ORDER_FILE, order)
    return jsonify({"ok": True, "order": order})


@app.route("/api/spotify/addable-playlists")
def api_spotify_addable_playlists():
    me, status, err = spotify_api("/me")
    if status == 401:
        return json_error("Spotify no conectado", 401)
    if not me or status >= 400:
        return json_error("No se pudo leer el usuario de Spotify", status or 500, err)
    user_id = me.get("id")

    playlists = []
    offset = 0
    limit = 50
    for _ in range(20):
        data, status, err = spotify_api("/me/playlists", params={"limit": limit, "offset": offset})
        if status == 401:
            return json_error("Spotify no conectado", 401)
        if not data or status >= 400:
            return json_error("No se pudieron leer tus playlists", status or 500, err)
        items = data.get("items") or []
        for p in items:
            if not p:
                continue
            owner = p.get("owner") or {}
            owner_id = owner.get("id") or ""
            collaborative = bool(p.get("collaborative"))
            # Para evitar Forbidden, aquí mostramos solo playlists propiedad de la cuenta autenticada.
            # Las colaborativas pueden aparecer en /me/playlists, pero Spotify no siempre permite modificarlas
            # vía API según permisos/estado de la lista.
            if owner_id != user_id:
                continue
            images = p.get("images") or []
            tracks_total = ((p.get("tracks") or {}).get("total"))
            is_public = p.get("public")
            if collaborative:
                visibility = "colaborativa"
            elif is_public is False:
                visibility = "privada"
            elif is_public is True:
                visibility = "pública"
            else:
                visibility = "playlist"
            pl_id = p.get("id") or ""
            playlists.append({
                "id": pl_id,
                "uri": p.get("uri") or (f"spotify:playlist:{pl_id}" if pl_id else ""),
                "name": p.get("name") or "Playlist",
                "image": images[0]["url"] if images else "",
                "tracks_total": tracks_total,
                "owner": owner.get("display_name") or owner_id,
                "public": is_public,
                "collaborative": collaborative,
                "visibility": visibility,
            })
        if not data.get("next") or not items:
            break
        offset += limit

    def sort_key(p):
        # Privadas primero, luego colaborativas, luego públicas.
        visibility_rank = 0 if p.get("public") is False else 1 if p.get("collaborative") else 2
        return (visibility_rank, (p.get("name") or "").lower())

    playlists.sort(key=sort_key)
    return jsonify({"ok": True, "playlists": playlists})


@app.route("/api/spotify/add-to-playlist", methods=["POST"])
def api_spotify_add_to_playlist():
    body = request.get_json(silent=True) or {}
    playlist_id = (body.get("playlist_id") or "").strip()
    track_uri = (body.get("track_uri") or "").strip()
    if not playlist_id:
        return json_error("Falta playlist_id", 400)
    if not track_uri or not track_uri.startswith("spotify:track:"):
        return json_error("No hay canción válida seleccionada", 400)

    me, me_status, me_err = spotify_api("/me")
    if me_status == 401:
        return json_error("Spotify no conectado", 401)
    if not me or me_status >= 400:
        return json_error("No se pudo leer el usuario de Spotify", me_status or 500, me_err)
    user_id = (me or {}).get("id")

    playlist, pl_status, pl_err = spotify_api(
        f"/playlists/{quote(playlist_id)}",
        params={"fields": "id,name,public,collaborative,owner(id,display_name)"},
    )
    if pl_status == 401:
        return json_error("Spotify no conectado", 401)
    if pl_status and pl_status >= 400:
        return json_error("No se pudo comprobar si la playlist es editable", pl_status or 500, pl_err)

    owner = (playlist or {}).get("owner") or {}
    owner_id = owner.get("id") or ""
    playlist_name = (playlist or {}).get("name") or "playlist"
    playlist_public = (playlist or {}).get("public")
    collaborative = bool((playlist or {}).get("collaborative"))

    # Evitamos falsas esperanzas: desde la API solo intentamos modificar playlists tuyas
    # o colaborativas. Aun así, Spotify puede devolver 403 por permisos/restricciones.
    if owner_id != user_id and not collaborative:
        return json_error(
            "Spotify no permite añadir canciones a esa playlist desde la API",
            403,
            {"error": {"message": "Esa playlist no es tuya ni colaborativa. Prueba con una playlist creada por tu cuenta."}},
        )

    token_scopes = set(str((load_tokens().get("spotify") or {}).get("scope") or "").split())
    missing_scopes = []
    if playlist_public is True and "playlist-modify-public" not in token_scopes:
        missing_scopes.append("playlist-modify-public")
    if playlist_public is False and "playlist-modify-private" not in token_scopes:
        missing_scopes.append("playlist-modify-private")
    # Si Spotify no dice si es pública, exigimos al menos uno de los dos permisos de escritura.
    if playlist_public is None and not ({"playlist-modify-private", "playlist-modify-public"} & token_scopes):
        missing_scopes.extend(["playlist-modify-private", "playlist-modify-public"])
    if missing_scopes:
        return json_error(
            "Faltan permisos de Spotify para modificar playlists",
            403,
            {"error": {"message": "Faltan permisos en el token actual: " + ", ".join(sorted(set(missing_scopes))) + ". Limpia token en /setup y reconecta Spotify."}, "granted_scopes": sorted(token_scopes)},
        )

    attempts = []

    # Endpoint actual: body JSON {uris:[...]}
    data, status, err = spotify_api(f"/playlists/{quote(playlist_id)}/tracks", "POST", {"uris": [track_uri]})
    attempts.append({"endpoint": "POST /playlists/{id}/tracks json", "status": status, "error": err})
    if status in (200, 201, 202, 204):
        return jsonify({"ok": True, "snapshot_id": (data or {}).get("snapshot_id"), "method": "json"})

    # Fallback: algunos clientes/errores funcionan mejor mandando uris como query param.
    data2, status2, err2 = spotify_api(f"/playlists/{quote(playlist_id)}/tracks", "POST", None, params={"uris": track_uri})
    attempts.append({"endpoint": "POST /playlists/{id}/tracks?uris=...", "status": status2, "error": err2})
    if status2 in (200, 201, 202, 204):
        return jsonify({"ok": True, "snapshot_id": (data2 or {}).get("snapshot_id"), "method": "query"})

    # Fallback legacy/deprecated. Spotify lo mantiene en algunas cuentas, en otras falla igual.
    data3, status3, err3 = spotify_api(f"/users/{quote(user_id or '')}/playlists/{quote(playlist_id)}/tracks", "POST", {"uris": [track_uri]})
    attempts.append({"endpoint": "POST /users/{user_id}/playlists/{id}/tracks json legacy", "status": status3, "error": err3})
    if status3 in (200, 201, 202, 204):
        return jsonify({"ok": True, "snapshot_id": (data3 or {}).get("snapshot_id"), "method": "legacy"})

    final_status = status3 or status2 or status or 500
    final_err = err3 or err2 or err or {}
    debug = {
        "playlist_id": playlist_id,
        "playlist_name": playlist_name,
        "playlist_public": playlist_public,
        "collaborative": collaborative,
        "owner_id": owner_id,
        "user_id": user_id,
        "granted_scopes": sorted(token_scopes),
        "track_uri_prefix": track_uri[:32],
        "attempts": attempts,
    }
    log_spotify_error("add_to_playlist_failed", debug)

    if final_status == 403:
        return json_error(
            "Spotify ha rechazado añadir la canción a esa playlist",
            403,
            {
                "error": {
                    "message": "Forbidden de Spotify. El token tiene scopes: " + (", ".join(sorted(token_scopes)) or "ninguno") + ". Si ya reconectaste y falla incluso en una playlist creada por ti, parece una restricción/error del lado de Spotify para escritura de playlists en tu app/cuenta. Revisa data\\spotify_errors.log."
                },
                "spotify_debug": debug,
                "spotify_detail": final_err,
            },
        )

    return json_error("No se pudo añadir la canción a esa playlist", final_status, {"spotify_debug": debug, "spotify_detail": final_err})


@app.route("/api/spotify/remove-from-playlist", methods=["POST", "DELETE"])
def api_spotify_remove_from_playlist():
    body = request.get_json(silent=True) or {}
    playlist_id = (body.get("playlist_id") or "").strip()
    track_uri = (body.get("track_uri") or "").strip()
    if not playlist_id:
        return json_error("Falta playlist_id", 400)
    if not track_uri or not track_uri.startswith("spotify:track:"):
        return json_error("No hay canción válida seleccionada", 400)

    # Spotify elimina por URI. Si la canción aparece varias veces, elimina una ocurrencia.
    payload = {"tracks": [{"uri": track_uri}]}
    data, status, err = spotify_api(f"/playlists/{quote(playlist_id)}/tracks", "DELETE", payload)
    if status == 401:
        return json_error("Spotify no conectado", 401)
    if status in (200, 201, 202, 204):
        return jsonify({"ok": True, "snapshot_id": (data or {}).get("snapshot_id")})
    return json_error("No se pudo quitar la canción de esa playlist", status or 500, err)


@app.route("/api/spotify/search")
def api_spotify_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"ok": True, "results": []})

    data, status, err = spotify_api("/search", params={
        "q": q,
        "type": "track,artist,playlist",
        "limit": 8,
        "market": "ES",
    })
    if status == 401:
        return json_error("Spotify no conectado", 401)
    if not data or status >= 400:
        return json_error("No se pudo buscar en Spotify", status or 500, err)

    results = []

    for tr in ((data.get("tracks") or {}).get("items") or []):
        simple = simplify_track(tr)
        if simple and simple.get("uri"):
            results.append({
                "type": "track",
                "id": simple.get("id"),
                "uri": simple.get("uri"),
                "name": simple.get("name") or "Canción",
                "subtitle": simple.get("artists") or simple.get("album") or "Canción",
                "image": simple.get("image") or "",
                "duration_ms": simple.get("duration_ms") or 0,
            })

    for ar in ((data.get("artists") or {}).get("items") or []):
        images = ar.get("images") or []
        artist_id = ar.get("id") or ""
        results.append({
            "type": "artist",
            "id": artist_id,
            "uri": ar.get("uri") or (f"spotify:artist:{artist_id}" if artist_id else ""),
            "name": ar.get("name") or "Artista",
            "subtitle": "Artista",
            "image": images[0]["url"] if images else "",
        })

    for pl in ((data.get("playlists") or {}).get("items") or []):
        if not pl:
            continue
        images = pl.get("images") or []
        owner = pl.get("owner") or {}
        pl_id = pl.get("id") or ""
        tracks_total = (pl.get("tracks") or {}).get("total")
        subtitle = "Playlist"
        if tracks_total is not None:
            subtitle += f" · {tracks_total} canciones"
        if owner.get("display_name") or owner.get("id"):
            subtitle += f" · {owner.get('display_name') or owner.get('id')}"
        results.append({
            "type": "playlist",
            "id": pl_id,
            "uri": pl.get("uri") or (f"spotify:playlist:{pl_id}" if pl_id else ""),
            "name": pl.get("name") or "Playlist",
            "subtitle": subtitle,
            "image": images[0]["url"] if images else "",
            "tracks_total": tracks_total,
        })

    return jsonify({"ok": True, "results": results})


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

    me_data, me_status, _ = spotify_api("/me")
    user_id = (me_data or {}).get("id") if me_status and me_status < 400 else ""

    def normalize_playlist(p):
        p = p or {}
        images = p.get("images") or []
        owner = p.get("owner") or {}
        owner_id = owner.get("id") or ""
        collaborative = bool(p.get("collaborative"))
        can_modify = bool(user_id and (owner_id == user_id or collaborative))
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
            "owner": owner.get("display_name") or owner_id or "",
            "public": p.get("public"),
            "collaborative": collaborative,
            "can_modify": can_modify,
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
        if status == 401:
            return {"ok": False, "error": "Spotify no conectado"}
        player_device = (player.get("device") if isinstance(player, dict) else None) or {}
        if player_device.get("id"):
            devices = [player_device]
            first_device = player_device
        else:
            devices, _, _ = spotify_list_devices()
            active_device = next((d for d in devices if d.get("is_active")), None)
            first_device = active_device or next((d for d in devices if d.get("id") and not d.get("is_restricted")), None)
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
                "device": player_device or first_device or {},
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
    uris = body.get("uris")
    if isinstance(uris, list) and uris:
        # Reproducir una lista explícita de pistas (p.ej. "Canciones que me gustan",
        # que no es una playlist con contexto fiable). Máximo recomendado por Spotify.
        payload["uris"] = [u for u in uris if isinstance(u, str) and u.startswith("spotify:track:")][:50]
        if body.get("offset_position") is not None:
            payload["offset"] = {"position": int(body.get("offset_position") or 0)}
    else:
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


@app.route("/api/calendar/events/<path:event_id>", methods=["PUT", "PATCH"])
def api_calendar_update_event(event_id):
    b = request.get_json(silent=True) or {}
    title = (b.get("title") or "").strip()
    if not title:
        return json_error("El título es obligatorio", 400)

    all_day = bool(b.get("all_day"))
    if all_day:
        start_date = b.get("date")
        if not start_date:
            return json_error("La fecha es obligatoria", 400)
        end_date = (datetime.fromisoformat(start_date) + timedelta(days=1)).date().isoformat()
        # Al pasar de 'con hora' a 'todo el día' hay que limpiar dateTime/timeZone,
        # si no Google los mantiene mezclados y devuelve "Invalid start time".
        payload = {
            "summary": title,
            "start": {"date": start_date, "dateTime": None, "timeZone": None},
            "end": {"date": end_date, "dateTime": None, "timeZone": None},
        }
    else:
        start = b.get("start")
        end = b.get("end")
        if not start or not end:
            return json_error("Inicio y fin son obligatorios", 400)
        # Al pasar de 'todo el día' a 'con hora' hay que limpiar el campo date,
        # si no Google deja date + dateTime a la vez y devuelve "Invalid start time".
        payload = {
            "summary": title,
            "start": {"dateTime": start, "timeZone": "Europe/Madrid", "date": None},
            "end": {"dateTime": end, "timeZone": "Europe/Madrid", "date": None},
        }

    # Mandamos campos vacíos si el usuario los borra para que Google los limpie.
    payload["location"] = (b.get("location") or "").strip()
    payload["description"] = (b.get("description") or "").strip()

    data, status, err = google_api(
        f"/calendars/primary/events/{quote(event_id, safe='')}",
        "PATCH",
        payload,
    )
    if status in (200, 201):
        return jsonify({"ok": True, "event": data})
    return json_error("No se pudo actualizar el evento", status or 500, err)


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
    city = (request.args.get("city") or current_weather_city()).strip()
    save_city = request.args.get("save") == "1"
    try:
        geo = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={
            "name": city, "count": 1, "language": "es", "format": "json"
        }, timeout=15).json()
        results = geo.get("results") or []
        if not results:
            return json_error("Ciudad no encontrada", 404)
        g = results[0]
        if save_city:
            settings = load_settings()
            settings["weather_city"] = g.get("name") or city
            settings["weather_country"] = g.get("country_code") or ""
            settings["weather_updated_at"] = int(time.time())
            save_settings(settings)
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
