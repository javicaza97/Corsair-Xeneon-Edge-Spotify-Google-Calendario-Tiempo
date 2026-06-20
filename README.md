# XENEON Dashboard Local

Dashboard local para usar en una pantalla Corsair XENEON/iCUE mediante iframe. Está pensado para ejecutarse en Windows y mostrar tres módulos:

- **Spotify**: carátula, reproducción actual, controles básicos y playlists.
- **El tiempo**: clima actual y previsión de próximos días.
- **Google Calendar**: calendario mensual, próximos eventos, consulta de eventos por día, creación y borrado de eventos.

La autenticación de Spotify y Google se hace desde el navegador normal del PC en `http://127.0.0.1:5050/setup`. La pantalla XENEON solo carga el dashboard local, así se evitan los problemas de login dentro de iframes/WebViews.

---

## 1. Requisitos previos

### Sistema recomendado

- Windows 10 o Windows 11.
- Python 3.10 o superior.
- Conexión a internet para Spotify, Google Calendar y el clima.
- Una cuenta de Spotify.
- Una cuenta de Google con acceso a Google Calendar.

### Instalar Python

1. Descarga Python desde `https://www.python.org/downloads/windows/`.
2. Durante la instalación marca la opción:

```txt
Add python.exe to PATH
```

3. Comprueba que Python está disponible abriendo PowerShell o CMD:

```powershell
py --version
```

Si aparece una versión de Python, puedes continuar.

---

## 2. Estructura del proyecto

La estructura limpia del proyecto es esta:

```txt
xeneon-dashboard-local/
├── app.py
├── requirements.txt
├── install.bat
├── start_dashboard.bat
├── open_setup.bat
├── run_dashboard.pyw
├── start_registry_now.bat
├── install_autostart_registry.bat
├── uninstall_autostart_registry.bat
├── stop_dashboard.bat
├── .env.example
├── .gitignore
├── README.md
├── static/
│   └── dashboard.html
└── data/
    └── .gitkeep
```

---

## 3. Instalación del proyecto

1. Clona o descomprime el proyecto en una ruta fija, por ejemplo:

```txt
C:\XENEON-Dashboard
```

También puede estar en una ruta con espacios, por ejemplo:

```txt
C:\Users\tu_usuario\Documents\Botones xeneon\xeneon_dashboard
```

2. Ejecuta:

```bat
install.bat
```

Ese script hace lo siguiente:

- crea el entorno virtual `.venv`,
- instala las dependencias de `requirements.txt`,
- crea `.env` a partir de `.env.example` si todavía no existe.

3. Edita el archivo `.env` y rellena tus credenciales de Spotify y Google.

Ejemplo:

```env
HOST=127.0.0.1
PORT=5050
DEFAULT_CITY=Móstoles

SPOTIFY_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

GOOGLE_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=tu_google_client_secret

SPOTIFY_PLAYLIST_CACHE_SECONDS=900
```

---

## 4. Configurar Spotify API

### 4.1 Crear la aplicación en Spotify for Developers

1. Entra en `https://developer.spotify.com/dashboard`.
2. Pulsa **Create app**.
3. Pon un nombre, por ejemplo:

```txt
XENEON Dashboard Local
```

4. En **Redirect URIs** añade exactamente:

```txt
http://127.0.0.1:5050/auth/spotify/callback
```

5. Guarda los cambios.
6. Copia el **Client ID** y ponlo en `.env`:

```env
SPOTIFY_CLIENT_ID=tu_client_id
```

No hace falta guardar `Client Secret` para Spotify, porque este proyecto usa OAuth con PKCE para Spotify.

### 4.2 Permisos usados por Spotify

El proyecto solicita permisos para:

- leer el estado de reproducción,
- controlar la reproducción,
- leer playlists privadas/colaborativas,
- leer datos básicos de la cuenta.

Nota: algunas acciones de reproducción de la Web API de Spotify pueden requerir Spotify Premium. Además, determinadas playlists públicas que no son tuyas pueden no permitir leer todas las canciones por API; en ese caso el dashboard usa la reproducción de la playlist completa o el fallback disponible.

### 4.3 Cambio de refresh tokens de Spotify

El backend gestiona tokens caducados o revocados. Si Spotify devuelve `invalid_grant` o un error equivalente al renovar el token, el proyecto elimina el token viejo y te obliga a reconectar desde `/setup`.

Para reconectar manualmente:

```txt
http://127.0.0.1:5050/setup
```

Y pulsa **Limpiar token Spotify** y después **Conectar Spotify**.

---

## 5. Configurar Google Calendar API

### 5.1 Crear o seleccionar proyecto en Google Cloud

1. Entra en `https://console.cloud.google.com/`.
2. Crea un proyecto nuevo o selecciona uno existente.
3. Ve a **APIs y servicios**.
4. Busca y habilita:

```txt
Google Calendar API
```

### 5.2 Configurar pantalla de consentimiento OAuth

1. Ve a **Google Auth Platform** o **Pantalla de consentimiento OAuth**.
2. Configura la app como **External** si es para una cuenta personal.
3. Pon nombre de app, correo de soporte y correo del desarrollador.
4. Si la app queda en modo **Testing**, añade tu cuenta de Gmail en **Test users / Usuarios de prueba**.

Ejemplo:

```txt
tu_correo@gmail.com
```

Si no añades la cuenta como tester, Google puede mostrar:

```txt
Error 403: access_denied
```

### 5.3 Crear credenciales OAuth

1. Ve a **APIs y servicios → Credenciales**.
2. Pulsa **Create credentials → OAuth client ID**.
3. En **Application type**, selecciona:

```txt
Web application
```

4. En **Authorized JavaScript origins** añade:

```txt
http://127.0.0.1:5050
```

