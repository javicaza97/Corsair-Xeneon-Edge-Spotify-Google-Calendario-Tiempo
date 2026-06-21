# XENEON Dashboard Local

Dashboard local para usar en una pantalla Corsair XENEON/iCUE mediante iframe. La aplicación se ejecuta en el PC, escucha por defecto en `127.0.0.1:5050` y muestra una interfaz compacta con tres módulos principales:

- **Spotify**: reproducción actual, controles, dispositivos, playlists, búsqueda, enlaces de playlists especiales y gestión básica de canciones.
- **Tiempo**: clima actual, sensación térmica, humedad, viento, índice UV y previsión de varios días.
- **Google Calendar**: calendario mensual, próximos eventos, eventos por día, creación y borrado de eventos.

La autenticación de Spotify y Google se realiza desde el navegador normal del PC en:

```txt
http://127.0.0.1:5050/setup
```

La pantalla XENEON carga únicamente el dashboard local, evitando problemas de login dentro de iframes o WebViews.

---

## 1. Requisitos previos

Antes de ejecutar `install.bat`, el sistema debe tener instalado:

- Windows 10 o Windows 11.
- Python 3.10 o superior.
- Python añadido al `PATH`.
- Navegador moderno.
- Conexión a internet para instalar dependencias y conectar las APIs.
- Cuenta de Spotify.
- Cuenta de Google con Google Calendar.
- Una aplicación creada en Spotify Developer Dashboard.
- Un proyecto de Google Cloud con Google Calendar API habilitada.

### 1.1 Comprobar Python

Abre CMD o PowerShell y ejecuta:

```powershell
py --version
```

Debe aparecer una versión de Python 3.10 o superior.

Si Windows no reconoce el comando, instala Python desde:

```txt
https://www.python.org/downloads/windows/
```

Durante la instalación marca:

```txt
Add python.exe to PATH
```

Después cierra y vuelve a abrir CMD/PowerShell.

---

## 2. Estructura del proyecto

```txt
xeneon_dashboard/
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
├── .env
├── .env.example
├── .gitignore
├── README.md
├── static/
│   └── dashboard.html
└── data/
    └── .gitkeep
```

La carpeta `data/` se usa para guardar datos locales de funcionamiento:

- tokens OAuth de Spotify y Google,
- ajustes persistentes,
- caché de playlists,
- orden personalizado de playlists,
- playlists fijadas,
- logs,
- PID del proceso local.

Por seguridad, el repositorio solo debe incluir `data/.gitkeep`. El resto de ficheros generados dentro de `data/` no deben subirse a GitHub.

---

## 3. Instalación rápida

1. Clona o descarga el repositorio.

2. Descomprime o coloca el proyecto en una ruta fija, por ejemplo:

```txt
C:\XENEON-Dashboard
```

También puede estar en una ruta con espacios, por ejemplo:

```txt
C:\Users\tu_usuario\Documents\Botones xeneon\xeneon_dashboard
```

3. Ejecuta:

```bat
install.bat
```

El instalador realiza estas acciones:

- crea el entorno virtual `.venv`,
- instala las dependencias de `requirements.txt`,
- crea `.env` a partir de `.env.example` si no existe.

4. Edita `.env` y configura tus credenciales reales de Spotify y Google.

5. Arranca el dashboard:

```bat
start_dashboard.bat
```

6. Abre la pantalla de configuración:

```txt
http://127.0.0.1:5050/setup
```

7. Conecta Spotify y Google Calendar desde esa pantalla.

8. Abre el dashboard:

```txt
http://127.0.0.1:5050/dashboard.html
```

---

## 4. Fichero `.env`

El proyecto incluye un `.env` de ejemplo, sin credenciales reales.

Ejemplo:

```env
# Servidor local
HOST=127.0.0.1
PORT=5050
DEFAULT_CITY=Móstoles

# Spotify for Developers
SPOTIFY_CLIENT_ID=your_spotify_client_id

# Google Cloud / Google Calendar API
GOOGLE_CLIENT_ID=your_google_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_google_client_secret

# Cache de playlists para evitar llamadas excesivas a Spotify
SPOTIFY_PLAYLIST_CACHE_SECONDS=900
```

No subas credenciales reales a GitHub. Si ya has conectado Spotify o Google, los tokens se guardan en `data/tokens.json`; ese fichero tampoco debe subirse.

---

## 5. Configurar Spotify API

Esta aplicación usa OAuth con PKCE para Spotify, por lo que solo necesita `SPOTIFY_CLIENT_ID` en el `.env`.

