import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .auth import COOKIE, membresia_actual, token_hash
from .models import LimiteAcceso, Membresia, Organizacion, Sesion, Usuario
from .router import MovilRouter

BASE = "/api/sistema/app/"
CLAVE = "UnaClaveSegura2026!"


@override_settings(RESEND_API_KEY="re_test_key", RESEND_SENDER_EMAIL="acceso@example.com")
class IdentityTests(TestCase):
    databases = {"default", "movil"}

    def setUp(self):
        for target, value in (("movil.login_verification.enviar_correo_resend", {"id": "test-email"}),
                              ("movil.login_verification.secrets.randbelow", 34512)):
            replacement = patch(target, return_value=value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def crear_usuario(self, nombre="ana", telefono="8112345678"):
        return Usuario.objects.create(nombre_completo=nombre.title(), usuario=nombre, correo=f"{nombre}@example.com", telefono=telefono, contrasena_hash=make_password(CLAVE))

    def autenticar(self, usuario, client=None):
        client = client or self.client
        token = (f"sesion_{usuario.id}_" + "A" * 48)
        Sesion.objects.create(usuario=usuario, token_hash=token_hash(token), expira_en=timezone.now() + timedelta(days=1))
        client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return token

    def enviar(self, ruta, datos=None, method="post", client=None):
        client = client or self.client
        return getattr(client, method)(BASE + ruta, data=json.dumps(datos or {}), content_type="application/json")

    def org_admin(self, usuario, nombre="Equipo Uno"):
        org = Organizacion.objects.create(nombre=nombre, descripcion="Desarrollo de software")
        Membresia.objects.create(usuario=usuario, organizacion=org, rol="admin", estado="aceptado")
        return org

    def test_registro_nativo_no_expone_contrasena_ni_establece_cookie(self):
        result = self.enviar("movil/registro/", {"nombre_completo": "Ana Pérez", "usuario": "ana", "correo": "ANA@example.com", "telefono": "+52 811 234 5678", "contrasena": CLAVE, "rol": "admin"})
        self.assertEqual(result.status_code, 200, result.content)
        datos = result.json()
        self.assertTrue(datos["requiere_verificacion"])
        self.assertNotIn("usuario", datos)
        self.assertNotIn("token", datos)
        self.assertFalse(Sesion.objects.exists())
        self.assertNotIn(COOKIE, result.cookies)
        usuario = Usuario.objects.get(usuario="ana")
        self.assertEqual(usuario.correo, "ana@example.com")
        self.assertEqual(usuario.telefono, "528112345678")
        self.assertNotEqual(usuario.contrasena_hash, CLAVE)
        self.assertNotIn(CLAVE, result.content.decode())
        verificacion = self.enviar("movil/verificacion-login/verificar/", {"verificacion_id": datos["verificacion_id"], "codigo": "034512"})
        self.assertEqual(verificacion.status_code, 200, verificacion.content)
        datos = verificacion.json()
        self.assertEqual(datos["usuario"]["estado_acceso"], "sin_organizacion")
        self.assertIsNone(datos["usuario"]["rol"])
        self.assertEqual(Sesion.objects.get(usuario=usuario).token_hash, token_hash(datos["token"]))
        self.assertNotIn(COOKIE, verificacion.cookies)

    def test_usuario_correo_y_telefono_son_identificadores_login(self):
        usuario = self.crear_usuario()
        for identificador in (usuario.usuario, usuario.correo.upper(), "+52 8112345678"):
            if identificador.startswith("+"):
                usuario.telefono = "528112345678"
                usuario.save()
            result = self.enviar("movil/login/", {"usuario": identificador, "contrasena": CLAVE})
            self.assertEqual(result.status_code, 200, result.content)
            self.assertTrue(result.json()["requiere_verificacion"])
            self.assertNotIn("token", result.json())
            verificacion = self.enviar("movil/verificacion-login/verificar/", {"verificacion_id": result.json()["verificacion_id"], "codigo": "034512"})
            self.assertEqual(verificacion.status_code, 200, verificacion.content)
            self.assertIn("token", verificacion.json())

    def test_fallos_de_contrasena_bloquean_cuenta_y_no_crean_sesion(self):
        usuario = self.crear_usuario()
        for _ in range(5):
            self.assertEqual(self.enviar("movil/login/", {"usuario": usuario.usuario, "contrasena": "incorrecta"}).status_code, 401)
        result = self.enviar("movil/login/", {"usuario": usuario.usuario, "contrasena": CLAVE})
        self.assertEqual(result.status_code, 401)
        self.assertFalse(Sesion.objects.exists())

    def test_cookie_web_csrf_y_token_nativo_se_mantienen_separados(self):
        usuario = self.crear_usuario()
        web = Client(enforce_csrf_checks=True)
        datos = {"usuario": usuario.usuario, "contrasena": CLAVE}
        self.assertEqual(self.enviar("login/", datos, client=web).status_code, 403)
        csrf = web.get(BASE + "csrf/").json()["csrfToken"]
        result = web.post(BASE + "login/", data=json.dumps(datos), content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertNotIn("token", result.json())
        self.assertNotIn(COOKIE, result.cookies)
        self.assertEqual(web.get(BASE + "yo/").status_code, 401)
        desafio = {"verificacion_id": result.json()["verificacion_id"], "codigo": "034512"}
        self.assertEqual(self.enviar("verificacion-login/verificar/", desafio, client=web).status_code, 403)
        result = web.post(BASE + "verificacion-login/verificar/", data=json.dumps(desafio), content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertNotIn("token", result.json())
        self.assertTrue(result.cookies[COOKIE]["httponly"])
        self.assertEqual(web.get(BASE + "yo/").status_code, 200)
        self.assertEqual(web.get(BASE + "yo/", HTTP_AUTHORIZATION="Bearer incorrecto").status_code, 401)
        native = Client(enforce_csrf_checks=True)
        result = self.enviar("movil/login/", datos, client=native)
        self.assertEqual(result.status_code, 200)
        self.assertNotIn(COOKIE, result.cookies)
        result = self.enviar("movil/verificacion-login/verificar/", {"verificacion_id": result.json()["verificacion_id"], "codigo": "034512"}, client=native)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertIn("token", result.json())
        self.assertNotIn(COOKIE, result.cookies)
        self.assertEqual(web.get(BASE + "yo/").status_code, 401)

    def test_sesion_expirada_revocada_y_salir(self):
        usuario = self.crear_usuario()
        self.autenticar(usuario)
        self.assertEqual(self.client.get(BASE + "yo/").status_code, 200)
        self.assertEqual(self.enviar("salir/").status_code, 200)
        self.assertEqual(self.client.get(BASE + "yo/").status_code, 401)
        sesion = Sesion.objects.get(usuario=usuario)
        sesion.revocado_en = None
        sesion.expira_en = timezone.now() - timedelta(seconds=1)
        sesion.save()
        self.assertEqual(self.client.get(BASE + "yo/").status_code, 401)

    def test_crear_organizacion_invita_y_asigna_roles_sin_duplicar_membresia(self):
        admin = self.crear_usuario()
        dev = self.crear_usuario("luis", "8123456789")
        self.autenticar(admin)
        result = self.enviar("organizaciones/", {"nombre": "Mi empresa", "descripcion": "Software", "invitaciones": [{"usuario": dev.usuario, "rol": "desarrollador"}]})
        self.assertEqual(result.status_code, 201, result.content)
        self.assertEqual(result.json()["usuario"]["rol"], "admin")
        self.assertEqual(Membresia.objects.get(usuario=dev).rol, "desarrollador")
        self.assertEqual(self.enviar("organizaciones/", {"nombre": "Otra"}).status_code, 409)
        self.assertEqual(Organizacion.objects.count(), 1)

    def test_invitacion_invalida_revierte_organizacion_completa(self):
        usuario = self.crear_usuario()
        self.autenticar(usuario)
        result = self.enviar("organizaciones/", {"nombre": "Nueva", "invitaciones": [{"usuario": "inexistente", "rol": "crm"}]})
        self.assertEqual(result.status_code, 400)
        self.assertFalse(Organizacion.objects.exists())
        self.assertFalse(Membresia.objects.exists())

    def test_solicitud_aprobacion_y_eliminacion_actualizan_permiso_inmediatamente(self):
        admin = self.crear_usuario()
        org = self.org_admin(admin)
        colaborador = self.crear_usuario("luis", "8123456789")
        colaborador_client = Client()
        self.autenticar(colaborador, colaborador_client)
        result = self.enviar("organizaciones/solicitar/", {"organizacion_id": str(org.id)}, client=colaborador_client)
        self.assertEqual(result.status_code, 201, result.content)
        self.assertEqual(result.json()["usuario"]["estado_acceso"], "pendiente")
        self.assertEqual(colaborador_client.get(BASE + "organizacion/").status_code, 403)
        self.autenticar(admin)
        solicitudes = self.client.get(BASE + "organizacion/solicitudes/").json()["solicitudes"]
        self.assertEqual(len(solicitudes), 1)
        self.assertEqual(solicitudes[0]["usuario"]["usuario"], "luis")
        miembro_id = solicitudes[0]["id"]
        self.assertEqual(self.enviar(f"organizacion/solicitudes/{miembro_id}/", {"aceptar": True, "rol": "crm"}).status_code, 200)
        self.assertEqual(self.client.get(BASE + "organizacion/solicitudes/").json()["solicitudes"], [])
        yo = colaborador_client.get(BASE + "yo/").json()["usuario"]
        self.assertEqual(yo["rol"], "crm")
        self.assertEqual(yo["estado_acceso"], "aceptado")
        self.assertEqual(self.enviar(f"organizacion/miembros/{miembro_id}/", method="delete").status_code, 200)
        yo = colaborador_client.get(BASE + "yo/").json()["usuario"]
        self.assertEqual(yo["estado_acceso"], "eliminado")
        self.assertIsNone(yo["rol"])
        self.assertEqual(colaborador_client.get(BASE + "organizacion/").status_code, 403)
        self.assertEqual(self.enviar("organizaciones/solicitar/", {"organizacion_id": str(org.id)}, client=colaborador_client).status_code, 201)
        self.assertEqual(Membresia.objects.filter(usuario=colaborador).count(), 1)

    def test_admin_no_puede_decidir_solicitudes_de_otra_organizacion(self):
        admin = self.crear_usuario()
        self.org_admin(admin)
        otro = self.crear_usuario("otro", "8123456789")
        otra_org = self.org_admin(otro, "Otra empresa")
        pendiente = self.crear_usuario("pendiente", "8134567890")
        solicitud = Membresia.objects.create(usuario=pendiente, organizacion=otra_org)
        self.autenticar(admin)
        self.assertEqual(self.enviar(f"organizacion/solicitudes/{solicitud.id}/", {"aceptar": True, "rol": "admin"}).status_code, 404)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, "pendiente")
        self.assertEqual(self.enviar(f"organizacion/miembros/{Membresia.objects.get(usuario=otro).id}/", method="delete").status_code, 404)

    def test_admin_no_puede_eliminar_su_propia_membresia(self):
        usuario = self.crear_usuario()
        self.org_admin(usuario)
        self.autenticar(usuario)
        miembro = Membresia.objects.get(usuario=usuario)
        self.assertEqual(self.enviar(f"organizacion/miembros/{miembro.id}/", method="delete").status_code, 409)
        miembro.refresh_from_db()
        self.assertEqual(miembro.estado, "aceptado")

    def test_mutaciones_admin_revalidan_permiso_despues_de_bloquear_organizacion(self):
        admin = self.crear_usuario()
        org = self.org_admin(admin)
        otro = self.crear_usuario("otro", "8123456789")
        otro_admin = Membresia.objects.create(usuario=otro, organizacion=org, rol="admin", estado="aceptado")
        candidato = self.crear_usuario("candidato", "8134567890")
        solicitud = Membresia.objects.create(usuario=candidato, organizacion=org, estado="pendiente")
        self.autenticar(admin)

        def revocar_despues_de_autorizar(usuario):
            # Simula que otro administrador confirmó la eliminación mientras
            # esta petición esperaba el bloqueo de la organización.
            snapshot = membresia_actual(usuario)
            Membresia.objects.filter(usuario=usuario, organizacion=org).update(estado="eliminado")
            return snapshot

        operaciones = [
            (f"organizacion/miembros/{otro_admin.id}/", {}, "delete"),
            (f"organizacion/solicitudes/{solicitud.id}/", {"aceptar": True, "rol": "admin"}, "post"),
            ("organizacion/", {"nombre": "Cambio sin permiso", "descripcion": ""}, "patch"),
        ]
        for ruta, datos, metodo in operaciones:
            Membresia.objects.filter(usuario=admin).update(estado="aceptado")
            with patch("movil.auth.membresia_actual", side_effect=revocar_despues_de_autorizar):
                result = self.enviar(ruta, datos, metodo)
            self.assertEqual(result.status_code, 403, (ruta, result.content))
        otro_admin.refresh_from_db()
        solicitud.refresh_from_db()
        org.refresh_from_db()
        self.assertEqual(otro_admin.estado, "aceptado")
        self.assertEqual(solicitud.estado, "pendiente")
        self.assertEqual(org.nombre, "Equipo Uno")
        self.assertEqual(Membresia.objects.filter(organizacion=org, estado="aceptado", rol="admin").count(), 1)

    def test_cuenta_no_permite_escalar_rol_y_exige_clave_para_datos_sensibles(self):
        usuario = self.crear_usuario()
        self.autenticar(usuario)
        self.assertEqual(self.enviar("cuenta/", {"rol": "admin"}, "patch").status_code, 400)
        self.assertEqual(self.enviar("cuenta/", {"correo": "nuevo@example.com"}, "patch").status_code, 403)
        self.assertEqual(self.enviar("cuenta/", {"nombre_completo": "Ana Actualizada"}, "patch").status_code, 200)
        result = self.enviar("cuenta/", {"correo": "nuevo@example.com", "contrasena_actual": CLAVE}, "patch")
        self.assertEqual(result.status_code, 200)
        usuario.refresh_from_db()
        self.assertEqual(usuario.correo, "nuevo@example.com")

    def test_cambio_clave_revoca_otras_sesiones(self):
        usuario = self.crear_usuario()
        self.autenticar(usuario)
        otra = Sesion.objects.create(usuario=usuario, token_hash=token_hash("B" * 64), expira_en=timezone.now() + timedelta(days=1))
        result = self.enviar("cuenta/", {"contrasena": "OtraClaveSegura2026!", "contrasena_actual": CLAVE}, "patch")
        self.assertEqual(result.status_code, 200, result.content)
        otra.refresh_from_db()
        self.assertIsNotNone(otra.revocado_en)
        self.assertEqual(self.client.get(BASE + "yo/").status_code, 200)

    @override_settings(SECURITY_RATE_LIMIT_ENABLED=True)
    def test_rate_limit_persistente_sin_depender_de_base_legacy(self):
        usuario = self.crear_usuario()
        for _ in range(20):
            result = self.enviar("movil/login/", {"usuario": usuario.usuario, "contrasena": "incorrecta"})
            self.assertEqual(result.status_code, 401)
        self.assertEqual(self.enviar("movil/login/", {"usuario": usuario.usuario, "contrasena": "incorrecta"}).status_code, 429)
        self.assertEqual(LimiteAcceso.objects.count(), 1)

    def test_router_impide_migrar_o_consultar_datos_moviles_en_legacy(self):
        router = MovilRouter()
        self.assertEqual(router.db_for_read(Usuario), "movil")
        self.assertFalse(router.allow_migrate("default", "movil"))
        self.assertFalse(router.allow_migrate("neon", "movil"))
        self.assertFalse(router.allow_migrate("movil", "auth"))
        self.assertTrue(router.allow_migrate("movil", "movil"))
