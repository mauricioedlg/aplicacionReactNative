"""Pruebas del supervisor sin ejecutar servidores ni consultar Neon."""
import contextlib
import importlib.util
import io
from pathlib import Path
import signal
import subprocess
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "arrancar_contenedor", Path(__file__).with_name("arrancar-contenedor.py")
)
arranque = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arranque)


class EntornoTests(unittest.TestCase):
    def setUp(self):
        self.entorno = mock.patch.dict(arranque.os.environ, {
            "SECRET_KEY": "clave-de-prueba-" * 4,
            "NEON_MOBILE_DATABASE_URL": "postgresql://usuario:privado@localhost/pruebas",
            "PORT": "10000",
        }, clear=True)
        self.entorno.start()
        self.addCleanup(self.entorno.stop)

    def test_entorno_valido(self):
        arranque.validar_entorno()

    def test_rechaza_clave_insegura(self):
        arranque.os.environ["SECRET_KEY"] = "django-insecure-" + "x" * 50
        with self.assertRaisesRegex(RuntimeError, "SECRET_KEY"):
            arranque.validar_entorno()

    def test_rechaza_conexion_ausente_sin_exponer_claves(self):
        arranque.os.environ["NEON_MOBILE_DATABASE_URL"] = "conexion-invalida-con-privado"
        with mock.patch.object(arranque.signal, "signal"), contextlib.redirect_stderr(io.StringIO()) as salida:
            resultado = arranque.main()
        self.assertEqual(resultado, 1)
        self.assertIn("NEON_MOBILE_DATABASE_URL", salida.getvalue())
        self.assertNotIn("privado", salida.getvalue())
        self.assertNotIn(arranque.os.environ["SECRET_KEY"], salida.getvalue())

    def test_no_permite_puerto_publico_igual_al_interno(self):
        arranque.os.environ["PORT"] = "8000"
        with self.assertRaisesRegex(RuntimeError, "PORT"):
            arranque.validar_entorno()

    def test_fallo_en_preparacion_impide_arrancar_servidores(self):
        with (
            mock.patch.object(arranque.signal, "signal"),
            mock.patch.object(arranque, "Supervisor") as supervisor_clase,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            supervisor = supervisor_clase.return_value
            supervisor.ejecutar.side_effect = RuntimeError("collectstatic fallo")
            self.assertEqual(arranque.main(), 1)
        supervisor.ejecutar.assert_called_once()
        supervisor.iniciar.assert_not_called()
        supervisor.apagar.assert_called_once()

    def test_fallo_de_un_servidor_ejecuta_el_apagado(self):
        with (
            mock.patch.object(arranque.signal, "signal"),
            mock.patch.object(arranque, "Supervisor") as supervisor_clase,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            supervisor = supervisor_clase.return_value
            supervisor.vigilar.side_effect = RuntimeError("Django termino inesperadamente")
            self.assertEqual(arranque.main(), 1)
        self.assertEqual(supervisor.iniciar.call_count, 2)
        supervisor.apagar.assert_called_once()


class SupervisorTests(unittest.TestCase):
    def test_senal_solicita_apagado(self):
        supervisor = arranque.Supervisor()
        supervisor.solicitar_apagado(signal.SIGTERM, None)
        with self.assertRaises(arranque.ApagadoSolicitado):
            supervisor.vigilar()
        with self.assertRaises(arranque.ApagadoSolicitado):
            supervisor.iniciar("Node", ["node", "server.cjs"], arranque.ROOT)

    def test_salida_inesperada_de_un_hijo_es_un_error(self):
        supervisor = arranque.Supervisor()
        proceso = mock.Mock()
        proceso.poll.return_value = 0
        proceso.returncode = 0
        supervisor.procesos["Django"] = proceso
        with self.assertRaisesRegex(RuntimeError, "Django termino inesperadamente"):
            supervisor.vigilar()

    def test_apagado_termina_los_dos_grupos_de_procesos(self):
        supervisor = arranque.Supervisor()
        for nombre, pid in (("Django", 111), ("Node", 222)):
            proceso = mock.Mock(pid=pid)
            proceso.poll.return_value = None
            supervisor.procesos[nombre] = proceso
        with mock.patch.object(arranque.os, "killpg", create=True) as matar:
            supervisor.apagar()
        self.assertEqual(matar.call_args_list, [
            mock.call(111, signal.SIGTERM), mock.call(222, signal.SIGTERM),
        ])
        for proceso in supervisor.procesos.values():
            proceso.wait.assert_called_once()

    def test_fuerza_salida_si_un_hijo_ignora_sigterm(self):
        supervisor = arranque.Supervisor()
        proceso = mock.Mock(pid=111)
        proceso.poll.return_value = None
        proceso.wait.side_effect = [subprocess.TimeoutExpired("Django", 25), 0]
        supervisor.procesos["Django"] = proceso
        sigkill = getattr(signal, "SIGKILL", 9)
        with (
            mock.patch.object(arranque.os, "killpg", create=True) as matar,
            mock.patch.object(arranque.signal, "SIGKILL", sigkill, create=True),
        ):
            supervisor.apagar()
        self.assertEqual(matar.call_args_list, [
            mock.call(111, signal.SIGTERM), mock.call(111, sigkill),
        ])


if __name__ == "__main__":
    unittest.main()
