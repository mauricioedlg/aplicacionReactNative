import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import UUID

import requests
from django.contrib.auth.hashers import check_password, make_password
from django.core.management import call_command
from django.db import DatabaseError
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from server.resend_service import enviar_correo_resend

from .auth import COOKIE, token_hash
from .models import LimiteAcceso, NotificacionSeguridad, RecuperacionContrasena, Sesion, Usuario

BASE = "/api/sistema/app/"
CLAVE = "MiContrasenaAnterior2026!"
NUEVA = "NuevaContrasenaSegura2026!"
CODIGO = "034512"


@override_settings(RESEND_API_KEY="re_test_key", RESEND_SENDER_EMAIL="seguridad@example.com", RESEND_SENDER_NAME="MEG",
                   MOBILE_PASSWORD_RESET_CODE_TTL_SECONDS=600, MOBILE_PASSWORD_RESET_TOKEN_TTL_SECONDS=600)
class PasswordRecoveryTests(TestCase):
    databases = {"default", "movil"}

    def setUp(self):
        self.usuario = Usuario.objects.create(nombre_completo="Ana <Pérez>", usuario="ana", correo="ana@example.com",
                                              telefono="8112345678", contrasena_hash=make_password(CLAVE))
        self.mail_patch = patch("movil.password_recovery.enviar_correo_resend", return_value={"id": "email-id"})
        self.mail = self.mail_patch.start()
        self.addCleanup(self.mail_patch.stop)
        self.codigo_patch = patch("movil.password_recovery.secrets.randbelow", return_value=int(CODIGO))
        self.codigo_patch.start()
        self.addCleanup(self.codigo_patch.stop)

    def post(self, accion, datos, client=None, native=True, **extra):
        prefix = "movil/" if native else ""
        return (client or self.client).post(BASE + prefix + "recuperacion/" + accion + "/",
                                            data=json.dumps(datos), content_type="application/json", **extra)

    def solicitar(self, correo=None, **kwargs):
        result = self.post("solicitar", {"correo": correo or self.usuario.correo}, **kwargs)
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()

    def verificar(self, recuperacion_id, codigo=CODIGO, **kwargs):
        return self.post("verificar", {"recuperacion_id": recuperacion_id, "codigo": codigo}, **kwargs)

    def token(self):
        solicitud = self.solicitar()
        result = self.verificar(solicitud["recuperacion_id"])
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()["token_recuperacion"]

    def restablecer(self, token, clave=NUEVA, confirmacion=None, **kwargs):
        return self.post("restablecer", {"token_recuperacion": token, "contrasena": clave,
                                        "confirmacion": clave if confirmacion is None else confirmacion}, **kwargs)

    def test_solicitud_envia_codigo_y_no_guarda_codigo_ni_claves_sin_hash(self):
        antes = timezone.now()
        datos = self.solicitar("  ANA@example.com  ")
        UUID(datos["recuperacion_id"])
        self.assertEqual(datos["reenviar_en"], 60)
        recuperacion = RecuperacionContrasena.objects.get()
        self.assertEqual(str(recuperacion.id), datos["recuperacion_id"])
        self.assertEqual(recuperacion.correo, self.usuario.correo)
        self.assertNotEqual(recuperacion.codigo_hash, CODIGO)
        self.assertNotIn(CLAVE, recuperacion.credencial_hash)
        self.assertIsNone(recuperacion.token_hash)
        self.assertGreaterEqual(recuperacion.expira_en, antes + timedelta(seconds=600))
        self.assertEqual(self.mail.call_args.kwargs["destinatario"], self.usuario.correo)
        self.assertIn(CODIGO, self.mail.call_args.kwargs["contenido_html"])
        self.assertIn("Ana &lt;Pérez&gt;", self.mail.call_args.kwargs["contenido_html"])
        self.assertNotIn(CODIGO, json.dumps(datos))
        self.assertNotIn(CLAVE, self.mail.call_args.kwargs["contenido_html"])
        self.assertFalse(Sesion.objects.exists())

    def test_cuenta_inexistente_o_inactiva_recibe_mismo_contrato_sin_correo(self):
        conocida = self.solicitar()
        self.mail.reset_mock()
        desconocida = self.solicitar("desconocida@example.com")
        self.usuario.activo = False
        self.usuario.save(update_fields=["activo"])
        inactiva = self.solicitar()
        for datos in (desconocida, inactiva):
            self.assertEqual(set(datos), set(conocida))
            self.assertEqual(datos["mensaje"], conocida["mensaje"])
            self.assertEqual(datos["reenviar_en"], conocida["reenviar_en"])
            self.assertEqual(self.verificar(datos["recuperacion_id"]).status_code, 400)
        self.mail.assert_not_called()
        self.assertEqual(RecuperacionContrasena.objects.count(), 1)

    @override_settings(RESEND_API_KEY="")
    def test_configuracion_ausente_no_revela_cuenta_ni_intenta_enviar(self):
        for correo in (self.usuario.correo, "nadie@example.com"):
            result = self.post("solicitar", {"correo": correo})
            self.assertEqual(result.status_code, 503)
            self.assertIn("no está configurado", result.json()["mensaje"])
        self.assertFalse(RecuperacionContrasena.objects.exists())
        self.mail.assert_not_called()

    def test_solicitud_valida_correo_y_contrato_json(self):
        for correo in ("", "invalid", 12, "a" * 255 + "@example.com"):
            self.assertEqual(self.post("solicitar", {"correo": correo}).status_code, 400)
        result = self.client.post(BASE + "movil/recuperacion/solicitar/", data={"correo": self.usuario.correo})
        self.assertEqual(result.status_code, 415)
        self.assertEqual(self.client.get(BASE + "movil/recuperacion/solicitar/").status_code, 405)
        self.mail.assert_not_called()

    def test_codigo_se_verifica_una_vez_y_token_solo_se_guarda_como_hash(self):
        solicitud = self.solicitar()
        result = self.verificar(solicitud["recuperacion_id"])
        self.assertEqual(result.status_code, 200)
        token = result.json()["token_recuperacion"]
        recuperacion = RecuperacionContrasena.objects.get()
        self.assertEqual(recuperacion.token_hash, token_hash(token))
        self.assertNotEqual(recuperacion.token_hash, token)
        self.assertIsNotNone(recuperacion.verificado_en)
        self.assertEqual(self.verificar(solicitud["recuperacion_id"]).status_code, 400)
        recuperacion.refresh_from_db()
        self.assertEqual(recuperacion.token_hash, token_hash(token))

    def test_cinco_codigos_incorrectos_se_conservan_y_bloquean_codigo_correcto(self):
        solicitud = self.solicitar()
        for intento in range(1, 6):
            self.assertEqual(self.verificar(solicitud["recuperacion_id"], "111111").status_code, 400)
            recuperacion = RecuperacionContrasena.objects.get()
            self.assertEqual(recuperacion.intentos, intento)
        self.assertIsNotNone(recuperacion.usado_en)
        self.assertEqual(self.verificar(solicitud["recuperacion_id"]).status_code, 400)
        self.assertIsNone(RecuperacionContrasena.objects.get().token_hash)

    def test_codigo_vencido_malformado_y_desafio_desconocido_son_rechazados(self):
        solicitud = self.solicitar()
        RecuperacionContrasena.objects.update(expira_en=timezone.now() - timedelta(seconds=1))
        for recuperacion_id, codigo in ((solicitud["recuperacion_id"], CODIGO), ("no-es-uuid", CODIGO),
                                        (solicitud["recuperacion_id"], "１２３４５６"), (solicitud["recuperacion_id"], 123456)):
            self.assertEqual(self.verificar(recuperacion_id, codigo).status_code, 400)

    def test_reenvio_aceptado_invalida_codigo_y_token_anteriores(self):
        solicitud = self.solicitar()
        token = self.verificar(solicitud["recuperacion_id"]).json()["token_recuperacion"]
        siguiente = self.solicitar()
        self.assertNotEqual(solicitud["recuperacion_id"], siguiente["recuperacion_id"])
        self.assertEqual(self.restablecer(token).status_code, 410)
        self.assertEqual(self.verificar(solicitud["recuperacion_id"]).status_code, 400)
        self.assertEqual(self.verificar(siguiente["recuperacion_id"]).status_code, 200)

    def test_fallo_de_reenvio_no_invalida_codigo_previo_ni_filtra_detalles(self):
        solicitud = self.solicitar()
        self.mail.side_effect = requests.RequestException("API_KEY=secret correo=ana@example.com")
        with self.assertLogs("movil.password_recovery", level="WARNING") as logs:
            siguiente = self.solicitar()
        self.assertEqual(siguiente["mensaje"], solicitud["mensaje"])
        self.assertNotIn("secret", " ".join(logs.output))
        self.assertNotIn(self.usuario.correo, " ".join(logs.output))
        self.assertEqual(RecuperacionContrasena.objects.count(), 1)
        self.assertEqual(self.verificar(solicitud["recuperacion_id"]).status_code, 200)

    def test_cambiar_correo_o_clave_invalida_codigos_y_tokens_previos(self):
        for campo, valor in (("correo", "nuevo@example.com"), ("contrasena_hash", make_password("OtraContrasena2026!"))):
            for verificado in (False, True):
                with self.subTest(campo=campo, verificado=verificado):
                    solicitud = self.solicitar()
                    token = self.verificar(solicitud["recuperacion_id"]).json()["token_recuperacion"] if verificado else None
                    anterior = getattr(self.usuario, campo)
                    setattr(self.usuario, campo, valor)
                    self.usuario.save(update_fields=[campo])
                    result = self.restablecer(token) if token else self.verificar(solicitud["recuperacion_id"])
                    self.assertEqual(result.status_code, 410 if token else 400)
                    setattr(self.usuario, campo, anterior)
                    self.usuario.save(update_fields=[campo])

    def test_cuenta_desactivada_no_puede_finalizar_recuperacion(self):
        token = self.token()
        self.usuario.activo = False
        self.usuario.save(update_fields=["activo"])
        self.assertEqual(self.restablecer(token).status_code, 410)
        self.assertFalse(NotificacionSeguridad.objects.exists())

    def test_clave_confirmacion_y_token_vencido_se_validan_sin_consumir_token_valido(self):
        token = self.token()
        for clave, confirmacion in (("corta", "corta"), ("1234567890123", "1234567890123"), (NUEVA, "no-coincide"), (CLAVE, CLAVE)):
            self.assertEqual(self.restablecer(token, clave, confirmacion).status_code, 400)
        self.assertIsNone(RecuperacionContrasena.objects.get().usado_en)
        RecuperacionContrasena.objects.update(token_expira_en=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.restablecer(token).status_code, 410)
        self.assertEqual(self.restablecer("malformado").status_code, 410)
        self.usuario.refresh_from_db()
        self.assertTrue(check_password(CLAVE, self.usuario.contrasena_hash))

    def test_cambio_final_revoca_sesiones_desbloquea_cuenta_y_envia_confirmacion_una_vez(self):
        self.usuario.bloqueado_hasta = timezone.now() + timedelta(minutes=15)
        self.usuario.intentos_fallidos = 3
        self.usuario.save()
        for letra in ("A", "B"):
            Sesion.objects.create(usuario=self.usuario, token_hash=token_hash(letra * 48), expira_en=timezone.now() + timedelta(days=1))
        token = self.token()
        self.mail.reset_mock()
        result = self.restablecer(token)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertTrue(result.json()["correo_enviado"])
        self.usuario.refresh_from_db()
        self.assertTrue(check_password(NUEVA, self.usuario.contrasena_hash))
        self.assertIsNone(self.usuario.bloqueado_hasta)
        self.assertEqual(self.usuario.intentos_fallidos, 0)
        self.assertFalse(Sesion.objects.filter(revocado_en__isnull=True).exists())
        self.assertEqual(result.cookies[COOKIE]["max-age"], 0)
        aviso = NotificacionSeguridad.objects.get()
        self.assertIsNotNone(aviso.enviado_en)
        self.assertEqual(self.mail.call_count, 1)
        self.assertNotIn(NUEVA, self.mail.call_args.kwargs["contenido_html"])
        self.assertTrue(self.mail.call_args.kwargs["idempotency_key"].endswith(str(aviso.id)))
        self.assertEqual(self.restablecer(token).status_code, 410)
        login = lambda clave: self.client.post(BASE + "movil/login/", data=json.dumps({"usuario": self.usuario.usuario, "contrasena": clave}), content_type="application/json")
        self.assertEqual(login(CLAVE).status_code, 401)
        with patch("movil.login_verification.enviar_correo_resend", return_value={"id": "test-login-email"}):
            ingreso = login(NUEVA)
        self.assertEqual(ingreso.status_code, 200, ingreso.content)
        self.assertTrue(ingreso.json()["requiere_verificacion"])
        self.assertNotIn("token", ingreso.json())
        self.assertFalse(Sesion.objects.filter(revocado_en__isnull=True).exists())
        self.assertEqual(self.mail.call_count, 1)

    def test_confirmacion_fallida_queda_pendiente_y_comando_reintenta_sin_duplicar(self):
        token = self.token()
        self.mail.side_effect = requests.RequestException("mensaje privado de Resend")
        with self.assertLogs("movil.password_recovery", level="WARNING"):
            result = self.restablecer(token)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["correo_enviado"])
        self.usuario.refresh_from_db()
        self.assertTrue(check_password(NUEVA, self.usuario.contrasena_hash))
        aviso = NotificacionSeguridad.objects.get()
        self.assertEqual(aviso.intentos, 1)
        self.assertIsNone(aviso.enviado_en)
        self.assertGreater(aviso.reintentar_en, timezone.now())
        NotificacionSeguridad.objects.filter(pk=aviso.id).update(reintentar_en=timezone.now() - timedelta(seconds=1))
        self.mail.side_effect = None
        self.mail.reset_mock()
        call_command("reenviar_notificaciones_seguridad", stdout=io.StringIO())
        aviso.refresh_from_db()
        self.assertIsNotNone(aviso.enviado_en)
        self.assertEqual(aviso.intentos, 2)
        self.assertEqual(self.mail.call_count, 1)
        call_command("reenviar_notificaciones_seguridad", stdout=io.StringIO())
        self.assertEqual(self.mail.call_count, 1)

    def test_caida_de_base_al_enviar_aviso_no_reporta_fallo_de_un_cambio_ya_guardado(self):
        token = self.token()
        with patch("movil.password_recovery.enviar_notificacion", side_effect=DatabaseError("detalle privado")):
            with self.assertLogs("movil.password_recovery", level="WARNING"):
                result = self.restablecer(token)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["correo_enviado"])
        self.usuario.refresh_from_db()
        self.assertTrue(check_password(NUEVA, self.usuario.contrasena_hash))
        self.assertTrue(NotificacionSeguridad.objects.filter(enviado_en__isnull=True).exists())

    def test_csrf_web_obligatorio_y_rutas_nativas_admiten_flujo_sin_cookie(self):
        navegador = Client(enforce_csrf_checks=True)
        datos = {"correo": self.usuario.correo}
        for accion in ("solicitar", "verificar", "restablecer"):
            self.assertEqual(self.post(accion, datos, client=navegador, native=False).status_code, 403)
        csrf = navegador.get(BASE + "csrf/").json()["csrfToken"]
        self.assertEqual(self.post("solicitar", datos, client=navegador, native=False, HTTP_X_CSRFTOKEN=csrf).status_code, 200)
        nativo = Client(enforce_csrf_checks=True)
        solicitud = self.solicitar(client=nativo)
        result = self.verificar(solicitud["recuperacion_id"], client=nativo)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.restablecer(result.json()["token_recuperacion"], client=nativo).status_code, 200)

    @override_settings(SECURITY_RATE_LIMIT_ENABLED=True)
    def test_espera_y_tope_por_correo_se_comparten_entre_ips(self):
        instante = 1000000000
        with patch("movil.password_recovery.time.time", return_value=instante):
            self.solicitar(REMOTE_ADDR="10.0.0.1")
            bloqueada = self.post("solicitar", {"correo": self.usuario.correo}, REMOTE_ADDR="10.0.0.2")
            self.assertEqual(bloqueada.status_code, 429)
            self.assertEqual(bloqueada["Retry-After"], "60")
        for indice in range(1, 6):
            with patch("movil.password_recovery.time.time", return_value=instante + 61 * indice):
                result = self.post("solicitar", {"correo": self.usuario.correo}, REMOTE_ADDR=f"10.0.0.{indice + 2}")
                self.assertEqual(result.status_code, 200 if indice < 5 else 429)
        self.assertEqual(self.mail.call_count, 5)
        self.assertTrue(LimiteAcceso.objects.exists())

    @override_settings(SECURITY_RATE_LIMIT_ENABLED=True)
    def test_limite_de_ip_aplica_a_correos_distintos_y_verificacion(self):
        for indice in range(21):
            result = self.post("solicitar", {"correo": f"nadie{indice}@example.com"})
            self.assertEqual(result.status_code, 200 if indice < 20 else 429)
        for indice in range(41):
            result = self.verificar("00000000-0000-0000-0000-000000000000")
            self.assertEqual(result.status_code, 400 if indice < 40 else 429)


