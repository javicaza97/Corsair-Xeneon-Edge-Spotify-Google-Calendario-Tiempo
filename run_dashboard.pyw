# XENEON Dashboard launcher sin consola
# Ejecuta app.py dentro de pythonw.exe, redirigiendo salida a logs.
# Así no depende de ningún CMD abierto.

from pathlib import Path
import os
import sys
import socket
import traceback
import runpy
from datetime import datetime

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

PID_FILE = DATA / "dashboard.pid"
AUTO_LOG = DATA / "autostart_registry.log"
STDOUT_LOG = DATA / "dashboard_stdout.log"
STDERR_LOG = DATA / "dashboard_stderr.log"
APP_FILE = ROOT / "app.py"
HOST = "127.0.0.1"
PORT = 5050


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with AUTO_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def port_open(host: str = HOST, port: int = PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


try:
    os.chdir(str(ROOT))
    log("Launcher iniciado")
    log(f"Carpeta del proyecto: {ROOT}")

    if port_open():
        log(f"Puerto {PORT} ya abierto. No se arranca otra instancia.")
        sys.exit(0)

    if not APP_FILE.exists():
        log(f"ERROR: no existe {APP_FILE}")
        sys.exit(1)

    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    log(f"PID launcher/servidor: {os.getpid()}")

    # Redirigir stdout/stderr antes de ejecutar Flask.
    stdout_f = STDOUT_LOG.open("a", encoding="utf-8", buffering=1)
    stderr_f = STDERR_LOG.open("a", encoding="utf-8", buffering=1)
    sys.stdout = stdout_f
    sys.stderr = stderr_f

    print("\n" + "=" * 60)
    print(datetime.now().strftime("[%Y-%m-%d %H:%M:%S] Arrancando XENEON Dashboard"))
    print(f"ROOT: {ROOT}")
    print(f"URL: http://{HOST}:{PORT}/dashboard.html")
    print("=" * 60)

    # Ejecuta app.py en ESTE MISMO proceso pythonw.exe.
    # No se lanza python.exe ni un .bat hijo, por lo que no hay CMD asociado.
    runpy.run_path(str(APP_FILE), run_name="__main__")

except SystemExit:
    raise
except Exception:
    tb = traceback.format_exc()
    log("ERROR fatal en run_dashboard.pyw:\n" + tb)
    try:
        with STDERR_LOG.open("a", encoding="utf-8") as f:
            f.write("\n" + tb + "\n")
    except Exception:
        pass
