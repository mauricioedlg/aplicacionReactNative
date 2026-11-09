"""Despliegue unificado: sin .env, sockets ni bases de datos reales."""
import os
import runpy
from unittest.mock import patch

from django.http import JsonResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from servidor.deployment import InternalProxyMiddleware, allowed_hosts_from_environment

from .auth import api, limitar, respuesta
from .models import LimiteAcceso


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
        self.assertEqual(configuration["TRUSTED_CLIENT_IP_HEADER"], "")


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
