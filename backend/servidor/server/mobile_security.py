"""CSRF de navegador intacto; credenciales explícitas para clientes nativos."""
from django.middleware.csrf import CsrfViewMiddleware

from .sistemaServidor.sistema_auth import _extraer_bearer


class SistemaCsrfMiddleware(CsrfViewMiddleware):
    MOBILE_AUTH_PATHS = frozenset({
        "/api/sistema/movil/login/",
        "/api/sistema/movil/verificar/",
        "/api/sistema/app/movil/login/",
        "/api/sistema/app/movil/registro/",
        "/api/sistema/app/movil/verificacion-login/verificar/",
        "/api/sistema/app/movil/verificacion-login/reenviar/",
        "/api/sistema/app/movil/recuperacion/solicitar/",
        "/api/sistema/app/movil/recuperacion/verificar/",
        "/api/sistema/app/movil/recuperacion/restablecer/",
    })

    def process_view(self, request, callback, callback_args, callback_kwargs):
        # Estas vistas reciben credenciales explícitas y no usan la cookie web.
        # POST JSON obligatorio y rate limit compartido en seguridad API.
        if request.path in self.MOBILE_AUTH_PATHS:
            return None
        # El navegador no adjunta Authorization por sí mismo. La vista sigue
        # validando el hash, vencimiento, revocación y rol contra PostgreSQL.
        # _extraer_token nunca recurre a cookies si Authorization está presente.
        if request.path.startswith("/api/sistema/") and _extraer_bearer(request):
            return None
        return super().process_view(request, callback, callback_args, callback_kwargs)
