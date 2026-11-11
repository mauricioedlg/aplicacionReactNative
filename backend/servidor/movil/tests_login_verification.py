"""Ingreso con correo y sesión única, sin Neon ni envíos reales."""
import json
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import requests
from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .auth import COOKIE, COOKIE_PATH, token_hash
from .models import LimiteAcceso, Sesion, Usuario, VerificacionLogin

BASE = "/api/sistema/app/"
CLAVE = "UnaClaveSegura2026!"
CODIGO = "034512"


@override_settings(RESEND_API_KEY="re_test_key", RESEND_SENDER_EMAIL="acceso@example.com",
                   MOBILE_LOGIN_CODE_TTL_SECONDS=600)
class LoginVerificationTests(TestCase):
    databases = {"default", "movil"}

    def setUp(self):
        self.usuario = Usuario.objects.create(nombre_completo="Ana <Pérez>", usuario="ana",
            correo="ana@example.com", telefono="8112345678", contrasena_hash=make_password(CLAVE))
        mail_patch = patch("movil.login_verification.enviar_correo_resend", return_value={"id": "email-id"})
        self.mail = mail_patch.start()
        self.addCleanup(mail_patch.stop)
        codigo_patch = patch("movil.login_verification.secrets.randbelow", return_value=int(CODIGO))
        self.codigo = codigo_patch.start()
        self.addCleanup(codigo_patch.stop)

    def post(self, ruta, datos, client=None, native=True, **extra):
        prefix = "movil/" if native else ""
        return (client or self.client).post(BASE + prefix + ruta + "/",
            data=json.dumps(datos), content_type="application/json", **extra)

    def login(self, **kwargs):
        result = self.post("login", {"usuario": self.usuario.usuario, "contrasena": CLAVE}, **kwargs)
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()

    def verificar(self, desafio, codigo=CODIGO, **kwargs):
        return self.post("verificacion-login/verificar",
            {"verificacion_id": desafio["verificacion_id"], "codigo": codigo}, **kwargs)

    def reenviar(self, desafio, **kwargs):
        return self.post("verificacion-login/reenviar", {"verificacion_id": desafio["verificacion_id"]}, **kwargs)

    def test_clave_correcta_exige_correo_sin_abrir_sesion_ni_exponer_secretos(self):
        antes = timezone.now()
        result = self.post("login", {"usuario": self.usuario.usuario, "contrasena": CLAVE})
        self.assertEqual(result.status_code, 200, result.content)
        datos = result.json()
        UUID(datos["verificacion_id"])
        self.assertTrue(datos["requiere_verificacion"])
        self.assertEqual(datos["reenviar_en"], 60)
        self.assertNotEqual(datos["correo_destino"], self.usuario.correo)
        for campo in ("usuario", "token", "codigo", "contrasena"):
            self.assertNotIn(campo, datos)
        self.assertNotIn(COOKIE, result.cookies)
        self.assertFalse(Sesion.objects.exists())
        self.assertEqual(self.client.get(BASE + "yo/").status_code, 401)
        fila = VerificacionLogin.objects.get()
        self.assertEqual(str(fila.id), datos["verificacion_id"])
        self.assertEqual(fila.correo, self.usuario.correo)
        self.assertNotEqual(fila.codigo_hash, CODIGO)
        self.assertEqual(len(fila.codigo_hash), 64)
        self.assertGreaterEqual(fila.expira_en, antes + timedelta(seconds=600))
        self.assertEqual(self.mail.call_args.kwargs["destinatario"], self.usuario.correo)
        html = self.mail.call_args.kwargs["contenido_html"]
        self.assertIn(CODIGO, html)
        self.assertIn("Ana &lt;Pérez&gt;", html)
        self.assertNotIn(CLAVE, html)
        self.assertNotIn(CODIGO, result.content.decode())
        self.assertNotIn(CLAVE, result.content.decode())

    def test_solo_credenciales_correctas_de_cuenta_activa_envian_codigo(self):
        for usuario, contrasena in (("nadie", CLAVE), (self.usuario.usuario, "incorrecta")):
            result = self.post("login", {"usuario": usuario, "contrasena": contrasena})
            self.assertEqual(result.status_code, 401)
        self.usuario.activo = False
        self.usuario.save(update_fields=["activo"])
        self.assertEqual(self.post("login", {"usuario": self.usuario.usuario, "contrasena": CLAVE}).status_code, 401)
        self.mail.assert_not_called()
        self.assertFalse(VerificacionLogin.objects.exists())
        self.assertFalse(Sesion.objects.exists())

    def test_verificar_emite_token_nativo_sin_vencimiento_y_codigo_no_se_reutiliza(self):
        desafio = self.login()
        result = self.verificar(desafio)
        self.assertEqual(result.status_code, 200, result.content)
        token = result.json()["token"]
        self.assertNotIn(COOKIE, result.cookies)
        fila = Sesion.objects.get()
        self.assertEqual(fila.token_hash, token_hash(token))
        self.assertNotEqual(fila.token_hash, token)
        self.assertIsNone(fila.expira_en)
        self.assertIsNotNone(VerificacionLogin.objects.get().usado_en)
        self.assertEqual(self.verificar(desafio).status_code, 400)
        self.assertEqual(Sesion.objects.count(), 1)
        futuro = timezone.now() + timedelta(days=800)
        with patch("movil.auth.timezone.now", return_value=futuro):
            nueva_instancia = Client(HTTP_AUTHORIZATION=f"Bearer {token}")
            self.assertEqual(nueva_instancia.get(BASE + "yo/").status_code, 200)

    def test_cinco_fallos_persisten_y_rechazan_incluso_codigo_correcto(self):
        desafio = self.login()
        for intento in range(1, 6):
            result = self.verificar(desafio, "111111")
            self.assertEqual(result.status_code, 400, result.content)
            self.assertEqual(VerificacionLogin.objects.get().intentos, intento)
        self.assertEqual(self.verificar(desafio).status_code, 400)
        self.assertFalse(Sesion.objects.exists())
        nuevo = self.reenviar(desafio)
        self.assertEqual(nuevo.status_code, 200, nuevo.content)
        self.assertEqual(self.verificar(nuevo.json()).status_code, 200)

    def test_codigo_vencido_malformado_y_desafio_desconocido_no_autentican(self):
        desafio = self.login()
        for codigo in ("12345", "1234567", "１２３４５６", 34512, None):
            self.assertEqual(self.verificar(desafio, codigo).status_code, 400)
        for verificacion_id in (str(uuid4()), "no-es-uuid", None):
            self.assertEqual(self.verificar({"verificacion_id": verificacion_id}).status_code, 400)
        VerificacionLogin.objects.update(expira_en=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.verificar(desafio).status_code, 400)
        self.assertFalse(Sesion.objects.exists())

    def test_codigo_corresponde_solo_a_su_desafio(self):
        primero = self.login()
        self.codigo.return_value = 987654
        segundo = self.login()
        self.assertEqual(self.verificar(segundo, CODIGO).status_code, 400)
        self.assertEqual(self.verificar(segundo, "987654").status_code, 200)
        self.assertEqual(self.verificar(primero).status_code, 400)

    def test_nuevo_login_reemplaza_sesion_anterior_solo_al_verificar(self):
        anterior = self.verificar(self.login()).json()["token"]
        viejo_dispositivo = Client(HTTP_AUTHORIZATION=f"Bearer {anterior}")
        desafio = self.login()
        self.assertEqual(viejo_dispositivo.get(BASE + "yo/").status_code, 200)
        self.assertEqual(self.verificar(desafio, "111111").status_code, 400)
        self.assertEqual(viejo_dispositivo.get(BASE + "yo/").status_code, 200)
        self.assertEqual(self.post("login", {"usuario": self.usuario.usuario, "contrasena": "incorrecta"}).status_code, 401)
        self.assertEqual(viejo_dispositivo.get(BASE + "yo/").status_code, 200)
        result = self.verificar(desafio)
        self.assertEqual(result.status_code, 200, result.content)
        nuevo = Client(HTTP_AUTHORIZATION=f"Bearer {result.json()['token']}")
        self.assertEqual(nuevo.get(BASE + "yo/").status_code, 200)
        self.assertEqual(viejo_dispositivo.get(BASE + "yo/").status_code, 401)
        self.assertEqual(Sesion.objects.filter(revocado_en__isnull=True).count(), 1)
        self.assertFalse(VerificacionLogin.objects.filter(usado_en__isnull=True).exists())

    def test_salir_revoca_sesion_persistente_y_proximo_login_pide_codigo(self):
        token = self.verificar(self.login()).json()["token"]
        dispositivo = Client(HTTP_AUTHORIZATION=f"Bearer {token}", enforce_csrf_checks=True)
        result = dispositivo.post(BASE + "salir/", data="{}", content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.cookies[COOKIE]["max-age"], 0)
        self.assertEqual(dispositivo.get(BASE + "yo/").status_code, 401)
        self.assertTrue(self.login()["requiere_verificacion"])
        self.assertFalse(Sesion.objects.filter(revocado_en__isnull=True).exists())

    @override_settings(DEBUG=False)
    def test_web_exige_csrf_y_renueva_cookie_persistente_sin_token_json(self):
        web = Client(enforce_csrf_checks=True)
        datos = {"usuario": self.usuario.usuario, "contrasena": CLAVE}
        self.assertEqual(self.post("login", datos, client=web, native=False).status_code, 403)
        csrf = web.get(BASE + "csrf/").json()["csrfToken"]
        desafio = self.login(client=web, native=False, HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(self.verificar(desafio, client=web, native=False).status_code, 403)
        self.assertEqual(self.reenviar(desafio, client=web, native=False).status_code, 403)
        result = self.verificar(desafio, client=web, native=False, HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertNotIn("token", result.json())
        cookie = result.cookies[COOKIE]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], COOKIE_PATH)
        self.assertEqual(cookie["max-age"], 400 * 86400)
        otra_instancia = Client()
        otra_instancia.cookies[COOKIE] = cookie.value
        visita = otra_instancia.get(BASE + "yo/")
        self.assertEqual(visita.status_code, 200)
        self.assertEqual(visita.cookies[COOKIE].value, cookie.value)
        self.assertEqual(visita.cookies[COOKIE]["max-age"], 400 * 86400)
        csrf = web.get(BASE + "csrf/").json()["csrfToken"]
        salida = web.post(BASE + "salir/", data="{}", content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(salida.status_code, 200)
        self.assertEqual(salida.cookies[COOKIE]["max-age"], 0)
        self.assertEqual(otra_instancia.get(BASE + "yo/").status_code, 401)

    def test_nativo_verifica_y_reenvia_sin_cookies_ni_csrf(self):
        native = Client(enforce_csrf_checks=True)
        desafio = self.login(client=native)
        nuevo = self.reenviar(desafio, client=native)
        self.assertEqual(nuevo.status_code, 200, nuevo.content)
        result = self.verificar(nuevo.json(), client=native)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertIn("token", result.json())
        self.assertNotIn(COOKIE, result.cookies)

    def test_reenvio_invalida_anterior_y_exige_nuevo_codigo(self):
        desafio = self.login()
        self.codigo.return_value = 987654
        result = self.reenviar(desafio)
        self.assertEqual(result.status_code, 200, result.content)
        siguiente = result.json()
        self.assertNotEqual(siguiente["verificacion_id"], desafio["verificacion_id"])
        self.assertEqual(self.verificar(desafio).status_code, 400)
        self.assertEqual(self.verificar(siguiente).status_code, 400)
        self.assertEqual(self.verificar(siguiente, "987654").status_code, 200)

    def test_reenvio_fallido_conserva_codigo_previo_y_no_filtra_error_proveedor(self):
        desafio = self.login()
        self.mail.side_effect = requests.RequestException("API_KEY=secret correo=ana@example.com")
        with self.assertLogs("movil.login_verification", level="WARNING") as logs:
            result = self.reenviar(desafio)
        self.assertEqual(result.status_code, 503, result.content)
        self.assertNotIn("secret", result.content.decode())
        self.assertNotIn("secret", " ".join(logs.output))
        self.assertNotIn(self.usuario.correo, " ".join(logs.output))
        self.assertEqual(self.verificar(desafio).status_code, 200)

    @override_settings(RESEND_API_KEY="")
    def test_configuracion_correo_ausente_no_emite_sesion(self):
        result = self.post("login", {"usuario": self.usuario.usuario, "contrasena": CLAVE})
        self.assertEqual(result.status_code, 503, result.content)
        self.mail.assert_not_called()
        self.assertFalse(Sesion.objects.exists())
        self.assertFalse(VerificacionLogin.objects.exists())

    def test_fallo_proveedor_en_login_no_emite_sesion(self):
        self.mail.side_effect = requests.RequestException("secret")
        with self.assertLogs("movil.login_verification", level="WARNING"):
            result = self.post("login", {"usuario": self.usuario.usuario, "contrasena": CLAVE})
        self.assertEqual(result.status_code, 503)
        self.assertFalse(Sesion.objects.exists())
        self.assertNotIn(COOKIE, result.cookies)
        self.assertNotIn("token", result.json())

    def test_cambiar_correo_o_clave_invalida_verificacion_y_reenvio(self):
        for campo, valor in (("correo", "nuevo@example.com"), ("contrasena_hash", make_password("NuevaClaveSegura2026!"))):
            with self.subTest(campo=campo):
                desafio = self.login()
                anterior = getattr(self.usuario, campo)
                setattr(self.usuario, campo, valor)
                self.usuario.save(update_fields=[campo])
                self.assertEqual(self.verificar(desafio).status_code, 400)
                self.assertEqual(self.reenviar(desafio).status_code, 400)
                setattr(self.usuario, campo, anterior)
                self.usuario.save(update_fields=[campo])
        self.assertFalse(Sesion.objects.exists())

    def test_cuenta_desactivada_no_puede_verificar_ni_reenviar(self):
        desafio = self.login()
        self.usuario.activo = False
        self.usuario.save(update_fields=["activo"])
        self.assertEqual(self.verificar(desafio).status_code, 400)
        self.assertEqual(self.reenviar(desafio).status_code, 400)
        self.assertFalse(Sesion.objects.exists())

    def test_verificacion_consumida_no_puede_reenviarse(self):
        desafio = self.login()
        self.assertEqual(self.verificar(desafio).status_code, 200)
        self.mail.reset_mock()
        self.assertEqual(self.reenviar(desafio).status_code, 400)
        self.mail.assert_not_called()

    def test_registro_web_y_nativo_no_omiten_verificacion(self):
        for indice, native in enumerate((False, True)):
            client = Client(enforce_csrf_checks=True)
            extra = {} if native else {"HTTP_X_CSRFTOKEN": client.get(BASE + "csrf/").json()["csrfToken"]}
            result = self.post("registro", {"nombre_completo": "Nueva Cuenta", "usuario": f"nueva{indice}",
                "correo": f"nueva{indice}@example.com", "telefono": f"812345678{indice}", "contrasena": CLAVE},
                client=client, native=native, **extra)
            self.assertEqual(result.status_code, 200, result.content)
            self.assertTrue(result.json()["requiere_verificacion"])
            self.assertNotIn("token", result.json())
            self.assertNotIn(COOKIE, result.cookies)
            self.assertEqual(client.get(BASE + "yo/").status_code, 401)
        self.assertFalse(Sesion.objects.exists())

    @override_settings(SECURITY_RATE_LIMIT_ENABLED=True)
    def test_espera_y_limite_de_cuenta_se_comparten_entre_ips(self):
        instante = 1000000000
        with patch("movil.login_verification.time.time", return_value=instante):
            desafio = self.login(REMOTE_ADDR="10.0.0.1")
            result = self.reenviar(desafio, REMOTE_ADDR="10.0.0.2")
            self.assertEqual(result.status_code, 429, result.content)
            self.assertEqual(result["Retry-After"], "60")
        for indice in range(1, 6):
            with patch("movil.login_verification.time.time", return_value=instante + 61 * indice):
                result = self.post("login", {"usuario": self.usuario.correo, "contrasena": CLAVE}, REMOTE_ADDR=f"10.0.0.{indice + 2}")
                self.assertEqual(result.status_code, 200 if indice < 5 else 429, result.content)
        self.assertEqual(self.mail.call_count, 5)
        self.assertTrue(LimiteAcceso.objects.exists())

    @override_settings(SECURITY_RATE_LIMIT_ENABLED=True)
    def test_limite_ip_restringe_codigos_para_ids_diferentes(self):
        for indice in range(41):
            result = self.verificar({"verificacion_id": str(uuid4())})
            self.assertEqual(result.status_code, 400 if indice < 40 else 429, result.content)

