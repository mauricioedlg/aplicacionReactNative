"""Contrato HTTP entre el servidor Node público y Django en el contenedor."""
import ipaddress
import re

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse


def allowed_hosts_from_environment(environment):
    hosts = [
        host.strip()
        for host in environment.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
        if host.strip()
    ]
    render_host = environment.get("RENDER_EXTERNAL_HOSTNAME", "").strip().lower()
    if render_host:
        # Render proporciona un hostname, nunca una URL ni un patrón wildcard.
        label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        if len(render_host) > 253 or not re.fullmatch(rf"{label}(?:\.{label})+", render_host):
            raise ValueError("RENDER_EXTERNAL_HOSTNAME debe ser un hostname exacto")
        if render_host not in {host.lower() for host in hosts}:
            hosts.append(render_host)
    return hosts


def _ip(value):
    if not isinstance(value, str) or "%" in value:
        raise ValueError("IP inválida")
    address = ipaddress.ip_address(value.strip())
    return getattr(address, "ipv4_mapped", None) or address


class InternalProxyMiddleware:
    """Confía en el header privado solo desde el proxy local habilitado."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        forwarded_ip = request.META.pop("HTTP_X_INTERNAL_CLIENT_IP", None)
        if getattr(settings, "ENABLE_INTERNAL_PROXY", False):
            try:
                internal_peer = _ip(request.META.get("REMOTE_ADDR", "")).is_loopback
            except ValueError:
                internal_peer = False
            if internal_peer:
                try:
                    request.META["REMOTE_ADDR"] = str(_ip(forwarded_ip))
                except ValueError:
                    pass
                # El proxy Node escribe un único esquema, sin listas de proxies.
                if request.META.get("HTTP_X_FORWARDED_PROTO") not in ("http", "https"):
                    request.META.pop("HTTP_X_FORWARDED_PROTO", None)
            else:
                # Incluso con TRUST_PROXY_HTTPS, una conexión externa no es Node.
                request.META.pop("HTTP_X_FORWARDED_PROTO", None)
        return self.get_response(request)


def healthz(request):
    """Confirma que Django atiende HTTP; no consulta bases ni servicios externos."""
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    response = JsonResponse({"ok": True})
    response["Cache-Control"] = "no-store"
    return response