### 5.1 Crear la aplicación en Spotify

1. Entra en Spotify Developer Dashboard.
2. Crea una nueva aplicación.
3. Copia el **Client ID**.
4. En la configuración de la app, añade esta Redirect URI exacta:

```txt
http://127.0.0.1:5050/auth/spotify/callback
```

5. Guarda los cambios.

### 5.2 Configurar `.env`

En el fichero `.env`:

```env
SPOTIFY_CLIENT_ID=tu_spotify_client_id
```

### 5.3 Conectar Spotify

1. Arranca el dashboard:

```bat
start_dashboard.bat
```

2. Abre:

```txt
http://127.0.0.1:5050/setup
```

3. Pulsa **Conectar Spotify**.
4. Acepta los permisos solicitados.

### 5.4 Error típico: `INVALID_CLIENT: Invalid redirect URI`

Revisa que la Redirect URI configurada en Spotify sea exactamente:

```txt
http://127.0.0.1:5050/auth/spotify/callback
```

Debe coincidir exactamente con la que usa la aplicación, incluyendo:

- `http`,
- `127.0.0.1`,
- puerto `5050`,
- ruta `/auth/spotify/callback`,
- sin barras extra al final.

---

## 6. Configurar Google Calendar API

La integración de Google Calendar usa OAuth 2.0 con una aplicación web.

### 6.1 Crear proyecto en Google Cloud

1. Entra en Google Cloud Console.
2. Crea un proyecto o usa uno existente.
3. Ve a **APIs & Services**.
4. Habilita la API:

```txt
Google Calendar API
```

### 6.2 Configurar pantalla de consentimiento OAuth

1. Ve a **APIs & Services → OAuth consent screen**.
2. Configura la pantalla de consentimiento.
3. Para uso personal, puedes dejar la app en modo **Testing**.
4. Si está en modo Testing, añade tu cuenta de Google como usuario de prueba.

### 6.3 Crear credenciales OAuth

1. Ve a **APIs & Services → Credentials**.
2. Pulsa **Create credentials → OAuth client ID**.
3. Tipo de aplicación:

```txt
Web application
```

4. En **Authorized JavaScript origins**, añade:

```txt
http://127.0.0.1:5050
```

5. En **Authorized redirect URIs**, añade:

```txt
http://127.0.0.1:5050/auth/google/callback
```

6. Guarda.
7. Copia el **Client ID** y el **Client Secret**.

### 6.4 Configurar `.env`

En el fichero `.env`:

```env
GOOGLE_CLIENT_ID=tu_google_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=tu_google_client_secret
```

### 6.5 Conectar Google Calendar

1. Arranca el dashboard:

```bat
start_dashboard.bat
```

2. Abre:

```txt
http://127.0.0.1:5050/setup
```

3. Pulsa **Conectar Google Calendar**.
4. Acepta los permisos.

### 6.6 Error típico: `redirect_uri_mismatch`

Revisa que Google tenga exactamente esta URI en **Authorized redirect URIs**:

```txt
http://127.0.0.1:5050/auth/google/callback
```

Y que también tenga este origen en **Authorized JavaScript origins**:

```txt
http://127.0.0.1:5050
```

Aunque la aplicación hace el intercambio OAuth desde Flask, mantener el origen JavaScript autorizado ayuda a evitar errores de configuración cuando Google valida el origen de la aplicación web.

---

## 7. Arranque y parada

### Arrancar con consola

```bat
start_dashboard.bat
```

URLs principales:

```txt
http://127.0.0.1:5050/setup
http://127.0.0.1:5050/dashboard.html
```

### Abrir solo la pantalla de configuración

```bat
open_setup.bat
```

### Parar el dashboard

```bat
stop_dashboard.bat
```

---

## 8. Autoarranque en Windows

Para que el dashboard arranque automáticamente al iniciar sesión en Windows:

```bat
install_autostart_registry.bat
```

Esto crea una entrada en el registro de Windows:

```txt
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
```

También arranca el dashboard sin dejar una consola abierta usando:

```txt
run_dashboard.pyw
```

Para quitar el autoarranque:

```bat
uninstall_autostart_registry.bat
```

Para probar el arranque sin consola:

```bat
start_registry_now.bat
```

---

## 9. Integración con Corsair XENEON / iCUE

La URL recomendada para el iframe o panel web es:

```txt
http://127.0.0.1:5050/dashboard.html
```

La autenticación no se debe hacer desde el iframe. Para conectar cuentas usa siempre:

