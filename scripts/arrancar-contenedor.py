"""Arranca y supervisa Expo web y Django en un unico contenedor Linux."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend" / "servidor"
STOP_TIMEOUT = 25


class ApagadoSolicitado(Exception):
    pass


class Supervisor:
    def __init__(self):
        self.procesos = {}
        self.senal = None

    def solicitar_apagado(self, numero, _frame):
        self.senal = numero

    def iniciar(self, nombre, comando, directorio):
        if self.senal:
            raise ApagadoSolicitado
        print(f"Iniciando {nombre}...", flush=True)
        proceso = subprocess.Popen(comando, cwd=directorio, start_new_session=True)
        self.procesos[nombre] = proceso
        return proceso

    def ejecutar(self, nombre, comando):
        proceso = self.iniciar(nombre, comando, BACKEND)
        while proceso.poll() is None:
            if self.senal:
                raise ApagadoSolicitado
            time.sleep(0.2)
        self.procesos.pop(nombre)
        if proceso.returncode:
            raise RuntimeError(f"{nombre} fallo (codigo {proceso.returncode}).")

    def vigilar(self):
        while not self.senal:
            for nombre, proceso in self.procesos.items():
                if proceso.poll() is not None:
                    raise RuntimeError(
                        f"{nombre} termino inesperadamente (codigo {proceso.returncode})."
                    )
            time.sleep(0.2)
        raise ApagadoSolicitado

    def apagar(self):
        # Cada hijo es lider de un grupo; Gunicorn incluye sus workers.
        for proceso in self.procesos.values():
            try:
                os.killpg(proceso.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        limite = time.monotonic() + STOP_TIMEOUT
        for proceso in self.procesos.values():
            try:
                proceso.wait(timeout=max(0, limite - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proceso.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proceso.wait()


def validar_entorno():
    clave = os.environ.get("SECRET_KEY", "").strip()
    if len(clave) < 32 or clave.startswith("django-insecure"):
        raise RuntimeError("Configura SECRET_KEY con una clave aleatoria de al menos 32 caracteres.")
    conexion = os.environ.get("NEON_MOBILE_DATABASE_URL", "").strip()
    try:
        partes = urlsplit(conexion)
        valida = partes.scheme in ("postgres", "postgresql") and partes.hostname and partes.path.lstrip("/")
    except ValueError:
        valida = False
    if not valida:
        raise RuntimeError("Configura NEON_MOBILE_DATABASE_URL con la conexion PostgreSQL de la aplicacion.")
    try:
        puerto = int(os.environ.get("PORT", "10000"))
    except ValueError:
        puerto = 0
    if not 1 <= puerto <= 65535 or puerto == 8000:
        raise RuntimeError("PORT debe ser un puerto valido distinto del puerto interno 8000.")


def main():
    supervisor = Supervisor()
    signal.signal(signal.SIGTERM, supervisor.solicitar_apagado)
    signal.signal(signal.SIGINT, supervisor.solicitar_apagado)
    try:
        validar_entorno()
        supervisor.ejecutar(
            "archivos estaticos de Django",
            [sys.executable, "manage.py", "collectstatic", "--noinput"],
        )
        supervisor.ejecutar(
            "migraciones de la aplicacion",
            [sys.executable, "manage.py", "migrate", "movil", "--database=movil", "--noinput"],
        )
        supervisor.iniciar(
            "Django / Gunicorn",
            [
                sys.executable, "-m", "gunicorn", "servidor.wsgi:application",
                "--bind", "127.0.0.1:8000", "--workers", "1", "--threads", "4",
                "--forwarded-allow-ips", "",
                "--timeout", "90", "--graceful-timeout", "20",
                "--access-logfile", "-", "--error-logfile", "-",
            ],
            BACKEND,
        )
        supervisor.iniciar("Expo web / Node", ["node", "server.cjs"], ROOT)
        supervisor.vigilar()
    except ApagadoSolicitado:
        print("Deteniendo los servidores...", flush=True)
        return 0
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr, flush=True)
        return 1
    finally:
        supervisor.apagar()


if __name__ == "__main__":
    sys.exit(main())