@override_settings(RESEND_API_KEY="re_test_key", RESEND_SENDER_EMAIL="seguridad@example.com", RESEND_SENDER_NAME="MEG")
class ResendRecoveryServiceTests(SimpleTestCase):
    @patch("server.resend_service.requests.post")
    def test_header_de_idempotencia_y_contrato_http(self, post):
        post.return_value = Mock(ok=True, json=Mock(return_value={"id": "email-id"}))
        result = enviar_correo_resend("ana@example.com", "Aviso de seguridad", "<p>Sin contraseña</p>", idempotency_key="seguridad/123")
        self.assertEqual(result, {"id": "email-id"})
        self.assertEqual(post.call_args.args, ("https://api.resend.com/emails",))
        self.assertEqual(post.call_args.kwargs["headers"]["Idempotency-Key"], "seguridad/123")
        self.assertEqual(post.call_args.kwargs["json"]["to"], ["ana@example.com"])
        self.assertEqual(post.call_args.kwargs["timeout"], 15)

    @patch("server.resend_service.requests.post")
    def test_llamadas_existentes_siguen_admitidas_sin_idempotencia(self, post):
        post.return_value = Mock(ok=True, json=Mock(return_value={"id": "email-id"}))
        enviar_correo_resend("ana@example.com", "Aviso", "<p>Mensaje</p>", reply_to="soporte@example.com")
        self.assertNotIn("Idempotency-Key", post.call_args.kwargs["headers"])
        self.assertEqual(post.call_args.kwargs["json"]["reply_to"], "soporte@example.com")
