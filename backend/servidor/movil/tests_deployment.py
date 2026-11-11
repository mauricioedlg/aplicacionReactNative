"""Despliegue unificado: sin .env, sockets ni bases de datos reales."""
import json
import os
import runpy
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.http import JsonResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from servidor.deployment import (
    InternalProxyMiddleware,
    allowed_hosts_from_environment,
    csrf_trusted_origins_from_environment,
)

from .auth import COOKIE, api, limitar, respuesta
from .models import LimiteAcceso, Usuario


class AllowedHostsTests(SimpleTestCase):
    def test_local_retains_loopback_hosts(self):
        self.assertEqual(allowed_hosts_from_environment({}), ["127.0.0.1", "localhost"])

    def test_render_adds_only_its_exact_hostname(self):
        self.assertEqual(allowed_hosts_from_environment({
            "ALLOWED_HOSTS": " app.example.com,localhost ",
            "RENDER_EXTERNAL_HOSTNAME": "APP.onrender.com",
        }), ["app.example.com", "localhost", "app.onrender.com"])

    def test_render_does_not_duplicate_explicit_host(self):
        self.assertEqual(allowed_hosts_from_environment({
            "ALLOWED_HOSTS": "app.onrender.com",
            "RENDER_EXTERNAL_HOSTNAME": "app.onrender.com",
        }), ["app.onrender.com"])

    def test_render_rejects_wildcards_urls_and_malformed_hosts(self):
        for host in ("*", "*.onrender.com", "https://app.onrender.com", "app.onrender.com/path", "app.onrender.com:443", "app..onrender.com"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                allowed_hosts_from_environment({"RENDER_EXTERNAL_HOSTNAME": host})

    def test_production_settings_enable_the_proxy_before_security(self):
        environment = {
            "SECRET_KEY": "test-deployment-settings-key-" * 3,
            "RENDER_EXTERNAL_HOSTNAME": "app.onrender.com",
            "CORS_ALLOWED_ORIGINS": "https://existing.example.com",
            "CSRF_TRUSTED_ORIGINS": "https://custom.example.com",
            "ENABLE_INTERNAL_PROXY": "True",
            "TRUST_PROXY_HTTPS": "True",
            "TRUSTED_CLIENT_IP_HEADER": "HTTP_X_FORWARDED_FOR",
        }
        # Suprimir load_dotenv evita leer cualquier configuración local real.
        with patch.dict(os.environ, environment, clear=True), patch("dotenv.load_dotenv"):
            configuration = runpy.run_module("servidor.settings")
        self.assertTrue(configuration["ENABLE_INTERNAL_PROXY"])
        self.assertEqual(configuration["MIDDLEWARE"][0], "servidor.deployment.InternalProxyMiddleware")
        self.assertEqual(configuration["SECURE_PROXY_SSL_HEADER"], ("HTTP_X_FORWARDED_PROTO", "https"))
        self.assertEqual(configuration["SECURE_REDIRECT_EXEMPT"], [r"^healthz$"])
        self.assertEqual(configuration["ALLOWED_HOSTS"], ["127.0.0.1", "localhost", "app.onrender.com"])
        self.assertEqual(configuration["CORS_ALLOWED_ORIGINS"], ["https://existing.example.com"])
        self.assertEqual(configuration["CSRF_TRUSTED_ORIGINS"], [
            "https://existing.example.com", "https://custom.example.com", "https://app.onrender.com",
        ])
        self.assertEqual(configuration["TRUSTED_CLIENT_IP_HEADER"], "")


class CsrfTrustedOriginsTests(SimpleTestCase):
    def test_local_retains_cors_origins_without_mutating_the_input(self):
        cors_origins = ["http://localhost:8081"]
        origins = csrf_trusted_origins_from_environment({}, cors_origins)
        self.assertEqual(origins, cors_origins)
        self.assertIsNot(origins, cors_origins)

    def test_render_adds_only_its_exact_https_origin(self):
        self.assertEqual(csrf_trusted_origins_from_environment({
            "RENDER_EXTERNAL_HOSTNAME": " APP.onrender.com ",
        }, ["http://localhost:8081"]), [
            "http://localhost:8081", "https://app.onrender.com",
        ])

    def test_explicit_origins_extend_cors_without_duplicates(self):
        self.assertEqual(csrf_trusted_origins_from_environment({
            "CSRF_TRUSTED_ORIGINS": " https://custom.example.com, ,https://app.onrender.com,https://custom.example.com ",
            "RENDER_EXTERNAL_HOSTNAME": "app.onrender.com",
        }, ["https://existing.example.com", "https://existing.example.com"]), [
            "https://existing.example.com", "https://custom.example.com", "https://app.onrender.com",
        ])

    def test_blank_environment_does_not_create_origins(self):
        self.assertEqual(csrf_trusted_origins_from_environment({
            "CSRF_TRUSTED_ORIGINS": " , ", "RENDER_EXTERNAL_HOSTNAME": " ",
        }, []), [])

    def test_render_rejects_wildcards_urls_and_malformed_hosts(self):
        for host in ("*", "*.onrender.com", "https://app.onrender.com", "app.onrender.com/path", "app.onrender.com:443", "app..onrender.com"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                csrf_trusted_origins_from_environment({"RENDER_EXTERNAL_HOSTNAME": host}, [])


class BrowserCsrfDeploymentTests(TestCase):
    databases = {"default", "movil"}
    origin = "https://app.onrender.com"
    base = "/api/sistema/app/"

    def setUp(self):
        environment = {
            "SECRET_KEY": "test-deployment-settings-key-" * 3,
            "RENDER_EXTERNAL_HOSTNAME": "app.onrender.com",
            "ENABLE_INTERNAL_PROXY": "True",
            "TRUST_PROXY_HTTPS": "True",
            "SECURITY_RATE_LIMIT_ENABLED": "False",
        }
        with patch.dict(os.environ, environment, clear=True), patch("dotenv.load_dotenv"):
            configuration = runpy.run_module("servidor.settings")
        names = (
            "DEBUG", "MIDDLEWARE", "ALLOWED_HOSTS", "ENABLE_INTERNAL_PROXY",
            "SECURE_PROXY_SSL_HEADER", "SECURE_SSL_REDIRECT", "CSRF_TRUSTED_ORIGINS",
            "CSRF_COOKIE_SECURE", "CSRF_COOKIE_HTTPONLY", "CORS_ALLOWED_ORIGINS",
            "SECURITY_RATE_LIMIT_ENABLED",
        )
        override = override_settings(**{name: configuration[name] for name in names})
        override.enable()
        self.addCleanup(override.disable)
        # Reproduce public HTTPS Origin arriving over an internal HTTP connection.
        self.browser = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")
        response = self.browser.get(self.base + "csrf/")
        self.assertEqual(response.status_code, 200)
        self.token = response.json()["csrfToken"]
        self.assertTrue(response.cookies["csrftoken"]["secure"])
        self.assertTrue(response.cookies["csrftoken"]["httponly"])

    def login(self, origin=None, token=True, credentials=None):
        headers = {"HTTP_ORIGIN": origin or self.origin}
        if token:
            headers["HTTP_X_CSRFTOKEN"] = self.token
        data = credentials if credentials is not None else {
            "usuario": "unknown-user", "contrasena": "unused-test-password",
        }
        return self.browser.post(self.base + "login/", data=json.dumps(data),
            content_type="application/json", **headers)

    def test_previous_local_only_origins_reproduce_render_login_rejection(self):
        with override_settings(CSRF_TRUSTED_ORIGINS=["http://localhost:5173"]):
            self.assertEqual(self.login().status_code, 403)

    def test_render_origin_reaches_json_credential_validation(self):
        response = self.login()
        self.assertFalse(response.wsgi_request.is_secure())
        self.assertEqual(response.wsgi_request.get_host(), "localhost")
        self.assertEqual(response.status_code, 401, response.content)
        self.assertFalse(response.json()["ok"])
        self.assertIn("mensaje", response.json())

    @override_settings(RESEND_API_KEY="re_test_key", RESEND_SENDER_EMAIL="acceso@example.com")
    @patch("movil.login_verification.enviar_correo_resend", return_value={"id": "test-email"})
    @patch("movil.login_verification.secrets.randbelow", return_value=34512)
    def test_render_login_establishes_a_secure_cookie_session(self, codigo, correo):
        password = "OnlyForDeploymentTest2026!"
        user = Usuario.objects.create(nombre_completo="Deployment Test", usuario="deployment-test",
            correo="deployment@example.com", telefono="8112345678", contrasena_hash=make_password(password))
        self.assertEqual(self.browser.get(self.base + "yo/").status_code, 401)
        response = self.login(credentials={"usuario": user.usuario, "contrasena": password})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("token", response.json())
        self.assertNotIn(COOKIE, response.cookies)
        self.assertEqual(self.browser.get(self.base + "yo/").status_code, 401)
        response = self.browser.post(self.base + "verificacion-login/verificar/",
            data=json.dumps({"verificacion_id": response.json()["verificacion_id"], "codigo": "034512"}),
            content_type="application/json", HTTP_ORIGIN=self.origin, HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("token", response.json())
        self.assertTrue(response.cookies[COOKIE]["secure"])
        self.assertTrue(response.cookies[COOKIE]["httponly"])
        identity = self.browser.get(self.base + "yo/")
        self.assertEqual(identity.status_code, 200, identity.content)
        self.assertEqual(identity.json()["usuario"]["usuario"], user.usuario)

    def test_untrusted_origin_is_still_rejected_with_a_valid_csrf_token(self):
        response = self.login(origin="https://untrusted.example.com")
        self.assertEqual(response.status_code, 403)

    def test_trusted_origin_still_requires_a_csrf_token(self):
        response = self.login(token=False)
        self.assertEqual(response.status_code, 403)


class InternalProxyTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = InternalProxyMiddleware(lambda request: JsonResponse({
            "ip": request.META.get("REMOTE_ADDR"),
            "secure": request.is_secure(),
            "private_header": "HTTP_X_INTERNAL_CLIENT_IP" in request.META,
        }))

    def request(self, peer="127.0.0.1", client="198.51.100.12", protocol="https"):
        return self.factory.get("/healthz", REMOTE_ADDR=peer,
            HTTP_X_INTERNAL_CLIENT_IP=client, HTTP_X_FORWARDED_PROTO=protocol)

    @override_settings(ENABLE_INTERNAL_PROXY=False, SECURE_PROXY_SSL_HEADER=None)
    def test_disabled_mode_does_not_trust_private_header(self):
        response = self.middleware(self.request())
        self.assertJSONEqual(response.content, {"ip": "127.0.0.1", "secure": False, "private_header": False})

    @override_settings(ENABLE_INTERNAL_PROXY=True, SECURE_PROXY_SSL_HEADER=None)
    def test_loopback_propagates_ip_without_implicitly_trusting_https(self):
        for peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            with self.subTest(peer=peer):
                response = self.middleware(self.request(peer=peer))
                self.assertJSONEqual(response.content, {"ip": "198.51.100.12", "secure": False, "private_header": False})

    @override_settings(ENABLE_INTERNAL_PROXY=True, SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_loopback_preserves_explicit_https_trust(self):
        response = self.middleware(self.request(client="2001:db8::123"))
        self.assertJSONEqual(response.content, {"ip": "2001:db8::123", "secure": True, "private_header": False})

    @override_settings(ENABLE_INTERNAL_PROXY=True, SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_external_peer_cannot_spoof_client_ip_or_https(self):
        for peer in ("203.0.113.50", "10.0.0.2", "invalid", ""):
            with self.subTest(peer=peer):
                response = self.middleware(self.request(peer=peer))
                self.assertJSONEqual(response.content, {"ip": peer, "secure": False, "private_header": False})

    @override_settings(ENABLE_INTERNAL_PROXY=True, SECURE_PROXY_SSL_HEADER=None)
    def test_malformed_ip_does_not_replace_the_peer(self):
        for client in ("198.51.100.12,203.0.113.1", "unknown", "198.51.100.12:5000", "fe80::1%eth0", ""):
            with self.subTest(client=client):
                response = self.middleware(self.request(client=client))
                self.assertJSONEqual(response.content, {"ip": "127.0.0.1", "secure": False, "private_header": False})

    @override_settings(ENABLE_INTERNAL_PROXY=True, SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_forwarded_scheme_lists_are_not_trusted(self):
        response = self.middleware(self.request(protocol="https,http"))
        self.assertJSONEqual(response.content, {"ip": "198.51.100.12", "secure": False, "private_header": False})


@override_settings(ENABLE_INTERNAL_PROXY=True, SECURITY_RATE_LIMIT_ENABLED=True)
class ProxyRateLimitTests(TestCase):
    databases = {"default", "movil"}

    def test_proxy_does_not_combine_different_clients_into_one_limit(self):
        @api
        def endpoint(request):
            limitar(request, "deployment-test", limite=1)
            return respuesta({})

        middleware = InternalProxyMiddleware(endpoint)
        factory = RequestFactory()

        def visit(client_ip):
            return middleware(factory.get("/api/sistema/app/yo/", REMOTE_ADDR="127.0.0.1",
                HTTP_X_INTERNAL_CLIENT_IP=client_ip))

        self.assertEqual(visit("198.51.100.12").status_code, 200)
        self.assertEqual(visit("198.51.100.12").status_code, 429)
        self.assertEqual(visit("198.51.100.13").status_code, 200)
        self.assertEqual(LimiteAcceso.objects.count(), 2)


@override_settings(MIDDLEWARE=[
    "servidor.deployment.InternalProxyMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "server.security.SeguridadAPIMiddleware",
    "django.middleware.common.CommonMiddleware",
], ENABLE_INTERNAL_PROXY=True, SECURITY_RATE_LIMIT_ENABLED=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^healthz$"],
    ALLOWED_HOSTS=["app.onrender.com"])
class HealthTests(SimpleTestCase):
    def test_health_works_through_proxy_without_accessing_any_database(self):
        response = self.client.get("/healthz", HTTP_HOST="app.onrender.com",
            REMOTE_ADDR="127.0.0.1", HTTP_X_INTERNAL_CLIENT_IP="198.51.100.12",
            HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.wsgi_request.META["REMOTE_ADDR"], "198.51.100.12")

    def test_health_rejects_unsafe_methods(self):
        response = Client().post("/healthz", HTTP_HOST="app.onrender.com",
            REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(response.status_code, 405)

    def test_health_remains_available_for_local_http_checks_with_ssl_redirect(self):
        response = self.client.get("/healthz", HTTP_HOST="app.onrender.com",
            REMOTE_ADDR="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertFalse(response.wsgi_request.is_secure())

    def test_host_validation_still_applies(self):
        response = self.client.get("/healthz", HTTP_HOST="untrusted.example.com",
            REMOTE_ADDR="127.0.0.1")
        self.assertEqual(response.status_code, 400)