```txt
http://127.0.0.1:5050/setup
```

---

## 10. Datos locales generados

Durante el uso se pueden crear ficheros como:

```txt
data/tokens.json
data/oauth_states.json
data/settings.json
data/pinned_playlists.json
data/playlist_order.json
data/spotify_playlists_cache.json
data/autostart_registry.log
data/dashboard_stdout.log
data/dashboard_stderr.log
data/dashboard.pid
```

Estos ficheros son locales y no deben subirse a GitHub.

Para limpiar tokens y reconectar desde cero:

1. Para Spotify, puedes usar el botón **Limpiar token Spotify** en `/setup`.
2. Para limpiar todo manualmente, para el dashboard y borra:

```txt
data/tokens.json
data/oauth_states.json
```

Después vuelve a abrir:

```txt
http://127.0.0.1:5050/setup
```

---

## 11. Actualizar el proyecto desde GitHub

### 11.1 Si ya tienes el repositorio clonado

Entra en la carpeta del proyecto:

```powershell
cd C:\XENEON-Dashboard
```

Comprueba cambios locales:

```powershell
git status
```

Descarga la última versión:

```powershell
git pull origin main
```

Si tu rama se llama `master`:

```powershell
git pull origin master
```

Después, si han cambiado dependencias:

```bat
install.bat
```

Y arranca de nuevo:

```bat
start_dashboard.bat
```

### 11.2 Si quieres subir cambios a tu repositorio GitHub

Desde la carpeta del proyecto:

```powershell
git status
```

Añade cambios:

```powershell
git add .
```

Crea commit:

```powershell
git commit -m "Actualiza documentación y limpieza del proyecto"
```

Sube a GitHub:

```powershell
git push origin main
```

Si tu rama se llama `master`:

```powershell
git push origin master
```

### 11.3 Si todavía no está conectado a GitHub

Inicializa Git:

```powershell
git init
```

Añade el remoto de GitHub:

```powershell
git remote add origin https://github.com/TU_USUARIO/TU_REPOSITORIO.git
```

Añade y sube:

```powershell
git add .
git commit -m "Primera versión XENEON Dashboard"
git branch -M main
git push -u origin main
```

---

## 12. Limpieza antes de subir a GitHub

Antes de hacer `git add .`, revisa:

```powershell
git status
```

No deben aparecer:

```txt
.env con credenciales reales
data/tokens.json
data/oauth_states.json
data/*.log
data/*.pid
.venv/
__pycache__/
```

Este repositorio incluye `.gitignore` para evitar subir esos ficheros.

Si alguna vez Git ya había empezado a trackear un fichero sensible, no basta con `.gitignore`. Hay que sacarlo del índice:

```powershell
git rm --cached .env
git rm --cached data/tokens.json
git commit -m "Elimina credenciales locales del repositorio"
```

Si subiste tokens reales accidentalmente, revócalos desde Spotify/Google y vuelve a conectar la aplicación.

---

## 13. Solución de problemas

### El puerto 5050 no responde

Comprueba si hay proceso escuchando:

```powershell
netstat -ano | findstr :5050
```

También puedes probar:

```powershell
Test-NetConnection 127.0.0.1 -Port 5050
```

### No existe `.venv`

Ejecuta:

```bat
install.bat
```

### Spotify no conecta

Revisa:

```env
SPOTIFY_CLIENT_ID=tu_spotify_client_id
```

Y en Spotify Developer Dashboard:

```txt
http://127.0.0.1:5050/auth/spotify/callback
```

### Google no conecta

Revisa:

```env
GOOGLE_CLIENT_ID=tu_google_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=tu_google_client_secret
```

Y en Google Cloud:

```txt
Authorized JavaScript origins:
http://127.0.0.1:5050

Authorized redirect URIs:
http://127.0.0.1:5050/auth/google/callback
```

### La app está en modo Testing en Google

Añade tu cuenta en:

```txt
OAuth consent screen → Test users
```

### Spotify pide reconectar

Desde `/setup`, pulsa:

```txt
Limpiar token Spotify
```

Después vuelve a conectar Spotify.

---

## 14. Seguridad

- No subas `.env` con credenciales reales.
- No subas `data/tokens.json`.
- No compartas `GOOGLE_CLIENT_SECRET`.
- Si publicas el proyecto en GitHub, revisa siempre `git status` antes de hacer commit.
- Si se filtra un token, revócalo y genera uno nuevo.

---

## 15. Licencia

Añade aquí la licencia que quieras aplicar al proyecto antes de publicarlo oficialmente.