En esta versión el login lo hace el backend, pero se recomienda dejar este origen autorizado para evitar problemas si se reutiliza el cliente desde navegador/local.

5. En **Authorized redirect URIs** añade exactamente:

```txt
http://127.0.0.1:5050/auth/google/callback
```

6. Guarda.
7. Copia el **Client ID** y el **Client Secret** en `.env`:

```env
GOOGLE_CLIENT_ID=tu_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=tu_client_secret
```

### 5.4 Permisos usados por Calendar

El proyecto usa el scope:

```txt
https://www.googleapis.com/auth/calendar.events
```

Esto permite leer, crear y borrar eventos del calendario principal.

---

## 6. Primera ejecución manual

Después de instalar dependencias y configurar `.env`, ejecuta:

```bat
start_dashboard.bat
```

Deberías ver algo como:

```txt
XENEON Dashboard local arrancado en http://127.0.0.1:5050/dashboard.html
Setup: http://127.0.0.1:5050/setup
```

Abre en el navegador:

```txt
http://127.0.0.1:5050/setup
```

Desde esa pantalla puedes conectar:

- Spotify,
- Google Calendar.

Luego abre:

```txt
http://127.0.0.1:5050/dashboard.html
```

---

## 7. Configurar el iframe en XENEON/iCUE

En el widget/webview de XENEON/iCUE usa:

```html
<iframe
  src="http://127.0.0.1:5050/dashboard.html?v=1"
  style="width:100%; height:100%; border:0; overflow:hidden;"
  allow="autoplay; clipboard-write"
></iframe>
```

Si iCUE permite poner solo una URL, usa:

```txt
http://127.0.0.1:5050/dashboard.html?v=1
```

Puedes cambiar el número de `v=1` a `v=2`, `v=3`, etc. cuando quieras forzar que XENEON no use caché.

---

## 8. Arranque automático con Windows

El arranque automático recomendado usa el Registro de Windows:

```txt
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
```

No usa la carpeta Inicio ni `.vbs`, así se evita la advertencia de seguridad de Windows.

### 8.1 Instalar autoarranque

Ejecuta:

```bat
install_autostart_registry.bat
```

Ese script registra el arranque de:

```txt
.venv\Scripts\pythonw.exe run_dashboard.pyw
```

`pythonw.exe` ejecuta el backend sin ventana de consola.

El instalador también arranca el dashboard en ese momento para comprobar que el puerto `5050` responde.

### 8.2 Probar si está levantado

En PowerShell:

```powershell
Test-NetConnection 127.0.0.1 -Port 5050
```

Si todo está bien, verás:

```txt
TcpTestSucceeded : True
```

También puedes abrir:

```txt
http://127.0.0.1:5050/dashboard.html
```

### 8.3 Desinstalar autoarranque

```bat
uninstall_autostart_registry.bat
```

### 8.4 Parar el dashboard manualmente

```bat
stop_dashboard.bat
```

---

## 9. Logs y solución de problemas

Los logs locales se guardan en:

```txt
data\autostart_registry.log
data\dashboard_stdout.log
data\dashboard_stderr.log
```

### El dashboard no abre

Comprueba si el puerto está abierto:

```powershell
Test-NetConnection 127.0.0.1 -Port 5050
```

Si falla, ejecuta:

```bat
start_registry_now.bat
```

Y revisa:

```txt
data\dashboard_stderr.log
```

### Spotify aparece como no conectado

Abre:

```txt
http://127.0.0.1:5050/setup
```

Pulsa:

```txt
Limpiar token Spotify
```

Después pulsa:

```txt
Conectar Spotify
```

### Spotify no reproduce

Comprueba que tienes Spotify abierto en el PC o en algún dispositivo de tu cuenta. El dashboard necesita un dispositivo activo de Spotify Connect para controlar reproducción.

### Google muestra `redirect_uri_mismatch`

Revisa que el Redirect URI configurado en Google sea exactamente:

```txt
http://127.0.0.1:5050/auth/google/callback
```

`localhost` y `127.0.0.1` no son equivalentes para OAuth. Usa exactamente `127.0.0.1`.

### Google muestra `access_denied` porque la app está en pruebas

Añade tu cuenta de Gmail como **Test user / Usuario de prueba** en la pantalla de consentimiento OAuth.

### He cambiado el puerto

Si cambias `PORT=5050` en `.env`, también debes cambiar las redirecciones en Spotify y Google. Por ejemplo, si usas `PORT=5051`:

```txt
http://127.0.0.1:5051/auth/spotify/callback
http://127.0.0.1:5051/auth/google/callback
http://127.0.0.1:5051
```

---

## 10. Limpieza antes de subir a GitHub

Antes de publicar el repositorio, asegúrate de no subir:

```txt
.env
.venv/
data/tokens.json
data/oauth_states.json
data/spotify_playlists_cache.json
data/*.log
data/*.pid
__pycache__/
```

El `.gitignore` incluido ya evita esos archivos.

---

## 11. Archivos principales

- `app.py`: backend Flask local.
- `static/dashboard.html`: interfaz de XENEON.
- `install.bat`: instala entorno virtual y dependencias.
- `start_dashboard.bat`: arranque manual para pruebas.
- `run_dashboard.pyw`: launcher sin consola para autoarranque.
- `install_autostart_registry.bat`: instala autoarranque en el Registro.
- `uninstall_autostart_registry.bat`: elimina autoarranque.
- `stop_dashboard.bat`: detiene el backend local.
- `.env.example`: plantilla de configuración.

---

## 12. Notas de seguridad

Este proyecto está pensado para uso local en tu PC. Por defecto escucha solo en:

```txt
127.0.0.1
```

No expongas el puerto a internet. No subas `.env` ni `data/tokens.json` a GitHub.
